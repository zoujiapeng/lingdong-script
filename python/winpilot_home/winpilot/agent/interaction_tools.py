"""Input and visible-action tool handlers for ToolSession."""
from __future__ import annotations

import json
import sys
import time
from typing import Any

import win32gui

from ..action import executor, verify
from ..perception import vision_cv
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, capture
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
        try:
            target = self._resolve_hwnd({})
            if target:
                return int(target)
        except Exception:
            pass
        try:
            fg = win32gui.GetForegroundWindow()
            if fg and win32gui.IsWindowVisible(fg):
                return int(fg)
        except Exception:
            pass
        return self.hwnd

    def _t_click(self, args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        verify_mod = _tools_attr("verify", verify)
        element_id = args.get("element_id")
        button = args.get("button", "left")
        double = bool(args.get("double", False))
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
                if button == "left" and not double and executor_mod.uia_invoke(element):
                    BUS.publish("action", action="uia_invoke", elem_id=element.elem_id,
                                text=element.text)
                    return {"ok": True, "via": "uia_invoke"}
                return executor_mod.click(x, y, button=button, double=double, hwnd=self._action_hwnd())
        elif "x" in args and "y" in args:
            x, y = int(args["x"]), int(args["y"])
            replay["coords"] = [x, y]
            hit = self._element_at_point(x, y)
            if hit is not None:
                blocked = self._blocked_power_click_result(hit)
                if blocked is not None:
                    return blocked

            def action() -> dict:
                return executor_mod.click(x, y, button=button, double=double, hwnd=self._action_hwnd())
        else:
            return ToolResult("click 需要 element_id 或 x,y 坐标")

        result = verify_mod.run_with_verify(
            "click", action, expect=args.get("expect"), hwnd=self.hwnd,
            timeout_s=_expect_timeout(args), stop_event=self.stop_event)
        if not args.get("expect"):
            result.setdefault("verified", False)
            result.setdefault("verify_detail", "未提供 expect；ok 只表示点击已执行，必须 observe 确认界面变化")
        return ToolResult(self._fmt(result), replay=replay)

    def _t_type_text(self, args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        verify_mod = _tools_attr("verify", verify)
        win32gui_mod = _tools_attr("win32gui", win32gui)
        text = str(args["text"])
        element_id = args.get("element_id")
        replay: dict[str, Any] = {
            "tool": "type_text", "text_input": text, "expect": args.get("expect"),
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
            if element is not None:
                if executor_mod.uia_set_value(element, text):
                    return {"ok": True, "via": "uia_set_value"}
                executor_mod.click(element.cx, element.cy, hwnd=self._action_hwnd())
                time.sleep(0.15)
            target = self._action_hwnd()
            if target and win32gui_mod.GetForegroundWindow() != target:
                executor_mod.focus_window(target)
                time.sleep(0.12)
            return executor_mod.type_text(text, hwnd=target)

        result = verify_mod.run_with_verify(
            "type_text", action, expect=args.get("expect"),
            hwnd=self.hwnd, retryable=False, timeout_s=_expect_timeout(args),
            stop_event=self.stop_event)
        if not args.get("expect"):
            result.setdefault("verified", False)
            result.setdefault("verify_detail", "未提供 expect；ok 只表示输入动作已执行，必须 observe/read_text 确认文本位置")
        return ToolResult(self._fmt(result), replay=replay)

    def _t_hotkey(self, args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        verify_mod = _tools_attr("verify", verify)
        win32gui_mod = _tools_attr("win32gui", win32gui)
        keys = normalize_key_sequence(args.get("keys"))
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

        def action() -> dict:
            target = self._action_hwnd()
            if target and win32gui_mod.GetForegroundWindow() != target:
                executor_mod.focus_window(target)
                time.sleep(0.12)
            return executor_mod.press_keys(keys)

        result = verify_mod.run_with_verify(
            "hotkey", action,
            expect=args.get("expect"), hwnd=self.hwnd, retryable=False,
            timeout_s=_expect_timeout(args), stop_event=self.stop_event)
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
        result = executor_mod.scroll(x, y, amount)
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
                        "for drawing/canvas tasks, re-observe and try a visible stroke "
                        "inside the canvas rather than toolbar or popup chrome."
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

    def _t_draw_strokes(self, args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        capture_fn = _tools_attr("capture", capture)
        raw = args.get("strokes")
        if not isinstance(raw, list) or not raw:
            return ToolResult("draw_strokes 需要 strokes: [[[x,y],...], ...]（至少一条笔画）")
        duration = float(args.get("duration", 0.4))
        strokes: list[list[list[int]]] = []
        all_coords: list[tuple[int, int]] = []
        for stroke in raw:
            pts: list[list[int]] = []
            for p in stroke or []:
                try:
                    x, y = int(p[0]), int(p[1])
                except (TypeError, ValueError, IndexError, KeyError):
                    continue
                pts.append([x, y])
                all_coords.append((x, y))
            if len(pts) >= 2:
                strokes.append(pts)
        if not strokes:
            return ToolResult("draw_strokes 没有有效笔画（每条至少需要2个点）")
        xs = [c[0] for c in all_coords]
        ys = [c[1] for c in all_coords]
        pad = 28
        region = Region(min(xs) - pad, min(ys) - pad,
                        max(1, max(xs) - min(xs) + 2 * pad),
                        max(1, max(ys) - min(ys) + 2 * pad)).clamp_to_screen()
        before = None
        try:
            before = capture_fn(region)
        except Exception as exc:
            logger.debug("draw_strokes pre-capture failed: %s", exc)
        result = executor_mod.draw_strokes(strokes, duration_s=duration, hwnd=self.hwnd)
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
        return ToolResult(
            self._fmt(result),
            replay={"tool": "draw_strokes", "strokes": strokes, "duration": duration},
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
