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

import win32con
import win32gui

from ..action import executor, shell, verify
from ..config import CONFIG
from ..perception import (apps, fusion, intent, ocr, search, tray, vision_cv,
                          vlm, win32, world)
from ..utils.image_capture import capture_window_jpeg_b64
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, capture, window_region
from . import planner

from .desktop_tools import DesktopToolMixin
from .file_tools import FileToolMixin
from .memory_tools import MemoryToolMixin
from .tool_schemas import tool_schemas
from .tool_result import ToolResult


class ToolSession(FileToolMixin, MemoryToolMixin, DesktopToolMixin):
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

    def _perception_mode(self) -> str:
        """Return the perception strategy mode for the current task + window title."""
        title = self.snapshot.title if self.snapshot else ""
        return intent.perception_mode(self.task, title)

    def _vision_priority(self) -> bool:
        """Legacy accessor — True when perception mode is 'visual'."""
        return self._perception_mode() != "normal"

    def _needs_visual_state_summary(self) -> bool:
        text = f"{self.task} {self.snapshot.title if self.snapshot else ''}".casefold()
        needles = (
            "draw", "drawing", "paint", "sketch", "canvas", "whiteboard",
            "\u753b", "\u753b\u56fe", "\u7ed8\u56fe", "\u6d82\u9e26",
            "\u753b\u677f", "\u767d\u677f", "\u989c\u8272", "color",
        )
        return any(needle in text for needle in needles)

    def _capture_window_b64(self, hwnd: int) -> str:
        """Capture the target window as a JPEG and return base64.

        Only called when the main model has native vision. Returns empty string
        on any error so the observe result is never blocked.
        """
        from ..agent.loop import _main_model_has_vision
        if not _main_model_has_vision():
            return ""
        image_b64 = capture_window_jpeg_b64(hwnd)
        if not image_b64:
            logger.debug("窗口截图捕获失败或返回空图像")
        return image_b64

    def _visual_state_summary(self, hwnd: int) -> str:
        if not self._needs_visual_state_summary():
            return ""
        try:
            region = window_region(hwnd)
            image = capture(region)
            canvas_bbox = vision_cv.likely_canvas_bbox(image)
            offset_x = 0
            offset_y = 0
            scope = "current window"
            if canvas_bbox:
                x1, y1, x2, y2 = canvas_bbox
                image = image[y1:y2, x1:x2]
                offset_x = x1
                offset_y = y1
                scope = f"likely_canvas@({region.left + x1},{region.top + y1},{region.left + x2},{region.top + y2})"
            stats = vision_cv.color_presence(image)
        except Exception as exc:
            logger.debug("visual state summary failed: %s", exc)
            return ""
        colors = stats.get("colors", {}) if isinstance(stats, dict) else {}
        visible_parts: list[str] = []
        for name in ("red", "orange", "yellow", "green", "blue", "purple", "black"):
            item = colors.get(name, {})
            if not item.get("visible"):
                continue
            bbox = item.get("largest_bbox") or item.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                sx1, sy1, sx2, sy2 = (
                    int(bbox[0]) + region.left + offset_x,
                    int(bbox[1]) + region.top + offset_y,
                    int(bbox[2]) + region.left + offset_x,
                    int(bbox[3]) + region.top + offset_y,
                )
                visible_parts.append(f"{name}@({sx1},{sy1},{sx2},{sy2})")
            else:
                visible_parts.append(name)
        popup_terms = (
            "PopupHost", "Line", "Fill", "Color 1:", "No fill", "Brush",
            "Edit colors", "OK", "Cancel", "RGB",
        )
        popup_open = False
        if self.snapshot is not None:
            popup_open = any(
                any(term in (element.text or "") for term in popup_terms)
                or "popup" in (element.role or "").casefold()
                for element in self.snapshot.elements[:80]
            )
        colors_text = ", ".join(visible_parts) if visible_parts else "none"
        popup_text = ""
        advisory = ""
        if popup_open and scope == "current window":
            popup_text = (
                "; popup/tool panel appears open; these colors may belong to "
                "the tool panel, not the canvas"
            )
            advisory = (
                "A tool/color modal is foreground. Its visible colors are controls, "
                "not proof of canvas content. Finish with OK/Cancel/Escape or close "
                "the modal, then observe the canvas before drawing or claiming color evidence."
            )
        elif popup_open:
            popup_text = "; popup/tool panel appears open"
            advisory = (
                "A popup/tool panel is open over the canvas. If tool/color selection is done, "
                "close it with OK/Escape or click back on the canvas before drawing. Do not keep "
                "probing the same panel."
            )
        if advisory and advisory != self._last_visual_advisory:
            self._pending_visual_advisory = advisory
            self._last_visual_advisory = advisory
        return (
            f"Visual state summary ({scope}): visible coarse colors: "
            f"{colors_text}{popup_text}. If this contradicts the plan, correct "
            "the next action before continuing."
        )

    def take_visual_advisory(self) -> str:
        note = self._pending_visual_advisory
        self._pending_visual_advisory = ""
        return note

    # -------------------------------------------------------------- tools
    def _t_list_windows(self, _args: dict) -> ToolResult:
        windows = win32.list_windows()
        lines = [
            f'hwnd={w["hwnd"]} "{w["title"]}" class={w["class_name"]} rect={w["rect"]}'
            for w in windows[:40]
        ]
        groups: dict[tuple[str, str], list[dict]] = {}
        for window in windows:
            key = (str(window.get("title", "")).lower(),
                   str(window.get("class_name", "")).lower())
            groups.setdefault(key, []).append(window)
        duplicates = [
            wins for (title, _class), wins in groups.items()
            if title and len(wins) > 1
        ]
        if duplicates:
            lines.append("")
            lines.append(
                "NOTE: multiple windows share the same title/class. Do not assume "
                "the foreground one is the right one; use scan_windows(query=...) "
                "or observe each hwnd before login/account actions."
            )
            for wins in duplicates[:5]:
                label = wins[0].get("title", "")
                hwnds = ", ".join(str(w.get("hwnd")) for w in wins[:8])
                lines.append(f'duplicate "{label}": hwnds={hwnds}')
        return ToolResult("\n".join(lines) or "No visible windows.",
                          summary=f"[folded] list_windows {len(windows)} windows")

    @staticmethod
    def _window_signal(text: str) -> str:
        low = text.lower()
        auth = (
            "登录", "扫码", "密码", "账号", "注册", "服务协议", "隐私政策",
            "login", "sign in", "password", "account", "privacy",
        )
        search_like = (
            "搜索", "查找", "联系人", "消息", "会话", "好友", "群聊", "聊天",
            "search", "contacts", "messages", "chats",
        )
        if any(word in low for word in auth):
            return "auth/login-like"
        if any(word in low for word in search_like):
            return "main/search-like"
        return "unknown"

    def _t_scan_windows(self, args: dict) -> ToolResult:
        query = str(args.get("query") or "").strip().lower()
        max_windows = max(1, min(int(args.get("max_windows", 6)), 10))
        windows = win32.list_windows()
        matches = [
            w for w in windows
            if not query
            or query in str(w.get("title", "")).lower()
            or query in str(w.get("class_name", "")).lower()
        ]
        used_hidden_candidates = False
        if not matches:
            app_state = win32.find_app(query) if query else {"windows": []}
            for item in app_state.get("windows", []):
                hwnd = int(item.get("hwnd") or 0)
                if not hwnd or not win32gui.IsWindow(hwnd):
                    continue
                try:
                    title = win32gui.GetWindowText(hwnd) or str(item.get("title", ""))
                    class_name = win32gui.GetClassName(hwnd)
                    if query and query not in title.lower() and query not in class_name.lower():
                        continue
                    rect = list(win32gui.GetWindowRect(hwnd))
                    matches.append({
                        "hwnd": hwnd,
                        "title": title,
                        "class_name": class_name,
                        "rect": rect,
                        "pid": item.get("pid", 0),
                        "visible": bool(win32gui.IsWindowVisible(hwnd)),
                    })
                except Exception:
                    continue
            used_hidden_candidates = bool(matches)
        if not matches:
            return ToolResult(f'No visible or running windows match query "{query}".')

        original = win32gui.GetForegroundWindow()
        rows: list[str] = []
        best_hwnd: int | None = None
        best_score = -999.0
        for window in matches[:max_windows]:
            hwnd = int(window["hwnd"])
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNORMAL)
                    time.sleep(0.25)
                executor.focus_window(hwnd)
                time.sleep(0.2)
                snapshot = fusion.observe(hwnd, include_icons=False)
                texts = [e.text.strip() for e in snapshot.elements if e.text.strip()]
                unique = list(dict.fromkeys(texts))
                joined = " | ".join(unique[:18])
                signal = self._window_signal(joined)
                score = (3 if signal.startswith("main") else 0) - (
                    2 if signal.startswith("auth") else 0
                ) + min(len(unique), 12) / 20
                if score > best_score:
                    best_score = score
                    best_hwnd = hwnd
                rows.append(
                    f'hwnd={hwnd} title="{window["title"]}" rect={window["rect"]} '
                    f'signal={signal} text={joined or "(no readable text)"}'
                )
            except Exception as exc:
                rows.append(
                    f'hwnd={hwnd} title="{window.get("title", "")}" '
                    f'error={type(exc).__name__}: {exc}'
                )
        if original and win32gui.IsWindow(original):
            try:
                executor.focus_window(original)
            except Exception:
                pass
        if best_hwnd is not None:
            rows.insert(0, f"recommended_hwnd={best_hwnd}")
        prefix = "Scanned hidden/running candidates because no visible window matched. " if used_hidden_candidates else ""
        rows.insert(
            0,
            prefix
            + "Prefer main/search-like windows over auth/login-like windows. "
            + "signal=unknown means insufficient evidence; do not classify it as a login page without login/auth text."
        )
        return ToolResult("\n".join(rows))

    def _t_observe(self, args: dict) -> ToolResult:
        hwnd = self._resolve_hwnd(args)
        if not hwnd:
            return ToolResult("无法确定目标窗口，请先 list_windows + focus_window")
        include_icons = bool(args.get("include_icons", True))
        self.snapshot = fusion.observe(hwnd, include_icons=include_icons,
                                       perception_mode=self._perception_mode())
        self._remember_target(hwnd, self.snapshot.title)
        text = self.snapshot.to_text()
        visual_summary = self._visual_state_summary(hwnd)
        if visual_summary:
            text = text + "\n" + visual_summary
        summary = (
            f'[已折叠] observe "{self.snapshot.title}" '
            f"{len(self.snapshot.elements)}个元素"
        )
        image_b64 = self._capture_window_b64(hwnd)
        return ToolResult(text, is_observe=True, summary=summary, image_b64=image_b64 if image_b64 else "")

    def _t_focus_window(self, args: dict) -> ToolResult:
        hwnd = int(args["hwnd"])
        title = win32._window_text(hwnd)
        result = executor.focus_window(hwnd)
        if result.get("ok"):
            self._remember_target(hwnd, title)
        return ToolResult(
            self._fmt(result),
            replay={"tool": "focus_window", "title": title},
        )

    def _t_find_app(self, args: dict) -> ToolResult:
        info = win32.find_app(str(args["name"]))
        return ToolResult(self._fmt(info))

    def _t_find_program(self, args: dict) -> ToolResult:
        candidates = apps.resolve(str(args["name"]))
        if not candidates:
            return ToolResult(
                f'未找到名称含 "{args["name"]}" 的已安装程序。'
                "可换更短的关键词重试，或用 search_files 搜可执行文件")
        lines = [
            f'{i}. "{c["name"]}" [{c["kind"]}] {c["target"]}'
            for i, c in enumerate(candidates, 1)
        ]
        self.target_hint = str(candidates[0].get("name") or args["name"])
        return ToolResult(
            "已安装程序候选（直接 launch 其名称或完整 target 即可启动）:\n"
            + "\n".join(lines))

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

    def _t_read_text(self, args: dict) -> ToolResult:
        hwnd = self._resolve_hwnd(args)
        if not hwnd:
            return ToolResult("无目标窗口")
        snapshot = fusion.observe(hwnd, include_icons=False,
                                   perception_mode=self._perception_mode())
        texts = [e.text for e in snapshot.elements if e.text.strip()]
        ocr_elements = ocr.collect(snapshot.region, min_conf=0.45)
        texts.extend(e.text for e in ocr_elements if e.text.strip())
        body = "\n".join(dict.fromkeys(texts))  # de-dupe, keep order
        return ToolResult(body or "（未识别到文字）",
                          summary=f"[已折叠] read_text {len(texts)}条文字")

    def _t_find_image(self, args: dict) -> ToolResult:
        matches = vision_cv.match_template(str(args["template"]))
        if not matches:
            available = vision_cv.list_templates()
            listing = ("可用模板: " + ", ".join(available)) if available else (
                "模板库为空——可用 capture_template 先截存一个图标区域作为模板")
            return ToolResult(f"未找到匹配（检查模板名/阈值）。{listing}")
        lines = [
            f'匹配 "{m.text}" 中心({m.cx},{m.cy}) 置信度{m.confidence:.2f}'
            for m in matches
        ]
        return ToolResult("\n".join(lines))

    def _t_capture_template(self, args: dict) -> ToolResult:
        region = Region(int(args["left"]), int(args["top"]),
                        int(args["width"]), int(args["height"]))
        if region.width < 8 or region.height < 8 or region.width > 600 or region.height > 600:
            return ToolResult("模板区域不合理（宽高需在 8~600 像素之间，应只框住目标图标/按钮）")
        path = vision_cv.save_template(region, str(args["name"]))
        BUS.publish("action", action="capture_template", name=path.stem,
                    size=f"{region.width}x{region.height}")
        return ToolResult(f"已保存模板 \"{path.stem}\"，之后可用 find_image(\"{path.stem}\") 查找")

    def _t_color_probe(self, args: dict) -> ToolResult:
        probe = vision_cv.color_probe(int(args["x"]), int(args["y"]))
        return ToolResult(self._fmt(probe))

    def _t_vlm_describe(self, args: dict) -> ToolResult:
        if not vlm.is_configured():
            return ToolResult("VLM 未配置")
        if all(k in args for k in ("left", "top", "width", "height")):
            region = Region(int(args["left"]), int(args["top"]),
                            int(args["width"]), int(args["height"]))
        else:
            hwnd = self._resolve_hwnd(args)
            if not hwnd:
                return ToolResult("无目标窗口且未指定区域")
            region = window_region(hwnd)
        answer = vlm.describe(region, str(args["question"]))
        return ToolResult(answer)

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
