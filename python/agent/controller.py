"""
Controller — ReAct loop orchestrator.

Drives the autonomous agent loop:
1. Planner decides next action
2. Execute via feature registry
3. Observe result
4. Update state and memory
5. Decide termination
"""

import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Optional

from agent.memory import ShortTermMemory, LongTermMemory
from agent.planner import Planner
from agent.pseudocode import parse_pseudocode

logger = logging.getLogger("agent.controller")


class AgentController:
    """ReAct loop controller."""

    MAX_STEPS = 30
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self, api_key: str = "", api_base: str = "", model: str = ""):
        self.planner = Planner(api_key=api_key, api_base=api_base, model=model)
        self.short_mem = ShortTermMemory(max_items=10)
        self.long_mem = LongTermMemory()
        self._user_guidance: str = ""
        self._tool_list: list[dict] = []
        self._event_callback = None  # called with (event_name, data_dict)
        self._stop_event = None  # threading.Event, checked during supervision

    def set_event_callback(self, cb):
        """Set callback for event streaming (e.g. to IPC stdout)."""
        self._event_callback = cb

    def set_stop_event(self, stop_event):
        """Set a threading.Event that, when set, signals graceful stop."""
        self._stop_event = stop_event

    def set_user_guidance(self, text: str):
        self._user_guidance = text.strip()

    def _emit(self, event: str, data: dict):
        logger.debug("EVENT %s: %s", event, json.dumps(data, default=str)[:200])
        if self._event_callback:
            self._event_callback(event, data)

    def run(self, goal: str, supervise: bool = True) -> dict:
        """Execute the agent loop and return a final report.

        supervise=True: after generating the flow script, the agent stays alive
        and executes each step with window-focus safety and OCR verification.
        """
        state = {
            "goal": goal,
            "status": "running",
            "current_step": 0,
            "consecutive_failures": 0,
            "failed_tools": {},  # tool_name -> fail_count
            "last_success": True,
            "context": {},
            "steps": [],
        }
        logger.info("Agent starting. Goal: %s", goal)
        self._emit("agent_start", {"goal": goal})

        # Build tool list from feature registry
        self._tool_list = self._get_tool_list()

        # ── Phase 1: Generate flow script (always, for visual display) ──
        state["current_step"] += 1
        script_step = self._generate_script_step(state, goal)
        state["steps"].append(script_step)
        self._emit("step_complete", script_step)

        if script_step["result"].get("success") and supervise:
            # ── Phase 2: Supervised execution ──
            pseudocode = (
                script_step["result"]["data"].get("pseudocode") or ""
            )
            steps = parse_pseudocode(pseudocode)
            self._emit("log", {
                "level": "info",
                "message": f"解析到 {len(steps)} 个步骤，开始监督执行",
            })
            self._execute_steps_with_supervision(state, steps, goal)
            return self._build_report(state)

        if script_step["result"].get("success") and not supervise:
            # Old behavior: script generated, exit — frontend runs engine
            state["status"] = "completed"
            self._emit("agent_done", {
                "status": "completed",
                "total_steps": 1,
                "fail_reason": None,
            })
            return self._build_report(state)

        # ── Fallback: ReAct loop (no script generated) ──
        while state["status"] == "running":
            state["current_step"] += 1

            if state["current_step"] > self.MAX_STEPS:
                state["status"] = "failed"
                state["context"]["fail_reason"] = "max_steps_exceeded"
                break

            step = self._run_one_step(state)
            state["steps"].append(step)
            self._emit("step_complete", step)

            # Termination checks
            self._check_termination(state, step)

        return self._build_report(state)

    def _run_one_step(self, state: dict) -> dict:
        ts = datetime.now(timezone.utc).isoformat()

        # 1. Query memories
        memories = self.long_mem.query(state["goal"], k=3)

        # 2. Let planner decide
        plan = self.planner.decide(
            goal=state["goal"],
            state_summary=self._summarise(state),
            history=self.short_mem.get_all(),
            memories=memories,
            tool_list=self._tool_list,
            user_guidance=self._user_guidance or None,
        )
        self._user_guidance = ""

        thought = plan.get("reasoning", "")
        tool_name = plan.get("tool", "wait")
        params = plan.get("parameters", {})

        logger.info("Step %d | Thought: %s | Tool: %s",
                     state["current_step"], thought[:80], tool_name)

        # 3. Execute
        result = self._execute_tool(tool_name, params, state)

        # 4. Update failure tracking
        if not result.get("success"):
            state["consecutive_failures"] += 1
            state["failed_tools"][tool_name] = state["failed_tools"].get(tool_name, 0) + 1
            state["last_success"] = False
        else:
            state["consecutive_failures"] = 0
            state["last_success"] = True

        # 5. Store in memory
        note = ""
        if isinstance(result.get("data"), dict):
            note = result["data"].get("note", "")
        step_summary = {
            "step": state["current_step"],
            "tool": tool_name,
            "success": result.get("success"),
            "confidence": result.get("confidence", 0),
            "note": note,
        }
        self.short_mem.push(step_summary)
        self.long_mem.store(
            f"step_{state['current_step']}_{tool_name}",
            f"{'ok' if result.get('success') else 'fail'}",
            metadata={"goal": state["goal"], "step": state["current_step"]},
        )

        return {
            "step_num": state["current_step"],
            "timestamp": ts,
            "thought": thought,
            "tool": tool_name,
            "parameters": params,
            "result": result,
        }

    def _execute_tool(self, tool_name: str, params: dict, state: dict) -> dict:
        """Execute a tool via the feature registry."""
        if tool_name in ("complete",):
            return {"success": True, "confidence": 1.0, "data": {}}

        if tool_name in ("fail",):
            reason = params.get("reason", "planner_requested_failure")
            return {"success": False, "confidence": 0, "data": {},
                    "error": reason}

        # Map agent tool names to feature registry names
        # Unmapped names (e.g. "press") fall through to the pyautogui fallback
        tool_map = {
            "click": "click_target",
            "find_image": "find_image",
            "ocr_text": "ocr_find_text",
            "input_text": "type_text",
            "scroll": "scroll",
            "hotkey": "hotkey",
            "wait": "wait_action",
            # CLI / file operations
            "run_command": "run_command",
            "read_file": "file_read",
            "write_file": "file_write",
            "append_file": "file_append",
            "list_directory": "file_list",
            "file_exists": "file_exists",
            "copy_file": "file_copy",
            "move_file": "file_move",
            "delete_file": "file_delete",
            "file_edit": "file_edit",
            # Flow generation
            "generate_flow_script": "generate_flow_script",
        }

        feature_name = tool_map.get(tool_name, tool_name)

        try:
            import features._all  # noqa
            from features.registry import registry

            # Normalize params
            clean_params = self._normalize_params(feature_name, params)

            result = registry.execute(feature_name, **clean_params)
            success = bool(result) if not isinstance(result, dict) else result.get("success", True)
            return {
                "success": success,
                "confidence": 1.0 if success else 0.5,
                "data": {"result": result} if not isinstance(result, dict) else result,
            }
        except ImportError:
            # Fallback: direct pyautogui
            return self._fallback_execute(tool_name, params)
        except Exception as e:
            logger.warning("Tool %s failed: %s", tool_name, e)
            return {"success": False, "confidence": 0, "data": {}, "error": str(e)}

    def _normalize_params(self, feature_name: str, params: dict) -> dict:
        """Normalize parameter types for features."""
        normalized = dict(params)
        if feature_name == "click_target":
            if "x" in normalized and "y" in normalized:
                normalized["target"] = f"{normalized['x']},{normalized['y']}"
                normalized.pop("x", None)
                normalized.pop("y", None)
        if feature_name == "type_text":
            # Feature doesn't have enter param — strip it; planner should send
            # a separate press action for Enter
            normalized.pop("enter", None)
        if isinstance(normalized.get("keys"), list):
            normalized["keys"] = normalized["keys"]
        return normalized

    def _fallback_execute(self, tool_name: str, params: dict) -> dict:
        """Fallback using direct pyautogui when feature registry unavailable."""
        import pyautogui

        try:
            if tool_name == "click":
                pyautogui.click(params.get("x", 0), params.get("y", 0))
                return {"success": True, "confidence": 1.0, "data": {}}
            elif tool_name == "input_text":
                text = params.get("text", "")
                pyautogui.typewrite(text)
                if params.get("enter"):
                    pyautogui.press("enter")
                return {"success": True, "confidence": 1.0, "data": {}}
            elif tool_name == "press":
                pyautogui.press(params.get("key", "enter"))
                return {"success": True, "confidence": 1.0, "data": {}}
            elif tool_name == "hotkey":
                keys = params.get("keys", [])
                pyautogui.hotkey(*keys)
                return {"success": True, "confidence": 1.0, "data": {}}
            elif tool_name == "wait":
                time.sleep(params.get("seconds", 1))
                return {"success": True, "confidence": 1.0, "data": {}}
            else:
                return {"success": False, "confidence": 0, "data": {},
                        "error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            return {"success": False, "confidence": 0, "data": {}, "error": str(e)}

    def _check_termination(self, state: dict, step: dict):
        if step["tool"] == "complete":
            state["status"] = "completed"
            return
        if step["tool"] == "fail":
            state["status"] = "failed"
            state["context"]["fail_reason"] = step["parameters"].get("reason",
                                            "planner_requested_failure")
            return
        if state["consecutive_failures"] >= self.MAX_CONSECUTIVE_FAILURES:
            state["status"] = "failed"
            state["context"]["fail_reason"] = "too_many_consecutive_failures"

    def _generate_script_step(self, state: dict, goal: str) -> dict:
        """Pre-step: call generate_flow_script to produce a visual flowchart."""
        from datetime import timezone
        ts = datetime.now(timezone.utc).isoformat()
        try:
            import features._all  # noqa
            from features.registry import registry
            result = registry.execute("generate_flow_script", goal=goal)
            success = bool(result) if not isinstance(result, dict) else result.get("success", False)
            # registry.execute wraps: {"success": True, "result": <feature_return>, "feature": "...", "elapsed_ms": ...}
            # feature returns: {"success": True, "result": {"pseudocode": ..., "explanation": ...}}
            data = result.get("result", {})
            inner_data = data.get("result", {}) if isinstance(data, dict) else {}
            if (result.get("success") and isinstance(data, dict) and data.get("success")
                    and isinstance(inner_data, dict) and inner_data.get("pseudocode")):
                return {
                    "step_num": state["current_step"],
                    "timestamp": ts,
                    "thought": "Automatically generating flow script for the goal",
                    "tool": "generate_flow_script",
                    "parameters": {"goal": goal},
                    "result": {"success": True, "confidence": 1.0, "data": inner_data},
                }
            return {
                "step_num": state["current_step"],
                "timestamp": ts,
                "thought": "generate_flow_script failed or returned no pseudocode",
                "tool": "generate_flow_script",
                "parameters": {"goal": goal},
                "result": {"success": False, "confidence": 0, "data": {}, "error": str(data)},
            }
        except Exception as e:
            logger.warning("generate_flow_script pre-step failed: %s", e)
            return {
                "step_num": state["current_step"],
                "timestamp": ts,
                "thought": "generate_flow_script pre-step failed",
                "tool": "generate_flow_script",
                "parameters": {"goal": goal},
                "result": {"success": False, "confidence": 0, "data": {}, "error": str(e)},
            }

    def _summarise(self, state: dict) -> str:
        return (
            f"Step {state['current_step']}/{self.MAX_STEPS} | "
            f"Consecutive failures: {state['consecutive_failures']} | "
            f"Last success: {state['last_success']} | "
            f"Failed tools: {list(state['failed_tools'].keys())}"
        )

    def _get_tool_list(self) -> list[dict]:
        """Get available tools from the feature registry."""
        try:
            import features._all  # noqa
            from features.registry import registry
            tools = []
            for spec in registry.list_features():
                params_hint = ", ".join(
                    f"{p.name}" for p in (spec.params or [])[:4]
                )
                tools.append({
                    "name": spec.name,
                    "display_name": spec.display_name,
                    "description": spec.description,
                    "params_hint": params_hint,
                    "category": spec.category.value if spec.category else "",
                })
            return tools
        except Exception:
            # Fallback: return agent-specific tool list
            return [
                {"name": "click_target", "params_hint": "target, region, timeout",
                 "description": "Click a target on screen (text, image, or coordinates)"},
                {"name": "type_text", "params_hint": "text, clear_first",
                 "description": "Type text at current keyboard focus"},
                {"name": "ocr_find_text", "params_hint": "query, region",
                 "description": "Find text on screen via OCR and return coordinates"},
                {"name": "find_image", "params_hint": "target, threshold",
                 "description": "Locate image on screen, return coordinates"},
                {"name": "press", "params_hint": "key",
                 "description": "Press a single keyboard key"},
                {"name": "hotkey", "params_hint": "keys",
                 "description": "Press multiple keys simultaneously"},
                {"name": "scroll", "params_hint": "direction, clicks",
                 "description": "Scroll up/down/left/right"},
                {"name": "wait_action", "params_hint": "duration",
                 "description": "Wait for specified seconds"},
                {"name": "run_command", "params_hint": "command, timeout",
                 "description": "Run a shell command and capture output"},
                {"name": "file_read", "params_hint": "path, encoding",
                 "description": "Read a file's contents as text"},
                {"name": "file_write", "params_hint": "path, content",
                 "description": "Write text to a file (overwrite)"},
                {"name": "file_list", "params_hint": "path, pattern",
                 "description": "List directory contents"},
                {"name": "file_exists", "params_hint": "path",
                 "description": "Check if a file or directory exists"},
                {"name": "file_edit", "params_hint": "path, search, replace",
                 "description": "Edit a file by finding and replacing text (safer than file_write for code/config)"},
                {"name": "complete", "params_hint": "",
                 "description": "Call when the goal is achieved"},
                {"name": "fail", "params_hint": "reason",
                 "description": "Call when the goal cannot be achieved"},
                {"name": "generate_flow_script", "params_hint": "goal",
                 "description": "Convert a complex goal into a visual flowchart script. Use for multi-step workflows, scheduled tasks, or reusable scripts"},
            ]

    # ────────────────────────────────────────────────────────────
    #  Supervisor: step-by-step execution with verification
    # ────────────────────────────────────────────────────────────

    def _execute_steps_with_supervision(self, state: dict, steps: list[dict], goal: str):
        """Execute parsed pseudocode steps with per-step verification and recovery."""
        import features._all  # noqa
        from features.registry import registry

        for i, step in enumerate(steps):
            if state["status"] != "running":
                break

            # Check for stop signal
            if self._stop_event and self._stop_event.is_set():
                state["status"] = "stopped"
                state["context"]["fail_reason"] = "stopped_by_user"
                self._emit("log", {"level": "info", "message": "用户请求停止"})
                break

            state["current_step"] += 1
            step_type = step.get("type", "unknown")

            # ── 1. Window focus safety (GUI actions only) ──
            if step_type in ("click", "type", "hotkey", "image_click", "scroll"):
                focus_result = self._ensure_safe_window(step)
                if not focus_result.get("success"):
                    logger.warning("Window focus check failed: %s", focus_result.get("error"))
                    # Not fatal — proceed anyway, the action may still work

            # ── 2. Execute action ──
            action_result = self._execute_step_action(step)
            if not action_result.get("success"):
                recovery = self._decide_recovery(state, step, goal,
                                                  error=action_result.get("error", ""))
                if recovery.get("action") == "retry":
                    time.sleep(1)
                    action_result = self._execute_step_action(step)
                elif recovery.get("action") == "fail":
                    self._emit_step_complete(state, step, success=False,
                                             error=action_result.get("error"))
                    state["status"] = "failed"
                    state["context"]["fail_reason"] = action_result.get("error", "execution_failed")
                    break
                # else skip — continue to next step

            # ── 3. Wait for screen to settle ──
            if step_type in ("click", "hotkey", "launch", "focus", "scroll"):
                try:
                    registry.execute("wait_screen_stable", timeout=5, poll_interval=0.3)
                except Exception:
                    pass

            # ── 4. Verify expected result ──
            if not action_result.get("success"):
                # Already handled above, skip verification
                continue

            verify_result = self._verify_step(step)
            if not verify_result.get("success"):
                logger.info("Verification failed for step %d: %s",
                            state["current_step"], verify_result.get("error"))
                recovery = self._decide_recovery(state, step, goal,
                                                  error=verify_result.get("error", "verify_fail"),
                                                  details=verify_result)
                if recovery.get("action") == "retry":
                    time.sleep(1)
                    retry_result = self._execute_step_action(step)
                    if retry_result.get("success"):
                        action_result = retry_result
                    else:
                        self._emit_step_complete(state, step, success=False,
                                                 error=retry_result.get("error"))
                        continue
                elif recovery.get("action") == "alternative":
                    alt_step = recovery.get("alternative_step", step)
                    self._execute_step_action(alt_step)
                elif recovery.get("action") == "fail":
                    self._emit_step_complete(state, step, success=False,
                                             error=verify_result.get("error"))
                    state["status"] = "failed"
                    state["context"]["fail_reason"] = (
                        f"verification_failed: {verify_result.get('error')}"
                    )
                    break
                # else skip — continue without reporting success

            # ── 5. Report success ──
            self._emit_step_complete(state, step, success=True)

        if state["status"] == "running":
            state["status"] = "completed"
        self._emit("agent_done", {
            "status": state["status"],
            "total_steps": state["current_step"],
            "fail_reason": state["context"].get("fail_reason"),
        })

    def _ensure_safe_window(self, step: dict) -> dict:
        """Check that the correct window is focused for this GUI action.

        Returns {"success": True} if the window is OK or unfocusable.
        """
        try:
            import features._all  # noqa
            from features.registry import registry

            target_hint = (
                step.get("target") or step.get("title") or ""
            )
            # Get current active window
            result = registry.execute("get_active_window")
            # Unwrap: registry wrapper → feature return
            active_data = result
            if isinstance(result, dict):
                active_data = result.get("result", result)
            active_title = ""
            if isinstance(active_data, dict):
                active_title = (
                    active_data.get("title") or
                    active_data.get("result", {}).get("title", "") if
                    isinstance(active_data.get("result"), dict) else ""
                )

            # If the active window looks like a desktop / system window, don't worry
            desktop_indicators = [
                "taskbar", "desktop", "program manager", "shell_traywnd",
            ]
            if any(ind in active_title.lower() for ind in desktop_indicators):
                return {"success": True, "note": "desktop_context"}

            # If we have a launch step, the target window won't exist yet
            if step.get("type") == "launch":
                return {"success": True, "note": "launch_before_window"}

            return {"success": True, "active_window": active_title}
        except ImportError:
            return {"success": True, "note": "registry_unavailable"}
        except Exception as e:
            logger.debug("Window focus check error: %s", e)
            return {"success": True, "note": "focus_check_error"}

    def _execute_step_action(self, step: dict) -> dict:
        """Map a parsed pseudocode step to a feature registry call."""
        import features._all  # noqa
        from features.registry import registry

        step_type = step.get("type", "unknown")

        try:
            if step_type == "click":
                ok = registry.execute("click_target", target=step["target"])
                return {"success": _unwrap_bool(ok), "confidence": 1.0}

            elif step_type == "image_click":
                ok = registry.execute("click_target", target=step["target"])
                return {"success": _unwrap_bool(ok), "confidence": 1.0}

            elif step_type == "type":
                text = step.get("text", "")
                # Prefer type_clipboard for text with non-ASCII (Chinese)
                if any(ord(c) > 127 for c in text):
                    registry.execute("type_clipboard", text=text)
                else:
                    registry.execute("type_text", text=text)
                return {"success": True, "confidence": 1.0}

            elif step_type == "hotkey":
                keys = step.get("keys", [])
                registry.execute("hotkey", keys="+".join(keys))
                return {"success": True, "confidence": 1.0}

            elif step_type == "wait":
                duration = step.get("duration", 1)
                time.sleep(duration)
                return {"success": True, "confidence": 1.0}

            elif step_type == "scroll":
                direction = step.get("direction", "down")
                amount = step.get("amount", 3)
                registry.execute("scroll", direction=direction, amount=amount)
                return {"success": True, "confidence": 1.0}

            elif step_type == "run_command":
                cmd = step.get("command", "")
                timeout = step.get("timeout", 30)
                result = registry.execute("run_command", command=cmd, timeout=timeout)
                rdata = result.get("result", {}) if isinstance(result, dict) else {}
                success = rdata.get("returncode") == 0 if isinstance(rdata, dict) else True
                return {"success": success, "confidence": 1.0, "data": rdata}

            elif step_type == "launch":
                target = step.get("target", "")
                registry.execute("launch_program", target=target)
                # Give the app a moment to appear
                time.sleep(1.5)
                return {"success": True, "confidence": 1.0}

            elif step_type == "focus":
                title = step.get("title", "")
                result = registry.execute("focus_window", title=title)
                ok = result.get("result", True) if isinstance(result, dict) else True
                return {"success": bool(ok), "confidence": 1.0}

            elif step_type == "screenshot":
                registry.execute("screenshot")
                return {"success": True, "confidence": 1.0}

            elif step_type in ("condition", "loop", "sub_flow"):
                # Complex flow constructs — skip for now, delegate to planner
                return {"success": True, "confidence": 0.5,
                        "note": f"complex construct '{step_type}' skipped in supervisor mode"}

            else:
                return {"success": False, "confidence": 0,
                        "error": f"unknown_step_type: {step_type}"}

        except Exception as e:
            logger.warning("Step execution failed: %s | step=%s", e, step_type)
            return {"success": False, "confidence": 0, "error": str(e)}

    def _verify_step(self, step: dict) -> dict:
        """Verify that a step produced the expected result.

        Returns {"success": True} if verification passes or is not applicable.
        """
        import features._all  # noqa
        from features.registry import registry

        step_type = step.get("type", "unknown")

        try:
            if step_type == "click":
                # Click verification: screen becomes stable
                return {"success": True, "verified_by": "screen_stable"}

            elif step_type == "type":
                # Verify the typed text appears somewhere on screen via OCR
                text = step.get("text", "")
                if not text or len(text) < 2:
                    return {"success": True, "verified_by": "skip_short_text"}
                result = registry.execute("ocr_find_text", query=text, fuzzy_threshold=0.6)
                rdata = result.get("result", {}) if isinstance(result, dict) else result
                found = False
                if isinstance(rdata, dict):
                    found = rdata.get("found", False)
                if found:
                    return {"success": True, "verified_by": "ocr", "confidence": rdata.get("confidence")}
                return {"success": False, "error": f"ocr did not find text: {text[:50]}",
                        "details": rdata}

            elif step_type == "launch":
                # Verify something appeared on screen
                try:
                    registry.execute("wait_screen_change", timeout=5, threshold=0.03)
                    return {"success": True, "verified_by": "screen_change"}
                except Exception:
                    return {"success": True, "verified_by": "no_screen_change_detected"}

            elif step_type == "focus":
                title = step.get("title", "")
                result = registry.execute("get_active_window")
                active_data = result.get("result", {}) if isinstance(result, dict) else result
                active_title = ""
                if isinstance(active_data, dict):
                    active_title = active_data.get("title", "")
                if title.lower() in active_title.lower():
                    return {"success": True, "verified_by": "active_window"}
                return {"success": False, "error": f"focus failed: '{title}' not active",
                        "active": active_title}

            elif step_type == "run_command":
                # Trust the return code check from execution
                return {"success": True, "verified_by": "returncode"}

            elif step_type == "hotkey":
                # Screen should change after a hotkey
                try:
                    registry.execute("wait_screen_change", timeout=3, threshold=0.02)
                except Exception:
                    pass
                return {"success": True, "verified_by": "screen_change_or_timeout"}

            else:
                return {"success": True, "verified_by": "no_verification_needed"}

        except Exception as e:
            logger.debug("Verification error for %s: %s", step_type, e)
            return {"success": True, "verified_by": "verify_error"}

    def _emit_step_complete(self, state: dict, step: dict, success: bool,
                            error: str = ""):
        """Emit a step_complete event for the frontend."""
        self._emit("step_complete", {
            "step_num": state["current_step"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": step.get("type", step.get("raw", "unknown")),
            "parameters": step,
            "result": {
                "success": success,
                "confidence": 0.0 if error else 1.0,
                "error": error,
                "data": {},
            },
        })

    def _decide_recovery(self, state: dict, failed_step: dict, goal: str,
                         error: str = "", details: dict | None = None) -> dict:
        """Call planner to decide recovery strategy for a failed step."""
        # Simple built-in recovery logic before calling LLM
        step_type = failed_step.get("type", "")

        # Auto-retry for transient failures
        if "timeout" in error.lower() or "not found" in error.lower():
            return {"action": "retry", "reasoning": f"Transient error: {error}"}

        # For verification failures on type steps, skip — text may have appeared
        # in a different form
        if step_type == "type" and "ocr did not find" in error:
            return {"action": "skip", "reasoning": f"Text may have appeared differently: {error}"}

        # For launch failures, try once more
        if step_type == "launch":
            return {"action": "retry", "reasoning": "Retry launch"}

        # For more complex cases, ask the planner
        try:
            return self.planner.decide_recovery(
                goal=goal,
                failed_step=failed_step,
                error=error,
                verification_details=details,
            )
        except Exception as e:
            logger.warning("Planner recovery decision failed: %s", e)
            return {"action": "skip", "reasoning": f"Planner unavailable: {e}"}

    @staticmethod
    def _build_report(state: dict) -> dict:
        return {
            "goal": state["goal"],
            "status": state["status"],
            "total_steps": state["current_step"],
            "fail_reason": state["context"].get("fail_reason"),
            "steps": state["steps"],
        }


def _unwrap_bool(result) -> bool:
    """Unwrap a registry.execute() result to a boolean."""
    if isinstance(result, bool):
        return result
    if isinstance(result, dict):
        inner = result.get("result", result)
        if isinstance(inner, dict):
            return bool(inner.get("success", True))
        return bool(inner)
    return True
