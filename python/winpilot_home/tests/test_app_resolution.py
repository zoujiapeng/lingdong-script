from __future__ import annotations


def test_resolve_inbox_notepad_includes_system_exe():
    from winpilot.perception import apps

    candidates = apps.resolve("notepad")
    assert candidates, "expected at least one notepad candidate"
    assert any(
        c["kind"] == "exe" and str(c["target"]).lower().endswith(r"system32\notepad.exe")
        for c in candidates
    )


def test_resolve_localized_inbox_tool_uses_fast_system_target(monkeypatch):
    from winpilot.perception import apps

    monkeypatch.setattr(
        apps,
        "_index",
        lambda: (_ for _ in ()).throw(AssertionError("slow app index must not run")),
    )

    candidates = apps.resolve("画图")

    assert candidates
    assert candidates[0]["name"] == "画图"
    assert str(candidates[0]["target"]).lower().endswith("mspaint.exe")


def test_resolve_uses_product_alias_for_installed_start_app(monkeypatch):
    from winpilot.perception import apps

    monkeypatch.setattr(
        apps,
        "_index",
        lambda: [{"name": "WPS Office", "target": "wps-office", "kind": "appid"}],
    )
    monkeypatch.setattr(apps, "_system_tool_entries", lambda: [])
    monkeypatch.setattr(apps, "_wps_suite_entries", lambda: [])

    results = apps.resolve("WPS文字")

    assert results[0]["name"] == "WPS Office"


def test_resolve_wps_spreadsheets_prefers_et_component(monkeypatch):
    from winpilot.perception import apps

    et = r"C:\Program Files\Kingsoft\WPS Office\office6\et.exe"
    monkeypatch.setattr(apps, "_system_tool_entries", lambda: [])
    monkeypatch.setattr(
        apps,
        "_wps_suite_entries",
        lambda: [
            {"name": "WPS Writer", "target": et.replace("et.exe", "wps.exe"), "kind": "exe"},
            {"name": "WPS Spreadsheets", "target": et, "kind": "exe"},
        ],
    )
    monkeypatch.setattr(
        apps,
        "_index",
        lambda: [{"name": "WPS Office", "target": "wps-office", "kind": "appid"}],
    )

    results = apps.resolve("WPS Spreadsheets")

    assert results[0]["target"].casefold().endswith(r"office6\et.exe")


def test_launch_wps_component_passes_registered_suite_arguments(monkeypatch):
    from winpilot.perception import apps

    calls = []
    monkeypatch.setattr(
        apps.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    result = apps.launch_target({
        "name": "WPS Writer",
        "target": r"C:\Program Files\Kingsoft\office6\wps.exe",
        "kind": "exe",
        "arguments": ["/prometheus", "/wps"],
    })

    assert result["ok"] is True
    assert calls[0][0][-2:] == ["/prometheus", "/wps"]
    assert result["arguments"] == ["/prometheus", "/wps"]
