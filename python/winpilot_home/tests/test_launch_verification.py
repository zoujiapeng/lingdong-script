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
