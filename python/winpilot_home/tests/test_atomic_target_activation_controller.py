from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest

from winpilot.action.desktop_queue import DesktopActionQueue
from winpilot.action.raw_device import (
    AdmittedPhysicalTargetActivationBackend,
    FinalKeyboardGuardDecision,
    FinalPointerGuardDecision,
    FinalTargetActivationGuardDecision,
    RawBackendResult,
    RawDeviceBackends,
    RawDeviceExecutor,
)
from winpilot.agent.frame_authority import FrameAuthority
from winpilot.process_identity import process_identity_for
from winpilot.vnext.actor import DesktopActionProposal
from winpilot.vnext.capabilities import PointerLeaseState
from winpilot.vnext.runtime import MutationPermitAuthority
from winpilot.vnext.runtime.target_activation import (
    ActivatedTargetIdentity,
    TargetActivationEnvelope,
    TargetActivationOutcome,
)
from winpilot.vnext.targeting import (
    TargetCandidate,
    candidate_fingerprint_for,
    candidate_ref_for,
)
from winpilot.visual_runtime.atomic_click_controller import (
    AtomicDesktopController,
    ControllerPostAction,
    TargetActivationObservation,
    TargetActivationPostAction,
)
from winpilot.visual_runtime.commit_admission import (
    CommitEnvironment,
    FrameCommitIdentity,
)


IMAGE = "YWJj"


def _process_identity(pid: int = 41, *, created: int = 100) -> str:
    return process_identity_for(
        process_id=pid,
        executable_path=fr"C:\Apps\host-{pid}.exe",
        creation_time_ns=created,
    )


def _candidate(*, state: str = "visible") -> TargetCandidate:
    identity = _process_identity()
    return TargetCandidate(
        candidate_ref=candidate_ref_for(
            activation="switch_window",
            identity=f"901|41|{identity}",
        ),
        activation="switch_window",
        state=state,
        label="Visible workspace",
        provenance=("visible_windows",),
        window_title="Workspace",
        hwnd=901,
        process_id=41,
        process_identity=identity,
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
    candidate: TargetCandidate | None = None,
    decision_id: str = "decision-activation-1",
) -> TargetActivationEnvelope:
    selected = candidate or _candidate()
    return TargetActivationEnvelope(
        decision_id=decision_id,
        mission_id="mission-1",
        run_id="run-1",
        mission_revision=4,
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
        broker_projection_id="projection-1",
        capability_grant_ids=("grant-1",),
        allowed_evidence_scope=("evidence-target-1",),
        candidate_fingerprint=candidate_fingerprint_for((selected,)),
        proposal=DesktopActionProposal.model_validate({
            "format": "actor-proposal/1",
            "kind": "desktop_action",
            "action": "activate_target",
            "args": {"candidate_ordinal": 1},
            "local_intent": "bring the selected target to the foreground",
        }),
        candidate=selected,
    )


def _activated(**overrides: object) -> ActivatedTargetIdentity:
    values: dict[str, object] = {
        "hwnd": 901,
        # A discovered top-level handle is not itself a proof of root identity.
        "root_hwnd": 900,
        "process_id": 41,
        "process_identity": _process_identity(),
        "window_region": (10, 20, 800, 600),
        "title_snapshot": "Workspace",
    }
    values.update(overrides)
    return ActivatedTargetIdentity(**values)


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
    def check_before_pointer_input(self, request, screen_point):
        return FinalPointerGuardDecision.ADMITTED

    def check_before_keyboard_input(self, request):
        return FinalKeyboardGuardDecision.ADMITTED


class _UnusedPostAction:
    def observe_after(self, envelope, *, admission, raw_receipt):
        return ControllerPostAction(
            envelope.frame.frame_id + 1,
            ("unused-actor-evidence",),
        )


class ActivationObservation:
    def __init__(
        self,
        *,
        target: ActivatedTargetIdentity | None = None,
        foreground: bool = False,
        sequence: tuple[TargetActivationObservation, ...] = (),
    ) -> None:
        self.target = target or _activated()
        self.foreground = foreground
        self.sequence = sequence
        self.calls: list[TargetActivationEnvelope] = []
        self._lock = Lock()

    def observe_activation(self, envelope):
        with self._lock:
            index = len(self.calls)
            self.calls.append(envelope)
            if self.sequence:
                return self.sequence[min(index, len(self.sequence) - 1)]
            return TargetActivationObservation(self.target, self.foreground)


class ActivationPost:
    def __init__(
        self,
        *,
        target: ActivatedTargetIdentity | None = None,
        foreground: bool = True,
        fail: bool = False,
    ) -> None:
        self.target = target or _activated()
        self.foreground = foreground
        self.fail = fail
        self.calls: list[tuple[TargetActivationEnvelope, object]] = []
        self._lock = Lock()

    def observe_after_activation(self, envelope, *, raw_receipt):
        with self._lock:
            self.calls.append((envelope, raw_receipt))
        if self.fail:
            raise RuntimeError("injected post failure")
        return TargetActivationPostAction(
            activated_target=self.target,
            exact_foreground=self.foreground,
            post_frame_id=envelope.frame.frame_id + 1,
            evidence_refs=("evidence-activation-post",),
        )


def _controller(
    envelope: TargetActivationEnvelope,
    authority: FrameAuthority,
    *,
    observation: ActivationObservation,
    post: ActivationPost,
    completed: bool | None = True,
    backend_hook=None,
    still_current=lambda: True,
    queue: DesktopActionQueue | None = None,
) -> tuple[AtomicDesktopController, MutationPermitAuthority, list[object]]:
    permits = MutationPermitAuthority(envelope.mission_id, envelope.run_id)
    permits.issue(
        envelope,
        turn_index=0,
        still_current=still_current,
    )
    calls: list[object] = []

    def backend(context, *, final_guard):
        calls.append(context)
        decision = final_guard()
        if backend_hook is not None:
            backend_hook(context, decision)
        if decision is not FinalTargetActivationGuardDecision.ADMITTED:
            return RawBackendResult(
                False,
                {"guard_decision": decision.value},
            )
        return RawBackendResult(
            completed,
            {"via": "set_foreground_window"},
        )

    controller = AtomicDesktopController(
        action_queue=queue or DesktopActionQueue(),
        frame_authority=authority,
        permits=permits,
        observation=_UnusedCommitObservation(),
        final_guard=_UnusedFinalGuard(),
        raw_device=RawDeviceExecutor(RawDeviceBackends(
            admitted_physical_target_activation=(
                AdmittedPhysicalTargetActivationBackend(backend)
            ),
        )),
        post_action=_UnusedPostAction(),
        activation_observation=observation,
        activation_post_action=post,
        enabled=True,
    )
    return controller, permits, calls


def test_visible_activation_uses_exact_root_and_raw_once_then_posts() -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    observation = ActivationObservation()
    post = ActivationPost()
    controller, permits, calls = _controller(
        envelope,
        authority,
        observation=observation,
        post=post,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is TargetActivationOutcome.COMPLETED
    assert transition.mutation_completed is True
    assert transition.action_id == envelope.decision_id
    assert transition.candidate_ref == envelope.candidate.candidate_ref
    assert transition.activated_target == _activated()
    assert len(calls) == 1
    assert calls[0].target_hwnd == 901
    assert calls[0].target_root_hwnd == 900
    assert len(observation.calls) == 2
    assert len(post.calls) == 1
    assert post.calls[0][1].input_completed is True
    assert permits.active_count == 0


def test_already_foreground_skips_raw_but_requires_fresh_post_proof() -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    observation = ActivationObservation(foreground=True)
    post = ActivationPost()
    controller, _permits, calls = _controller(
        envelope,
        authority,
        observation=observation,
        post=post,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is TargetActivationOutcome.COMPLETED
    assert transition.mutation_completed is False
    assert calls == []
    assert len(observation.calls) == 2
    assert len(post.calls) == 1
    assert post.calls[0][1] is None


@pytest.mark.parametrize(
    ("post", "expected"),
    (
        (ActivationPost(fail=True), TargetActivationOutcome.REJECTED),
        (
            ActivationPost(foreground=False),
            TargetActivationOutcome.REJECTED,
        ),
    ),
)
def test_zero_mutation_post_failure_stays_replay_safe(
    post: ActivationPost,
    expected: TargetActivationOutcome,
) -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    controller, _permits, calls = _controller(
        envelope,
        authority,
        observation=ActivationObservation(foreground=True),
        post=post,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is expected
    assert transition.mutation_completed is False
    assert calls == []


@pytest.mark.parametrize("completed", (None,))
def test_unknown_raw_outcome_is_not_posted_or_replayed(completed) -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    post = ActivationPost()
    controller, _permits, calls = _controller(
        envelope,
        authority,
        observation=ActivationObservation(),
        post=post,
        completed=completed,
    )

    first = controller.execute(envelope)
    second = controller.execute(envelope)

    assert first.outcome is TargetActivationOutcome.UNKNOWN
    assert first.mutation_completed is None
    assert second.outcome is TargetActivationOutcome.REJECTED
    assert len(calls) == 1
    assert post.calls == []


def test_applied_raw_with_missing_post_is_unknown() -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    controller, _permits, calls = _controller(
        envelope,
        authority,
        observation=ActivationObservation(),
        post=ActivationPost(fail=True),
    )

    transition = controller.execute(envelope)

    assert transition.outcome is TargetActivationOutcome.UNKNOWN
    assert transition.mutation_completed is None
    assert len(calls) == 1


def test_stale_full_desktop_frame_never_consumes_or_calls_raw() -> None:
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
    observation = ActivationObservation()
    post = ActivationPost()
    controller, permits, calls = _controller(
        envelope,
        authority,
        observation=observation,
        post=post,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is TargetActivationOutcome.REJECTED
    assert transition.failure_reason == "frame_changed"
    assert calls == []
    assert observation.calls == []
    assert post.calls == []
    assert permits.pending_count == 1


def test_recoverable_candidate_is_rejected_before_os_observation() -> None:
    authority, frame = _authority()
    envelope = _envelope(frame, candidate=_candidate(state="recoverable"))
    observation = ActivationObservation()
    post = ActivationPost()
    controller, _permits, calls = _controller(
        envelope,
        authority,
        observation=observation,
        post=post,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is TargetActivationOutcome.REJECTED
    assert transition.failure_reason == "candidate_requires_recovery"
    assert calls == []
    assert observation.calls == []
    assert post.calls == []


@pytest.mark.parametrize(
    "changed",
    (
        _activated(root_hwnd=902),
        _activated(window_region=(10, 20, 801, 600)),
        _activated(process_identity=_process_identity(created=101)),
    ),
)
def test_final_guard_rejects_strong_identity_drift_before_raw_effect(
    changed: ActivatedTargetIdentity,
) -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    observation = ActivationObservation(sequence=(
        TargetActivationObservation(_activated(), False),
        TargetActivationObservation(changed, False),
    ))
    post = ActivationPost()
    controller, _permits, calls = _controller(
        envelope,
        authority,
        observation=observation,
        post=post,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is TargetActivationOutcome.REJECTED
    assert transition.mutation_completed is False
    assert len(calls) == 1
    assert post.calls == []


def test_stop_requested_by_final_guard_prevents_raw_effect() -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    stop = Event()

    class StoppingObservation(ActivationObservation):
        def observe_activation(self, actual_envelope):
            result = super().observe_activation(actual_envelope)
            if len(self.calls) == 2:
                stop.set()
            return result

    observation = StoppingObservation()
    post = ActivationPost()
    controller, _permits, calls = _controller(
        envelope,
        authority,
        observation=observation,
        post=post,
    )

    transition = controller.execute(envelope, stop_event=stop)

    assert transition.outcome is TargetActivationOutcome.REJECTED
    assert len(calls) == 1
    assert post.calls == []


def test_concurrent_duplicate_activation_has_one_raw_owner() -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    observation = ActivationObservation()
    post = ActivationPost()
    controller, permits, calls = _controller(
        envelope,
        authority,
        observation=observation,
        post=post,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _index: controller.execute(envelope), range(2)))

    assert sorted(item.outcome.value for item in results) == [
        "completed",
        "rejected",
    ]
    assert len(calls) == 1
    assert len(post.calls) == 1
    assert permits.active_count == 0
