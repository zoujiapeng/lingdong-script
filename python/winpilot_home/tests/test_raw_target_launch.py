from __future__ import annotations

from pathlib import Path
import random

import pytest

from winpilot.action.raw_device import (
    AdmittedPhysicalTargetLaunchBackend,
    FinalTargetLaunchGuardDecision,
    RawDeviceBackends,
    RawDeviceExecutor,
    RawTargetLaunchContext,
    RawTargetLaunchReceipt,
)
from winpilot.action.replay_policy import MutationOutcome
from winpilot.action.windows_target_launch import (
    PyWin32CreateProcessWApi,
    WindowsCreatedProcess,
    WindowsCreateProcessWBackend,
    WindowsLaunchProcessIdentity,
)
from winpilot.process_identity import process_identity_for

EXE = r"C:\Apps\Workspace.exe"
PID = 51
PROCESS_IDENTITY = process_identity_for(
    process_id=PID,
    creation_time_ns=200,
    executable_path=EXE,
)
CREATION_TIME_NS = 200


def _identity(
    *,
    process_id: int = PID,
    creation_time_ns: int = CREATION_TIME_NS,
    executable_path: str = EXE,
    process_identity: str | None = None,
) -> WindowsLaunchProcessIdentity:
    identity = process_identity or process_identity_for(
        process_id=process_id,
        creation_time_ns=creation_time_ns,
        executable_path=executable_path,
    )
    return WindowsLaunchProcessIdentity(
        process_id,
        creation_time_ns,
        executable_path,
        identity,
    )


class _Api:
    def __init__(
        self,
        *,
        created: object = WindowsCreatedProcess(101, 202, PID),
        create_error: bool = False,
        handle_pid: int = PID,
        close_results: dict[object, object] | None = None,
        events: list[object] | None = None,
    ) -> None:
        self.created = created
        self.create_error = create_error
        self.handle_pid = handle_pid
        self.close_results = close_results or {}
        self.events = events if events is not None else []
        self.create_calls = 0

    def create_process_w(self, executable_path: str) -> object:
        self.create_calls += 1
        self.events.append(("create", executable_path))
        if self.create_error:
            raise OSError("injected CreateProcessW uncertainty")
        return self.created

    def process_id(self, process_handle: object) -> int:
        self.events.append(("process_id", process_handle))
        return self.handle_pid

    def close_handle(self, handle: object) -> bool:
        self.events.append(("close", handle))
        result = self.close_results.get(handle, True)
        if isinstance(result, Exception):
            raise result
        return result


def _backend(
    api: _Api,
    *,
    observed: object = None,
    identity_error: bool = False,
) -> WindowsCreateProcessWBackend:
    resolved = _identity() if observed is None else observed

    def read_identity(process_id: int) -> object:
        api.events.append(("identity", process_id))
        if identity_error:
            raise OSError("injected identity failure")
        return resolved

    return WindowsCreateProcessWBackend(read_identity, api=api)


@pytest.mark.parametrize(
    "value",
    (
        "Workspace.exe",
        r"C:\Apps\Workspace.lnk",
        r'"C:\Apps\Workspace.exe"',
        r"C:\Apps\folder\..\Workspace.exe",
        "C:\\Apps\\Workspace.exe\x00--arg",
        "C:\\Apps\\Workspace.exe\n",
        7,
        None,
    ),
)
def test_launch_context_accepts_only_one_absolute_exe_path(value: object) -> None:
    with pytest.raises(ValueError, match="absolute .exe"):
        RawTargetLaunchContext(value)

    assert RawTargetLaunchContext(EXE).executable_path == EXE


@pytest.mark.parametrize(
    "values",
    (
        (MutationOutcome.APPLIED, 0, PID, PROCESS_IDENTITY, None),
        (MutationOutcome.APPLIED, 1, None, None, None),
        (MutationOutcome.NOT_STARTED, 1, None, None, "stale"),
        (MutationOutcome.NOT_APPLIED, 0, None, None, "not_applied"),
        (MutationOutcome.OUTCOME_UNKNOWN, 0, None, None, "uncertain"),
        (MutationOutcome.OUTCOME_UNKNOWN, 1, PID, PROCESS_IDENTITY, "uncertain"),
    ),
)
def test_launch_receipt_rejects_contradictory_three_state_facts(values) -> None:
    with pytest.raises(ValueError, match="contradict"):
        RawTargetLaunchReceipt(
            values[0],
            values[1],
            process_id=values[2],
            process_identity=values[3],
            error_kind=values[4],
        )


def test_launch_receipt_rejects_non_integer_request_count() -> None:
    with pytest.raises(ValueError, match="zero, one, or null"):
        RawTargetLaunchReceipt(
            MutationOutcome.NOT_STARTED,
            1.0,
            error_kind="invalid_count",
        )


def test_raw_device_launch_slot_is_strict_and_private_inventory_is_explicit() -> None:
    with pytest.raises(TypeError, match="AdmittedPhysicalTargetLaunchBackend"):
        RawDeviceBackends(admitted_physical_target_launch=object())
    wrapper = AdmittedPhysicalTargetLaunchBackend(
        lambda *_args, **_kwargs: RawTargetLaunchReceipt(
            MutationOutcome.NOT_STARTED,
            0,
            error_kind="injected",
        )
    )
    raw = RawDeviceExecutor(RawDeviceBackends(admitted_physical_target_launch=wrapper))

    assert wrapper.execute is not None
    assert "launch_target" in raw.admitted_action_names
    assert (
        "launch_target"
        not in RawDeviceExecutor(RawDeviceBackends()).admitted_action_names
    )


def test_raw_device_rejects_before_backend_and_never_falls_back() -> None:
    calls: list[object] = []
    wrapper = AdmittedPhysicalTargetLaunchBackend(
        lambda *args, **kwargs: calls.append((args, kwargs))
    )
    raw = RawDeviceExecutor(RawDeviceBackends(admitted_physical_target_launch=wrapper))

    invalid_context = raw.launch_target(
        object(),
        final_guard=lambda: FinalTargetLaunchGuardDecision.ADMITTED,
    )
    invalid_guard = raw.launch_target(
        RawTargetLaunchContext(EXE),
        final_guard=object(),
    )
    unavailable = RawDeviceExecutor(RawDeviceBackends()).launch_target(
        RawTargetLaunchContext(EXE),
        final_guard=lambda: FinalTargetLaunchGuardDecision.ADMITTED,
    )

    assert [item.outcome for item in (invalid_context, invalid_guard, unavailable)] == [
        MutationOutcome.NOT_STARTED,
        MutationOutcome.NOT_STARTED,
        MutationOutcome.NOT_STARTED,
    ]
    assert [
        item.launch_request_count
        for item in (
            invalid_context,
            invalid_guard,
            unavailable,
        )
    ] == [0, 0, 0]
    assert calls == []


@pytest.mark.parametrize("backend_result", ("raise", object()))
def test_raw_device_backend_uncertainty_has_no_identity_or_internal_retry(
    backend_result: object,
) -> None:
    calls: list[str] = []

    def backend(*_args, **_kwargs):
        calls.append("backend")
        if backend_result == "raise":
            raise OSError("uncertain")
        return backend_result

    receipt = RawDeviceExecutor(
        RawDeviceBackends(
            admitted_physical_target_launch=AdmittedPhysicalTargetLaunchBackend(backend)
        )
    ).launch_target(
        RawTargetLaunchContext(EXE),
        final_guard=lambda: FinalTargetLaunchGuardDecision.ADMITTED,
    )

    assert calls == ["backend"]
    assert receipt.outcome is MutationOutcome.OUTCOME_UNKNOWN
    assert receipt.launch_request_count is None
    assert receipt.process_id is receipt.process_identity is None


def test_create_process_success_closes_thread_then_reads_identity_then_closes_process() -> (
    None
):
    events: list[object] = []
    api = _Api(events=events)
    backend = _backend(api)

    receipt = backend(
        RawTargetLaunchContext(EXE),
        final_guard=lambda: (
            events.append("guard") or FinalTargetLaunchGuardDecision.ADMITTED
        ),
    )

    assert events == [
        "guard",
        ("create", EXE),
        ("close", 202),
        ("process_id", 101),
        ("identity", PID),
        ("close", 101),
    ]
    assert receipt == RawTargetLaunchReceipt(
        MutationOutcome.APPLIED,
        1,
        process_id=PID,
        process_identity=PROCESS_IDENTITY,
    )
    assert "handle" not in repr(receipt.to_dict()).casefold()
    assert api.create_calls == 1


@pytest.mark.parametrize(
    "decision",
    tuple(
        decision
        for decision in FinalTargetLaunchGuardDecision
        if decision is not FinalTargetLaunchGuardDecision.ADMITTED
    ),
)
def test_final_guard_is_last_gate_and_rejection_makes_no_request(decision) -> None:
    api = _Api()
    receipt = _backend(api)(
        RawTargetLaunchContext(EXE),
        final_guard=lambda: decision,
    )

    assert receipt.outcome is MutationOutcome.NOT_STARTED
    assert receipt.launch_request_count == 0
    assert receipt.process_id is receipt.process_identity is None
    assert api.create_calls == 0
    assert api.events == []


@pytest.mark.parametrize(
    "scenario",
    (
        "create_error",
        "malformed_result",
        "thread_close_false",
        "process_close_false",
        "handle_pid_mismatch",
        "identity_error",
        "identity_pid_mismatch",
        "identity_path_mismatch",
        "identity_result_invalid",
    ),
)
def test_any_post_boundary_uncertainty_is_unknown_without_identity(
    scenario: str,
) -> None:
    kwargs: dict[str, object] = {}
    observed: object = _identity()
    identity_error = False
    if scenario == "create_error":
        kwargs["create_error"] = True
    elif scenario == "malformed_result":
        kwargs["created"] = object()
    elif scenario == "thread_close_false":
        kwargs["close_results"] = {202: False}
    elif scenario == "process_close_false":
        kwargs["close_results"] = {101: OSError("uncertain close")}
    elif scenario == "handle_pid_mismatch":
        kwargs["handle_pid"] = PID + 1
    elif scenario == "identity_error":
        identity_error = True
    elif scenario == "identity_pid_mismatch":
        observed = _identity(process_id=PID + 1)
    elif scenario == "identity_path_mismatch":
        observed = _identity(executable_path=r"C:\Apps\Other.exe")
    elif scenario == "identity_result_invalid":
        observed = object()
    api = _Api(**kwargs)
    backend = _backend(
        api,
        observed=observed,
        identity_error=identity_error,
    )

    receipt = backend(
        RawTargetLaunchContext(EXE),
        final_guard=lambda: FinalTargetLaunchGuardDecision.ADMITTED,
    )

    assert receipt.outcome is MutationOutcome.OUTCOME_UNKNOWN
    assert receipt.launch_request_count == 1
    assert receipt.process_id is receipt.process_identity is None
    assert api.create_calls == 1
    assert api.events.count(("close", 202)) <= 1
    assert api.events.count(("close", 101)) <= 1


def test_launch_identity_rejects_forged_content_hash() -> None:
    with pytest.raises(ValueError, match="does not match"):
        WindowsLaunchProcessIdentity(
            PID,
            CREATION_TIME_NS,
            EXE,
            process_identity_for(
                process_id=PID,
                creation_time_ns=CREATION_TIME_NS + 1,
                executable_path=EXE,
            ),
        )


def test_aliased_or_invalid_handles_are_never_closed_twice() -> None:
    for created, expected_closes in (
        (WindowsCreatedProcess(101, 101, PID), [("close", 101)]),
        (WindowsCreatedProcess(None, 202, PID), [("close", 202)]),
    ):
        api = _Api(created=created)
        receipt = _backend(api)(
            RawTargetLaunchContext(EXE),
            final_guard=lambda: FinalTargetLaunchGuardDecision.ADMITTED,
        )

        assert receipt.outcome is MutationOutcome.OUTCOME_UNKNOWN
        assert [event for event in api.events if event[0] == "close"] == expected_closes


def test_random_failure_points_never_repeat_create_or_close_handles() -> None:
    rng = random.Random(20260723)
    for _ in range(100):
        thread_ok, process_ok = rng.choice((True, False)), rng.choice((True, False))
        identity_ok, pid_ok = rng.choice((True, False)), rng.choice((True, False))
        api = _Api(
            handle_pid=PID if pid_ok else PID + 1,
            close_results={202: thread_ok, 101: process_ok},
        )
        receipt = _backend(api, identity_error=not identity_ok)(
            RawTargetLaunchContext(EXE),
            final_guard=lambda: FinalTargetLaunchGuardDecision.ADMITTED,
        )

        assert api.create_calls == 1
        assert api.events.count(("close", 202)) == 1
        assert api.events.count(("close", 101)) == 1
        assert receipt.outcome in {
            MutationOutcome.APPLIED,
            MutationOutcome.OUTCOME_UNKNOWN,
        }
        if receipt.outcome is MutationOutcome.OUTCOME_UNKNOWN:
            assert receipt.process_id is receipt.process_identity is None


def test_pywin32_boundary_passes_exact_application_and_no_command_line(
    monkeypatch,
) -> None:
    calls: list[tuple[object, ...]] = []
    process, thread = object(), object()
    monkeypatch.setattr(
        "winpilot.action.windows_target_launch.win32process.STARTUPINFO",
        lambda: "startup",
    )
    monkeypatch.setattr(
        "winpilot.action.windows_target_launch.win32process.CreateProcess",
        lambda *args: calls.append(args) or (process, thread, PID, 7),
    )

    created = PyWin32CreateProcessWApi.create_process_w(EXE)

    assert created == WindowsCreatedProcess(process, thread, PID)
    assert calls == [
        (
            EXE,
            None,
            None,
            None,
            False,
            0,
            None,
            None,
            "startup",
        )
    ]


def test_production_backend_source_contains_no_shell_or_fallback_route() -> None:
    source = (
        (Path(__file__).parents[1] / "winpilot" / "action" / "windows_target_launch.py")
        .read_text(encoding="utf-8")
        .casefold()
    )

    for forbidden in (
        "subprocess",
        "shellexecute",
        "os.startfile",
        "popen",
        "appid",
        "fallback",
    ):
        if forbidden == "fallback":
            assert source.count(forbidden) == 1  # module boundary declaration only
        else:
            assert forbidden not in source
