"""
Download all STT models for voice server.
Run: python python/voice/download_models.py
"""
import os
import sys
import shutil
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

CACHE_DIR = os.path.join(Path.home(), ".cache", "whisper")
HF_CACHE = os.path.join(Path.home(), ".cache", "huggingface", "hub")

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HUGGINGFACE_HUB_ENDPOINT", "https://hf-mirror.com")


def download_openai(name: str, redownload: bool = False):
    path = os.path.join(CACHE_DIR, f"{name}.pt")
    if redownload and os.path.exists(path):
        os.remove(path)
        print(f"[download] Deleted existing {name}.pt")

    import whisper
    print(f"[download] Loading openai-whisper/{name}...")
    model = whisper.load_model(name)
    print(f"[download] openai-whisper/{name} OK")


def download_faster(engine: str):
    model_id = {
        "whisper-tiny": "Systran/faster-whisper-tiny",
        "whisper-base": "Systran/faster-whisper-base",
        "whisper-small": "Systran/faster-whisper-small",
    }[engine]

    # Clean existing cache if any
    repo = "models--" + model_id.replace("/", "--")
    repo_path = os.path.join(HF_CACHE, repo)
    if os.path.exists(repo_path):
        shutil.rmtree(repo_path, ignore_errors=True)
        print(f"[download] Deleted existing HF cache for {model_id}")

    from faster_whisper import WhisperModel
    print(f"[download] Loading faster-whisper: {model_id}...")
    model = WhisperModel(model_id, device="cpu", compute_type="int8",
                         cpu_threads=4, num_workers=1)
    # Test with a tiny silent audio
    import numpy as np
    dummy = np.zeros(16000, dtype=np.float32)
    segments, info = model.transcribe(dummy, language="zh")
    list(segments)  # consume iterator
    print(f"[download] faster-whisper {model_id} OK")
    del model


if __name__ == "__main__":
    print("=== Step 1: Redownload openai-whisper/base (fresh copy) ===")
    download_openai("base", redownload=True)

    print("\n=== Step 2: Download openai-whisper/small ===")
    download_openai("small")

    print("\n=== Step 3: Download faster-whisper/base ===")
    download_faster("whisper-base")

    print("\n=== Step 4: Download faster-whisper/small ===")
    download_faster("whisper-small")

    print("\n=== All models downloaded! ===")
