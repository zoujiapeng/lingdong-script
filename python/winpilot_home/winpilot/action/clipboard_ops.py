"""vNext-owned physical capture / window / clipboard backends.

The vNext kernel is forbidden from importing win32 directly; these backends
live in the action layer (the product line's allowed desktop boundary) and
are injected into the Visual Actor as plain callables.  They are separate
from the legacy executor functions so the vNext tool surface never routes
through legacy owned code.
"""
from __future__ import annotations

import ctypes
import ntpath
import time
from ctypes import wintypes
from io import BytesIO
from pathlib import Path
from typing import Any

from ..utils.log import logger

CAPTURE_FORMAT = "capture-screen/1"
WINDOWS_FORMAT = "windows-list/1"
PASTE_FORMAT = "paste-image/1"

_CAPTURE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def _process_name(pid: int) -> str:
    """Return the executable basename for one real process id."""

    if int(pid) <= 0:
        return ""
    kernel32 = ctypes.windll.kernel32
    handle = 0
    try:
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return ""
        size = ctypes.c_ulong(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle,
            0,
            buffer,
            ctypes.byref(size),
        ):
            return ""
        return ntpath.basename(buffer.value)
    except Exception:
        return ""
    finally:
        if handle:
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass


def capture_screen_backend(*, path: str, window: bool) -> dict[str, Any]:
    """Save one screen/window capture as a PNG/JPEG file."""
    from ..utils.dpi import ensure_process_dpi_awareness
    from ..utils.screenshot import capture as capture_region

    ensure_process_dpi_awareness()
    target = Path(str(path or "")).expanduser()
    if target.suffix.casefold() not in _CAPTURE_EXTENSIONS:
        return {
            "format": CAPTURE_FORMAT,
            "ok": False,
            "error": "capture_screen path must end in .png, .jpg, or .jpeg",
        }
    if not target.parent.is_dir():
        return {
            "format": CAPTURE_FORMAT,
            "ok": False,
            "error": f"capture_screen directory does not exist: {target.parent}",
        }
    region = None
    if window:
        source_hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        if not source_hwnd:
            return {
                "format": CAPTURE_FORMAT,
                "ok": False,
                "error": (
                    "capture_screen window=true requires a target window"
                ),
            }
        from ..utils.screenshot import window_region

        region = window_region(source_hwnd)
    from PIL import Image

    frame = capture_region(region)
    image = Image.fromarray(frame[:, :, ::-1].copy())
    payload = BytesIO()
    image_format = "PNG" if target.suffix.casefold() == ".png" else "JPEG"
    save_options = {"format": image_format}
    if image_format == "JPEG":
        save_options.update({"quality": 85, "optimize": True})
    image.save(payload, **save_options)
    target.write_bytes(payload.getvalue())
    return {
        "format": CAPTURE_FORMAT,
        "ok": True,
        "path": str(target),
        "width": int(image.width),
        "height": int(image.height),
        "size_bytes": len(payload.getvalue()),
        "scope": "window" if window else "screen",
    }


def list_windows_backend() -> dict[str, Any]:
    """Return bounded visible windows and background applications.

    Self-contained EnumWindows implementation so the vNext tool surface does
    not reach into the legacy perception stack.
    """
    user32 = ctypes.windll.user32
    foreground_hwnd = int(user32.GetForegroundWindow() or 0)

    windows: list[dict[str, Any]] = []
    background_windows: list[dict[str, Any]] = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
    )

    def callback(hwnd: ctypes.c_void_p, _param: ctypes.c_void_p) -> bool:
        handle = int(hwnd or 0)
        if not handle:
            return True
        visible = bool(user32.IsWindowVisible(handle))
        minimized = bool(user32.IsIconic(handle))
        title_length = user32.GetWindowTextLengthW(handle)
        title_buffer = ctypes.create_unicode_buffer(max(title_length, 0) + 1)
        user32.GetWindowTextW(handle, title_buffer, len(title_buffer))
        title = title_buffer.value
        if not title:
            return True
        # skip tool windows / our own overlay-style windows
        ex_style = user32.GetWindowLongW(handle, -20)  # GWL_EXSTYLE
        if ex_style & 0x00000080:  # WS_EX_TOOLWINDOW
            return True
        class_buffer = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(handle, class_buffer, 64)
        rect = wintypes.RECT()
        if not user32.GetWindowRect(handle, ctypes.byref(rect)):
            return True
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < 50 or height < 50:
            return True
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        item = {
            "hwnd": handle,
            "title": title,
            "class_name": class_buffer.value,
            "rect": [rect.left, rect.top, rect.right, rect.bottom],
            "pid": int(pid.value or 0),
            "foreground": handle == foreground_hwnd,
            "visible": visible,
            "minimized": minimized,
        }
        if visible and not minimized:
            windows.append(item)
        else:
            background_windows.append(item)
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(callback), 0)
    except Exception as exc:
        return {
            "format": WINDOWS_FORMAT,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    bounded: list[dict[str, Any]] = []
    for item in windows[:24]:
        window = dict(item)
        window["process_name"] = _process_name(int(window.get("pid") or 0))
        bounded.append(window)
    background_applications: list[dict[str, Any]] = []
    seen_background: set[tuple[str, str]] = set()
    for item in background_windows:
        process_name = _process_name(int(item.get("pid") or 0))
        title = str(item.get("title") or "").strip()
        identity = (
            ("process", process_name.casefold())
            if process_name
            else ("title", title.casefold())
        )
        if identity in seen_background:
            continue
        seen_background.add(identity)
        background_applications.append({
            "process_name": process_name,
            "title": title,
            "state": "minimized" if item.get("minimized") else "background",
        })
        if len(background_applications) >= 24:
            break
    return {
        "format": WINDOWS_FORMAT,
        "ok": True,
        "windows": bounded,
        "count": len(bounded),
        "background_applications": background_applications,
        "background_count": len(background_applications),
    }


def _image_file_to_dib(path: Path) -> tuple[bytes, int, int]:
    """Read a raster image into the CF_DIB payload accepted by Windows apps."""
    from PIL import Image

    with Image.open(path) as source:
        image = source.convert("RGB")
        width, height = image.size
        buffer = BytesIO()
        image.save(buffer, format="BMP")
    # CF_DIB contains a BITMAPINFOHEADER, while BMP files prefix it with file
    # metadata that clipboard consumers do not expect.
    return buffer.getvalue()[14:], int(width), int(height)


def set_clipboard_dib(dib: bytes) -> None:
    """Replace the clipboard with one CF_DIB payload (shared with executor)."""
    import win32clipboard
    import win32con

    opened = False
    last_error: Exception | None = None
    for _attempt in range(4):
        try:
            win32clipboard.OpenClipboard()
            opened = True
            break
        except Exception as exc:
            last_error = exc
            time.sleep(0.04)
    if not opened:
        raise RuntimeError(f"clipboard is busy: {last_error}")
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
    finally:
        win32clipboard.CloseClipboard()


def paste_image_backend(*, path: str) -> dict[str, Any]:
    """Put one image file onto the clipboard and press Ctrl+V."""
    import win32con

    image_path = Path(str(path or "")).expanduser()
    if not image_path.is_file():
        return {
            "format": PASTE_FORMAT,
            "ok": False,
            "error": f"image file not found: {image_path}",
        }
    if image_path.suffix.casefold() not in {
        ".png", ".jpg", ".jpeg", ".bmp", ".webp",
    }:
        return {
            "format": PASTE_FORMAT,
            "ok": False,
            "error": "paste_image requires a PNG, JPEG, BMP, or WebP file",
        }
    dib, width, height = _image_file_to_dib(image_path)
    set_clipboard_dib(dib)

    # Ctrl+V through SendInput to the foreground window.  This is the vNext
    # input path: raw physical keyboard events, no legacy executor.
    user32 = ctypes.windll.user32
    KEYEVENTF_KEYUP = 0x0002
    vk_ctrl = 0x11
    vk_v = 0x56
    user32.keybd_event(vk_ctrl, 0, 0, 0)
    user32.keybd_event(vk_v, 0, 0, 0)
    user32.keybd_event(vk_v, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(vk_ctrl, 0, KEYEVENTF_KEYUP, 0)
    return {
        "format": PASTE_FORMAT,
        "ok": True,
        "path": str(image_path),
        "width": int(width),
        "height": int(height),
        "via": "clipboard_dib",
    }


__all__ = [
    "capture_screen_backend",
    "list_windows_backend",
    "paste_image_backend",
]
