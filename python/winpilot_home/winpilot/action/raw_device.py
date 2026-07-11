"""Low-overhead controller adapter for already-authorized desktop actions.

The outer ToolSession owns policy, scope, trace and world-state updates. Inside
one controller batch this adapter calls the concrete tool handler directly so
child actions do not recursively pay the complete ToolSession dispatch cost.
"""
from __future__ import annotations

import time
from typing import Any

from ..agent.tool_result import ToolResult


RAW_DEVICE_ACTIONS = {
    "click",
    "type_text",
    "hotkey",
    "scroll",
    "move_mouse",
    "drag",
    "draw_strokes",
    "clipboard_read",
    "clipboard_write",
    "clipboard_paste",
    "focus_window",
    "launch",
}


class RawDeviceExecutor:
    def __init__(self, session) -> None:
        self.session = session

    def supports(self, action: str) -> bool:
        return action in RAW_DEVICE_ACTIONS and callable(
            getattr(self.session, f"_t_{action}", None)
        )

    def execute(self, action: str, args: dict[str, Any]) -> ToolResult:
        if not self.supports(action):
            raise ValueError(f"raw device action is not supported: {action}")
        started = time.monotonic()
        handler = getattr(self.session, f"_t_{action}")
        result = handler(dict(args))
        if not isinstance(result, ToolResult):
            result = ToolResult(str(result))
        result.replay = dict(result.replay or {})
        result.replay["raw_device"] = True
        result.replay.setdefault("elapsed_ms", int((time.monotonic() - started) * 1000))
        return result
