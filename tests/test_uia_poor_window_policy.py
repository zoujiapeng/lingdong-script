from __future__ import annotations

from agent.supervisor import Supervisor


def test_uia_poor_window_text_detection() -> None:
    assert Supervisor._is_uia_poor_window_text("Electron host window")
    assert Supervisor._is_uia_poor_window_text("WebView2 surface")
    assert Supervisor._is_uia_poor_window_text("CEF webview host")
    assert Supervisor._is_uia_poor_window_text("Qt custom-drawn panel")
    assert not Supervisor._is_uia_poor_window_text("Notepad")
    assert not Supervisor._is_uia_poor_window_text("Calculator")
