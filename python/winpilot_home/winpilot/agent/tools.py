"""Agent tool dispatcher and per-run tool session state.

Function-calling schemas live in ``tool_schemas.py``. This module keeps the
runtime state: latest Snapshot, current target hwnd, world model, undo stack,
and concrete tool handlers. Every action tool can accept an ``expect``
condition (see action/verify.py DSL) and runs through Action→Verify→Retry.
"""
from __future__ import annotations

import json
import time
from typing import Any

import win32gui

from ..action import executor, shell, verify
from ..config import CONFIG
from ..perception import apps, fusion, ocr, search, tray, vision_cv, win32, world
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, capture, window_region
from . import planner

from .desktop_tools import DesktopToolMixin
from .file_tools import FileToolMixin
from .memory_tools import MemoryToolMixin
from .perception_tools import PerceptionToolMixin
from .tool_schemas import tool_schemas
from .tool_result import ToolResult
from .window_tools import WindowToolMixin


class ToolSession(
    FileToolMixin,
    MemoryToolMixin,
    DesktopToolMixin,
    WindowToolMixin,
    PerceptionToolMixin,
):
    """Per-run state: latest snapshot + current target window."""

    def __init__(self, stop_event=None, task: str = "") -> None:
        self.snapshot: fusion.Snapshot | None = None
        self.hwnd: int | None = None
        self.task = task or ""
        self._pending_visual_advisory = ""
        self._last_visual_advisory = ""
        self.target_hint: str = ""
        # Shared with the runner so long ops (reuse_skill replay) honor Stop.
        import threading
        self.stop_event = stop_event or threading.Event()
        # World model is fed from dispatch(); read once per run (runs are
        # short-lived, created per task) so a runtime toggle change applies
        # to the next task rather than mid-run.
        self.world_enabled: bool = bool(CONFIG.get("agent", "world_model", default=True))
        self.world = world.WorldModel()
        # Macro recovery: give the model one forced reconsideration before a
        # fail() actually terminates the run.
        self.replan_on_fail: bool = bool(CONFIG.get("agent", "replan_on_fail", default=True))
        self._fail_softened = False
        self.plan: planner.Plan | None = None
        # Lightweight undo stack: things this run created that can be rolled back.
        self._undo_stack: list[tuple[str, object]] = []

    # ----------------------------------------------------------- dispatch
    def dispatch(self, name: str, args: dict[str, Any]) -> ToolResult:
        handler = getattr(self, f"_t_{name}", None)
        if handler is None:
            return ToolResult(f"未知工具: {name}")
        try:
            result = handler(args)
        except Exception as exc:
            logger.exception("工具 %s 执行异常", name)
            return ToolResult(f"工具 {name} 执行异常: {exc}")
        if self.world_enabled:
            try:
                self.world.record_action(name, args)
                if result.is_observe and self.snapshot is not None:
                    self.world.observe_snapshot(self.snapshot)
            except Exception:
                logger.exception("世界模型更新失败")  # never block tool flow
        return result

    @staticmethod
    def _norm_window_text(value: object) -> str:
        return str(value or "").casefold()

    @staticmethod
    def _window_alive(hwnd: int | None) -> bool:
        return bool(hwnd and win32gui.IsWindow(int(hwnd)) and win32gui.IsWindowVisible(int(hwnd)))

    def _remember_target(self, hwnd: int | None = None, hint: str = "") -> None:
        if hwnd and self._window_alive(hwnd):
            self.hwnd = int(hwnd)
        hint = (hint or "").strip()
        if hint:
            self.target_hint = hint

    def _find_target_window(self) -> int | None:
        needle = self._norm_window_text(self.target_hint)
        if not needle:
            return None
        for window in win32.list_windows():
            title = self._norm_window_text(window.get("title"))
            class_name = self._norm_window_text(window.get("class_name"))
            if needle in title or title in needle or needle in class_name:
                hwnd = int(window.get("hwnd") or 0)
                if self._window_alive(hwnd):
                    return hwnd
        return None

    def _resolve_hwnd(self, args: dict) -> int | None:
        if args.get("hwnd"):
            hwnd = int(args["hwnd"])
            if self._window_alive(hwnd):
                self._remember_target(hwnd)
            return hwnd
        if self._window_alive(self.hwnd):
            return self.hwnd
        target = self._find_target_window()
        if target:
            self._remember_target(target)
            return target
        fg = win32gui.GetForegroundWindow()
        return fg if fg else None

    def _resolve_element(self, elem_id: int):
        if self.snapshot is None:
            return None, "尚未 observe，无元素可用"
        element = self.snapshot.by_id(elem_id)
        if element is None:
            return None, f"元素 id={elem_id} 不存在于最近一次 observe（请重新 observe）"
        return element, ""

    @staticmethod
    def _fmt(data: dict) -> str:
        return json.dumps(data, ensure_ascii=False, default=str)

    def _t_run_shell(self, args: dict) -> ToolResult:
        command = str(args["command"])
        shell_kind = str(args.get("shell", "powershell"))
        timeout_s = float(args.get("timeout_s", 30))
        result = shell.run(command, shell=shell_kind, timeout_s=timeout_s)
        return ToolResult(
            self._fmt(result),
            replay={"tool": "run_shell", "command": command, "shell": shell_kind,
                    "timeout_s": timeout_s},
        )

    def _t_run_detached(self, args: dict) -> ToolResult:
        script = str(args["script"])
        name = str(args.get("name", "task"))
        result = shell.run_detached(script, name=name)
        if result.get("ok") and result.get("pid"):
            self._undo_stack.append(("detached", result["pid"]))  # cancelable
        return ToolResult(
            self._fmt(result),
            replay={"tool": "run_detached", "script": script, "name": name},
        )

    def _t_undo_last(self, _args: dict) -> ToolResult:
        """Best-effort rollback of the most recent reversible action this run:
        cancel a scheduled/background task, close the last launched app, or
        Ctrl+Z the foreground window. GUI is only partly reversible."""
        while self._undo_stack:
            kind, payload = self._undo_stack.pop()
            if kind == "detached":
                import subprocess
                subprocess.run(["taskkill", "/f", "/pid", str(payload)],
                               capture_output=True, timeout=10)
                BUS.publish("action", action="undo", what=f"取消后台任务 pid={payload}")
                return ToolResult(f"已取消最近的后台/定时任务 (pid={payload})。")
            if kind == "app":
                # Close the specific launched window (not the current foreground,
                # which the user may have switched). Skip if it's already gone.
                hwnd = payload
                if not hwnd or not win32gui.IsWindow(hwnd):
                    continue
                title = win32._window_text(hwnd)
                res = executor.close_window(hwnd)
                if res.get("ok"):
                    BUS.publish("action", action="undo", what=f"关闭窗口 {title}")
                    return ToolResult(f'已关闭最近打开的程序窗口 "{title}"。')
                # couldn't close → fall through to next undo candidate
        # nothing on the stack → editor-style undo on the foreground
        executor.press_keys(["ctrl", "z"])
        return ToolResult("无可回收的启动记录；已对当前窗口执行撤销 (Ctrl+Z)。")

    def _t_search_files(self, args: dict) -> ToolResult:
        result = search.search(
            str(args["query"]),
            max_results=int(args.get("max_results", 50)),
            path=args.get("path"),
        )
        return ToolResult(self._fmt(result))

    def _t_tray_click(self, args: dict) -> ToolResult:
        result = tray.click_tray_icon(
            str(args["name"]),
            double=bool(args.get("double", False)),
            button=str(args.get("button", "left")),
        )
        return ToolResult(
            self._fmt(result),
            replay={"tool": "tray_click", "name": str(args["name"]),
                    "double": bool(args.get("double", False)),
                    "button": str(args.get("button", "left"))},
        )

    def _t_launch(self, args: dict) -> ToolResult:
        command = str(args["command"]).strip()
        resolved = self._resolve_launch_target(command)

        def do_launch() -> dict:
            if resolved is not None:
                return apps.launch_target(resolved)
            return executor.launch(command)

        result = verify.run_with_verify(
            "launch",
            do_launch,
            expect=args.get("expect"),
            hwnd=self.hwnd,
            timeout_s=10.0,
            stop_event=self.stop_event,
        )
        if result.get("ok"):
            # Record the launched app's window (post-launch foreground) so
            # undo_last closes THAT window, not whatever is foreground later.
            launched_hwnd = (result.get("post_state") or {}).get("foreground_hwnd")
            self._undo_stack.append(("app", launched_hwnd))
            # Prefer resolving the launched window by its expected title: the
            # post-launch foreground can be stolen by another window (a console,
            # a notification), which would otherwise make the next bare observe
            # target the wrong window. Title match is focus-independent.
            expect = args.get("expect") or {}
            want_title = str(expect.get("window_appears") or "").strip()
            title_hwnd = None
            if want_title:
                found = win32.find_window(want_title)
                if found and self._window_alive(int(found.get("hwnd") or 0)):
                    title_hwnd = int(found["hwnd"])
            if title_hwnd:
                self._remember_target(title_hwnd, want_title)
                result["target_hwnd"] = title_hwnd
            elif resolved is not None:
                self._remember_target(launched_hwnd, str(resolved.get("name") or command))
                target = self._find_target_window()
                if target:
                    self._remember_target(target)
            elif launched_hwnd:
                self._remember_target(launched_hwnd)
        if resolved is not None:
            result["resolved_app"] = f'{resolved["name"]} [{resolved["kind"]}]'
            if self.hwnd:
                result["target_hwnd"] = self.hwnd
        return ToolResult(
            self._fmt(result),
            replay={"tool": "launch", "command": command,
                    "expect": args.get("expect")},
        )

    @staticmethod
    def _resolve_launch_target(command: str) -> dict | None:
        """Map an app display name (e.g. 网易云音乐) to an installed-app target.

        Only kicks in when the command is clearly NOT directly executable:
        not an existing path, not on PATH, no shell syntax. Keeps notepad/calc
        and full commands on the legacy Popen path untouched.
        """
        import shutil
        from pathlib import Path as _P

        if not command or any(ch in command for ch in "\\/|&><\""):
            return None
        if _P(command).exists() or shutil.which(command):
            return None
        base = command.split()[0] if command.isascii() else command
        if command.isascii() and (shutil.which(base) or len(command.split()) > 1):
            return None  # looks like a real command line — don't second-guess it
        candidates = apps.resolve(command)
        return candidates[0] if candidates else None

    def _t_click(self, args: dict) -> ToolResult:
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

            def action() -> dict:
                # UIA fast path for plain left single clicks
                if button == "left" and not double and executor.uia_invoke(element):
                    BUS.publish("action", action="uia_invoke", elem_id=element.elem_id,
                                text=element.text)
                    return {"ok": True, "via": "uia_invoke"}
                return executor.click(x, y, button=button, double=double, hwnd=self.hwnd)
        elif "x" in args and "y" in args:
            x, y = int(args["x"]), int(args["y"])
            replay["coords"] = [x, y]

            def action() -> dict:
                return executor.click(x, y, button=button, double=double, hwnd=self.hwnd)
        else:
            return ToolResult("click 需要 element_id 或 x,y 坐标")

        result = verify.run_with_verify(
            "click", action, expect=args.get("expect"), hwnd=self.hwnd,
            stop_event=self.stop_event)
        if not args.get("expect"):
            result.setdefault("verified", False)
            result.setdefault("verify_detail", "未提供 expect；ok 只表示点击已执行，必须 observe 确认界面变化")
        return ToolResult(self._fmt(result), replay=replay)

    def _t_type_text(self, args: dict) -> ToolResult:
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
            import time
            if element is not None:
                if executor.uia_set_value(element, text):
                    return {"ok": True, "via": "uia_set_value"}
                executor.click(element.cx, element.cy, hwnd=self.hwnd)
                time.sleep(0.15)
            elif self.hwnd:
                # Guard against focus theft (e.g. another window came to front
                # between the last observe and now). Re-focus the target window
                # before typing so SendInput lands in the right place.
                import win32gui
                if win32gui.GetForegroundWindow() != self.hwnd:
                    executor.focus_window(self.hwnd)
                    time.sleep(0.12)
            return executor.type_text(text, hwnd=self.hwnd)

        result = verify.run_with_verify(
            "type_text", action, expect=args.get("expect"),
            hwnd=self.hwnd, retryable=False, stop_event=self.stop_event)
        if not args.get("expect"):
            result.setdefault("verified", False)
            result.setdefault("verify_detail", "未提供 expect；ok 只表示输入动作已执行，必须 observe/read_text 确认文本位置")
        return ToolResult(self._fmt(result), replay=replay)

    def _t_hotkey(self, args: dict) -> ToolResult:
        keys = [str(k) for k in args["keys"]]

        def action() -> dict:
            import time
            import win32gui
            if self.hwnd and win32gui.GetForegroundWindow() != self.hwnd:
                executor.focus_window(self.hwnd)
                time.sleep(0.12)
            return executor.press_keys(keys)

        result = verify.run_with_verify(
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
        x, y, amount = int(args["x"]), int(args["y"]), int(args["amount"])
        result = executor.scroll(x, y, amount)
        return ToolResult(
            self._fmt(result),
            replay={"tool": "scroll", "coords": [x, y], "amount": amount},
        )

    def _t_drag(self, args: dict) -> ToolResult:
        x1, y1 = int(args["x1"]), int(args["y1"])
        x2, y2 = int(args["x2"]), int(args["y2"])
        points = args.get("points") if isinstance(args.get("points"), list) else None
        duration = float(args.get("duration", 0.4))
        region = self._drag_feedback_region(x1, y1, x2, y2, points)
        before = None
        try:
            before = capture(region)
        except Exception as exc:
            logger.debug("drag pre-capture failed: %s", exc)
        result = executor.drag(x1, y1, x2, y2, duration_s=duration,
                               points=points, hwnd=self.hwnd)
        if before is not None:
            try:
                time.sleep(0.12)
                after = capture(region)
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
        raw = args.get("strokes")
        if not isinstance(raw, list) or not raw:
            return ToolResult("draw_strokes 需要 strokes: [[[x,y],...], ...]（至少一条笔画）")
        duration = float(args.get("duration", 0.4))
        # Normalize + collect all coords for one before/after diff region.
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
            before = capture(region)
        except Exception as exc:
            logger.debug("draw_strokes pre-capture failed: %s", exc)
        result = executor.draw_strokes(strokes, duration_s=duration, hwnd=self.hwnd)
        if before is not None:
            try:
                time.sleep(0.12)
                after = capture(region)
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
        condition = args.get("condition") or {}
        if not isinstance(condition, dict) or not condition:
            return ToolResult("wait 需要 condition 验证条件对象")
        timeout_s = float(args.get("timeout_s", 10.0))
        outcome = verify.check(condition, hwnd=self.hwnd, timeout_s=timeout_s,
                               stop_event=self.stop_event)
        return ToolResult(
            self._fmt(outcome.to_dict()),
            replay={"tool": "wait", "condition": condition, "timeout_s": timeout_s},
        )

    def _t_revise_plan(self, args: dict) -> ToolResult:
        steps = args.get("steps") or []
        if not isinstance(steps, list) or not steps:
            return ToolResult("revise_plan 需要非空的 steps 数组")
        self.plan = planner.Plan(steps)  # items may be str or {goal, check}
        # A new approach earns a fresh soft-fail bounce (per-approach, not per-run).
        self._fail_softened = False
        BUS.publish("agent_plan", action="revise", plan=self.plan.snapshot())
        return ToolResult(f"已更新计划：\n{self.plan.render()}")

    def _t_complete_subgoal(self, args: dict) -> ToolResult:
        if self.plan is None or not self.plan.active():
            return ToolResult("当前没有计划，可先调用 revise_plan 制定")
        index = int(args.get("index", -1))
        if not (1 <= index <= len(self.plan.steps)):
            return ToolResult(f"子目标序号 {index} 无效。\n{self.plan.render()}")
        # Subgoal verification gate: if the planner attached a deterministic
        # check, the subgoal can't be marked done until it actually passes.
        check_expect = self.plan.check_for(index)
        if check_expect:
            outcome = verify.check(check_expect, hwnd=self.hwnd, timeout_s=4.0)
            BUS.publish("agent_plan", action="subgoal_check", index=index,
                        verified=outcome.verified, detail=outcome.detail)
            if not outcome.verified:
                return ToolResult(
                    f"子目标 {index} 核验未通过（{outcome.detail}）——尚未真正完成，"
                    "请先达成该子目标再标完成，不要跳步。")
        self.plan.complete(index)
        BUS.publish("agent_plan", action="complete", index=index,
                    plan=self.plan.snapshot())
        if self.plan.all_done():
            return ToolResult(
                f"已完成子目标 {index}（已核验）。所有子目标均完成——若任务确已达成请调用 done。\n"
                f"{self.plan.render()}")
        return ToolResult(f"已完成子目标 {index}{'（已核验）' if check_expect else ''}。\n"
                          f"{self.plan.render()}")

    def _t_done(self, args: dict) -> ToolResult:
        summary = str(args.get("summary", ""))
        return ToolResult(f"任务完成: {summary}", terminal="done")

    def _t_fail(self, args: dict) -> ToolResult:
        reason = str(args.get("reason", ""))
        # Macro recovery: don't give up on the first fail — force one change of
        # approach. Only the second fail (or when disabled) truly terminates.
        if self.replan_on_fail and not self._fail_softened:
            self._fail_softened = True
            BUS.publish("agent_recovery", reason=reason[:200])
            return ToolResult(
                f"在放弃前请再换一种思路尝试一次（你给的失败原因：{reason}）。"
                "建议：重新 observe 确认当前真实界面；检查是否有未处理的弹窗/遮挡；"
                "能否改用 run_shell 直接命令行完成；是否漏看了某个元素或其它窗口。"
                "若确认所有可行办法都已尝试且无解，再次调用 fail 即真正结束。",
                terminal=None,
            )
        return ToolResult(f"任务失败: {reason}", terminal="fail")
