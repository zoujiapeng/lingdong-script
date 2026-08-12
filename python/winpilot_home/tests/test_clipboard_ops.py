from __future__ import annotations

from types import SimpleNamespace

from winpilot.action import clipboard_ops


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
