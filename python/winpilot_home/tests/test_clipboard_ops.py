from __future__ import annotations

import base64
from types import SimpleNamespace

from winpilot.action import clipboard_ops
from winpilot.vnext import desktop_ops


class _FakeUser32:
    def __init__(self, handles: list[int], *, foreground: int) -> None:
        self.handles = handles
        self.foreground = foreground

    def GetForegroundWindow(self) -> int:
        return self.foreground

    def EnumWindows(self, callback, _param) -> bool:
        for handle in self.handles:
            callback(handle, 0)
        return True

    @staticmethod
    def IsWindowVisible(_handle: int) -> bool:
        return True

    @staticmethod
    def IsIconic(_handle: int) -> bool:
        return False

    @staticmethod
    def GetWindowTextLengthW(handle: int) -> int:
        return len(f"Window {handle}")

    @staticmethod
    def GetWindowTextW(handle: int, buffer, _length: int) -> int:
        buffer.value = f"Window {handle}"
        return len(buffer.value)

    @staticmethod
    def GetWindowLongW(_handle: int, _index: int) -> int:
        return 0

    @staticmethod
    def GetClassNameW(_handle: int, buffer, _length: int) -> int:
        buffer.value = "TestWindow"
        return len(buffer.value)

    @staticmethod
    def GetWindowRect(handle: int, rect_pointer) -> bool:
        rect = rect_pointer._obj
        rect.left = handle
        rect.top = handle + 1
        rect.right = handle + 101
        rect.bottom = handle + 102
        return True

    @staticmethod
    def GetWindowThreadProcessId(handle: int, pid_pointer) -> int:
        pid_pointer._obj.value = handle + 1000
        return 1


class _MixedVisibilityUser32(_FakeUser32):
    @staticmethod
    def IsWindowVisible(handle: int) -> bool:
        return handle == 100

    @staticmethod
    def IsIconic(handle: int) -> bool:
        return handle == 200

    @staticmethod
    def GetWindowTextLengthW(handle: int) -> int:
        return len({100: "Editor", 200: "QQ", 201: "QQ"}[handle])

    @staticmethod
    def GetWindowTextW(handle: int, buffer, _length: int) -> int:
        buffer.value = {100: "Editor", 200: "QQ", 201: "QQ"}[handle]
        return len(buffer.value)


def test_capture_screen_delegates_relative_path_to_backend() -> None:
    calls: list[dict[str, object]] = []

    def backend(**arguments):
        calls.append(arguments)
        return {"ok": True, "path": arguments["path"]}

    result = desktop_ops.capture_screen(
        backend,
        path="current_screen.png",
        window=False,
    )

    assert calls == [{"path": "current_screen.png", "window": False}]
    assert result == {
        "ok": True,
        "path": "current_screen.png",
        "format": "capture-screen/1",
    }


def test_copy_current_view_delegates_the_exact_submitted_frame() -> None:
    calls: list[str] = []

    def backend(*, image_b64: str):
        calls.append(image_b64)
        return {"ok": True, "source": "current_model_view"}

    result = desktop_ops.copy_current_view(backend, image_b64="exact-jpeg")

    assert calls == ["exact-jpeg"]
    assert result == {
        "ok": True,
        "source": "current_model_view",
        "format": "copy-image/1",
    }


def test_copy_seen_frame_puts_exact_model_jpeg_on_clipboard(monkeypatch) -> None:
    payload = b"\xff\xd8exact-model-view\xff\xd9"
    encoded = base64.b64encode(payload).decode("ascii")
    decoded: list[bytes] = []
    clipboard_dibs: list[bytes] = []

    def to_dib(value: bytes):
        decoded.append(value)
        return b"dib", 1120, 630

    monkeypatch.setattr(clipboard_ops, "_image_bytes_to_dib", to_dib)
    monkeypatch.setattr(
        clipboard_ops, "set_clipboard_dib", clipboard_dibs.append
    )

    result = clipboard_ops.copy_seen_frame_backend(image_b64=encoded)

    assert result == {
        "format": "copy-image/1",
        "ok": True,
        "source": "current_model_view",
        "width": 1120,
        "height": 630,
        "size_bytes": len(payload),
    }
    assert decoded == [payload]
    assert clipboard_dibs == [b"dib"]


def test_capture_screen_backend_puts_image_on_clipboard(monkeypatch, tmp_path):
    import numpy as np
    from winpilot.utils import screenshot as screenshot_utils

    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    monkeypatch.setattr(
        screenshot_utils, "capture", lambda region=None: frame
    )
    clipboard_dibs: list[bytes] = []
    monkeypatch.setattr(
        clipboard_ops, "_image_file_to_dib", lambda path: (b"dib", 8, 8)
    )
    monkeypatch.setattr(
        clipboard_ops, "set_clipboard_dib", clipboard_dibs.append
    )

    target = tmp_path / "shot.png"
    result = clipboard_ops.capture_screen_backend(
        path=str(target), window=False
    )

    assert result["ok"] is True
    assert result["path"] == str(target)
    assert result["clipboard"] is True
    assert "clipboard_error" not in result
    assert clipboard_dibs == [b"dib"]
    assert target.is_file()


def test_capture_screen_backend_busy_clipboard_keeps_file_ok(monkeypatch, tmp_path):
    import numpy as np
    from winpilot.utils import screenshot as screenshot_utils

    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    monkeypatch.setattr(
        screenshot_utils, "capture", lambda region=None: frame
    )
    monkeypatch.setattr(
        clipboard_ops, "_image_file_to_dib", lambda path: (b"dib", 8, 8)
    )

    def busy(_dib: bytes) -> None:
        raise RuntimeError("clipboard is busy")

    monkeypatch.setattr(clipboard_ops, "set_clipboard_dib", busy)

    target = tmp_path / "shot.png"
    result = clipboard_ops.capture_screen_backend(
        path=str(target), window=False
    )

    assert result["ok"] is True
    assert result["clipboard"] is False
    assert "clipboard_error" in result
    assert target.is_file()


def test_list_windows_keeps_frontmost_24_with_truthful_facts(monkeypatch) -> None:
    handles = list(range(100, 130))
    user32 = _FakeUser32(handles, foreground=103)
    process_calls: list[int] = []

    monkeypatch.setattr(
        clipboard_ops.ctypes,
        "windll",
        SimpleNamespace(user32=user32),
    )
    monkeypatch.setattr(
        clipboard_ops.ctypes,
        "WINFUNCTYPE",
        lambda *_args: lambda callback: callback,
    )
    monkeypatch.setattr(
        clipboard_ops,
        "_process_name",
        lambda pid: process_calls.append(pid) or f"process-{pid}.exe",
    )

    result = clipboard_ops.list_windows_backend()

    assert result["ok"] is True
    assert result["count"] == 24
    assert [window["hwnd"] for window in result["windows"]] == handles[:24]
    assert process_calls == [handle + 1000 for handle in handles[:24]]
    assert result["windows"][0] == {
        "hwnd": 100,
        "title": "Window 100",
        "class_name": "TestWindow",
        "rect": [100, 101, 201, 202],
        "pid": 1100,
        "foreground": False,
        "visible": True,
        "minimized": False,
        "process_name": "process-1100.exe",
    }
    assert result["windows"][3]["foreground"] is True


def test_list_windows_does_not_depend_on_ctypes_wintypes_side_effect(
    monkeypatch,
) -> None:
    """Enumeration must work before any other module imports ctypes.wintypes."""
    user32 = _FakeUser32([100], foreground=100)
    monkeypatch.setattr(
        clipboard_ops.ctypes,
        "windll",
        SimpleNamespace(user32=user32),
    )
    monkeypatch.setattr(
        clipboard_ops.ctypes,
        "WINFUNCTYPE",
        lambda *_args: lambda callback: callback,
    )
    monkeypatch.delattr(clipboard_ops.ctypes, "wintypes", raising=False)
    monkeypatch.setattr(
        clipboard_ops,
        "_process_name",
        lambda pid: f"process-{pid}.exe",
    )

    result = clipboard_ops.list_windows_backend()

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["windows"][0]["hwnd"] == 100
    assert result["windows"][0]["foreground"] is True


def test_list_windows_reports_deduped_background_applications(monkeypatch) -> None:
    user32 = _MixedVisibilityUser32([100, 200, 201], foreground=100)
    monkeypatch.setattr(
        clipboard_ops.ctypes,
        "windll",
        SimpleNamespace(user32=user32),
    )
    monkeypatch.setattr(
        clipboard_ops.ctypes,
        "WINFUNCTYPE",
        lambda *_args: lambda callback: callback,
    )
    monkeypatch.setattr(
        clipboard_ops,
        "_process_name",
        lambda pid: "editor.exe" if pid == 1100 else "QQ.exe",
    )

    result = clipboard_ops.list_windows_backend()

    assert [item["title"] for item in result["windows"]] == ["Editor"]
    assert result["background_count"] == 1
    assert result["background_applications"] == [{
        "process_name": "QQ.exe",
        "title": "QQ",
        "state": "minimized",
    }]


class _FakeKernel32:
    def __init__(self, *, path: str = "", opens: bool = True) -> None:
        self.path = path
        self.opens = opens
        self.closed: list[int] = []

    def OpenProcess(self, _access: int, _inherit: bool, pid: int) -> int:
        return pid + 5000 if self.opens else 0

    def QueryFullProcessImageNameW(
        self,
        _handle: int,
        _flags: int,
        buffer,
        size_pointer,
    ) -> bool:
        buffer.value = self.path
        size_pointer._obj.value = len(self.path)
        return bool(self.path)

    def CloseHandle(self, handle: int) -> bool:
        self.closed.append(handle)
        return True


def test_process_name_uses_pid_path_basename_and_fails_empty(monkeypatch) -> None:
    kernel32 = _FakeKernel32(path=r"C:\Program Files\Tencent\QQ.exe")
    monkeypatch.setattr(
        clipboard_ops.ctypes,
        "windll",
        SimpleNamespace(kernel32=kernel32),
    )

    assert clipboard_ops._process_name(42) == "QQ.exe"
    assert kernel32.closed == [5042]

    unavailable = _FakeKernel32(opens=False)
    monkeypatch.setattr(
        clipboard_ops.ctypes,
        "windll",
        SimpleNamespace(kernel32=unavailable),
    )
    assert clipboard_ops._process_name(42) == ""
    assert clipboard_ops._process_name(0) == ""


def test_paste_image_never_uses_stale_clipboard_when_path_is_wrong(
    monkeypatch,
    tmp_path,
) -> None:
    key_events: list[tuple[int, int]] = []

    class User32:
        @staticmethod
        def keybd_event(vk: int, _scan: int, flags: int, _extra: int) -> None:
            key_events.append((vk, flags))

    monkeypatch.setattr(
        clipboard_ops.ctypes,
        "windll",
        SimpleNamespace(user32=User32()),
    )

    missing = tmp_path / "invented-user" / "shot.png"
    result = clipboard_ops.paste_image_backend(path=str(missing))

    assert result == {
        "format": "paste-image/1",
        "ok": False,
        "error": f"image file not found: {missing}",
    }
    assert key_events == []
