from __future__ import annotations

from winpilot.tasking.execution_context import (
    capture_foreground_window_context,
    foreground_handoff_conflict,
    normalize_execution_context,
    validated_handoff_target,
)
from winpilot.utils.screenshot import Region


def _inputs(*, yielded: bool, required: bool = True, pid: int = 4412) -> dict:
    return {
        "execution_context": {
            "format": "execution-context/1",
            "entrypoint": "native_agent_cockpit",
            "task_id": "task-1",
            "submitted_at": 1234,
            "desktop_handoff": {
                "required": required,
                "strategy": "foreground_after_control_surface_yield",
                "yielded": yielded,
                "control_surface_pid": pid,
                "prepared_at": 1200,
            },
            "untrusted_extra": "discarded",
        },
    }


def test_execution_context_normalization_keeps_only_runtime_contract_fields():
    context = normalize_execution_context(_inputs(yielded=True))

    assert context["format"] == "execution-context/1"
    assert context["entrypoint"] == "native_agent_cockpit"
    assert context["desktop_handoff"]["yielded"] is True
    assert "untrusted_extra" not in context


def test_native_run_fails_before_model_when_control_surface_did_not_yield():
    context = normalize_execution_context(_inputs(yielded=False))

    conflict = foreground_handoff_conflict(
        context,
        99,
        process_id_resolver=lambda _hwnd: 123,
    )

    assert conflict is not None
    assert conflict["failure_type"] == "control_surface_handoff_failed"


def test_native_run_rejects_control_surface_as_active_target_after_handoff():
    context = normalize_execution_context(_inputs(yielded=True, pid=4412))

    conflict = foreground_handoff_conflict(
        context,
        99,
        process_id_resolver=lambda _hwnd: 4412,
    )

    assert conflict is not None
    assert conflict["target_process_id"] == 4412


def test_native_run_accepts_a_different_foreground_application():
    context = normalize_execution_context(_inputs(yielded=True, pid=4412))

    assert foreground_handoff_conflict(
        context,
        99,
        process_id_resolver=lambda _hwnd: 8824,
    ) is None


def test_api_run_without_native_handoff_context_keeps_existing_semantics():
    assert foreground_handoff_conflict({}, 99, process_id_resolver=lambda _hwnd: 4412) is None


def test_foreground_capture_records_external_window_after_client_yields():
    target = capture_foreground_window_context(
        4412,
        foreground_resolver=lambda: 99,
        process_id_resolver=lambda _hwnd: 8824,
        windows_provider=lambda: [{
            "hwnd": 99,
            "title": "External target",
            "class_name": "TargetWindow",
            "rect": [0, 0, 1200, 800],
        }],
        time_provider=lambda: 1.25,
    )

    assert target["usable"] is True
    assert target["hwnd"] == 99
    assert target["process_id"] == 8824
    assert target["captured_at"] == 1250


def test_foreground_inventory_uses_visible_dwm_bounds_for_maximized_window(
    monkeypatch,
):
    from winpilot.perception import win32

    monkeypatch.setattr(
        win32.win32gui,
        "EnumWindows",
        lambda callback, param: callback(99, param),
    )
    monkeypatch.setattr(win32.win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(win32.win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(win32, "_window_text", lambda _hwnd: "Maximized target")
    monkeypatch.setattr(win32.win32gui, "GetWindowLong", lambda *_args: 0)
    monkeypatch.setattr(
        win32.win32gui,
        "GetWindowRect",
        lambda _hwnd: (-9, -9, 1929, 1049),
    )
    monkeypatch.setattr(
        win32,
        "window_region",
        lambda _hwnd: Region(0, 0, 1920, 1040),
    )
    monkeypatch.setattr(
        win32.win32process,
        "GetWindowThreadProcessId",
        lambda _hwnd: (1, 8824),
    )
    monkeypatch.setattr(
        win32.win32gui,
        "GetClassName",
        lambda _hwnd: "TargetWindow",
    )

    windows = win32.list_windows()

    assert windows == [{
        "hwnd": 99,
        "title": "Maximized target",
        "class_name": "TargetWindow",
        "rect": [0, 0, 1920, 1040],
        "pid": 8824,
    }]


def test_foreground_capture_rejects_the_native_client_process():
    target = capture_foreground_window_context(
        4412,
        foreground_resolver=lambda: 99,
        process_id_resolver=lambda _hwnd: 4412,
        windows_provider=lambda: [{
            "hwnd": 99,
            "title": "Native cockpit",
            "class_name": "Chrome_WidgetWin_1",
            "rect": [0, 0, 1200, 800],
        }],
    )

    assert target["usable"] is False
    assert target["failure_type"] == "control_surface_handoff_failed"


def test_foreground_capture_classifies_unavailable_window_inventory():
    def broken_inventory():
        raise RuntimeError("window inventory unavailable")

    target = capture_foreground_window_context(
        4412,
        foreground_resolver=lambda: 99,
        process_id_resolver=lambda _hwnd: 8824,
        windows_provider=broken_inventory,
    )

    assert target["usable"] is False
    assert target["failure_type"] == "target_binding_missing"


def test_handoff_target_must_be_live_and_recent():
    inputs = _inputs(yielded=True)
    inputs["execution_context"]["target_window"] = {
        "format": "desktop-target/1",
        "hwnd": 99,
        "title": "External target",
        "process_id": 8824,
        "class_name": "TargetWindow",
        "rect": [0, 0, 1200, 800],
        "captured_at": 5_000,
        "foreground_verified": True,
        "usable": True,
    }
    context = normalize_execution_context(inputs)

    hwnd, issue = validated_handoff_target(
        context,
        window_exists_resolver=lambda _hwnd: True,
        now_ms=5_500,
    )
    assert (hwnd, issue) == (99, None)

    stale_hwnd, stale = validated_handoff_target(
        context,
        window_exists_resolver=lambda _hwnd: True,
        now_ms=25_001,
    )
    assert stale_hwnd == 0
    assert stale is not None
    assert stale["failure_type"] == "target_binding_stale"
