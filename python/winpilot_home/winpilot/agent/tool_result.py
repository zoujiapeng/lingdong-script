"""Shared return object for WinPilot agent tools."""
from __future__ import annotations


class ToolResult:
    """Dispatcher output: text for the LLM + metadata for the loop."""

    def __init__(
        self,
        text: str,
        is_observe: bool = False,
        terminal: str | None = None,
        summary: str = "",
        replay: dict | None = None,
        image_b64: str = "",
    ) -> None:
        self.text = text
        self.is_observe = is_observe
        self.terminal = terminal          # None | "done" | "fail"
        self.summary = summary            # one-line replacement when folded
        self.replay = replay              # selector info for offline replay export
        self.image_b64 = image_b64        # inline screenshot for vision-capable models
