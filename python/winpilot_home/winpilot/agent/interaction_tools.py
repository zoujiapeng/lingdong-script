"""Input and visible-action tool handlers for ToolSession."""
from __future__ import annotations

import json
import hashlib
import inspect
import math
import sys
import time
from typing import Any

import win32gui

from ..action import executor, verify
from ..config import CONFIG
from ..perception import vision_cv
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, capture, window_region
from .task_rules import _is_desktop_icon_drag_task
from .tool_result import ToolResult


_CANCEL_TEXTS = {"cancel", "\u53d6\u6d88"}
_INPUT_ROLES = {"edit", "combobox", "\u7f16\u8f91", "\u7ec4\u5408\u6846"}
_DANGEROUS_POWER_TERMS = (
    "shutdown", "shut down", "restart", "sign out", "log off", "sleep", "hibernate",
    "\u5173\u673a", "\u91cd\u542f", "\u91cd\u65b0\u542f\u52a8", "\u6ce8\u9500",
    "\u7761\u7720", "\u4f11\u7720", "\u7535\u6e90",
)
_POWER_TASK_ALLOW_TERMS = (
    "shutdown", "shut down", "restart", "sign out", "log off", "sleep", "hibernate",
    "power setting", "power settings", "battery",
    "\u5173\u673a", "\u91cd\u542f", "\u91cd\u65b0\u542f\u52a8", "\u6ce8\u9500",
    "\u7761\u7720", "\u4f11\u7720", "\u7535\u6e90", "\u7535\u6c60",
)


def _call_with_optional_hwnd(fn, *args, hwnd: int | None = None):
    """Keep test/adaptor compatibility while passing scope to new executors."""
    try:
        parameters = inspect.signature(fn).parameters.values()
        accepts_hwnd = any(
            parameter.name == "hwnd"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    except (TypeError, ValueError):
        accepts_hwnd = True
    if accepts_hwnd:
        return fn(*args, hwnd=hwnd)
    return fn(*args)


def _clipboard_sequence_number() -> int | None:
    """Return the Windows clipboard generation without reading its payload."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        return int(ctypes.windll.user32.GetClipboardSequenceNumber())
    except (AttributeError, OSError, ValueError):
        return None


def _looks_like_save_task(task: str) -> bool:
    text = (task or "").casefold()
    return any(marker in text for marker in (
        "save", "save as", "export",
        "\u4fdd\u5b58", "\u53e6\u5b58", "\u53e6\u5b58\u4e3a", "\u5bfc\u51fa",
    ))


def _looks_like_cancel_element(text: str, role: str) -> bool:
    label = (text or "").strip().casefold()
    role_text = (role or "").casefold()
    return label in _CANCEL_TEXTS and ("button" in role_text or "\u6309\u94ae" in role_text or not role_text)


def _looks_like_input_element(role: str) -> bool:
    role_text = (role or "").strip().casefold()
    return role_text in _INPUT_ROLES or "edit" in role_text or "combo" in role_text


def _prefer_physical_text_input(element: Any, text: str, task: str) -> bool:
    """Use real keyboard input where UIA SetValue is not a reliable commit.

    Composite controls frequently expose a writable wrapper even though changing
    that wrapper does not update the focused child editor.  Native and custom
    file dialogs are a common example.  Treat this as a control-shape rule, not
    an application rule: composite inputs and explicit file paths in save/open
    workflows must go through the focused keyboard surface.
    """
    role = str(getattr(element, "role", "") or "").strip().casefold()
    if "combo" in role or "\u7ec4\u5408\u6846" in role:
        return True
    value = str(text or "").strip().strip('"')
    absolute_path = (
        len(value) >= 3
        and value[0].isalpha()
        and value[1] == ":"
        and value[2] in {"\\", "/"}
    )
    return bool(absolute_path and _looks_like_save_task(task))


def _task_allows_power_action(task: str) -> bool:
    text = (task or "").casefold()
    return any(term in text for term in _POWER_TASK_ALLOW_TERMS)


def _looks_like_dangerous_power_element(text: str, role: str) -> bool:
    label = (text or "").strip().casefold()
    if not label:
        return False
    role_text = (role or "").casefold()
    clickable = (
        "button" in role_text
        or "\u6309\u94ae" in role_text
        or "menu" in role_text
        or "\u83dc\u5355" in role_text
        or not role_text
    )
    return clickable and any(term in label for term in _DANGEROUS_POWER_TERMS)


def _normalize_draw_strokes(raw: object) -> list[list[list[int]]]:
    """Normalize common model stroke encodings to ``[[[x, y], ...], ...]``.

    Tool schemas describe canonical polylines, but text models frequently emit
    either one unwrapped polyline (``[[x, y], ...]``) or line segments encoded
    as ``[x1, y1, x2, y2]``.  Both are unambiguous and safe to normalize in the
    controller instead of wasting LLM retries on array nesting.
    """
    if not isinstance(raw, list) or not raw:
        return []

    def number(value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def point(value: object) -> bool:
        return (
            isinstance(value, (list, tuple))
            and len(value) >= 2
            and number(value[0])
            and number(value[1])
        )

    # A model often omits the outer list for a single polyline.
    candidates: list[object]
    if all(point(item) and len(item) == 2 for item in raw):
        candidates = [raw]
    else:
        candidates = list(raw)

    normalized: list[list[list[int]]] = []
    for stroke in candidates:
        if not isinstance(stroke, (list, tuple)) or not stroke:
            continue
        if all(number(value) for value in stroke):
            values = [int(value) for value in stroke]
            if len(values) < 4 or len(values) % 2:
                continue
            pts = [[values[index], values[index + 1]] for index in range(0, len(values), 2)]
        else:
            pts = [
                [int(value[0]), int(value[1])]
                for value in stroke
                if point(value)
            ]
        if len(pts) >= 2:
            normalized.append(pts)
    return normalized


def _validate_normalized_draw_strokes(
    raw: object,
) -> tuple[list[list[list[float]]], str]:
    """Validate canonical 0..1000 work-surface paths without partial repair."""
    if not isinstance(raw, list) or not raw:
        return [], "normalized_strokes must contain at least one stroke"
    normalized: list[list[list[float]]] = []
    for stroke_index, stroke in enumerate(raw):
        if not isinstance(stroke, (list, tuple)) or len(stroke) < 2:
            return [], (
                f"normalized_strokes[{stroke_index}] must contain at least two [x,y] points"
            )
        points: list[list[float]] = []
        for point_index, point in enumerate(stroke):
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                return [], (
                    f"normalized_strokes[{stroke_index}][{point_index}] must be exactly [x,y]"
                )
            if any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in point
            ):
                return [], (
                    f"normalized_strokes[{stroke_index}][{point_index}] coordinates must be numeric"
                )
            x, y = float(point[0]), float(point[1])
            if not math.isfinite(x) or not math.isfinite(y):
                return [], (
                    f"normalized_strokes[{stroke_index}][{point_index}] coordinates must be finite"
                )
            if not (0.0 <= x <= 1000.0 and 0.0 <= y <= 1000.0):
                return [], (
                    f"normalized_strokes[{stroke_index}][{point_index}] is outside 0..1000; "
                    "the whole action was rejected instead of clamping"
                )
            points.append([x, y])
        normalized.append(points)
    return normalized, ""


def _tools_attr(name: str, default):
    """Preserve historical monkeypatch paths on winpilot.agent.tools."""
    tools_mod = sys.modules.get("winpilot.agent.tools")
    return getattr(tools_mod, name, default) if tools_mod is not None else default


def _expect_timeout(args: dict, default: float = 6.0) -> float:
    expect = args.get("expect")
    if isinstance(expect, dict):
        try:
            return float(expect.get("timeout_s", default))
        except (TypeError, ValueError):
            return default
    return default


def _printable_text_sequence(keys: list[str]) -> str:
    if len(keys) <= 1:
        return ""
    aliases = {"space": " ", "plus": "+", "equals": "=", "equal": "="}
    chars: list[str] = []
    for key in keys:
        value = str(key)
        low = value.casefold()
        if low in aliases:
            chars.append(aliases[low])
        elif len(value) == 1 and value.isprintable():
            chars.append(value)
        else:
            return ""
    return "".join(chars)


def normalize_key_sequence(raw: Any) -> list[str]:
    """Normalize model-produced hotkey args without treating JSON as text.

    Some providers occasionally return an array-looking string for a JSON
    schema array, and in rare cases append an extra brace. Shortcut tools are
    too risky to interpret such strings as printable text.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        parsed: Any = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            if "[" in text and "]" in text:
                bracketed = text[text.find("["):text.rfind("]") + 1]
                try:
                    parsed = json.loads(bracketed)
                except json.JSONDecodeError:
                    parsed = None
        if isinstance(parsed, (list, tuple)):
            return [str(key) for key in parsed]
        if isinstance(parsed, str):
            text = parsed.strip()
        if "+" in text:
            parts = [part.strip() for part in text.split("+")]
            return [part for part in parts if part]
        if "," in text:
            parts = [part.strip(" \t\r\n'\"") for part in text.split(",")]
            return [part for part in parts if part]
        return [text] if text else []
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for item in raw:
            if isinstance(item, str) and item.strip().startswith("["):
                parsed = normalize_key_sequence(item)
                if len(parsed) > 1:
                    out.extend(parsed)
                    continue
            out.append(str(item))
        return out
    return [str(raw)]


def canonical_hotkey_order(keys: list[str]) -> list[str]:
    """Place chord modifiers first while preserving all relative ordering."""
    aliases = {
        "control": "ctrl",
        "ctl": "ctrl",
        "option": "alt",
        "command": "meta",
        "cmd": "meta",
        "super": "meta",
    }
    normalized = [aliases.get(str(key).casefold(), str(key).casefold()) for key in keys]
    modifiers = {"ctrl", "alt", "shift", "meta", "control_l", "control_r", "alt_l", "alt_r", "shift_l", "shift_r"}
    chord_modifiers = [key for key in normalized if key in modifiers]
    ordinary = [key for key in normalized if key not in modifiers]
    return [*chord_modifiers, *ordinary] if chord_modifiers else normalized


class InteractionToolMixin:
    def _element_at_point(self, x: int, y: int):
        snapshot = getattr(self, "snapshot", None)
        elements = getattr(snapshot, "elements", None) or []
        hits = []
        for element in elements:
            try:
                left = int(getattr(element, "left", 0))
                top = int(getattr(element, "top", 0))
                width = int(getattr(element, "width", 0))
                height = int(getattr(element, "height", 0))
            except (TypeError, ValueError):
                continue
            if width <= 0 or height <= 0:
                continue
            if left <= x <= left + width and top <= y <= top + height:
                hits.append((width * height, element))
        if not hits:
            return None
        hits.sort(key=lambda item: item[0])
        return hits[0][1]

    def _blocked_power_click_result(self, element) -> ToolResult | None:
        if _task_allows_power_action(getattr(self, "task", "")):
            return None
        text = str(getattr(element, "text", "") or "")
        role = str(getattr(element, "role", "") or "")
        if not _looks_like_dangerous_power_element(text, role):
            return None
        result = {
            "ok": False,
            "verified": False,
            "blocked": True,
            "error": (
                "Refused to click a system power/session control outside an explicit "
                "power-management task. Re-observe the intended app/window and choose a "
                "non-destructive target."
            ),
            "blocked_element": {
                "text": text,
                "role": role,
                "source": str(getattr(element, "source", "") or ""),
                "coords": [int(getattr(element, "cx", 0) or 0), int(getattr(element, "cy", 0) or 0)],
            },
        }
        return ToolResult(self._fmt(result))

    def _action_hwnd(self) -> int | None:
        # Action dispatch must not resolve/adopt a new foreground window.  The
        # remembered target is established by launch/focus/observe workflows;
        # mutating actions merely consume that binding.
        try:
            if self._window_alive(self.hwnd):
                return int(self.hwnd)
        except Exception:
            pass
        if CONFIG.get("controller", "multi_input_cooperation", default=False):
            return None
        try:
            fg = win32gui.GetForegroundWindow()
            if fg and win32gui.IsWindowVisible(fg):
                return int(fg)
        except Exception:
            pass
        return self.hwnd

    def _verification_watcher(self):
        try:
            return self._fast_controller().watcher
        except Exception:
            return None

    def _t_click(self, args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        verify_mod = _tools_attr("verify", verify)
        element_id = args.get("element_id")
        button = args.get("button", "left")
        double = bool(args.get("double", False))
        physical = bool(args.get("physical") or args.get("force_physical"))
        prepositioned = bool(args.get("_prepositioned"))
        replay: dict[str, Any] = {
            "tool": "click", "button": button, "double": double,
            "expect": args.get("expect"),
        }

        if element_id is not None:
            element, err = self._resolve_element(int(element_id))
            if element is None:
                return ToolResult(err)
            x, y = element.cx, element.cy
            replay.update({
                "text": element.text, "role": element.role,
                "coords": [x, y], "source": element.source,
            })
            if _looks_like_input_element(element.role):
                self._last_input_element = element
            if (
                button == "left"
                and not double
                and _looks_like_save_task(getattr(self, "task", ""))
                and _looks_like_cancel_element(element.text, element.role)
            ):
                result = {
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "error": (
                        "Refused to click a Cancel/取消 button during a save/export task. "
                        "Re-observe the active dialog, query for Save/保存, and click the "
                        "actual confirm button or press Enter only after the filename field is correct."
                    ),
                    "post_state": verify_mod.post_state_summary(),
                }
                return ToolResult(self._fmt(result), replay=replay)
            blocked = self._blocked_power_click_result(element)
            if blocked is not None:
                return blocked

            def action() -> dict:
                if button == "left" and not double and not physical and executor_mod.uia_invoke(element):
                    BUS.publish("action", action="uia_invoke", elem_id=element.elem_id,
                                text=element.text)
                    return {"ok": True, "via": "uia_invoke"}
                click_kwargs = {"prepositioned": True} if prepositioned else {}
                return executor_mod.click(
                    x, y, button=button, double=double,
                    hwnd=self._action_hwnd(), **click_kwargs,
                )
        elif "x" in args and "y" in args:
            x, y = int(args["x"]), int(args["y"])
            replay["coords"] = [x, y]
            hit = self._element_at_point(x, y)
            if hit is not None:
                blocked = self._blocked_power_click_result(hit)
                if blocked is not None:
                    return blocked

            def action() -> dict:
                click_kwargs = {"prepositioned": True} if prepositioned else {}
                return executor_mod.click(
                    x, y, button=button, double=double,
                    hwnd=self._action_hwnd(), **click_kwargs,
                )
        else:
            return ToolResult("click 需要 element_id 或 x,y 坐标")

        result = verify_mod.run_with_verify(
            "click", action, expect=args.get("expect"), hwnd=self.hwnd,
            retryable=bool(args.get("retryable", False)),
            timeout_s=_expect_timeout(args), stop_event=self.stop_event,
            watcher=self._verification_watcher(),
            run_id=getattr(self, "run_id", None))
        if not args.get("expect"):
            result.setdefault("verified", False)
            result.setdefault("verify_detail", "未提供 expect；ok 只表示点击已执行，必须 observe 确认界面变化")
        return ToolResult(self._fmt(result), replay=replay)

    def _t_type_text(self, args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        verify_mod = _tools_attr("verify", verify)
        win32gui_mod = _tools_attr("win32gui", win32gui)
        text = str(args["text"])
        keyboard_mode = str(args.get("keyboard_mode") or "").strip().casefold()
        numeric_keypad = keyboard_mode in {"numeric", "numeric_keypad", "calculation"}
        element_id = args.get("element_id")
        replay: dict[str, Any] = {
            "tool": "type_text", "text_input": text, "expect": args.get("expect"),
            "keyboard_mode": keyboard_mode,
        }
        if element_id is not None:
            element, err = self._resolve_element(int(element_id))
            if element is None:
                return ToolResult(err)
            replay.update({
                "element_text": element.text, "coords": [element.cx, element.cy],
            })
        else:
            element = getattr(self, "_last_input_element", None)
            if element is not None:
                replay.update({
                    "element_text": getattr(element, "text", ""),
                    "coords": [getattr(element, "cx", 0), getattr(element, "cy", 0)],
                    "source": "last_clicked_input",
                })

        def action() -> dict:
            target = self._action_hwnd()
            if element is not None:
                prefer_physical = _prefer_physical_text_input(
                    element,
                    text,
                    str(getattr(self, "task", "") or ""),
                )
                if not prefer_physical and executor_mod.uia_set_value(element, text):
                    return {"ok": True, "via": "uia_set_value"}
            cooperative_guard = getattr(
                executor_mod, "cooperative_input_guard", lambda *_args, **_kwargs: None,
            )
            cooperative_block = cooperative_guard("type_text", target)
            if cooperative_block is not None:
                return cooperative_block
            if element is not None:
                click_result = executor_mod.click(
                    element.cx, element.cy, hwnd=self._action_hwnd(),
                )
                if click_result.get("ok") is not True:
                    return click_result
                time.sleep(0.15)
            if target and win32gui_mod.GetForegroundWindow() != target:
                cooperative_block = cooperative_guard("type_text", target)
                if cooperative_block is not None:
                    return cooperative_block
                executor_mod.focus_window(target)
                time.sleep(0.12)
            if numeric_keypad:
                return executor_mod.type_text(text, hwnd=target, numeric_keypad=True)
            return executor_mod.type_text(text, hwnd=target)

        result = verify_mod.run_with_verify(
            "type_text", action, expect=args.get("expect"),
            hwnd=self.hwnd, retryable=False, timeout_s=_expect_timeout(args),
            stop_event=self.stop_event, watcher=self._verification_watcher(),
            run_id=getattr(self, "run_id", None))
        if not args.get("expect"):
            result.setdefault("verified", False)
            result.setdefault("verify_detail", "未提供 expect；ok 只表示输入动作已执行，必须 observe/read_text 确认文本位置")
        return ToolResult(self._fmt(result), replay=replay)

    def _t_hotkey(self, args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        verify_mod = _tools_attr("verify", verify)
        win32gui_mod = _tools_attr("win32gui", win32gui)
        keys = canonical_hotkey_order(normalize_key_sequence(args.get("keys")))
        if not keys:
            return ToolResult("hotkey requires keys[]")
        printable_text = _printable_text_sequence(keys)
        if printable_text:
            result = self._t_type_text({
                "text": printable_text,
                "expect": args.get("expect"),
            })
            try:
                data = json.loads(result.text or "{}")
                if isinstance(data, dict):
                    data["coerced_from_hotkey"] = keys
                    result.text = self._fmt(data)
            except (json.JSONDecodeError, TypeError):
                pass
            return result

        key_set = {str(key).strip().casefold() for key in keys}
        verifies_text_copy = (
            "c" in key_set
            and bool(key_set.intersection({"ctrl", "control", "ctl"}))
        )
        clipboard_sequence_before = (
            _clipboard_sequence_number() if verifies_text_copy else None
        )

        def action() -> dict:
            target = self._action_hwnd()
            cooperative_guard = getattr(
                executor_mod, "cooperative_input_guard", lambda *_args, **_kwargs: None,
            )
            cooperative_block = cooperative_guard("hotkey", target)
            if cooperative_block is not None:
                return cooperative_block
            if target and win32gui_mod.GetForegroundWindow() != target:
                cooperative_block = cooperative_guard("hotkey", target)
                if cooperative_block is not None:
                    return cooperative_block
                executor_mod.focus_window(target)
                time.sleep(0.12)
            result = _call_with_optional_hwnd(
                executor_mod.press_keys, keys, hwnd=target,
            )
            if not verifies_text_copy:
                return result
            time.sleep(0.08)
            sequence_after = _clipboard_sequence_number()
            clipboard_text = executor_mod.clipboard_read()
            sequence_changed = (
                clipboard_sequence_before is not None
                and sequence_after is not None
                and sequence_after != clipboard_sequence_before
            )
            copied_text = sequence_changed and bool(clipboard_text)
            result.update({
                "clipboard_sequence_changed": sequence_changed,
                "clipboard_text_length": len(clipboard_text),
                "clipboard_text_fingerprint": (
                    hashlib.sha256(clipboard_text.encode("utf-8")).hexdigest()
                    if clipboard_text
                    else ""
                ),
                "verified": copied_text,
                "verify_method": "clipboard_sequence_and_nonempty_text",
                "verify_detail": (
                    "Ctrl+C changed the Windows clipboard generation and produced non-empty text."
                    if copied_text
                    else "Ctrl+C was sent, but no new non-empty clipboard text was observed."
                ),
            })
            return result

        result = verify_mod.run_with_verify(
            "hotkey", action,
            expect=args.get("expect"), hwnd=self.hwnd, retryable=False,
            timeout_s=_expect_timeout(args), stop_event=self.stop_event,
            watcher=self._verification_watcher(),
            run_id=getattr(self, "run_id", None))
        if not args.get("expect"):
            result.setdefault("verified", False)
            result.setdefault("verify_detail", "未提供 expect；ok 只表示快捷键已发送，必须 observe/read_text 确认结果")
        return ToolResult(
            self._fmt(result),
            replay={"tool": "hotkey", "keys": keys, "expect": args.get("expect")},
        )

    def _t_scroll(self, args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        x, y, amount = int(args["x"]), int(args["y"]), int(args["amount"])
        result = _call_with_optional_hwnd(
            executor_mod.scroll,
            x,
            y,
            amount,
            hwnd=self._action_hwnd(),
        )
        return ToolResult(
            self._fmt(result),
            replay={"tool": "scroll", "coords": [x, y], "amount": amount},
        )

    def _t_drag(self, args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        capture_fn = _tools_attr("capture", capture)
        x1, y1 = int(args["x1"]), int(args["y1"])
        x2, y2 = int(args["x2"]), int(args["y2"])
        adjusted_drop = self._desktop_icon_safe_drop_target(x1, y1, x2, y2)
        if adjusted_drop and not adjusted_drop.get("adjusted", True):
            return ToolResult(self._fmt({
                "ok": False,
                "blocked": True,
                "stage": "drop_target",
                "error": adjusted_drop.get("reason", "unsafe desktop icon drop target"),
                "adjusted_drop_target": adjusted_drop,
            }))
        if adjusted_drop:
            x2, y2 = adjusted_drop["x"], adjusted_drop["y"]
        points = args.get("points") if isinstance(args.get("points"), list) else None
        duration = float(args.get("duration", 0.4))
        region = self._drag_feedback_region(x1, y1, x2, y2, points)
        before = None
        try:
            before = capture_fn(region)
        except Exception as exc:
            logger.debug("drag pre-capture failed: %s", exc)
        result = executor_mod.drag(x1, y1, x2, y2, duration_s=duration,
                                   points=points, hwnd=self.hwnd)
        if adjusted_drop:
            result["adjusted_drop_target"] = adjusted_drop
        if before is not None:
            try:
                time.sleep(0.12)
                after = capture_fn(region)
                ratio = vision_cv.diff_ratio(before, after)
                result["visual_change_ratio"] = round(float(ratio), 5)
                changed = bool(ratio >= 0.0008)
                result["visual_change_detected"] = changed
                if changed:
                    result["verified"] = True
                    result["verify_method"] = "cv-diff"
                    result["verify_detail"] = (
                        "visible pixels changed across the requested action region"
                    )
                if ratio < 0.0008:
                    result["visual_feedback"] = (
                        "drag completed but the local screen region barely changed; "
                        "re-observe and re-ground the source, path, and target before "
                        "retrying; do not repeat identical pointer geometry without "
                        "new visual evidence."
                    )
                else:
                    result["visual_feedback"] = (
                        "local screen pixels changed after drag; this is useful evidence "
                        "that the action affected the visible surface."
                    )
            except Exception as exc:
                logger.debug("drag post-capture/diff failed: %s", exc)
        return ToolResult(
            self._fmt(result),
            replay={"tool": "drag", "from": [x1, y1], "to": [x2, y2],
                    "points": points, "duration": duration},
        )

    @staticmethod
    def _drag_feedback_region(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        points: list | None,
    ) -> Region:
        coords: list[tuple[int, int]] = [(x1, y1), (x2, y2)]
        for point in points or []:
            try:
                coords.append((int(point[0]), int(point[1])))
            except (TypeError, ValueError, IndexError, KeyError):
                continue
        xs = [x for x, _ in coords]
        ys = [y for _, y in coords]
        pad = 28
        left = min(xs) - pad
        top = min(ys) - pad
        right = max(xs) + pad
        bottom = max(ys) + pad
        return Region(left, top, max(1, right - left), max(1, bottom - top)).clamp_to_screen()

    def _desktop_icon_safe_drop_target(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
    ) -> dict[str, Any] | None:
        if not _is_desktop_icon_drag_task(getattr(self, "task", "")):
            return None
        snapshot = getattr(self, "snapshot", None)
        if snapshot is None:
            return None
        title = str(getattr(snapshot, "title", "") or "")
        elements = list(getattr(snapshot, "elements", []) or [])
        is_desktop = title == "Program Manager" or any(
            "list" in str(getattr(elem, "role", "") or "").casefold()
            and str(getattr(elem, "text", "") or "") in {"桌面", "Desktop"}
            for elem in elements
        )
        if not is_desktop:
            return None
        icons = [
            elem for elem in elements
            if "listitem" in str(getattr(elem, "role", "") or "").casefold()
            and int(getattr(elem, "width", 0) or 0) >= 32
            and int(getattr(elem, "height", 0) or 0) >= 32
        ]
        if not icons:
            return None

        def hits_icon(px: int, py: int, pad: int = 12) -> bool:
            for elem in icons:
                left = int(getattr(elem, "left", 0) or 0) - pad
                top = int(getattr(elem, "top", 0) or 0) - pad
                right = left + int(getattr(elem, "width", 0) or 0) + pad * 2
                bottom = top + int(getattr(elem, "height", 0) or 0) + pad * 2
                if left <= px <= right and top <= py <= bottom:
                    return True
            return False

        if not hits_icon(x2, y2):
            return None

        region = getattr(snapshot, "region", None)
        left = int(getattr(region, "left", 0) or 0)
        top = int(getattr(region, "top", 0) or 0)
        width = int(getattr(region, "width", 1920) or 1920)
        height = int(getattr(region, "height", 1080) or 1080)
        right = left + width
        bottom = top + height
        task_text = str(getattr(self, "task", "") or "")
        wants_top_right = (
            "top-right" in task_text.casefold()
            or "top right" in task_text.casefold()
            or "右上" in task_text
        )
        x_start = min(right - 36, max(left + 36, x2))
        y_start = min(bottom - 96, max(top + 36, y2))
        candidates: list[tuple[int, int]] = []
        if wants_top_right:
            for y in range(top + 40, min(bottom - 96, top + int(height * 0.42)), 42):
                for x in range(right - 40, max(left + int(width * 0.62), left + 40), -42):
                    candidates.append((x, y))
        for dy in (0, 42, -42, 84, -84, 126, 168, 210):
            for dx in (0, -42, 42, -84, 84, -126, 126, -168, 168, -210, 210):
                candidates.append((x_start + dx, y_start + dy))

        seen: set[tuple[int, int]] = set()
        safe: list[tuple[int, int]] = []
        for px, py in candidates:
            px = min(right - 24, max(left + 24, int(px)))
            py = min(bottom - 96, max(top + 24, int(py)))
            key = (px, py)
            if key in seen:
                continue
            seen.add(key)
            if hits_icon(px, py):
                continue
            if abs(px - x1) < 48 and abs(py - y1) < 48:
                continue
            safe.append(key)
        if not safe:
            return {
                "x": x2,
                "y": y2,
                "adjusted": False,
                "reason": "drop target overlaps another desktop icon and no nearby empty point was found",
            }
        safe.sort(key=lambda p: (abs(p[0] - x2) + abs(p[1] - y2), abs(p[0] - (right - 40)), p[1]))
        nx, ny = safe[0]
        return {
            "x": nx,
            "y": ny,
            "adjusted": True,
            "from": [x2, y2],
            "reason": "desktop icon drop target overlapped another icon; adjusted to nearby empty desktop space",
        }

    @staticmethod
    def _draw_region_payload(region: Region) -> dict[str, int]:
        return {
            "x": int(region.left),
            "y": int(region.top),
            "width": int(region.width),
            "height": int(region.height),
        }

    def _draw_target_geometry(self) -> tuple[int | None, Region | None, str]:
        """Return the explicitly bound, visible target window geometry."""
        target = getattr(self, "hwnd", None)
        try:
            target = int(target) if target else None
        except (TypeError, ValueError):
            target = None
        if not target:
            return None, None, (
                "normalized_strokes requires an explicitly focused/observed target window"
            )
        try:
            if not self._window_alive(target):
                return target, None, "the bound target window is no longer visible"
            if bool(win32gui.IsIconic(target)):
                return target, None, "the bound target window is minimized"
        except Exception:
            return target, None, "the bound target window could not be validated"
        try:
            region_fn = _tools_attr("window_region", window_region)
            region = region_fn(target).clamp_to_screen()
        except Exception as exc:
            return target, None, f"target-window geometry could not be read: {exc}"
        if region.width < 160 or region.height < 120:
            return target, None, "the visible target window is too small to ground a work surface"
        return target, region, ""

    def _cached_draw_surface(
        self,
        target: int,
        window: Region,
    ) -> tuple[Region | None, dict[str, Any] | None]:
        cache = getattr(self, "_draw_surface_cache", None)
        if not isinstance(cache, dict):
            return None, None
        expected_window = self._draw_region_payload(window)
        if cache.get("hwnd") != target or cache.get("window_region") != expected_window:
            self._draw_surface_cache = None
            return None, None
        raw = cache.get("surface_region")
        if not isinstance(raw, dict):
            self._draw_surface_cache = None
            return None, None
        try:
            surface = Region(
                int(raw["x"]), int(raw["y"]),
                int(raw["width"]), int(raw["height"]),
            )
        except (KeyError, TypeError, ValueError):
            self._draw_surface_cache = None
            return None, None
        if (
            surface.width < 120
            or surface.height < 80
            or surface.left < window.left
            or surface.top < window.top
            or surface.right > window.right
            or surface.bottom > window.bottom
        ):
            self._draw_surface_cache = None
            return None, None
        evidence = dict(cache)
        evidence["mapping_source"] = "same_hwnd_and_window_geometry_cache"
        return surface, evidence

    def _locate_draw_surface(
        self,
        target: int,
        window: Region,
        capture_fn,
    ) -> tuple[Region | None, dict[str, Any] | None, str]:
        cached, evidence = self._cached_draw_surface(target, window)
        if cached is not None:
            return cached, evidence, ""
        try:
            image = capture_fn(window)
        except Exception as exc:
            return None, None, f"could not capture current target-window pixels: {exc}"
        if image is None or getattr(image, "size", 0) == 0:
            return None, None, "current target-window capture was empty"
        try:
            image_height, image_width = image.shape[:2]
        except (AttributeError, TypeError, ValueError):
            return None, None, "current target-window capture had invalid pixel geometry"
        if image_width != window.width or image_height != window.height:
            return None, None, (
                "target-window capture dimensions did not match its physical-pixel geometry"
            )
        detection = vision_cv.credible_light_work_surface(image)
        if not isinstance(detection, dict) or detection.get("ok") is not True:
            reason = (
                str(detection.get("reason") or "surface detector returned no grounded region")
                if isinstance(detection, dict)
                else "surface detector returned invalid evidence"
            )
            return None, detection if isinstance(detection, dict) else None, reason
        bbox = detection.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return None, detection, "surface detector returned invalid bounding geometry"
        try:
            x1, y1, x2, y2 = [int(value) for value in bbox]
        except (TypeError, ValueError):
            return None, detection, "surface detector returned non-integer bounding geometry"
        if not (0 <= x1 < x2 <= image_width and 0 <= y1 < y2 <= image_height):
            return None, detection, "surface detector returned out-of-frame bounding geometry"
        surface = Region(
            window.left + x1,
            window.top + y1,
            x2 - x1,
            y2 - y1,
        )
        if surface.width < 120 or surface.height < 80:
            return None, detection, "detected work surface was too small for safe path mapping"
        payload: dict[str, Any] = {
            "hwnd": target,
            "window_region": self._draw_region_payload(window),
            "surface_region": self._draw_region_payload(surface),
            "detected_from": "pre_action_current_target_pixels",
            "mapping_source": "pre_action_current_target_pixels",
            "detection": {
                key: value for key, value in detection.items()
                if key != "bbox"
            },
        }
        self._draw_surface_cache = payload
        return surface, dict(payload), ""

    @staticmethod
    def _draw_prepare_error(failure_type: str, error: str) -> dict[str, Any]:
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "mutation_applied": False,
            "failure_type": failure_type,
            "error": error,
            "needs_observe": True,
            "recovery": (
                "Focus and uncover exactly one editable light work surface, observe the target "
                "again, then retry normalized_strokes. Do not guess physical coordinates."
            ),
        }

    def _prepare_draw_strokes_args(self, args: dict, capture_fn=None) -> dict:
        """Resolve one physical or surface-normalized stroke source exactly once."""
        prepared = dict(args or {})
        if prepared.get("_draw_strokes_prepared") is True:
            return prepared
        prepared["_draw_strokes_prepared"] = True
        has_physical = "strokes" in prepared and prepared.get("strokes") is not None
        has_normalized = (
            "normalized_strokes" in prepared
            and prepared.get("normalized_strokes") is not None
        )
        if has_physical == has_normalized:
            prepared["_draw_strokes_error"] = self._draw_prepare_error(
                "DRAW_COORDINATE_SOURCE_INVALID",
                "draw_strokes requires exactly one of strokes or normalized_strokes",
            )
            return prepared

        capture_fn = capture_fn or _tools_attr("capture", capture)
        target, window, target_error = self._draw_target_geometry()
        if has_physical:
            # Preserve the legacy physical-pixel contract. Surface grounding is
            # best-effort here solely to prime a complete pre-draw cache for a
            # later normalized call; failure must not block grounded px input.
            if target is not None and window is not None:
                surface, evidence, _error = self._locate_draw_surface(
                    target, window, capture_fn,
                )
                if surface is not None and evidence is not None:
                    prepared["_draw_surface_evidence"] = evidence
            return prepared

        normalized, validation_error = _validate_normalized_draw_strokes(
            prepared.get("normalized_strokes"),
        )
        if validation_error:
            prepared["_draw_strokes_error"] = self._draw_prepare_error(
                "INVALID_NORMALIZED_STROKES", validation_error,
            )
            return prepared
        if target is None or window is None:
            prepared["_draw_strokes_error"] = self._draw_prepare_error(
                "DRAW_SURFACE_NOT_GROUNDED", target_error,
            )
            return prepared
        surface, evidence, surface_error = self._locate_draw_surface(
            target, window, capture_fn,
        )
        if surface is None:
            prepared["_draw_strokes_error"] = self._draw_prepare_error(
                "DRAW_SURFACE_NOT_GROUNDED",
                f"normalized paths were not executed: {surface_error}",
            )
            if evidence:
                prepared["_draw_strokes_error"]["surface_detection"] = evidence
            return prepared

        x_span = max(0, surface.width - 1)
        y_span = max(0, surface.height - 1)
        prepared["strokes"] = [
            [
                [
                    surface.left + int(round(point[0] * x_span / 1000.0)),
                    surface.top + int(round(point[1] * y_span / 1000.0)),
                ]
                for point in stroke
            ]
            for stroke in normalized
        ]
        prepared.pop("normalized_strokes", None)
        prepared["mapped_from_coordinate_space"] = "work_surface_normalized_0_1000"
        prepared["_draw_surface_evidence"] = evidence
        return prepared

    def _t_draw_strokes(self, args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        capture_fn = _tools_attr("capture", capture)
        args = self._prepare_draw_strokes_args(args, capture_fn=capture_fn)
        prepare_error = args.get("_draw_strokes_error")
        if isinstance(prepare_error, dict):
            return ToolResult(self._fmt(prepare_error))
        raw = args.get("strokes")
        if not isinstance(raw, list) or not raw:
            return ToolResult("draw_strokes 需要 strokes: [[[x,y],...], ...]（至少一条笔画）")
        strokes = _normalize_draw_strokes(raw)
        duration_defaulted = args.get("duration") is None
        try:
            duration = (
                max(0.08, min(0.24, 1.2 / max(1, len(strokes))))
                if duration_defaulted
                else float(args.get("duration"))
            )
        except (TypeError, ValueError):
            return ToolResult(self._fmt({
                "ok": False,
                "blocked": True,
                "failure_type": "INVALID_DRAW_DURATION",
                "error": "draw_strokes duration must be a finite positive number",
            }))
        if not math.isfinite(duration) or duration <= 0:
            return ToolResult(self._fmt({
                "ok": False,
                "blocked": True,
                "failure_type": "INVALID_DRAW_DURATION",
                "error": "draw_strokes duration must be a finite positive number",
            }))
        all_coords: list[tuple[int, int]] = []
        for stroke in strokes:
            all_coords.extend((point[0], point[1]) for point in stroke)
        if not strokes:
            return ToolResult("draw_strokes 没有有效笔画（每条至少需要2个点）")
        xs = [c[0] for c in all_coords]
        ys = [c[1] for c in all_coords]
        pad = 28
        region = Region(min(xs) - pad, min(ys) - pad,
                        max(1, max(xs) - min(xs) + 2 * pad),
                        max(1, max(ys) - min(ys) + 2 * pad)).clamp_to_screen()
        deferred_verify = bool(args.get("defer_visual_verify"))
        before = None
        if not deferred_verify:
            try:
                before = capture_fn(region)
            except Exception as exc:
                logger.debug("draw_strokes pre-capture failed: %s", exc)
        result = executor_mod.draw_strokes(strokes, duration_s=duration, hwnd=self.hwnd)
        result["motion_duration_per_stroke_s"] = round(duration, 4)
        result["motion_duration_defaulted"] = duration_defaulted
        surface_evidence = args.get("_draw_surface_evidence")
        if isinstance(surface_evidence, dict):
            result["surface_region"] = surface_evidence.get("surface_region")
            result["surface_mapping_source"] = surface_evidence.get("mapping_source")
        if args.get("mapped_from_coordinate_space"):
            result["mapped_from_coordinate_space"] = args["mapped_from_coordinate_space"]
        frame_stroke_preparation = args.get("frame_stroke_preparation")
        if isinstance(frame_stroke_preparation, dict):
            # Keep the safety transform auditable without echoing the often
            # large point arrays back to the model.  This proves whether the
            # runtime used direct frame mapping, one whole-composition affine
            # fit, and/or conservative dense-anchor simplification.
            result["frame_stroke_preparation"] = frame_stroke_preparation
        if deferred_verify:
            result["visual_verify_deferred"] = True
            result["verify_method"] = "batch-final-visual-verify"
        if before is not None:
            try:
                time.sleep(0.12)
                after = capture_fn(region)
                ratio = vision_cv.diff_ratio(before, after)
                result["visual_change_ratio"] = round(float(ratio), 5)
                changed = bool(ratio >= 0.0008)
                result["visual_change_detected"] = changed
                if changed:
                    result["verified"] = True
                    result["verify_method"] = "cv-diff"
                    result["verify_detail"] = (
                        "visible pixels changed across the requested action region"
                    )
                if not changed:
                    # Device input being accepted is not proof that it landed
                    # on the intended drawable surface.  Keep this fail-closed:
                    # callers must recover focus/tool/canvas grounding instead
                    # of treating a no-op stroke as completed work.
                    result.update({
                        "ok": False,
                        "verified": False,
                        "mutation_applied": False,
                        "failure_type": "DRAW_NO_VISIBLE_CHANGE",
                        "error": "draw strokes produced no visible pixel change in the intended region",
                    })
                result["visual_feedback"] = (
                    f"drew {result.get('strokes_drawn', 0)} strokes; "
                    + ("visible pixels changed across the figure region — good evidence "
                       "the strokes landed on the canvas. Re-observe/zoom once if needed; "
                       "when a requested simple visible shape is confirmed, call done "
                       "instead of redrawing for thickness or aesthetics."
                       if changed else
                       "the region barely changed; the strokes may have hit toolbar/popup "
                       "chrome or an inactive surface. Re-observe, ensure a Pencil/Brush "
                       "tool is selected and the canvas is focused, then retry.")
                )
            except Exception as exc:
                logger.debug("draw_strokes post-capture/diff failed: %s", exc)
        elif not deferred_verify:
            # Capture failure must not silently become visual success. The
            # caller can observe or retry, but cannot mark the stroke verified.
            result.setdefault("verified", False)
            result["needs_observe"] = True
            result["verify_detail"] = "draw result has no visual capture evidence"
        return ToolResult(
            self._fmt(result),
            replay={
                "tool": "draw_strokes",
                "strokes": strokes,
                "duration": duration,
                **({
                    "surface_region": surface_evidence.get("surface_region"),
                    "mapped_from_coordinate_space": args.get("mapped_from_coordinate_space"),
                } if isinstance(surface_evidence, dict) else {}),
                **({
                    "frame_stroke_preparation": frame_stroke_preparation,
                } if isinstance(frame_stroke_preparation, dict) else {}),
            },
        )

    def _t_wait(self, args: dict) -> ToolResult:
        verify_mod = _tools_attr("verify", verify)
        condition = args.get("condition") or {}
        if not isinstance(condition, dict) or not condition:
            return ToolResult("wait 需要 condition 验证条件对象")
        timeout_s = float(args.get("timeout_s", 10.0))
        outcome = verify_mod.check(condition, hwnd=self.hwnd, timeout_s=timeout_s,
                                   stop_event=self.stop_event)
        return ToolResult(
            self._fmt(outcome.to_dict()),
            replay={"tool": "wait", "condition": condition, "timeout_s": timeout_s},
        )
