from winpilot.agent.tools import ToolSession
from types import SimpleNamespace


def test_resolve_hwnd_prefers_live_session_target(monkeypatch):
    session = ToolSession()
    session.hwnd = 100
    session.target_hint = "Sticky Notes"

    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindow", lambda hwnd: True)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetForegroundWindow", lambda: 200)
    monkeypatch.setattr(session, "_same_window_process", lambda hwnd, target: False)

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
    monkeypatch.setattr(session, "_same_window_process", lambda hwnd, target: False)

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


def test_resolve_hwnd_promotes_owned_dialog_even_with_explicit_parent(monkeypatch):
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

    assert session._resolve_hwnd({"hwnd": 100}) == 200
    assert session.hwnd == 200


def test_resolve_hwnd_ignores_dead_explicit_dialog_and_recovers_by_hint(monkeypatch):
    session = ToolSession()
    session.hwnd = 200
    session.target_hint = "Notepad"

    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindow", lambda hwnd: hwnd == 100)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindowVisible", lambda hwnd: hwnd == 100)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetForegroundWindow", lambda: 100)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetAncestor", lambda hwnd, flag: hwnd)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetClassName", lambda hwnd: "Notepad")
    monkeypatch.setattr("winpilot.agent.tools.win32._window_text", lambda hwnd: "result.txt - Notepad")

    assert session._resolve_hwnd({"hwnd": 200}) == 100
    assert session.hwnd == 100


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


def test_resolve_hwnd_adopts_same_process_foreground_sibling(monkeypatch):
    session = ToolSession()
    session.hwnd = 100
    session.target_hint = "Old page title"

    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindow", lambda hwnd: hwnd in {100, 200})
    monkeypatch.setattr("winpilot.agent.tools.win32gui.IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetForegroundWindow", lambda: 200)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetAncestor", lambda hwnd, flag: hwnd)
    monkeypatch.setattr("winpilot.agent.tools.win32gui.GetWindow", lambda hwnd, flag: 0)
    monkeypatch.setattr("winpilot.agent.tools.win32._window_text", lambda hwnd: "New page title")
    monkeypatch.setattr(session, "_same_window_process", lambda hwnd, target: (hwnd, target) == (200, 100))

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


def test_recover_bound_target_window_uses_process_identity_when_title_changes(monkeypatch):
    session = ToolSession()
    session.hwnd = 100
    session.target_hint = "Example App"
    session._target_binding_verified = True
    session._target_process_id = 10
    session._target_process_name = "example.exe"
    session._target_window_class = "OldWindow"

    monkeypatch.setattr(
        "winpilot.agent.tools.win32gui.IsWindow",
        lambda hwnd: hwnd == 300,
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32gui.IsWindowVisible",
        lambda hwnd: hwnd == 300,
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32gui.GetForegroundWindow",
        lambda: 300,
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32gui.GetClassName",
        lambda _hwnd: "NewWindow",
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32process.GetWindowThreadProcessId",
        lambda _hwnd: (1, 30),
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32._process_name",
        lambda pid: "example.exe" if pid == 30 else "",
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32.list_windows",
        lambda: [{
            "hwnd": 300,
            "pid": 30,
            "title": "Completely different page title",
            "class_name": "NewWindow",
        }],
    )
    monkeypatch.setattr(session, "_bind_perception_target", lambda _hwnd: None)

    assert session._recover_bound_target_window() == 300
    assert session.hwnd == 300
    assert session.target_hint == "Example App"
    assert session._last_scope_recovery["reason"] == "same_application_window_rebound"


def test_refresh_bound_target_follows_matching_foreground_successor(monkeypatch):
    session = ToolSession()
    session.hwnd = 100
    session.target_hint = "Example App"
    session._target_binding_verified = True

    monkeypatch.setattr(
        "winpilot.agent.tools.win32gui.IsWindow",
        lambda hwnd: hwnd in {100, 300},
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32gui.IsWindowVisible",
        lambda hwnd: hwnd in {100, 300},
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32gui.GetForegroundWindow", lambda: 300,
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32gui.GetAncestor", lambda hwnd, _flag: hwnd,
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32gui.GetClassName", lambda _hwnd: "AppWindow",
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32._window_text",
        lambda hwnd: "Example App" if hwnd == 300 else "Starting...",
    )
    monkeypatch.setattr(session, "_bind_perception_target", lambda _hwnd: None)
    monkeypatch.setattr(
        "winpilot.agent.tools.win32process.GetWindowThreadProcessId",
        lambda hwnd: (1, hwnd),
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32._process_name", lambda _pid: "example.exe",
    )

    assert session._refresh_bound_target_window() == 300
    assert session.hwnd == 300
    assert session._last_scope_recovery == {
        "reason": "foreground_application_successor_rebound",
        "previous_target_hwnd": 100,
        "target_hwnd": 300,
        "target_title": "Example App",
        "target_class": "AppWindow",
        "hint_match": True,
        "related_window": False,
        "same_process": False,
        "authoritative_frame_required": True,
    }


def test_refresh_bound_target_keeps_live_binding_for_unrelated_foreground(monkeypatch):
    session = ToolSession()
    session.hwnd = 100
    session.target_hint = "Example App"
    session._target_binding_verified = True

    monkeypatch.setattr(
        "winpilot.agent.tools.win32gui.IsWindow",
        lambda hwnd: hwnd in {100, 400},
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32gui.IsWindowVisible",
        lambda hwnd: hwnd in {100, 400},
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32gui.GetForegroundWindow", lambda: 400,
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32gui.GetAncestor", lambda hwnd, _flag: hwnd,
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32gui.GetClassName", lambda _hwnd: "OtherWindow",
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.win32._window_text", lambda _hwnd: "Unrelated App",
    )
    monkeypatch.setattr(session, "_same_window_process", lambda *_args: False)
    monkeypatch.setattr(session, "_window_related_to_target", lambda *_args: False)

    assert session._refresh_bound_target_window() == 100
    assert session.hwnd == 100


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
