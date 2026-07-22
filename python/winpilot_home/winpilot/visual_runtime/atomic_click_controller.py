"""Opt-in atomic vNext ControllerPort adapter for image-grounded clicks.

The adapter deliberately supports only one narrow mutation slice.  It owns no
global runtime state and is not exported from :mod:`visual_runtime`; callers
must inject every existing owner explicitly and enable it deliberately.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from threading import Event
from typing import Protocol, runtime_checkable

from ..action.desktop_queue import DesktopActionQueue
from ..action.raw_device import (
    ClickCount,
    ClickMechanism,
    FinalClickGuardDecision,
    MouseButton,
    RawActionContext,
    RawActionReceipt,
    RawDeviceExecutor,
)
from ..action.replay_policy import MutationOutcome
from ..agent.frame_authority import FrameAuthority
from ..vnext.actor import ActorDecisionEnvelope, DesktopActionProposal
from ..vnext.runtime.contracts import (
    ControllerOutcome,
    ControllerTransition,
)
from ..vnext.runtime.mutation_permits import (
    MutationPermitAuthority,
    RuntimeMutationPermit,
)
from .commit_admission import (
    CommitAdmissionGate,
    CommitAdmissionReason,
    CommitAdmissionRequest,
    CommitAdmissionResult,
    CommitAdmissionStatus,
    CommitCallbackResult,
    CommitEnvironment,
    CommitRoute,
)
from .normalized_frame_coordinates import NormalizedFrameCoordinateMapper
from .submitted_image_coordinates import SubmittedImageCoordinateMapper


_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")


class AtomicClickControllerError(ValueError):
    """Raised before queue admission when this narrow adapter cannot execute."""


@runtime_checkable
class DesktopCommitObservation(Protocol):
    """Read current OS facts while the authoritative frame is pinned."""

    def observe_commit(self, request: CommitAdmissionRequest) -> CommitEnvironment: ...


@runtime_checkable
class PhysicalClickFinalGuardPort(Protocol):
    """Read-only final target guard invoked by the admitted physical backend."""

    def check_before_mouse_down(
        self, request: CommitAdmissionRequest, screen_point: tuple[int, int],
    ) -> FinalClickGuardDecision: ...


@dataclass(frozen=True, slots=True)
class ControllerPostAction:
    """Independent post-action frame/evidence returned inside the same lease."""

    post_frame_id: int
    evidence_refs: tuple[str, ...]
    visible_effect: str = ""

    def __post_init__(self) -> None:
        if type(self.post_frame_id) is not int or self.post_frame_id <= 0:
            raise AtomicClickControllerError("post_frame_id must be a positive integer")
        if isinstance(self.evidence_refs, (str, bytes)):
            raise AtomicClickControllerError("evidence_refs must be an identity array")
        try:
            refs = tuple(self.evidence_refs)
        except TypeError as exc:
            raise AtomicClickControllerError(
                "evidence_refs must be an identity array"
            ) from exc
        if not refs or any(
            type(ref) is not str or not _IDENTITY.fullmatch(ref.strip()) for ref in refs
        ):
            raise AtomicClickControllerError(
                "evidence_refs must contain valid non-empty identities"
            )
        normalized = tuple(sorted(ref.strip() for ref in refs))
        if len(normalized) != len(set(normalized)):
            raise AtomicClickControllerError("evidence_refs must not contain duplicates")
        if type(self.visible_effect) is not str:
            raise AtomicClickControllerError("visible_effect must be a string")
        object.__setattr__(self, "evidence_refs", normalized)
        object.__setattr__(self, "visible_effect", self.visible_effect.strip())


@runtime_checkable
class ControllerPostActionPort(Protocol):
    """Capture evidence after the frame pin is released, without leaving lease."""

    def observe_after(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        admission: CommitAdmissionResult,
        raw_receipt: RawActionReceipt | None,
    ) -> ControllerPostAction: ...


class AtomicClickController:
    """Default-off adapter implementing only physical click/double-click."""

    def __init__(
        self,
        *,
        action_queue: DesktopActionQueue,
        frame_authority: FrameAuthority,
        permits: MutationPermitAuthority,
        observation: DesktopCommitObservation,
        final_guard: PhysicalClickFinalGuardPort,
        raw_device: RawDeviceExecutor,
        post_action: ControllerPostActionPort,
        enabled: bool = False,
    ) -> None:
        if not isinstance(action_queue, DesktopActionQueue):
            raise TypeError("action_queue must be DesktopActionQueue")
        if not isinstance(frame_authority, FrameAuthority):
            raise TypeError("frame_authority must be FrameAuthority")
        if not isinstance(permits, MutationPermitAuthority):
            raise TypeError("permits must be MutationPermitAuthority")
        if not isinstance(raw_device, RawDeviceExecutor):
            raise TypeError("raw_device must be RawDeviceExecutor")
        if not isinstance(observation, DesktopCommitObservation):
            raise TypeError("observation must implement DesktopCommitObservation")
        if not isinstance(final_guard, PhysicalClickFinalGuardPort):
            raise TypeError("final_guard must implement PhysicalClickFinalGuardPort")
        if not isinstance(post_action, ControllerPostActionPort):
            raise TypeError("post_action must implement ControllerPostActionPort")
        if type(enabled) is not bool:
            raise TypeError("enabled must be boolean")
        self._queue = action_queue
        self._frames = frame_authority
        self._permits = permits
        self._gate = CommitAdmissionGate(observation.observe_commit)
        self._final_guard = final_guard
        self._raw = raw_device
        self._post = post_action
        self._enabled = enabled

    @staticmethod
    def _request(envelope: ActorDecisionEnvelope) -> tuple[
        CommitAdmissionRequest, ClickCount,
    ]:
        proposal = envelope.proposal
        if not isinstance(proposal, DesktopActionProposal) or proposal.action not in {
            "click", "double_click",
        }:
            raise AtomicClickControllerError(
                "AtomicClickController supports only click and double_click"
            )
        if envelope.target is None:
            raise AtomicClickControllerError("click requires a Runtime-bound target")
        args = dict(proposal.args)
        if set(args) != {"image_x", "image_y"}:
            raise AtomicClickControllerError(
                "click args must contain only submitted-image image_x and image_y"
            )
        try:
            mapped, _source = SubmittedImageCoordinateMapper(
                envelope.frame.submitted_size
            ).map_point_pair(
                args,
                image_keys=("image_x", "image_y"),
                frame_keys=("frame_x", "frame_y"),
                context=proposal.action,
            )
            point = NormalizedFrameCoordinateMapper(envelope.frame.region).map_point(
                mapped["frame_x"], mapped["frame_y"]
            )
            request = CommitAdmissionRequest(
                request_id=envelope.decision_id,
                frame=envelope.frame,
                target=envelope.target,
                route=CommitRoute.PHYSICAL,
                screen_point=point,
            )
        except ValueError as exc:
            raise AtomicClickControllerError(str(exc)) from exc
        count = (
            ClickCount.DOUBLE
            if proposal.action == "double_click"
            else ClickCount.SINGLE
        )
        return request, count

    @staticmethod
    def _rejected(
        request: CommitAdmissionRequest,
        reason: CommitAdmissionReason,
    ) -> CommitCallbackResult[RawActionReceipt]:
        return CommitCallbackResult(
            CommitAdmissionResult(
                request.request_id,
                CommitAdmissionStatus.NOT_STARTED,
                reason,
                False,
            ),
            None,
        )

    @classmethod
    def _permit_result(
        cls,
        request: CommitAdmissionRequest,
        permit: object,
        gate: CommitAdmissionGate,
        callback,
    ) -> CommitCallbackResult[RawActionReceipt]:
        if not isinstance(permit, RuntimeMutationPermit) or permit.allowed is not True:
            return cls._rejected(
                request, CommitAdmissionReason.MUTATION_NOT_ALLOWED,
            )
        return gate.admit_and_commit(request, callback)

    @staticmethod
    def _transition(
        envelope: ActorDecisionEnvelope,
        commit: CommitCallbackResult[RawActionReceipt],
        post: ControllerPostAction,
    ) -> ControllerTransition:
        receipt = commit.value
        if commit.admission.status is CommitAdmissionStatus.NOT_STARTED:
            outcome, completed = ControllerOutcome.REJECTED, False
        elif not isinstance(receipt, RawActionReceipt):
            outcome, completed = ControllerOutcome.UNKNOWN, None
        elif receipt.outcome is MutationOutcome.APPLIED:
            outcome, completed = ControllerOutcome.COMPLETED, True
        elif receipt.outcome in {
            MutationOutcome.NOT_STARTED,
            MutationOutcome.NOT_APPLIED,
        }:
            outcome, completed = ControllerOutcome.REJECTED, False
        else:
            outcome, completed = ControllerOutcome.UNKNOWN, None
        return ControllerTransition(
            outcome=outcome,
            action_id=envelope.decision_id,
            action=envelope.proposal.action,
            input_completed=completed,
            visible_effect=post.visible_effect,
            evidence_refs=post.evidence_refs,
            post_frame_id=post.post_frame_id,
        )

    def execute(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        stop_event: Event | None = None,
    ) -> ControllerTransition:
        if not self._enabled:
            raise AtomicClickControllerError("AtomicClickController is disabled")
        if not isinstance(envelope, ActorDecisionEnvelope):
            raise TypeError("envelope must be ActorDecisionEnvelope")
        if stop_event is not None and not isinstance(stop_event, Event):
            raise TypeError("stop_event must be threading.Event or null")
        request, count = self._request(envelope)
        target = envelope.target
        assert target is not None  # established by _request

        def final_guard(x: int, y: int) -> FinalClickGuardDecision:
            if stop_event is not None and stop_event.is_set():
                return FinalClickGuardDecision.STOP_REQUESTED
            decision = self._final_guard.check_before_mouse_down(request, (x, y))
            if stop_event is not None and stop_event.is_set():
                return FinalClickGuardDecision.STOP_REQUESTED
            return decision

        def under_physical_lease() -> ControllerTransition:
            def under_frame_pin() -> CommitCallbackResult[RawActionReceipt]:
                permit = self._permits.consume(envelope)
                return self._permit_result(
                    request,
                    permit,
                    self._gate,
                    lambda: self._raw.click(
                        RawActionContext(target.hwnd, target.hwnd),
                        request.screen_point[0],
                        request.screen_point[1],
                        MouseButton.LEFT,
                        count,
                        ClickMechanism.PHYSICAL,
                        final_guard=final_guard,
                    ),
                )

            pinned, commit = self._frames.run_if_current(
                envelope.frame, under_frame_pin,
            )
            if not pinned:
                commit = self._rejected(
                    request, CommitAdmissionReason.FRAME_CHANGED,
                )
            assert isinstance(commit, CommitCallbackResult)
            try:
                post = self._post.observe_after(
                    envelope,
                    admission=commit.admission,
                    raw_receipt=commit.value,
                )
                if not isinstance(post, ControllerPostAction):
                    raise TypeError("post_action returned an invalid result")
            except Exception:
                # Input may already have begun.  Never turn a missing post-frame
                # into a replayable rejection or call RawDevice a second time.
                return ControllerTransition(
                    outcome=ControllerOutcome.UNKNOWN,
                    action_id=envelope.decision_id,
                    action=envelope.proposal.action,
                    input_completed=None,
                )
            return self._transition(envelope, commit, post)

        return self._queue.run(
            envelope.proposal.action,
            under_physical_lease,
            target_hwnd=target.hwnd,
            scope="physical",
            cancel_event=stop_event,
            metadata={"owner": "vnext_atomic_click_controller"},
        )


__all__ = [
    "AtomicClickController",
    "AtomicClickControllerError",
    "ControllerPostAction",
    "ControllerPostActionPort",
    "DesktopCommitObservation",
    "PhysicalClickFinalGuardPort",
]
