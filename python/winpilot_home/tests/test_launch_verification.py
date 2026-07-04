import json
from types import SimpleNamespace


def test_launch_verifies_visible_process_window(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="open a GUI app")
    session._resolve_launch_target = lambda _command: None
    session._window_alive = lambda hwnd: bool(hwnd)

    monkeypatch.setattr(
        tools,
        "verify",
        SimpleNamespace(
            run_with_verify=lambda *args, **kwargs: {
                "ok": True,
                "verified": False,
                "verify_detail": 'foreground window title "Codex" does not contain "画图"',
                "post_state": {"foreground_hwnd": 999},
            }
        ),
    )
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(
            launch=lambda command: {"ok": True, "command": command},
            focus_window=lambda hwnd: {"ok": True, "hwnd": hwnd},
        ),
    )
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(
            find_app=lambda query: {
                "running": True,
                "process_count": 1,
                "windows": [
                    {
                        "hwnd": 123,
                        "title": "Untitled - Paint",
                        "visible": True,
                        "pid": 456,
                    }
                ],
            },
            find_window=lambda title: None,
        ),
    )

    result = session._t_launch({
        "command": "mspaint",
        "expect": {"window_title": "画图"},
    })
    data = json.loads(result.text)

    assert data["verified"] is True
    assert data["verify_method"] == "win32-process"
    assert data["target_hwnd"] == 123
    assert session.hwnd == 123
    assert session.target_hint == "Untitled - Paint"


def test_launch_does_not_verify_hidden_process(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="open a GUI app")
    session._resolve_launch_target = lambda _command: None
    session._window_alive = lambda hwnd: bool(hwnd)
    session._wait_launch_visible_window = lambda *args, **kwargs: None

    monkeypatch.setattr(
        tools,
        "verify",
        SimpleNamespace(
            run_with_verify=lambda *args, **kwargs: {
                "ok": True,
                "verified": False,
                "verify_detail": "window not detected",
                "post_state": {"foreground_hwnd": 999},
            }
        ),
    )
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(
            launch=lambda command: {"ok": True, "command": command},
            focus_window=lambda hwnd: {"ok": True, "hwnd": hwnd},
        ),
    )
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(
            find_app=lambda query: {
                "running": True,
                "process_count": 1,
                "windows": [
                    {"hwnd": 123, "title": "", "visible": False, "pid": 456}
                ],
            },
            find_window=lambda title: None,
        ),
    )

    result = session._t_launch({
        "command": "dummyapp",
        "expect": {"window_title": "Dummy"},
    })
    data = json.loads(result.text)

    assert data["verified"] is False
    assert "launch_window" not in data


def test_launch_url_fallback_prefers_matching_browser_window_not_prior_foreground(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    foreground = {"hwnd": 1639798}
    focused = []
    session = ToolSession(task="open web target")
    session._resolve_launch_target = lambda _command: None
    session._window_alive = lambda hwnd: bool(hwnd)
    session.hwnd = 1639798

    monkeypatch.setattr(
        tools,
        "verify",
        SimpleNamespace(
            run_with_verify=lambda *args, **kwargs: {
                "ok": True,
                "verified": False,
                "verify_detail": 'foreground window title "Codex" does not contain "coord1"',
                "post_state": {"foreground_hwnd": 1639798, "foreground_title": "Codex"},
            },
            post_state_summary=lambda: {
                "foreground_hwnd": foreground["hwnd"],
                "foreground_title": "WinPilot GUI Agent Target - Google Chrome",
            },
        ),
    )

    def focus_window(hwnd):
        focused.append(hwnd)
        foreground["hwnd"] = hwnd
        return {"ok": True, "hwnd": hwnd}

    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(
            launch=lambda command: {
                "ok": True,
                "command": command,
                "target": command,
                "via": "url",
            },
            focus_window=focus_window,
        ),
    )
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(
            find_app=lambda query: {"running": False, "process_count": 0, "windows": []},
            find_window=lambda title: None,
            list_windows=lambda: [
                {
                    "hwnd": 1639798,
                    "title": "Codex",
                    "class_name": "Chrome_WidgetWin_1",
                    "rect": [-9, -9, 1929, 1029],
                    "pid": 10,
                },
                {
                    "hwnd": 7013712,
                    "title": "WinPilot GUI Agent Target - Google Chrome",
                    "class_name": "Chrome_WidgetWin_1",
                    "rect": [-9, -9, 1929, 1029],
                    "pid": 20,
                },
            ],
        ),
    )
    monkeypatch.setattr(tools.win32gui, "GetForegroundWindow", lambda: foreground["hwnd"])

    result = session._t_launch({
        "command": "http://127.0.0.1:8877/gui_agent_target.html?v=coord1",
        "expect": {"window_title": "coord1"},
    })
    data = json.loads(result.text)

    assert data["verified"] is True
    assert data["verify_method"] == "win32-url-window"
    assert data["target_hwnd"] == 7013712
    assert focused == [7013712]
    assert session.hwnd == 7013712


def test_launch_uses_single_attempt_before_process_window_fallback(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    captured_verify = {}
    session = ToolSession(task="open a GUI app")
    session._resolve_launch_target = lambda _command: None
    session._window_alive = lambda hwnd: bool(hwnd)

    def fake_run_with_verify(*args, **kwargs):
        captured_verify["kwargs"] = kwargs
        return {
            "ok": True,
            "verified": False,
            "verify_detail": "foreground window title did not match",
            "post_state": {"foreground_hwnd": 999},
        }

    monkeypatch.setattr(
        tools,
        "verify",
        SimpleNamespace(run_with_verify=fake_run_with_verify),
    )
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(
            launch=lambda command: {"ok": True, "command": command},
            focus_window=lambda hwnd: {"ok": True, "hwnd": hwnd},
        ),
    )
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(
            find_app=lambda query: {
                "running": True,
                "process_count": 1,
                "windows": [
                    {"hwnd": 321, "title": "Dummy App", "visible": True, "pid": 654}
                ],
            },
            find_window=lambda title: None,
        ),
    )

    result = session._t_launch({
        "command": "dummyapp",
        "expect": {"window_title": "Dummy"},
    })
    data = json.loads(result.text)

    assert data["verified"] is True
    assert data["verify_method"] == "win32-process"
    assert captured_verify["kwargs"]["retryable"] is False
    assert captured_verify["kwargs"]["timeout_s"] == 3.0


def test_launch_focuses_target_window_even_when_verify_already_passed(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    foreground = {"hwnd": 999}
    focused = []
    session = ToolSession(task="open notepad")
    session._resolve_launch_target = lambda _command: None
    session._window_alive = lambda hwnd: bool(hwnd)

    monkeypatch.setattr(
        tools,
        "verify",
        SimpleNamespace(
            run_with_verify=lambda *args, **kwargs: {
                "ok": True,
                "verified": True,
                "verify_detail": "all conditions met",
                "post_state": {"foreground_hwnd": 999, "foreground_title": "Codex"},
            },
            post_state_summary=lambda: {
                "foreground_hwnd": foreground["hwnd"],
                "foreground_title": "Untitled - Notepad",
            },
        ),
    )

    def focus_window(hwnd):
        focused.append(hwnd)
        foreground["hwnd"] = hwnd
        return {"ok": True, "hwnd": hwnd}

    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(
            launch=lambda command: {"ok": True, "command": command},
            focus_window=focus_window,
        ),
    )
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(
            find_app=lambda query: {"running": False, "process_count": 0, "windows": []},
            find_window=lambda title: {"hwnd": 123, "title": "Untitled - Notepad"},
        ),
    )
    monkeypatch.setattr(tools.win32gui, "GetForegroundWindow", lambda: foreground["hwnd"])

    result = session._t_launch({
        "command": "notepad",
        "expect": {"window_appears": "Notepad"},
    })
    data = json.loads(result.text)

    assert focused == [123]
    assert session.hwnd == 123
    assert data["target_hwnd"] == 123
    assert data["foreground_matches_target"] is True
    assert data["post_state"]["foreground_hwnd"] == 123


def test_launch_focuses_window_title_expect_when_verify_passed(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    foreground = {"hwnd": 999}
    focused = []
    session = ToolSession(task="open web target")
    session._resolve_launch_target = lambda _command: None
    session._window_alive = lambda hwnd: bool(hwnd)

    monkeypatch.setattr(
        tools,
        "verify",
        SimpleNamespace(
            run_with_verify=lambda *args, **kwargs: {
                "ok": True,
                "verified": True,
                "post_state": {"foreground_hwnd": 999, "foreground_title": "Codex"},
            },
            post_state_summary=lambda: {
                "foreground_hwnd": foreground["hwnd"],
                "foreground_title": "WinPilot GUI Agent Target - Google Chrome",
            },
        ),
    )

    def focus_window(hwnd):
        focused.append(hwnd)
        foreground["hwnd"] = hwnd
        return {"ok": True, "hwnd": hwnd}

    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(
            launch=lambda command: {"ok": True, "command": command, "via": "url"},
            focus_window=focus_window,
        ),
    )
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(
            find_app=lambda query: {"running": False, "process_count": 0, "windows": []},
            find_window=lambda title: {"hwnd": 123, "title": "WinPilot GUI Agent Target - Google Chrome"},
        ),
    )
    monkeypatch.setattr(tools.win32gui, "GetForegroundWindow", lambda: foreground["hwnd"])

    result = session._t_launch({
        "command": "http://127.0.0.1:8877/gui_agent_target.html",
        "expect": {"window_title": "gui_agent_target"},
    })
    data = json.loads(result.text)

    assert focused == [123]
    assert session.hwnd == 123
    assert data["target_hwnd"] == 123
    assert data["foreground_matches_target"] is True


def test_launch_window_title_fallback_marks_verified(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="open web target")
    session._resolve_launch_target = lambda _command: None
    session._window_alive = lambda hwnd: bool(hwnd)
    session._ensure_screen_watch = lambda reason="": None

    monkeypatch.setattr(
        tools,
        "verify",
        SimpleNamespace(
            run_with_verify=lambda *args, **kwargs: {
                "ok": True,
                "verified": False,
                "verify_detail": "initial foreground did not match",
                "post_state": {"foreground_hwnd": 999, "foreground_title": "Codex"},
            },
            post_state_summary=lambda: {
                "foreground_hwnd": 123,
                "foreground_title": "WinPilot GUI Agent Target - Google Chrome",
            },
        ),
    )
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(
            launch=lambda command: {"ok": True, "command": command, "via": "url"},
            focus_window=lambda hwnd: {"ok": True, "hwnd": hwnd},
        ),
    )
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(
            find_app=lambda query: {"running": False, "process_count": 0, "windows": []},
            find_window=lambda title: {"hwnd": 123, "title": "WinPilot GUI Agent Target - Google Chrome"},
        ),
    )
    monkeypatch.setattr(tools.win32gui, "GetForegroundWindow", lambda: 123)

    result = session._t_launch({
        "command": "http://127.0.0.1:8877/gui_agent_target.html",
        "expect": {"window_title": "gui_agent_target"},
    })
    data = json.loads(result.text)

    assert data["verified"] is True
    assert data["verify_method"] == "win32-title"
    assert data["target_hwnd"] == 123
