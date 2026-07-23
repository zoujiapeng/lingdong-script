from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import random
import sys
from types import SimpleNamespace

import pytest

from winpilot.action.raw_device import (
    FinalKeyboardGuardDecision,
    FinalPointerGuardDecision,
)
from winpilot.visual_runtime.commit_admission import (
    CommitAdmissionError,
    CommitAdmissionGate,
    CommitInputKind,
    CommitAdmissionReason,
    CommitAdmissionRequest,
    CommitEnvironment,
    FrameCommitIdentity,
    TargetCommitIdentity,
)
from winpilot.process_identity import (
    ProcessIdentityError,
    process_identity_for,
)
from winpilot.visual_runtime.windows_desktop_facts import (
    PyWin32DesktopFactsBackend,
    RuntimeCommitState,
    TargetActivationFacts,
    WindowsDesktopCommitObservation,
    WindowsDesktopFactsDriftError,
    WindowsDesktopFactsError,
    WindowsDesktopFactsReader,
    WindowsDesktopFactsSourceError,
    WindowsPhysicalInputFinalGuard,
)


class Backend:
    def __init__(self) -> None:
        self.live = {101, 100, 111}
        self.roots = {101: 100, 100: 100, 111: 100}
        self.pids = {101: 7, 100: 7, 111: 7}
        self.creation_times = {7: 1_700_000_000_123_456_000}
        self.executable_paths = {7: r"C:\Apps\Host\host.exe"}
        self.regions = {101: (10, 20, 800, 600)}
        self.foreground = 100
        self.hit = 111
        self.titles = {101: "Workspace"}
        self.calls = []
        self.target_sample_count = 0
        self.second_target_region = None
        self.second_target_creation_time = None
        self.process_creation_overrides = {}
        self.process_sample_count = 0

    def is_window(self, hwnd):
        self.calls.append(("is_window", hwnd))
        return hwnd in self.live

    def root_hwnd(self, hwnd):
        self.calls.append(("root_hwnd", hwnd))
        return self.roots[hwnd]

    def process_id(self, hwnd):
        self.calls.append(("process_id", hwnd))
        return self.pids[hwnd]

    def process_creation_time_ns(self, process_id):
        self.calls.append(("process_creation_time_ns", process_id))
        self.process_sample_count += 1
        if self.process_sample_count in self.process_creation_overrides:
            return self.process_creation_overrides[self.process_sample_count]
        if (
            self.process_sample_count == 2
            and self.second_target_creation_time is not None
        ):
            return self.second_target_creation_time
        return self.creation_times[process_id]

    def process_executable_path(self, process_id):
        self.calls.append(("process_executable_path", process_id))
        return self.executable_paths[process_id]

    def window_region(self, hwnd):
        self.calls.append(("window_region", hwnd))
        self.target_sample_count += 1
        if self.target_sample_count == 2 and self.second_target_region is not None:
            return self.second_target_region
        return self.regions[hwnd]

    def foreground_hwnd(self):
        self.calls.append(("foreground_hwnd",))
        return self.foreground

    def window_title(self, hwnd):
        self.calls.append(("window_title", hwnd))
        return self.titles.get(hwnd, "")

    def window_from_point(self, point):
        self.calls.append(("window_from_point", point))
        return self.hit


class RuntimeState:
    def __init__(self, state=None):
        self.state = state or RuntimeCommitState(True, True, False, False)
        self.requests = []

    def read_commit_state(self, request):
        self.requests.append(request)
        return self.state


def _process_identity(
    process_id: int = 7,
    *,
    creation_time_ns: int = 1_700_000_000_123_456_000,
    executable_path: str = r"C:\Apps\Host\host.exe",
) -> str:
    return process_identity_for(
        process_id=process_id,
        creation_time_ns=creation_time_ns,
        executable_path=executable_path,
    )


def test_neutral_process_identity_is_canonical_and_rejects_weak_inputs():
    identity = process_identity_for(
        process_id=7,
        creation_time_ns=1_700_000_000_123_456_000,
        executable_path=r"C:/Apps/Host/../Host/HOST.EXE",
    )

    assert identity == _process_identity()
    assert len(identity) == len("process-") + 32
    assert identity.startswith("process-")
    for path in ("host.exe", r"\Apps\host.exe", r"C:host.exe"):
        with pytest.raises(ProcessIdentityError, match="absolute"):
            process_identity_for(
                process_id=7,
                creation_time_ns=1,
                executable_path=path,
            )


def test_neutral_process_identity_does_not_depend_on_vnext():
    tree = ast.parse(Path(
        "python/winpilot_home/winpilot/process_identity.py"
    ).read_text(encoding="utf-8"))
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imports.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert not any("vnext" in module.casefold().split(".") for module in imports)
    with pytest.raises(ProcessIdentityError, match="positive integer"):
        process_identity_for(
            process_id=True,
            creation_time_ns=1,
            executable_path=r"C:\Apps\host.exe",
        )


def test_target_commit_identity_requires_and_round_trips_strong_identity():
    target = _request().target

    assert TargetCommitIdentity.from_dict(target.to_dict()) == target
    assert target.to_dict()["process_identity"] == _process_identity()
    with pytest.raises(CommitAdmissionError, match="process-<32hex>"):
        replace(target, process_identity="pid:7")


def test_pywin32_backend_reads_process_creation_time_and_absolute_executable(
    monkeypatch,
):
    class Process:
        def __init__(self, process_id):
            assert process_id == 7

        @staticmethod
        def create_time():
            return 1_700_000_000.125

        @staticmethod
        def exe():
            return r"C:\Apps\Host\host.exe"

    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=Process))
    backend = PyWin32DesktopFactsBackend()

    assert backend.process_creation_time_ns(7) == int(1_700_000_000.125 * 1_000_000_000)
    assert backend.process_executable_path(7) == r"C:\Apps\Host\host.exe"


@pytest.mark.parametrize("method", ("create_time", "exe"))
def test_pywin32_backend_types_psutil_failures_as_source_errors(
    monkeypatch,
    method,
):
    class Process:
        def __init__(self, process_id):
            assert process_id == 7

        @staticmethod
        def create_time():
            if method == "create_time":
                raise RuntimeError("opaque")
            return 1_700_000_000.125

        @staticmethod
        def exe():
            if method == "exe":
                raise RuntimeError("opaque")
            return r"C:\Apps\Host\host.exe"

    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=Process))
    backend = PyWin32DesktopFactsBackend()
    read = (
        backend.process_creation_time_ns
        if method == "create_time"
        else backend.process_executable_path
    )

    with pytest.raises(WindowsDesktopFactsSourceError):
        read(7)


def _request(**changes) -> CommitAdmissionRequest:
    request = CommitAdmissionRequest(
        request_id="decision-1",
        frame=FrameCommitIdentity(
            5, 3, 101, (10, 20, 800, 600), "target_window",
            (800, 600), "a" * 64,
        ),
        target=TargetCommitIdentity(
            binding_id="binding-1",
            hwnd=101,
            root_hwnd=100,
            process_id=7,
            process_identity=_process_identity(),
            epoch=3,
            window_region=(10, 20, 800, 600),
        ),
        route="physical",
        input_kind="point",
        screen_point=(150, 140),
    )
    return replace(request, **changes)


def _keyboard_request(**changes) -> CommitAdmissionRequest:
    values = {
        "input_kind": CommitInputKind.KEYBOARD,
        "screen_point": None,
    }
    values.update(changes)
    return _request(**values)


def test_reader_uses_os_identity_but_only_carries_runtime_binding_and_epoch():
    backend = Backend()
    request = _request(target=TargetCommitIdentity(
        binding_id="binding-runtime",
        hwnd=101,
        root_hwnd=999,
        process_id=999,
        process_identity=_process_identity(
            999,
            executable_path=r"C:\Apps\Old\old.exe",
        ),
        epoch=8,
        window_region=(1, 2, 3, 4),
    ), frame=FrameCommitIdentity(
        5, 8, 101, (10, 20, 800, 600), "target_window",
        (800, 600), "a" * 64,
    ))

    facts = WindowsDesktopFactsReader(backend).read(request)

    assert facts.target == TargetCommitIdentity(
        binding_id="binding-runtime",
        hwnd=101,
        root_hwnd=100,
        process_id=7,
        process_identity=_process_identity(),
        epoch=8,
        window_region=(10, 20, 800, 600),
    )
    assert (facts.foreground.hwnd, facts.foreground.root_hwnd,
            facts.foreground.process_id) == (100, 100, 7)
    assert facts.foreground.process_identity == _process_identity()
    assert (facts.hit.hwnd, facts.hit.root_hwnd, facts.hit.process_id) == (
        111, 100, 7,
    )
    assert facts.hit.process_identity == _process_identity()
    region_reads = [
        index for index, call in enumerate(backend.calls)
        if call == ("window_region", 101)
    ]
    assert len(region_reads) == 2
    assert region_reads[0] < backend.calls.index(("foreground_hwnd",)) < region_reads[1]


def test_reader_exposes_stable_target_identity_for_runtime_composition():
    backend = Backend()

    target = WindowsDesktopFactsReader(backend).read_target_identity(
        101,
        binding_id="binding-runtime",
        epoch=4,
    )

    assert target == TargetCommitIdentity(
        binding_id="binding-runtime",
        hwnd=101,
        root_hwnd=100,
        process_id=7,
        process_identity=_process_identity(),
        epoch=4,
        window_region=(10, 20, 800, 600),
    )
    assert backend.calls.count(("window_region", 101)) == 2
    assert ("foreground_hwnd",) not in backend.calls
    assert not any(call[0] == "window_from_point" for call in backend.calls)


def test_activation_facts_double_sample_exact_target_and_foreground() -> None:
    backend = Backend()
    backend.foreground = 101

    facts = WindowsDesktopFactsReader(backend).read_activation_facts(101)

    assert facts == TargetActivationFacts(
        target=facts.target,
        window_region=(10, 20, 800, 600),
        foreground=facts.target,
        title_snapshot="Workspace",
    )
    assert facts.target == facts.foreground
    assert facts.exact_foreground is True
    region_reads = [
        index for index, call in enumerate(backend.calls)
        if call == ("window_region", 101)
    ]
    assert len(region_reads) == 2
    foreground_read = backend.calls.index(("foreground_hwnd",))
    assert region_reads[0] < foreground_read < region_reads[1]


def test_activation_facts_do_not_treat_same_root_sibling_as_exact_foreground() -> None:
    backend = Backend()
    backend.foreground = 100

    facts = WindowsDesktopFactsReader(backend).read_activation_facts(101)

    assert facts.foreground is not None
    assert facts.foreground.root_hwnd == facts.target.root_hwnd
    assert facts.foreground.hwnd != facts.target.hwnd
    assert facts.exact_foreground is False


def test_activation_facts_fail_closed_on_identity_or_region_drift() -> None:
    backend = Backend()
    backend.second_target_region = (11, 20, 800, 600)

    with pytest.raises(WindowsDesktopFactsError, match="activation target changed"):
        WindowsDesktopFactsReader(backend).read_activation_facts(101)


def test_strict_activation_does_not_turn_foreground_identity_failure_into_drift():
    backend = Backend()
    backend.foreground = 101
    backend.process_creation_overrides[2] = 0

    with pytest.raises(WindowsDesktopFactsError) as captured:
        WindowsDesktopFactsReader(backend).read_activation_facts(
            101,
            strict_foreground=True,
        )

    assert not isinstance(captured.value, WindowsDesktopFactsDriftError)


def test_activation_title_is_optional_and_never_substitutes_for_identity() -> None:
    backend = Backend()
    backend.window_title = None

    facts = WindowsDesktopFactsReader(backend).read_activation_facts(101)

    assert facts.title_snapshot == ""
    assert facts.target.process_identity == _process_identity()


def test_keyboard_reader_never_calls_window_from_point_or_fabricates_hit():
    backend = Backend()

    facts = WindowsDesktopFactsReader(backend).read(_keyboard_request())

    assert facts.hit_test_point is None
    assert facts.hit is None
    assert (facts.foreground.hwnd, facts.foreground.root_hwnd,
            facts.foreground.process_id) == (100, 100, 7)
    assert not any(call[0] == "window_from_point" for call in backend.calls)
    assert backend.calls.count(("window_region", 101)) == 2


def test_runtime_composition_target_read_fails_closed_on_sample_drift():
    backend = Backend()
    backend.second_target_region = (11, 20, 800, 600)

    with pytest.raises(WindowsDesktopFactsError, match="changed"):
        WindowsDesktopFactsReader(backend).read_target_identity(
            101,
            binding_id="binding-runtime",
            epoch=4,
        )


def test_runtime_composition_target_read_fails_closed_on_process_reuse():
    backend = Backend()
    backend.second_target_creation_time = (
        backend.creation_times[7] + 1
    )

    with pytest.raises(WindowsDesktopFactsError, match="changed"):
        WindowsDesktopFactsReader(backend).read_target_identity(
            101,
            binding_id="binding-runtime",
            epoch=4,
        )


@pytest.mark.parametrize("changed_fact", ["creation_time", "executable_path"])
def test_commit_gate_rejects_same_hwnd_and_pid_after_process_reuse(changed_fact):
    backend = Backend()
    if changed_fact == "creation_time":
        backend.creation_times[7] += 1
    else:
        backend.executable_paths[7] = r"C:\Apps\Replacement\replacement.exe"
    observation = WindowsDesktopCommitObservation(
        WindowsDesktopFactsReader(backend), RuntimeState(),
    )
    calls = []

    result = CommitAdmissionGate(observation.observe_commit).admit_and_commit(
        _request(), lambda: calls.append("raw"),
    )

    assert result.admission.reason is CommitAdmissionReason.TARGET_CHANGED
    assert calls == []


def test_commit_gate_fails_closed_when_target_executable_is_unavailable():
    backend = Backend()
    del backend.executable_paths[7]
    observation = WindowsDesktopCommitObservation(
        WindowsDesktopFactsReader(backend), RuntimeState(),
    )
    calls = []

    result = CommitAdmissionGate(observation.observe_commit).admit_and_commit(
        _request(), lambda: calls.append("raw"),
    )

    assert result.admission.reason is CommitAdmissionReason.OBSERVATION_FAILED
    assert calls == []


@pytest.mark.parametrize(
    ("request_factory", "sample_index", "reason"),
    (
        (_request, 2, CommitAdmissionReason.FOREGROUND_CHANGED),
        (_request, 3, CommitAdmissionReason.HIT_TEST_CHANGED),
        (_keyboard_request, 2, CommitAdmissionReason.FOREGROUND_CHANGED),
    ),
)
def test_current_guard_compares_strong_foreground_and_hit_process_identity(
    request_factory, sample_index, reason,
):
    backend = Backend()
    backend.process_creation_overrides[sample_index] = (
        backend.creation_times[7] + 1
    )
    observation = WindowsDesktopCommitObservation(
        WindowsDesktopFactsReader(backend), RuntimeState(),
    )
    calls = []

    result = CommitAdmissionGate(observation.observe_commit).admit_and_commit(
        request_factory(), lambda: calls.append("raw"),
    )

    assert result.admission.reason is reason
    assert calls == []


def test_adapter_builds_environment_and_gate_admits_once():
    backend = Backend()
    runtime = RuntimeState()
    request = _request()
    observation = WindowsDesktopCommitObservation(
        WindowsDesktopFactsReader(backend), runtime,
    )
    calls = []

    result = CommitAdmissionGate(observation.observe_commit).admit_and_commit(
        request, lambda: calls.append("raw") or "receipt",
    )

    assert result.admission.reason is CommitAdmissionReason.ADMITTED
    assert result.value == "receipt"
    assert calls == ["raw"]
    assert runtime.requests == [request]


def test_keyboard_observation_and_gate_admit_without_hit_test():
    backend = Backend()
    runtime = RuntimeState()
    request = _keyboard_request()
    observation = WindowsDesktopCommitObservation(
        WindowsDesktopFactsReader(backend), runtime,
    )
    calls = []

    result = CommitAdmissionGate(observation.observe_commit).admit_and_commit(
        request, lambda: calls.append("keyboard") or "receipt",
    )

    assert result.admission.reason is CommitAdmissionReason.ADMITTED
    assert result.value == "receipt"
    assert calls == ["keyboard"]
    assert runtime.requests == [request]
    assert not any(call[0] == "window_from_point" for call in backend.calls)


@pytest.mark.parametrize(
    ("state", "reason"),
    (
        (RuntimeCommitState(False, True, False, False),
         CommitAdmissionReason.POINTER_NOT_RELEASED),
        (RuntimeCommitState(True, False, False, False),
         CommitAdmissionReason.MUTATION_NOT_ALLOWED),
        (RuntimeCommitState(True, True, True, False),
         CommitAdmissionReason.STOP_REQUESTED),
        (RuntimeCommitState(True, True, False, True),
         CommitAdmissionReason.CANCELED),
    ),
)
def test_runtime_commit_state_is_not_inferred_from_request(state, reason):
    request = _request()
    observation = WindowsDesktopCommitObservation(
        WindowsDesktopFactsReader(Backend()), RuntimeState(state),
    )
    calls = []

    result = CommitAdmissionGate(observation.observe_commit).admit_and_commit(
        request, lambda: calls.append(True),
    )

    assert result.admission.reason is reason
    assert calls == []


def test_target_change_between_samples_fails_closed_before_callback():
    backend = Backend()
    backend.second_target_region = (11, 20, 800, 600)
    observation = WindowsDesktopCommitObservation(
        WindowsDesktopFactsReader(backend), RuntimeState(),
    )
    calls = []

    with pytest.raises(WindowsDesktopFactsError, match="changed"):
        observation.observe_commit(_request())
    gate_backend = Backend()
    gate_backend.second_target_region = (11, 20, 800, 600)
    gate_observation = WindowsDesktopCommitObservation(
        WindowsDesktopFactsReader(gate_backend), RuntimeState(),
    )
    result = CommitAdmissionGate(gate_observation.observe_commit).admit_and_commit(
        _request(), lambda: calls.append(True),
    )
    assert result.admission.reason is CommitAdmissionReason.OBSERVATION_FAILED
    assert calls == []


def test_final_pointer_guard_reads_no_foreground_and_admits_exact_target_hit():
    backend = Backend()
    guard = WindowsPhysicalInputFinalGuard(WindowsDesktopFactsReader(backend))

    decision = guard.check_before_pointer_input(_request(), (150, 140))

    assert decision is FinalPointerGuardDecision.ADMITTED
    assert guard.check_before_pointer_input(
        _request(), (151, 140)
    ) is FinalPointerGuardDecision.HIT_TEST_CHANGED
    assert ("foreground_hwnd",) not in backend.calls
    assert backend.calls.count(("window_region", 101)) == 2
    assert backend.calls.count(("window_from_point", (150, 140))) == 1


def test_final_keyboard_guard_reads_foreground_without_hit_test_and_admits():
    backend = Backend()
    guard = WindowsPhysicalInputFinalGuard(WindowsDesktopFactsReader(backend))

    decision = guard.check_before_keyboard_input(_keyboard_request())

    assert decision is FinalKeyboardGuardDecision.ADMITTED
    assert backend.calls.count(("foreground_hwnd",)) == 1
    assert backend.calls.count(("window_region", 101)) == 2
    assert not any(call[0] == "window_from_point" for call in backend.calls)


@pytest.mark.parametrize("kind", [CommitInputKind.POINT, CommitInputKind.KEYBOARD])
def test_final_guard_rejects_same_hwnd_and_pid_after_process_reuse(kind):
    backend = Backend()
    backend.creation_times[7] += 1
    guard = WindowsPhysicalInputFinalGuard(WindowsDesktopFactsReader(backend))

    if kind is CommitInputKind.POINT:
        actual = guard.check_before_pointer_input(_request(), (150, 140))
        expected = FinalPointerGuardDecision.TARGET_PROCESS_CHANGED
    else:
        actual = guard.check_before_keyboard_input(_keyboard_request())
        expected = FinalKeyboardGuardDecision.TARGET_PROCESS_CHANGED

    assert actual is expected


@pytest.mark.parametrize(
    ("kind", "expected"),
    (
        (CommitInputKind.POINT, FinalPointerGuardDecision.HIT_TEST_CHANGED),
        (CommitInputKind.KEYBOARD, FinalKeyboardGuardDecision.FOREGROUND_CHANGED),
    ),
)
def test_final_guard_compares_strong_related_window_process_identity(kind, expected):
    backend = Backend()
    backend.process_creation_overrides[2] = backend.creation_times[7] + 1
    guard = WindowsPhysicalInputFinalGuard(WindowsDesktopFactsReader(backend))

    actual = (
        guard.check_before_pointer_input(_request(), (150, 140))
        if kind is CommitInputKind.POINT
        else guard.check_before_keyboard_input(_keyboard_request())
    )

    assert actual is expected


@pytest.mark.parametrize(
    ("change", "decision"),
    (
        ("target_unavailable", FinalKeyboardGuardDecision.TARGET_UNAVAILABLE),
        ("target_root", FinalKeyboardGuardDecision.TARGET_ROOT_CHANGED),
        ("target_pid", FinalKeyboardGuardDecision.TARGET_PROCESS_CHANGED),
        ("target_geometry", FinalKeyboardGuardDecision.TARGET_GEOMETRY_CHANGED),
        ("foreground_unavailable", FinalKeyboardGuardDecision.FOREGROUND_UNAVAILABLE),
        ("foreground_root", FinalKeyboardGuardDecision.FOREGROUND_CHANGED),
        ("foreground_pid", FinalKeyboardGuardDecision.FOREGROUND_CHANGED),
    ),
)
def test_final_keyboard_guard_fails_closed_on_stale_target_or_foreground(
    change, decision,
):
    backend = Backend()
    if change == "target_unavailable":
        backend.live.remove(101)
    elif change == "target_root":
        backend.roots[101] = 999
    elif change == "target_pid":
        backend.pids[101] = 999
        backend.creation_times[999] = backend.creation_times[7]
        backend.executable_paths[999] = r"C:\Apps\Other\other.exe"
    elif change == "target_geometry":
        backend.regions[101] = (11, 20, 800, 600)
    elif change == "foreground_unavailable":
        backend.foreground = 0
    elif change == "foreground_root":
        backend.roots[100] = 999
    else:
        backend.pids[100] = 999
        backend.creation_times[999] = backend.creation_times[7]
        backend.executable_paths[999] = r"C:\Apps\Other\other.exe"

    actual = WindowsPhysicalInputFinalGuard(
        WindowsDesktopFactsReader(backend)
    ).check_before_keyboard_input(_keyboard_request())

    assert actual is decision
    assert not any(call[0] == "window_from_point" for call in backend.calls)


def test_randomized_final_keyboard_guard_replays_identity_without_hit_testing():
    rng = random.Random(20260723)
    for index in range(30):
        target_hwnd = 1000 + index
        root_hwnd = 2000 + index
        process_id = 3000 + index
        region = (
            rng.randint(-500, 500),
            rng.randint(-300, 300),
            rng.randint(50, 1000),
            rng.randint(50, 800),
        )
        backend = Backend()
        backend.live = {target_hwnd, root_hwnd}
        backend.roots = {target_hwnd: root_hwnd, root_hwnd: root_hwnd}
        backend.pids = {target_hwnd: process_id, root_hwnd: process_id}
        creation_time_ns = 1_700_000_000_000_000_000 + index
        executable_path = fr"C:\Apps\Host\host-{index}.exe"
        backend.creation_times = {process_id: creation_time_ns}
        backend.executable_paths = {process_id: executable_path}
        backend.regions = {target_hwnd: region}
        backend.foreground = root_hwnd
        backend.hit = 0
        request = CommitAdmissionRequest(
            request_id=f"keyboard-{index}",
            frame=FrameCommitIdentity(
                index + 1,
                index,
                target_hwnd,
                region,
                "target_window",
                (region[2], region[3]),
                f"{index + 1:064x}",
            ),
            target=TargetCommitIdentity(
                binding_id=f"binding-{index}",
                hwnd=target_hwnd,
                root_hwnd=root_hwnd,
                process_id=process_id,
                process_identity=_process_identity(
                    process_id,
                    creation_time_ns=creation_time_ns,
                    executable_path=executable_path,
                ),
                epoch=index,
                window_region=region,
            ),
            route="physical",
            input_kind=CommitInputKind.KEYBOARD,
            screen_point=None,
        )
        guard = WindowsPhysicalInputFinalGuard(
            WindowsDesktopFactsReader(backend)
        )

        assert guard.check_before_keyboard_input(
            request,
        ) is FinalKeyboardGuardDecision.ADMITTED
        assert not any(call[0] == "window_from_point" for call in backend.calls)

        backend.pids[root_hwnd] += 1
        reused_pid = backend.pids[root_hwnd]
        backend.creation_times[reused_pid] = creation_time_ns + 1
        backend.executable_paths[reused_pid] = fr"C:\Apps\Other\other-{index}.exe"
        assert guard.check_before_keyboard_input(
            request,
        ) is FinalKeyboardGuardDecision.FOREGROUND_CHANGED


@pytest.mark.parametrize(
    ("change", "decision"),
    (
        ("root", FinalPointerGuardDecision.TARGET_ROOT_CHANGED),
        ("pid", FinalPointerGuardDecision.TARGET_PROCESS_CHANGED),
        ("geometry", FinalPointerGuardDecision.TARGET_GEOMETRY_CHANGED),
        ("hit", FinalPointerGuardDecision.HIT_TEST_CHANGED),
        ("hit_unavailable", FinalPointerGuardDecision.HIT_TEST_UNAVAILABLE),
        ("point", FinalPointerGuardDecision.POINT_OUTSIDE_TARGET),
    ),
)
def test_final_pointer_guard_fails_closed_on_each_mechanical_identity(
    change, decision,
):
    backend = Backend()
    request = _request()
    if change == "root":
        backend.roots[101] = 999
    elif change == "pid":
        backend.pids[101] = 999
        backend.creation_times[999] = backend.creation_times[7]
        backend.executable_paths[999] = r"C:\Apps\Other\other.exe"
    elif change == "geometry":
        backend.regions[101] = (11, 20, 800, 600)
    elif change == "hit":
        backend.roots[111] = 999
    elif change == "hit_unavailable":
        backend.hit = 0
    else:
        request = replace(request, screen_point=(900, 900))

    assert WindowsPhysicalInputFinalGuard(
        WindowsDesktopFactsReader(backend)
    ).check_before_pointer_input(request, request.screen_point) is decision


@pytest.mark.parametrize("live", ({100, 111}, {101, 100, 111}))
def test_target_is_window_failure_on_either_sample_is_fail_closed(live):
    backend = Backend()
    backend.live = live
    if 101 in live:
        original = backend.is_window
        count = 0

        def fail_second(hwnd):
            nonlocal count
            if hwnd == 101:
                count += 1
                if count == 2:
                    return False
            return original(hwnd)

        backend.is_window = fail_second

    with pytest.raises(WindowsDesktopFactsError, match="not a live window"):
        WindowsDesktopFactsReader(backend).read(_request())


def test_unavailable_foreground_or_hit_is_explicit_not_fabricated():
    backend = Backend()
    backend.foreground = 0
    backend.hit = 0

    environment = WindowsDesktopCommitObservation(
        WindowsDesktopFactsReader(backend), RuntimeState(),
    ).observe_commit(_request())

    assert environment.foreground_hwnd is None
    assert environment.foreground_root_hwnd is None
    assert environment.foreground_process_id is None
    assert environment.hit_test_point is None
    assert environment.hit_hwnd is None


def test_invalid_runtime_state_port_result_fails_closed():
    class InvalidState:
        def read_commit_state(self, _request):
            return {"mutation_allowed": True}

    observation = WindowsDesktopCommitObservation(
        WindowsDesktopFactsReader(Backend()), InvalidState(),
    )
    with pytest.raises(WindowsDesktopFactsError, match="invalid state"):
        observation.observe_commit(_request())


def test_runtime_state_and_facts_contracts_reject_boolean_coercion():
    with pytest.raises(WindowsDesktopFactsError, match="boolean"):
        RuntimeCommitState(1, True, False, False)  # type: ignore[arg-type]

    backend = Backend()
    backend.pids[101] = True
    with pytest.raises(WindowsDesktopFactsError, match="process id"):
        WindowsDesktopFactsReader(backend).read(_request())


def test_module_has_no_semantic_or_mutating_dependencies():
    source = Path(
        "python/winpilot_home/winpilot/visual_runtime/windows_desktop_facts.py"
    ).read_text(encoding="utf-8").casefold()
    for forbidden in (
        "setforegroundwindow", "uia", "ocr", "mcp", "focus_window",
        "missionruntime", "rawdevice", "controllerport",
    ):
        assert forbidden not in source
