"""General desktop utility tool handlers for ToolSession."""
from __future__ import annotations

from pathlib import Path

from ..action import executor
from .tool_result import ToolResult


class DesktopToolMixin:
    def _t_clipboard_read(self, _args: dict) -> ToolResult:
        text = executor.clipboard_read()
        return ToolResult(text or "（剪贴板为空）",
                          summary=f"[已折叠] clipboard_read {len(text)}字符")

    def _t_clipboard_write(self, args: dict) -> ToolResult:
        text = str(args.get("text", ""))
        result = executor.clipboard_write(text)
        return ToolResult(self._fmt(result))

    def _t_clipboard_paste(self, _args: dict) -> ToolResult:
        result = executor.clipboard_paste()
        return ToolResult(self._fmt(result))

    def _t_cursor_position(self, _args: dict) -> ToolResult:
        result = executor.cursor_position()
        return ToolResult(self._fmt(result))

    def _t_mouse_down(self, args: dict) -> ToolResult:
        button = str(args.get("button", "left"))
        result = executor.mouse_down(button=button)
        return ToolResult(self._fmt(result))

    def _t_mouse_up(self, args: dict) -> ToolResult:
        button = str(args.get("button", "left"))
        result = executor.mouse_up(button=button)
        return ToolResult(self._fmt(result))

    def _t_move_mouse(self, args: dict) -> ToolResult:
        x, y = int(args["x"]), int(args["y"])
        executor.move_mouse(x, y)
        return ToolResult(self._fmt({"ok": True, "x": x, "y": y}))

    def _t_screenshot(self, args: dict) -> ToolResult:
        quality = int(args.get("quality", 75))
        result = executor.screenshot_base64(quality=quality)
        self._fold_base64(result)
        return ToolResult(self._fmt(result))

    def _t_hold_key(self, args: dict) -> ToolResult:
        keys = args.get("keys") or []
        if isinstance(keys, str):
            keys = [keys]
        result = executor.hold_keys([str(k) for k in keys],
                                    int(args.get("duration_ms", 0)))
        return ToolResult(self._fmt(result))

    def _t_list_displays(self, _args: dict) -> ToolResult:
        result = executor.list_displays()
        return ToolResult(self._fmt(result))

    def _t_zoom(self, args: dict) -> ToolResult:
        result = executor.zoom_region(
            int(args["x"]), int(args["y"]),
            int(args["width"]), int(args["height"]),
            quality=int(args.get("quality", 80)),
        )
        self._fold_base64(result)
        return ToolResult(self._fmt(result))

    def _t_list_installed_apps(self, _args: dict) -> ToolResult:
        try:
            import winreg
            results: dict[str, dict] = {}
            reg_paths = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            ]
            for hive, sub_key in reg_paths:
                try:
                    key = winreg.OpenKey(hive, sub_key)
                except OSError:
                    continue
                try:
                    i = 0
                    while True:
                        try:
                            name = winreg.EnumKey(key, i)
                            i += 1
                        except OSError:
                            break
                        try:
                            app_key = winreg.OpenKey(key, name)
                        except OSError:
                            continue
                        try:
                            display_name = winreg.QueryValueEx(app_key, "DisplayName")[0]
                        except OSError:
                            winreg.CloseKey(app_key)
                            continue
                        try:
                            install_location = winreg.QueryValueEx(app_key, "InstallLocation")[0]
                        except OSError:
                            install_location = ""
                        try:
                            display_icon = winreg.QueryValueEx(app_key, "DisplayIcon")[0]
                        except OSError:
                            display_icon = ""
                        normalized_icon = str(display_icon).split(",")[0].strip().strip('"')
                        normalized_install_location = str(install_location).strip().strip('"')
                        bundle_id = name
                        for candidate in (normalized_icon, normalized_install_location):
                            if not candidate:
                                continue
                            candidate_path = Path(candidate)
                            if candidate_path.suffix.lower() == ".exe":
                                bundle_id = candidate_path.stem
                                break
                        app_path = normalized_icon or normalized_install_location or ""
                        if bundle_id not in results:
                            results[bundle_id] = {
                                "bundleId": bundle_id,
                                "displayName": str(display_name),
                                "path": app_path,
                            }
                        winreg.CloseKey(app_key)
                finally:
                    winreg.CloseKey(key)
            apps = sorted(results.values(), key=lambda item: item["displayName"].lower())
            lines = [
                f'{i}. "{a["displayName"]}" [{a["bundleId"]}] {a["path"]}'
                for i, a in enumerate(apps[:80], 1)
            ]
            return ToolResult(
                f"已安装应用 ({len(apps)} 个，显示前 {min(len(apps), 80)} 个):\n" + "\n".join(lines)
                or "未找到已安装应用",
                summary=f"[已折叠] list_installed_apps {len(apps)}个应用",
            )
        except Exception as exc:
            return ToolResult(f"列举已安装应用失败: {exc}")

    @staticmethod
    def _fold_base64(result: dict) -> None:
        if result.get("ok") and result.get("base64"):
            b64 = result["base64"]
            result["base64_preview"] = b64[:120] + "...(" + str(len(b64)) + " chars total)"
            result["base64_available"] = True
            del result["base64"]
