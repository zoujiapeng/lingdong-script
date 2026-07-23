from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
import time

import pytest

from winpilot.action.desktop_queue import DesktopActionCanceled, DesktopActionQueue
from winpilot.action.raw_device import (
    AdmittedPhysicalTargetLaunchBackend,
    FinalKeyboardGuardDecision,
    FinalPointerGuardDecision,
    FinalTargetLaunchGuardDecision,
    RawDeviceBackends,
    RawDeviceExecutor,
    RawTargetLaunchReceipt,
)
from winpilot.action.replay_policy import MutationOutcome
from winpilot.agent.frame_authority import FrameAuthority
from winpilot.process_identity import process_identity_for
from winpilot.vnext.actor import DesktopActionProposal
from winpilot.vnext.capabilities import PointerLeaseState
from winpilot.vnext.runtime import MutationPermitAuthority
from winpilot.vnext.runtime.target_activation import ActivatedTargetIdentity
from winpilot.vnext.runtime.target_launch import (
    TargetLaunchEnvelope,
    TargetLaunchOutcome,
)
from winpilot.vnext.targeting import (
    ProcessTargetFact,
    TargetCandidate,
    candidate_fingerprint_for,
    candidate_ref_for,
    launch_identity_for,
)
from winpilot.vnext.targeting.executable_presence import (
    ExecutablePresenceSnapshot,
)
from winpilot.visual_runtime.atomic_click_controller import (
    AtomicDesktopController,
    ControllerPostAction,
    TargetActivationObservation,
    TargetActivationPostAction,
    TargetLaunchObservationResult,
    TargetLaunchObservationState,
)
from winpilot.visual_runtime.commit_admission import (
    CommitEnvironment,
    FrameCommitIdentity,
)


EXE = r"C:\Apps\Workspace.exe"
IMAGE = "YWJj"


def _process_identity(pid: int = 51, *, created: int = 200) -> str:
    return process_identity_for(
        process_id=pid,
        executable_path=EXE,
        creation_time_ns=created,
    )


def _candidate() -> TargetCandidate:
    return TargetCandidate(
        candidate_ref=candidate_ref_for(
            activation="open_app",
            identity=launch_identity_for(kind="exe", target=EXE),
        ),
        activation="open_app",
        state="installed",
        label="Installed workspace",
        provenance=("installed_catalog",),
        launch_kind="exe",
        launch_target=EXE,
    )


def _authority() -> tuple[FrameAuthority, object]:
    authority = FrameAuthority()
    frame = authority.commit(
        epoch=0,
        hwnd=1,
        region=(0, 0, 1920, 1080),
        scope="full_desktop",
        reason="target_discovery",
        submitted_size=(1280, 720),
        image_b64=IMAGE,
        captured_at=1.0,
    )
    return authority, frame


def _envelope(
    frame: object,
    *,
    decision_id: str = "decision-launch-1",
) -> TargetLaunchEnvelope:
    candidate = _candidate()
    return TargetLaunchEnvelope(
        decision_id=decision_id,
        mission_id="mission-1",
        run_id="run-1",
        mission_revision=5,
        obligation_id="obligation-1",
        frame=FrameCommitIdentity(
            frame_id=frame.frame_id,
            epoch=frame.epoch,
            hwnd=frame.hwnd,
            region=frame.region,
            scope=frame.scope,
            submitted_size=frame.submitted_size,
            image_sha256=frame.image_sha256,
        ),
        pointer=PointerLeaseState(),
        broker_projection_id="projection-launch-1",
        capability_grant_ids=("grant-1",),
        allowed_evidence_scope=("evidence-target-1",),
        candidate_fingerprint=candidate_fingerprint_for((candidate,)),
        proposal=DesktopActionProposal.model_validate({
            "format": "actor-proposal/1",
            "kind": "desktop_action",
            "action": "activate_target",
            "args": {"candidate_ordinal": 1},
            "local_intent": "activate the selected installed target",
        }),
        candidate=candidate,
    )


def _activated(**overrides: object) -> ActivatedTargetIdentity:
    values: dict[str, object] = {
        "hwnd": 951,
        "root_hwnd": 951,
        "process_id": 51,
        "process_identity": _process_identity(),
        "window_region": (20, 30, 900, 650),
        "title_snapshot": "Workspace",
    }
    values.update(overrides)
    return ActivatedTargetIdentity(**values)


def _presence(
    *,
    exact: bool = False,
    unresolved: tuple[int, ...] = (),
) -> ExecutablePresenceSnapshot:
    instances = ()
    if exact:
        instances = (
            ProcessTargetFact(
                process_id=61,
                creation_time_ns=300,
                executable_path=EXE,
                process_identity=_process_identity(61, created=300),
            ),
        )
    return ExecutablePresenceSnapshot(
        EXE,
        instances,
        unresolved,
        captured_at=1.0,
    )


def _applied_receipt() -> RawTargetLaunchReceipt:
    return RawTargetLaunchReceipt(
        MutationOutcome.APPLIED,
        1,
        process_id=51,
        process_identity=_process_identity(),
    )


def _ready(
    target: ActivatedTargetIdentity | None = None,
    *,
    foreground: bool = True,
) -> TargetLaunchObservationResult:
    return TargetLaunchObservationResult(
        TargetLaunchObservationState.READY,
        TargetActivationObservation(target or _activated(), foreground),
    )


class _UnusedCommitObservation:
    def observe_commit(self, request):
        return CommitEnvironment(
            target=request.target,
            pointer_released=True,
            mutation_allowed=True,
            stop_requested=False,
            canceled=False,
            foreground_hwnd=request.target.hwnd,
            foreground_root_hwnd=request.target.root_hwnd,
            foreground_process_id=request.target.process_id,
            foreground_process_identity=request.target.process_identity,
            hit_test_point=request.screen_point,
            hit_hwnd=request.target.hwnd,
            hit_root_hwnd=request.target.root_hwnd,
            hit_process_id=request.target.process_id,
            hit_process_identity=request.target.process_identity,
        )


class _UnusedFinalGuard:
    def check_before_pointer_input(self, _request, _screen_point):
        return FinalPointerGuardDecision.ADMITTED

    def check_before_keyboard_input(self, _request):
        return FinalKeyboardGuardDecision.ADMITTED


class _UnusedPostAction:
    def observe_after(self, envelope, *, admission, raw_receipt):
        return ControllerPostAction(
            envelope.frame.frame_id + 1,
            ("unused-actor-evidence",),
        )


class Presence:
    def __init__(self, values: tuple[object, ...]) -> None:
        self.values = values
        self.calls: list[str] = []
        self._lock = Lock()

    def read(self, executable_path: str):
        with self._lock:
            index = len(self.calls)
            self.calls.append(executable_path)
            value = self.values[min(index, len(self.values) - 1)]
        if isinstance(value, Exception):
            raise value
        return value


class LaunchObservation:
    def __init__(self, values: tuple[object, ...]) -> None:
        self.values = values
        self.calls: list[int | None] = []
        self._lock = Lock()

    def observe_launch(
        self,
        _envelope,
        *,
        raw_receipt,
        selected_hwnd,
    ):
        with self._lock:
            index = len(self.calls)
            self.calls.append(selected_hwnd)
            value = self.values[min(index, len(self.values) - 1)]
        if isinstance(value, Exception):
            raise value
        return value


class LaunchPost:
    def __init__(
        self,
        *,
        target: ActivatedTargetIdentity | None = None,
        fail: bool = False,
    ) -> None:
        self.target = target or _activated()
        self.fail = fail
        self.calls: list[tuple[object, object]] = []
        self._lock = Lock()

    def observe_after_launch(
        self,
        envelope,
        *,
        raw_receipt,
        selected_target,
    ):
        with self._lock:
            self.calls.append((raw_receipt, selected_target))
        if self.fail:
            raise RuntimeError("injected post failure")
        return TargetActivationPostAction(
            self.target,
            True,
            envelope.frame.frame_id + 1,
            ("evidence-launch-post",),
        )


def _controller(
    envelope: TargetLaunchEnvelope,
    authority: FrameAuthority,
    *,
    presence: Presence,
    observation: LaunchObservation,
    post: LaunchPost,
    raw_result: object | None = None,
    backend_hook=None,
    issue_permit: bool = True,
    queue: DesktopActionQueue | None = None,
    poll_attempts: int = 3,
) -> tuple[
    AtomicDesktopController,
    MutationPermitAuthority,
    list[object],
    list[FinalTargetLaunchGuardDecision],
]:
    permits = MutationPermitAuthority(envelope.mission_id, envelope.run_id)
    if issue_permit:
        permits.issue(envelope, turn_index=0, still_current=lambda: True)
    calls: list[object] = []
    guards: list[FinalTargetLaunchGuardDecision] = []
    result = _applied_receipt() if raw_result is None else raw_result

    def backend(context, *, final_guard):
        calls.append(context)
        decision = final_guard()
        guards.append(decision)
        if backend_hook is not None:
            backend_hook(decision)
        if decision is not FinalTargetLaunchGuardDecision.ADMITTED:
            return RawTargetLaunchReceipt(
                MutationOutcome.NOT_STARTED,
                0,
                error_kind=f"final_guard_{decision.value}",
            )
        if isinstance(result, Exception):
            raise result
        return result

    controller = AtomicDesktopController(
        action_queue=queue or DesktopActionQueue(),
        frame_authority=authority,
        permits=permits,
        observation=_UnusedCommitObservation(),
        final_guard=_UnusedFinalGuard(),
        raw_device=RawDeviceExecutor(RawDeviceBackends(
            admitted_physical_target_launch=(
                AdmittedPhysicalTargetLaunchBackend(backend)
            ),
        )),
        post_action=_UnusedPostAction(),
        launch_presence=presence,
        launch_observation=observation,
        launch_post_action=post,
        launch_poll_attempts=poll_attempts,
        launch_poll_interval_s=0.0,
        launch_poll_wait=lambda _delay: None,
        enabled=True,
    )
    return controller, permits, calls, guards


def test_launch_success_uses_one_raw_bounded_poll_and_fixed_post_target() -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    presence = Presence((_presence(),))
    observation = LaunchObservation((
        TargetLaunchObservationResult(
            TargetLaunchObservationState.NOT_READY
        ),
        _ready(),
    ))
    post = LaunchPost()
    controller, permits, calls, guards = _controller(
        envelope,
        authority,
        presence=presence,
        observation=observation,
        post=post,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is TargetLaunchOutcome.COMPLETED
    assert transition.launch_request_count == 1
    assert transition.launch_process_id == 51
    assert transition.launch_process_identity == _process_identity()
    assert transition.activated_target == _activated()
    assert len(calls) == 1
    assert calls[0].executable_path == EXE
    assert guards == [FinalTargetLaunchGuardDecision.ADMITTED]
    assert presence.calls == [EXE, EXE]
    assert observation.calls == [None, None]
    assert post.calls == [(_applied_receipt(), _activated())]
    assert permits.active_count == 0


@pytest.mark.parametrize(
    ("value", "reason"),
    (
        (_presence(exact=True), "existing_process"),
        (_presence(unresolved=(61,)), "process_state_unknown"),
        (RuntimeError("source failed"), "process_state_unknown"),
        (object(), "process_state_unknown"),
    ),
)
def test_preflight_presence_failure_proves_zero_launch_requests(
    value: object,
    reason: str,
) -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    presence = Presence((value,))
    observation = LaunchObservation((_ready(),))
    post = LaunchPost()
    controller, _permits, calls, _guards = _controller(
        envelope,
        authority,
        presence=presence,
        observation=observation,
        post=post,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is TargetLaunchOutcome.REJECTED
    assert transition.launch_request_count == 0
    assert transition.failure_reason == reason
    assert calls == []
    assert observation.calls == []
    assert post.calls == []


@pytest.mark.parametrize(
    ("second", "guard"),
    (
        (_presence(exact=True), FinalTargetLaunchGuardDecision.EXISTING_PROCESS),
        (
            _presence(unresolved=(61,)),
            FinalTargetLaunchGuardDecision.PROCESS_STATE_UNKNOWN,
        ),
        (
            RuntimeError("source failed"),
            FinalTargetLaunchGuardDecision.PROCESS_STATE_UNKNOWN,
        ),
    ),
)
def test_final_guard_rereads_presence_and_rejects_without_launch(
    second: object,
    guard: FinalTargetLaunchGuardDecision,
) -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    presence = Presence((_presence(), second))
    observation = LaunchObservation((_ready(),))
    post = LaunchPost()
    controller, _permits, calls, guards = _controller(
        envelope,
        authority,
        presence=presence,
        observation=observation,
        post=post,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is TargetLaunchOutcome.REJECTED
    assert transition.launch_request_count == 0
    assert len(calls) == 1
    assert guards == [guard]
    assert presence.calls == [EXE, EXE]
    assert observation.calls == []
    assert post.calls == []


def test_stale_frame_never_consumes_permit_or_reads_presence() -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    authority.commit(
        epoch=0,
        hwnd=1,
        region=(0, 0, 1920, 1080),
        scope="full_desktop",
        reason="newer_observation",
        submitted_size=(1280, 720),
        image_b64=IMAGE + "A",
        captured_at=2.0,
        expected_previous_frame_id=frame.frame_id,
    )
    presence = Presence((_presence(),))
    observation = LaunchObservation((_ready(),))
    post = LaunchPost()
    controller, permits, calls, _guards = _controller(
        envelope,
        authority,
        presence=presence,
        observation=observation,
        post=post,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is TargetLaunchOutcome.REJECTED
    assert transition.launch_request_count == 0
    assert transition.failure_reason == "frame_changed"
    assert permits.pending_count == 1
    assert presence.calls == []
    assert calls == []


def test_missing_permit_rejects_before_presence_or_raw() -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    presence = Presence((_presence(),))
    controller, _permits, calls, _guards = _controller(
        envelope,
        authority,
        presence=presence,
        observation=LaunchObservation((_ready(),)),
        post=LaunchPost(),
        issue_permit=False,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is TargetLaunchOutcome.REJECTED
    assert transition.launch_request_count == 0
    assert transition.failure_reason == "mutation_permit_missing"
    assert presence.calls == []
    assert calls == []


@pytest.mark.parametrize(
    "raw_result",
    (
        RawTargetLaunchReceipt(
            MutationOutcome.OUTCOME_UNKNOWN,
            None,
            error_kind="backend_uncertain",
        ),
        RawTargetLaunchReceipt(
            MutationOutcome.OUTCOME_UNKNOWN,
            1,
            error_kind="create_process_outcome_unknown",
        ),
        RuntimeError("backend failed"),
        object(),
    ),
)
def test_uncertain_raw_result_is_never_polled_or_replayed(
    raw_result: object,
) -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    observation = LaunchObservation((_ready(),))
    post = LaunchPost()
    controller, _permits, calls, _guards = _controller(
        envelope,
        authority,
        presence=Presence((_presence(),)),
        observation=observation,
        post=post,
        raw_result=raw_result,
    )

    first = controller.execute(envelope)
    second = controller.execute(envelope)

    assert first.outcome is TargetLaunchOutcome.UNKNOWN
    assert first.launch_request_count in {None, 1}
    assert first.launch_process_id is None
    assert first.activated_target is None
    assert second.outcome is TargetLaunchOutcome.REJECTED
    assert len(calls) == 1
    assert observation.calls == []
    assert post.calls == []


@pytest.mark.parametrize(
    "poll_value",
    (
        TargetLaunchObservationResult(TargetLaunchObservationState.EXITED),
        TargetLaunchObservationResult(
            TargetLaunchObservationState.PROCESS_REUSED
        ),
        TargetLaunchObservationResult(TargetLaunchObservationState.AMBIGUOUS),
        TargetLaunchObservationResult(
            TargetLaunchObservationState.SOURCE_UNAVAILABLE
        ),
        RuntimeError("poll source failed"),
        _ready(_activated(process_identity=_process_identity(created=201))),
        _ready(foreground=False),
    ),
)
def test_terminal_poll_failures_are_unknown_without_positive_identity(
    poll_value: object,
) -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    observation = LaunchObservation((poll_value,))
    post = LaunchPost()
    controller, _permits, calls, _guards = _controller(
        envelope,
        authority,
        presence=Presence((_presence(),)),
        observation=observation,
        post=post,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is TargetLaunchOutcome.UNKNOWN
    assert transition.launch_request_count == 1
    assert transition.launch_process_id is None
    assert transition.launch_process_identity is None
    assert transition.activated_target is None
    assert len(calls) == 1
    assert post.calls == []


def test_poll_timeout_is_bounded_and_does_not_relaunch() -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    observation = LaunchObservation((
        TargetLaunchObservationResult(
            TargetLaunchObservationState.NOT_READY
        ),
    ))
    controller, _permits, calls, _guards = _controller(
        envelope,
        authority,
        presence=Presence((_presence(),)),
        observation=observation,
        post=LaunchPost(),
        poll_attempts=4,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is TargetLaunchOutcome.UNKNOWN
    assert transition.launch_request_count == 1
    assert transition.failure_reason == "launch_poll_timeout"
    assert observation.calls == [None] * 4
    assert len(calls) == 1


def test_stop_after_applied_raw_is_unknown_and_never_relaunches() -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    stop = Event()
    observation = LaunchObservation((_ready(),))
    post = LaunchPost()
    controller, _permits, calls, _guards = _controller(
        envelope,
        authority,
        presence=Presence((_presence(),)),
        observation=observation,
        post=post,
        backend_hook=lambda decision: stop.set(),
    )

    transition = controller.execute(envelope, stop_event=stop)

    assert transition.outcome is TargetLaunchOutcome.UNKNOWN
    assert transition.launch_request_count == 1
    assert transition.failure_reason == "launch_poll_stop_requested"
    assert len(calls) == 1
    assert observation.calls == []
    assert post.calls == []


def test_preexisting_stop_rejects_with_proven_zero_launch_requests() -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    stop = Event()
    stop.set()
    presence = Presence((_presence(),))
    controller, permits, calls, _guards = _controller(
        envelope,
        authority,
        presence=presence,
        observation=LaunchObservation((_ready(),)),
        post=LaunchPost(),
    )

    transition = controller.execute(envelope, stop_event=stop)

    assert transition.outcome is TargetLaunchOutcome.REJECTED
    assert transition.launch_request_count == 0
    assert transition.failure_reason == "desktop_action_canceled"
    assert permits.pending_count == 1
    assert presence.calls == []
    assert calls == []


def test_cancel_while_waiting_for_queue_rejects_without_callback() -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    queue = DesktopActionQueue()
    stop = Event()
    acquired = Event()
    release = Event()
    presence = Presence((_presence(),))
    controller, permits, calls, _guards = _controller(
        envelope,
        authority,
        presence=presence,
        observation=LaunchObservation((_ready(),)),
        post=LaunchPost(),
        queue=queue,
    )

    def blocker() -> None:
        acquired.set()
        release.wait(2.0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        blocked = pool.submit(
            queue.run,
            "blocker",
            blocker,
            target_hwnd=77,
            scope="physical",
        )
        assert acquired.wait(1.0)
        launch = pool.submit(controller.execute, envelope, stop_event=stop)
        deadline = time.monotonic() + 1.0
        while queue.status()["resource_waiters"] != 1:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        stop.set()
        transition = launch.result(timeout=2.0)
        release.set()
        blocked.result(timeout=2.0)

    assert transition.outcome is TargetLaunchOutcome.REJECTED
    assert transition.launch_request_count == 0
    assert transition.failure_reason == "desktop_action_canceled"
    assert permits.pending_count == 1
    assert presence.calls == []
    assert calls == []


def test_cancellation_named_exception_after_callback_is_not_count_zero() -> None:
    class PostCallbackCanceledQueue(DesktopActionQueue):
        def run(self, action, fn, **kwargs):
            super().run(action, fn, **kwargs)
            raise DesktopActionCanceled("injected after callback")

    authority, frame = _authority()
    envelope = _envelope(frame)
    presence = Presence((_presence(),))
    post = LaunchPost()
    controller, permits, calls, _guards = _controller(
        envelope,
        authority,
        presence=presence,
        observation=LaunchObservation((_ready(),)),
        post=post,
        queue=PostCallbackCanceledQueue(),
    )

    with pytest.raises(DesktopActionCanceled, match="after callback"):
        controller.execute(envelope)

    assert len(calls) == 1
    assert len(post.calls) == 1
    assert permits.pending_count == 0
    assert permits.active_count == 0


@pytest.mark.parametrize(
    "post",
    (
        LaunchPost(fail=True),
        LaunchPost(target=_activated(hwnd=952, root_hwnd=952)),
    ),
)
def test_post_failure_after_launch_is_unknown(
    post: LaunchPost,
) -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    controller, _permits, calls, _guards = _controller(
        envelope,
        authority,
        presence=Presence((_presence(),)),
        observation=LaunchObservation((_ready(),)),
        post=post,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is TargetLaunchOutcome.UNKNOWN
    assert transition.launch_request_count == 1
    assert transition.launch_process_id is None
    assert transition.activated_target is None
    assert len(calls) == 1


def test_concurrent_duplicate_launch_has_one_raw_owner() -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    presence = Presence((_presence(),))
    observation = LaunchObservation((_ready(),))
    post = LaunchPost()
    controller, permits, calls, _guards = _controller(
        envelope,
        authority,
        presence=presence,
        observation=observation,
        post=post,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            pool.map(lambda _index: controller.execute(envelope), range(2))
        )

    assert sorted(item.outcome.value for item in results) == [
        "completed",
        "rejected",
    ]
    assert len(calls) == 1
    assert len(post.calls) == 1
    assert permits.active_count == 0


def test_poll_prefix_property_is_deterministic_and_bounded() -> None:
    for not_ready_count in range(5):
        authority, frame = _authority()
        envelope = _envelope(
            frame,
            decision_id=f"decision-launch-{not_ready_count}",
        )
        values = (
            TargetLaunchObservationResult(
                TargetLaunchObservationState.NOT_READY
            ),
        ) * not_ready_count + (_ready(),)
        observation = LaunchObservation(values)
        controller, _permits, calls, _guards = _controller(
            envelope,
            authority,
            presence=Presence((_presence(),)),
            observation=observation,
            post=LaunchPost(),
            poll_attempts=5,
        )

        transition = controller.execute(envelope)

        expected_ready = not_ready_count < 5
        assert (
            transition.outcome is TargetLaunchOutcome.COMPLETED
        ) is expected_ready
        assert len(observation.calls) == min(not_ready_count + 1, 5)
        assert len(calls) == 1
