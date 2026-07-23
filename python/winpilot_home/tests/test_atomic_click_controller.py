from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from winpilot.action.desktop_queue import DesktopActionQueue
from winpilot.action.raw_device import (
    AdmittedPhysicalClickBackend,
    AdmittedPhysicalHotkeyBackend,
    AdmittedPhysicalScrollBackend,
    AdmittedPhysicalTypeTextBackend,
    ClickCount,
    ClickMechanism,
    FinalKeyboardGuardDecision,
    FinalPointerGuardDecision,
    HotkeyChord,
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
    AtomicDesktopController,
    AtomicDesktopControllerError,
    ControllerPostAction,
)
from winpilot.visual_runtime.commit_admission import (
    CommitAdmissionReason,
    CommitEnvironment,
    TargetCommitIdentity,
)


IMAGE = "YWJj"
PROCESS_IDENTITY = "process-" + "1" * 32


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
            "target-1", 101, 101, 7, PROCESS_IDENTITY, 3,
            (10, 20, 101, 51),
        ),
        pointer=PointerLeaseState(PointerDisposition.RELEASED),
        broker_projection_id="projection-1",
        capability_grant_ids=(),
        allowed_evidence_scope=("frame-proof-1",),
        proposal=parse_actor_proposal({
            "format": "actor-proposal/1",
            "kind": "desktop_action",
            "action": action,
            "args": {"image_x": 10, "image_y": 5} if args is None else args,
            "local_intent": "activate the visible target",
        }),
    )


class Observation:
    def __init__(self, *, pointer_released=True, foreground=True):
        self.pointer_released = pointer_released
        self.foreground = foreground
        self.requests = []

    def observe_commit(self, request):
        self.requests.append(request)
        return CommitEnvironment(
            target=request.target,
            pointer_released=self.pointer_released,
            mutation_allowed=True,
            stop_requested=False,
            canceled=False,
            foreground_hwnd=request.target.hwnd if self.foreground else 202,
            foreground_root_hwnd=request.target.root_hwnd if self.foreground else 202,
            foreground_process_id=request.target.process_id if self.foreground else 8,
            foreground_process_identity=(
                request.target.process_identity
                if self.foreground else "process-" + "2" * 32
            ),
            hit_test_point=request.screen_point,
            hit_hwnd=request.target.hwnd if request.screen_point is not None else None,
            hit_root_hwnd=(
                request.target.root_hwnd if request.screen_point is not None else None
            ),
            hit_process_id=(
                request.target.process_id if request.screen_point is not None else None
            ),
            hit_process_identity=(
                request.target.process_identity
                if request.screen_point is not None else None
            ),
        )


class FinalGuard:
    def __init__(
        self,
        decision=FinalPointerGuardDecision.ADMITTED,
        keyboard_decision=FinalKeyboardGuardDecision.ADMITTED,
    ):
        self.decision = decision
        self.keyboard_decision = keyboard_decision
        self.requests = []
        self.keyboard_requests = []

    def check_before_pointer_input(self, request, screen_point):
        self.requests.append((request, screen_point))
        return self.decision

    def check_before_keyboard_input(self, request):
        self.keyboard_requests.append(request)
        return self.keyboard_decision


def _admitted_backend(backend):
    def execute(x, y, *, button, count, mechanism, hwnd, final_guard):
        decision = final_guard(x, y)
        if decision is not FinalPointerGuardDecision.ADMITTED:
            return RawBackendResult(False, {"final_guard": decision.value})
        return backend(
            x, y, button=button, count=count, mechanism=mechanism, hwnd=hwnd,
        )

    return AdmittedPhysicalClickBackend(execute)


def _admitted_scroll_backend(backend):
    def execute(x, y, amount, *, hwnd, final_guard):
        decision = final_guard(x, y)
        if decision is not FinalPointerGuardDecision.ADMITTED:
            return RawBackendResult(False, {"final_guard": decision.value})
        return backend(x, y, amount, hwnd=hwnd)

    return AdmittedPhysicalScrollBackend(execute)


def _admitted_keyboard_backend(wrapper, backend):
    def execute(value, *, hwnd, final_guard):
        decision = final_guard()
        if decision is not FinalKeyboardGuardDecision.ADMITTED:
            return RawBackendResult(False, {"guard_decision": decision.value})
        return backend(value, hwnd=hwnd)

    return wrapper(execute)


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
    scroll_backend=None,
    type_text_backend=None,
    hotkey_backend=None,
):
    permits = permits or MutationPermitAuthority(envelope.mission_id, envelope.run_id)
    permits.issue(envelope, turn_index=turn_index, still_current=lambda: True)
    return AtomicDesktopController(
        action_queue=queue or DesktopActionQueue(),
        frame_authority=authority,
        permits=permits,
        observation=observation or Observation(),
        final_guard=final_guard or FinalGuard(),
        raw_device=RawDeviceExecutor(RawDeviceBackends(
            admitted_physical_click=_admitted_backend(backend),
            admitted_physical_scroll=(
                None
                if scroll_backend is None
                else _admitted_scroll_backend(scroll_backend)
            ),
            admitted_physical_type_text=(
                None if type_text_backend is None else _admitted_keyboard_backend(
                    AdmittedPhysicalTypeTextBackend, type_text_backend,
                )
            ),
            admitted_physical_hotkey=(
                None if hotkey_backend is None else _admitted_keyboard_backend(
                    AdmittedPhysicalHotkeyBackend, hotkey_backend,
                )
            ),
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


@pytest.mark.parametrize(
    ("direction", "notches", "expected_amount"),
    (("down", 12, -12), ("down", 1, -1), ("up", 1, 1), ("up", 12, 12)),
)
def test_scroll_maps_explicit_direction_to_one_canonical_backend_call(
    direction, notches, expected_amount,
):
    authority, frame = _authority()
    envelope = _envelope(
        frame,
        action="scroll",
        args={
            "image_x": 10,
            "image_y": 5,
            "direction": direction,
            "notches": notches,
        },
    )
    raw_calls = []

    def scroll_backend(x, y, amount, *, hwnd):
        raw_calls.append((x, y, amount, hwnd))
        return RawBackendResult(True, {"sent_notches": amount})

    final_guard = FinalGuard()
    post = PostAction(frame_authority=authority)
    controller, _permits = _controller(
        envelope,
        authority,
        lambda *_args, **_kwargs: pytest.fail("click backend must not run"),
        final_guard=final_guard,
        post=post,
        scroll_backend=scroll_backend,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.COMPLETED
    assert transition.input_completed is True
    assert transition.action == "scroll"
    assert raw_calls == [(110, 70, expected_amount, 101)]
    assert len(final_guard.requests) == 1
    assert len(post.calls) == 1


@pytest.mark.parametrize(
    "args",
    (
        {"image_x": 10, "image_y": 5},
        {"image_x": 10, "image_y": 5, "direction": "up"},
        {"image_x": 10, "image_y": 5, "direction": "left", "notches": 1},
        {"image_x": 10, "image_y": 5, "direction": "up", "notches": 0},
        {"image_x": 10, "image_y": 5, "direction": "up", "notches": True},
        {"image_x": 10, "image_y": 5, "direction": "up", "notches": 13},
        {"image_x": 10, "image_y": 5, "direction": "down", "notches": -1},
        {
            "image_x": 10, "image_y": 5, "direction": "down", "notches": 1,
            "amount": -1,
        },
    ),
)
def test_scroll_rejects_noncanonical_args_before_queue_or_raw(args):
    authority, frame = _authority()
    envelope = _envelope(frame, action="scroll", args=args)
    raw_calls = []
    controller, _permits = _controller(
        envelope,
        authority,
        lambda *_args, **_kwargs: pytest.fail("click backend must not run"),
        scroll_backend=lambda *call, **kwargs: raw_calls.append((call, kwargs)),
    )

    with pytest.raises(AtomicDesktopControllerError):
        controller.execute(envelope)

    assert raw_calls == []


def test_uncertain_scroll_backend_is_called_once_and_never_replayed():
    authority, frame = _authority()
    envelope = _envelope(
        frame,
        action="scroll",
        args={
            "image_x": 10,
            "image_y": 5,
            "direction": "up",
            "notches": 3,
        },
    )
    raw_calls = []

    def uncertain(*args, **kwargs):
        raw_calls.append((args, kwargs))
        raise OSError("wheel outcome unknown")

    controller, _permits = _controller(
        envelope,
        authority,
        lambda *_args, **_kwargs: pytest.fail("click backend must not run"),
        scroll_backend=uncertain,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.UNKNOWN
    assert transition.input_completed is None
    assert len(raw_calls) == 1


@pytest.mark.parametrize(
    ("action", "args", "expected_value"),
    (
        ("type_text", {"text": "Hello, 世界"}, "Hello, 世界"),
        (
            "hotkey",
            {"modifiers": ["shift", "ctrl"], "key": "a"},
            HotkeyChord(("ctrl", "shift", "a")),
        ),
    ),
)
def test_keyboard_actions_use_one_coordinate_free_atomic_transaction(
    action, args, expected_value,
):
    authority, frame = _authority()
    envelope = _envelope(frame, action=action, args=args)
    raw_calls = []
    observation = Observation()
    final_guard = FinalGuard()
    post = PostAction(frame_authority=authority)
    queue = DesktopActionQueue()

    def backend(value, *, hwnd):
        raw_calls.append((value, hwnd))
        return RawBackendResult(True, {"ok": True})

    controller, permits = _controller(
        envelope,
        authority,
        lambda *_args, **_kwargs: pytest.fail("pointer backend must not run"),
        observation=observation,
        final_guard=final_guard,
        post=post,
        queue=queue,
        type_text_backend=backend if action == "type_text" else None,
        hotkey_backend=backend if action == "hotkey" else None,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.COMPLETED
    assert transition.input_completed is True
    assert raw_calls == [(expected_value, 101)]
    assert observation.requests[0].screen_point is None
    assert final_guard.requests == []
    assert final_guard.keyboard_requests == [observation.requests[0]]
    assert len(post.calls) == 1
    assert permits.pending_count == 0
    assert permits.consumed_count == 1
    queue_status = queue.status()
    assert queue_status["submitted"] == 1
    assert queue_status["completed"] == 1
    assert queue_status["last_action"] == action


def test_keyboard_final_guard_rejects_without_pointer_guard_or_input():
    authority, frame = _authority()
    envelope = _envelope(frame, action="type_text", args={"text": "never sent"})
    raw_calls = []
    final_guard = FinalGuard(
        keyboard_decision=FinalKeyboardGuardDecision.FOREGROUND_CHANGED,
    )
    controller, _permits = _controller(
        envelope,
        authority,
        lambda *_args, **_kwargs: pytest.fail("pointer backend must not run"),
        final_guard=final_guard,
        type_text_backend=lambda *call, **kwargs: raw_calls.append((call, kwargs)),
    )

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.REJECTED
    assert transition.input_completed is False
    assert raw_calls == []
    assert final_guard.requests == []
    assert len(final_guard.keyboard_requests) == 1


@pytest.mark.parametrize("action,args", (
    ("type_text", {"text": "once"}),
    ("hotkey", {"modifiers": ["ctrl"], "key": "a"}),
))
@pytest.mark.parametrize("failure", ("unknown", "exception", "post"))
def test_keyboard_uncertainty_is_never_replayed(action, args, failure):
    authority, frame = _authority()
    envelope = _envelope(frame, action=action, args=args)
    raw_calls = []
    post = PostAction(fail=failure == "post")

    def backend(value, *, hwnd):
        raw_calls.append((value, hwnd))
        if failure == "exception":
            raise OSError("input outcome unknown")
        return RawBackendResult(None if failure == "unknown" else True, {})

    controller, _permits = _controller(
        envelope,
        authority,
        lambda *_args, **_kwargs: pytest.fail("pointer backend must not run"),
        post=post,
        type_text_backend=backend if action == "type_text" else None,
        hotkey_backend=backend if action == "hotkey" else None,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.UNKNOWN
    assert transition.input_completed is None
    assert len(raw_calls) == 1
    assert len(post.calls) == 1


@pytest.mark.parametrize("action,args", (
    ("type_text", {"text": "must not be sent"}),
    ("hotkey", {"modifiers": ["ctrl"], "key": "a"}),
))
@pytest.mark.parametrize("rejection", ("stale_frame", "foreground"))
def test_keyboard_stale_frame_or_foreground_rejects_before_raw(
    action, args, rejection,
):
    authority, frame = _authority()
    envelope = _envelope(frame, action=action, args=args)
    if rejection == "stale_frame":
        authority.commit(
            epoch=3, hwnd=101, region=(10, 20, 101, 51),
            scope="target_window", reason="newer", submitted_size=(11, 6),
            image_b64=IMAGE + "A", captured_at=2.0,
            expected_previous_frame_id=frame.frame_id,
        )
    raw_calls = []

    def backend(*call, **kwargs):
        raw_calls.append((call, kwargs))
        return RawBackendResult(True, {})

    observation = Observation(foreground=rejection != "foreground")
    controller, _permits = _controller(
        envelope,
        authority,
        lambda *_args, **_kwargs: pytest.fail("pointer backend must not run"),
        observation=observation,
        type_text_backend=backend if action == "type_text" else None,
        hotkey_backend=backend if action == "hotkey" else None,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.REJECTED
    assert transition.input_completed is False
    assert raw_calls == []
    if rejection == "stale_frame":
        assert observation.requests == []
    else:
        request = observation.requests[0]
        assert request.screen_point is None


def test_commit_observation_reads_the_same_transient_permit_owner() -> None:
    authority, frame = _authority()
    envelope = _envelope(frame)
    permits = MutationPermitAuthority(envelope.mission_id, envelope.run_id)

    class PermitBackedObservation(Observation):
        def observe_commit(self, request):
            assert permits.active_admission_matches(
                request.request_id,
                frame=request.frame,
                target=request.target,
                pointer=envelope.pointer,
            ) is True
            return super().observe_commit(request)

    controller, _ = _controller(
        envelope,
        authority,
        lambda *_args, **_kwargs: RawBackendResult(True, {}),
        observation=PermitBackedObservation(),
        permits=permits,
    )

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.COMPLETED
    assert permits.active_count == 0
    assert permits.consumed_count == 1


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
    final_guard = FinalGuard(FinalPointerGuardDecision.HIT_TEST_CHANGED)
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
        def check_before_pointer_input(self, request, screen_point):
            stop_event.set()
            return super().check_before_pointer_input(request, screen_point)

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


def test_post_action_failure_preserves_proven_admission_rejection():
    authority, frame = _authority()
    envelope = _envelope(frame)
    raw_calls = []
    controller, _permits = _controller(
        envelope,
        authority,
        lambda *args, **kwargs: raw_calls.append((args, kwargs)),
        observation=Observation(pointer_released=False),
        post=PostAction(fail=True),
    )

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.REJECTED
    assert transition.input_completed is False
    assert transition.post_frame_id is None
    assert transition.evidence_refs == ()
    assert raw_calls == []


def test_post_action_failure_preserves_proven_raw_non_execution():
    authority, frame = _authority()
    envelope = _envelope(frame)
    raw_calls = []
    controller, _permits = _controller(
        envelope,
        authority,
        lambda *args, **kwargs: (
            raw_calls.append((args, kwargs)) or RawBackendResult(False, {})
        ),
        post=PostAction(fail=True),
    )

    transition = controller.execute(envelope)

    assert transition.outcome is ControllerOutcome.REJECTED
    assert transition.input_completed is False
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

    controller = AtomicDesktopController(
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
    with pytest.raises(AtomicDesktopControllerError, match="disabled"):
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
    with pytest.raises(AtomicDesktopControllerError, match="submitted-image contract"):
        enabled.execute(bad)
    assert raw_calls == []


def test_bound_actor_activate_target_never_enters_coordinate_or_raw_path() -> None:
    authority, frame = _authority()
    envelope = _envelope(
        frame,
        action="activate_target",
        args={"candidate_ordinal": 1},
    )
    raw_calls = []
    observation = Observation()
    queue = DesktopActionQueue()
    controller, _permits = _controller(
        envelope,
        authority,
        lambda *call, **kwargs: raw_calls.append((call, kwargs)),
        observation=observation,
        queue=queue,
    )

    with pytest.raises(
        AtomicDesktopControllerError,
        match="Runtime-bound target acquisition envelope",
    ):
        controller.execute(envelope)

    assert raw_calls == []
    assert observation.requests == []
    assert queue.status()["submitted"] == 0


def test_controller_post_action_contract_is_strict():
    with pytest.raises(AtomicDesktopControllerError, match="positive"):
        ControllerPostAction(0, ("evidence-1",))
    with pytest.raises(AtomicDesktopControllerError, match="duplicates"):
        ControllerPostAction(2, ("evidence-1", "evidence-1"))
    with pytest.raises(AtomicDesktopControllerError, match="valid"):
        ControllerPostAction(2, ("not an identity",))
