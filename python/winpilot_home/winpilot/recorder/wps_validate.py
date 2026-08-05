"""Structural validation for the ``.wps.json`` offline replay script format.

Pure stdlib module (plus ``replay_control``): deliberately free of the legacy
perception/verify stack so the vNext product line can validate REPLAY_STEP
graphs without pulling ``recorder.trace`` (and everything it imports) into
its closure.  Extracted from ``recorder.trace`` unchanged.
"""
from __future__ import annotations

from typing import Any

from .replay_control import count_leaves


# Tools the offline Replayer understands: leaf actions + control flow + ext.*.
LEAF_TOOLS = frozenset({
    "click", "type_text", "hotkey", "launch", "focus_window",
    "scroll", "drag", "wait", "run_shell", "tray_click",
    "draw_strokes", "run_detached",
})
CTRL_TOOLS = frozenset({"ctrl.if", "ctrl.loop"})
EXT_TOOLS = frozenset({
    "ext.delay", "ext.var_set", "ext.clipboard_set",
    "ext.clipboard_get", "ext.run_shell", "ext.screenshot",
})
KNOWN_TOOLS = LEAF_TOOLS | CTRL_TOOLS | EXT_TOOLS


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_point(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 2
        and _is_number(value[0])
        and _is_number(value[1])
    )


def _target_window_hint_from(value: Any, source: str) -> dict[str, Any] | None:
    """Normalize optional replay target-window metadata."""
    if isinstance(value, str):
        title = value.strip()
        return {"source": source, "title": title} if title else None
    if not isinstance(value, dict):
        return None

    hint: dict[str, Any] = {"source": source}
    title = (
        str(value.get("title") or value.get("window_title") or value.get("name") or "")
        .strip()
    )
    process = (
        str(value.get("process") or value.get("process_name") or value.get("app") or "")
        .strip()
    )
    hwnd = value.get("hwnd")
    try:
        hwnd_int = int(hwnd) if hwnd not in (None, "") else None
    except (TypeError, ValueError):
        hwnd_int = None

    if hwnd_int:
        hint["hwnd"] = hwnd_int
    if title:
        hint["title"] = title
    if process:
        hint["process"] = process
    return hint if len(hint) > 1 else None


def _target_window_hint(script: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("target_window", "target"):
        hint = _target_window_hint_from(script.get(key), key)
        if hint:
            return hint

    conditions = script.get("conditions")
    if isinstance(conditions, dict):
        for key in ("target_window", "window_title", "window_appears", "target"):
            hint = _target_window_hint_from(conditions.get(key), f"conditions.{key}")
            if hint:
                return hint
    return None


def _target_window_spec_present(script: dict[str, Any]) -> bool:
    if "target_window" in script:
        return True
    conditions = script.get("conditions")
    return isinstance(conditions, dict) and any(
        key in conditions for key in ("target_window", "window_title", "window_appears")
    )


def _has_click_target(step: dict) -> bool:
    return (
        _nonempty_str(step.get("text"))
        or _nonempty_str(step.get("element_text"))
        or _is_point(step.get("coords"))
    )


def _valid_strokes(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    return any(
        isinstance(stroke, list)
        and sum(1 for point in stroke if _is_point(point)) >= 2
        for stroke in value
    )


def _validate_leaf_step(step: dict, loc: str) -> str | None:
    tool = step.get("tool")
    if tool == "click" and not _has_click_target(step):
        return f"{loc} click requires text/element_text or coords"
    if tool == "type_text" and "text_input" not in step:
        return f"{loc} type_text requires text_input"
    if tool == "hotkey" and not isinstance(step.get("keys"), list):
        return f"{loc} hotkey requires keys[]"
    if tool == "launch" and not _nonempty_str(step.get("command")):
        return f"{loc} launch requires command"
    if tool == "focus_window" and not _nonempty_str(step.get("title")):
        return f"{loc} focus_window requires title"
    if tool == "scroll" and not _is_point(step.get("coords")):
        return f"{loc} scroll requires coords"
    if tool == "drag" and (not _is_point(step.get("from")) or not _is_point(step.get("to"))):
        return f"{loc} drag requires from/to points"
    if tool == "draw_strokes" and not _valid_strokes(step.get("strokes")):
        return f"{loc} draw_strokes requires at least one stroke with two points"
    if tool == "wait" and not isinstance(step.get("condition"), dict):
        return f"{loc} wait requires condition object"
    if tool == "run_shell" and not _nonempty_str(step.get("command")):
        return f"{loc} run_shell requires command"
    if tool == "run_detached" and not _nonempty_str(step.get("script")):
        return f"{loc} run_detached requires script"
    if tool == "tray_click" and not _nonempty_str(step.get("name")):
        return f"{loc} tray_click requires name"
    if tool == "ext.delay" and not _is_number(step.get("seconds", 0)):
        return f"{loc} ext.delay requires numeric seconds"
    if tool == "ext.var_set" and not _nonempty_str(step.get("name")):
        return f"{loc} ext.var_set requires name"
    if tool == "ext.clipboard_set" and "text" not in step:
        return f"{loc} ext.clipboard_set requires text"
    if tool == "ext.clipboard_get" and step.get("into") is not None and not _nonempty_str(step.get("into")):
        return f"{loc} ext.clipboard_get into must be a non-empty string"
    if tool == "ext.run_shell" and not _nonempty_str(step.get("command")):
        return f"{loc} ext.run_shell requires command"
    return None


def _validate_steps(steps: Any, where: str) -> str | None:
    if not isinstance(steps, list):
        return f"{where} 必须是数组"
    for i, step in enumerate(steps, start=1):
        loc = f"{where}[{i}]"
        if not isinstance(step, dict):
            return f"{loc} 必须是对象"
        tool = step.get("tool")
        if tool not in KNOWN_TOOLS:
            return f"{loc} 未知步骤类型: {tool!r}"
        if "target_window" in step and not _target_window_hint_from(
            step.get("target_window"), f"{loc}.target_window",
        ):
            return f"{loc}.target_window requires title/window_title/name, hwnd, or process"
        if tool == "ctrl.if":
            cond = step.get("cond")
            if not isinstance(cond, dict):
                return f"{loc} ctrl.if requires cond object"
            if not cond:
                return f"{loc} ctrl.if cond must be non-empty or {{'always': true}}"
            for branch in ("then", "else"):
                if step.get(branch) is not None:
                    err = _validate_steps(step[branch], f"{loc}.{branch}")
                    if err:
                        return err
        elif tool == "ctrl.loop":
            loop_type = step.get("loop_type", "count")
            if loop_type not in ("count", "while", "until", "infinite"):
                return f"{loc} invalid loop_type: {loop_type!r}"
            if loop_type == "count" and step.get("count") is not None and not _is_number(step.get("count")):
                return f"{loc} ctrl.loop count requires numeric count"
            if loop_type in ("while", "until"):
                cond = step.get("cond")
                if not isinstance(cond, dict):
                    return f"{loc} ctrl.loop {loop_type} requires cond object"
                if not cond:
                    return f"{loc} ctrl.loop {loop_type} cond must be non-empty"
            if step.get("max_iters") is not None and not _is_number(step.get("max_iters")):
                return f"{loc} ctrl.loop max_iters must be numeric"
            err = _validate_steps(step.get("body") or [], f"{loc}.body")
            if err:
                return err
        else:
            err = _validate_leaf_step(step, loc)
            if err:
                return err
    return None


def validate_script(data: Any) -> str | None:
    """Structural validation of a .wps.json document (any nesting depth).

    Returns an error message string, or None if valid. Used by the PUT
    endpoint to reject malformed/unknown-tool scripts before saving."""
    if not isinstance(data, dict):
        return "脚本必须是 JSON 对象"
    fmt = data.get("format")
    if fmt not in ("wps/1", "wps/2"):
        return f"未知 format: {fmt!r}（应为 wps/1 或 wps/2）"
    if data.get("vars") is not None and not isinstance(data.get("vars"), dict):
        return "vars 必须是对象"
    if _target_window_spec_present(data) and not _target_window_hint(data):
        return "target_window requires title/window_title/name, hwnd, or process"
    steps = data.get("steps")
    error = _validate_steps(steps, "steps")
    if error:
        return error
    if count_leaves(steps) <= 0:
        return "steps must contain at least one executable leaf"
    return None
