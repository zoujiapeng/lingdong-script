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
  {"type": "step_complete", step_num: N, thought:"...", tool:"...",
       "parameters":{...}, "result": {"success": bool, "confidence": float, ...}}
  {"type": "agent_done", "status": "completed|failed|stopped",
       "total_steps": N, "fail_reason": "..."}
  {"type": "error", "message": "..."}
"""

import json
import logging
import os
import re
import sys
import threading
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

# ── Structured step → feature tool mapping ──────────────────────
_STEP_TO_TOOL: dict[str, str] = {
    "click":            "click_target",
    "text_click":       "click_target",
    "image_click":      "find_image",
    "type":             "type_text",
    "hotkey":           "hotkey",
    "wait":             "wait_action",
    "scroll":           "scroll",
    "launch":           "launch_program",
    "focus":            "focus_window",
    "screenshot":       "screenshot",
    "run_command":      "run_command",
    "mouse_move":       "mouse_move",
    "drag":             "drag",
    "browser_connect":   "browser_connect",
    "browser_navigate":  "browser_navigate",
    "browser_click":     "browser_click",
    "browser_type":      "browser_type",
    "browser_find":      "browser_find_elements",
    "browser_wait_element": "browser_wait_element",
    "browser_get_text":  "browser_get_text",
    "browser_screenshot":"browser_screenshot",
    "browser_exec_js":   "browser_execute_js",
    "file_read":         "file_read",
    "file_write":        "file_write",
    "file_save":         "file_write",
    "check_condition":   "__check_condition__",
    "wait_until":        "__wait_until__",
}

# GUI step types that benefit from window focus + settle wait
_STEP_GUI: set[str] = {"click", "text_click", "type", "hotkey", "image_click",
                        "scroll", "drag", "launch", "focus", "mouse_move"}

# Browser step types (no visual locator, no focus settling)
_STEP_BROWSER: set[str] = {"browser_connect", "browser_navigate", "browser_click", "browser_type",
                            "browser_find", "browser_wait_element", "browser_get_text",
                            "browser_screenshot", "browser_exec_js"}


def _STEP_TO_KWARGS(step_type: str, step: dict) -> dict:
    """Convert structured step dict to keyword args for the feature tool."""
    if step_type in ("click", "text_click"):
        target = step.get("target", "")
        # If target looks like "x,y" coordinates, pass x/y directly
        if re.match(r'^-?\d+\s*,\s*-?\d+$', target):
            parts = target.split(",")
            return {"x": int(parts[0].strip()), "y": int(parts[1].strip())}
        return {"target": target}
    elif step_type == "image_click":
        return {"target": step.get("target", ""), "similarity": 0.8}
    elif step_type == "type":
        return {"text": step.get("text", "")}
    elif step_type == "hotkey":
        keys = step.get("keys", step.get("target", ""))
        if isinstance(keys, list):
            return {"keys": "+".join(keys)}
        return {"keys": str(keys)}
    elif step_type == "wait":
        return {"duration": step.get("duration", 1)}
    elif step_type == "scroll":
        return {"target": step.get("target", "down")}
    elif step_type == "launch":
        return {"target": step.get("target", "")}
    elif step_type == "focus":
        return {"title": step.get("title", step.get("target", ""))}
    elif step_type == "screenshot":
        return {}
    elif step_type == "run_command":
        return {"command": step.get("command", "")}
    elif step_type == "mouse_move":
        return {"x": step.get("x", 0), "y": step.get("y", 0)}
    elif step_type == "drag":
        return {"from_target": step.get("from_target", ""), "to_target": step.get("to_target", "")}
    elif step_type == "browser_connect":
        return {"port": step.get("port", 9222)}
    elif step_type == "browser_navigate":
        return {"url": step.get("url", step.get("target", ""))}
    elif step_type == "browser_click":
        return {"selector": step.get("selector", step.get("target", ""))}
    elif step_type == "browser_type":
        return {"selector": step.get("selector", step.get("target", "")),
                "text": step.get("text", "")}
    elif step_type == "browser_find":
        return {"selector": step.get("selector", step.get("target", ""))}
    elif step_type == "browser_wait_element":
        return {"selector": step.get("selector", step.get("target", "")),
                "timeout": step.get("timeout", 10)}
    elif step_type == "browser_get_text":
        return {"selector": step.get("selector", step.get("target", ""))}
    elif step_type == "browser_screenshot":
        return {}
    elif step_type == "browser_exec_js":
        return {"code": step.get("code", "")}
    elif step_type == "file_read":
        return {"path": step.get("path", "")}
    elif step_type == "file_write":
        return {"path": step.get("path", ""), "content": step.get("content", "")}
    elif step_type == "file_save":
        return {"path": step.get("path", ""), "content": step.get("content", "")}
    elif step_type == "check_condition":
        return {"condition": step.get("condition", ""), "timeout": step.get("timeout", 0)}
    elif step_type == "wait_until":
        return {"condition": step.get("condition", ""), "timeout": step.get("timeout", 10)}
    return {}


def _execute_supervised(tool_name: str, kwargs: dict, step_type: str,
                         stop_event: threading.Event | None,
                         step_index: int, total_steps: int) -> bool:
    """Execute a single step via the @feature registry with supervision.

    Supervision layers:
    1. Browser steps → direct execution (no focus/settle needed)
    2. GUI steps → window focus, retry with backoff, screen settle, auto-dialog
    3. Other → direct execution
    """
    from features.registry import registry

    is_gui = step_type in _STEP_GUI
    is_browser = step_type in _STEP_BROWSER
    max_retries = 2 if is_gui else 1

    for attempt in range(max_retries + 1):
        if stop_event and stop_event.is_set():
            _emit("step_complete", {
                "step_num": step_index, "tool": tool_name,
                "step_type": step_type, "status": "stopped",
                "result": {"success": False, "error": "stopped"},
            })
            return False

        if attempt > 0:
            delay = 2 ** (attempt - 1)
            _emit("log", {"level": "info",
                          "message": f"Retry {tool_name} ({attempt}/{max_retries}, {delay}s)"})
            time.sleep(delay)

        try:
            # GUI: ensure window focus before action
            if is_gui and attempt == 0:
                _focus_for_step(kwargs)

            result = registry.execute(tool_name, **kwargs)
            success = result.get("success", False)

            if not success:
                if attempt < max_retries:
                    continue
                _emit("step_complete", {
                    "step_num": step_index, "tool": tool_name,
                    "step_type": step_type, "status": "failed",
                    "result": result,
                })
                return False

            # GUI: settle wait + dialog check
            if is_gui:
                try:
                    from agent.dialog_handler import handle_any_dialog
                    handle_any_dialog()
                except Exception:
                    pass
                time.sleep(0.5)

            _emit("step_complete", {
                "step_num": step_index, "tool": tool_name,
                "step_type": step_type, "status": "completed",
                "result": result,
            })
            return True

        except Exception as exc:
            if attempt < max_retries:
                continue
            _emit("step_complete", {
                "step_num": step_index, "tool": tool_name,
                "step_type": step_type, "status": "failed",
                "result": {"success": False, "error": str(exc)},
            })
            return False

    return False


def _focus_for_step(kwargs: dict):
    """Try to ensure the target window is focused for GUI actions."""
    target = kwargs.get("target") or kwargs.get("title", "")
    if not target:
        return
    try:
        from features.registry import registry as _r
        _r.execute("focus_window", title=target)
        time.sleep(0.3)
    except Exception:
        pass


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
               supervise: bool = True,
               steps: list | None = None):
    """Run agent in a background thread, streaming events to stdout.

    Two modes:
      supervise=True  — Generate plan via LLM, then auto-execute structured_steps
                        using @feature registry (no LLM re-decision for each step).
                        If steps= is provided, skip LLM call and execute directly.
      supervise=False — Direct call to generate_flow_script only (for "生成流程图").
                        Fast path — no execution.
    """
    cfg = config or {}
    api_key = (cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")).strip()
    api_base = (cfg.get("api_base") or os.environ.get("LLM_API_BASE", "")).strip()
    model = (cfg.get("model") or os.environ.get("LLM_MODEL", "")).strip()

    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key
    if api_base:
        os.environ["LLM_API_BASE"] = api_base
    if model:
        os.environ["LLM_MODEL"] = model

    # Clear proxy env vars — the agent only calls DeepSeek API via local network
    # (no external proxy needed). If a proxy is inherited from the parent process,
    # it breaks HTTP calls made by the `requests` library.
    for _proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(_proxy_var, None)
    _log("debug", f"Proxy cleared — HTTP_PROXY={os.environ.get('HTTP_PROXY', 'N/A')}")

    # ── Fast path: supervise=False → direct generate_flow_script ──
    if not supervise:
        _emit("agent_start", {"goal": goal, "engine": "direct"})
        _log("info", f"Direct generate_flow_script — goal: {goal[:120]}")
        if not _HAS_GFS:
            _emit("error", {"message": "generate_flow_script feature not available"})
            _emit("agent_done", {"status": "failed", "total_steps": 0,
                                 "fail_reason": "generate_flow_script not available"})
            return
        try:
            result = _generate_flow_script(goal)
            success = result.get("success", False)
            _emit("step_complete", {
                "step_num": 1, "tool": "generate_flow_script",
                "args": {"goal": goal},
                "step_type": "generate_flow_script",
                "status": "completed" if success else "failed",
                "result": {
                    "success": success,
                    "confidence": 1.0 if success else 0.0,
                    "data": result.get("result", result),
                },
            })
            _emit("agent_done", {
                "status": "completed" if success else "failed",
                "total_steps": 1,
                "steps": [],
                "summary": "Flow script generated" if success else "Generation failed",
            })
        except Exception as exc:
            _emit("error", {"message": f"generate_flow_script failed: {exc}"})
            _emit("agent_done", {"status": "failed", "total_steps": 0,
                                 "fail_reason": str(exc)})
        return

    # ── Supervise mode: generate plan THEN auto-execute structured_steps ──
    # Step 1: generate the plan (or use pre-generated steps if provided)
    _emit("agent_start", {"goal": goal, "engine": "supervised"})
    _log("info", f"Supervised execution — goal: {goal[:120]}")

    if steps:
        # Pre-generated steps from the frontend's first API call — skip LLM
        _log("info", f"Using {len(steps)} pre-generated steps from frontend")
        structured_steps = steps
        pseudocode = ""
        plan_success = True

        # Re-emit step_complete to acknowledge plan source
        _emit("step_complete", {
            "step_num": 0, "tool": "generate_flow_script",
            "args": {"goal": goal},
            "step_type": "generate_flow_script",
            "status": "completed",
            "result": {
                "success": True,
                "data": {"structured_steps": steps, "pseudocode": ""},
            },
        })
    else:
        # Generate plan via LLM
        if not _HAS_GFS:
            _emit("error", {"message": "generate_flow_script feature not available"})
            _emit("agent_done", {"status": "failed", "total_steps": 0,
                                 "fail_reason": "generate_flow_script not available"})
            return

        try:
            plan = _generate_flow_script(goal)
        except Exception as exc:
            _emit("error", {"message": f"generate_flow_script failed: {exc}"})
            _emit("agent_done", {"status": "failed", "total_steps": 0, "fail_reason": str(exc)})
            return

        plan_success = plan.get("success", False)
        plan_result = plan.get("result", {})
        structured_steps = plan_result.get("structured_steps", [])
        pseudocode = plan_result.get("pseudocode", "")

        _emit("step_complete", {
            "step_num": 0, "tool": "generate_flow_script",
            "args": {"goal": goal},
            "step_type": "generate_flow_script",
            "status": "completed" if plan_success and structured_steps else "failed",
            "result": {
                "success": plan_success,
                "data": plan_result,
            },
        })

    if not plan_success or not structured_steps:
        _emit("agent_done", {
            "status": "failed",
            "total_steps": 0,
            "steps": [],
            "summary": "Failed to generate execution plan",
            "fail_reason": plan.get("error", "No structured_steps in plan"),
        })
        return

    # Step 2: auto-execute structured_steps using the @feature registry
    _log("info", f"Executing {len(structured_steps)} structured steps automatically")
    step_index = 0
    errors = 0

    try:
        from features.registry import registry
        import features._all  # noqa: F401 — ensure all features loaded
    except ImportError:
        _emit("error", {"message": "Feature registry not available"})
        _emit("agent_done", {"status": "failed", "total_steps": 0,
                             "fail_reason": "Feature registry not available"})
        return

    # Auto-launch browser if any browser_* step exists but no explicit launch step
    # CDP auto-connects on first browser_* call — no manual launch needed

    for step in structured_steps:
        step_index += 1
        if stop_event and stop_event.is_set():
            _emit("log", {"level": "info", "message": "Stop requested — skipping remaining steps"})
            break

        step_type = step.get("type", "")
        step_label = step.get("target") or step.get("text") or step.get("title") or step.get("name") or step_type

        # Map step_type → feature tool name
        tool_name = _STEP_TO_TOOL.get(step_type)
        if not tool_name:
            _emit("step_complete", {
                "step_num": step_index, "tool": step_type,
                "step_type": step_type, "status": "skipped",
                "result": {"success": False, "error": f"Unknown step type: {step_type}"},
            })
            errors += 1
            continue

        # Build kwargs from step fields
        kwargs = _STEP_TO_KWARGS(step_type, step)
        _emit("log", {"level": "info", "message": f"Step {step_index}/{len(structured_steps)}: {tool_name}({kwargs})"})

        # ── Inline handlers for special step types ──
        if tool_name == "__check_condition__":
            try:
                from features.action_map import _eval_condition
                cond_ok = _eval_condition(kwargs.get("condition", ""))
                _emit("step_complete", {
                    "step_num": step_index, "tool": "check_condition",
                    "step_type": step_type, "status": "completed" if cond_ok else "failed",
                    "result": {"success": cond_ok, "condition": kwargs.get("condition", "")},
                })
                if not cond_ok:
                    errors += 1
            except Exception as exc:
                _emit("step_complete", {
                    "step_num": step_index, "tool": "check_condition",
                    "step_type": step_type, "status": "failed",
                    "result": {"success": False, "error": str(exc)},
                })
                errors += 1
            continue

        if tool_name == "__wait_until__":
            condition = kwargs.get("condition", "")
            timeout = kwargs.get("timeout", 10)
            deadline = time.time() + timeout
            found = False
            try:
                from features.action_map import _eval_condition
                while time.time() < deadline:
                    if stop_event and stop_event.is_set():
                        break
                    if _eval_condition(condition):
                        found = True
                        break
                    time.sleep(0.5)
                _emit("step_complete", {
                    "step_num": step_index, "tool": "wait_until",
                    "step_type": step_type, "status": "completed" if found else "failed",
                    "result": {"success": found, "condition": condition, "timeout": timeout},
                })
                if not found:
                    errors += 1
            except Exception as exc:
                _emit("step_complete", {
                    "step_num": step_index, "tool": "wait_until",
                    "step_type": step_type, "status": "failed",
                    "result": {"success": False, "error": str(exc)},
                })
                errors += 1
            continue

        # Execute with supervision (focus / retry / settle)
        step_ok = _execute_supervised(tool_name, kwargs, step_type,
                                       stop_event, step_index, len(structured_steps))
        if not step_ok:
            errors += 1

    # Summary
    completed = step_index - errors
    status = "completed" if errors == 0 else ("stopped" if (stop_event and stop_event.is_set()) else "failed")
    _emit("agent_done", {
        "status": status,
        "total_steps": step_index,
        "steps": [],
        "summary": f"Executed {step_index} steps ({completed} ok, {errors} errors)",
    })
    _log("info", f"Supervised execution done — status={status}, steps={step_index}, errors={errors}")


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
        supervise = msg.get("supervise", True)
        steps = msg.get("steps")  # pre-generated structured_steps from frontend
        self._stop_event = threading.Event()
        self._guidance_queue = Queue()

        self._agent_thread = threading.Thread(
            target=_run_agent,
            args=(goal, config, self._stop_event, self._guidance_queue),
            kwargs={"supervise": supervise, "steps": steps},
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
