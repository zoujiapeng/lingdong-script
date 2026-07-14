"""General desktop utility tool handlers for ToolSession."""
from __future__ import annotations

import base64
import ctypes
from io import BytesIO
from pathlib import Path

from ..action import executor
from ..action.verify import _match_visible_text
from ..config import CONFIG
from ..perception import vision_cv
from ..utils.screenshot import Region, capture, desktop_geometry, screen_size, window_region
from .interaction_tools import _call_with_optional_hwnd, normalize_key_sequence
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
        target = getattr(self, "_action_hwnd", lambda: None)()
        result = _call_with_optional_hwnd(
            executor.clipboard_paste, hwnd=target,
        )
        return ToolResult(self._fmt(result))

    def _t_cursor_position(self, _args: dict) -> ToolResult:
        result = executor.cursor_position()
        return ToolResult(self._fmt(result))

    def _t_mouse_down(self, args: dict) -> ToolResult:
        button = str(args.get("button", "left"))
        target = getattr(self, "_action_hwnd", lambda: None)()
        result = _call_with_optional_hwnd(
            executor.mouse_down, button, hwnd=target,
        )
        return ToolResult(self._fmt(result))

    def _t_mouse_up(self, args: dict) -> ToolResult:
        button = str(args.get("button", "left"))
        result = executor.mouse_up(button=button)
        return ToolResult(self._fmt(result))

    def _t_move_mouse(self, args: dict) -> ToolResult:
        x, y = int(args["x"]), int(args["y"])
        target = getattr(self, "_action_hwnd", lambda: None)()
        result = _call_with_optional_hwnd(
            executor.move_mouse, x, y, hwnd=target,
        )
        return ToolResult(self._fmt(result))

    def _t_screenshot(self, args: dict) -> ToolResult:
        quality = int(args.get("quality", 75))
        save_path = str(args.get("path") or "").strip()
        if save_path:
            target = Path(save_path).expanduser()
            if not target.is_absolute():
                return ToolResult("screenshot path must be absolute")
            if target.suffix.casefold() not in {".png", ".jpg", ".jpeg"}:
                return ToolResult("screenshot path must end in .png, .jpg, or .jpeg")
            if target.exists() and not bool(args.get("overwrite")):
                return ToolResult(f"screenshot path already exists: {target}")
            if not target.parent.is_dir():
                return ToolResult(f"screenshot directory does not exist: {target.parent}")
            try:
                region = None
                hwnd = None
                if bool(args.get("window")):
                    hwnd = int(args.get("hwnd") or getattr(self, "hwnd", 0) or 0)
                    if not hwnd:
                        action_hwnd = getattr(self, "_action_hwnd", None)
                        hwnd = int(action_hwnd() or 0) if callable(action_hwnd) else 0
                    if not hwnd:
                        return ToolResult("screenshot window=true requires an active target window")
                    region = window_region(hwnd)
                elif all(key in args for key in ("x", "y", "width", "height")):
                    region = Region(
                        int(args["x"]), int(args["y"]),
                        int(args["width"]), int(args["height"]),
                    ).clamp_to_screen()
                frame = capture(region)
                from PIL import Image

                image = Image.fromarray(frame[:, :, ::-1].copy())
                payload = BytesIO()
                image_format = "PNG" if target.suffix.casefold() == ".png" else "JPEG"
                save_options = {"format": image_format}
                if image_format == "JPEG":
                    save_options.update({"quality": max(1, min(100, quality)), "optimize": True})
                image.save(payload, **save_options)
                target.write_bytes(payload.getvalue())
                result = {
                    "ok": True,
                    "path": str(target),
                    "width": int(image.width),
                    "height": int(image.height),
                    "size_bytes": len(payload.getvalue()),
                    "scope": "window" if hwnd else ("region" if region else "screen"),
                }
                if region is not None:
                    result["region"] = {
                        "left": region.left,
                        "top": region.top,
                        "width": region.width,
                        "height": region.height,
                    }
                saved = getattr(self, "saved_screenshot_paths", None)
                if isinstance(saved, list):
                    saved.append(str(target))
                    self.saved_screenshot_paths = saved[-12:]
                return ToolResult(self._fmt(result))
            except Exception as exc:
                return ToolResult(self._fmt({"ok": False, "error": str(exc)}))
        result = executor.screenshot_base64(quality=quality)
        self._fold_base64(result)
        return ToolResult(self._fmt(result))

    def _t_paste_image_file(self, args: dict) -> ToolResult:
        path = str(args.get("path") or "").strip()
        if not path:
            return ToolResult("paste_image_file requires an absolute image path")
        target = args.get("hwnd") or getattr(self, "hwnd", None)
        if not target:
            action_hwnd = getattr(self, "_action_hwnd", None)
            target = action_hwnd() if callable(action_hwnd) else None
        result = executor.paste_image_file(path, hwnd=int(target) if target else None)
        return ToolResult(self._fmt(result))

    def _t_hold_key(self, args: dict) -> ToolResult:
        keys = normalize_key_sequence(args.get("keys") or [])
        target = getattr(self, "_action_hwnd", lambda: None)()
        result = _call_with_optional_hwnd(
            executor.hold_keys,
            keys,
            int(args.get("duration_ms", 0)),
            hwnd=target,
        )
        return ToolResult(self._fmt(result))

    def _t_list_displays(self, _args: dict) -> ToolResult:
        result = executor.list_displays()
        return ToolResult(self._fmt(result))

    def _t_coordinate_diagnostics(self, args: dict) -> ToolResult:
        """Report coordinate/capture geometry for DPI/ROI mismatch debugging."""
        payload: dict = {"ok": True}
        try:
            geometry = desktop_geometry()
            sw, sh = screen_size()
            payload["screen_size"] = {
                "left": geometry.virtual.left,
                "top": geometry.virtual.top,
                "right": geometry.virtual.right,
                "bottom": geometry.virtual.bottom,
                "width": int(sw),
                "height": int(sh),
                "source": "GetSystemMetrics virtual desktop",
            }
            payload["primary_screen"] = {
                "left": geometry.primary.left,
                "top": geometry.primary.top,
                "width": geometry.primary.width,
                "height": geometry.primary.height,
            }
        except Exception as exc:
            payload["screen_size_error"] = str(exc)
        try:
            full = capture()
            payload["full_capture"] = {
                "left": payload.get("screen_size", {}).get("left", 0),
                "top": payload.get("screen_size", {}).get("top", 0),
                "width": int(full.shape[1]),
                "height": int(full.shape[0]),
                "source": "mss",
            }
        except Exception as exc:
            payload["full_capture_error"] = str(exc)
        try:
            payload["displays"] = executor.list_displays()
        except Exception as exc:
            payload["displays_error"] = str(exc)

        hwnd = args.get("hwnd") or getattr(self, "hwnd", None)
        region = None
        if hwnd:
            try:
                hwnd = int(hwnd)
                region = window_region(hwnd)
                payload["target_window"] = {
                    "hwnd": hwnd,
                    "region": {
                        "left": region.left,
                        "top": region.top,
                        "width": region.width,
                        "height": region.height,
                        "right": region.right,
                        "bottom": region.bottom,
                    },
                }
                try:
                    dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
                    payload["target_window"]["dpi"] = int(dpi)
                    payload["target_window"]["scale_from_96dpi"] = round(float(dpi) / 96.0, 4)
                except Exception as exc:
                    payload["target_window"]["dpi_error"] = str(exc)
                try:
                    cropped = capture(region)
                    payload["target_window"]["capture"] = {
                        "width": int(cropped.shape[1]),
                        "height": int(cropped.shape[0]),
                        "matches_region": (
                            int(cropped.shape[1]) == int(region.width)
                            and int(cropped.shape[0]) == int(region.height)
                        ),
                    }
                except Exception as exc:
                    payload["target_window"]["capture_error"] = str(exc)
            except Exception as exc:
                payload["target_window_error"] = str(exc)

        if args.get("x") is not None and args.get("y") is not None:
            x, y = int(args["x"]), int(args["y"])
            point = {"x": x, "y": y}
            if region is not None:
                point["relative_to_target"] = {
                    "x": x - region.left,
                    "y": y - region.top,
                    "inside": region.left <= x < region.right and region.top <= y < region.bottom,
                }
            try:
                probe = capture(Region(x - 2, y - 2, 5, 5))
                if getattr(probe, "size", 0):
                    center = probe[min(2, probe.shape[0] - 1), min(2, probe.shape[1] - 1)].tolist()
                    point["bgr_at_point"] = [int(v) for v in center[:3]]
            except Exception as exc:
                point["probe_error"] = str(exc)
            payload["point"] = point

        warnings: list[str] = []
        screen = payload.get("screen_size") or {}
        full_capture = payload.get("full_capture") or {}
        if screen and full_capture and (
            int(screen.get("width") or -1) != int(full_capture.get("width") or -2)
            or int(screen.get("height") or -1) != int(full_capture.get("height") or -2)
        ):
            warnings.append("screen_size differs from full_capture; check DPI awareness or virtual monitor origin.")
        target = payload.get("target_window") or {}
        target_capture = target.get("capture") if isinstance(target, dict) else {}
        if isinstance(target_capture, dict) and target_capture.get("matches_region") is False:
            warnings.append("target window capture size does not match window_region; ROI coordinate mapping is suspect.")
        payload["warnings"] = warnings
        return ToolResult(self._fmt(payload))

    def _t_coordinate_click_test(self, args: dict) -> ToolResult:
        """Perform one real mouse click and return coordinate evidence."""
        x, y = int(args["x"]), int(args["y"])
        remembered_hwnd = getattr(self, "hwnd", None)
        requested_hwnd = args.get("hwnd")
        if CONFIG.get("controller", "multi_input_cooperation", default=False):
            try:
                mismatched_target = bool(
                    requested_hwnd
                    and int(requested_hwnd) != int(remembered_hwnd or 0)
                )
            except (TypeError, ValueError):
                mismatched_target = True
            if mismatched_target:
                return ToolResult(self._fmt({
                    "ok": False,
                    "verified": False,
                    "blocked": True,
                    "cooperative_input": True,
                    "error": (
                        "coordinate_click_test cannot override the remembered "
                        "cooperative target hwnd"
                    ),
                    "target_hwnd": int(remembered_hwnd or 0),
                }))
            hwnd = remembered_hwnd
        else:
            hwnd = requested_hwnd or remembered_hwnd
        try:
            hwnd = int(hwnd) if hwnd else None
        except (TypeError, ValueError):
            hwnd = None
        button = str(args.get("button") or "left")
        double = bool(args.get("double", False))
        payload: dict = {
            "ok": False,
            "requested": {"x": x, "y": y},
            "button": button,
            "double": double,
            "real_mouse_path": True,
            "note": (
                "This tool calls executor.click with physical screen coordinates. "
                "Compare requested.x/y with the target app's own click echo. "
                "Browser event.screenX/screenY may be CSS screen coordinates under DPI scaling, "
                "not physical pixels."
            ),
        }
        try:
            virtual = desktop_geometry().virtual
            payload["virtual_screen"] = {
                "left": virtual.left,
                "top": virtual.top,
                "right": virtual.right,
                "bottom": virtual.bottom,
            }
            if not (virtual.left <= x < virtual.right and virtual.top <= y < virtual.bottom):
                payload["blocked"] = True
                payload["error"] = "Requested point is outside the virtual desktop bounds."
                return ToolResult(self._fmt(payload))
        except Exception as exc:
            payload["geometry_error"] = str(exc)
        try:
            payload["cursor_before"] = executor.cursor_position()
        except Exception as exc:
            payload["cursor_before_error"] = str(exc)
        if hwnd:
            try:
                region = window_region(hwnd)
                payload["target_window"] = {
                    "hwnd": hwnd,
                    "region": {
                        "left": region.left,
                        "top": region.top,
                        "width": region.width,
                        "height": region.height,
                        "right": region.right,
                        "bottom": region.bottom,
                    },
                    "requested_relative": {
                        "x": x - region.left,
                        "y": y - region.top,
                        "inside": region.left <= x < region.right and region.top <= y < region.bottom,
                    },
                }
                try:
                    dpi = ctypes.windll.user32.GetDpiForWindow(hwnd)
                    payload["target_window"]["dpi"] = int(dpi)
                    payload["target_window"]["scale_from_96dpi"] = round(float(dpi) / 96.0, 4)
                except Exception as exc:
                    payload["target_window"]["dpi_error"] = str(exc)
            except Exception as exc:
                payload["target_window_error"] = str(exc)
        try:
            click_result = executor.click(x, y, button=button, double=double, hwnd=hwnd)
            payload["click_result"] = click_result
            payload["ok"] = bool(click_result.get("ok"))
        except Exception as exc:
            payload["error"] = str(exc)
            return ToolResult(self._fmt(payload))
        try:
            cursor_after = executor.cursor_position()
            payload["cursor_after"] = cursor_after
            if cursor_after.get("ok"):
                dx = int(cursor_after.get("x", x)) - x
                dy = int(cursor_after.get("y", y)) - y
                payload["cursor_error"] = {"dx": dx, "dy": dy}
                payload["coordinate_verified"] = abs(dx) <= 1 and abs(dy) <= 1
                if not payload["coordinate_verified"]:
                    payload["ok"] = False
                    payload["error"] = "Real cursor position does not match the requested physical coordinate."
        except Exception as exc:
            payload["cursor_after_error"] = str(exc)
        wait_ms = args.get("wait_ms", 250)
        try:
            import time
            time.sleep(max(0.0, min(float(wait_ms), 3000.0)) / 1000.0)
        except Exception:
            pass
        expected = str(args.get("expect_text") or "").strip()
        if expected:
            try:
                read = self._t_read_text({"hwnd": hwnd} if hwnd else {})
                payload["read_text_preview"] = read.text[:1200]
                payload["verified"] = bool(_match_visible_text(expected, read.text))
                payload["expect_text"] = expected
            except Exception as exc:
                payload["verify_error"] = str(exc)
                payload["verified"] = False
        return ToolResult(self._fmt(payload))

    def _t_zoom(self, args: dict) -> ToolResult:
        result = executor.zoom_region(
            int(args["x"]), int(args["y"]),
            int(args["width"]), int(args["height"]),
            quality=int(args.get("quality", 80)),
        )
        self._fold_base64(result)
        return ToolResult(self._fmt(result))

    def _t_list_installed_apps(self, args: dict) -> ToolResult:
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
            offset = max(0, int(args.get("offset") or 0))
            limit = max(1, min(200, int(args.get("limit") or 80)))
            page = apps[offset:offset + limit]
            next_offset = offset + len(page) if offset + len(page) < len(apps) else None
            lines = [
                f'{i}. "{a["displayName"]}" [{a["bundleId"]}] {a["path"]}'
                for i, a in enumerate(page, offset + 1)
            ]
            range_text = (
                f"{offset + 1}-{offset + len(page)}" if page else "empty"
            )
            pagination = (
                f" next_offset={next_offset}; call again with offset={next_offset} to continue."
                if next_offset is not None else " complete=true."
            )
            return ToolResult(
                f"已安装应用 total={len(apps)} range={range_text};{pagination}\n" + "\n".join(lines)
                or "未找到已安装应用",
                summary=f"[已折叠] list_installed_apps {len(apps)}个应用",
            )
        except Exception as exc:
            return ToolResult(f"列举已安装应用失败: {exc}")

    @staticmethod
    def _fold_base64(result: dict) -> None:
        if result.get("ok") and result.get("base64"):
            b64 = result["base64"]
            DesktopToolMixin._add_nonvisual_summary(result, b64)
            result["base64_preview"] = b64[:120] + "...(" + str(len(b64)) + " chars total)"
            result["base64_available"] = True
            result["text_only_note"] = (
                "Base64 image bytes are for trace/debug. A text-only main model "
                "cannot inspect this image directly; use nonvisual_summary, "
                "observe OCR/UIA/CV, or a VLM tool when available."
            )
            del result["base64"]

    @staticmethod
    def _add_nonvisual_summary(result: dict, b64: str) -> None:
        try:
            import cv2
            import numpy as np

            raw = base64.b64decode(b64)
            image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                return
            summary = DesktopToolMixin._compact_image_summary(image)
            canvas = vision_cv.likely_canvas_bbox(image)
            if canvas:
                summary["likely_canvas_bbox"] = canvas
                x1, y1, x2, y2 = canvas
                crop = image[y1:y2, x1:x2]
                summary["likely_canvas_summary"] = DesktopToolMixin._compact_image_summary(crop)
            result["nonvisual_summary"] = summary
        except Exception as exc:
            result["nonvisual_summary_error"] = str(exc)

    @staticmethod
    def _compact_image_summary(image) -> dict:
        ink = vision_cv.image_ink_stats_from_array(image)
        colors = vision_cv.color_presence(image).get("colors", {})
        visible_colors = {
            name: {
                key: value
                for key, value in stats.items()
                if key in {"pixels", "ratio", "bbox", "largest_bbox", "largest_area"}
            }
            for name, stats in colors.items()
            if stats.get("visible")
        }
        return {
            "width": ink.get("width"),
            "height": ink.get("height"),
            "blankish": ink.get("blankish"),
            "ink_ratio": ink.get("ink_ratio"),
            "ink_bbox": ink.get("ink_bbox"),
            "component_count": ink.get("component_count"),
            "visible_colors": visible_colors,
        }
