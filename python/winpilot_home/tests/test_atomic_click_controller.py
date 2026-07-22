from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from winpilot.action.desktop_queue import DesktopActionQueue
from winpilot.action.raw_device import (
    AdmittedPhysicalClickBackend,
    ClickCount,
    ClickMechanism,
    FinalClickGuardDecision,
    MouseButton,
    RawBackendResult,
    RawDeviceBackends,
    RawDeviceExecutor,
)
from winpilot.agent.frame_authority import FrameAuthority
from winpilot.vnext.actor import ActorDecisionEnvelope, parse_actor_proposal
from winpilot.vnext.capabilities import PointerDisposition, PointerLeaseState
from winpilot.vnext.runtime import (
    ControllerOutcome,
    MutationPermitAuthority,
)
from winpilot.visual_runtime.atomic_click_controller import (
    AtomicClickController,
    AtomicClickControllerError,
    ControllerPostAction,
)
from winpilot.visual_runtime.commit_admission import (
    CommitAdmissionReason,
    CommitEnvironment,
    TargetCommitIdentity,
)


IMAGE = "YWJj"


def _authority() -> tuple[FrameAuthority, object]:
    authority = FrameAuthority()
    frame = authority.commit(
        epoch=3,
        hwnd=101,
        region=(10, 20, 101, 51),
        scope="target_window",
        reason="test",
        submitted_size=(11, 6),
        image_b64=IMAGE,
        captured_at=1.0,
    )
    return authority, frame


def _envelope(frame, *, decision_id="decision-1", action="click", args=None):
    return ActorDecisionEnvelope(
        decision_id=decision_id,
        mission_id="mission-1",
        run_id="run-1",
        mission_revision=4,
        obligation_id="obligation-1",
        frame={
            "frame_id": frame.frame_id,
            "epoch": frame.epoch,
            "hwnd": frame.hwnd,
            "region": frame.region,
            "scope": frame.scope,
            "submitted_size": frame.submitted_size,
            "image_sha256": frame.image_sha256,
        },
        target=TargetCommitIdentity(
            "target-1", 101, 101, 7, 3, (10, 20, 101, 51),
        ),
        pointer=PointerLeaseState(PointerDisposition.RELEASED),
        broker_projection_id="projection-1",
        capability_grant_ids=(),
        allowed_evidence_scope=("frame-proof-1",),
        proposal=parse_actor_proposal({
            "format": "actor-proposal/0",
            "kind": "desktop_action",
            "action": action,
            "args": {"image_x": 10, "image_y": 5} if args is None else args,
            "local_intent": "activate the visible target",
        }),
    )


class Observation:
    def __init__(self, *, pointer_released=True):
        self.pointer_released = pointer_released
        self.requests = []

    def observe_commit(self, request):
        self.requests.append(request)
        return CommitEnvironment(
            target=request.target,
            pointer_released=self.pointer_released,
            mutation_allowed=True,
            stop_requested=False,
            canceled=False,
            foreground_hwnd=request.target.hwnd,
            foreground_root_hwnd=request.target.root_hwnd,
            foreground_process_id=request.target.process_id,
            hit_test_point=request.screen_point,
            hit_hwnd=request.target.hwnd,
            hit_root_hwnd=request.target.root_hwnd,
            hit_process_id=request.target.process_id,
        )


class FinalGuard:
    def __init__(self, decision=FinalClickGuardDecision.ADMITTED):
        self.decision = decision
        self.requests = []

    def check_before_mouse_down(self, request, screen_point):
        self.requests.append((request, screen_point))
        return self.decision


def _admitted_backend(backend):
    def execute(x, y, *, button, count, mechanism, hwnd, final_guard):
        decision = final_guard(x, y)
        if decision is not FinalClickGuardDecision.ADMITTED:
            return RawBackendResult(False, {"final_guard": decision.value})
        return backend(
            x, y, button=button, count=count, mechanism=mechanism, hwnd=hwnd,
        )

    return AdmittedPhysicalClickBackend(execute)


class PostAction:
    def __init__(self, *, frame_authority=None, fail=False, log=None):
        self.frame_authority = frame_authority
        self.fail = fail
        self.log = log
        self.calls = []

    def observe_after(self, envelope, *, admission, raw_receipt):
        self.calls.append((envelope, admission, raw_receipt))
        if self.log is not None:
            self.log.append(("post", envelope.decision_id))
        if self.fail:
            raise RuntimeError("post capture failed")
        post_frame_id = envelope.frame.frame_id + 1
        if self.frame_authority is not None:
            state = self.frame_authority.commit(
                epoch=envelope.frame.epoch,
                hwnd=envelope.frame.hwnd,
                region=envelope.frame.region,
                scope=envelope.frame.scope,
                reason="post_action",
                submitted_size=envelope.frame.submitted_size,
                image_b64=IMAGE + "A",
                captured_at=2.0,
                expected_previous_frame_id=envelope.frame.frame_id,
            )
            post_frame_id = state.frame_id
        return ControllerPostAction(
            post_frame_id,
            (f"evidence-{envelope.decision_id}",),
            "fresh post-action frame captured",
        )


def _controller(
    envelope,
    authority,
    backend,
    *,
    observation=None,
    final_guard=None,
    post=None,
    queue=None,
    enabled=True,
    permits=None,
    turn_index=0,
):
    permits = permits or MutationPermitAuthority(envelope.mission_id, envelope.run_id)
    permits.issue(envelope, turn_index=turn_index, still_current=lambda: True)
    return AtomicClickController(
        action_queue=queue or DesktopActionQueue(),
        frame_authority=authority,
        permits=permits,
        observation=observation or Observation(),
        final_guard=final_guard or FinalGuard(),
        raw_device=RawDeviceExecutor(RawDeviceBackends(
            admitted_physical_click=_admitted_backend(backend),
        )),
        post_action=post or PostAction(),
        enabled=enabled,
    ), permits


@pytest.mark.parametrize(
    ("action", "expected_count"),
    (("click", ClickCount.SINGLE), ("double_click", ClickCount.DOUBLE)),
)
def test_success_maps_submitted_pixels_and_posts_outside_frame_pin(
    action, expected_count,
):
    authority, frame = _authority()
    envelope = _envelope(frame, action=action)
    raw_calls = []

    def backend(x, y, *, button, count, mechanism, hwnd):
        raw_calls.append((x, y, button, count, mechanism, hwnd))
        return RawBackendResult(True, {"sent": 2 if count is ClickCount.DOUBLE else 1})

    post = PostAction(frame_authority=authority)
    controller, _permits = _controller(
        envelope, authority, backend, post=post,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.COMPLETED
    assert transition.input_completed is True
    assert transition.post_frame_id == 2
    assert raw_calls == [(
        110, 70, MouseButton.LEFT, expected_count, ClickMechanism.PHYSICAL, 101,
    )]
    assert len(post.calls) == 1


def test_commit_admission_rejection_never_calls_raw_device():
    authority, frame = _authority()
    envelope = _envelope(frame)
    raw_calls = []
    observation = Observation(pointer_released=False)
    controller, permits = _controller(
        envelope,
        authority,
        lambda *args, **kwargs: raw_calls.append((args, kwargs)),
        observation=observation,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.REJECTED
    assert transition.input_completed is False
    assert raw_calls == []
    assert permits.pending_count == 0
    assert observation.requests[0].screen_point == (110, 70)


def test_final_guard_rejection_is_not_applied_and_never_calls_input_backend():
    authority, frame = _authority()
    envelope = _envelope(frame)
    raw_calls = []
    final_guard = FinalGuard(FinalClickGuardDecision.HIT_TEST_CHANGED)
    controller, _permits = _controller(
        envelope,
        authority,
        lambda *args, **kwargs: raw_calls.append((args, kwargs)),
        final_guard=final_guard,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.REJECTED
    assert transition.input_completed is False
    assert raw_calls == []
    assert len(final_guard.requests) == 1


def test_stop_requested_at_final_guard_rejects_before_input():
    authority, frame = _authority()
    envelope = _envelope(frame)
    raw_calls = []
    stop_event = threading.Event()

    class StopBeforeGuard(FinalGuard):
        def check_before_mouse_down(self, request, screen_point):
            stop_event.set()
            return super().check_before_mouse_down(request, screen_point)

    controller, _permits = _controller(
        envelope,
        authority,
        lambda *args, **kwargs: raw_calls.append((args, kwargs)),
        final_guard=StopBeforeGuard(),
    )

    transition = controller.execute(envelope, stop_event=stop_event)

    assert transition.outcome is ControllerOutcome.REJECTED
    assert transition.input_completed is False
    assert raw_calls == []


def test_unknown_raw_outcome_is_not_replayed():
    authority, frame = _authority()
    envelope = _envelope(frame)
    raw_calls = []

    def backend(*args, **kwargs):
        raw_calls.append((args, kwargs))
        return RawBackendResult(None, {"down_sent": True})

    controller, _permits = _controller(envelope, authority, backend)

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.UNKNOWN
    assert transition.input_completed is None
    assert len(raw_calls) == 1


def test_permit_is_single_use_and_duplicate_execute_calls_raw_once():
    authority, frame = _authority()
    envelope = _envelope(frame)
    raw_calls = []
    post = PostAction()
    controller, permits = _controller(
        envelope,
        authority,
        lambda *args, **kwargs: (
            raw_calls.append((args, kwargs)) or RawBackendResult(True, {})
        ),
        post=post,
    )

    first = controller.execute(envelope)
    second = controller.execute(envelope)

    assert first.outcome is ControllerOutcome.COMPLETED
    assert second.outcome is ControllerOutcome.REJECTED
    assert len(raw_calls) == 1
    assert len(post.calls) == 2
    assert permits.pending_count == 0
    assert permits.consumed_count == 1


def test_stale_frame_rejects_before_permit_observation_and_raw():
    authority, frame = _authority()
    envelope = _envelope(frame)
    authority.commit(
        epoch=3,
        hwnd=101,
        region=(10, 20, 101, 51),
        scope="target_window",
        reason="newer",
        submitted_size=(11, 6),
        image_b64=IMAGE + "A",
        captured_at=2.0,
        expected_previous_frame_id=1,
    )
    raw_calls = []
    observation = Observation()
    post = PostAction()
    controller, permits = _controller(
        envelope,
        authority,
        lambda *args, **kwargs: raw_calls.append((args, kwargs)),
        observation=observation,
        post=post,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.REJECTED
    assert post.calls[0][1].reason is CommitAdmissionReason.FRAME_CHANGED
    assert permits.pending_count == 1
    assert observation.requests == []
    assert raw_calls == []


def test_post_action_failure_returns_unknown_without_repeating_raw():
    authority, frame = _authority()
    envelope = _envelope(frame)
    raw_calls = []
    controller, _permits = _controller(
        envelope,
        authority,
        lambda *args, **kwargs: (
            raw_calls.append((args, kwargs)) or RawBackendResult(True, {})
        ),
        post=PostAction(fail=True),
    )

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.UNKNOWN
    assert transition.input_completed is None
    assert transition.post_frame_id is None
    assert transition.evidence_refs == ()
    assert len(raw_calls) == 1


def test_physical_queue_serializes_raw_through_post_action():
    authority, frame = _authority()
    first = _envelope(frame, decision_id="decision-1")
    second = _envelope(frame, decision_id="decision-2", action="double_click")
    permits = MutationPermitAuthority("mission-1", "run-1")
    permits.issue(first, turn_index=0, still_current=lambda: True)
    permits.issue(second, turn_index=1, still_current=lambda: True)
    log = []
    lock = threading.Lock()

    def backend(*args, **kwargs):
        with lock:
            log.append(("raw", kwargs["count"].value))
        time.sleep(0.02)
        return RawBackendResult(True, {})

    controller = AtomicClickController(
        action_queue=DesktopActionQueue(),
        frame_authority=authority,
        permits=permits,
        observation=Observation(),
        final_guard=FinalGuard(),
        raw_device=RawDeviceExecutor(RawDeviceBackends(
            admitted_physical_click=_admitted_backend(backend),
        )),
        post_action=PostAction(log=log),
        enabled=True,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(controller.execute, (first, second)))

    assert all(result.outcome is ControllerOutcome.COMPLETED for result in results)
    assert [kind for kind, _value in log] == ["raw", "post", "raw", "post"]


def test_default_off_and_non_image_args_fail_before_queue_or_raw():
    authority, frame = _authority()
    envelope = _envelope(frame)
    raw_calls = []
    controller, _permits = _controller(
        envelope,
        authority,
        lambda *args, **kwargs: raw_calls.append((args, kwargs)),
        enabled=False,
    )
    with pytest.raises(AtomicClickControllerError, match="disabled"):
        controller.execute(envelope)

    bad = _envelope(
        frame,
        decision_id="decision-2",
        args={"image_x": 10, "image_y": 5, "button": "right"},
    )
    enabled, _ = _controller(
        bad,
        authority,
        lambda *args, **kwargs: raw_calls.append((args, kwargs)),
    )
    with pytest.raises(AtomicClickControllerError, match="only submitted-image"):
        enabled.execute(bad)
    assert raw_calls == []


def test_controller_post_action_contract_is_strict():
    with pytest.raises(AtomicClickControllerError, match="positive"):
        ControllerPostAction(0, ("evidence-1",))
    with pytest.raises(AtomicClickControllerError, match="duplicates"):
        ControllerPostAction(2, ("evidence-1", "evidence-1"))
    with pytest.raises(AtomicClickControllerError, match="valid"):
        ControllerPostAction(2, ("not an identity",))
