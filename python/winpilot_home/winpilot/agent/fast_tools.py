"""Fast-controller tool handlers for ToolSession."""
from __future__ import annotations

import json

from ..fast.controller import FastGuiController
from .tool_result import ToolResult


class FastToolMixin:
    def _fast_controller(self) -> FastGuiController:
        controller = getattr(self, "_fast_gui_controller", None)
        if controller is None:
            controller = FastGuiController(self)
            self._fast_gui_controller = controller
        return controller

    def _t_run_action_batch(self, args: dict) -> ToolResult:
        actions = args.get("actions")
        if not isinstance(actions, list) or not actions:
            return ToolResult("run_action_batch requires a non-empty actions array")
        stop_on_error = bool(args.get("stop_on_error", True))
        result = self._fast_controller().execute(actions, stop_on_error=stop_on_error)
        return ToolResult(self._fmt(result))

    def _t_screen_watch(self, args: dict) -> ToolResult:
        action = str(args.get("action") or "status").casefold()
        watcher = self._fast_controller().watcher
        if action == "start":
            watcher.start()
            return ToolResult(json.dumps({"ok": True, "screen_watch": watcher.status()}, ensure_ascii=False))
        if action == "stop":
            watcher.stop()
            return ToolResult(json.dumps({"ok": True, "screen_watch": watcher.status()}, ensure_ascii=False))
        if action == "sample":
            delta = watcher.sample_once()
            return ToolResult(json.dumps({
                "ok": True,
                "delta": delta.to_dict(),
                "screen_watch": watcher.status(),
            }, ensure_ascii=False))
        if action == "status":
            return ToolResult(json.dumps({"ok": True, "screen_watch": watcher.status()}, ensure_ascii=False))
        return ToolResult(f"screen_watch unknown action: {action}")
