"""Window listing, scanning, and focus tool handlers for ToolSession."""
from __future__ import annotations

import time

import win32con
import win32gui

from ..action import executor
from ..perception import apps, fusion, win32
from .tool_result import ToolResult


class WindowToolMixin:
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
            matches = self._hidden_window_matches(app_state, query)
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

    @staticmethod
    def _hidden_window_matches(app_state: dict, query: str) -> list[dict]:
        matches: list[dict] = []
        for item in app_state.get("windows", []):
            hwnd = int(item.get("hwnd") or 0)
            if not hwnd or not win32gui.IsWindow(hwnd):
                continue
            try:
                title = win32gui.GetWindowText(hwnd) or str(item.get("title", ""))
                class_name = win32gui.GetClassName(hwnd)
                if query and query not in title.lower() and query not in class_name.lower():
                    continue
                matches.append({
                    "hwnd": hwnd,
                    "title": title,
                    "class_name": class_name,
                    "rect": list(win32gui.GetWindowRect(hwnd)),
                    "pid": item.get("pid", 0),
                    "visible": bool(win32gui.IsWindowVisible(hwnd)),
                })
            except Exception:
                continue
        return matches

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
