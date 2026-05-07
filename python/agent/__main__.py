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
  {"type": "status"}          — reply with current agent state

Events (stdout, one JSON object per line, flush()ed):
  {"type": "log", "level": "info|warn|error", "message": "..."}
  {"type": "agent_start", "goal": "..."}
  {"type": "step_complete", "step_num": N, "thought":"...", "tool":"...",
       "parameters":{...}, "result": {"success": bool, "confidence": float, ...}}
  {"type": "agent_done", "status": "completed|failed|stopped",
       "total_steps": N, "fail_reason": "..."}
  {"type": "error", "message": "..."}
"""

import json
import logging
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from queue import Empty, Queue

# Ensure the parent python/ directory is on sys.path so features/ is importable
_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PATH not in sys.path:
    sys.path.insert(0, _PATH)

from agent.controller import AgentController

logger = logging.getLogger("agent.ipc")


# ====================================================================
#  stdout JSON emitter (thread-safe)
# ====================================================================

_lock = threading.Lock()


def _emit(event: str, data: dict):
    """Write a JSON event to stdout (thread-safe, atomic line)."""
    payload = json.dumps({"type": event, **data}, default=str, ensure_ascii=False)
    with _lock:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


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
#  Agent runner (background thread)
# ====================================================================

def _run_agent(goal: str, config: dict | None = None, stop_event: threading.Event = None):
    """Run the agent in a background thread, streaming events to stdout."""
    cfg = config or {}
    api_key = cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    api_base = cfg.get("api_base") or os.environ.get("LLM_API_BASE", "")
    model = cfg.get("model") or os.environ.get("LLM_MODEL", "")

    controller = AgentController(api_key=api_key, api_base=api_base, model=model)
    guidance_queue: Queue = getattr(_run_agent, "_guidance_queue", Queue())

    # Wire event callback → stdout
    controller.set_event_callback(lambda ev, d: _emit(ev, d))

    # Inject stop-awareness: patch _run_one_step to check stop_event between steps
    if stop_event:
        original_run_one = controller._run_one_step

        def _patched_run_one(state):
            if stop_event.is_set():
                state["status"] = "stopped"
                return {
                    "step_num": state["current_step"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "thought": "Stop requested by user",
                    "tool": "stop",
                    "parameters": {},
                    "result": {"success": True, "confidence": 1.0, "data": {}},
                }
            # Drain guidance queue
            msgs = []
            while True:
                try:
                    msgs.append(guidance_queue.get_nowait())
                except Empty:
                    break
            if msgs:
                controller.set_user_guidance("\n".join(msgs))
                _emit("user_guidance", {"text": "\n".join(msgs)})
            return original_run_one(state)

        controller._run_one_step = _patched_run_one

    # Store guidance queue reference for external access
    _run_agent._guidance_queue = guidance_queue

    _emit("agent_start", {"goal": goal})
    _log("info", f"Agent started — goal: {goal[:120]}")

    try:
        report = controller.run(goal)
        status = report["status"]
        if stop_event and stop_event.is_set():
            status = "stopped"
        _emit("agent_done", {
            "status": status,
            "total_steps": report["total_steps"],
            "fail_reason": report.get("fail_reason"),
        })
        _log("info", f"Agent done — status={status}, steps={report['total_steps']}")
    except Exception as exc:
        _emit("error", {"message": f"Agent crashed: {exc}"})
        _log("error", f"Agent crashed: {exc}\n{traceback.format_exc()}")
        _emit("agent_done", {
            "status": "failed",
            "total_steps": 0,
            "fail_reason": str(exc),
        })


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
        elif msg_type == "status":
            self._cmd_status()
        else:
            _log("warn", f"Unknown command type: {msg_type}")

    def _cmd_start(self, msg: dict):
        if self._agent_thread and self._agent_thread.is_alive():
            _emit("error", {"message": "Agent is already running"})
            return

        goal = (msg.get("goal") or "").strip()
        if not goal:
            _emit("error", {"message": "goal is required"})
            return

        config = msg.get("config", {})
        self._stop_event = threading.Event()
        self._guidance_queue = Queue()

        # Wire guidance queue into _run_agent
        _run_agent._guidance_queue = self._guidance_queue

        self._agent_thread = threading.Thread(
            target=_run_agent,
            args=(goal, config, self._stop_event),
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
            self._guidance_queue.put(text)
            _emit("user_guidance_sent", {"text": text})
        else:
            _log("warn", "No agent running to receive guidance")

    def _cmd_status(self):
        running = self._agent_thread is not None and self._agent_thread.is_alive()
        _emit("status_report", {
            "running": running,
            "stopped": self._stop_event is not None and self._stop_event.is_set(),
        })


# ====================================================================
#  Main entry point
# ====================================================================

def main():
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
