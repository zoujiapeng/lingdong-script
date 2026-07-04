import json

import numpy as np


def test_coordinate_diagnostics_reports_capture_mismatch(monkeypatch):
    from winpilot.agent.desktop_tools import DesktopToolMixin

    class Session(DesktopToolMixin):
        hwnd = None

        @staticmethod
        def _fmt(data):
            return json.dumps(data, ensure_ascii=False, default=str)

    monkeypatch.setattr("winpilot.agent.desktop_tools.screen_size", lambda: (1920, 1080))
    monkeypatch.setattr("winpilot.agent.desktop_tools.capture", lambda region=None: np.zeros((900, 1600, 3), dtype=np.uint8))
    monkeypatch.setattr("winpilot.agent.desktop_tools.executor.list_displays", lambda: {"ok": True, "displays": []})

    result = Session()._t_coordinate_diagnostics({})
    payload = json.loads(result.text)

    assert payload["screen_size"]["width"] == 1920
    assert payload["full_capture"]["width"] == 1600
    assert payload["warnings"]


def test_coordinate_click_test_uses_real_executor_click_and_verifies_text(monkeypatch):
    from winpilot.agent.desktop_tools import DesktopToolMixin

    calls = []

    class Session(DesktopToolMixin):
        hwnd = 99

        @staticmethod
        def _fmt(data):
            return json.dumps(data, ensure_ascii=False, default=str)

        def _t_read_text(self, args):
            from winpilot.agent.tool_result import ToolResult

            return ToolResult("CALIBRATION_OK screen=321,456 target_offset=221,256")

    monkeypatch.setattr("winpilot.agent.desktop_tools.executor.cursor_position", lambda: {"ok": True, "x": 10, "y": 20})
    monkeypatch.setattr("winpilot.agent.desktop_tools.window_region", lambda hwnd: type("R", (), {
        "left": 100,
        "top": 200,
        "width": 800,
        "height": 600,
        "right": 900,
        "bottom": 800,
    })())

    def fake_click(x, y, button="left", double=False, hwnd=None):
        calls.append((x, y, button, double, hwnd))
        return {"ok": True, "x": x, "y": y, "via": "sendinput"}

    monkeypatch.setattr("winpilot.agent.desktop_tools.executor.click", fake_click)

    result = Session()._t_coordinate_click_test({
        "x": 321,
        "y": 456,
        "expect_text": "CALIBRATION_OK",
    })
    payload = json.loads(result.text)

    assert calls == [(321, 456, "left", False, 99)]
    assert payload["ok"] is True
    assert payload["real_mouse_path"] is True
    assert payload["verified"] is True
    assert payload["requested"] == {"x": 321, "y": 456}
    assert payload["target_window"]["requested_relative"] == {"x": 221, "y": 256, "inside": True}
