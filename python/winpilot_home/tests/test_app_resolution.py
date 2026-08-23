from __future__ import annotations

import json
import sys
from types import SimpleNamespace


def test_app_alias_expansion_returns_only_the_normalized_literal():
    from winpilot.perception.app_aliases import expand

    assert expand("  Ｎｅｂｕｌａ Desk  ") == ["nebula desk"]
    assert expand("星云工作台") == ["星云工作台"]
    assert expand("") == []


def test_app_index_uses_only_generic_discovery_sources(monkeypatch):
    from winpilot.perception import apps

    shortcut = {
        "name": "Nebula Desk",
        "target": r"C:\Start\Nebula Desk.lnk",
        "kind": "lnk",
        "aliases": [r"C:\Programs\Nebula\nebula.exe"],
    }
    start_app = {
        "name": "Orbit Notes",
        "target": "Fictional.OrbitNotes_123!App",
        "kind": "appid",
    }
    app_path = {
        "name": "prism",
        "target": r"C:\Programs\Prism\prism.exe",
        "kind": "exe",
    }
    monkeypatch.setattr(apps, "_cache", None)
    monkeypatch.setattr(apps, "_start_menu_entries", lambda: [shortcut])
    monkeypatch.setattr(apps, "_uwp_entries", lambda: [start_app])
    monkeypatch.setattr(apps, "_app_paths_entries", lambda: [app_path])

    assert apps._index() == [shortcut, start_app, app_path]
    assert not hasattr(apps, "_system_tool_entries")
    assert not hasattr(apps, "_wps_suite_entries")


def test_resolve_fictional_app_by_discovered_display_name(monkeypatch):
    from winpilot.perception import apps

    monkeypatch.setattr(apps, "_index", lambda: [{
        "name": "Nebula Desk",
        "target": "Fictional.NebulaDesk_123!App",
        "kind": "appid",
    }])

    result = apps.resolve("Nebula Desk")

    assert result[0]["name"] == "Nebula Desk"
    assert result[0]["target"] == "Fictional.NebulaDesk_123!App"
    assert result[0]["match_kind"] == "literal_or_alias"


def test_resolve_fictional_app_by_discovered_shortcut_alias(monkeypatch):
    from winpilot.perception import apps

    monkeypatch.setattr(apps, "_index", lambda: [{
        "name": "Aurora Studio",
        "target": r"C:\Start\Aurora Studio.lnk",
        "kind": "lnk",
        "aliases": [r"C:\Programs\Aurora\aurora-render.exe"],
    }])

    result = apps.resolve("aurora-render")

    assert result[0]["name"] == "Aurora Studio"
    assert result[0]["matched_term"].casefold().endswith("aurora-render.exe")
    assert result[0]["match_kind"] == "target_alias"


def test_resolve_keeps_display_typos_but_rejects_fuzzy_path_aliases(
    monkeypatch,
):
    from winpilot.perception import apps, search

    access = {
        "name": "Access",
        "target": (
            r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Access.lnk"
        ),
        "kind": "lnk",
        "aliases": [r"C:\Program Files\Microsoft Office\MSACCESS.EXE"],
    }
    one_drive = {
        "name": "OneDrive",
        "target": r"C:\Start\OneDrive.lnk",
        "kind": "lnk",
        "aliases": ["Microsoft OneDrive"],
    }
    aurora = {
        "name": "Aurora Render",
        "target": r"C:\Start\Aurora Render.lnk",
        "kind": "lnk",
    }
    monkeypatch.setattr(apps, "_cache", None)
    monkeypatch.setattr(apps, "_index", lambda: [access, one_drive, aurora])
    monkeypatch.setattr(
        search,
        "search",
        lambda *args, **kwargs: {"results": []},
    )

    assert "Microsoft" not in apps._entry_terms(access)
    assert apps.resolve("Microsoft Edge") == []
    fuzzy = apps.resolve("Aurroa Render")
    assert fuzzy[0]["name"] == "Aurora Render"
    assert fuzzy[0]["match_kind"] == "fuzzy"


def test_launch_discovered_exe_passes_generic_arguments(monkeypatch):
    from winpilot.perception import apps

    calls = []
    monkeypatch.setattr(
        apps.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    result = apps.launch_target({
        "name": "Prism Workspace",
        "target": r"C:\Programs\Prism\prism.exe",
        "kind": "exe",
        "arguments": ["--workspace", "demo"],
    })

    assert result["ok"] is True
    assert calls[0][0][-2:] == ["--workspace", "demo"]
    assert result["arguments"] == ["--workspace", "demo"]


def test_launch_application_waits_for_and_activates_new_window(monkeypatch):
    from winpilot.perception import apps

    entry = {
        "name": "Nebula Desk",
        "target": r"C:\Start\Nebula Desk.lnk",
        "kind": "lnk",
    }
    probes = iter([
        None,
        {
            "candidate_count": 1,
            "candidates": [{"hwnd": 41, "visible": True}],
        },
        {
            "candidate_count": 1,
            "candidates": [{"hwnd": 42, "visible": True}],
            "activated_hwnd": 42,
            "note": "application became foreground",
        },
    ])
    sleeps = []
    window_calls = []
    monkeypatch.setattr(apps.time, "sleep", sleeps.append)
    monkeypatch.setattr(apps, "resolve", lambda *_args, **_kwargs: [entry])

    def activate(_entry, *, visible_only=False, activate=True):
        window_calls.append((visible_only, activate))
        if len(window_calls) == 1:
            return None
        return next(probes)

    monkeypatch.setattr(
        apps,
        "_activate_resolved_entry",
        activate,
    )
    monkeypatch.setattr(
        apps,
        "launch_target",
        lambda _entry: {
            "ok": True,
            "via": "lnk",
            "target": entry["target"],
            "arguments": [],
            "name": entry["name"],
            "aliases": [],
        },
    )

    result = apps.launch_application("Nebula Desk")

    assert result["activated_hwnd"] == 42
    assert result["via"] == "lnk"
    assert result["note"] == "launched application and activated its window"
    assert sleeps == [0.1, 0.1]
    assert window_calls == [
        (False, True),
        (True, False),
        (True, False),
        (True, False),
    ]


def test_cold_launch_activates_once_after_window_deadline(monkeypatch):
    from winpilot.perception import apps

    entry = {"name": "Nebula Desk"}
    visible = {
        "candidate_count": 1,
        "candidates": [{"hwnd": 41, "visible": True}],
    }
    activated = {**visible, "activated_hwnd": 41}
    monotonic = iter([100.0, 100.0, 110.0])
    sleeps = []
    calls = []
    monkeypatch.setattr(apps.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(apps.time, "sleep", sleeps.append)

    def find(_entry, *, visible_only=False, activate=True):
        calls.append((visible_only, activate))
        return activated if activate else visible

    monkeypatch.setattr(
        apps,
        "_activate_resolved_entry",
        find,
    )

    result = apps._wait_for_resolved_window(entry)

    assert result["activated_hwnd"] == 41
    assert sleeps == [0.1]
    assert calls == [(True, False), (False, True)]


def test_cold_launch_returns_immediately_when_app_is_foreground(monkeypatch):
    from winpilot.perception import apps

    entry = {"name": "Nebula Desk"}
    foreground = {
        "candidate_count": 1,
        "candidates": [{"hwnd": 41, "visible": True}],
        "activated_hwnd": 41,
    }
    monotonic = iter([100.0, 100.0])
    calls = []
    monkeypatch.setattr(apps.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(
        apps.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(
            AssertionError("an already-foreground app was delayed")
        ),
    )

    def find(_entry, *, visible_only=False, activate=True):
        calls.append((visible_only, activate))
        return foreground

    monkeypatch.setattr(apps, "_activate_resolved_entry", find)

    assert apps._wait_for_resolved_window(entry) == foreground
    assert calls == [(True, False)]


def test_existing_window_prefers_titled_main_window_over_visible_surface(
    monkeypatch,
):
    from winpilot.perception import apps

    black_surface = 11
    minimized_main = 22
    ime_helper = 33
    styles = {
        black_surface: 0,
        minimized_main: 0,
        ime_helper: 0x80,
    }
    titles = {
        black_surface: "",
        minimized_main: "Nebula Desk",
        ime_helper: "Default IME",
    }
    rects = {
        black_surface: (0, 0, 1600, 900),
        minimized_main: (-32000, -32000, -31800, -31960),
        ime_helper: (0, 0, 0, 0),
    }
    restored = []
    activated = []
    fake_win32con = SimpleNamespace(
        GWL_EXSTYLE=-20,
        GW_OWNER=4,
        SW_RESTORE=9,
        WS_EX_APPWINDOW=0x40000,
        WS_EX_TOOLWINDOW=0x80,
        WS_EX_TOPMOST=0x8,
    )
    fake_win32gui = SimpleNamespace(
        GetWindowLong=lambda hwnd, _index: styles[hwnd],
        GetWindow=lambda _hwnd, _command: 0,
        GetWindowRect=lambda hwnd: rects[hwnd],
        GetWindowText=lambda hwnd: titles[hwnd],
        IsWindowVisible=lambda hwnd: hwnd == black_surface,
        IsIconic=lambda hwnd: hwnd == minimized_main,
        ShowWindow=lambda hwnd, command: restored.append((hwnd, command)),
    )
    monkeypatch.setitem(sys.modules, "win32con", fake_win32con)
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setattr(apps, "_foreground_root_hwnd", lambda: 0)
    monkeypatch.setattr(
        apps,
        "_activate_window",
        lambda hwnd: activated.append(hwnd) or hwnd,
    )

    result = apps._existing_window_outcome(
        [black_surface],
        [minimized_main, ime_helper],
    )

    assert result["activated_hwnd"] == minimized_main
    assert result["candidates"][0]["hwnd"] == minimized_main
    assert restored == [(minimized_main, fake_win32con.SW_RESTORE)]
    assert activated == [minimized_main]


def test_existing_window_preserves_any_same_app_foreground_peer(monkeypatch):
    from winpilot.perception import apps

    ranked_first = 11
    actual_foreground = 22
    fake_win32con = SimpleNamespace(
        GWL_EXSTYLE=-20,
        GW_OWNER=4,
        SW_RESTORE=9,
        WS_EX_APPWINDOW=0x40000,
        WS_EX_TOOLWINDOW=0x80,
        WS_EX_TOPMOST=0x8,
    )
    fake_win32gui = SimpleNamespace(
        GetWindowLong=lambda _hwnd, _index: 0,
        GetWindow=lambda _hwnd, _command: 0,
        GetWindowRect=lambda _hwnd: (0, 0, 1600, 900),
        GetWindowText=lambda hwnd: "Main" if hwnd == ranked_first else "",
        IsWindowVisible=lambda _hwnd: True,
        IsIconic=lambda _hwnd: False,
        ShowWindow=lambda *_args: (_ for _ in ()).throw(
            AssertionError("an existing foreground peer was restored")
        ),
    )
    monkeypatch.setitem(sys.modules, "win32con", fake_win32con)
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setattr(
        apps,
        "_foreground_root_hwnd",
        lambda: actual_foreground,
    )
    monkeypatch.setattr(apps, "_root_hwnd", lambda hwnd: hwnd)
    monkeypatch.setattr(
        apps,
        "_activate_window",
        lambda _hwnd: (_ for _ in ()).throw(
            AssertionError("an existing foreground peer was reactivated")
        ),
    )

    result = apps._existing_window_outcome(
        [ranked_first, actual_foreground],
        [],
    )

    assert result["activated_hwnd"] == actual_foreground
    assert result["candidates"][0]["hwnd"] == actual_foreground


def test_cold_launch_probe_never_restores_or_activates_a_helper(monkeypatch):
    from winpilot.perception import apps

    black_surface = 11
    fake_win32con = SimpleNamespace(
        GWL_EXSTYLE=-20,
        GW_OWNER=4,
        SW_RESTORE=9,
        WS_EX_APPWINDOW=0x40000,
        WS_EX_TOOLWINDOW=0x80,
        WS_EX_TOPMOST=0x8,
    )
    fake_win32gui = SimpleNamespace(
        GetWindowLong=lambda _hwnd, _index: 0,
        GetWindow=lambda _hwnd, _command: 0,
        GetWindowRect=lambda _hwnd: (0, 0, 1600, 900),
        GetWindowText=lambda _hwnd: "",
        IsWindowVisible=lambda _hwnd: True,
        IsIconic=lambda _hwnd: False,
        ShowWindow=lambda *_args: (_ for _ in ()).throw(
            AssertionError("passive launch probe restored a window")
        ),
    )
    monkeypatch.setitem(sys.modules, "win32con", fake_win32con)
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setattr(
        apps,
        "_activate_window",
        lambda _hwnd: (_ for _ in ()).throw(
            AssertionError("passive launch probe activated a window")
        ),
    )

    result = apps._existing_window_outcome(
        [black_surface],
        [],
        activate=False,
    )

    assert result == {
        "candidate_count": 1,
        "candidates": [{
            "hwnd": black_surface,
            "title": "",
            "visible": True,
            "minimized": False,
        }],
    }


def test_resolve_refreshes_a_cached_multisource_index_once_on_miss(monkeypatch):
    from winpilot.perception import apps

    refreshed = {
        "name": "Comet Canvas",
        "target": r"C:\Start\Comet Canvas.lnk",
        "kind": "lnk",
    }
    calls = []
    monkeypatch.setattr(apps, "_cache", (1.0, []))

    def fake_index(*, force_refresh=False):
        calls.append(force_refresh)
        return [refreshed] if force_refresh else []

    monkeypatch.setattr(apps, "_index", fake_index)

    assert apps.resolve("Comet Canvas")[0]["target"] == refreshed["target"]
    assert calls == [False, True]


def test_index_keeps_same_kind_entries_with_distinct_launch_targets(monkeypatch):
    from winpilot.perception import apps

    entries = [
        {"name": "Comet", "target": r"C:\Apps\A\Comet.lnk", "kind": "lnk"},
        {"name": "Comet", "target": r"C:\Apps\B\Comet.lnk", "kind": "lnk"},
    ]
    monkeypatch.setattr(apps, "_cache", None)
    monkeypatch.setattr(apps, "_start_menu_entries", lambda: entries)
    monkeypatch.setattr(apps, "_uwp_entries", lambda: [])
    monkeypatch.setattr(apps, "_app_paths_entries", lambda: [])

    assert [item["target"] for item in apps._index()] == [
        entries[0]["target"], entries[1]["target"],
    ]


def test_start_app_record_adds_only_catalog_proven_runtime_identity():
    from winpilot.perception import apps

    executable = r"C:\Program Files\Orbit\orbit-player.exe"
    entry = apps._start_app_entry({
        "name": "Orbit Player",
        "app_id": "Fictional.Orbit_123!Desktop",
        "executable": executable,
        "package_family": "Fictional.Orbit_123",
    })

    assert entry == {
        "name": "Orbit Player",
        "target": "Fictional.Orbit_123!Desktop",
        "kind": "appid",
        "app_id": "Fictional.Orbit_123!Desktop",
        "package_family": "Fictional.Orbit_123",
        "executable": executable,
        "process_name": "orbit-player.exe",
        "aliases": ["orbit-player.exe", "orbit-player", executable],
    }


def test_start_app_record_does_not_invent_a_process_from_display_or_app_id():
    from winpilot.perception import apps

    entry = apps._start_app_entry({
        "name": "Comet Canvas",
        "app_id": "Fictional.Comet_123!App",
        "package_family": "Fictional.Comet_123",
    })

    assert entry["package_family"] == "Fictional.Comet_123"
    assert "executable" not in entry
    assert "process_name" not in entry
    assert "aliases" not in entry


def test_start_apps_json_parser_preserves_executable_and_package_identity():
    from winpilot.perception import apps

    payload = json.dumps([{
        "name": "星云播放器",
        "app_id": "Fictional.Nebula_456!Main",
        "executable": r"C:\Apps\Nebula\nebula-host.exe",
        "package_family": "Fictional.Nebula_456",
    }], ensure_ascii=False)

    result = apps._parse_start_apps_output(payload)

    assert result[0]["name"] == "星云播放器"
    assert result[0]["process_name"] == "nebula-host.exe"
    assert result[0]["package_family"] == "Fictional.Nebula_456"


def test_start_apps_discovery_uses_system_catalog_metadata_once(monkeypatch):
    from winpilot.perception import apps

    calls = []
    payload = json.dumps([{
        "name": "Orbit Player",
        "app_id": "Fictional.Orbit_123!Desktop",
        "executable": r"C:\Apps\Orbit\orbit.exe",
        "package_family": "Fictional.Orbit_123",
    }])

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=payload)

    monkeypatch.setattr(apps.subprocess, "run", fake_run)

    entry = apps._uwp_entries()[0]
    script = calls[0][0][-1]
    assert entry["process_name"] == "orbit.exe"
    assert "shell:AppsFolder" in script
    assert "Get-AppxPackageManifest" in script
    assert "Get-ChildItem" not in script
    assert len(calls) == 1


def test_app_paths_ignores_stale_executable_targets(monkeypatch, tmp_path):
    from winpilot.perception import apps

    live = tmp_path / "msedge.exe"
    live.write_bytes(b"MZ")
    catalog = {
        "msedge.exe": str(live),
        "ghost.exe": str(tmp_path / "missing.exe"),
    }

    class FakeKey:
        def __init__(self, *, entries=None, value=""):
            self.entries = entries or {}
            self.value = value

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def Close(self):
            return None

    hklm = object()
    hkcu = object()

    def open_key(parent, name):
        if parent is hklm:
            return FakeKey(entries=catalog)
        if parent is hkcu:
            raise OSError("no user App Paths")
        return FakeKey(value=parent.entries[name])

    fake_winreg = SimpleNamespace(
        HKEY_LOCAL_MACHINE=hklm,
        HKEY_CURRENT_USER=hkcu,
        OpenKey=open_key,
        QueryInfoKey=lambda key: (len(key.entries), 0, 0),
        EnumKey=lambda key, index: list(key.entries)[index],
        QueryValueEx=lambda key, _name: (key.value, 1),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    assert apps._app_paths_entries() == [{
        "name": "msedge",
        "target": str(live),
        "kind": "exe",
    }]


def test_index_merges_structured_identity_for_the_same_app_id(monkeypatch):
    from winpilot.perception import apps

    app_id = "Fictional.Orbit_123!Desktop"
    sparse = {"name": "Orbit Player", "target": app_id, "kind": "appid"}
    rich = apps._start_app_entry({
        "name": "Orbit Player Desktop",
        "app_id": app_id,
        "executable": r"C:\Apps\Orbit\orbit-player.exe",
        "package_family": "Fictional.Orbit_123",
    })
    monkeypatch.setattr(apps, "_cache", None)
    monkeypatch.setattr(apps, "_start_menu_entries", lambda: [sparse])
    monkeypatch.setattr(apps, "_uwp_entries", lambda: [rich])
    monkeypatch.setattr(apps, "_app_paths_entries", lambda: [])

    entry = apps._index()[0]

    assert entry["process_name"] == "orbit-player.exe"
    assert entry["package_family"] == "Fictional.Orbit_123"
    assert entry["aliases"][:3] == [
        "orbit-player.exe",
        "orbit-player",
        r"C:\Apps\Orbit\orbit-player.exe",
    ]


def test_resolve_can_match_a_discovered_runtime_process_alias(monkeypatch):
    from winpilot.perception import apps

    entry = apps._start_app_entry({
        "name": "Orbit Player",
        "app_id": "Fictional.Orbit_123!Desktop",
        "executable": r"C:\Apps\Orbit\orbit-player.exe",
        "package_family": "Fictional.Orbit_123",
    })
    monkeypatch.setattr(apps, "_index", lambda: [entry])

    result = apps.resolve("orbit-player.exe")

    assert result[0]["target"] == "Fictional.Orbit_123!Desktop"
    assert result[0]["process_name"] == "orbit-player.exe"
    assert result[0]["match_kind"] == "target_alias"


def test_resolve_falls_back_to_structured_executable_search(monkeypatch):
    from winpilot.perception import apps, search

    calls = []
    portable_executable = r"C:\Portable\Nebula Desk.exe"
    monkeypatch.setattr(apps, "_cache", None)
    monkeypatch.setattr(apps, "_index", lambda: [])

    def fake_everything(
        query,
        max_results,
        path,
        *,
        kind="any",
        extensions=None,
        sort="path",
    ):
        calls.append((query, max_results, path, kind, extensions, sort))
        return {
            "engine": "everything",
            "results": [portable_executable],
            "count": 1,
        }

    monkeypatch.setattr(search, "_search_everything", fake_everything)
    monkeypatch.setattr(
        search,
        "_search_scandir",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Everything result should be returned directly")
        ),
    )

    result = apps.resolve("Nebula Desk.exe")

    assert result == [{
        "name": "Nebula Desk",
        "target": portable_executable,
        "kind": "exe",
    }]
    assert calls == [("Nebula Desk", 20, None, "file", ["exe"], "path")]


def test_resolve_executable_fallback_keeps_only_exact_stems(monkeypatch):
    from winpilot.perception import apps, search

    calls = []
    edge_core = r"C:\Program Files (x86)\Microsoft\EdgeCore\146\msedge.exe"
    monkeypatch.setattr(apps, "_cache", None)
    monkeypatch.setattr(apps, "_index", lambda: [])

    def fake_search(query, *, max_results, kind, extensions):
        calls.append((query, max_results, kind, extensions))
        if query == "msedge":
            results = [
                r"C:\Edge\msedgewebview2.exe",
                edge_core,
                r"C:\EdgeWebView\msedge.exe",
            ]
        else:
            results = [r"C:\EdgeUpdate\MicrosoftEdgeUpdate.exe"]
        return {"results": results}

    monkeypatch.setattr(search, "search", fake_search)

    assert apps.resolve("msedge", max_results=1) == [{
        "name": "msedge",
        "target": edge_core,
        "kind": "exe",
    }]
    assert apps.resolve("Microsoft Edge", max_results=1) == []
    assert calls == [
        ("msedge", 20, "file", ["exe"]),
        ("Microsoft Edge", 20, "file", ["exe"]),
    ]
