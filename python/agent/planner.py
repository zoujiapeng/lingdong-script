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

logger = logging.getLogger("agent.planner")

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
"""


class Planner:
    """LLM-powered planner."""

    def __init__(self, api_key: str = "", api_base: str = "", model: str = ""):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._api_base = (api_base or os.environ.get("LLM_API_BASE", "") or
                          "https://api.deepseek.com/v1").rstrip("/")
        self._model = model or os.environ.get("LLM_MODEL", "") or "deepseek-chat"

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

    def _call_api(self, messages: list[dict]) -> dict:
        import requests as _req

        for attempt in range(3):
            try:
                resp = _req.post(
                    f"{self._api_base}/chat/completions",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self._api_key}",
                    },
                    json={
                        "model": self._model,
                        "messages": messages,
                        "max_tokens": 1000,
                        "temperature": 0.1,
                    },
                    timeout=90,
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                return json.loads(raw)
            except Exception as e:
                logger.warning("API attempt %d failed: %s", attempt + 1, e)
                if attempt < 2:
                    time.sleep(1.5 ** attempt)
        return self._fallback_plan(str(messages[-1].get("content", ""))[:100])

    @staticmethod
    def _fallback_plan(state_summary: str) -> dict:
        return {
            "reasoning": f"API unavailable; defaulting to wait. State: {state_summary}",
            "tool": "wait_action",
            "parameters": {"duration": 2},
        }
