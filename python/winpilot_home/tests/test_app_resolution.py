from __future__ import annotations


def test_resolve_inbox_notepad_includes_system_exe():
    from winpilot.perception import apps

    candidates = apps.resolve("notepad")
    assert candidates, "expected at least one notepad candidate"
    assert any(
        c["kind"] == "exe" and str(c["target"]).lower().endswith(r"system32\notepad.exe")
        for c in candidates
    )
