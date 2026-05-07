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

    def set_event_callback(self, cb):
        """Set callback for event streaming (e.g. to IPC stdout)."""
        self._event_callback = cb

    def set_user_guidance(self, text: str):
        self._user_guidance = text.strip()

    def _emit(self, event: str, data: dict):
        logger.debug("EVENT %s: %s", event, json.dumps(data, default=str)[:200])
        if self._event_callback:
            self._event_callback(event, data)

    def run(self, goal: str) -> dict:
        """Execute the agent loop and return a final report."""
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
            f"Step {state['current_step']}: {tool_name} → "
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
            ]

    @staticmethod
    def _build_report(state: dict) -> dict:
        return {
            "goal": state["goal"],
            "status": state["status"],
            "total_steps": state["current_step"],
            "fail_reason": state["context"].get("fail_reason"),
            "steps": state["steps"],
        }
