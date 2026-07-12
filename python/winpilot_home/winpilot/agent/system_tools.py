"""System, launch, tray, shell, and undo tool handlers for ToolSession."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import win32gui

from ..action import executor, shell, verify
from .. import scheduler as agent_scheduler
from ..perception import apps, search, tray, web_search, win32
from ..utils.log import BUS
from .tool_result import ToolResult


def _tools_attr(name: str, default):
    """Preserve historical monkeypatch paths on winpilot.agent.tools."""
    tools_mod = sys.modules.get("winpilot.agent.tools")
    return getattr(tools_mod, name, default) if tools_mod is not None else default


def _suite_component_title_terms(value: str) -> tuple[str, tuple[str, ...]] | None:
    text = str(value or "").casefold()
    if any(term in text for term in ("wps writer", "wps文字", "wps 文字")):
        return "writer", ("文字文稿", "文字", "文档", "document", "writer", ".docx", ".doc")
    if any(term in text for term in ("wps spreadsheet", "wps表格", "wps 表格")):
        return "spreadsheet", ("工作簿", "表格", "spreadsheet", "workbook", ".xlsx", ".xls")
    if any(term in text for term in ("wps presentation", "wps演示", "wps 演示")):
        return "presentation", ("演示", "presentation", "幻灯片", ".pptx", ".ppt")
    return None


class SystemToolMixin:
    @staticmethod
    def _best_visible_app_window(app_state: dict, name: str) -> dict | None:
        """Choose a visible app window without relying on enumeration order."""
        query = re.sub(r"\s+", "", str(name or "").casefold())
        component = _suite_component_title_terms(name)
        scored: list[tuple[float, dict]] = []
        for window in app_state.get("windows") or []:
            if not isinstance(window, dict) or not window.get("visible"):
                continue
            hwnd = int(window.get("hwnd") or 0)
            if not hwnd:
                continue
            title = str(window.get("title") or "").strip()
            compact_title = re.sub(r"\s+", "", title.casefold())
            if component is not None and not any(term in title.casefold() for term in component[1]):
                continue
            score = 1.0
            if compact_title == query:
                score += 3.0
            elif query and query in compact_title:
                score += 2.0
            if title:
                score += 0.5
            scored.append((score, window))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1] if scored else None

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

    @staticmethod
    def _resolved_component_title_terms(resolved: dict | None) -> tuple[str, tuple[str, ...]] | None:
        """Return stable title evidence for host-shared desktop suite components.

        Some suites launch a component process (for example a spreadsheet
        executable) but render the document in an existing shared host process.
        Process-only discovery can then bind a splash/helper window even though
        the real foreground document is already visible.  Keep this adapter at
        the product-component boundary instead of teaching task prompts about
        individual documents or coordinates.
        """
        if not isinstance(resolved, dict):
            return None
        target = str(resolved.get("target") or "").strip().strip('"')
        stem = Path(target).stem.casefold()
        name = str(resolved.get("name") or "").casefold()
        labeled = _suite_component_title_terms(name)
        if labeled is not None:
            return labeled
        if stem == "et" or any(term in name for term in ("spreadsheet", "wps表格")):
            return "spreadsheet", ("工作簿", "表格", "spreadsheet", "workbook", ".xlsx", ".xls")
        if stem == "wpp" or any(term in name for term in ("presentation", "wps演示")):
            return "presentation", ("演示", "presentation", "幻灯片", ".pptx", ".ppt")
        if stem == "wps" or any(term in name for term in ("writer", "wps文字")):
            return "writer", ("文字", "文档", "document", "writer", ".docx", ".doc")
        return None

    def _wait_resolved_component_window(
        self,
        resolved: dict | None,
        post_state: dict | None,
        win32_mod,
        timeout_s: float = 7.0,
    ) -> dict | None:
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            found = self._resolved_component_window(resolved, post_state, win32_mod)
            if found is not None:
                return found
            if time.monotonic() >= deadline:
                return None
            if getattr(self, "stop_event", None) is not None and self.stop_event.is_set():
                return None
            time.sleep(0.25)

    def _resolved_component_window(
        self,
        resolved: dict | None,
        post_state: dict | None,
        win32_mod,
    ) -> dict | None:
        """Prefer the visible document host that matches the launched component."""
        component = self._resolved_component_title_terms(resolved)
        if component is None:
            return None
        kind, terms = component
        foreground_hwnd = int((post_state or {}).get("foreground_hwnd") or 0)
        foreground_title = str((post_state or {}).get("foreground_title") or "").strip()

        candidates: list[dict] = []
        if foreground_hwnd and self._window_alive(foreground_hwnd):
            candidates.append({
                "hwnd": foreground_hwnd,
                "title": foreground_title,
                "pid": None,
            })
        list_windows = getattr(win32_mod, "list_windows", None)
        if callable(list_windows):
            try:
                for window in list_windows() or []:
                    if not isinstance(window, dict):
                        continue
                    hwnd = int(window.get("hwnd") or 0)
                    if not hwnd or not self._window_alive(hwnd):
                        continue
                    if any(int(item.get("hwnd") or 0) == hwnd for item in candidates):
                        continue
                    candidates.append(window)
            except Exception:
                pass

        scored: list[tuple[float, dict]] = []
        for window in candidates:
            title = str(window.get("title") or "").strip()
            title_key = title.casefold()
            matches = sum(1 for term in terms if term in title_key)
            if not matches:
                continue
            score = matches * 3.0
            if int(window.get("hwnd") or 0) == foreground_hwnd:
                score += 4.0
            if title:
                score += 0.5
            scored.append((score, window))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        score, window = scored[0]
        return {
            "hwnd": int(window.get("hwnd") or 0),
            "title": str(window.get("title") or ""),
            "pid": window.get("pid"),
            "query": f"resolved-{kind}-host",
            "score": round(float(score), 3),
        }

    @staticmethod
    def _blank_document_surface_ready(
        component: tuple[str, tuple[str, ...]] | None,
        title: str,
    ) -> bool:
        """Recognize a suite-created untitled Writer surface from window state.

        This belongs at the suite adapter boundary: the controller only needs
        to know that an editable blank document already exists, not where a
        product-specific New button happens to be.
        """
        if component is None or component[0] != "writer":
            return False
        compact = re.sub(r"\s+", "", str(title or "").casefold())
        return bool(re.search(
            r"(?:(?:文字文稿|文档|document)[-_]?\d+|(?:untitled|未命名)[-_]?\d*)"
            r".*(?:wps|writer|office)",
            compact,
            re.IGNORECASE,
        ))

    def _mark_blank_document_surface(
        self,
        payload: dict,
        component: tuple[str, tuple[str, ...]] | None,
    ) -> dict:
        title = str(payload.get("title") or "")
        if component is None or component[0] != "writer":
            return payload
        if not self._blank_document_surface_ready(component, title):
            compact = re.sub(r"\s+", "", title.casefold())
            if compact in {
                "文档-wpsoffice",
                "新建文档-wpsoffice",
                "wpsoffice",
                "wps文字-wpsoffice",
            }:
                self._document_surface_ready = False
                payload["document_surface_ready"] = False
                payload["workspace_stage"] = "application_home"
                payload["fast_path_advice"] = (
                    "The Writer application home is focused, but no editable document surface "
                    "exists yet. For a create-document task, submit the complete document "
                    "execute_subgoal; the controller will create the blank document before input."
                )
            return payload
        self._new_blank_applied = True
        self._document_surface_ready = True
        payload["blank_document_ready"] = True
        payload["document_surface_ready"] = True
        payload["needs_observe"] = False
        payload["fast_path_advice"] = (
            "A blank editable document is already open and focused. Do not click New, "
            "a template, or the window title. Submit the complete document content, "
            "formatting, and exact save path in one execute_subgoal controller routine."
        )
        return payload

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
            kind=str(args.get("kind") or "any"),
            extensions=args.get("extensions") if isinstance(args.get("extensions"), list) else None,
            sort=str(args.get("sort") or "path"),
        )
        return ToolResult(self._fmt(result))

    def _t_web_search(self, args: dict) -> ToolResult:
        result = web_search.search(
            str(args.get("query") or ""),
            max_results=int(args.get("max_results") or 5),
            timeout_s=float(args.get("timeout_s") or 10.0),
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

    def _t_open_app(self, args: dict) -> ToolResult:
        """Resolve, open, focus, and bind a desktop app in one local routine."""
        name = str(args.get("name") or args.get("command") or "").strip()
        if not name:
            return ToolResult(self._fmt({"ok": False, "error": "open_app requires name"}))
        win32_mod = _tools_attr("win32", win32)
        executor_mod = _tools_attr("executor", executor)
        resolved_hint = self._resolve_launch_target(name)
        component_hint = self._resolved_component_title_terms(resolved_hint)
        initial_query = "wps" if component_hint is not None else name
        try:
            initial = win32_mod.find_app(initial_query)
        except Exception as exc:
            initial = {"running": False, "windows": [], "error": str(exc)}
        visible = self._best_visible_app_window(initial, name)
        if visible is None:
            # Process snapshots can briefly miss a just-promoted/reparented
            # desktop window.  A visible title match is sufficient to reuse the
            # existing app and prevents open_app from creating duplicate windows.
            try:
                title_match = win32_mod.find_window(name)
            except Exception:
                title_match = None
            if isinstance(title_match, dict) and self._window_alive(title_match.get("hwnd")):
                visible = dict(title_match)
                visible.setdefault("visible", True)
        if visible is not None:
            hwnd = int(visible.get("hwnd") or 0)
            focused = executor_mod.focus_window(hwnd)
            if focused.get("ok"):
                self._remember_target(hwnd, str(visible.get("title") or name))
            payload = {
                    "ok": bool(focused.get("ok")),
                    "verified": bool(focused.get("ok")),
                    "route": "existing_visible_window",
                    "app": name,
                    "target_hwnd": hwnd,
                    "title": str(visible.get("title") or ""),
                    "focus": focused,
                    "pre_state": {
                        "running": bool(initial.get("running")),
                        "process_count": int(initial.get("process_count") or 0),
                        "visible_window_count": sum(
                            1 for item in (initial.get("windows") or [])
                            if isinstance(item, dict) and item.get("visible")
                        ),
                    },
                    "needs_observe": True,
                    "fast_path_advice": (
                        "The app is open and focused with target_hwnd established. "
                        "Observe it once, then execute the next GUI subgoal; do not launch it again."
                    ),
                }
            self._mark_blank_document_surface(payload, component_hint)
            return ToolResult(
                self._fmt(payload),
                replay={"tool": "open_app", "name": name},
            )

        launch_result = self._t_launch({"command": name})
        try:
            launched = json.loads(launch_result.text or "{}")
        except (json.JSONDecodeError, TypeError):
            launched = {"ok": False, "error": launch_result.text or "launch failed"}
        target_hwnd = int(launched.get("target_hwnd") or 0)
        verified = bool(launched.get("verified") and target_hwnd)
        route = "reopen_background_app" if initial.get("running") else "resolve_and_launch"
        payload = {
            "ok": bool(launched.get("ok")) and bool(target_hwnd),
            "verified": verified,
            "route": route,
            "app": name,
            "target_hwnd": target_hwnd or None,
            "title": str((launched.get("launch_window") or {}).get("title") or self.target_hint or ""),
            "pre_state": {
                "running": bool(initial.get("running")),
                "process_count": int(initial.get("process_count") or 0),
                "visible_window_count": 0,
            },
            "launch": launched,
            "needs_observe": bool(target_hwnd),
        }
        if target_hwnd:
            payload["fast_path_advice"] = (
                "Background/no-window app recovery succeeded and target_hwnd is established. "
                "Observe it once and continue the GUI task; do not inspect processes or paths with shell."
            )
        else:
            payload["error"] = str(launched.get("error") or "app launched but no visible target window appeared")
            payload["needs_llm_reason"] = True
            payload["fast_path_advice"] = (
                "No visible app window appeared after local resolve+launch. Use scan_windows once "
                "or report the actual blocker; do not repeat process/path shell probes."
            )
        self._mark_blank_document_surface(payload, component_hint)
        return ToolResult(
            self._fmt(payload),
            replay={"tool": "open_app", "name": name},
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
        component_requested = self._resolved_component_title_terms(resolved)
        if result.get("ok") and component_requested is not None:
            # Generic process/screen-change verification is insufficient for
            # a shared office suite. Require a window matching the requested
            # Writer/Spreadsheet/Presentation component.
            result["verified"] = False
        launch_window = None
        if result.get("ok") and result.get("verified") is not True:
            if component_requested is not None:
                launch_window = self._wait_resolved_component_window(
                    resolved,
                    result.get("post_state"),
                    win32_mod,
                )
            else:
                launch_window = self._wait_launch_visible_window(command, result, win32_mod)
                if launch_window is None:
                    launch_window = self._wait_url_launch_visible_window(
                        command,
                        result,
                        win32_mod,
                        prior_hwnd,
                    )
            if launch_window:
                hwnd = int(launch_window["hwnd"])
                focus_result = executor_mod.focus_window(hwnd)
                if focus_result.get("ok"):
                    result["verified"] = True
                    result["verify_method"] = (
                        "win32-url-window"
                        if launch_window.get("query") == "url"
                        else (
                            "win32-component-window"
                            if str(launch_window.get("query") or "").startswith("resolved-")
                            else "win32-process"
                        )
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
            elif resolved is not None and component_requested is None:
                self.target_hint = str(resolved.get("name") or command)
                target = self._find_target_window()
                if target:
                    self._remember_target(target)
                elif launched_hwnd and launched_hwnd != prior_hwnd:
                    self._remember_target(
                        launched_hwnd,
                        str(resolved.get("name") or command),
                    )
            elif component_requested is None and launched_hwnd and launched_hwnd != prior_hwnd:
                self._remember_target(launched_hwnd)
            target_hwnd = self.hwnd if self._window_alive(self.hwnd) else None
            target_belongs_to_launch = bool(
                launch_window
                or title_hwnd
                or (target_hwnd and target_hwnd != prior_hwnd)
            )
            if target_hwnd and target_belongs_to_launch:
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
            else:
                # ShellExecute can report ok while doing nothing. The previous
                # foreground hwnd is not evidence that the requested app opened.
                result.pop("target_hwnd", None)
                result["foreground_matches_target"] = False
        if resolved is not None:
            result["resolved_app"] = f'{resolved["name"]} [{resolved["kind"]}]'
            if self.hwnd and (component_requested is None or launch_window is not None):
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
        # Resolve registered display names before treating whitespace as a
        # possible shell command. Real applications frequently have multiword
        # English names ("WPS Spreadsheets", "Visual Studio Code").
        candidates = apps_mod.resolve(command)
        if candidates:
            return candidates[0]
        base = command.split()[0] if command.isascii() else command
        if command.isascii() and (shutil.which(base) or len(command.split()) > 1):
            return None
        return None
