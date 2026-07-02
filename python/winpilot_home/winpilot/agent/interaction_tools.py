"""Input and visible-action tool handlers for ToolSession."""
from __future__ import annotations

import sys
import time
from typing import Any

import win32gui

from ..action import executor, verify
from ..perception import vision_cv
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, capture
from .tool_result import ToolResult


_CANCEL_TEXTS = {"cancel", "\u53d6\u6d88"}


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


def _tools_attr(name: str, default):
    """Preserve historical monkeypatch paths on winpilot.agent.tools."""
    tools_mod = sys.modules.get("winpilot.agent.tools")
    return getattr(tools_mod, name, default) if tools_mod is not None else default


class InteractionToolMixin:
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

            def action() -> dict:
                if button == "left" and not double and executor_mod.uia_invoke(element):
                    BUS.publish("action", action="uia_invoke", elem_id=element.elem_id,
                                text=element.text)
                    return {"ok": True, "via": "uia_invoke"}
                return executor_mod.click(x, y, button=button, double=double, hwnd=self.hwnd)
        elif "x" in args and "y" in args:
            x, y = int(args["x"]), int(args["y"])
            replay["coords"] = [x, y]

            def action() -> dict:
                return executor_mod.click(x, y, button=button, double=double, hwnd=self.hwnd)
        else:
            return ToolResult("click 需要 element_id 或 x,y 坐标")

        result = verify_mod.run_with_verify(
            "click", action, expect=args.get("expect"), hwnd=self.hwnd,
            stop_event=self.stop_event)
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
            element = None

        def action() -> dict:
            if element is not None:
                if executor_mod.uia_set_value(element, text):
                    return {"ok": True, "via": "uia_set_value"}
                executor_mod.click(element.cx, element.cy, hwnd=self.hwnd)
                time.sleep(0.15)
            elif self.hwnd and win32gui_mod.GetForegroundWindow() != self.hwnd:
                executor_mod.focus_window(self.hwnd)
                time.sleep(0.12)
            return executor_mod.type_text(text, hwnd=self.hwnd)

        result = verify_mod.run_with_verify(
            "type_text", action, expect=args.get("expect"),
            hwnd=self.hwnd, retryable=False, stop_event=self.stop_event)
        if not args.get("expect"):
            result.setdefault("verified", False)
            result.setdefault("verify_detail", "未提供 expect；ok 只表示输入动作已执行，必须 observe/read_text 确认文本位置")
        return ToolResult(self._fmt(result), replay=replay)

    def _t_hotkey(self, args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        verify_mod = _tools_attr("verify", verify)
        win32gui_mod = _tools_attr("win32gui", win32gui)
        keys = [str(k) for k in args["keys"]]

        def action() -> dict:
            if self.hwnd and win32gui_mod.GetForegroundWindow() != self.hwnd:
                executor_mod.focus_window(self.hwnd)
                time.sleep(0.12)
            return executor_mod.press_keys(keys)

        result = verify_mod.run_with_verify(
            "hotkey", action,
            expect=args.get("expect"), hwnd=self.hwnd, retryable=False,
            stop_event=self.stop_event)
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
        if before is not None:
            try:
                time.sleep(0.12)
                after = capture_fn(region)
                ratio = vision_cv.diff_ratio(before, after)
                result["visual_change_ratio"] = round(float(ratio), 5)
                result["visual_change_detected"] = bool(ratio >= 0.0008)
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
                result["visual_change_detected"] = bool(ratio >= 0.0008)
                result["visual_feedback"] = (
                    f"drew {result.get('strokes_drawn', 0)} strokes; "
                    + ("visible pixels changed across the figure region — good evidence "
                       "the strokes landed on the canvas. Re-observe to check the result."
                       if ratio >= 0.0008 else
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
