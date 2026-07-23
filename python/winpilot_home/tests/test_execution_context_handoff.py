from __future__ import annotations

import pytest

from winpilot.process_identity import process_identity_for
from winpilot.tasking.execution_context import (
    capture_foreground_window_context,
    foreground_handoff_conflict,
    normalize_execution_context,
    validated_handoff_target,
)
from winpilot.visual_runtime.windows_desktop_facts import (
    WindowsDesktopFactsSourceError,
)


PROCESS_IDENTITY = "process-" + "a" * 32


class CaptureBackend:
    def __init__(self) -> None:
        self.values = {
            "foreground": [99],
            "shell": [0],
            "desktop": [0],
            "is_window": [True],
            "root": [99],
            "pid": [8824],
            "creation": [1_000_000_000],
            "executable": [r"C:\Apps\target.exe"],
            "region": [(0, 0, 1200, 800)],
        }
        self.fail: set[str] = set()
        self.foreground_reads = 0

    def __bool__(self) -> bool:
        return False

    def _read(self, name: str):
        if name in self.fail:
            raise RuntimeError(f"{name} source unavailable")
        values = self.values[name]
        return values.pop(0) if len(values) > 1 else values[0]

    def foreground_hwnd(self):
        self.foreground_reads += 1
        return self._read("foreground")

    def shell_hwnd(self):
        return self._read("shell")

    def desktop_hwnd(self):
        return self._read("desktop")

    def is_window(self, _hwnd):
        return self._read("is_window")

    def root_hwnd(self, _hwnd):
        return self._read("root")

    def process_id(self, _hwnd):
        return self._read("pid")

    def process_creation_time_ns(self, _process_id):
        return self._read("creation")

    def process_executable_path(self, _process_id):
        return self._read("executable")

    def window_region(self, _hwnd):
        return self._read("region")

    def foreground_identity(self) -> str:
        return process_identity_for(
            process_id=self.values["pid"][0],
            creation_time_ns=self.values["creation"][0],
            executable_path=self.values["executable"][0],
        )

    @staticmethod
    def window_title(_hwnd):
        return ""

    @staticmethod
    def window_from_point(_point):
        return 99


def _assert_canonical_absence(target: dict) -> None:
    assert target == {
        "format": "desktop-target/2",
        "hwnd": 0,
        "root_hwnd": 0,
        "title": "",
        "process_id": 0,
        "process_identity": "",
        "class_name": "",
        "rect": [],
        "captured_at": target["captured_at"],
        "foreground_verified": False,
        "usable": False,
        "failure_type": "target_binding_missing",
        "reason": target["reason"],
    }
    assert type(target["captured_at"]) is int
    assert target["captured_at"] > 0
    assert type(target["reason"]) is str


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
    backend = CaptureBackend()
    expected_identity = backend.foreground_identity()
    target = capture_foreground_window_context(
        4412,
        facts_backend=backend,
        time_provider=lambda: 1.25,
    )

    assert target["usable"] is True
    assert target["hwnd"] == 99
    assert target["root_hwnd"] == 99
    assert target["process_id"] == 8824
    assert target["process_identity"] == expected_identity
    assert target["format"] == "desktop-target/2"
    assert target["captured_at"] == 1250


def test_foreground_capture_rejects_the_native_client_process():
    backend = CaptureBackend()
    backend.values["pid"] = [4412]
    target = capture_foreground_window_context(
        4412,
        facts_backend=backend,
    )

    assert target["usable"] is False
    assert target["failure_type"] == "control_surface_handoff_failed"


def test_foreground_capture_classifies_unavailable_facts_source():
    backend = CaptureBackend()
    backend.fail.add("executable")
    target = capture_foreground_window_context(
        4412,
        facts_backend=backend,
    )

    assert target["usable"] is False
    assert target["failure_type"] == "target_observation_source_unavailable"
    assert target["hwnd"] == 99


def test_foreground_capture_maps_typed_facts_source_error_without_message_matching():
    backend = CaptureBackend()

    def unavailable(_process_id):
        raise WindowsDesktopFactsSourceError("opaque")

    backend.process_creation_time_ns = unavailable
    target = capture_foreground_window_context(facts_backend=backend)

    assert target["usable"] is False
    assert target["failure_type"] == "target_observation_source_unavailable"
    assert target["hwnd"] == 99


def test_foreground_capture_identity_read_failure_is_unverified_not_drift():
    backend = CaptureBackend()
    backend.values["creation"] = [1_000_000_000, 0, 1_000_000_000]

    target = capture_foreground_window_context(facts_backend=backend)

    assert target["usable"] is False
    assert target["failure_type"] == "target_binding_unverified"
    assert target["hwnd"] == 99


def test_foreground_capture_source_failure_is_not_target_absence():
    backend = CaptureBackend()
    backend.fail.add("foreground")
    target = capture_foreground_window_context(
        facts_backend=backend,
    )

    assert target["usable"] is False
    assert target["failure_type"] == "target_observation_source_unavailable"
    assert target["hwnd"] == 0


def test_foreground_capture_stable_zero_is_exact_canonical_absence():
    backend = CaptureBackend()
    backend.values["foreground"] = [0]
    target = capture_foreground_window_context(
        facts_backend=backend,
        time_provider=lambda: 1.25,
    )

    assert backend.foreground_reads == 2
    _assert_canonical_absence(target)
    assert target["captured_at"] == 1250


@pytest.mark.parametrize(
    ("shell_hwnd", "desktop_hwnd"),
    ((99, 0), (0, 99)),
)
def test_foreground_capture_stable_shell_or_desktop_is_canonical_absence(
    shell_hwnd: int,
    desktop_hwnd: int,
):
    backend = CaptureBackend()
    backend.values["shell"] = [shell_hwnd]
    backend.values["desktop"] = [desktop_hwnd]
    target = capture_foreground_window_context(
        facts_backend=backend,
        time_provider=lambda: 1.25,
    )

    _assert_canonical_absence(target)


def test_foreground_capture_nonzero_unknown_window_is_not_target_absence():
    backend = CaptureBackend()
    backend.values["is_window"] = [False]
    target = capture_foreground_window_context(
        facts_backend=backend,
    )

    assert target["usable"] is False
    assert target["hwnd"] == 99
    assert target["failure_type"] == "target_binding_unverified"


def test_foreground_capture_shell_source_failure_is_not_target_absence():
    backend = CaptureBackend()
    backend.fail.add("shell")
    target = capture_foreground_window_context(
        facts_backend=backend,
    )

    assert target["usable"] is False
    assert target["hwnd"] == 99
    assert target["failure_type"] == "target_observation_source_unavailable"


def test_foreground_capture_shell_drift_is_not_target_absence():
    backend = CaptureBackend()
    backend.values["foreground"] = [99, 100]
    backend.values["shell"] = [99]
    target = capture_foreground_window_context(
        facts_backend=backend,
    )

    assert target["usable"] is False
    assert target["hwnd"] == 100
    assert target["failure_type"] == "target_identity_changed_during_capture"


def test_foreground_capture_rechecks_shell_after_strong_identity_sampling():
    backend = CaptureBackend()
    backend.values["shell"] = [98, 99]

    target = capture_foreground_window_context(facts_backend=backend)

    assert target["usable"] is False
    assert target["hwnd"] == 99
    assert target["failure_type"] == "target_identity_changed_during_capture"


def test_foreground_capture_zero_absence_rechecks_shell_stability():
    backend = CaptureBackend()
    backend.values["foreground"] = [0]
    backend.values["shell"] = [98, 99]

    target = capture_foreground_window_context(facts_backend=backend)

    assert target["usable"] is False
    assert target["failure_type"] == "target_identity_changed_during_capture"


def test_foreground_capture_timestamp_is_taken_after_final_sample():
    backend = CaptureBackend()
    def completed_time():
        assert backend.foreground_reads >= 3
        return 1.25

    target = capture_foreground_window_context(
        facts_backend=backend,
        time_provider=completed_time,
    )

    assert target["usable"] is True
    assert target["captured_at"] == 1250


def test_target_binding_missing_implies_exact_canonical_absence():
    zero = CaptureBackend()
    zero.values["foreground"] = [0]
    ordinary = CaptureBackend()
    ordinary.values["is_window"] = [False]
    shell = CaptureBackend()
    shell.values["shell"] = [99]
    results = (
        capture_foreground_window_context(facts_backend=zero),
        capture_foreground_window_context(facts_backend=ordinary),
        capture_foreground_window_context(facts_backend=shell),
    )

    for target in results:
        if target["failure_type"] == "target_binding_missing":
            _assert_canonical_absence(target)


def test_production_capture_uses_strict_facts_not_filtered_inventory(
    monkeypatch,
):
    from winpilot.perception import win32
    import winpilot.visual_runtime.windows_desktop_facts as facts_module

    def forbidden_inventory():
        raise AssertionError("filtered inventory is not capture authority")

    backend = CaptureBackend()
    monkeypatch.setattr(win32, "list_windows", forbidden_inventory)
    monkeypatch.setattr(
        facts_module, "PyWin32DesktopFactsBackend", lambda: backend,
    )

    target = capture_foreground_window_context(
        time_provider=lambda: 1.25,
    )

    assert target["usable"] is True
    assert target["hwnd"] == 99
    assert target["root_hwnd"] == 99
    assert target["process_id"] == 8824
    assert target["title"] == ""
    assert target["class_name"] == ""
    assert target["rect"] == [0, 0, 1200, 800]
    assert target["captured_at"] == 1250


@pytest.mark.parametrize(
    "region",
    ((), (0, 0, 0, 800), (0, 0, 1200, 0)),
)
def test_foreground_capture_never_marks_invalid_geometry_usable(region):
    backend = CaptureBackend()
    backend.values["region"] = [region]
    target = capture_foreground_window_context(
        facts_backend=backend,
    )

    assert target["usable"] is False
    assert target["failure_type"] == "target_binding_unverified"


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
    backend = CaptureBackend()
    backend.values["creation"] = [0]
    target = capture_foreground_window_context(
        4412,
        facts_backend=backend,
    )

    assert target["usable"] is False
    assert target["failure_type"] == "target_binding_unverified"


@pytest.mark.parametrize("changed_fact", ["hwnd", "root", "pid", "identity"])
def test_foreground_capture_rejects_identity_drift_between_samples(
    changed_fact: str,
):
    backend = CaptureBackend()
    if changed_fact == "hwnd":
        backend.values["foreground"] = [99, 99, 100]
    elif changed_fact == "root":
        backend.values["root"] = [99, 99, 100]
    elif changed_fact == "pid":
        backend.values["pid"] = [8824, 8824, 8825]
    else:
        backend.values["creation"] = [
            1_000_000_000, 1_000_000_000, 2_000_000_000,
        ]
    target = capture_foreground_window_context(
        4412,
        facts_backend=backend,
    )

    assert target["usable"] is False
    assert target["failure_type"] == "target_identity_changed_during_capture"


def test_foreground_capture_rejects_missing_root_window_identity():
    backend = CaptureBackend()
    backend.values["root"] = [0]
    target = capture_foreground_window_context(
        facts_backend=backend,
    )

    assert target["usable"] is False
    assert target["failure_type"] == "target_binding_unverified"


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
