import json
import sys

import numpy as np
from PIL import Image


def test_screenshot_saves_target_window_without_shell(monkeypatch, tmp_path):
    from winpilot.agent import desktop_tools
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    image_path = tmp_path / "calculator.png"
    frame = np.full((24, 40, 3), (10, 20, 30), dtype=np.uint8)
    monkeypatch.setattr(desktop_tools, "capture", lambda region: frame)
    monkeypatch.setattr(desktop_tools, "window_region", lambda hwnd: Region(8, 9, 40, 24))

    session = ToolSession(task="save calculator screenshot")
    session.hwnd = 123
    result = session._t_screenshot({"path": str(image_path), "window": True})
    payload = json.loads(result.text)

    assert payload["ok"] is True
    assert payload["scope"] == "window"
    assert payload["region"] == {"left": 8, "top": 9, "width": 40, "height": 24}
    assert image_path.is_file()
    assert Image.open(image_path).size == (40, 24)


def test_paste_image_file_uses_clipboard_dib_and_focused_target(monkeypatch, tmp_path):
    from winpilot.action import executor

    image_path = tmp_path / "result.png"
    Image.new("RGB", (18, 11), color=(12, 34, 56)).save(image_path)
    calls = []

    class Clipboard:
        @staticmethod
        def OpenClipboard():
            calls.append("open")

        @staticmethod
        def EmptyClipboard():
            calls.append("empty")

        @staticmethod
        def SetClipboardData(fmt, payload):
            calls.append(("set", fmt, payload[:4]))

        @staticmethod
        def CloseClipboard():
            calls.append("close")

    monkeypatch.setitem(sys.modules, "win32clipboard", Clipboard)
    monkeypatch.setattr(executor, "_raw_focus_window", lambda hwnd: {"ok": hwnd == 88})
    monkeypatch.setattr(executor, "_raw_press_keys", lambda keys: calls.append(("keys", keys)))

    result = executor.paste_image_file(str(image_path), hwnd=88)

    assert result["ok"] is True
    assert result["via"] == "clipboard_dib"
    assert ("set", executor.win32con.CF_DIB, b"(\x00\x00\x00") in calls
    assert ("keys", ["ctrl", "v"]) in calls


def test_paste_image_file_rejects_missing_image():
    from winpilot.action import executor

    result = executor.paste_image_file(r"C:\\missing\\calculator.png")

    assert result["ok"] is False
    assert "not found" in result["error"]
