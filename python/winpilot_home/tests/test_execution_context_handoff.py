from __future__ import annotations

import pytest

from winpilot.tasking.execution_context import (
    capture_foreground_window_context,
    foreground_handoff_conflict,
    normalize_execution_context,
    validated_handoff_target,
)


PROCESS_IDENTITY = "process-" + "a" * 32


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
        root_hwnd_resolver=lambda _hwnd: 99,
        process_id_resolver=lambda _hwnd: 8824,
        process_identity_resolver=lambda _pid: PROCESS_IDENTITY,
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
    assert target["root_hwnd"] == 99
    assert target["process_id"] == 8824
    assert target["process_identity"] == PROCESS_IDENTITY
    assert target["format"] == "desktop-target/2"
    assert target["captured_at"] == 1250


def test_foreground_capture_rejects_the_native_client_process():
    target = capture_foreground_window_context(
        4412,
        foreground_resolver=lambda: 99,
        root_hwnd_resolver=lambda _hwnd: 99,
        process_id_resolver=lambda _hwnd: 4412,
        process_identity_resolver=lambda _pid: PROCESS_IDENTITY,
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
        root_hwnd_resolver=lambda _hwnd: 99,
        process_id_resolver=lambda _hwnd: 8824,
        process_identity_resolver=lambda _pid: PROCESS_IDENTITY,
        windows_provider=broken_inventory,
    )

    assert target["usable"] is False
    assert target["failure_type"] == "target_binding_missing"


@pytest.mark.parametrize("rect", ([], [0, 0, 0, 800], [0, 0, 1200]))
def test_foreground_capture_never_marks_invalid_geometry_usable(rect):
    target = capture_foreground_window_context(
        foreground_resolver=lambda: 99,
        root_hwnd_resolver=lambda _hwnd: 99,
        process_id_resolver=lambda _hwnd: 8824,
        process_identity_resolver=lambda _pid: PROCESS_IDENTITY,
        windows_provider=lambda: [{
            "hwnd": 99,
            "title": "External target",
            "class_name": "TargetWindow",
            "rect": rect,
        }],
    )

    assert target["usable"] is False
    assert target["failure_type"] == "target_binding_contract_invalid"


def test_handoff_target_must_be_live_and_recent():
    inputs = _inputs(yielded=True)
    inputs["execution_context"]["target_window"] = {
        "format": "desktop-target/2",
        "hwnd": 99,
        "root_hwnd": 99,
        "title": "External target",
        "process_id": 8824,
        "process_identity": PROCESS_IDENTITY,
        "class_name": "TargetWindow",
        "rect": [0, 0, 1200, 800],
        "captured_at": 5_000,
        "foreground_verified": True,
        "usable": True,
        "failure_type": "",
        "reason": "",
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


def test_native_handoff_rejects_unsupported_outer_contract():
    inputs = _inputs(yielded=True)
    inputs["execution_context"]["format"] = "execution-context/0"
    context = normalize_execution_context(inputs)

    hwnd, issue = validated_handoff_target(context)

    assert hwnd == 0
    assert issue is not None
    assert issue["failure_type"] == "execution_context_contract_invalid"


def test_foreground_capture_fails_closed_without_strong_process_identity():
    target = capture_foreground_window_context(
        4412,
        foreground_resolver=lambda: 99,
        root_hwnd_resolver=lambda _hwnd: 99,
        process_id_resolver=lambda _hwnd: 8824,
        process_identity_resolver=lambda _pid: "",
        windows_provider=lambda: [{
            "hwnd": 99,
            "root_hwnd": 99,
            "title": "External target",
            "class_name": "TargetWindow",
            "rect": [0, 0, 1200, 800],
        }],
    )

    assert target["usable"] is False
    assert target["failure_type"] == "target_process_identity_unavailable"


@pytest.mark.parametrize("changed_fact", ["hwnd", "root", "pid", "identity"])
def test_foreground_capture_rejects_identity_drift_between_samples(
    changed_fact: str,
):
    calls = {"foreground": 0, "root": 0, "pid": 0, "identity": 0}

    def sampled(name, first, second):
        def resolve(_value=None):
            calls[name] += 1
            return first if calls[name] == 1 else second
        return resolve

    target = capture_foreground_window_context(
        4412,
        foreground_resolver=sampled(
            "foreground", 99, 100 if changed_fact == "hwnd" else 99,
        ),
        root_hwnd_resolver=sampled(
            "root", 99, 100 if changed_fact == "root" else 99,
        ),
        process_id_resolver=sampled(
            "pid", 8824, 8825 if changed_fact == "pid" else 8824,
        ),
        process_identity_resolver=sampled(
            "identity",
            PROCESS_IDENTITY,
            "process-" + "b" * 32 if changed_fact == "identity" else PROCESS_IDENTITY,
        ),
        windows_provider=lambda: [{
            "hwnd": 99,
            "title": "External target",
            "class_name": "TargetWindow",
            "rect": [0, 0, 1200, 800],
        }],
    )

    assert target["usable"] is False
    assert target["failure_type"] == "target_identity_changed_during_capture"


def test_foreground_capture_rejects_missing_root_window_identity():
    target = capture_foreground_window_context(
        foreground_resolver=lambda: 99,
        root_hwnd_resolver=lambda _hwnd: 0,
        process_id_resolver=lambda _hwnd: 8824,
        process_identity_resolver=lambda _pid: PROCESS_IDENTITY,
        windows_provider=lambda: [{
            "hwnd": 99,
            "title": "External target",
            "class_name": "TargetWindow",
            "rect": [0, 0, 1200, 800],
        }],
    )

    assert target["usable"] is False
    assert target["failure_type"] == "target_process_identity_unavailable"


def test_handoff_rejects_legacy_or_noncanonical_process_identity():
    for target_format, process_identity in (
        ("desktop-target/1", PROCESS_IDENTITY),
        ("desktop-target/2", "process-weak"),
    ):
        inputs = _inputs(yielded=True)
        inputs["execution_context"]["target_window"] = {
            "format": target_format,
            "hwnd": 99,
            "title": "External target",
            "process_id": 8824,
            "process_identity": process_identity,
            "class_name": "TargetWindow",
            "rect": [0, 0, 1200, 800],
            "captured_at": 5_000,
            "foreground_verified": True,
            "usable": True,
            "failure_type": "",
            "reason": "",
        }
        hwnd, issue = validated_handoff_target(
            normalize_execution_context(inputs),
            window_exists_resolver=lambda _hwnd: True,
            now_ms=5_500,
        )

        assert hwnd == 0
        assert issue is not None
        assert issue["failure_type"] in {
            "target_binding_contract_invalid",
            "target_process_identity_unavailable",
        }


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("process_id", True),
        ("root_hwnd", "99"),
        ("rect", [0, 0, 1200, 800, 900]),
        ("captured_at", 6_000),
        ("unexpected", True),
    ),
)
def test_handoff_rejects_noncanonical_target_fields(field, value):
    inputs = _inputs(yielded=True)
    target = {
        "format": "desktop-target/2",
        "hwnd": 99,
        "root_hwnd": 99,
        "title": "External target",
        "process_id": 8824,
        "process_identity": PROCESS_IDENTITY,
        "class_name": "TargetWindow",
        "rect": [0, 0, 1200, 800],
        "captured_at": 5_000,
        "foreground_verified": True,
        "usable": True,
        "failure_type": "",
        "reason": "",
    }
    target[field] = value
    inputs["execution_context"]["target_window"] = target

    hwnd, issue = validated_handoff_target(
        normalize_execution_context(inputs),
        window_exists_resolver=lambda _hwnd: True,
        now_ms=5_500,
    )

    assert hwnd == 0
    assert issue is not None
