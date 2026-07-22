from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from winpilot.action.raw_device import FinalClickGuardDecision
from winpilot.visual_runtime.commit_admission import (
    CommitAdmissionGate,
    CommitAdmissionReason,
    CommitAdmissionRequest,
    CommitEnvironment,
    FrameCommitIdentity,
    TargetCommitIdentity,
)
from winpilot.visual_runtime.windows_desktop_facts import (
    RuntimeCommitState,
    WindowsDesktopCommitObservation,
    WindowsDesktopFactsError,
    WindowsDesktopFactsReader,
    WindowsPhysicalClickFinalGuard,
)


class Backend:
    def __init__(self) -> None:
        self.live = {101, 100, 111}
        self.roots = {101: 100, 100: 100, 111: 100}
        self.pids = {101: 7, 100: 7, 111: 7}
        self.regions = {101: (10, 20, 800, 600)}
        self.foreground = 100
        self.hit = 111
        self.calls = []
        self.target_sample_count = 0
        self.second_target_region = None

    def is_window(self, hwnd):
        self.calls.append(("is_window", hwnd))
        return hwnd in self.live

    def root_hwnd(self, hwnd):
        self.calls.append(("root_hwnd", hwnd))
        return self.roots[hwnd]

    def process_id(self, hwnd):
        self.calls.append(("process_id", hwnd))
        return self.pids[hwnd]

    def window_region(self, hwnd):
        self.calls.append(("window_region", hwnd))
        self.target_sample_count += 1
        if self.target_sample_count == 2 and self.second_target_region is not None:
            return self.second_target_region
        return self.regions[hwnd]

    def foreground_hwnd(self):
        self.calls.append(("foreground_hwnd",))
        return self.foreground

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


def _request(**changes) -> CommitAdmissionRequest:
    request = CommitAdmissionRequest(
        request_id="decision-1",
        frame=FrameCommitIdentity(
            5, 3, 101, (10, 20, 800, 600), "target_window",
            (800, 600), "a" * 64,
        ),
        target=TargetCommitIdentity(
            "binding-1", 101, 100, 7, 3, (10, 20, 800, 600),
        ),
        route="physical",
        screen_point=(150, 140),
    )
    return replace(request, **changes)


def test_reader_uses_os_identity_but_only_carries_runtime_binding_and_epoch():
    backend = Backend()
    request = _request(target=TargetCommitIdentity(
        "binding-runtime", 101, 999, 999, 8, (1, 2, 3, 4),
    ), frame=FrameCommitIdentity(
        5, 8, 101, (10, 20, 800, 600), "target_window",
        (800, 600), "a" * 64,
    ))

    facts = WindowsDesktopFactsReader(backend).read(request)

    assert facts.target == TargetCommitIdentity(
        "binding-runtime", 101, 100, 7, 8, (10, 20, 800, 600),
    )
    assert (facts.foreground.hwnd, facts.foreground.root_hwnd,
            facts.foreground.process_id) == (100, 100, 7)
    assert (facts.hit.hwnd, facts.hit.root_hwnd, facts.hit.process_id) == (
        111, 100, 7,
    )
    region_reads = [
        index for index, call in enumerate(backend.calls)
        if call == ("window_region", 101)
    ]
    assert len(region_reads) == 2
    assert region_reads[0] < backend.calls.index(("foreground_hwnd",)) < region_reads[1]


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


def test_final_click_guard_reads_no_foreground_and_admits_exact_target_hit():
    backend = Backend()
    guard = WindowsPhysicalClickFinalGuard(WindowsDesktopFactsReader(backend))

    decision = guard.check_before_mouse_down(_request(), (150, 140))

    assert decision is FinalClickGuardDecision.ADMITTED
    assert guard.check_before_mouse_down(
        _request(), (151, 140)
    ) is FinalClickGuardDecision.HIT_TEST_CHANGED
    assert ("foreground_hwnd",) not in backend.calls
    assert backend.calls.count(("window_region", 101)) == 2
    assert backend.calls.count(("window_from_point", (150, 140))) == 1


@pytest.mark.parametrize(
    ("change", "decision"),
    (
        ("root", FinalClickGuardDecision.TARGET_ROOT_CHANGED),
        ("pid", FinalClickGuardDecision.TARGET_PROCESS_CHANGED),
        ("geometry", FinalClickGuardDecision.TARGET_GEOMETRY_CHANGED),
        ("hit", FinalClickGuardDecision.HIT_TEST_CHANGED),
        ("hit_unavailable", FinalClickGuardDecision.HIT_TEST_UNAVAILABLE),
        ("point", FinalClickGuardDecision.POINT_OUTSIDE_TARGET),
    ),
)
def test_final_click_guard_fails_closed_on_each_mechanical_identity(
    change, decision,
):
    backend = Backend()
    request = _request()
    if change == "root":
        backend.roots[101] = 999
    elif change == "pid":
        backend.pids[101] = 999
    elif change == "geometry":
        backend.regions[101] = (11, 20, 800, 600)
    elif change == "hit":
        backend.roots[111] = 999
    elif change == "hit_unavailable":
        backend.hit = 0
    else:
        request = replace(request, screen_point=(900, 900))

    assert WindowsPhysicalClickFinalGuard(
        WindowsDesktopFactsReader(backend)
    ).check_before_mouse_down(request, request.screen_point) is decision


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
