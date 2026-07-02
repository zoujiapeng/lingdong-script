"""System, launch, tray, shell, and undo tool handlers for ToolSession."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import win32gui

from ..action import executor, shell, verify
from ..perception import apps, search, tray, win32
from ..utils.log import BUS
from .tool_result import ToolResult


def _tools_attr(name: str, default):
    """Preserve historical monkeypatch paths on winpilot.agent.tools."""
    tools_mod = sys.modules.get("winpilot.agent.tools")
    return getattr(tools_mod, name, default) if tools_mod is not None else default


class SystemToolMixin:
    @staticmethod
    def _launch_process_query(command: str, launch_result: dict) -> str:
        """Best-effort process basename for a shell launch command.

        This is intentionally conservative: URI launches and shell namespaces
        do not map to a stable process name, while exe/bare commands usually do.
        """
        candidates = [
            (str(launch_result.get("target") or ""), True),
            (command, False),
        ]
        for candidate, is_resolved_target in candidates:
            text = (candidate or "").strip().strip('"')
            if not text:
                continue
            lower = text.casefold()
            if (
                "://" in lower
                or lower.startswith(("shell:", "steam:", "http:", "https:", "ms-"))
            ):
                continue
            if is_resolved_target:
                head = text
            elif text.startswith('"') and '"' in text[1:]:
                head = text[1:].split('"', 1)[0]
            else:
                head = text.split()[0].strip('"')
            name = Path(head).name or head
            stem = Path(name).stem
            if stem.casefold() in {"start", "explorer", "cmd", "powershell"}:
                continue
            if stem:
                return stem
        return ""

    def _launch_visible_window(self, command: str, launch_result: dict, win32_mod) -> dict | None:
        query = self._launch_process_query(command, launch_result)
        if not query:
            return None
        try:
            app_state = win32_mod.find_app(query)
        except Exception:
            return None
        for window in app_state.get("windows") or []:
            hwnd = int(window.get("hwnd") or 0)
            if window.get("visible") and self._window_alive(hwnd):
                return {
                    "hwnd": hwnd,
                    "title": str(window.get("title") or ""),
                    "pid": window.get("pid"),
                    "query": query,
                    "process_count": app_state.get("process_count", 0),
                }
        return None

    def _wait_launch_visible_window(
        self,
        command: str,
        launch_result: dict,
        win32_mod,
        timeout_s: float = 7.0,
    ) -> dict | None:
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            found = self._launch_visible_window(command, launch_result, win32_mod)
            if found:
                return found
            if time.monotonic() >= deadline:
                return None
            if getattr(self, "stop_event", None) is not None and self.stop_event.is_set():
                return None
            time.sleep(0.35)

    def _t_run_shell(self, args: dict) -> ToolResult:
        shell_mod = _tools_attr("shell", shell)
        command = str(args["command"])
        shell_kind = str(args.get("shell", "powershell"))
        timeout_s = float(args.get("timeout_s", 30))
        result = shell_mod.run(command, shell=shell_kind, timeout_s=timeout_s)
        return ToolResult(
            self._fmt(result),
            replay={"tool": "run_shell", "command": command, "shell": shell_kind,
                    "timeout_s": timeout_s},
        )

    def _t_run_detached(self, args: dict) -> ToolResult:
        shell_mod = _tools_attr("shell", shell)
        script = str(args["script"])
        name = str(args.get("name", "task"))
        result = shell_mod.run_detached(script, name=name)
        if result.get("ok") and result.get("pid"):
            self._undo_stack.append(("detached", result["pid"]))
        return ToolResult(
            self._fmt(result),
            replay={"tool": "run_detached", "script": script, "name": name},
        )

    def _t_undo_last(self, _args: dict) -> ToolResult:
        executor_mod = _tools_attr("executor", executor)
        win32_mod = _tools_attr("win32", win32)
        win32gui_mod = _tools_attr("win32gui", win32gui)
        while self._undo_stack:
            kind, payload = self._undo_stack.pop()
            if kind == "detached":
                import subprocess
                subprocess.run(["taskkill", "/f", "/pid", str(payload)],
                               capture_output=True, timeout=10)
                BUS.publish("action", action="undo", what=f"取消后台任务 pid={payload}")
                return ToolResult(f"已取消最近的后台/定时任务 (pid={payload})。")
            if kind == "app":
                hwnd = payload
                if not hwnd or not win32gui_mod.IsWindow(hwnd):
                    continue
                title = win32_mod._window_text(hwnd)
                res = executor_mod.close_window(hwnd)
                if res.get("ok"):
                    BUS.publish("action", action="undo", what=f"关闭窗口 {title}")
                    return ToolResult(f'已关闭最近打开的程序窗口 "{title}"。')
        executor_mod.press_keys(["ctrl", "z"])
        return ToolResult("无可回收的启动记录；已对当前窗口执行撤销 (Ctrl+Z)。")

    def _t_search_files(self, args: dict) -> ToolResult:
        search_mod = _tools_attr("search", search)
        result = search_mod.search(
            str(args["query"]),
            max_results=int(args.get("max_results", 50)),
            path=args.get("path"),
        )
        return ToolResult(self._fmt(result))

    def _t_tray_click(self, args: dict) -> ToolResult:
        tray_mod = _tools_attr("tray", tray)
        result = tray_mod.click_tray_icon(
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
        apps_mod = _tools_attr("apps", apps)
        executor_mod = _tools_attr("executor", executor)
        verify_mod = _tools_attr("verify", verify)
        win32_mod = _tools_attr("win32", win32)
        command = str(args["command"]).strip()
        resolved = self._resolve_launch_target(command)
        prior_hwnd = self.hwnd

        def do_launch() -> dict:
            if resolved is not None:
                return apps_mod.launch_target(resolved)
            return executor_mod.launch(command)

        result = verify_mod.run_with_verify(
            "launch",
            do_launch,
            expect=args.get("expect"),
            hwnd=self.hwnd,
            retryable=False,
            timeout_s=3.0,
            stop_event=self.stop_event,
        )
        launch_window = None
        if result.get("ok") and result.get("verified") is not True:
            launch_window = self._wait_launch_visible_window(command, result, win32_mod)
            if launch_window:
                hwnd = int(launch_window["hwnd"])
                focus_result = executor_mod.focus_window(hwnd)
                if focus_result.get("ok"):
                    result["verified"] = True
                    result["verify_method"] = "win32-process"
                    result["verify_detail"] = (
                        "visible launched app window detected: "
                        f"{launch_window.get('title') or launch_window.get('query')} "
                        f"(hwnd={hwnd})"
                    )
                result["target_hwnd"] = hwnd
                result["launch_window"] = launch_window
                result["focus_after_launch"] = focus_result
        if result.get("ok"):
            launched_hwnd = (result.get("post_state") or {}).get("foreground_hwnd")
            expect = args.get("expect") or {}
            want_title = str(expect.get("window_appears") or "").strip()
            title_hwnd = None
            if want_title:
                found = win32_mod.find_window(want_title)
                if found and self._window_alive(int(found.get("hwnd") or 0)):
                    title_hwnd = int(found["hwnd"])
            if launch_window:
                self._remember_target(
                    int(launch_window["hwnd"]),
                    str(launch_window.get("title") or launch_window.get("query") or command),
                )
            elif title_hwnd:
                self._remember_target(title_hwnd, want_title)
                result["target_hwnd"] = title_hwnd
            elif resolved is not None:
                self._remember_target(launched_hwnd, str(resolved.get("name") or command))
                target = self._find_target_window()
                if target:
                    self._remember_target(target)
            elif launched_hwnd:
                self._remember_target(launched_hwnd)
            target_hwnd = self.hwnd if self._window_alive(self.hwnd) else None
            if target_hwnd:
                result["target_hwnd"] = int(target_hwnd)
                try:
                    foreground = win32gui.GetForegroundWindow()
                except Exception:
                    foreground = 0
                if not self._window_related_to_target(foreground, target_hwnd):
                    focus_result = executor_mod.focus_window(target_hwnd)
                    result["focus_after_launch"] = focus_result
                    if focus_result.get("ok"):
                        try:
                            result["post_state"] = verify_mod.post_state_summary()
                        except Exception:
                            pass
                try:
                    result["foreground_matches_target"] = self._window_related_to_target(
                        win32gui.GetForegroundWindow(),
                        target_hwnd,
                    )
                except Exception:
                    result["foreground_matches_target"] = False
                if target_hwnd != prior_hwnd or result.get("target_hwnd"):
                    self._undo_stack.append(("app", target_hwnd))
            elif launched_hwnd and launched_hwnd != prior_hwnd:
                self._undo_stack.append(("app", launched_hwnd))
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
        apps_mod = _tools_attr("apps", apps)
        import shutil
        from pathlib import Path as _P

        if not command or any(ch in command for ch in "\\/|&><\""):
            return None
        if _P(command).exists() or shutil.which(command):
            return None
        base = command.split()[0] if command.isascii() else command
        if command.isascii() and (shutil.which(base) or len(command.split()) > 1):
            return None
        candidates = apps_mod.resolve(command)
        return candidates[0] if candidates else None
