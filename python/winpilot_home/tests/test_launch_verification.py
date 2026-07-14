import json
from types import SimpleNamespace


def test_open_app_focuses_existing_visible_window_without_launch(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    focused = []
    session = ToolSession(task="open the editor")
    session._window_alive = lambda hwnd: bool(hwnd)
    session._t_launch = lambda _args: (_ for _ in ()).throw(AssertionError("must not launch"))
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(find_app=lambda _name: {
            "running": True,
            "process_count": 2,
            "windows": [
                {"hwnd": 11, "title": "Editor helper", "visible": False},
                {"hwnd": 22, "title": "Editor", "visible": True},
            ],
        }),
    )
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(focus_window=lambda hwnd: focused.append(hwnd) or {"ok": True, "hwnd": hwnd}),
    )

    data = json.loads(session._t_open_app({"name": "Editor"}).text)

    assert data["ok"] is True
    assert data["route"] == "existing_visible_window"
    assert data["target_hwnd"] == 22
    assert session.hwnd == 22
    assert focused == [22]
    assert "newest attached frame or structured observation" in data["fast_path_advice"]
    assert "Observe it once" not in data["fast_path_advice"]


def test_open_app_marks_existing_blank_writer_as_ready_without_new_click(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="Use WPS Writer to create a report")
    session._window_alive = lambda hwnd: bool(hwnd)
    session._resolve_launch_target = lambda _name: {
        "name": "WPS Writer",
        "target": r"C:\\Program Files\\Kingsoft\\office6\\wps.exe",
        "kind": "exe",
    }
    session._t_launch = lambda _args: (_ for _ in ()).throw(AssertionError("must not relaunch"))
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(find_app=lambda _name: {
            "running": True,
            "process_count": 1,
            "windows": [
                {"hwnd": 22, "title": "文字文稿1 - WPS Office", "visible": True},
            ],
        }),
    )
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(focus_window=lambda hwnd: {"ok": True, "hwnd": hwnd}),
    )

    data = json.loads(session._t_open_app({"name": "WPS Writer"}).text)

    assert data["blank_document_ready"] is True
    assert data["document_surface_ready"] is True
    assert data["needs_observe"] is False
    assert session._new_blank_applied is True
    assert "do not click new" in data["fast_path_advice"].casefold()


def test_wps_home_title_is_not_mistaken_for_blank_document_surface():
    from winpilot.agent.tools import ToolSession

    component = ("writer", ("文字", "文档", "document", "writer"))

    assert ToolSession._blank_document_surface_ready(
        component,
        "文档 - WPS Office",
    ) is False
    assert ToolSession._blank_document_surface_ready(
        component,
        "文字文稿1 - WPS Office",
    ) is True


def test_writer_home_is_exposed_as_non_editable_document_stage(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="create a report")
    session._window_alive = lambda hwnd: bool(hwnd)
    session._resolve_launch_target = lambda _name: {
        "name": "WPS Writer",
        "target": r"C:\\Program Files\\Kingsoft\\office6\\wps.exe",
    }
    monkeypatch.setattr(tools, "win32", SimpleNamespace(find_app=lambda _name: {
        "running": True,
        "process_count": 1,
        "windows": [{"hwnd": 22, "title": "文档 - WPS Office", "visible": True}],
    }))
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(focus_window=lambda hwnd: {"ok": True, "hwnd": hwnd}),
    )

    data = json.loads(session._t_open_app({"name": "WPS Writer"}).text)

    assert data["document_surface_ready"] is False
    assert data["workspace_stage"] == "application_home"
    assert session._document_surface_ready is False
    assert not hasattr(session, "_new_blank_applied")


def test_new_document_hub_title_is_also_non_editable_stage():
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="create a report")
    payload = {"title": "新建文档 - WPS Office"}
    component = ("writer", ("文字", "文档", "document", "writer"))

    session._mark_blank_document_surface(payload, component)

    assert payload["document_surface_ready"] is False
    assert session._document_surface_ready is False


def test_open_app_reuses_title_matched_window_when_process_snapshot_misses(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    focused = []
    session = ToolSession(task="continue in Chrome")
    session._window_alive = lambda hwnd: bool(hwnd)
    session._t_launch = lambda _args: (_ for _ in ()).throw(AssertionError("must not launch duplicate"))
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(
            find_app=lambda _name: {
                "running": False,
                "process_count": 0,
                "windows": [],
            },
            find_window=lambda _name: {
                "hwnd": 33,
                "title": "Product specifications - Google Chrome",
                "visible": True,
            },
        ),
    )
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(focus_window=lambda hwnd: focused.append(hwnd) or {"ok": True, "hwnd": hwnd}),
    )

    data = json.loads(session._t_open_app({"name": "Chrome"}).text)

    assert data["route"] == "existing_visible_window"
    assert data["target_hwnd"] == 33
    assert session.hwnd == 33
    assert focused == [33]


def test_open_app_reopens_background_process_once_without_shell(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession
    from winpilot.agent.tool_result import ToolResult

    session = ToolSession(task="open the chat app")
    calls = []
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(find_app=lambda _name: {
            "running": True,
            "process_count": 7,
            "windows": [{"hwnd": 11, "title": "Chat", "visible": False}],
        }),
    )
    session._t_launch = lambda args: calls.append(args) or ToolResult(json.dumps({
        "ok": True,
        "verified": True,
        "target_hwnd": 44,
        "launch_window": {"hwnd": 44, "title": "Chat"},
    }))

    data = json.loads(session._t_open_app({"name": "Chat"}).text)

    assert data["ok"] is True
    assert data["verified"] is True
    assert data["route"] == "reopen_background_app"
    assert data["target_hwnd"] == 44
    assert calls == [{"command": "Chat"}]
    assert "shell" in data["fast_path_advice"].casefold()


def test_open_app_failure_advice_does_not_require_an_unexposed_scan_tool(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="open MissingApp")
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(
            find_app=lambda _name: {
                "running": False,
                "process_count": 0,
                "windows": [],
            },
            find_window=lambda _name: None,
        ),
    )
    session._t_launch = lambda _args: ToolResult(json.dumps({
        "ok": False,
        "verified": False,
        "error": "no visible window",
    }))

    data = json.loads(session._t_open_app({"name": "MissingApp"}).text)

    assert data["ok"] is False
    assert data["needs_llm_reason"] is True
    assert "scan_windows" not in data["fast_path_advice"]
    assert "exposed window-list/focus recovery tool" in data["fast_path_advice"]


def test_resolve_launch_target_accepts_registered_multiword_application_name(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    expected = {
        "name": "WPS Spreadsheets",
        "target": r"C:\\Program Files\\Kingsoft\\office6\\et.exe",
        "kind": "exe",
    }
    monkeypatch.setattr(
        tools,
        "apps",
        SimpleNamespace(resolve=lambda query: [expected] if query == "WPS Spreadsheets" else []),
    )

    assert ToolSession(task="open spreadsheet")._resolve_launch_target("WPS Spreadsheets") == expected


def test_scan_windows_no_match_upgrades_open_intent_to_open_app(monkeypatch):
    from winpilot.agent import window_tools
    from winpilot.agent.tools import ToolSession
    from winpilot.agent.tool_result import ToolResult

    session = ToolSession(task="open Chat and send a message")
    session._t_open_app = lambda args: ToolResult(json.dumps({
        "ok": True,
        "verified": True,
        "target_hwnd": 77,
        "app": args["name"],
    }))
    monkeypatch.setattr(window_tools.win32, "list_windows", lambda: [])
    monkeypatch.setattr(
        window_tools.win32,
        "find_app",
        lambda _query: {"running": False, "windows": []},
    )

    data = json.loads(session._t_scan_windows({"query": "Chat"}).text)

    assert data["ok"] is True
    assert data["target_hwnd"] == 77
    assert data["triggered_by"] == "scan_windows:no_match"
    assert "do not call list_windows" in data["fast_path_advice"]


def test_scan_windows_does_not_launch_dialog_label(monkeypatch):
    from winpilot.agent import window_tools
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="The active Save As dialog is already open")
    session._t_open_app = lambda _args: (_ for _ in ()).throw(
        AssertionError("a dialog label must not be launched as an application")
    )
    monkeypatch.setattr(window_tools.win32, "list_windows", lambda: [])
    monkeypatch.setattr(
        window_tools.win32,
        "find_app",
        lambda _query: {"running": False, "windows": []},
    )

    result = session._t_scan_windows({"query": "Save As"})

    assert "No visible or running windows match" in result.text


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


def test_launch_does_not_rebind_unchanged_prior_foreground(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="open a GUI app")
    session.hwnd = 777
    session.target_hint = "Lingdong"
    session._resolve_launch_target = lambda _command: None
    session._window_alive = lambda hwnd: bool(hwnd)
    session._wait_launch_visible_window = lambda *args, **kwargs: None
    session._wait_url_launch_visible_window = lambda *args, **kwargs: None

    monkeypatch.setattr(
        tools,
        "verify",
        SimpleNamespace(
            run_with_verify=lambda *args, **kwargs: {
                "ok": True,
                "verified": False,
                "post_state": {"foreground_hwnd": 777, "foreground_title": "Lingdong"},
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
            find_app=lambda query: {"running": False, "process_count": 0, "windows": []},
            find_window=lambda title: None,
        ),
    )
    monkeypatch.setattr(tools.win32gui, "GetForegroundWindow", lambda: 777)

    data = json.loads(session._t_launch({"command": "missing-app"}).text)

    assert data["ok"] is True
    assert data["verified"] is False
    assert "target_hwnd" not in data
    assert data["foreground_matches_target"] is False


def test_launch_rebinds_shared_suite_host_to_requested_spreadsheet_component(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    foreground = {"hwnd": 3868392}
    focused = []
    session = ToolSession(task="open WPS Spreadsheets")
    session.hwnd = 3868392
    session.target_hint = "Old document - WPS Office"
    session._window_alive = lambda hwnd: bool(hwnd)
    session._resolve_launch_target = lambda _command: {
        "name": "WPS Spreadsheets",
        "target": r"C:\\Program Files\\Kingsoft\\office6\\et.exe",
        "kind": "exe",
    }

    monkeypatch.setattr(
        tools,
        "verify",
        SimpleNamespace(
            run_with_verify=lambda *args, **kwargs: {
                "ok": True,
                "verified": False,
                "post_state": {
                    "foreground_hwnd": 3868392,
                    "foreground_title": "工作簿1 - WPS Office",
                },
            },
            post_state_summary=lambda: {
                "foreground_hwnd": foreground["hwnd"],
                "foreground_title": "工作簿1 - WPS Office",
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
        SimpleNamespace(focus_window=focus_window),
    )
    monkeypatch.setattr(
        tools,
        "apps",
        SimpleNamespace(launch_target=lambda resolved: {
            "ok": True,
            "target": resolved["target"],
        }),
    )
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(
            find_app=lambda query: {
                "running": True,
                "process_count": 1,
                "windows": [
                    {"hwnd": 2098894, "title": "WPS", "visible": True, "pid": 17900},
                ],
            },
            list_windows=lambda: [
                {"hwnd": 2098894, "title": "WPS", "visible": True, "pid": 17900},
                {"hwnd": 3868392, "title": "工作簿1 - WPS Office", "visible": True, "pid": 14740},
            ],
            find_window=lambda _title: None,
        ),
    )
    monkeypatch.setattr(tools.win32gui, "GetForegroundWindow", lambda: foreground["hwnd"])

    data = json.loads(session._t_launch({"command": "WPS Spreadsheets"}).text)

    assert data["verified"] is True
    assert data["verify_method"] == "win32-component-window"
    assert data["target_hwnd"] == 3868392
    assert data["launch_window"]["query"] == "resolved-spreadsheet-host"
    assert session.hwnd == 3868392
    assert focused == [3868392]


def test_writer_component_never_binds_to_visible_spreadsheet_host(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="open WPS Writer")
    session.hwnd = 332776
    session._window_alive = lambda hwnd: bool(hwnd)
    session._resolve_launch_target = lambda _command: {
        "name": "WPS Writer",
        "target": r"C:\Program Files\Kingsoft\office6\wps.exe",
        "kind": "exe",
        "arguments": ["/prometheus", "/wps"],
    }
    focused = []
    monkeypatch.setattr(
        tools,
        "verify",
        SimpleNamespace(
            run_with_verify=lambda *args, **kwargs: {
                "ok": True,
                "verified": False,
                "post_state": {
                    "foreground_hwnd": 332776,
                    "foreground_title": "工作簿36 - WPS Office",
                },
            },
            post_state_summary=lambda: {
                "foreground_hwnd": 141694,
                "foreground_title": "文字文稿1 - WPS Office",
            },
        ),
    )
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(
            focus_window=lambda hwnd: focused.append(hwnd) or {"ok": True, "hwnd": hwnd},
        ),
    )
    monkeypatch.setattr(
        tools,
        "apps",
        SimpleNamespace(launch_target=lambda resolved: {
            "ok": True,
            "target": resolved["target"],
            "arguments": resolved["arguments"],
        }),
    )
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(
            list_windows=lambda: [
                {"hwnd": 332776, "title": "工作簿36 - WPS Office", "pid": 12780},
                {"hwnd": 141694, "title": "文字文稿1 - WPS Office", "pid": 20060},
            ],
            find_window=lambda _title: None,
        ),
    )
    monkeypatch.setattr(tools.win32gui, "GetForegroundWindow", lambda: 141694)

    data = json.loads(session._t_launch({"command": "WPS Writer"}).text)

    assert data["verified"] is True
    assert data["target_hwnd"] == 141694
    assert data["launch_window"]["query"] == "resolved-writer-host"
    assert focused == [141694]


def test_existing_component_window_filter_rejects_wrong_wps_component():
    from winpilot.agent.tools import ToolSession

    state = {
        "windows": [
            {"hwnd": 1, "title": "工作簿1 - WPS Office", "visible": True},
            {"hwnd": 2, "title": "文字文稿1 - WPS Office", "visible": True},
        ]
    }

    assert ToolSession._best_visible_app_window(state, "WPS Writer")["hwnd"] == 2
    assert ToolSession._best_visible_app_window(
        {"windows": [state["windows"][0]]},
        "WPS Writer",
    ) is None


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
