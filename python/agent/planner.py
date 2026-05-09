"""
Planner — LLM-powered decision engine.

Calls the LLM (DeepSeek / compatible) to decide the next action based on
the goal, current state, history, and optional user guidance.
"""

import json
import logging
import os
import re
import time
from typing import Optional

from core.interfaces import LLMProvider as LLMProviderProtocol

logger = logging.getLogger("agent.planner")

SUPERVISOR_PROMPT = """You are supervising execution of a pre-generated desktop automation plan.
For each failure, you receive:
- goal: the original objective
- failed_step: the step that failed (type, target, raw text)
- error: what went wrong
- verification_details: optional details about verification failure (includes chain_results listing each verification method tried)

Available verification methods per step type:
- click/type/text_click: ocr_text (find text on screen), screen_change
- launch: process_running (check process exists), window_active (check window title), ocr_text, screen_change
- focus: window_active
- run_command: command_exitcode (check return code), file_exists
- file_write: file_contains (check file content), file_exists
- file_save: file_exists (check file saved on disk)
- hotkey/scroll/drag: screen_change, ocr_text

When suggesting an alternative_step, you may also suggest changing the verification strategy
by including a "verify" field on the alternative step, e.g.:
{"type": "launch", "target": "notepad.exe", "verify": [{"method": "process_running", "name": "notepad.exe"}]}

You must decide ONE recovery action:
1. "retry" — execute the same step again (for transient errors)
2. "alternative" — a different approach to achieve the same goal (provide alternative_step)
3. "skip" — skip this step, continue with the next one
4. "fail" — the goal is impossible, stop execution

Respond with ONLY JSON:
{
  "action": "retry|alternative|skip|fail",
  "reasoning": "brief explanation of your decision",
  "alternative_step": {"type": "...", ...}  // only for action="alternative"
}"""

SYSTEM_PROMPT = """You are an autonomous desktop agent using the ReAct (Reason + Act) pattern.

You receive:
- goal: the high-level objective
- state_summary: current execution state (step count, failures, blocked tools)
- history: recent steps taken + screen text feedback
- memories: relevant past knowledge
- tool_list: available tools with descriptions and parameters

You must respond with ONLY a JSON object (no markdown, no extra text):
{
  "reasoning": "step-by-step analysis of what to do next",
  "tool": "tool_name",
  "parameters": { ... }
}

Strategy rules (MUST follow):
1. Look at the history and state FIRST before deciding anything.
2. If a tool appears in failed_tools (blocked_actions), do NOT use it again.
3. If the last action failed, try a DIFFERENT approach immediately.
4. Think step-by-step before choosing a tool.
5. Call complete() as soon as the goal is achieved.
6. Call fail(reason) only if you are certain the goal is impossible.
7. IMPORTANT: On Chinese Windows, before typing English text, use hotkey(keys=["alt","shift"]) to switch IME first.
8. Do NOT repeat the same action if it already failed twice.
9. If you type text into a dialog, press Enter by sending a separate "press" action with key "enter".
10. For CLI tasks (running commands, scripts, git, npm, etc.), ALWAYS use run_command() — do NOT open cmd via GUI and type into it.
11. For file operations (read, write, list, copy), use the file_* tools directly — do NOT use GUI apps like Notepad.
12. For editing code or config files, use file_edit() with SEARCH/REPLACE blocks. It's safer than file_write() because it only changes the matched portion. Use file_write() only when you want to overwrite the entire file.
13. GUI automation (click, type_text, OCR) is only for desktop applications that have no CLI interface.
14. When a task can be done via CLI, prefer run_command() — it's faster, more reliable, and captures output automatically.
15. **generate_flow_script(goal)** is a key tool you should use PROACTIVELY. Call it when:
    - The goal describes a multi-step desktop automation task (opening apps, clicking, typing, etc.)
    - The task involves conditions, loops, or waiting for events
    - The user asks for a "script", "flowchart", "automation", "scheduled task", or "定时任务"
    - The task has 3+ steps that would benefit from visual flow representation
    - **When in doubt, prefer generate_flow_script over manual step-by-step execution** — it produces a reusable visual script
    After calling generate_flow_script successfully, call complete() — the result is handled automatically.
"""



class Planner:
    """LLM-powered planner with pluggable provider backend.

    Accepts either a provider instance or legacy api_key/api_base/model
    parameters for backward compatibility.
    """

    def __init__(self, api_key: str = "", api_base: str = "", model: str = "",
                 multimodal: bool | str = False,
                 provider: LLMProviderProtocol | None = None):
        if provider is not None:
            self._provider = provider
            self._multimodal = multimodal if isinstance(multimodal, bool) else False
        else:
            # Legacy initialization — create default DeepSeekProvider
            from providers.deepseek import DeepSeekProvider
            resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            resolved_base = (api_base or os.environ.get("LLM_API_BASE", "") or
                            "https://api.deepseek.com/v1").rstrip("/")
            resolved_model = model or os.environ.get("LLM_MODEL", "") or "deepseek-chat"
            self._provider = DeepSeekProvider(
                api_key=resolved_key, api_base=resolved_base, model=resolved_model,
            )
            # Read multimodal flag from param or env
            if isinstance(multimodal, bool):
                self._multimodal = multimodal
            else:
                env_val = multimodal or os.environ.get("LLM_MULTIMODAL", "")
                self._multimodal = env_val.lower() in ("true", "1", "yes")

    def decide_recovery(
        self,
        goal: str,
        failed_step: dict,
        error: str,
        verification_details: dict | None = None,
    ) -> dict:
        """Call LLM to decide recovery strategy for a failed execution step."""
        step_type = failed_step.get("type", "unknown")
        step_raw = failed_step.get("raw", str(failed_step))

        user_content = (
            f"GOAL: {goal}\n\n"
            f"FAILED STEP ({step_type}):\n  {step_raw}\n\n"
            f"ERROR: {error}\n"
        )
        if verification_details:
            user_content += f"\nVERIFICATION DETAILS:\n{json.dumps(verification_details, indent=2, default=str)}\n"

        messages = [
            {"role": "system", "content": SUPERVISOR_PROMPT},
            {"role": "user", "content": user_content},
        ]

        try:
            plan = self._call_api(messages)
            if isinstance(plan, dict) and plan.get("action") in ("retry", "alternative", "skip", "fail"):
                return plan
            logger.warning("Recovery returned invalid action: %s", plan)
            return {"action": "skip", "reasoning": f"Invalid recovery response: {plan}"}
        except Exception as e:
            logger.warning("Recovery API call failed: %s", e)
            return {"action": "skip", "reasoning": f"API unavailable: {e}"}

    def decide(
        self,
        goal: str,
        state_summary: str,
        history: list[dict],
        memories: list[str],
        tool_list: list[dict],
        user_guidance: str | None = None,
    ) -> dict:
        """Call the LLM and return a parsed action plan."""
        messages = self._build_messages(goal, state_summary, history, memories,
                                        tool_list, user_guidance)
        try:
            plan = self._call_api(messages)
            logger.debug("Planner response: %s", plan)
            return plan
        except Exception as e:
            logger.warning("Planner API call failed: %s; using fallback.", e)
            return self._fallback_plan(state_summary)

    def _build_messages(
        self,
        goal: str,
        state_summary: str,
        history: list[dict],
        memories: list[str],
        tool_list: list[dict],
        user_guidance: str | None = None,
    ) -> list[dict]:
        recent = history[-5:] if len(history) > 5 else history
        hist_text = "\n".join(
            f"  Step {h.get('step', '?')}: {h.get('tool')} → "
            f"{'✓' if h.get('success') else '✗'} | {h.get('note', '')}"
            for h in recent
        ) or "  (none)"

        mem_text = "\n".join(f"  - {m}" for m in memories) or "  (none)"

        tool_text = "\n".join(
            f"- {t.get('name')}({t.get('params_hint', '')}) — {t.get('description', '')}"
            for t in tool_list[:30]
        )

        guidance_block = f"\nUSER GUIDANCE:\n{user_guidance}\n" if user_guidance else ""

        user_content = (
            f"GOAL: {goal}\n\n"
            f"CURRENT STATE:\n  {state_summary}\n\n"
            f"RECENT HISTORY:\n{hist_text}\n\n"
            f"RELEVANT MEMORIES:\n{mem_text}\n\n"
            f"AVAILABLE TOOLS:\n{tool_text}\n"
            f"{guidance_block}"
            f"\nREMEMBER your GOAL: {goal}\n"
            "Decide the SINGLE next action."
        )

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    @property
    def provider(self) -> LLMProviderProtocol:
        """Access the underlying LLM provider."""
        return self._provider

    def _call_api(self, messages: list[dict]) -> dict:
        """Send messages to the LLM provider and parse the response as JSON."""
        try:
            response = self._provider.chat(
                messages,
                temperature=0.1,
                max_tokens=1000,
            )
            raw = response.get("content", "")
            if not raw:
                logger.warning("Provider returned empty content")
                return self._fallback_plan(str(messages[-1].get("content", ""))[:100])
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse LLM response as JSON: %s", e)
            return self._fallback_plan(str(messages[-1].get("content", ""))[:100])
        except Exception as e:
            logger.warning("Provider call failed: %s", e)
            return self._fallback_plan(str(messages[-1].get("content", ""))[:100])

    @staticmethod
    def _fallback_plan(state_summary: str) -> dict:
        return {
            "reasoning": f"API unavailable; defaulting to wait. State: {state_summary}",
            "tool": "wait_action",
            "parameters": {"duration": 2},
        }
