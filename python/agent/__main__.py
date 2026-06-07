"""
agent/__main__.py — IPC entry point for the agent subprocess.

Follows the same stdin/stdout JSON protocol as engine/engine.py:
  - Reads JSON commands from stdin
  - Outputs JSON events to stdout (each line a flush()ed JSON object)

Designed to be spawned by the Electron main process via child_process.spawn.

Commands (stdin, one JSON object per line):
  {"type": "start", "goal": "...", "config": {"api_key":"...","api_base":"...","model":"..."}}
  {"type": "stop"}            — request graceful stop after current step
  {"type": "guidance", "text":"..."} — inject user guidance for next step
  {"type": "chat", "text":"..."} — inject chat message (conversation mode)
  {"type": "status"}          — reply with current agent state
  {"type": "memory_query"}    — query current memory snapshot

Events (stdout, one JSON object per line, flush()ed):
  {"type": "log", "level": "info|warn|error", "message": "..."}
  {"type": "agent_start", "goal": "..."}
  {"type": "step_complete", step_num: N, thought:"...", tool:"...",
       "parameters":{...}, "result": {"success": bool, "confidence": float, ...}}
  {"type": "agent_done", "status": "completed|failed|stopped",
       "total_steps": N, "fail_reason": "..."}
  {"type": "agent_reflection", "step_num": N, "insight":"...", "rule":"..."}
  {"type": "agent_learned", "rules_count": N, "prefs_count": N, "insights":[...], "mistakes":[...]}
  {"type": "memory_snapshot", "preferences": N, "rules": N, "episodes": N}
  {"type": "error", "message": "..."}
"""

import json
import logging
import os
import sys
import threading


def _enable_dpi_awareness():
    """Make Win32 window rectangles match physical screenshot pixels."""
    if os.name != "nt":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
            return
        except Exception:
            pass
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    except Exception:
        pass


_enable_dpi_awareness()

from dotenv import load_dotenv
_env_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
load_dotenv(_env_path)
import time
import traceback
from datetime import datetime, timezone
from queue import Empty, Queue

# Ensure the parent python/ directory is on sys.path so features/ is importable
_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PATH not in sys.path:
    sys.path.insert(0, _PATH)

# Import in the main thread (not the agent thread) — importing all @feature
# modules takes ~8s in the main thread but ~21s in a thread due to GIL contention.
# The fast path (supervise=False) calls this function directly.
_HAS_GFS = False
try:
    from features.flow_features import generate_flow_script as _generate_flow_script
    _HAS_GFS = True
except ImportError:
    _generate_flow_script = None

logger = logging.getLogger("agent.ipc")


# ====================================================================
#  stdout JSON emitter (thread-safe)
# ====================================================================

_lock = threading.Lock()
_AGENT_TIMEOUT = 300  # seconds — hard limit on agent.run() (browser tasks need more time)


def _emit(event: str, data: dict):
    """Write a JSON event to stdout (thread-safe, atomic line)."""
    payload = json.dumps({"type": event, **data}, default=str, ensure_ascii=False)
    try:
        with _lock:
            sys.stdout.write(payload + "\n")
            sys.stdout.flush()
    except (OSError, UnicodeEncodeError) as exc:
        # stdout pipe gone — log to stderr and carry on
        try:
            sys.stderr.write(f"[agent:stdout_error] {exc}\n")
            sys.stderr.flush()
        except Exception:
            pass
        return
    # Mirror to stderr for diagnostics (shown in frontend as [进程] messages)
    msg_preview = str(data.get('message') or data.get('goal') or data.get('status') or '')[:80]
    try:
        sys.stderr.write(f"[agent:{event}] {msg_preview}\n")
        sys.stderr.flush()
    except (UnicodeEncodeError, OSError):
        ascii_safe = msg_preview.encode('ascii', errors='replace').decode('ascii')
        try:
            sys.stderr.write(f"[agent:{event}] {ascii_safe}\n")
            sys.stderr.flush()
        except Exception:
            pass


def _log(level: str, message: str):
    _emit("log", {"level": level, "message": message, "timestamp": datetime.now(timezone.utc).isoformat()})


# ====================================================================
#  stdin reader (background thread)
# ====================================================================

def _stdin_reader(queue: Queue):
    """Read lines from stdin and enqueue parsed JSON."""
    try:
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                queue.put(json.loads(line))
            except json.JSONDecodeError:
                _log("warn", f"Invalid JSON from stdin: {line[:200]}")
    except (EOFError, OSError):
        pass


# ====================================================================
#  Agent runner (background thread) — LangGraphAgent only
# ====================================================================

def _run_agent(goal: str, config: dict | None = None,
               stop_event: threading.Event = None,
               guidance_queue: Queue = None,
               steps: list | None = None):
    """Run agent in a background thread, streaming events to stdout.

    Uses unified AgentConfig dispatch from frontend settings.
    """
    cfg = config or {}
    model_name = (cfg.get("model") or os.environ.get("VLM_MODEL", "")).strip().lower()

    # ── Build AgentConfig from IPC config ──
    from agent.config import AgentConfig

    agent_cfg_dict = cfg.get("agent_config", {})
    if "multimodal" in cfg and agent_cfg_dict.get("multimodal", "auto") == "auto":
        agent_cfg_dict = {**agent_cfg_dict, "multimodal": cfg["multimodal"]}
    _log("info", f"Using AgentConfig from frontend: {json.dumps(agent_cfg_dict, ensure_ascii=False)}")
    agent_cfg = AgentConfig.from_dict(agent_cfg_dict)

    # ── Create unified LingdongAgent ──
    from agent.agent import LingdongAgent
    agent_kw = dict(
        api_key=cfg.get("api_key") or os.environ.get("OPENAI_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", ""),
        api_base=cfg.get("api_base") or os.environ.get("OPENAI_API_BASE", "") or os.environ.get("LLM_API_BASE", ""),
        model=cfg.get("model") or os.environ.get("VLM_MODEL", "") or os.environ.get("LLM_MODEL", ""),
        event_callback=lambda d: _emit(d.get("type", "log"), {k: v for k, v in d.items() if k != "type"}) if callable(_emit) else None,
        stop_event=stop_event,
        multimodal=cfg.get("multimodal") == "true",
        auto_learn=cfg.get("auto_learn") == "true",
        omniparser_enabled=cfg.get("omniparser_enabled") == "true",
    )
    # Inject config values from AgentConfig
    if agent_cfg_dict:
        agent_kw["temperature"] = agent_cfg_dict.get("temperature", 0.1)
        agent_kw["max_tokens"] = agent_cfg_dict.get("max_tokens", 4096)
        agent_kw["llm_retries"] = agent_cfg_dict.get("llm_retries", 2)
        agent_kw["screenshot_quality"] = agent_cfg_dict.get("screenshot_quality", 60)
        agent_kw["som_enabled"] = agent_cfg_dict.get("som_enabled", True)
        agent_kw["verify_enabled"] = agent_cfg_dict.get("verify", True)
        agent_kw["local_vision_enabled"] = agent_cfg_dict.get("local_vision", False)
        if agent_cfg_dict.get("auto_learn"):
            agent_kw["auto_learn"] = True
    if guidance_queue is not None:
        agent_kw["guidance_queue"] = guidance_queue
    agent = LingdongAgent(**agent_kw)

    # ── Unified execution ──
    _emit("agent_start", {"goal": goal, "engine": agent_cfg.execution_mode,
                          "model": model_name})
    result = {}
    try:
        result = agent.run(goal, agent_config=agent_cfg, steps=steps)
        status = "completed" if result.get("success") else "failed"
        _emit("agent_done", {
            "status": status,
            "total_steps": result.get("total_steps", 0),
            "summary": result.get("summary", ""),
            "fail_reason": result.get("error", "" if result.get("success") else result.get("summary", "")),
        })
        total = result.get("total_steps", 0)
    except Exception as e:
        _log("error", f"Agent execution failed: {e}")
        _emit("agent_done", {"status": "failed", "total_steps": 0,
                             "fail_reason": str(e)})
        total = 0

    _log("info", f"Agent done — status={'success' if result.get('success') else 'failed'}, total_steps={total}")


# ====================================================================
#  Message dispatch
# ====================================================================

class AgentIPC:
    """IPC handler for the agent subprocess."""

    def __init__(self):
        self._agent_thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._guidance_queue: Queue | None = None

    def handle(self, msg: dict):
        msg_type = msg.get("type", "")

        if msg_type == "start":
            self._cmd_start(msg)
        elif msg_type == "stop":
            self._cmd_stop()
        elif msg_type == "guidance":
            self._cmd_guidance(msg)
        elif msg_type == "chat":
            self._cmd_chat(msg)
        elif msg_type == "status":
            self._cmd_status()
        elif msg_type == "memory_query":
            self._cmd_memory_query()
        else:
            _log("warn", f"Unknown command type: {msg_type}")

    def _cmd_start(self, msg: dict):
        if self._agent_thread and self._agent_thread.is_alive():
            _log("warn", "Agent already running — stopping previous and restarting")
            if self._stop_event:
                self._stop_event.set()
            # Wait up to 3s for the old agent to finish
            self._agent_thread.join(timeout=3.0)

        goal = (msg.get("goal") or "").strip()
        if not goal:
            _emit("error", {"message": "goal is required"})
            return

        config = msg.get("config", {})
        steps = msg.get("steps")  # pre-generated structured_steps from frontend
        self._stop_event = threading.Event()
        self._guidance_queue = Queue()

        self._agent_thread = threading.Thread(
            target=_run_agent,
            args=(goal, config, self._stop_event, self._guidance_queue),
            kwargs={"steps": steps},
            daemon=True,
        )
        self._agent_thread.start()

    def _cmd_stop(self):
        if self._stop_event:
            self._stop_event.set()
            _log("info", "Stop requested — agent will finish current step")
        else:
            _log("warn", "No agent running to stop")

    def _cmd_guidance(self, msg: dict):
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if self._guidance_queue is not None:
            self._guidance_queue.put({"text": text, "type": "guidance"})
            _emit("user_guidance_sent", {"text": text})
        else:
            _log("warn", "No agent running to receive guidance")

    def _cmd_chat(self, msg: dict):
        """对话模式: 接收用户聊天消息并注入 agent 的 guidance_queue。"""
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if self._guidance_queue is not None:
            self._guidance_queue.put({"text": text, "type": "chat"})
            _emit("user_guidance_sent", {"text": text, "type": "chat"})
            _log("info", f"Chat message received: {text[:100]}")
        else:
            _log("warn", "No agent running to receive chat message")

    def _cmd_status(self):
        running = self._agent_thread is not None and self._agent_thread.is_alive()
        _emit("status_report", {
            "running": running,
            "stopped": self._stop_event is not None and self._stop_event.is_set(),
        })

    def _cmd_memory_query(self):
        """查询内存快照 (从 MemoryStore)."""
        try:
            from agent.memory import MemoryStore
            mem = MemoryStore()
            _emit("memory_snapshot", mem.stats())
            _log("info", f"Memory stats: {mem.stats()}")
        except Exception as e:
            _emit("error", {"message": f"Memory query failed: {e}"})
            _log("warn", f"Memory query error: {e}")


# ====================================================================
#  Main entry point
# ====================================================================

def main():
    # Pre-load @feature registry in the main thread so agent thread doesn't block on import.
    # _all.py uses lazy module registration (registry.set_lazy_modules), so we must
    # call registry.all() to trigger actual importlib.import_module for all 43 modules.
    _log("info", "Pre-loading feature registry...")
    _emit("log", {"level": "info", "message": "正在加载功能模块..."})
    try:
        import features._all  # noqa
        from features.registry import registry
        registry.all()  # Trigger actual module loading
    except Exception as e:
        _log("warn", f"Feature pre-load failed (will retry in agent thread): {e}")
    _log("info", "Feature registry loaded")

    ipc = AgentIPC()
    msg_queue: Queue = Queue()

    reader_thread = threading.Thread(target=_stdin_reader, args=(msg_queue,), daemon=True)
    reader_thread.start()

    _log("info", "Agent IPC process started — waiting for commands")

    try:
        while True:
            try:
                msg = msg_queue.get(timeout=0.3)
            except Empty:
                if not reader_thread.is_alive():
                    _log("info", "stdin closed — shutting down")
                    break
                continue

            try:
                ipc.handle(msg)
            except Exception as exc:
                _emit("error", {"message": f"Handler error: {exc}"})
                _log("error", f"Handler error: {exc}\n{traceback.format_exc()}")

    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        # Request graceful stop
        if ipc._stop_event:
            ipc._stop_event.set()
        _log("info", "Agent IPC process exiting")


if __name__ == "__main__":
    main()
