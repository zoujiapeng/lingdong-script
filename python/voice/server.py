"""
Voice Conversation Server
WebSocket server that can:
  - Receive text from browser → DeepSeek LLM → Edge TTS
  - Receive "start_recording" → capture mic → whisper → DeepSeek LLM → Edge TTS

STT backends:
  - openai-whisper (tiny / base / small)
  - faster-whisper (CTranslate2, better compatibility)
"""
import asyncio
import json
import os
import shutil
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import edge_tts
import httpx
import numpy as np
import sounddevice as sd
import websockets

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from features._all import *  # noqa: F401, F403
from features.registry import registry

VOICE = "zh-CN-XiaoxiaoNeural"
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

SYSTEM_PROMPT = (
    "你是一个智能语音助手，可以回答问题并执行电脑自动化操作。"
    "回答要简洁自然、口语化，像真人对话一样，不需要使用 Markdown 格式。"
    "当用户需要操作电脑时，使用可用的工具来完成。"
    "当前语音识别引擎: {engine}。用户可以说'切换到xxx'来切换引擎。"
)

# ── STT Configuration ───────────────────────────────────────────
SAMPLE_RATE = 16000            # target sample rate for STT models
ENERGY_THRESHOLD = 8000        # int16 RMS; adjusted dynamically during mic detection
SPEECH_CONFIRM_CHUNKS = 2      # need N consecutive above-threshold chunks to start recording
SILENCE_DURATION = 1.0         # seconds of silence before considering utterance complete

# Mic device detection — set automatically by _find_mic_device()
MIC_SR = 44100                 # recording sample rate (typically 44100 for most mics)
MIC_BLOCK_SIZE = 4410          # 100ms @ 44100Hz (WDM-KS min viable block ~75ms)

_thread_pool = ThreadPoolExecutor(max_workers=2)

# Use HF mirror for model downloads (China-friendly)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HUGGINGFACE_HUB_ENDPOINT", "https://hf-mirror.com")

def _find_mic_device() -> tuple[int, int]:
    """Find the best microphone device and adjust ENERGY_THRESHOLD.

    Priority: WDM-KS (hostapi=3) > WASAPI (hostapi=2) > DirectSound > MME.
    Skips ToDesk virtual devices and Stereo Mix (loopback).
    After selection, measures ambient RMS for 1s and sets ENERGY_THRESHOLD
    to 3× ambient RMS (min 2000) so VAD adapts to the device's gain level.

    Returns (device_index, sample_rate).
    """
    global ENERGY_THRESHOLD
    import sounddevice as _sd
    devices = _sd.query_devices()
    default_idx = _sd.default.device[0]

    def is_mic(d):
        name = d["name"]
        if "ToDesk" in name or "todesk" in name.lower():
            return False
        if "立体声混音" in name or "Stereo" in name or "output" in name.lower():
            return False
        if d["max_input_channels"] == 0:
            return False
        return True

    def test_device(idx, sr):
        """Record 1s and return peak level, or -1 on failure."""
        try:
            rec = _sd.rec(int(sr), samplerate=sr, channels=1, dtype="int16", device=idx)
            _sd.wait()
            return int(np.max(np.abs(rec)))
        except Exception:
            return -1

    best_idx, best_sr, best_peak = default_idx, 44100, 0

    for hapi in [3, 2, 1, 0]:
        for d in devices:
            if d["hostapi"] != hapi or not is_mic(d):
                continue
            idx, sr = d["index"], int(d["default_samplerate"])
            peak = test_device(idx, sr)
            if peak > best_peak:
                best_idx, best_sr, best_peak = idx, sr, peak

    dev_name = _sd.query_devices(best_idx)["name"]
    print(f"[voice] Mic device: [{best_idx}] {dev_name} ({best_sr}Hz, peak={best_peak})")

    if best_peak < 100:
        print(f"[voice] Warning: very low mic level ({best_peak})")

    # Measure ambient RMS and set ENERGY_THRESHOLD adaptively
    try:
        ambient = _sd.rec(int(best_sr), samplerate=best_sr, channels=1, dtype="int16", device=best_idx)
        _sd.wait()
        ambient_rms = float(np.sqrt(np.mean(ambient.astype(np.float64) ** 2)))
        ENERGY_THRESHOLD = max(int(ambient_rms * 3), 2000)
        print(f"[voice] Ambient RMS={ambient_rms:.0f}, ENERGY_THRESHOLD={ENERGY_THRESHOLD}")
    except Exception as e:
        print(f"[voice] Ambient measurement failed: {e}, using default threshold={ENERGY_THRESHOLD}")

    return best_idx, best_sr

# ── Mic Configuration ──────────────────────────────────────────
# Auto-detected on first recording; stores (device_index, sample_rate)
_mic_config = None  # set by _record_mic_sync on first call

# ── Dynamic STT Model Loading ──────────────────────────────────
# _model = (type: "openai"|"faster"|"paraformer", instance) or None
_model = None
_current_engine = None

WHISPER_CACHE_DIR = os.path.join(Path.home(), ".cache", "whisper")
OPENAI_WHISPER_SIZES = {
    "whisper-tiny": "tiny",
    "whisper-base": "base",
    "whisper-small": "small",
    "whisper-large-v3-turbo": "large-v3-turbo",
}
FASTER_MODEL_IDS = {
    "whisper-tiny": "Systran/faster-whisper-tiny",
    "whisper-base": "Systran/faster-whisper-base",
    "whisper-small": "Systran/faster-whisper-small",
}
PARAFORMER_MODEL_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "models", "sherpa-onnx-paraformer-zh-2023-03-28",
)


def _clear_model_cache(engine: str):
    """Delete cached model file(s) for the given engine."""
    cleared = False
    if engine in OPENAI_WHISPER_SIZES:
        fname = OPENAI_WHISPER_SIZES[engine] + ".pt"
        path = os.path.join(WHISPER_CACHE_DIR, fname)
        if os.path.exists(path):
            os.remove(path)
            print(f"[voice] Deleted cached model: {path}")
            cleared = True
        if not cleared:
            print(f"[voice] No cached file at {path}")
    elif engine == "faster-whisper":
        hf_cache = os.path.join(Path.home(), ".cache", "huggingface", "hub")
        for mid in FASTER_MODEL_IDS.values():
            repo = "models--" + mid.replace("/", "--")
            rp = os.path.join(hf_cache, repo)
            if os.path.exists(rp):
                shutil.rmtree(rp, ignore_errors=True)
                print(f"[voice] Deleted HF cache: {rp}")
                cleared = True
    return cleared


def _load_openai_whisper(engine: str):
    global _model
    model_name = OPENAI_WHISPER_SIZES[engine]
    import whisper as _w
    print(f"[voice] Loading openai-whisper/{model_name}...")
    inst = _w.load_model(model_name)
    _model = ("openai", inst)
    print(f"[voice] openai-whisper/{model_name} loaded")


def _load_faster_whisper(engine: str):
    global _model
    from faster_whisper import WhisperModel
    mid = FASTER_MODEL_IDS.get(engine, "Systran/faster-whisper-tiny")
    print(f"[voice] Loading faster-whisper: {mid}...")
    inst = WhisperModel(mid, device="cpu", compute_type="int8",
                        cpu_threads=4, num_workers=1)
    _model = ("faster", inst)
    print(f"[voice] faster-whisper loaded: {mid}")


def _download_paraformer():
    """Download paraformer model from GitHub if not present."""
    model_dir = PARAFORMER_MODEL_DIR
    model_int8 = os.path.join(model_dir, "model.int8.onnx")
    tokens_file = os.path.join(model_dir, "tokens.txt")
    if os.path.exists(model_int8) and os.path.exists(tokens_file):
        return
    os.makedirs(model_dir, exist_ok=True)
    base_url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-paraformer-zh-2023-03-28"
    files = [
        "model.int8.onnx",
        "tokens.txt",
        "README.md",
    ]
    import urllib.request
    for fname in files:
        url = f"{base_url}/{fname}"
        dest = os.path.join(model_dir, fname)
        if os.path.exists(dest):
            continue
        print(f"[voice] Downloading paraformer: {fname}...")
        try:
            urllib.request.urlretrieve(url, dest)
        except Exception:
            # fallback to requests if available
            import requests
            r = requests.get(url, stream=True, timeout=300)
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        print(f"[voice] Downloaded {fname}")


def _load_paraformer():
    """Load Paraformer model via sherpa-onnx (fast Chinese ASR)."""
    global _model
    from sherpa_onnx import OfflineRecognizer

    model_path = os.path.join(PARAFORMER_MODEL_DIR, "model.int8.onnx")
    tokens_path = os.path.join(PARAFORMER_MODEL_DIR, "tokens.txt")

    _download_paraformer()

    if not os.path.exists(model_path):
        raise RuntimeError(
            f"Paraformer model still not found after download at {model_path}"
        )

    print(f"[voice] Loading paraformer: {model_path}")
    recognizer = OfflineRecognizer.from_paraformer(
        paraformer=model_path,
        tokens=tokens_path,
        num_threads=4,
        decoding_method="greedy_search",
        provider="cpu",
    )
    _model = ("paraformer", recognizer)
    print(f"[voice] paraformer loaded OK")


def _ensure_model(engine: str):
    global _model, _current_engine
    if _model is not None and _current_engine == engine:
        return
    _model = None
    _current_engine = engine
    if engine in OPENAI_WHISPER_SIZES:
        _load_openai_whisper(engine)
    elif engine == "faster-whisper":
        _load_faster_whisper(engine)
    elif engine == "paraformer":
        _load_paraformer()
    else:
        # Default fallback
        print(f"[voice] Unknown engine '{engine}', falling back to whisper-tiny")
        _load_openai_whisper("whisper-tiny")


_TYPE_MAP = {
    "str": "string", "string": "string",
    "bool": "boolean", "boolean": "boolean",
    "int": "integer",
    "number": "number",
    "float": "number",
    "list": "array", "list[str]": "array",
    "object": "object",
    "any": "string",
}


def build_functions() -> list[dict]:
    """Convert @feature registry tools to OpenAI function calling format."""
    functions = []
    for spec in registry.all():
        if spec.deprecated or spec.hidden:
            continue
        properties = {}
        required = []
        for p in spec.params:
            json_type = _TYPE_MAP.get(p.type, "string")
            prop = {"type": json_type, "description": p.description}
            if p.default is not None:
                prop["default"] = p.default
            properties[p.name] = prop
            if p.required:
                required.append(p.name)
        functions.append({
            "type": "function",
            "function": {
                "name": spec.name,
                "description": f"{spec.display_name}: {spec.description}",
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })
    # Built-in voice command: switch STT engine
    functions.append({
        "type": "function",
        "function": {
            "name": "switch_stt_engine",
            "description": "切换语音识别引擎。用户说'切换到xxx'、'换用xxx'等时调用此函数。切换后告知用户当前引擎。",
            "parameters": {
                "type": "object",
                "properties": {
                    "engine": {
                        "type": "string",
                        "enum": ["whisper-tiny", "whisper-base", "whisper-small", "whisper-large-v3-turbo", "paraformer"],
                        "description": "目标引擎名称",
                    }
                },
                "required": ["engine"],
            },
        },
    })
    return functions


async def call_deepseek(
    messages: list[dict],
    api_key: str,
    functions: list[dict],
    client: httpx.AsyncClient,
) -> dict:
    """Call DeepSeek chat completions API (non-streaming)."""
    body = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 2048,
        "stream": False,
    }
    if functions:
        body["tools"] = functions
        body["tool_choice"] = "auto"

    resp = await client.post(
        f"{DEEPSEEK_API_BASE}/chat/completions",
        json=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    return resp.json()


def _record_mic_sync() -> tuple[str, np.ndarray]:
    """Record from mic until silence. Returns (text, audio_array at 16kHz)."""
    global _mic_config

    import queue as _queue

    # Auto-detect mic device on first call
    if _mic_config is None:
        _mic_config = _find_mic_device()

    mic_dev, rec_sr = _mic_config

    # 100ms chunks (works reliably across all device types including WDM-KS)
    chunk_dur = 0.1
    chunk_size = int(rec_sr * chunk_dur)
    silence_limit = int(SILENCE_DURATION / chunk_dur)
    confirm_limit = SPEECH_CONFIRM_CHUNKS
    IDLE_TIMEOUT = int(8.0 / chunk_dur)

    q: _queue.Queue = _queue.Queue()

    def callback(indata, frames, time, status):
        q.put(indata.copy())

    frames_arr = []
    silent_chunks = 0
    spoken = False
    speech_confirm = 0
    idle_chunks = 0
    debug_log = 0

    with sd.InputStream(
        samplerate=rec_sr, channels=1, callback=callback,
        dtype="int16", blocksize=chunk_size, device=mic_dev,
    ):
        while True:
            try:
                chunk = q.get(timeout=5)
            except _queue.Empty:
                break
            energy = np.sqrt(np.mean(chunk.astype(np.float32) ** 2))
            if debug_log < 5:
                print(f"[voice] energy[{debug_log}]={energy:.0f} (threshold={ENERGY_THRESHOLD})")
                debug_log += 1
            if energy > ENERGY_THRESHOLD:
                if spoken:
                    silent_chunks = 0
                    frames_arr.append(chunk)
                else:
                    speech_confirm += 1
                    if speech_confirm >= confirm_limit:
                        spoken = True
                        frames_arr.append(chunk)
                        speech_confirm = 0
            else:
                speech_confirm = 0
                if spoken:
                    silent_chunks += 1
                    frames_arr.append(chunk)
                    if silent_chunks > silence_limit:
                        break
                else:
                    idle_chunks += 1
                    if idle_chunks > IDLE_TIMEOUT:
                        break

    if not spoken or not frames_arr:
        return ("", np.array([], dtype=np.int16))

    audio = np.concatenate(frames_arr)
    peak = np.max(np.abs(audio))
    rms = np.sqrt(np.mean(audio.astype(np.float32) ** 2))
    print(f"[voice] recording done: {len(audio)/rec_sr:.2f}s, peak={peak}, rms={rms:.0f}")

    audio_sq = audio.squeeze()

    # Resample from device SR to SAMPLE_RATE (16000) for STT
    if rec_sr != SAMPLE_RATE:
        ratio = SAMPLE_RATE / rec_sr
        new_len = int(len(audio_sq) * ratio)
        resampled = np.interp(
            np.linspace(0.0, 1.0, new_len),
            np.linspace(0.0, 1.0, len(audio_sq)),
            audio_sq.astype(np.float64),
        ).astype(np.int16)
        print(f"[voice] resampled {len(audio_sq)}@{rec_sr} -> {len(resampled)}@{SAMPLE_RATE}")
        return ("", np.ascontiguousarray(resampled))

    return ("", np.ascontiguousarray(audio_sq))


def _transcribe(audio: np.ndarray) -> str:
    """Transcribe audio using the currently loaded STT model."""
    global _model
    if audio.ndim != 1:
        audio = audio.squeeze()
    if audio.ndim != 1 or audio.size == 0:
        return ""

    print(f"[voice] transcribe: audio.size={audio.size} ({audio.size/SAMPLE_RATE:.2f}s)")

    # Pad short audio to at least 1 second
    MIN_AUDIO_LEN = SAMPLE_RATE * 1
    if audio.size < MIN_AUDIO_LEN:
        audio = np.pad(audio, (0, MIN_AUDIO_LEN - audio.size), mode="constant")

    # Normalize volume: scale so peak amplitude hits 0.95 (prevents quiet speech from being lost)
    peak = np.max(np.abs(audio))
    if peak > 0 and peak < 30000:
        scale = 30000.0 / peak
        audio = (audio.astype(np.float32) * scale).astype(np.int16)
        audio = np.clip(audio, -32768, 32767)
    audio_f32 = audio.astype(np.float32) / 32768.0

    if _model is None:
        raise RuntimeError("STT 模型未加载")

    model_type, model_inst = _model

    try:
        if model_type == "openai":
            result = model_inst.transcribe(audio_f32, language="zh", task="transcribe")
            return result["text"].strip()
        elif model_type == "faster":
            segments, _info = model_inst.transcribe(audio_f32, language="zh")
            return "".join(seg.text for seg in segments).strip()
        elif model_type == "paraformer":
            stream = model_inst.create_stream()
            stream.accept_waveform(SAMPLE_RATE, audio_f32)
            model_inst.decode_stream(stream)
            return stream.result.text.strip()
    except RuntimeError as e:
        err_msg = str(e)
        print(f"[voice] transcribe error: {err_msg}")
        if "reshape" in err_msg or "shape" in err_msg or "size" in err_msg:
            print(f"[voice] padding to 30s and retrying...")
            padded = np.zeros(480000, dtype=np.float32)
            copy_len = min(audio_f32.size, 480000)
            padded[:copy_len] = audio_f32[:copy_len]
            if model_type == "openai":
                result = model_inst.transcribe(padded, language="zh", task="transcribe")
                return result["text"].strip()
            elif model_type == "faster":
                segs, _ = model_inst.transcribe(padded, language="zh")
                return "".join(s.text for s in segs).strip()
        raise RuntimeError(f"语音识别失败: {err_msg}")


async def handle_stt() -> str:
    """
    Record from microphone and transcribe using the loaded STT model.
    """
    loop = asyncio.get_event_loop()
    _text, audio = await loop.run_in_executor(
        _thread_pool, _record_mic_sync,
    )
    if not audio.size:
        return ""
    if _model is None:
        raise RuntimeError("未加载语音识别模型")
    text = await loop.run_in_executor(
        _thread_pool, _transcribe, audio,
    )
    return text


async def elevenlabs_tts_stream(
    text: str,
    voice_id: str,
    api_key: str,
    ws: websockets.WebSocketServerProtocol,
    client: httpx.AsyncClient,
) -> None:
    """Stream TTS audio from ElevenLabs API directly to WebSocket."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    body = {
        "text": text,
        "model_id": "eleven_flash_v2_5",
        "voice_settings": {
            "stability": 0.3,
            "similarity_boost": 0.5,
        },
    }

    async with client.stream("POST", url, json=body, headers=headers) as resp:
        if resp.status_code != 200:
            error_body = await resp.aread()
            error_text = error_body.decode("utf-8", errors="replace")[:200]
            raise RuntimeError(f"ElevenLabs TTS error {resp.status_code}: {error_text}")

        async for chunk in resp.aiter_bytes():
            if chunk:
                await ws.send(chunk)


async def llm_and_tts_loop(
    text: str,
    history: list,
    api_key: str,
    functions: list[dict],
    client: httpx.AsyncClient,
    ws: websockets.WebSocketServerProtocol,
    elevenlabs_key: str = "",
    elevenlabs_voice_id: str = "",
    engine_changed: list = None,
) -> None:
    """Process a transcript through LLM (with tool calling) and stream TTS audio."""
    if not text:
        return

    await ws.send(json.dumps({"type": "llm_start"}))
    history.append({"role": "user", "content": text})

    for _turn in range(10):
        response = await call_deepseek(history, api_key, functions, client)

        if "error" in response:
            err = response["error"]
            if isinstance(err, dict):
                err = err.get("message", str(err))
            await ws.send(json.dumps({"type": "error", "message": f"API 错误: {err}"}))
            history.pop()
            return

        choice = response["choices"][0]
        msg = choice["message"]

        if msg.get("tool_calls"):
            history.append(msg)
            for tc in msg["tool_calls"]:
                func_name = tc["function"]["name"]
                if func_name == "switch_stt_engine":
                    try:
                        args = json.loads(tc["function"]["arguments"])
                        engine = args.get("engine", "whisper-small")
                        _ensure_model(engine)
                        await ws.send(json.dumps({
                            "type": "switch_engine",
                            "engine": engine,
                        }))
                        if engine_changed is not None:
                            engine_changed[0] = engine
                            # Update system prompt with new engine
                            history[0]["content"] = SYSTEM_PROMPT.format(engine=engine)
                        result_str = f"已切换到 {engine}"
                    except Exception as e:
                        result_str = f"切换失败: {e}"
                else:
                    result_str = await handle_tool_call(tc)
                history.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result_str,
                })
            continue

        reply = (msg.get("content") or "").strip()
        if not reply:
            reply = "已完成操作。"

        history.append({"role": "assistant", "content": reply})

        if len(history) > 50:
            history = [history[0]] + history[-40:]

        await ws.send(json.dumps({"type": "llm_text", "text": reply}))

        if elevenlabs_key and elevenlabs_voice_id:
            await elevenlabs_tts_stream(reply, elevenlabs_voice_id, elevenlabs_key, ws, client)
        else:
            communicate = edge_tts.Communicate(reply, VOICE)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    await ws.send(chunk["data"])

        await ws.send(json.dumps({"type": "tts_end"}))
        return  # done


async def handle_tool_call(tool_call: dict) -> str:
    """Execute a tool call from DeepSeek via the @feature registry."""
    func_name = tool_call["function"]["name"]
    try:
        args = json.loads(tool_call["function"]["arguments"])
    except json.JSONDecodeError:
        return "Error: Invalid arguments JSON"

    result = registry.execute(func_name, **args)
    if result["success"]:
        ret = result.get("result", "")
        if not isinstance(ret, str):
            return json.dumps(ret, ensure_ascii=False)
        return ret
    else:
        return f"Error: {result.get('error', 'Unknown error')}"


async def handle_connection(ws: websockets.WebSocketServerProtocol) -> None:
    """Handle a single WebSocket conversation session."""
    try:
        init_raw = await asyncio.wait_for(ws.recv(), timeout=30)
    except asyncio.TimeoutError:
        return

    try:
        init_data = json.loads(init_raw)
    except json.JSONDecodeError:
        return

    api_key = init_data.get("apiKey", "")
    elevenlabs_key = init_data.get("elevenlabsKey", "")
    elevenlabs_voice_id = init_data.get("elevenlabsVoiceId", "")
    stt_engine = init_data.get("sttEngine", "whisper-small")

    if not api_key:
        await ws.send(json.dumps({"type": "error", "message": "API 密钥未配置"}))
        return

    # Load STT model based on engine setting
    try:
        _ensure_model(stt_engine)
    except Exception as e:
        print(f"[voice] Failed to load model '{stt_engine}': {e}")
        await ws.send(json.dumps({"type": "error", "message": f"语音模型加载失败: {e}"}))
        return

    functions = build_functions()
    engine_changed = [stt_engine]  # mutable container for llm_and_tts_loop to update
    system_prompt = SYSTEM_PROMPT.format(engine=stt_engine)
    history = [{"role": "system", "content": system_prompt}]

    # Turn processing guard: only one active turn per connection
    processing_turn = False
    turn_cancel = False

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type", "")

                if msg_type == "redownload_model":
                    try:
                        _clear_model_cache(stt_engine)
                        _ensure_model(stt_engine)
                        await ws.send(json.dumps({"type": "model_redownloaded"}))
                    except Exception as e:
                        await ws.send(json.dumps({
                            "type": "model_redownload_error",
                            "message": str(e),
                        }))
                    continue

                elif msg_type == "transcript":
                    if processing_turn:
                        continue
                    processing_turn = True
                    turn_cancel = False
                    text = data.get("text", "").strip()
                    await llm_and_tts_loop(text, history, api_key, functions, client, ws, elevenlabs_key, elevenlabs_voice_id, engine_changed)
                    processing_turn = False

                elif msg_type == "start_recording":
                    if processing_turn:
                        continue
                    processing_turn = True
                    turn_cancel = False
                    try:
                        await ws.send(json.dumps({"type": "recording_start"}))
                        text = await handle_stt()
                        if turn_cancel:
                            processing_turn = False
                            continue
                        if not text:
                            await ws.send(json.dumps({
                                "type": "stt_result", "text": "",
                            }))
                            processing_turn = False
                            continue
                        await ws.send(json.dumps({"type": "stt_result", "text": text}))
                        await llm_and_tts_loop(text, history, api_key, functions, client, ws, elevenlabs_key, elevenlabs_voice_id, engine_changed)
                    except RuntimeError as e:
                        await ws.send(json.dumps({
                            "type": "error", "message": f"语音识别失败: {e}",
                        }))
                    processing_turn = False

                elif msg_type == "cancel":
                    turn_cancel = True
                    processing_turn = False

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception:
            try:
                await ws.send(json.dumps({
                    "type": "error",
                    "message": f"服务器错误: {traceback.format_exc()[:500]}",
                }))
            except Exception:
                pass


async def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765

    async with websockets.serve(handle_connection, "127.0.0.1", port) as server:
        # Get actual port (when port=0, OS assigns a random port)
        actual_port = server.sockets[0].getsockname()[1]
        print(json.dumps({"type": "voice_server_ready", "port": actual_port}))
        sys.stdout.flush()
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
