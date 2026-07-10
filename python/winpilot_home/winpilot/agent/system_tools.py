"""System, launch, tray, shell, and undo tool handlers for ToolSession."""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import win32gui

from ..action import executor, shell, verify
from .. import scheduler as agent_scheduler
from ..perception import apps, search, tray, win32
from ..utils.log import BUS
from .tool_result import ToolResult


def _tools_attr(name: str, default):
    """Preserve historical monkeypatch paths on winpilot.agent.tools."""
    tools_mod = sys.modules.get("winpilot.agent.tools")
    return getattr(tools_mod, name, default) if tools_mod is not None else default


class SystemToolMixin:
    @staticmethod
    def _url_launch_terms(command: str, launch_result: dict) -> list[str]:
        target = str(launch_result.get("target") or command or "").strip()
        if "://" not in target:
            return []
        parsed = urlparse(target)
        parts: list[str] = []
        for chunk in (parsed.netloc, parsed.path):
            parts.extend(re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", chunk))
        terms: list[str] = []
        for part in parts:
            value = part.strip().casefold()
            if len(value) >= 3 and value not in {"http", "https", "html", "htm", "www", "com", "localhost"}:
                terms.append(value)
        return list(dict.fromkeys(terms))

    @staticmethod
    def _browser_window_score(window: dict, terms: list[str], prior_hwnd: int | None) -> float:
        hwnd = int(window.get("hwnd") or 0)
        title = str(window.get("title") or "")
        class_name = str(window.get("class_name") or "")
        title_key = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", title.casefold())
        score = 0.0
        if hwnd and prior_hwnd and hwnd == int(prior_hwnd):
            score -= 6.0
        if class_name in {"Chrome_WidgetWin_1", "MozillaWindowClass", "ApplicationFrameWindow"}:
            score += 1.5
        if any(browser in title.casefold() for browser in ("chrome", "edge", "firefox", "browser")):
            score += 0.8
        if title.strip().casefold() == "codex":
            score -= 5.0
        matched_terms = 0
        for term in terms:
            if term and term in title_key:
                matched_terms += 1
        score += matched_terms * 1.2
        if matched_terms >= 2:
            score += 2.0
        return score

    def _url_launch_visible_window(
        self,
        command: str,
        launch_result: dict,
        win32_mod,
        prior_hwnd: int | None,
    ) -> dict | None:
        terms = self._url_launch_terms(command, launch_result)
        if not terms:
            return None
        list_windows = getattr(win32_mod, "list_windows", None)
        if not callable(list_windows):
            return None
        try:
            windows = list_windows()
        except Exception:
            return None
        scored: list[tuple[float, dict]] = []
        for window in windows or []:
            hwnd = int(window.get("hwnd") or 0)
            if not hwnd or not self._window_alive(hwnd):
                continue
            score = self._browser_window_score(window, terms, prior_hwnd)
            if score > 0.5:
                scored.append((score, window))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return None
        score, window = scored[0]
        return {
            "hwnd": int(window.get("hwnd") or 0),
            "title": str(window.get("title") or ""),
            "pid": window.get("pid"),
            "query": "url",
            "score": round(float(score), 3),
            "terms": terms,
        }

    def _wait_url_launch_visible_window(
        self,
        command: str,
        launch_result: dict,
        win32_mod,
        prior_hwnd: int | None,
        timeout_s: float = 7.0,
    ) -> dict | None:
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            found = self._url_launch_visible_window(command, launch_result, win32_mod, prior_hwnd)
            if found:
                return found
            if time.monotonic() >= deadline:
                return None
            if getattr(self, "stop_event", None) is not None and self.stop_event.is_set():
                return None
            time.sleep(0.35)

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
        command = str(args["command"])
        shell_kind = str(args.get("shell", "powershell"))
        timeout_s = float(args.get("timeout_s", 30))
        coding_runtime = getattr(self, "coding_runtime", None)
        if coding_runtime is not None:
            result = coding_runtime.dispatch("run_shell", args)
            replay = {
                "tool": "run_shell",
                "command": command,
                "shell": shell_kind,
                "timeout_s": timeout_s,
            }
            if args.get("cwd"):
                replay["cwd"] = str(args.get("cwd"))
            return ToolResult(result.text, summary=result.summary, replay=replay)
        shell_mod = _tools_attr("shell", shell)
        cwd = str(args.get("cwd") or "") or None
        result = shell_mod.run(command, shell=shell_kind, timeout_s=timeout_s, cwd=cwd)
        replay = {"tool": "run_shell", "command": command, "shell": shell_kind,
                  "timeout_s": timeout_s}
        if cwd:
            replay["cwd"] = cwd
        return ToolResult(
            self._fmt(result),
            replay=replay,
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

    def _t_schedule_task(self, args: dict) -> ToolResult:
        try:
            entry = agent_scheduler.create_task(
                str(args.get("task") or ""),
                name=str(args.get("name") or ""),
                run_at=str(args.get("run_at") or ""),
                delay_seconds=args.get("delay_seconds"),
                interval_seconds=args.get("interval_seconds"),
                enabled=bool(args.get("enabled", True)),
                max_steps=int(args.get("max_steps")) if args.get("max_steps") else None,
            )
        except Exception as exc:
            return ToolResult(f"schedule_task failed: {exc}")
        return ToolResult(self._fmt({"ok": True, "schedule": entry}))

    def _t_list_scheduled_tasks(self, args: dict) -> ToolResult:
        items = agent_scheduler.list_tasks(enabled_only=bool(args.get("enabled_only", False)))
        return ToolResult(self._fmt({"ok": True, "schedules": items, "count": len(items)}))

    def _t_cancel_scheduled_task(self, args: dict) -> ToolResult:
        schedule_id = str(args.get("id") or "")
        ok = agent_scheduler.cancel_task(schedule_id)
        return ToolResult(self._fmt({"ok": ok, "id": schedule_id}))

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
            run_id=getattr(self, "run_id", None),
        )
        launch_window = None
        if result.get("ok") and result.get("verified") is not True:
            launch_window = self._wait_launch_visible_window(command, result, win32_mod)
            if launch_window is None:
                launch_window = self._wait_url_launch_visible_window(command, result, win32_mod, prior_hwnd)
            if launch_window:
                hwnd = int(launch_window["hwnd"])
                focus_result = executor_mod.focus_window(hwnd)
                if focus_result.get("ok"):
                    result["verified"] = True
                    result["verify_method"] = (
                        "win32-url-window"
                        if launch_window.get("query") == "url"
                        else "win32-process"
                    )
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
            want_title = str(expect.get("window_appears") or expect.get("window_title") or "").strip()
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
                if result.get("verified") is not True:
                    result["verified"] = True
                    result["verify_method"] = "win32-title"
                    result["verify_detail"] = (
                        f'visible window title matched "{want_title}" '
                        f"(hwnd={title_hwnd})"
                    )
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
