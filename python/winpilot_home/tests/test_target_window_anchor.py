from winpilot.agent.tools import ToolSession
from types import SimpleNamespace


def test_resolve_hwnd_prefers_live_session_target(monkeypatch):
    session = ToolSession()
    session.hwnd = 100
    session.target_hint = "Sticky Notes"

    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindow", lambda hwnd: True)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetForegroundWindow", lambda: 200)

    assert session._resolve_hwnd({}) == 100


def test_action_hwnd_prefers_session_target_over_unrelated_foreground(monkeypatch):
    session = ToolSession()
    session.hwnd = 100

    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindow", lambda hwnd: hwnd in {100, 200})
    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetForegroundWindow", lambda: 200)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetAncestor", lambda hwnd, flag: hwnd)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetClassName", lambda hwnd: "Window")
    monkeypatch.setattr("winpilot.agent.tools.win32._window_text", lambda hwnd: "Codex")

    assert session._action_hwnd() == 100


def test_resolve_hwnd_prefers_owned_foreground_dialog(monkeypatch):
    session = ToolSession()
    session.hwnd = 100
    session.target_hint = "Notepad"

    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindow", lambda hwnd: hwnd in {100, 200})
    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetForegroundWindow", lambda: 200)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetAncestor", lambda hwnd, flag: hwnd)
    monkeypatch.setattr(
        "winpilot.agent.tools.win32gui.GetWindow",
        lambda hwnd, flag: 100 if hwnd == 200 else 0,
    )
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetClassName", lambda hwnd: "#32770")
    monkeypatch.setattr(
        "winpilot.agent.tools.win32._window_text",
        lambda hwnd: "另存为" if hwnd == 200 else "无标题 - 记事本",
    )

    assert session._resolve_hwnd({}) == 200
    assert session.hwnd == 200
    assert session.target_hint == "Notepad"


def test_resolve_hwnd_prefers_matching_foreground_over_stale_target(monkeypatch):
    session = ToolSession()
    session.hwnd = 100
    session.target_hint = "Calculator"

    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindow", lambda hwnd: True)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetForegroundWindow", lambda: 200)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetAncestor", lambda hwnd, flag: hwnd)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetClassName", lambda hwnd: "ApplicationFrameWindow")
    monkeypatch.setattr(
        "winpilot.agent.tools.win32._window_text",
        lambda hwnd: "Calculator" if hwnd == 200 else "Calculator",
    )

    assert session._resolve_hwnd({}) == 200
    assert session.hwnd == 200


def test_resolve_hwnd_recovers_target_by_title_before_foreground(monkeypatch):
    session = ToolSession()
    session.hwnd = 100
    session.target_hint = "Sticky Notes"

    def is_window(hwnd):
        return hwnd in {200, 300}

    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindow", is_window)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetForegroundWindow", lambda: 200)
    monkeypatch.setattr(
        "winpilot.agent.tools.win32.list_windows",
        lambda: [
            {"hwnd": 200, "title": "Clock", "class_name": "ApplicationFrameWindow"},
            {"hwnd": 300, "title": "Sticky Notes (new)", "class_name": "Framework::CMemoryFrame"},
        ],
    )

    assert session._resolve_hwnd({}) == 300
    assert session.hwnd == 300


def test_read_text_merges_forced_ocr(monkeypatch):
    session = ToolSession()
    session.hwnd = 100

    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindow", lambda hwnd: True)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(
        "winpilot.agent.tools.fusion.observe",
        lambda *a, **k: SimpleNamespace(
            elements=[SimpleNamespace(text="新建笔记")],
            region=SimpleNamespace(),
        ),
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.ocr.collect",
        lambda *a, **k: [SimpleNamespace(text="锚定修复测试")],
    )

    result = session._t_read_text({})

    assert "新建笔记" in result.text
    assert "锚定修复测试" in result.text
