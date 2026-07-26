"""Legacy ToolSession reflection retained only for FastGuiController migration."""
from __future__ import annotations

import time
from typing import Any

from ..agent.tool_result import ToolResult


LEGACY_SESSION_ACTIONS = frozenset({
    "click", "type_text", "hotkey", "scroll", "move_mouse", "drag",
    "draw_strokes", "clipboard_read", "clipboard_write", "clipboard_paste",
    "focus_window", "launch",
})


class LegacyToolSessionActionAdapter:
    """Reflect already-admitted controller children into legacy ToolSession."""

    def __init__(self, session) -> None:
        self.session = session

    def supports(self, action: str) -> bool:
        return action in LEGACY_SESSION_ACTIONS and callable(
            getattr(self.session, f"_t_{action}", None)
        )

    def execute(self, action: str, args: dict[str, Any]) -> ToolResult:
        if not self.supports(action):
            raise ValueError(f"raw device action is not supported: {action}")
        started = time.monotonic()
        result = getattr(self.session, f"_t_{action}")(dict(args))
        if not isinstance(result, ToolResult):
            result = ToolResult(str(result))
        result.replay = dict(result.replay or {})
        result.replay["raw_device"] = True
        result.replay.setdefault(
            "elapsed_ms", int((time.monotonic() - started) * 1000),
        )
        return result


__all__ = ["LEGACY_SESSION_ACTIONS", "LegacyToolSessionActionAdapter"]
