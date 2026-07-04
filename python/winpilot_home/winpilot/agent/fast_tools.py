"""Fast-controller tool handlers for ToolSession."""
from __future__ import annotations

import json
from typing import Any

from ..fast.controller import BATCH_MUTATING_ACTIONS, FastGuiController
from ..fast.subgoal import SubgoalExecutor
from .tool_result import ToolResult


def _has_execute_subgoal_routine(args: dict[str, Any]) -> bool:
    if not isinstance(args, dict):
        return False
    input_spec = args.get("input") or args.get("type") or args.get("text_input")
    click_spec = args.get("click") or args.get("button") or args.get("press")
    clicks = args.get("clicks") or args.get("buttons")
    draw_spec = args.get("draw") or args.get("drawing")
    draws = args.get("draws") or args.get("drawings")
    verify = args.get("verify_texts") or args.get("verify")
    if isinstance(input_spec, dict) and input_spec:
        return True
    if isinstance(click_spec, dict) and click_spec:
        return True
    if isinstance(clicks, list) and any(isinstance(item, dict) and item for item in clicks):
        return True
    if isinstance(draw_spec, dict) and draw_spec:
        return True
    if isinstance(draws, list) and any(isinstance(item, dict) and item for item in draws):
        return True
    if isinstance(verify, str) and verify.strip():
        return True
    if isinstance(verify, list) and any(str(item or "").strip() for item in verify):
        return True
    return bool(args.get("text") or args.get("button_label") or args.get("shape"))


def _needs_resolved_target_window(session: Any, args: dict[str, Any]) -> bool:
    target = args.get("target_window")
    if not isinstance(target, dict):
        return False
    if target.get("hwnd"):
        return False
    if not (str(target.get("process") or "").strip() or str(target.get("title") or "").strip()):
        return False
    try:
        if int(getattr(session, "hwnd", 0) or 0):
            return False
    except Exception:
        pass
    return True


def _blocked_unresolved_target_payload(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "verified": False,
        "blocked": True,
        "needs_target_window": True,
        "subgoal": str(args.get("subgoal") or args.get("goal") or ""),
        "subgoal_route": "controller_first",
        "controller_state": {
            "phase": "blocked",
            "needs_llm_reason": True,
            "needs_target_window": True,
        },
        "evidence_packet": {"matched_texts": [], "missing_texts": []},
        "error": (
            "execute_subgoal target_window has only a process/title hint. "
            "Resolve it to a real hwnd before running a controller routine."
        ),
        "needs_llm_reason": True,
        "fast_path_advice": (
            "Use launch/find_app/list_windows/scan_windows/focus_window to establish "
            "the target app/window first. Do not click the current foreground window "
            "as a substitute for an unresolved target_window."
        ),
    }


class FastToolMixin:
    def _fast_controller(self) -> FastGuiController:
        controller = getattr(self, "_fast_gui_controller", None)
        if controller is None:
            controller = FastGuiController(self)
            self._fast_gui_controller = controller
        return controller

    def _fast_screen_status(self) -> dict:
        controller = getattr(self, "_fast_gui_controller", None)
        if controller is None:
            return {}
        try:
            return controller.watcher.status()
        except Exception:
            return {}

    @staticmethod
    def _batch_has_mutation(actions: list) -> bool:
        for action in actions:
            if not isinstance(action, dict):
                continue
            name = str(action.get("action") or action.get("tool") or "")
            if not name and len(action) == 1:
                name = str(next(iter(action.keys())))
            if name in BATCH_MUTATING_ACTIONS:
                return True
        return False

    def _t_execute_subgoal(self, args: dict) -> ToolResult:
        if not isinstance(args, dict):
            args = {}
        if not _has_execute_subgoal_routine(args):
            payload = {
                "ok": False,
                "verified": False,
                "blocked": True,
                "needs_target_window": False,
                "subgoal": str(args.get("subgoal") or args.get("goal") or ""),
                "subgoal_route": "controller_first",
                "controller_state": {
                    "phase": "blocked",
                    "needs_llm_reason": True,
                    "needs_target_window": False,
                },
                "evidence_packet": {"matched_texts": [], "missing_texts": []},
                "error": (
                    "execute_subgoal requires an executable routine: input.text, "
                    "click.label/clicks, draw/draws, or verify_texts. The subgoal "
                    "string is only a label and cannot launch/discover an app."
                ),
                "needs_llm_reason": True,
                "fast_path_advice": (
                    "Do not use execute_subgoal with only a natural-language subgoal. "
                    "First use launch/find_app/focus_window/list_windows to establish "
                    "the target app/window, or provide input/click/draw/verify_texts "
                    "for a concrete controller routine."
                ),
            }
            return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))
        if _needs_resolved_target_window(self, args):
            return ToolResult(json.dumps(_blocked_unresolved_target_payload(args), ensure_ascii=False, default=str))
        return SubgoalExecutor(self, self._fast_controller()).execute(args)

    def _t_run_action_batch(self, args: dict) -> ToolResult:
        actions = args.get("actions")
        if not isinstance(actions, list) or not actions:
            return ToolResult("run_action_batch requires a non-empty actions array")
        stop_on_error = bool(args.get("stop_on_error", True))
        if "sync" in args:
            sync = args.get("sync") or None
        elif self._batch_has_mutation(actions):
            sync = {"mode": "stable", "stable_ms": 250, "timeout_s": 2.0}
        else:
            sync = None
        result = self._fast_controller().execute(
            actions,
            stop_on_error=stop_on_error,
            sync=sync,
            subgoal=str(args.get("subgoal") or ""),
        )
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
