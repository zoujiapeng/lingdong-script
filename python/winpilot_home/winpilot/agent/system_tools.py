"""System, launch, tray, shell, and undo tool handlers for ToolSession."""
from __future__ import annotations

import json
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import win32con
import win32gui

from ..action import executor, shell, verify
from ..config import CONFIG
from ..perception import apps, search, tray, web_search, win32
from ..utils.log import BUS
from .interaction_tools import _call_with_optional_hwnd
from .tool_result import ToolResult


def _tools_attr(name: str, default):
    """Preserve historical monkeypatch paths on winpilot.agent.tools."""
    tools_mod = sys.modules.get("winpilot.agent.tools")
    return getattr(tools_mod, name, default) if tools_mod is not None else default


class SystemToolMixin:
    @staticmethod
    def _normalized_application_term(value: object) -> str:
        return re.sub(
            r"[^0-9a-z\u4e00-\u9fff]+",
            "",
            str(value or "").casefold(),
        )

    def _remember_application_identity(
        self,
        requested_name: str,
        resolved: dict | None,
        hwnd: int,
    ) -> None:
        terms = [requested_name]
        if isinstance(resolved, dict):
            terms.extend([
                str(resolved.get("name") or ""),
                *(str(item) for item in (resolved.get("aliases") or [])),
            ])
        normalized = list(dict.fromkeys(
            term
            for term in (
                self._normalized_application_term(item) for item in terms
            )
            if term
        ))
        self._target_application_identity = {
            "hwnd": int(hwnd),
            "terms": normalized,
            "name": str(
                (resolved or {}).get("name") if isinstance(resolved, dict) else ""
            ) or requested_name,
            "kind": str((resolved or {}).get("kind") or ""),
            "target": str((resolved or {}).get("target") or ""),
        }

    def _bound_application_matches(self, requested_name: str) -> bool:
        identity = getattr(self, "_target_application_identity", None)
        if not isinstance(identity, dict):
            return False
        try:
            same_hwnd = int(identity.get("hwnd") or 0) == int(self.hwnd or 0)
        except (TypeError, ValueError):
            same_hwnd = False
        query = self._normalized_application_term(requested_name)
        return bool(
            query
            and same_hwnd
            and query in set(identity.get("terms") or [])
            and getattr(self, "_target_binding_verified", False)
            and self._window_alive(self.hwnd)
        )

    @staticmethod
    def _best_visible_app_window(app_state: dict, name: str) -> dict | None:
        """Choose a visible app window without relying on enumeration order."""
        query = re.sub(r"\s+", "", str(name or "").casefold())
        scored: list[tuple[float, dict]] = []
        for window in app_state.get("windows") or []:
            if not isinstance(window, dict) or not window.get("visible"):
                continue
            hwnd = int(window.get("hwnd") or 0)
            if not hwnd:
                continue
            title = str(window.get("title") or "").strip()
            compact_title = re.sub(r"\s+", "", title.casefold())
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

    def _credible_hidden_app_windows(self, app_state: dict, name: str) -> list[dict]:
        """Rank restorable top-level windows already owned by a running app."""

        from ..perception.app_aliases import expand as expand_app_aliases

        aliases = [
            re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", item.casefold())
            for item in expand_app_aliases(name)
            if str(item).strip()
        ]
        candidates: list[tuple[float, dict]] = []
        for raw in app_state.get("windows") or []:
            if not isinstance(raw, dict) or raw.get("visible"):
                continue
            hwnd = int(raw.get("hwnd") or 0)
            try:
                live_window = bool(hwnd and win32gui.IsWindow(hwnd))
            except Exception:
                live_window = bool(hwnd)
            if not live_window:
                continue
            title = str(raw.get("title") or "").strip()
            try:
                title = win32gui.GetWindowText(hwnd).strip() or title
            except Exception:
                pass
            try:
                class_name = str(win32gui.GetClassName(hwnd) or "")
            except Exception:
                class_name = ""
            folded_system_surface = f"{title} {class_name}".casefold()
            if any(marker in folded_system_surface for marker in (
                "default ime", "msctfime", "input method", "tooltips_class32",
            )):
                continue
            try:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                width, height = max(0, right - left), max(0, bottom - top)
            except Exception:
                left = top = right = bottom = 0
                width = height = 0
            # Tiny hidden Chromium/helper surfaces are not credible main app
            # windows.  Retain geometry-less test/provider records so the
            # caller can still attempt a verified restoration.
            if width and height and (width < 160 or height < 100):
                continue
            compact = re.sub(
                r"[^0-9a-z\u4e00-\u9fff]+", "", f"{title} {class_name}".casefold()
            )
            alias_match = max(
                (len(alias) for alias in aliases if len(alias) >= 2 and alias in compact),
                default=0,
            )
            if aliases and not alias_match and title:
                continue
            area = width * height
            score = float(alias_match * 20)
            if title:
                score += 15.0
            if area:
                score += min(50.0, area / 50_000.0)
            try:
                if not win32gui.GetWindow(hwnd, win32con.GW_OWNER):
                    score += 8.0
            except Exception:
                pass
            candidates.append((score, {
                **raw,
                "hwnd": hwnd,
                "title": title,
                "class_name": class_name,
                "rect": [left, top, right, bottom],
                "visible": False,
                "recovery_score": round(score, 3),
            }))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return [item for _score, item in candidates]

    def _restore_running_background_app(
        self,
        app_state: dict,
        name: str,
        win32_mod,
        executor_mod,
    ) -> dict:
        """Restore an existing hidden/tray instance before any new launch."""

        candidates = self._credible_hidden_app_windows(app_state, name)
        attempts: list[dict] = []
        for candidate in candidates[:5]:
            hwnd = int(candidate.get("hwnd") or 0)
            attempt = {
                "route": "hidden_window",
                "hwnd": hwnd,
                "title": str(candidate.get("title") or ""),
                "recovery_score": candidate.get("recovery_score"),
            }
            try:
                show_mode = (
                    win32con.SW_RESTORE
                    if win32gui.IsIconic(hwnd)
                    else win32con.SW_SHOW
                )
                win32gui.ShowWindow(hwnd, show_mode)
                time.sleep(0.2)
                focused = executor_mod.focus_window(hwnd)
                attempt["focus"] = focused
                visible = bool(win32gui.IsWindowVisible(hwnd))
                attempt["visible_after_restore"] = visible
                if focused.get("ok") is True and visible and self._window_alive(hwnd):
                    title = str(candidate.get("title") or name)
                    self._remember_target(hwnd, title)
                    attempts.append(attempt)
                    return {
                        "ok": True,
                        "verified": True,
                        "route": "existing_hidden_window",
                        "target_hwnd": hwnd,
                        "title": title,
                        "attempts": attempts,
                        "credible_hidden_window_count": len(candidates),
                    }
            except Exception as exc:
                attempt["error"] = str(exc)
            attempts.append(attempt)

        tray_mod = _tools_attr("tray", tray)
        tray_result: dict = {}
        try:
            tray_result = tray_mod.click_tray_icon(name, double=True)
        except Exception as exc:
            tray_result = {"ok": False, "error": str(exc)}
        if tray_result.get("ok") is True:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    refreshed = win32_mod.find_app(name)
                except Exception:
                    refreshed = {}
                visible = self._best_visible_app_window(refreshed, name)
                if visible is None:
                    try:
                        visible = win32_mod.find_window(name)
                    except Exception:
                        visible = None
                if isinstance(visible, dict):
                    hwnd = int(visible.get("hwnd") or 0)
                    if hwnd and self._window_alive(hwnd):
                        focused = executor_mod.focus_window(hwnd)
                        if focused.get("ok") is True:
                            title = str(visible.get("title") or name)
                            self._remember_target(hwnd, title)
                            return {
                                "ok": True,
                                "verified": True,
                                "route": "existing_tray_instance",
                                "target_hwnd": hwnd,
                                "title": title,
                                "attempts": attempts,
                                "tray": tray_result,
                                "credible_hidden_window_count": len(candidates),
                            }
                time.sleep(0.25)
        return {
            "ok": False,
            "verified": False,
            "route": "background_recovery_failed",
            "attempts": attempts,
            "tray": tray_result,
            "credible_hidden_window_count": len(candidates),
            "duplicate_launch_unsafe": bool(candidates),
        }

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
        # Do not spend a second launch-verification timeout treating a local
        # executable, shortcut, or AppID as if it were a web destination.
        if not self._url_launch_terms(command, launch_result):
            return None
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
        process_names = launch_result.get("process_names") or []
        if not isinstance(process_names, (list, tuple, set)):
            process_names = [process_names]
        candidates = [
            *(
                (str(process_name), True)
                for process_name in (
                    [launch_result.get("process_name")]
                    + list(process_names)
                )
                if str(process_name or "").strip()
            ),
            *(
                (str(alias), True)
                for alias in (launch_result.get("aliases") or [])
                if str(alias).strip()
            ),
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

    @staticmethod
    def _visible_window_snapshot(win32_mod) -> dict[int, dict]:
        list_windows = getattr(win32_mod, "list_windows", None)
        if not callable(list_windows):
            return {}
        try:
            windows = list_windows() or []
        except Exception:
            return {}
        return {
            int(window.get("hwnd") or 0): dict(window)
            for window in windows
            if isinstance(window, dict) and int(window.get("hwnd") or 0)
        }

    def _launch_visible_window(
        self,
        command: str,
        launch_result: dict,
        win32_mod,
        *,
        before_windows: dict[int, dict] | None = None,
        prelaunch_foreground_hwnd: int = 0,
        prior_target_hwnd: int = 0,
    ) -> dict | None:
        before_windows = before_windows or {}
        excluded = {
            int(prelaunch_foreground_hwnd or 0),
            int(prior_target_hwnd or 0),
        }
        query = self._launch_process_query(command, launch_result)
        if query:
            try:
                app_state = win32_mod.find_app(query)
            except Exception:
                app_state = {}
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

        # Store/AppID launches often expose a localized display name but run
        # under a package host whose executable does not resemble that name.
        # In that case a visible title match is identity evidence; treating the
        # display name as a process basename is not.  This is deliberately
        # generic and uses only resolver-returned identity, never an app rule.
        title_queries: list[str] = []
        for raw in (
            launch_result.get("name"),
            *(launch_result.get("aliases") or []),
            command,
        ):
            value = str(raw or "").strip().strip('"')
            folded = value.casefold()
            if (
                not value
                or "://" in folded
                or folded.startswith(("shell:", "steam:", "ms-"))
                or "\\" in value
                or "/" in value
                or "!" in value
            ):
                continue
            if value not in title_queries:
                title_queries.append(value)
        for title_query in title_queries:
            try:
                window = win32_mod.find_window(title_query)
            except Exception:
                window = None
            if not isinstance(window, dict):
                continue
            hwnd = int(window.get("hwnd") or 0)
            if (
                hwnd
                and hwnd not in before_windows
                and hwnd not in excluded
                and self._window_alive(hwnd)
            ):
                return {
                    "hwnd": hwnd,
                    "title": str(window.get("title") or ""),
                    "pid": window.get("pid"),
                    "query": f"title:{title_query}",
                }

        # A launcher can legitimately create a window whose title and process
        # do not resemble its Start-menu/AppID identity.  A unique top-level
        # window created after this launch is causal evidence; an already-open
        # foreground/controller window is not.  Ambiguous deltas remain
        # unbound so the semantic loop can inspect them visually.
        after_windows = self._visible_window_snapshot(win32_mod)
        new_windows = [
            window
            for hwnd, window in after_windows.items()
            if hwnd not in before_windows
            and hwnd not in excluded
            and self._window_alive(hwnd)
        ]
        try:
            foreground = int(win32gui.GetForegroundWindow() or 0)
        except Exception:
            foreground = 0
        foreground_candidates = [
            window
            for window in new_windows
            if int(window.get("hwnd") or 0) == foreground
        ]
        candidate = (
            foreground_candidates[0]
            if len(foreground_candidates) == 1
            else None
        )
        if candidate is not None:
            return {
                **candidate,
                "hwnd": int(candidate.get("hwnd") or 0),
                "title": str(candidate.get("title") or ""),
                "query": "launch-window-delta",
                "causal_new_window": True,
                "ownership_evidence": "new_foreground_window_after_launch",
            }
        return None

    def _wait_launch_visible_window(
        self,
        command: str,
        launch_result: dict,
        win32_mod,
        *,
        before_windows: dict[int, dict] | None = None,
        prelaunch_foreground_hwnd: int = 0,
        prior_target_hwnd: int = 0,
        timeout_s: float = 7.0,
    ) -> dict | None:
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            found = self._launch_visible_window(
                command,
                launch_result,
                win32_mod,
                before_windows=before_windows,
                prelaunch_foreground_hwnd=prelaunch_foreground_hwnd,
                prior_target_hwnd=prior_target_hwnd,
            )
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
        return ToolResult(
            "schedule_task was removed with the legacy agent-schedules feature; "
            "submit the goal through the Agent panel instead"
        )

    def _t_list_scheduled_tasks(self, args: dict) -> ToolResult:
        return ToolResult(
            self._fmt({"ok": True, "schedules": [], "count": 0, "removed": True})
        )

    def _t_cancel_scheduled_task(self, args: dict) -> ToolResult:
        return ToolResult(
            self._fmt({"ok": False, "id": str(args.get("id") or ""), "removed": True})
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
        # Never guess that Ctrl+Z belongs to this run. The foreground document
        # may contain user-authored history that predates the agent. Recovery is
        # safe only for a concrete mutation token recorded above.
        return ToolResult(self._fmt({
            "ok": False,
            "undone": False,
            "blocked": True,
            "error": "No reversible mutation token from this run is available.",
        }))

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
        apps_mod = _tools_attr("apps", apps)
        executor_mod = _tools_attr("executor", executor)
        if self._bound_application_matches(name):
            hwnd = int(self.hwnd)
            focused = executor_mod.focus_window(hwnd)
            if focused.get("ok"):
                self._remember_target(hwnd, name)
            try:
                title_reader = getattr(win32_mod, "_window_text", win32._window_text)
                title = str(title_reader(hwnd) or "")
            except Exception:
                title = ""
            return ToolResult(
                self._fmt({
                    "ok": bool(focused.get("ok")),
                    "verified": bool(focused.get("ok")),
                    "route": "existing_bound_application",
                    "app": name,
                    "target_hwnd": hwnd,
                    "title": title,
                    "focus": focused,
                    "binding_preserved": True,
                    "needs_observe": True,
                    **({
                        "error": "The proven application window is still live but could not be focused."
                    } if not focused.get("ok") else {}),
                    "fast_path_advice": (
                        "The already proven application instance was reused by stable launch identity. "
                        "Continue from the newest authoritative frame; do not launch a duplicate."
                    ),
                }),
                replay={"tool": "open_app", "name": name},
            )
        resolved_hint = self._resolve_launch_target(name)
        direct_launchable = bool(
            Path(name).exists()
            or shutil.which(name)
            or re.match(r"^(?:https?://|[A-Za-z][A-Za-z0-9+.-]*:)", name)
        )
        initial_query = self._launch_process_query(name, resolved_hint or {}) or name
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
                self._remember_target(hwnd, name)
                self._remember_application_identity(name, resolved_hint, hwnd)
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
                        "Continue from the newest attached frame or structured observation; "
                        "observe once only when no fresh state was supplied. Do not launch it again."
                    ),
                }
            return ToolResult(
                self._fmt(payload),
                replay={"tool": "open_app", "name": name},
            )

        background_recovery: dict = {}
        if initial.get("running"):
            background_recovery = self._restore_running_background_app(
                initial,
                name,
                win32_mod,
                executor_mod,
            )
            if background_recovery.get("ok") is True:
                recovered_hwnd = int(background_recovery.get("target_hwnd") or 0)
                if recovered_hwnd:
                    self._remember_target(recovered_hwnd, name)
                    self._remember_application_identity(
                        name, resolved_hint, recovered_hwnd,
                    )
                payload = {
                    **background_recovery,
                    "app": name,
                    "pre_state": {
                        "running": True,
                        "process_count": int(initial.get("process_count") or 0),
                        "visible_window_count": 0,
                    },
                    "needs_observe": True,
                    "fast_path_advice": (
                        "A pre-existing background/tray instance was restored and bound. "
                        "Continue from its newest frame; do not launch another instance."
                    ),
                }
                return ToolResult(
                    self._fmt(payload),
                    replay={"tool": "open_app", "name": name},
                )
            if background_recovery.get("duplicate_launch_unsafe") is True:
                return ToolResult(self._fmt({
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "failure_type": "background_instance_restore_failed",
                    "app": name,
                    "pre_state": {
                        "running": True,
                        "process_count": int(initial.get("process_count") or 0),
                        "visible_window_count": 0,
                    },
                    "background_recovery": background_recovery,
                    "error": (
                        "The application is already running and owns credible hidden windows, "
                        "but none could be restored or activated. A new launch was refused because "
                        "it could create an uninitialized duplicate instance."
                    ),
                    "needs_llm_reason": True,
                    "fast_path_advice": (
                        "Use the returned hidden-window/tray evidence or report the blocker; "
                        "do not launch another copy of the already-running application."
                    ),
                }))

        if resolved_hint is None and not direct_launchable:
            try:
                ranked = apps_mod.resolve(name, max_results=5) or []
            except Exception:
                ranked = []
            ranked_candidates = [
                {
                    "display_name": str(item.get("name") or ""),
                    "launch_kind": str(item.get("kind") or ""),
                    "launch_target": str(item.get("target") or ""),
                    "aliases": [
                        str(alias) for alias in (item.get("aliases") or [])
                        if str(alias).strip()
                    ],
                    "match_score": item.get("match_score"),
                    "match_kind": str(item.get("match_kind") or ""),
                    "matched_term": str(item.get("matched_term") or ""),
                }
                for item in ranked
                if isinstance(item, dict)
            ]
            return ToolResult(self._fmt({
                "ok": False,
                "verified": False,
                "failure_type": (
                    "application_resolution_ambiguous"
                    if ranked_candidates
                    else "application_not_found"
                ),
                "error": (
                    f'No unambiguous installed application could be resolved for "{name}". '
                    "The local resolver did not execute a guessed target."
                ),
                "app": name,
                "ranked_candidates": ranked_candidates,
                "resolution_sources": [
                    "running_processes",
                    "visible_windows",
                    "start_menu",
                    "start_apps",
                    "app_paths",
                    "executable_index",
                ],
            }))

        launch_result = self._t_launch({"command": name, "_reuse_checked": True})
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
            "background_recovery": background_recovery or None,
            "needs_observe": bool(target_hwnd),
        }
        if target_hwnd:
            self._remember_target(target_hwnd, name)
            self._remember_application_identity(name, resolved_hint, target_hwnd)
            payload["fast_path_advice"] = (
                "Background/no-window app recovery succeeded and target_hwnd is established. "
                "Continue from the newest attached frame or structured observation; observe once "
                "only when no fresh state was supplied. Do not inspect processes or paths with shell."
            )
        else:
            payload["error"] = str(launched.get("error") or "app launched but no visible target window appeared")
            payload["needs_llm_reason"] = True
            if launched.get("ok") is True:
                payload.update({
                    "failure_type": "launch_target_pending",
                    "retryable": True,
                    "needs_observe": True,
                })
                payload["error"] = (
                    "The launch request succeeded, but no visible window has yet been "
                    "proven to belong to the requested application. Observe/list windows "
                    "once and bind only identity-matching evidence; do not reuse the "
                    "previous target window."
                )
            payload["fast_path_advice"] = (
                "No proven visible app window appeared after local resolve+launch. Observe "
                "or use one exposed window-list/focus recovery tool, then continue if the "
                "requested window becomes identifiable; do not repeat the launch or probe "
                "process paths with shell."
            )
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
        explicit_target = bool(
            Path(command).exists()
            or re.match(r"^(?:https?://|www\.|[A-Za-z][A-Za-z0-9+.-]*:)", command)
        )
        if not bool(args.get("_reuse_checked")) and not explicit_target:
            # ``open_app`` and ``launch`` are two model-facing spellings of the
            # same app operation.  Bare app identities always share the same
            # reuse-first primitive so choosing either tool cannot create a
            # duplicate logged-out instance.  URLs, protocols and explicit
            # executable paths remain direct launch targets.
            return self._t_open_app({"name": command})
        resolved = self._resolve_launch_target(command)
        prior_hwnd = self.hwnd
        before_windows = self._visible_window_snapshot(win32_mod)
        try:
            prelaunch_foreground_hwnd = int(win32gui.GetForegroundWindow() or 0)
        except Exception:
            prelaunch_foreground_hwnd = 0
        cooperative = bool(CONFIG.get(
            "controller", "multi_input_cooperation", default=False,
        ))

        def do_launch() -> dict:
            nonlocal resolved
            if resolved is not None:
                attempted_targets: set[str] = set()
                attempts: list[dict] = []

                def attempt(candidate: dict) -> dict:
                    target = str(candidate.get("target") or "")
                    attempted_targets.add(target.casefold())
                    outcome = apps_mod.launch_target(candidate)
                    attempts.append({
                        "name": str(candidate.get("name") or ""),
                        "kind": str(candidate.get("kind") or ""),
                        "target": target,
                        "ok": bool(outcome.get("ok")),
                        "error": str(outcome.get("error") or ""),
                    })
                    return outcome

                launched = attempt(resolved)
                if launched.get("ok") is not True:
                    # A synchronous failure means no launch request was
                    # accepted, so one refreshed, distinct resolver target is
                    # safe.  Never retry after ok=true merely because its
                    # window is still pending.
                    try:
                        refreshed = apps_mod.resolve(
                            command,
                            max_results=5,
                            force_refresh=True,
                        ) or []
                    except Exception:
                        refreshed = []
                    fallback = next((
                        item for item in refreshed
                        if isinstance(item, dict)
                        and str(item.get("target") or "").casefold()
                        not in attempted_targets
                    ), None)
                    if fallback is not None:
                        resolved = fallback
                        launched = attempt(fallback)
                launched = dict(launched)
                launched["launch_attempts"] = attempts
                return launched
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
        if result.get("ok"):
            # Generic launch verification observes global foreground/process
            # changes; it cannot prove they belong to this resolved identity.
            # Preserve it only as request evidence and require an owned window
            # below before returning verified=true.
            result["launch_request_verified"] = bool(result.get("verified"))
            result["verified"] = False
            result.pop("target_hwnd", None)
        launch_window = None
        if result.get("ok") and result.get("verified") is not True:
            launch_window = self._wait_launch_visible_window(
                command,
                result,
                win32_mod,
                before_windows=before_windows,
                prelaunch_foreground_hwnd=prelaunch_foreground_hwnd,
                prior_target_hwnd=int(prior_hwnd or 0),
            )
            if launch_window is None and self._url_launch_terms(command, result):
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
                    launch_query = str(launch_window.get("query") or "")
                    result["verify_method"] = (
                        "win32-url-window"
                        if launch_query == "url"
                        else (
                            "win32-title"
                            if launch_query.startswith("title:")
                            else (
                                "win32-window-delta"
                                if launch_query == "launch-window-delta"
                                else (
                                "win32-process"
                                )
                            )
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
            expect = args.get("expect") or {}
            want_title = str(expect.get("window_appears") or expect.get("window_title") or "").strip()
            title_hwnd = None
            if want_title:
                found = win32_mod.find_window(want_title)
                candidate_hwnd = int((found or {}).get("hwnd") or 0)
                if (
                    candidate_hwnd
                    and candidate_hwnd not in before_windows
                    and candidate_hwnd not in {
                        int(prelaunch_foreground_hwnd or 0),
                        int(prior_hwnd or 0),
                    }
                    and self._window_alive(candidate_hwnd)
                ):
                    title_hwnd = candidate_hwnd
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
                self.target_hint = str(resolved.get("name") or command)
                if not cooperative:
                    target = self._find_target_window()
                    if (
                        target
                        and int(target) not in before_windows
                        and int(target) not in {
                            int(prelaunch_foreground_hwnd or 0),
                            int(prior_hwnd or 0),
                        }
                    ):
                        self._remember_target(target)
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
                try:
                    result["post_state"] = verify_mod.post_state_summary()
                except Exception:
                    pass
                if target_hwnd != prior_hwnd or result.get("target_hwnd"):
                    self._undo_stack.append(("app", target_hwnd))
            else:
                # ShellExecute can report ok while doing nothing. The previous
                # foreground hwnd is not evidence that the requested app opened.
                result.pop("target_hwnd", None)
                result["foreground_matches_target"] = False
        if result.get("ok") and not int(result.get("target_hwnd") or 0):
            result["verified"] = False
            result["foreground_matches_target"] = False
        if resolved is not None:
            result["resolved_app"] = f'{resolved["name"]} [{resolved["kind"]}]'
            if not int(result.get("target_hwnd") or 0):
                # Never launder the previously bound window into evidence for
                # a newly requested application.  Leave the session unbound so
                # the semantic loop observes/rebinds instead of clicking the
                # stale controller or terminal window.
                self.hwnd = None
                self._target_binding_verified = False
                self._target_process_id = 0
                self._target_process_name = ""
                self._target_window_class = ""
                self._target_application_identity = {}
                self.target_hint = str(resolved.get("name") or command)
                result["verified"] = False
                result["foreground_matches_target"] = False
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
        # Localized display names and executable aliases can differ.
        candidates = apps_mod.resolve(command)
        if candidates:
            first = candidates[0]
            if str(first.get("match_kind") or "") == "fuzzy" and len(candidates) > 1:
                first_score = int(first.get("match_score") or 0)
                second_score = int(candidates[1].get("match_score") or 0)
                # A typo correction is safe only when one installed target is
                # materially better than the alternatives.  Ambiguous fuzzy
                # matches must not launch an arbitrary application.
                same_display_name = (
                    str(first.get("name") or "").strip().casefold()
                    == str(candidates[1].get("name") or "").strip().casefold()
                )
                if first_score - second_score < 6 and not same_display_name:
                    return None
            return first
        base = command.split()[0] if command.isascii() else command
        if command.isascii() and (shutil.which(base) or len(command.split()) > 1):
            return None
        return None
