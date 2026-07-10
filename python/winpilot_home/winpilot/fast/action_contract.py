"""Shared contract for actions accepted by the low-latency controller."""
from __future__ import annotations

from typing import Any


ALLOWED_BATCH_ACTIONS = {
    "observe",
    "click",
    "click_element",
    "type_into_element",
    "type_text",
    "hotkey",
    "scroll",
    "move_mouse",
    "drag",
    "draw_strokes",
    "select_color",
    "focus_window",
    "launch",
    "wait",
    "wait_change",
    "wait_stable",
    "read_text",
    "zoom",
    "screenshot",
    "clipboard_read",
    "clipboard_write",
    "clipboard_paste",
}

BATCH_MUTATING_ACTIONS = {
    "click",
    "click_element",
    "type_into_element",
    "type_text",
    "hotkey",
    "scroll",
    "move_mouse",
    "drag",
    "draw_strokes",
    "select_color",
    "focus_window",
    "launch",
    "clipboard_paste",
}

BATCH_OBSERVE_ACTIONS = {
    "observe",
    "read_text",
    "zoom",
    "screenshot",
    "clipboard_read",
    "auto_visual_verify",
}


def batch_action_name(action: dict[str, Any]) -> str:
    """Return the action name for explicit and shorthand batch formats."""
    name = str(action.get("action") or action.get("tool") or "").strip()
    if name:
        return name
    if len(action) == 1:
        return str(next(iter(action))).strip()
    return ""


def batch_action_args(action: dict[str, Any]) -> dict[str, Any]:
    if action.get("action") or action.get("tool"):
        return {key: value for key, value in action.items() if key not in {"action", "tool"}}
    if len(action) == 1:
        _name, value = next(iter(action.items()))
        return dict(value) if isinstance(value, dict) else {"value": value}
    return {}


def _missing_action_fields(name: str, args: dict[str, Any]) -> str:
    anchor_keys = {"element_id", "x", "label", "text_query", "fallback_text", "anchor"}
    if name == "click" and not (
        args.get("element_id") is not None
        or (args.get("x") is not None and args.get("y") is not None)
        or any(args.get(key) not in (None, "", []) for key in anchor_keys - {"element_id", "x"})
    ):
        return "requires coordinates, element_id, or a visual anchor"
    if name == "click_element" and not any(
        args.get(key) not in (None, "", []) for key in ("label", "text_query", "fallback_text", "anchor")
    ):
        return "requires label, text_query, fallback_text, or anchor"
    if name in {"type_text", "type_into_element", "clipboard_write"} and "text" not in args:
        return "requires text"
    if name == "hotkey" and not (args.get("keys") or args.get("value")):
        return "requires keys"
    if name in {"scroll", "move_mouse"} and not all(args.get(key) is not None for key in ("x", "y")):
        return "requires x and y"
    if name == "scroll" and args.get("amount") is None:
        return "requires amount"
    if name == "drag" and not all(args.get(key) is not None for key in ("x1", "y1", "x2", "y2")):
        return "requires x1, y1, x2, and y2"
    if name == "focus_window" and not str(args.get("title") or "").strip():
        return "requires title"
    if name == "launch" and not str(args.get("command") or "").strip():
        return "requires command"
    condition = args.get("condition")
    if name == "wait" and not (
        (isinstance(condition, dict) and any(value not in (None, "", []) for value in condition.values()))
        or any(isinstance(args.get(key), (int, float)) for key in ("duration_s", "seconds", "duration_ms", "ms", "value"))
    ):
        return "requires condition or a numeric duration"
    return ""


def validate_batch_actions(actions: Any) -> list[str]:
    """Validate action shape without importing the heavyweight controller."""
    if not isinstance(actions, list) or not actions:
        return ["config.actions must be a non-empty array"]
    errors: list[str] = []
    for index, action in enumerate(actions, start=1):
        if not isinstance(action, dict):
            errors.append(f"action {index} must be an object")
            continue
        name = batch_action_name(action)
        if not name:
            errors.append(f"action {index} has no action/tool name")
        elif name not in ALLOWED_BATCH_ACTIONS:
            errors.append(f"action {index} uses unsupported controller action {name!r}")
        else:
            missing = _missing_action_fields(name, batch_action_args(action))
            if missing:
                errors.append(f"action {index} ({name}) {missing}")
    return errors
