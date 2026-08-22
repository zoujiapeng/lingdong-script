from __future__ import annotations

from winpilot.tasking.execution_context import (
    capture_desktop_workspace_context,
    windows_shell_desktop_context,
)


def test_windows_shell_context_reads_localized_win32_identity(monkeypatch):
    from winpilot.tasking import execution_context

    monkeypatch.setattr(execution_context, "_window_title", lambda _hwnd: "桌面窗口")
    monkeypatch.setattr(execution_context, "_window_class_name", lambda _hwnd: "Progman")

    target = windows_shell_desktop_context(
        region_provider=lambda: [0, 0, 1536, 864],
        hwnd_resolver=lambda: 65878,
        process_id_resolver=lambda _hwnd: 19076,
    )

    assert target["title"] == "桌面窗口"
    assert target["class_name"] == "Progman"
    assert target["binding_mode"] == "desktop_shell"


def test_desktop_workspace_uses_only_the_neutral_shell_anchor():
    target = capture_desktop_workspace_context(
        time_provider=lambda: 1.25,
        desktop_shell_resolver=lambda: {
            "hwnd": 65878,
            "title": "Program Manager",
            "process_id": 19076,
            "class_name": "Progman",
            "rect": [0, 0, 1536, 864],
            "usable": True,
        },
    )

    assert target == {
        "format": "desktop-target/1",
        "hwnd": 65878,
        "title": "Program Manager",
        "process_id": 19076,
        "class_name": "Progman",
        "rect": [0, 0, 1536, 864],
        "captured_at": 1250,
        "foreground_verified": False,
        "usable": True,
        "failure_type": "",
        "reason": "",
        "binding_mode": "desktop_shell",
    }


def test_desktop_workspace_reports_a_missing_shell_directly():
    target = capture_desktop_workspace_context(
        time_provider=lambda: 2.0,
        desktop_shell_resolver=lambda: {},
    )

    assert target["usable"] is False
    assert target["hwnd"] == 0
    assert target["captured_at"] == 2000
    assert target["failure_type"] == "target_binding_missing"
    assert target["binding_mode"] == "desktop_shell"


def test_windows_shell_context_uses_class_when_title_is_empty(monkeypatch):
    from winpilot.tasking import execution_context

    monkeypatch.setattr(execution_context, "_window_title", lambda _hwnd: "")
    monkeypatch.setattr(execution_context, "_window_class_name", lambda _hwnd: "WorkerW")

    target = windows_shell_desktop_context(
        region_provider=lambda: [0, 0, 1536, 864],
        hwnd_resolver=lambda: 65878,
        process_id_resolver=lambda _hwnd: 19076,
    )

    assert target["title"] == "WorkerW"
    assert target["class_name"] == "WorkerW"
