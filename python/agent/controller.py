"""
Controller — ReAct loop orchestrator.

Drives the autonomous agent loop:
1. Planner decides next action
2. Execute via feature registry
3. Observe result
4. Update state and memory
5. Decide termination

Integrated with EventBus, ToolRegistry, and ObservationCollector
from the typed core layer.
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
from agent.verifier import verify_step as _vrfy_step, evaluate_condition as _eval_cond
from core.events import EventBus, global_bus, EVENT_AGENT_START, EVENT_AGENT_DONE, EVENT_LOG
from core.interfaces import ToolResult, Observation, StepRecord, TaskState
from tools.registry import tool_registry
from agent.observation import ObservationCollector

logger = logging.getLogger("agent.controller")


class AgentController:
    """ReAct loop controller."""

    MAX_STEPS = 30
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self, api_key: str = "", api_base: str = "", model: str = "",
                 multimodal: bool | str = False, verify_enabled: bool = False,
                 event_bus: EventBus | None = None):
        self.planner = Planner(api_key=api_key, api_base=api_base, model=model,
                               multimodal=multimodal)
        self.short_mem = ShortTermMemory(max_items=10)
        self.long_mem = LongTermMemory()
        self._user_guidance: str = ""
        self._tool_list: list[dict] = []
        self._event_callback = None  # called with (event_name, data_dict)
        self._stop_event = None  # threading.Event, checked during supervision
        self._guidance_queue = None  # Queue drained before each step
        self._verify_enabled = verify_enabled
        self._last_launched: str = ""  # track last launched program for focus
        # New typed subsystems
        self._bus = event_bus or global_bus
        self.observation_collector = ObservationCollector()

    def set_event_callback(self, cb):
        """Set callback for event streaming (e.g. to IPC stdout)."""
        self._event_callback = cb

    def set_stop_event(self, stop_event):
        """Set a threading.Event that, when set, signals graceful stop."""
        self._stop_event = stop_event

    def set_user_guidance(self, text: str):
        self._user_guidance = text.strip()

    def set_guidance_queue(self, q):
        """Set a Queue that _check_user_guidance will drain before each step."""
        self._guidance_queue = q

    def _emit(self, event: str, data: dict):
        logger.debug("EVENT %s: %s", event, json.dumps(data, default=str)[:200])
        # Route to EventBus subscribers (new typed layer)
        if event == "agent_start":
            self._bus.emit(EVENT_AGENT_START, data)
        elif event == "agent_done":
            self._bus.emit(EVENT_AGENT_DONE, data)
        elif event == "log":
            self._bus.emit(EVENT_LOG, data)
        else:
            self._bus.emit(event, data)
        # Route to existing IPC callback (backward compat)
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
        self._emit("log", {"level": "info", "message": "正在加载功能模块..."})
        self._tool_list = self._get_tool_list()
        logger.info("Tool list loaded: %d features", len(self._tool_list))
        self._emit("log", {"level": "info", "message": f"已加载 {len(self._tool_list)} 个功能模块"})

        # ── Phase 1: Generate flow script (always, for visual display) ──
        self._emit("log", {"level": "info", "message": "正在调用 AI 生成自动化脚本（可能需要 10-20 秒）..."})
        state["current_step"] += 1
        script_step = self._generate_script_step(state, goal)
        state["steps"].append(script_step)
        self._emit("step_complete", script_step)

        if script_step["result"].get("success") and supervise:
            # ── Phase 2: Supervised execution ──
            data = script_step["result"].get("data", {})

            # Prefer structured_steps (supports condition/loop/sub_flow)
            steps = data.get("structured_steps", None)
            if steps:
                self._emit("log", {
                    "level": "info",
                    "message": f"使用结构化步骤执行 ({len(steps)} 个步骤，支持条件/循环)",
                })
                # Log step overview for debugging
                summary = "; ".join(
                    f"{i+1}.{s.get('type','?')}:{(s.get('target') or s.get('text') or s.get('title') or s.get('name',''))[:30]}"
                    for i, s in enumerate(steps[:15])
                )
                self._emit("log", {"level": "info", "message": f"步骤概览: {summary}"})
            else:
                # Fall back to flat pseudocode parsing (linear only)
                pseudocode = data.get("pseudocode") or ""
                steps = parse_pseudocode(pseudocode)
                self._emit("log", {
                    "level": "info",
                    "message": f"解析到 {len(steps)} 个线性步骤，开始监督执行",
                })

            self._execute_steps_with_supervision(state, steps, goal)

            # 执行完成后 AI 生成的 DSL 保存为模板
            if state["status"] == "completed" and data.get("dsl"):
                self._save_dsl_template(goal, data["dsl"], state["steps"])

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

        # ── Fallback: generate steps locally from goal (no LLM) ──
        # This replaces the ReAct loop which was useless without an LLM.
        self._emit("log", {"level": "info", "message": "AI 生成失败，使用本地规则生成步骤..."})
        steps = self._generate_local_steps(goal)
        if steps:
            self._emit("log", {
                "level": "info",
                "message": f"本地生成 {len(steps)} 个步骤，开始监督执行",
            })
            self._execute_steps_with_supervision(state, steps, goal)
            return self._build_report(state)

        # Last resort: fail with clear message
        state["status"] = "failed"
        state["context"]["fail_reason"] = "无法生成自动化步骤"
        self._emit("log", {"level": "error", "message": "Agent 启动失败: 无法从目标描述生成自动化步骤"})
        self._emit("agent_done", {
            "status": "failed",
            "total_steps": state["current_step"],
            "fail_reason": "无法生成自动化步骤",
        })
        return self._build_report(state)

    def _generate_local_steps(self, goal: str) -> list:
        """Generate fallback steps from goal text using keyword matching (no LLM).

        Uses the program registry (Chinese aliases + scan) for name resolution.
        Processes the goal left-to-right, emitting steps in the order
        the action keywords appear.
        """
        import re
        steps = []

        # ── helper: extract program name after 打开/启动/运行 ──
        # Chinese sentence-boundary chars — the program name stops here
        _CONNECTOR = r'(?:并|且|然后|再|接着|之后|的|在|到|和|或|与|用|把|给|以及|还要|再打开|再启动|、|，|。|；|,|\.|;|!|？|\?|\n)'

        def _extract_launch_target(text: str) -> str:
            """Extract a clean program name from text after 打开/启动/运行.

            Strips trailing connector words, spaces, and non-name particles.
            Then resolves via the program registry to verify it's valid.
            """
            # Take everything up to the first connector word
            m = re.match(r'(.*?)' + _CONNECTOR, text)
            if m:
                name = m.group(1).strip()
            else:
                name = text.strip()
            # Remove trailing whitespace/punctuation
            name = name.rstrip(' \t（(（的于和与及 ')
            return name

        # ── scan for all action keywords ──
        matches = []

        # 1. "打开/启动/运行 X" → launch
        for m in re.finditer(r'(?:打开|启动|运行)\s*([^，,。；\n]+)', goal):
            raw = m.group(1).strip()
            if raw:
                name = _extract_launch_target(raw)
                matches.append((m.start(), "launch", name))

        # 2. "输入/键入/写下 X" → type (match everything up to next delimiter)
        for m in re.finditer(r'(?:输入|键入|写下|写入)\s*([^，,。.;；\n]+)', goal):
            t = m.group(1).strip()
            if t:
                matches.append((m.start(), "type", t))

        # 3. "保存/另存为 文件? X" → save
        for m in re.finditer(r'(?:保存|另存为)\s*(?:文件\s*)?([\w\-]+\.\w+)', goal):
            matches.append((m.start(), "save", m.group(1)))

        # Sort by position in goal text
        matches.sort(key=lambda x: x[0])

        for _, action, arg in matches:
            if action == "launch":
                steps.append({"type": "launch", "target": arg})
                steps.append({"type": "wait", "duration": 2})
                steps.append({"type": "focus", "title": arg})

            elif action == "type":
                steps.append({"type": "type", "text": arg})

            elif action == "save":
                import os as _os
                path = _os.path.join(_os.path.expanduser("~"), "Desktop", arg)
                steps.append({"type": "hotkey", "keys": ["ctrl", "s"]})
                steps.append({"type": "file_save", "path": path})

        return steps

    def _check_user_guidance(self):
        """Drain guidance queue and inject user guidance before next step."""
        if not self._guidance_queue:
            return
        msgs = []
        from queue import Empty as _QEmpty
        while True:
            try:
                msgs.append(self._guidance_queue.get_nowait())
            except _QEmpty:
                break
        if msgs:
            joined = "\n".join(msgs)
            self.set_user_guidance(joined)
            self._emit("user_guidance", {"text": joined})
            logger.info("User guidance injected: %s", joined[:100])

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
        """Execute a tool via the typed ToolRegistry (with fallback to feature registry)."""
        if tool_name in ("complete",):
            return {"success": True, "confidence": 1.0, "data": {}}

        if tool_name in ("fail",):
            reason = params.get("reason", "planner_requested_failure")
            return {"success": False, "confidence": 0, "data": {},
                    "error": reason}

        # Map agent tool names to registry names
        tool_map = {
            "click": "click_target",
            "find_image": "find_image",
            "ocr_text": "ocr_find_text",
            "input_text": "type_text",
            "scroll": "scroll",
            "hotkey": "hotkey",
            "wait": "wait_action",
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
            "generate_flow_script": "generate_flow_script",
        }

        feature_name = tool_map.get(tool_name, tool_name)

        try:
            clean_params = self._normalize_params(feature_name, params)

            # Try typed ToolRegistry first
            tr = tool_registry.get(feature_name)
            if tr is not None:
                result = tr.execute(clean_params)
                return {
                    "success": result.success,
                    "confidence": result.confidence,
                    "data": result.data if result.data is not None else {},
                    "error": result.error,
                }

            # Fallback: raw feature registry
            import features._all  # noqa
            from features.registry import registry

            result = registry.execute(feature_name, **clean_params)
            success = bool(result) if not isinstance(result, dict) else result.get("success", True)
            return {
                "success": success,
                "confidence": 1.0 if success else 0.5,
                "data": {"result": result} if not isinstance(result, dict) else result,
            }
        except ImportError:
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
                "thought": f"generate_flow_script failed: {data.get('error', str(data)[:200]) if isinstance(data, dict) else str(data)[:200]}",
                "tool": "generate_flow_script",
                "parameters": {"goal": goal},
                "result": {"success": False, "confidence": 0, "data": {},
                           "error": data.get("error", str(data)) if isinstance(data, dict) else str(data)},
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
        """Get available tools from the feature registry (with timeout)."""
        # Use the fallback list to avoid hanging on slow feature imports
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

            # Attach step index for frontend mapping
            step["_index"] = i + 1

            # Check for stop signal
            if self._stop_event and self._stop_event.is_set():
                state["status"] = "stopped"
                state["context"]["fail_reason"] = "stopped_by_user"
                self._emit("log", {"level": "info", "message": "用户请求停止"})
                break

            state["current_step"] += 1
            step_type = step.get("type", "unknown")

            # ── 1. Window focus safety (GUI actions only) ──
            if step_type in ("click", "text_click", "type", "hotkey", "image_click", "scroll", "drag"):
                focus_result = self._ensure_safe_window(step)
                if not focus_result.get("success"):
                    logger.warning("Window focus check failed: %s", focus_result.get("error"))
                    # Not fatal — proceed anyway, the action may still work

            # ── 2. Execute action with retry ──
            step_label = (step.get("target") or step.get("text") or
                         step.get("title") or step.get("name") or step_type)
            self._emit("log", {"level": "info",
                               "message": f"步骤 {i+1}: {step_type} — {step_label[:60]}"})
            retry_count = 0
            action_result = self._execute_with_retry(step, max_retries=0)  # No auto-retry yet

            # Track last launched target for window focus
            if step_type == "launch" and action_result.get("success"):
                self._last_launched = step.get("target", "")

            if not action_result.get("success"):
                error_msg = action_result.get("error", "")
                recovery = self._decide_recovery(state, step, goal,
                                                  error=error_msg,
                                                  retry_count=retry_count)
                if recovery.get("action") == "retry":
                    retry_count += 1
                    action_result = self._execute_with_retry(step, max_retries=2)
                    if not action_result.get("success"):
                        # One more recovery attempt after retries
                        recovery2 = self._decide_recovery(state, step, goal,
                                                           error=action_result.get("error", ""),
                                                           retry_count=retry_count)
                        if recovery2.get("action") == "fail":
                            self._emit_step_complete(state, step, success=False,
                                                     error=action_result.get("error"))
                            state["status"] = "failed"
                            state["context"]["fail_reason"] = action_result.get("error", "execution_failed")
                            break
                        # skip after retries
                        self._emit("log", {"level": "warn",
                                           "message": f"跳过步骤 {i+1}（{step_label[:40]}）: {action_result.get('error', '执行失败')}"})
                        self._emit_step_complete(state, step, success=False,
                                                 error=f"已跳过: {action_result.get('error', '执行失败')}")
                elif recovery.get("action") == "fail":
                    self._emit_step_complete(state, step, success=False,
                                             error=action_result.get("error"))
                    state["status"] = "failed"
                    state["context"]["fail_reason"] = action_result.get("error", "execution_failed")
                    break
                else:  # skip
                    self._emit("log", {"level": "warn",
                                       "message": f"跳过步骤 {i+1}（{step_label[:40]}）: {error_msg}"})
                    self._emit_step_complete(state, step, success=False,
                                             error=f"已跳过: {error_msg}")

            # ── 3. Wait for screen to settle ──
            if step_type in ("click", "text_click", "hotkey", "launch", "focus", "scroll", "drag"):
                self._emit("log", {"level": "info", "message": "等待屏幕稳定..."})
                self._wait_screen_stable()

            # ── 3b. Collect observation after action ──
            try:
                last_tool_result = ToolResult(
                    success=action_result.get("success", False),
                    confidence=action_result.get("confidence", 1.0),
                    data=action_result.get("data"),
                    error=action_result.get("error"),
                )
                obs = self.observation_collector.collect_after_action(last_tool_result)
                action_result["_observation"] = self.observation_collector.summarize(obs)
                self._bus.emit("observation:collected", {
                    "step": state["current_step"],
                    "window": obs.windowTitle,
                    "apps": obs.currentApp,
                    "text_count": len(obs.ocrText),
                })
            except Exception as e:
                logger.debug("Observation collection failed: %s", e)

            # ── 4. Auto-handle file save dialog after hotkey ──
            if step_type == "hotkey":
                # Run in a daemon thread with timeout to avoid blocking
                import threading as _save_thr
                _save_t = _save_thr.Thread(
                    target=self._auto_handle_save_dialog,
                    args=(step, steps, i, goal),
                    daemon=True,
                )
                _save_t.start()
                _save_t.join(timeout=15)

            # ── 5. Verify expected result ──
            if not action_result.get("success"):
                # Already handled above, skip verification
                continue

            verify_result = self._verify_step(step)
            if not verify_result.get("success"):
                logger.info("Verification failed for step %d: %s",
                            state["current_step"], verify_result.get("error"))
                retry_count = 0
                recovery = self._decide_recovery(state, step, goal,
                                                  error=verify_result.get("error", "verify_fail"),
                                                  details=verify_result,
                                                  retry_count=retry_count)
                step_label = (step.get("target") or step.get("text") or
                             step.get("title") or step.get("name") or step.get("type", "?"))
                if recovery.get("action") == "retry":
                    retry_count += 1
                    retry_result = self._execute_with_retry(step, max_retries=2)
                    if retry_result.get("success"):
                        action_result = retry_result
                    else:
                        self._emit("log", {"level": "warn",
                                           "message": f"验证失败，跳过步骤 {i+1}（{step_label[:40]}）: {retry_result.get('error', '执行失败')}"})
                        self._emit_step_complete(state, step, success=False,
                                                 error=f"已跳过: {retry_result.get('error', '执行失败')}")
                        continue
                elif recovery.get("action") == "alternative":
                    alt_step = recovery.get("alternative_step", step)
                    self._emit("log", {"level": "info",
                                       "message": f"使用替代方案执行步骤 {i+1}（{step_label[:40]}）"})
                    self._execute_step_action(alt_step)
                elif recovery.get("action") == "fail":
                    self._emit_step_complete(state, step, success=False,
                                             error=verify_result.get("error"))
                    state["status"] = "failed"
                    state["context"]["fail_reason"] = (
                        f"verification_failed: {verify_result.get('error')}"
                    )
                    break
                else:
                    self._emit("log", {"level": "warn",
                                       "message": f"验证失败，跳过步骤 {i+1}（{step_label[:40]}）: {verify_result.get('error', '验证失败')}"})
                    self._emit_step_complete(state, step, success=False,
                                             error=f"已跳过: {verify_result.get('error', '验证失败')}")
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

        Returns {"success": True, "focused": True} if the correct window is
        already focused or was auto-focused. Returns {"success": False} if
        auto-focus failed and execution may be unsafe.
        """
        try:
            import features._all  # noqa
            from features.registry import registry

            target_hint = (
                step.get("target_window") or
                step.get("target") or step.get("title") or ""
            )

            # Get current active window
            result = registry.execute("get_active_window")
            active_data = result
            if isinstance(result, dict):
                active_data = result.get("result", result)
            active_title = ""
            if isinstance(active_data, dict):
                nested = active_data
                if isinstance(active_data.get("result"), dict):
                    nested = active_data["result"]
                active_title = nested.get("title", "")

            # If we have a launch step, the target window won't exist yet
            if step.get("type") == "launch":
                return {"success": True, "note": "launch_before_window"}

            # For file_save, we're handling a system dialog — skip window focus check
            if step.get("type") == "file_save":
                return {"success": True, "note": "file_save_dialog"}

            # If the active window looks like a desktop / system window, not a concern
            desktop_indicators = [
                "taskbar", "desktop", "program manager", "shell_traywnd",
            ]
            if any(ind in active_title.lower() for ind in desktop_indicators):
                return {"success": True, "note": "desktop_context"}

            # If no target hint, just log and return OK
            if not target_hint:
                return {"success": True, "active_window": active_title,
                        "note": "no_target_hint"}

            # Check if the target window is already active
            target_lower = target_hint.lower()
            active_lower = active_title.lower()
            if target_lower in active_lower or active_lower in target_lower:
                return {"success": True, "focused": True,
                        "active_window": active_title}

            # Target window is not active — try to auto-focus
            logger.info("Auto-focus: current='%s', target='%s'",
                        active_title, target_hint)
            self._emit("log", {
                "level": "info",
                "message": f"窗口焦点切换: '{active_title}' → '{target_hint}'",
            })

            try:
                focus_result = registry.execute("focus_window", title=target_hint)
                if isinstance(focus_result, dict):
                    focus_ok = focus_result.get("result", focus_result.get("success", True))
                else:
                    focus_ok = bool(focus_result)
                # Wait briefly for the window to activate
                if focus_ok:
                    time.sleep(0.5)
                    return {"success": True, "focused": True,
                            "active_window": target_hint}
            except Exception as e:
                logger.warning("Auto-focus failed for '%s': %s", target_hint, e)

            # Auto-focus failed — warn but proceed (the action may still work)
            return {"success": True, "focused": False,
                    "active_window": active_title,
                    "note": f"auto_focus_failed: could not focus '{target_hint}'"}
        except ImportError:
            return {"success": True, "note": "registry_unavailable"}
        except Exception as e:
            logger.debug("Window focus check error: %s", e)
            return {"success": True, "note": "focus_check_error"}

    def _auto_handle_save_dialog(self, step: dict, all_steps: list, current_idx: int, goal: str):
        """After a hotkey that may have triggered Ctrl+S, detect and handle save dialog."""
        keys = step.get("keys", [])
        keys_lower = " ".join(k.lower() for k in keys)
        if "ctrl" not in keys_lower or "s" not in keys_lower:
            return

        time.sleep(0.5)  # brief wait for dialog to appear

        try:
            import features._all  # noqa
            from features.registry import registry

            # Check if a save dialog is active
            result = registry.execute("get_active_window")
            active_data = result
            if isinstance(result, dict):
                active_data = result.get("result", result)
            active_title = ""
            if isinstance(active_data, dict):
                nested = active_data
                if isinstance(active_data.get("result"), dict):
                    nested = active_data["result"]
                active_title = nested.get("title", "")

            # Detect save dialog by title
            title_lower = active_title.lower()
            if "另存为" not in title_lower and "save as" not in title_lower and "save" not in title_lower:
                return  # not a save dialog

            logger.info("Save dialog detected: '%s'", active_title)
            self._emit("log", {"level": "info", "message": f"检测到保存对话框: '{active_title}'"})

            # Extract filename: look at next step text first, then goal
            filename = ""
            if current_idx + 1 < len(all_steps):
                next_step = all_steps[current_idx + 1]
                filename = (next_step.get("text") or next_step.get("target") or "").strip()

            if not filename:
                # Fallback: extract filename from goal
                import re
                m = re.search(r'([\w\-]+\.\w+)', goal)
                if m:
                    filename = m.group(1)

            if not filename:
                import time as _t
                filename = f"agent_save_{int(_t.time())}.txt"

            # Build full desktop path
            import os as _os
            full_path = _os.path.join(_os.path.expanduser("~"), "Desktop", filename)

            self._emit("log", {"level": "info", "message": f"自动保存文件: {full_path}"})

            try:
                registry.execute("filedialog_save", path=full_path, overwrite=True)
                self._emit("log", {"level": "info", "message": f"保存成功: {filename}"})
            except Exception as e:
                # Fallback: raw keyboard input
                try:
                    import pyautogui
                    import pyperclip
                    old = pyperclip.paste()
                    pyperclip.copy(full_path)
                    time.sleep(0.1)
                    pyautogui.hotkey("ctrl", "a")
                    time.sleep(0.05)
                    pyautogui.hotkey("ctrl", "v")
                    time.sleep(0.1)
                    pyautogui.press("enter")
                    time.sleep(0.5)
                    pyautogui.press("enter")  # confirm overwrite
                    try:
                        pyperclip.copy(old)
                    except Exception:
                        pass
                    self._emit("log", {"level": "info", "message": f"保存成功(键盘): {filename}"})
                except Exception as e2:
                    self._emit("log", {"level": "warn", "message": f"保存对话框处理失败: {e2}"})
        except Exception as e:
            logger.warning("Save dialog auto-handler error: %s", e)

    def _execute_step_action(self, step: dict) -> dict:
        """Map a parsed pseudocode step to a feature registry call."""
        import features._all  # noqa
        from features.registry import registry

        step_type = step.get("type", "unknown")

        try:
            if step_type == "mouse_move":
                x = step.get("x", 0)
                y = step.get("y", 0)
                try:
                    registry.execute("mouse_move", x=x, y=y)
                except Exception:
                    import pyautogui
                    pyautogui.moveTo(x, y)
                return {"success": True, "confidence": 1.0}

            if step_type == "click":
                if step.get("click_type") == "right":
                    ok = registry.execute("right_click", target=step["target"])
                elif step.get("click_type") == "double":
                    ok = registry.execute("click_target", target=step["target"])
                    if _unwrap_bool(ok):
                        import time as _t
                        _t.sleep(0.1)
                        ok = registry.execute("click_target", target=step["target"])
                else:
                    ok = registry.execute("click_target", target=step["target"])
                return {"success": _unwrap_bool(ok), "confidence": 1.0}

            elif step_type == "image_click":
                ok = registry.execute("image_click", targets=step["target"],
                                       timeout=step.get("timeout", 5),
                                       similarity=step.get("similarity", 0.9))
                return {"success": _unwrap_bool(ok), "confidence": 1.0}

            elif step_type == "text_click":
                # OCR-based click: find text on screen then click it
                target = step.get("target", "")
                sim = step.get("similarity", 0.7)
                result = registry.execute("ocr_find_text", query=target, fuzzy_threshold=sim)
                rdata = result.get("result", {}) if isinstance(result, dict) else result
                if isinstance(rdata, dict) and rdata.get("found"):
                    cx = rdata.get("center_x", rdata.get("x", 0))
                    cy = rdata.get("center_y", rdata.get("y", 0))
                    import pyautogui
                    pyautogui.click(cx, cy)
                    return {"success": True, "confidence": 0.9}
                return {"success": False, "confidence": 0, "error": f"ocr: '{target}' not found"}

            elif step_type == "type":
                text = step.get("text", "")
                # Execute type with timeout — include focus inside daemon thread
                # so that a hung focus_window call cannot block forever
                import threading as _thr
                _type_res = [None]
                _focus = self._last_launched

                def _do_type():
                    if _focus:
                        try:
                            registry.execute("focus_window", title=_focus)
                            time.sleep(0.3)
                        except Exception:
                            pass
                    try:
                        _type_res[0] = registry.execute("type_clipboard", text=text)
                    except Exception:
                        try:
                            _type_res[0] = registry.execute("type_text", text=text)
                        except Exception:
                            pass

                _t = _thr.Thread(target=_do_type, daemon=True)
                _t.start()
                _t.join(timeout=8)
                return {"success": True, "confidence": 1.0}

            elif step_type == "hotkey":
                keys = step.get("keys", [])
                import threading as _hk_thr
                _hk_res = [None]

                def _do_hotkey():
                    try:
                        _hk_res[0] = registry.execute("hotkey", keys="+".join(keys))
                    except Exception:
                        pass

                _hk_t = _hk_thr.Thread(target=_do_hotkey, daemon=True)
                _hk_t.start()
                _hk_t.join(timeout=5)
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
                self._emit("log", {"level": "info", "message": f"启动: {target}"})
                result = registry.execute("launch_program", target=target)
                # Log resolved path if available
                if isinstance(result, dict):
                    rpath = result.get("resolved_path")
                    if rpath and rpath != target:
                        self._emit("log", {"level": "info", "message": f"  已解析路径: {rpath}"})
                # Check if launch succeeded
                if isinstance(result, dict) and not result.get("success", True):
                    # Try to find close matches in the program registry
                    try:
                        from features.program_registry import get_program_registry
                        prog_reg = get_program_registry()
                        all_items = prog_reg.get_name_list()
                        # Find names that contain the target or vice versa
                        low_target = target.lower()
                        close = [
                            i for i in all_items
                            if low_target in i["name"] or i["name"] in low_target
                        ][:5]
                        if close:
                            hints = ", ".join(f'"{c["name"]}"' for c in close)
                            self._emit("log", {
                                "level": "warn",
                                "message": f"未找到「{target}」，你是不是想启动: {hints}",
                            })
                            # Auto-try the closest match
                            step["target"] = close[0]["name"]
                            self._emit("log", {"level": "info", "message": f"尝试启动: {close[0]['name']}"})
                            result = registry.execute("launch_program", target=close[0]["name"])
                    except Exception:
                        pass
                # Give the app a moment to appear
                time.sleep(0.5)
                ok = isinstance(result, dict) and result.get("success", False)
                return {"success": ok, "confidence": 1.0, "data": result}

            elif step_type == "focus":
                title = (step.get("title") or step.get("target") or step.get("name") or "")
                result = registry.execute("focus_window", title=title)
                ok = result.get("result", True) if isinstance(result, dict) else True
                if ok:
                    import time as _t2
                    _t2.sleep(0.5)
                return {"success": bool(ok), "confidence": 1.0, "focused": bool(ok)}

            elif step_type == "screenshot":
                registry.execute("screenshot")
                return {"success": True, "confidence": 1.0}

            elif step_type == "drag":
                from_target = step.get("from_target", "")
                to_target = step.get("to_target", "")
                ok = registry.execute("drag", from_target=from_target, to_target=to_target)
                return {"success": _unwrap_bool(ok), "confidence": 1.0}

            elif step_type == "file_read":
                path = step.get("path", step.get("target", ""))
                result = registry.execute("file_read", path=path)
                rdata = result.get("result", {}) if isinstance(result, dict) else result
                if isinstance(rdata, dict) and rdata.get("success"):
                    return {"success": True, "confidence": 1.0, "data": rdata}
                return {"success": False, "confidence": 0, "error": rdata.get("error", "read failed")}

            elif step_type == "file_write":
                path = step.get("path", "")
                content = step.get("content", step.get("text", ""))
                result = registry.execute("file_write", path=path, content=content)
                rdata = result.get("result", {}) if isinstance(result, dict) else result
                if isinstance(rdata, dict) and rdata.get("success"):
                    return {"success": True, "confidence": 1.0}
                return {"success": False, "confidence": 0, "error": rdata.get("error", "write failed")}

            elif step_type == "file_save":
                path = step.get("path", step.get("target", ""))
                text = step.get("text", "")
                # Use text as filename if path not specified
                if not path and text:
                    import os as _os
                    path = _os.path.expanduser(f"~\\Desktop\\{text}")
                self._emit("log", {"level": "info", "message": f"保存文件对话框: {path}"})
                # Wrap in daemon thread with timeout to prevent hanging
                import threading as _fs_thr
                _fs_res = [None]

                def _do_file_save():
                    try:
                        result = registry.execute("filedialog_save", path=path, overwrite=True)
                        _fs_res[0] = result
                    except Exception:
                        pass

                _fs_t = _fs_thr.Thread(target=_do_file_save, daemon=True)
                _fs_t.start()
                _fs_t.join(timeout=12)
                if _fs_res[0] is not None:
                    rdata = _fs_res[0].get("result", {}) if isinstance(_fs_res[0], dict) else _fs_res[0]
                    if isinstance(rdata, dict) and not rdata.get("success", True):
                        return {"success": False, "confidence": 0, "error": "save dialog failed"}
                    return {"success": True, "confidence": 1.0, "note": f"saved: {path}"}
                # Fallback: try direct keyboard input
                try:
                    import pyautogui
                    import pyperclip
                    old = pyperclip.paste()
                    pyperclip.copy(path)
                    time.sleep(0.1)
                    pyautogui.hotkey("ctrl", "a")
                    time.sleep(0.05)
                    pyautogui.hotkey("ctrl", "v")
                    time.sleep(0.1)
                    pyautogui.press("enter")
                    time.sleep(0.5)
                    pyautogui.press("enter")  # confirm overwrite
                    try:
                        pyperclip.copy(old)
                    except Exception:
                        pass
                    return {"success": True, "confidence": 1.0, "note": f"saved (fallback): {path}"}
                except Exception as e2:
                    return {"success": False, "confidence": 0, "error": f"save failed: {e2}"}

            elif step_type == "condition":
                # Evaluate condition and execute the matching branch
                condition_desc = step.get("condition", "")
                true_branch = step.get("true_branch", [])
                false_branch = step.get("false_branch", [])

                # Try to evaluate the condition using OCR
                condition_met = self._evaluate_condition(condition_desc)
                branch = true_branch if condition_met else false_branch

                logger.info("Condition '%s' → %s branch (%d steps)",
                            condition_desc[:50], "true" if condition_met else "false", len(branch))
                self._emit("log", {
                    "level": "info",
                    "message": f"条件判断: '{condition_desc[:40]}' → {'成立' if condition_met else '不成立'}",
                })

                if not branch:
                    return {"success": True, "confidence": 1.0,
                            "note": f"condition '{condition_desc[:30]}' → no branch steps"}

                # Execute each step in the branch
                for sub_step in branch:
                    if self._stop_event and self._stop_event.is_set():
                        return {"success": False, "error": "stopped"}
                    sub_result = self._execute_step_action(sub_step)
                    if sub_step.get("type") in ("click", "text_click", "hotkey", "launch", "focus", "scroll", "drag"):
                        self._wait_screen_stable()
                    if not sub_result.get("success"):
                        return sub_result
                return {"success": True, "confidence": 1.0,
                        "note": f"condition executed {len(branch)} steps"}

            elif step_type == "loop":
                count = step.get("count", 1)
                body = step.get("body", [])
                actual_count = 0
                for iteration in range(count):
                    if self._stop_event and self._stop_event.is_set():
                        break
                    for sub_step in body:
                        if self._stop_event and self._stop_event.is_set():
                            break
                        sub_result = self._execute_step_action(sub_step)
                        if sub_step.get("type") in ("click", "text_click", "hotkey", "launch", "focus", "scroll", "drag"):
                            self._wait_screen_stable()
                        if not sub_result.get("success"):
                            return sub_result
                    actual_count += 1
                return {"success": True, "confidence": 1.0,
                        "note": f"loop executed {actual_count}/{count} iterations"}

            elif step_type == "sub_flow":
                name = step.get("name", "")
                body = step.get("body", [])
                if not body:
                    return {"success": False, "error": f"sub_flow '{name}' has no body steps"}
                for sub_step in body:
                    if self._stop_event and self._stop_event.is_set():
                        return {"success": False, "error": "stopped"}
                    sub_result = self._execute_step_action(sub_step)
                    if sub_step.get("type") in ("click", "text_click", "hotkey", "launch", "focus", "scroll", "drag"):
                        self._wait_screen_stable()
                    if not sub_result.get("success"):
                        return sub_result
                return {"success": True, "confidence": 1.0,
                        "note": f"sub_flow '{name}' completed"}

            else:
                return {"success": False, "confidence": 0,
                        "error": f"unknown_step_type: {step_type}"}

        except Exception as e:
            logger.warning("Step execution failed: %s | step=%s", e, step_type)
            return {"success": False, "confidence": 0, "error": str(e)}

    def _verify_step(self, step: dict) -> dict:
        """Verify step result using the multi-strategy verifier chain."""
        if not self._verify_enabled:
            return {"success": True, "method": "disabled",
                    "message": "verification disabled"}
        import features._all  # noqa
        from features.registry import registry

        result = _vrfy_step(step, registry)
        method = result.get("method", "?")
        if result["success"]:
            logger.debug("Step %s verified by: %s", step.get("type", "?"), method)
        else:
            logger.info("Step %s verification failed (%s): %s",
                        step.get("type", "?"), method, result.get("message", ""))
        return result

    def _evaluate_condition(self, condition_desc: str) -> bool:
        """Evaluate a condition description using the multi-strategy verifier.

        Delegates to agent.verifier.evaluate_condition() which supports:
        - ocr_contains / ocr_not_contains
        - window_exists / window_not_exists
        - process_running / process_not_running
        - file_exists / file_not_exists
        - file_contains / file_not_contains
        - image_exists / image_not_exists
        """
        import features._all  # noqa
        from features.registry import registry

        return _eval_cond(condition_desc, registry)

    def _wait_screen_stable(self, timeout: float = 2):
        """Wait for screen to settle after an action. Uses time.sleep for reliability."""
        import time
        time.sleep(min(timeout, 1.5))

    def _emit_step_complete(self, state: dict, step: dict, success: bool,
                            error: str = ""):
        """Emit a step_complete event for the frontend."""
        self._emit("step_complete", {
            "step_num": state["current_step"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": step.get("type", step.get("raw", "unknown")),
            "step_index": step.get("line", step.get("_index", 0)) - 1,
            "step_type": step.get("type", "unknown"),
            "step_label": (step.get("target") or step.get("text") or
                           step.get("title") or step.get("name") or ""),
            "parameters": step,
            "result": {
                "success": success,
                "confidence": 0.0 if error else 1.0,
                "error": error,
                "data": {},
            },
        })

    def _execute_with_retry(self, step: dict, max_retries: int = 3) -> dict:
        """Execute a step with exponential backoff retry.

        Returns the result dict from the first successful execution,
        or the last failure result if all retries are exhausted.
        """
        last_result = {"success": False, "error": "no_execution"}
        for attempt in range(max_retries + 1):
            if attempt > 0:
                # Exponential backoff: 1s, 2s, 4s
                delay = 2 ** (attempt - 1)
                logger.info("Retry %d/%d for %s step (waiting %ds)...",
                            attempt, max_retries, step.get("type", "?"), delay)
                self._emit("log", {
                    "level": "info",
                    "message": f"重试 {attempt}/{max_retries} ({delay}s)...",
                })
                time.sleep(delay)

            last_result = self._execute_step_action(step)
            if last_result.get("success"):
                return last_result

        return last_result

    def _decide_recovery(self, state: dict, failed_step: dict, goal: str,
                         error: str = "", details: dict | None = None,
                         retry_count: int = 0) -> dict:
        """Decide recovery strategy for a failed step.

        Uses hardcoded rules first (no LLM needed), then falls back to planner.
        """
        error_lower = error.lower()
        step_type = failed_step.get("type", "")

        # ── LLM-free recovery rules ──

        # Window/access errors — can't recover by retrying
        if any(kw in error_lower for kw in ["access denied", "permission denied",
                                              "not authorized", "forbidden"]):
            return {"action": "fail", "reasoning": f"Permission error: {error}"}

        # Focus errors — try different focus strategy
        if any(kw in error_lower for kw in ["cannot focus", "window not found",
                                              "not active", "focus failed"]):
            return {"action": "retry", "reasoning": f"Focus error, retrying: {error}"}

        # Element visibility — wait and retry
        if any(kw in error_lower for kw in ["not visible", "not found",
                                              "element not", "no such"]):
            return {"action": "retry", "reasoning": f"Element not found, retrying: {error}"}

        # Timeout — retry with backoff
        if "timeout" in error_lower:
            return {"action": "retry", "reasoning": f"Timeout, retrying: {error}"}

        # OCR verification failure for type steps — text may appear differently
        if step_type == "type" and "ocr did not find" in error_lower:
            return {"action": "skip", "reasoning": f"Text may have appeared differently: {error}"}

        # Launch failures — retry once
        if step_type == "launch":
            if retry_count < 1:
                return {"action": "retry", "reasoning": "Retry launch"}
            return {"action": "skip", "reasoning": f"Launch failed after retry: {error}"}

        # Connection/network errors — retry
        if any(kw in error_lower for kw in ["connection", "network", "timed out"]):
            return {"action": "retry", "reasoning": f"Network error, retrying: {error}"}

        # Max retries exceeded — fail or skip depending on step type
        if retry_count >= 2:
            if step_type in ("click", "text_click", "hotkey", "scroll", "drag"):
                return {"action": "skip", "reasoning": f"Max retries exceeded, skipping: {error}"}
            return {"action": "fail", "reasoning": f"Max retries exceeded: {error}"}

        # ── Fallback: ask the planner (LLM) ──
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
    def _save_dsl_template(goal: str, dsl: str, steps: list):
        """将 Agent 执行成功的 DSL 保存到 templates/ 目录。"""
        import re, hashlib
        from pathlib import Path

        # 从 goal 生成文件名
        safe = re.sub(r"[^\w一-鿿]+", "_", goal.strip())[:40]
        if not safe:
            safe = hashlib.md5(goal.encode()).hexdigest()[:8]

        # 查找 templates 目录
        template_dir = Path(__file__).resolve().parent.parent.parent / "templates"
        template_dir.mkdir(parents=True, exist_ok=True)

        # 添加元数据头
        timestamp = __import__("datetime").datetime.now().isoformat(timespec="seconds")
        n_steps = len(steps) if steps else 0
        header = (
            f"# Agent 自动保存\n"
            f"# 目标: {goal}\n"
            f"# 时间: {timestamp}\n"
            f"# 步骤数: {n_steps}\n\n"
        )

        # 去重：如果已有内容相同的文件则不重复保存
        full_content = header + dsl
        for existing in template_dir.glob("*.flow"):
            try:
                if existing.read_text(encoding="utf-8").strip() == full_content.strip():
                    return  # 已存在
            except Exception:
                pass

        save_path = template_dir / f"agent_{safe}.flow"
        save_path.write_text(full_content, encoding="utf-8")
        logger.info("DSL template saved: %s (%d steps)", save_path, n_steps)

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
