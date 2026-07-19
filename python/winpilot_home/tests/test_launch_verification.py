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


def test_redundant_open_app_reuses_stable_identity_when_window_title_changes(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="continue in a media app")
    session.hwnd = 44
    session._target_binding_verified = True
    session._target_application_identity = {
        "hwnd": 44,
        "name": "Media Player",
        "terms": ["mediaplayer"],
        "kind": "appid",
        "target": "Vendor.Media!App",
    }
    session._window_alive = lambda hwnd: hwnd == 44
    session._t_launch = lambda _args: (_ for _ in ()).throw(
        AssertionError("a title change must not launch a duplicate")
    )
    focused = []
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(
            focus_window=lambda hwnd: focused.append(hwnd) or {"ok": True, "hwnd": hwnd},
        ),
    )
    monkeypatch.setattr(
        tools.win32,
        "_window_text",
        lambda hwnd: "Current Song - Different Artist",
    )

    data = json.loads(session._t_open_app({"name": "Media Player"}).text)

    assert data["ok"] is True
    assert data["route"] == "existing_bound_application"
    assert data["target_hwnd"] == 44
    assert data["binding_preserved"] is True
    assert focused == [44]


def test_open_app_restores_background_window_without_launching_duplicate(monkeypatch):
    from winpilot.agent import system_tools, tools
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="open the chat app")
    session._window_alive = lambda hwnd: bool(hwnd)
    session._t_launch = lambda _args: (_ for _ in ()).throw(
        AssertionError("must not launch a duplicate process")
    )
    focused = []
    shown = []
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(
            find_app=lambda _name: {
                "running": True,
                "process_count": 7,
                "windows": [{"hwnd": 11, "title": "Chat", "visible": False}],
            },
            find_window=lambda _name: None,
        ),
    )
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(
            focus_window=lambda hwnd: focused.append(hwnd) or {"ok": True, "hwnd": hwnd},
        ),
    )
    monkeypatch.setattr(system_tools.win32gui, "GetWindowText", lambda _hwnd: "Chat")
    monkeypatch.setattr(system_tools.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(system_tools.win32gui, "GetClassName", lambda _hwnd: "ChatWindow")
    monkeypatch.setattr(system_tools.win32gui, "GetWindowRect", lambda _hwnd: (0, 0, 1000, 700))
    monkeypatch.setattr(system_tools.win32gui, "GetWindow", lambda *_args: 0)
    monkeypatch.setattr(system_tools.win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(system_tools.win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(
        system_tools.win32gui,
        "ShowWindow",
        lambda hwnd, mode: shown.append((hwnd, mode)) or True,
    )

    data = json.loads(session._t_open_app({"name": "Chat"}).text)

    assert data["ok"] is True
    assert data["verified"] is True
    assert data["route"] == "existing_hidden_window"
    assert data["target_hwnd"] == 11
    assert focused == [11]
    assert shown and shown[0][0] == 11
    assert "do not launch another instance" in data["fast_path_advice"].casefold()


def test_open_app_refuses_duplicate_when_existing_hidden_window_cannot_restore(monkeypatch):
    from winpilot.agent import system_tools, tools
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="open the chat app")
    session._window_alive = lambda hwnd: bool(hwnd)
    session._t_launch = lambda _args: (_ for _ in ()).throw(
        AssertionError("must not launch a duplicate process")
    )
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(
            find_app=lambda _name: {
                "running": True,
                "process_count": 2,
                "windows": [{"hwnd": 11, "title": "Chat", "visible": False}],
            },
            find_window=lambda _name: None,
        ),
    )
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(focus_window=lambda _hwnd: {"ok": False}),
    )
    monkeypatch.setattr(
        tools,
        "tray",
        SimpleNamespace(click_tray_icon=lambda *_args, **_kwargs: {"ok": False}),
    )
    monkeypatch.setattr(system_tools.win32gui, "GetWindowText", lambda _hwnd: "Chat")
    monkeypatch.setattr(system_tools.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(system_tools.win32gui, "GetClassName", lambda _hwnd: "ChatWindow")
    monkeypatch.setattr(system_tools.win32gui, "GetWindowRect", lambda _hwnd: (0, 0, 1000, 700))
    monkeypatch.setattr(system_tools.win32gui, "GetWindow", lambda *_args: 0)
    monkeypatch.setattr(system_tools.win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(system_tools.win32gui, "IsWindowVisible", lambda _hwnd: False)
    monkeypatch.setattr(system_tools.win32gui, "ShowWindow", lambda *_args: True)

    data = json.loads(session._t_open_app({"name": "Chat"}).text)

    assert data["ok"] is False
    assert data["blocked"] is True
    assert data["failure_type"] == "background_instance_restore_failed"
    assert data["background_recovery"]["duplicate_launch_unsafe"] is True


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
    assert data["failure_type"] == "application_not_found"
    assert data["ranked_candidates"] == []
    assert "fast_path_advice" not in data


def test_open_app_returns_ranked_identity_facts_for_ambiguous_local_matches(
    monkeypatch,
):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="open Comet")
    session._resolve_launch_target = lambda _name: None
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(
            find_app=lambda _name: {"running": False, "windows": []},
            find_window=lambda _name: None,
        ),
    )
    monkeypatch.setattr(
        tools,
        "apps",
        SimpleNamespace(resolve=lambda *_args, **_kwargs: [
            {
                "name": "Comet Canvas",
                "kind": "lnk",
                "target": r"C:\Start\Comet Canvas.lnk",
                "aliases": ["comet-canvas.exe"],
                "match_score": 64,
                "match_kind": "fuzzy",
                "matched_term": "Comet Canvas",
            },
            {
                "name": "Comet Notes",
                "kind": "appid",
                "target": "Vendor.Comet!Notes",
                "match_score": 62,
                "match_kind": "fuzzy",
                "matched_term": "Comet Notes",
            },
        ]),
    )

    data = json.loads(session._t_open_app({"name": "Comet"}).text)

    assert data["failure_type"] == "application_resolution_ambiguous"
    assert [item["display_name"] for item in data["ranked_candidates"]] == [
        "Comet Canvas", "Comet Notes",
    ]
    assert data["ranked_candidates"][0]["launch_kind"] == "lnk"
    assert data["ranked_candidates"][0]["launch_target"].endswith("Canvas.lnk")


def test_non_url_launch_skips_url_window_polling_immediately():
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="open a local app")
    started = __import__("time").monotonic()

    found = session._wait_url_launch_visible_window(
        "Comet Canvas",
        {"target": r"C:\Start\Comet Canvas.lnk"},
        SimpleNamespace(),
        None,
        timeout_s=5.0,
    )

    assert found is None
    assert __import__("time").monotonic() - started < 0.1


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
        "_reuse_checked": True,
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
        "_reuse_checked": True,
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

    assert data["ok"] is False
    assert data["verified"] is False
    assert "target_hwnd" not in data
    assert data["failure_type"] == "application_not_found"


def test_resolved_launch_never_returns_the_previous_bound_window(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="open a resolved desktop app")
    session.hwnd = 777
    session.target_hint = "Command Prompt"
    session._target_binding_verified = True
    session._target_process_id = 123
    session._target_process_name = "cmd.exe"
    session._target_window_class = "ConsoleWindowClass"
    session._resolve_launch_target = lambda _command: {
        "name": "Example Music",
        "target": "Vendor.Package!MUSIC",
        "kind": "appid",
        "aliases": ["Example Music"],
    }
    session._window_alive = lambda hwnd: bool(hwnd)
    session._find_target_window = lambda: None
    session._wait_launch_visible_window = lambda *args, **kwargs: None
    session._wait_url_launch_visible_window = lambda *args, **kwargs: None

    monkeypatch.setattr(
        tools,
        "verify",
        SimpleNamespace(
            run_with_verify=lambda *args, **kwargs: {
                "ok": True,
                "verified": True,
                "name": "Example Music",
                "aliases": ["Example Music"],
                "target": "Vendor.Package!MUSIC",
                "post_state": {
                    "foreground_hwnd": 777,
                    "foreground_title": "Command Prompt",
                },
            }
        ),
    )
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(
            focus_window=lambda hwnd: {"ok": True, "hwnd": hwnd},
        ),
    )
    monkeypatch.setattr(tools.win32gui, "GetForegroundWindow", lambda: 777)

    data = json.loads(session._t_launch({
        "command": "Example Music",
        "_reuse_checked": True,
    }).text)

    assert data["ok"] is True
    assert data["verified"] is False
    assert data["launch_request_verified"] is True
    assert "target_hwnd" not in data
    assert session.hwnd is None
    assert session.target_hint == "Example Music"
    assert session._target_binding_verified is False
    assert session._target_process_id == 0
    assert session._target_process_name == ""
    assert session._target_window_class == ""


def test_synchronous_resolved_target_failure_tries_one_refreshed_distinct_target(
    monkeypatch,
):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    primary = {
        "name": "Comet Player",
        "target": r"C:\Start\Comet Player.lnk",
        "kind": "lnk",
    }
    fallback = {
        "name": "Comet Player",
        "target": "Vendor.Comet!Player",
        "kind": "appid",
    }
    attempts = []
    session = ToolSession(task="open Comet Player")
    session._resolve_launch_target = lambda _command: dict(primary)
    session._visible_window_snapshot = lambda _win32: {}
    session._wait_launch_visible_window = lambda *args, **kwargs: None
    session._find_target_window = lambda: None
    session._window_alive = lambda _hwnd: False

    def launch_target(entry):
        attempts.append(entry["target"])
        if entry["target"] == primary["target"]:
            return {"ok": False, "error": "shortcut target is missing"}
        return {"ok": True, "target": entry["target"], "via": entry["kind"]}

    monkeypatch.setattr(
        tools,
        "apps",
        SimpleNamespace(
            launch_target=launch_target,
            resolve=lambda *_args, **_kwargs: [primary, fallback],
        ),
    )
    monkeypatch.setattr(
        tools,
        "verify",
        SimpleNamespace(run_with_verify=lambda _name, action, **_kwargs: {
            **action(), "verified": False,
        }),
    )
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(find_window=lambda _name: None, list_windows=lambda: []),
    )
    monkeypatch.setattr(tools.win32gui, "GetForegroundWindow", lambda: 0)

    data = json.loads(session._t_launch({
        "command": "Comet Player",
        "_reuse_checked": True,
    }).text)

    assert attempts == [primary["target"], fallback["target"]]
    assert data["resolved_app"] == "Comet Player [appid]"
    assert data["verified"] is False
    assert "target_hwnd" not in data


def test_prelaunch_same_title_window_is_not_launch_ownership(monkeypatch):
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="open Comet Player")
    session._window_alive = lambda hwnd: bool(hwnd)
    monkeypatch.setattr(
        "winpilot.agent.system_tools.win32gui.GetForegroundWindow",
        lambda: 700,
    )
    win32_mod = SimpleNamespace(
        find_app=lambda _query: {"windows": []},
        find_window=lambda _query: {"hwnd": 700, "title": "Comet Player"},
        list_windows=lambda: [{"hwnd": 700, "title": "Comet Player"}],
    )

    found = session._launch_visible_window(
        "Comet Player",
        {"name": "Comet Player", "target": "Vendor.Comet!Player"},
        win32_mod,
        before_windows={700: {"hwnd": 700, "title": "Comet Player"}},
        prelaunch_foreground_hwnd=700,
        prior_target_hwnd=700,
    )

    assert found is None


def test_appid_launch_can_bind_resolver_display_title_without_process_alias(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    foreground = {"hwnd": 777}
    focused = []
    session = ToolSession(task="open a localized Store application")
    session.hwnd = 777
    session.target_hint = "Command Prompt"
    session._resolve_launch_target = lambda _command: {
        "name": "Example Music",
        "target": "Vendor.Package!MUSIC",
        "kind": "appid",
        "aliases": ["Example Music"],
    }
    session._window_alive = lambda hwnd: bool(hwnd)

    monkeypatch.setattr(
        tools,
        "verify",
        SimpleNamespace(
            run_with_verify=lambda *args, **kwargs: {
                "ok": True,
                "verified": False,
                "name": "Example Music",
                "aliases": ["Example Music"],
                "target": "Vendor.Package!MUSIC",
                "post_state": {
                    "foreground_hwnd": 777,
                    "foreground_title": "Command Prompt",
                },
            },
            post_state_summary=lambda: {
                "foreground_hwnd": foreground["hwnd"],
                "foreground_title": "Example Music",
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
        "win32",
        SimpleNamespace(
            find_app=lambda _query: {
                "running": False,
                "process_count": 0,
                "windows": [],
            },
            find_window=lambda title: (
                {"hwnd": 123, "title": "Example Music", "pid": 456}
                if title == "Example Music"
                else None
            ),
        ),
    )
    monkeypatch.setattr(
        tools.win32gui,
        "GetForegroundWindow",
        lambda: foreground["hwnd"],
    )

    data = json.loads(session._t_launch({
        "command": "Example Music",
        "_reuse_checked": True,
    }).text)

    assert data["verified"] is True
    assert data["verify_method"] == "win32-title"
    assert data["target_hwnd"] == 123
    assert data["launch_window"]["query"] == "title:Example Music"
    assert focused == [123]
    assert session.hwnd == 123


def test_resolved_appid_binds_the_unique_new_top_level_window(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    foreground = {"hwnd": 900}
    snapshots = [
        [{"hwnd": 900, "title": "Command Prompt", "pid": 90}],
        [
            {"hwnd": 900, "title": "Command Prompt", "pid": 90},
            {"hwnd": 321, "title": "Now Playing", "pid": 32},
        ],
    ]
    focused = []
    session = ToolSession(task="open a localized Store application")
    session._resolve_launch_target = lambda _command: {
        "name": "Localized Player",
        "target": "Vendor.Package!PLAYER",
        "kind": "appid",
        "aliases": ["Localized Player"],
    }
    session._window_alive = lambda hwnd: bool(hwnd)

    monkeypatch.setattr(
        tools,
        "verify",
        SimpleNamespace(
            run_with_verify=lambda *args, **kwargs: {
                "ok": True,
                "verified": False,
                "name": "Localized Player",
                "aliases": ["Localized Player"],
                "target": "Vendor.Package!PLAYER",
                "post_state": {
                    "foreground_hwnd": 900,
                    "foreground_title": "Command Prompt",
                },
            },
            post_state_summary=lambda: {
                "foreground_hwnd": foreground["hwnd"],
                "foreground_title": "Now Playing",
            },
        ),
    )

    def focus_window(hwnd):
        focused.append(hwnd)
        foreground["hwnd"] = hwnd
        return {"ok": True, "hwnd": hwnd}

    def list_windows():
        if len(snapshots) > 1:
            return snapshots.pop(0)
        foreground["hwnd"] = 321
        return snapshots[0]

    monkeypatch.setattr(tools, "executor", SimpleNamespace(focus_window=focus_window))
    monkeypatch.setattr(
        tools,
        "win32",
        SimpleNamespace(
            find_app=lambda _query: {
                "running": False,
                "process_count": 0,
                "windows": [],
            },
            find_window=lambda _title: None,
            list_windows=list_windows,
        ),
    )
    monkeypatch.setattr(
        tools.win32gui,
        "GetForegroundWindow",
        lambda: foreground["hwnd"],
    )

    data = json.loads(session._t_launch({
        "command": "Localized Player",
        "_reuse_checked": True,
    }).text)

    assert data["verified"] is True
    assert data["verify_method"] == "win32-window-delta"
    assert data["target_hwnd"] == 321
    assert data["launch_window"]["query"] == "launch-window-delta"
    assert data["launch_window"]["causal_new_window"] is True
    assert focused == [321]


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
        "_reuse_checked": True,
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
        "_reuse_checked": True,
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

    foreground = {"hwnd": 999}
    session = ToolSession(task="open web target")
    session._resolve_launch_target = lambda _command: None
    session._window_alive = lambda hwnd: bool(hwnd)
    session._ensure_screen_watch = lambda reason="": None

    monkeypatch.setattr(
        tools,
        "verify",
        SimpleNamespace(
                run_with_verify=lambda *args, **kwargs: (
                    foreground.__setitem__("hwnd", 123)
                    or {
                        "ok": True,
                        "verified": False,
                        "verify_detail": "initial foreground did not match",
                        "post_state": {
                            "foreground_hwnd": 999,
                            "foreground_title": "Codex",
                        },
                    }
                ),
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
                launch=lambda command: (
                    foreground.__setitem__("hwnd", 123)
                    or {"ok": True, "command": command, "via": "url"}
                ),
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
    monkeypatch.setattr(
        tools.win32gui,
        "GetForegroundWindow",
        lambda: foreground["hwnd"],
    )

    result = session._t_launch({
        "command": "http://127.0.0.1:8877/gui_agent_target.html",
        "expect": {"window_title": "gui_agent_target"},
    })
    data = json.loads(result.text)

    assert data["verified"] is True
    assert data["verify_method"] == "win32-title"
    assert data["target_hwnd"] == 123
