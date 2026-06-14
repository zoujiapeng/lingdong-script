"""
agent/__main__.py — WinPilot agent IPC adapter.

Drives ``winpilot.agent.loop.AgentRunner`` in-process and streams its native
``BUS`` events to stdout as JSON lines. This replaces the legacy LingdongAgent;
the Electron front-end consumes the WinPilot native event schema directly.

Spawned by the Electron main process as ``python -u -m agent`` (cwd = python/),
same stdin/stdout JSON-line protocol as before.

Commands (stdin, one JSON object per line):
  {"type":"start","goal":"...","config":{"api_key","api_base","model",
       "agent_config":{max_steps,temperature,max_tokens,...},
       "multimodal","verify_enabled",...}}
  {"type":"stop"}                     — request graceful stop (stop_event)
  {"type":"guidance","text":"..."}    — inject a mid-run hint (interjection)
  {"type":"chat","text":"..."}        — same mechanism as guidance
  {"type":"status"}                   — reply with running state

Events (stdout, one JSON object per line): WinPilot native BUS events emitted
verbatim — agent_start, agent_step, agent_tool, agent_usage, agent_plan,
agent_recovery, playbook, world, done_check, chat, agent_error, agent_done —
plus adapter-local "log" / "status_report" / "user_guidance_sent".
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

# Optional: load API keys from .env (project root) for env-based config.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
except Exception:
    pass

import traceback
from datetime import datetime, timezone
from queue import Empty, Queue

# Put winpilot_home on sys.path so `import winpilot...` resolves regardless of
# cwd. winpilot's config.ROOT_DIR resolves to winpilot_home/, keeping all of
# WinPilot's runtime files (config.json, traces, playbooks) contained there.
_HOME = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "winpilot_home")
)
if _HOME not in sys.path:
    sys.path.insert(0, _HOME)

from winpilot.config import CONFIG

# CONFIG.set()/update() normally persist to config.json. Disable persistence so
# API keys injected from the IPC config live only in memory for this process.
CONFIG.save = lambda *a, **k: None  # type: ignore[assignment]

from winpilot.utils.log import BUS
from winpilot.agent.loop import AgentRunner
from winpilot.agent.router import build_runner
from winpilot.agent.session import SESSION

logger = logging.getLogger("agent.winpilot.ipc")

# ====================================================================
#  stdout JSON emitter (thread-safe)
# ====================================================================

_lock = threading.Lock()


def _write_line(obj: dict):
    """Write one JSON object to stdout as an atomic, flushed line."""
    payload = json.dumps(obj, default=str, ensure_ascii=False)
    try:
        with _lock:
            sys.stdout.write(payload + "\n")
            sys.stdout.flush()
    except (OSError, UnicodeEncodeError) as exc:
        try:
            sys.stderr.write(f"[agent:stdout_error] {exc}\n")
            sys.stderr.flush()
        except Exception:
            pass


def _emit_raw(event: dict):
    """BUS sink: re-emit a native WinPilot event verbatim to stdout."""
    _write_line(event)


def _log(level: str, message: str):
    _write_line({
        "type": "log",
        "level": level,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# Forward every native BUS event verbatim. The sink runs synchronously inside
# the agent thread; the body is just a guarded stdout write, so it is cheap.
BUS.add_sink(_emit_raw)


# ====================================================================
#  stdin reader (background thread)
# ====================================================================

def _stdin_reader(queue: Queue):
    """Read lines from stdin and enqueue parsed JSON commands."""
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
#  Message dispatch
# ====================================================================

class AgentIPC:
    """Bridges Electron IPC commands to a WinPilot AgentRunner."""

    def __init__(self):
        self._runner: AgentRunner | None = None
        self._thread: threading.Thread | None = None
        self._stop: threading.Event | None = None

    def handle(self, msg: dict):
        msg_type = msg.get("type", "")
        if msg_type == "start":
            self._cmd_start(msg)
        elif msg_type == "stop":
            self._cmd_stop()
        elif msg_type in ("guidance", "chat"):
            self._cmd_interject(msg)
        elif msg_type == "status":
            self._cmd_status()
        else:
            _log("warn", f"Unknown command type: {msg_type}")

    def _apply_config(self, cfg: dict):
        """Inject IPC-provided settings into WinPilot CONFIG (memory only)."""
        if cfg.get("api_key"):
            CONFIG.set(cfg["api_key"], "api", "api_key")
        if cfg.get("api_base"):
            CONFIG.set(cfg["api_base"], "api", "base_url")
        if cfg.get("model"):
            CONFIG.set(cfg["model"], "api", "model")

        ac = cfg.get("agent_config") or {}
        if "max_steps" in ac:
            try:
                CONFIG.set(int(ac["max_steps"]), "agent", "max_steps")
            except (TypeError, ValueError):
                pass
        if "temperature" in ac:
            try:
                CONFIG.set(float(ac["temperature"]), "api", "temperature")
            except (TypeError, ValueError):
                pass
        if "max_tokens" in ac:
            try:
                CONFIG.set(int(ac["max_tokens"]), "api", "max_tokens")
            except (TypeError, ValueError):
                pass

        verify = cfg.get("verify_enabled")
        if verify is not None:
            CONFIG.set(verify == "true" or verify is True, "agent", "verify_done")

        # multimodal toggle → enable the VLM perception dimension (a VLM
        # endpoint must be configured in config.json for it to actually fire).
        if cfg.get("multimodal") == "true" or cfg.get("multimodal") is True:
            CONFIG.set(True, "vlm", "enabled")
            CONFIG.set(True, "perception", "vlm", "enabled")

    def _cmd_start(self, msg: dict):
        if self._thread and self._thread.is_alive():
            _log("warn", "Agent already running — stopping previous and restarting")
            if self._stop:
                self._stop.set()
            self._thread.join(timeout=3.0)

        goal = (msg.get("goal") or "").strip()
        if not goal:
            _write_line({"type": "agent_error", "error": "goal is required"})
            return

        try:
            self._apply_config(msg.get("config") or {})
        except Exception as exc:
            _log("warn", f"Config apply failed: {exc}")

        self._stop = threading.Event()
        runner = build_runner(goal, stop_event=self._stop,
                              session_context=SESSION.recent())
        self._runner = runner

        def worker():
            try:
                runner.run()  # publishes agent_start..agent_done to BUS itself
                try:
                    SESSION.append(goal, runner.status, runner.result_text, runner.run_id)
                except Exception:
                    pass
            except Exception as exc:
                _write_line({"type": "agent_error", "error": str(exc)})
                _log("error", f"Agent crashed: {exc}\n{traceback.format_exc()}")

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()

    def _cmd_stop(self):
        if self._stop:
            self._stop.set()
            _log("info", "Stop requested — agent will halt after the current step")
        else:
            _log("warn", "No agent running to stop")

    def _cmd_interject(self, msg: dict):
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if self._runner and self._thread and self._thread.is_alive():
            try:
                self._runner.interject(text)
                _write_line({"type": "user_guidance_sent", "text": text})
            except Exception as exc:
                _log("warn", f"Interject failed: {exc}")
        else:
            _log("warn", "No running agent to receive the hint")

    def _cmd_status(self):
        running = self._thread is not None and self._thread.is_alive()
        _write_line({
            "type": "status_report",
            "running": running,
            "run_id": self._runner.run_id if self._runner else None,
        })


# ====================================================================
#  Main entry point
# ====================================================================

def main():
    ipc = AgentIPC()
    msg_queue: Queue = Queue()

    reader_thread = threading.Thread(target=_stdin_reader, args=(msg_queue,), daemon=True)
    reader_thread.start()

    _log("info", "WinPilot agent IPC started — waiting for commands")

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
                _write_line({"type": "agent_error", "error": f"handler: {exc}"})
                _log("error", f"Handler error: {exc}\n{traceback.format_exc()}")
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        if ipc._stop:
            ipc._stop.set()
        _log("info", "WinPilot agent IPC exiting")


if __name__ == "__main__":
    main()
