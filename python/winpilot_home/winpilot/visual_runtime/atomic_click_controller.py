"""Opt-in atomic vNext ControllerPort adapter for bounded desktop input.

The adapter deliberately supports only a narrow mutation slice.  It owns no
global runtime state and is not exported from :mod:`visual_runtime`; callers
must inject every existing owner explicitly and enable it deliberately.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import ntpath
import re
from threading import Event
import time
from typing import Protocol, TypeVar, runtime_checkable

from ..action.desktop_queue import DesktopActionCanceled, DesktopActionQueue
from ..action.raw_device import (
    ClickCount,
    ClickMechanism,
    FinalKeyboardGuardDecision,
    FinalPointerGuardDecision,
    FinalTargetActivationGuardDecision,
    FinalTargetLaunchGuardDecision,
    MouseButton,
    RawActionContext,
    RawActionReceipt,
    RawDeviceExecutor,
    RawTargetActivationContext,
    RawTargetLaunchContext,
    RawTargetLaunchReceipt,
)
from ..action.replay_policy import MutationOutcome
from ..agent.frame_authority import FrameAuthority
from ..vnext.actor import ActorDecisionEnvelope, DesktopActionProposal
from ..vnext.runtime.contracts import (
    ControllerOutcome,
    ControllerTransition,
)
from ..vnext.runtime.mutation_permits import (
    MutationEnvelope,
    MutationPermitAuthority,
    RuntimeMutationPermit,
)
from ..vnext.runtime.target_activation import (
    ActivatedTargetIdentity,
    TargetActivationEnvelope,
    TargetActivationOutcome,
    TargetActivationTransition,
)
from ..vnext.runtime.target_launch import (
    TargetLaunchEnvelope,
    TargetLaunchOutcome,
    TargetLaunchTransition,
)
from ..vnext.targeting import TargetState
from ..vnext.targeting.executable_presence import ExecutablePresenceSnapshot
from .commit_admission import (
    CommitAdmissionGate,
    CommitInputKind,
    CommitAdmissionReason,
    CommitAdmissionRequest,
    CommitAdmissionResult,
    CommitAdmissionStatus,
    CommitCallbackResult,
    CommitEnvironment,
    CommitRoute,
)
from .keyboard_contracts import (
    CANONICAL_HOTKEY_KEYS,
    CANONICAL_HOTKEY_MODIFIERS,
    HotkeyChord,
    MAX_TYPE_TEXT_CODEPOINTS,
)
from .normalized_frame_coordinates import NormalizedFrameCoordinateMapper
from .submitted_image_coordinates import SubmittedImageCoordinateMapper


_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_AcquisitionResult = TypeVar("_AcquisitionResult")
SUPPORTED_DESKTOP_TOOL_NAMES = tuple(sorted((
    "click", "double_click", "scroll", "type_text", "hotkey",
)))
REQUIRED_RAW_ACTION_NAMES = tuple(sorted((
    "click", "scroll", "type_text", "hotkey",
)))


class AtomicDesktopControllerError(ValueError):
    """Raised before queue admission when this narrow adapter cannot execute."""


@runtime_checkable
class DesktopCommitObservation(Protocol):
    """Read current OS facts while the authoritative frame is pinned."""

    def observe_commit(self, request: CommitAdmissionRequest) -> CommitEnvironment: ...


@runtime_checkable
class PhysicalInputFinalGuardPort(Protocol):
    """Read-only final target guard invoked by an admitted physical backend."""

    def check_before_pointer_input(
        self, request: CommitAdmissionRequest, screen_point: tuple[int, int],
    ) -> FinalPointerGuardDecision: ...

    def check_before_keyboard_input(
        self, request: CommitAdmissionRequest,
    ) -> FinalKeyboardGuardDecision: ...


@runtime_checkable
class AdmittedRawDeviceInventoryPort(Protocol):
    """Name-only view used by composition before any Actor is constructed."""

    @property
    def admitted_action_names(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class ControllerPostAction:
    """Independent post-action frame/evidence returned inside the same lease."""

    post_frame_id: int
    evidence_refs: tuple[str, ...]
    visible_effect: str = ""

    def __post_init__(self) -> None:
        if type(self.post_frame_id) is not int or self.post_frame_id <= 0:
            raise AtomicDesktopControllerError("post_frame_id must be a positive integer")
        if isinstance(self.evidence_refs, (str, bytes)):
            raise AtomicDesktopControllerError("evidence_refs must be an identity array")
        try:
            refs = tuple(self.evidence_refs)
        except TypeError as exc:
            raise AtomicDesktopControllerError(
                "evidence_refs must be an identity array"
            ) from exc
        if not refs or any(
            type(ref) is not str or not _IDENTITY.fullmatch(ref.strip()) for ref in refs
        ):
            raise AtomicDesktopControllerError(
                "evidence_refs must contain valid non-empty identities"
            )
        normalized = tuple(sorted(ref.strip() for ref in refs))
        if len(normalized) != len(set(normalized)):
            raise AtomicDesktopControllerError("evidence_refs must not contain duplicates")
        if type(self.visible_effect) is not str:
            raise AtomicDesktopControllerError("visible_effect must be a string")
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


@dataclass(frozen=True, slots=True)
class TargetActivationObservation:
    """Fresh strong identity read for one Runtime-selected window."""

    activated_target: ActivatedTargetIdentity
    exact_foreground: bool

    def __post_init__(self) -> None:
        if not isinstance(self.activated_target, ActivatedTargetIdentity):
            raise AtomicDesktopControllerError(
                "activated_target must be ActivatedTargetIdentity"
            )
        if type(self.exact_foreground) is not bool:
            raise AtomicDesktopControllerError(
                "exact_foreground must be boolean"
            )


@runtime_checkable
class TargetActivationObservationPort(Protocol):
    """Read current target/foreground facts without mutating the desktop."""

    def observe_activation(
        self,
        envelope: TargetActivationEnvelope,
    ) -> TargetActivationObservation: ...


@dataclass(frozen=True, slots=True)
class TargetActivationPostAction:
    """Independent foreground/frame proof after one activation transaction."""

    activated_target: ActivatedTargetIdentity
    exact_foreground: bool
    post_frame_id: int
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.activated_target, ActivatedTargetIdentity):
            raise AtomicDesktopControllerError(
                "activated_target must be ActivatedTargetIdentity"
            )
        if type(self.exact_foreground) is not bool:
            raise AtomicDesktopControllerError(
                "exact_foreground must be boolean"
            )
        if type(self.post_frame_id) is not int or self.post_frame_id <= 0:
            raise AtomicDesktopControllerError(
                "post_frame_id must be a positive integer"
            )
        if isinstance(self.evidence_refs, (str, bytes)):
            raise AtomicDesktopControllerError(
                "evidence_refs must be an identity array"
            )
        try:
            refs = tuple(self.evidence_refs)
        except TypeError as exc:
            raise AtomicDesktopControllerError(
                "evidence_refs must be an identity array"
            ) from exc
        if not refs or any(
            type(ref) is not str or not _IDENTITY.fullmatch(ref.strip())
            for ref in refs
        ):
            raise AtomicDesktopControllerError(
                "evidence_refs must contain valid non-empty identities"
            )
        normalized = tuple(sorted(ref.strip() for ref in refs))
        if len(normalized) != len(set(normalized)):
            raise AtomicDesktopControllerError(
                "evidence_refs must not contain duplicates"
            )
        object.__setattr__(self, "evidence_refs", normalized)


@runtime_checkable
class TargetActivationPostActionPort(Protocol):
    """Capture exact foreground and target-window frame proof after activation."""

    def observe_after_activation(
        self,
        envelope: TargetActivationEnvelope,
        *,
        raw_receipt: RawActionReceipt | None,
    ) -> TargetActivationPostAction: ...


class TargetLaunchObservationState(str, Enum):
    """One read-only launched-process/window observation."""

    READY = "ready"
    NOT_READY = "not_ready"
    EXITED = "exited"
    PROCESS_REUSED = "process_reused"
    AMBIGUOUS = "ambiguous"
    SOURCE_UNAVAILABLE = "source_unavailable"


@dataclass(frozen=True, slots=True)
class TargetLaunchObservationResult:
    """A status plus the existing strong target shape when uniquely ready."""

    state: TargetLaunchObservationState
    observation: TargetActivationObservation | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.state, TargetLaunchObservationState)
            or (self.state is TargetLaunchObservationState.READY)
            != isinstance(self.observation, TargetActivationObservation)
        ):
            raise AtomicDesktopControllerError(
                "only a valid ready launch observation may carry a target"
            )


@runtime_checkable
class TargetLaunchPresencePort(Protocol):
    """Read exact/unresolved process presence for one canonical EXE."""

    def read(self, executable_path: str) -> ExecutablePresenceSnapshot: ...


@runtime_checkable
class TargetLaunchObservationPort(Protocol):
    """Take one read-only observation; Runtime owns the bounded poll loop."""

    def observe_launch(
        self,
        envelope: TargetLaunchEnvelope,
        *,
        raw_receipt: RawTargetLaunchReceipt,
        selected_hwnd: int | None,
    ) -> TargetLaunchObservationResult: ...


@runtime_checkable
class TargetLaunchPostActionPort(Protocol):
    """Capture proof for the one launched target selected by bounded polling."""

    def observe_after_launch(
        self,
        envelope: TargetLaunchEnvelope,
        *,
        raw_receipt: RawTargetLaunchReceipt,
        selected_target: ActivatedTargetIdentity,
    ) -> TargetActivationPostAction: ...


@dataclass(frozen=True, slots=True)
class _PreparedInput:
    request: CommitAdmissionRequest
    click_count: ClickCount | None = None
    scroll_amount: int | None = None
    text: str | None = None
    chord: HotkeyChord | None = None


@dataclass(frozen=True, slots=True)
class _TargetAcquisitionAttempt:
    raw_receipt: RawActionReceipt | RawTargetLaunchReceipt | None = None
    already_foreground: bool = False
    failure_reason: str = ""


class AtomicDesktopController:
    """Default-off adapter for one physical desktop-input transaction."""

    supported_tool_names = SUPPORTED_DESKTOP_TOOL_NAMES
    required_raw_action_names = REQUIRED_RAW_ACTION_NAMES

    def __init__(
        self,
        *,
        action_queue: DesktopActionQueue,
        frame_authority: FrameAuthority,
        permits: MutationPermitAuthority,
        observation: DesktopCommitObservation,
        final_guard: PhysicalInputFinalGuardPort,
        raw_device: RawDeviceExecutor,
        post_action: ControllerPostActionPort,
        activation_observation: TargetActivationObservationPort | None = None,
        activation_post_action: TargetActivationPostActionPort | None = None,
        launch_presence: TargetLaunchPresencePort | None = None,
        launch_observation: TargetLaunchObservationPort | None = None,
        launch_post_action: TargetLaunchPostActionPort | None = None,
        launch_poll_attempts: int = 51,
        launch_poll_interval_s: float = 0.1,
        launch_poll_wait: Callable[[float], None] = time.sleep,
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
        if not isinstance(final_guard, PhysicalInputFinalGuardPort):
            raise TypeError("final_guard must implement PhysicalInputFinalGuardPort")
        if not isinstance(post_action, ControllerPostActionPort):
            raise TypeError("post_action must implement ControllerPostActionPort")
        if activation_observation is not None and not isinstance(
            activation_observation, TargetActivationObservationPort
        ):
            raise TypeError(
                "activation_observation must implement "
                "TargetActivationObservationPort or be null"
            )
        if activation_post_action is not None and not isinstance(
            activation_post_action, TargetActivationPostActionPort
        ):
            raise TypeError(
                "activation_post_action must implement "
                "TargetActivationPostActionPort or be null"
            )
        for name, value, port in (
            ("launch_presence", launch_presence, TargetLaunchPresencePort),
            ("launch_observation", launch_observation, TargetLaunchObservationPort),
            ("launch_post_action", launch_post_action, TargetLaunchPostActionPort),
        ):
            if value is not None and not isinstance(value, port):
                raise TypeError(f"{name} does not implement its launch port")
        if type(launch_poll_attempts) is not int or not 1 <= launch_poll_attempts <= 100:
            raise ValueError("launch_poll_attempts must be in [1, 100]")
        if (
            type(launch_poll_interval_s) not in {int, float}
            or not 0.0 <= float(launch_poll_interval_s) <= 1.0
        ):
            raise ValueError("launch_poll_interval_s must be finite and in [0, 1]")
        if not callable(launch_poll_wait):
            raise TypeError("launch_poll_wait must be callable")
        if type(enabled) is not bool:
            raise TypeError("enabled must be boolean")
        self._queue = action_queue
        self._frames = frame_authority
        self._permits = permits
        self._gate = CommitAdmissionGate(observation.observe_commit)
        self._final_guard = final_guard
        self._raw = raw_device
        self._post = post_action
        self._activation_observation = activation_observation
        self._activation_post = activation_post_action
        self._launch_presence = launch_presence
        self._launch_observation = launch_observation
        self._launch_post = launch_post_action
        self._launch_poll_attempts = launch_poll_attempts
        self._launch_poll_interval_s = float(launch_poll_interval_s)
        self._launch_poll_wait = launch_poll_wait
        self._enabled = enabled

    @staticmethod
    def _request(envelope: ActorDecisionEnvelope) -> _PreparedInput:
        proposal = envelope.proposal
        if (
            not isinstance(proposal, DesktopActionProposal)
            or proposal.action not in SUPPORTED_DESKTOP_TOOL_NAMES
        ):
            raise AtomicDesktopControllerError(
                "AtomicDesktopController does not support this desktop action"
            )
        if envelope.target is None:
            raise AtomicDesktopControllerError(
                "desktop input requires a Runtime-bound target"
            )
        args = dict(proposal.args)
        if proposal.action == "type_text":
            if set(args) != {"text"}:
                raise AtomicDesktopControllerError(
                    "type_text args do not match the desktop-input contract"
                )
            text = args["text"]
            if type(text) is not str or not 1 <= len(text) <= MAX_TYPE_TEXT_CODEPOINTS:
                raise AtomicDesktopControllerError(
                    "type_text requires bounded non-empty text"
                )
            return _PreparedInput(
                request=CommitAdmissionRequest(
                    request_id=envelope.decision_id,
                    frame=envelope.frame,
                    target=envelope.target,
                    route=CommitRoute.PHYSICAL,
                    input_kind=CommitInputKind.KEYBOARD,
                    screen_point=None,
                ),
                text=text,
            )
        if proposal.action == "hotkey":
            if set(args) != {"modifiers", "key"}:
                raise AtomicDesktopControllerError(
                    "hotkey args do not match the desktop-input contract"
                )
            modifiers, key = args["modifiers"], args["key"]
            if (
                not isinstance(modifiers, (list, tuple))
                or any(type(item) is not str for item in modifiers)
                or len(modifiers) != len(set(modifiers))
                or any(item not in CANONICAL_HOTKEY_MODIFIERS for item in modifiers)
                or type(key) is not str
                or key not in CANONICAL_HOTKEY_KEYS
                or key in CANONICAL_HOTKEY_MODIFIERS
            ):
                raise AtomicDesktopControllerError(
                    "hotkey requires unique canonical modifiers and one primary key"
                )
            try:
                chord = HotkeyChord((
                    *tuple(sorted(
                        modifiers, key=CANONICAL_HOTKEY_MODIFIERS.index,
                    )),
                    key,
                ))
            except (TypeError, ValueError) as exc:
                raise AtomicDesktopControllerError(str(exc)) from exc
            return _PreparedInput(
                request=CommitAdmissionRequest(
                    request_id=envelope.decision_id,
                    frame=envelope.frame,
                    target=envelope.target,
                    route=CommitRoute.PHYSICAL,
                    input_kind=CommitInputKind.KEYBOARD,
                    screen_point=None,
                ),
                chord=chord,
            )
        expected_args = (
            {"image_x", "image_y", "direction", "notches"}
            if proposal.action == "scroll"
            else {"image_x", "image_y"}
        )
        if set(args) != expected_args:
            raise AtomicDesktopControllerError(
                f"{proposal.action} args do not match the submitted-image contract"
            )
        direction = args.pop("direction", None)
        notches = args.pop("notches", None)
        if proposal.action == "scroll" and (
            type(direction) is not str
            or direction not in {"up", "down"}
            or type(notches) is not int
            or not 1 <= notches <= 12
        ):
            raise AtomicDesktopControllerError(
                "scroll requires direction up/down and integer notches from 1 through 12"
            )
        amount = None
        if proposal.action == "scroll":
            amount = notches if direction == "up" else -notches
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
                input_kind=CommitInputKind.POINT,
                screen_point=point,
            )
        except ValueError as exc:
            raise AtomicDesktopControllerError(str(exc)) from exc
        return _PreparedInput(
            request=request,
            click_count=(
                None
                if proposal.action == "scroll"
                else ClickCount.DOUBLE
                if proposal.action == "double_click"
                else ClickCount.SINGLE
            ),
            scroll_amount=amount,
        )

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

    @staticmethod
    def _transition_without_post(
        envelope: ActorDecisionEnvelope,
        commit: CommitCallbackResult[RawActionReceipt],
    ) -> ControllerTransition:
        """Preserve a proven non-mutation when post observation cannot close.

        Missing post-action evidence is fatal once physical input may have
        begun.  It must not erase the stronger, independently known fact that
        an admission or RawDevice receipt proved no input was executed.
        """

        receipt = commit.value
        definitely_not_executed = bool(
            commit.admission.status is CommitAdmissionStatus.NOT_STARTED
            or (
                isinstance(receipt, RawActionReceipt)
                and receipt.outcome in {
                    MutationOutcome.NOT_STARTED,
                    MutationOutcome.NOT_APPLIED,
                }
            )
        )
        return ControllerTransition(
            outcome=(
                ControllerOutcome.REJECTED
                if definitely_not_executed
                else ControllerOutcome.UNKNOWN
            ),
            action_id=envelope.decision_id,
            action=envelope.proposal.action,
            input_completed=False if definitely_not_executed else None,
        )

    @staticmethod
    def _activation_identity_guard(
        envelope: TargetActivationEnvelope,
        observation: TargetActivationObservation,
    ) -> FinalTargetActivationGuardDecision:
        target = observation.activated_target
        candidate = envelope.candidate
        if target.hwnd != candidate.hwnd:
            return FinalTargetActivationGuardDecision.TARGET_ROOT_CHANGED
        if (
            target.process_id != candidate.process_id
            or target.process_identity != candidate.process_identity
        ):
            return FinalTargetActivationGuardDecision.TARGET_PROCESS_CHANGED
        return FinalTargetActivationGuardDecision.ADMITTED

    @staticmethod
    def _activation_failure(
        envelope: TargetActivationEnvelope,
        *,
        unknown: bool,
        reason: str,
    ) -> TargetActivationTransition:
        return TargetActivationTransition(
            outcome=(
                TargetActivationOutcome.UNKNOWN
                if unknown
                else TargetActivationOutcome.REJECTED
            ),
            action_id=envelope.decision_id,
            candidate_ref=envelope.candidate.candidate_ref,
            mutation_completed=None if unknown else False,
            activated_target=None,
            post_frame_id=None,
            evidence_refs=(),
            failure_reason=reason,
        )

    @staticmethod
    def _activation_completed(
        envelope: TargetActivationEnvelope,
        post: TargetActivationPostAction,
        *,
        mutation_completed: bool,
    ) -> TargetActivationTransition:
        return TargetActivationTransition(
            outcome=TargetActivationOutcome.COMPLETED,
            action_id=envelope.decision_id,
            candidate_ref=envelope.candidate.candidate_ref,
            mutation_completed=mutation_completed,
            activated_target=post.activated_target,
            post_frame_id=post.post_frame_id,
            evidence_refs=post.evidence_refs,
            failure_reason="",
        )

    @staticmethod
    def _launch_transition(
        envelope: TargetLaunchEnvelope,
        *,
        request_count: int | None,
        reason: str,
        receipt: RawTargetLaunchReceipt | None = None,
        post: TargetActivationPostAction | None = None,
    ) -> TargetLaunchTransition:
        completed = receipt is not None and post is not None
        return TargetLaunchTransition(
            outcome=(
                TargetLaunchOutcome.COMPLETED
                if completed
                else TargetLaunchOutcome.REJECTED
                if request_count == 0
                else TargetLaunchOutcome.UNKNOWN
            ),
            action_id=envelope.decision_id,
            candidate_ref=envelope.candidate.candidate_ref,
            launch_request_count=request_count,
            launch_process_id=None if receipt is None else receipt.process_id,
            launch_process_identity=(
                None if receipt is None else receipt.process_identity
            ),
            activated_target=None if post is None else post.activated_target,
            post_frame_id=None if post is None else post.post_frame_id,
            evidence_refs=() if post is None else post.evidence_refs,
            failure_reason="" if completed else reason,
        )

    def _launch_presence_decision(
        self,
        envelope: TargetLaunchEnvelope,
    ) -> FinalTargetLaunchGuardDecision:
        presence = self._launch_presence
        executable_path = envelope.candidate.launch_target
        assert presence is not None and executable_path is not None
        try:
            snapshot = presence.read(executable_path)
        except Exception:
            return FinalTargetLaunchGuardDecision.PROCESS_STATE_UNKNOWN
        if not isinstance(snapshot, ExecutablePresenceSnapshot):
            return FinalTargetLaunchGuardDecision.PROCESS_STATE_UNKNOWN
        if (
            ntpath.normcase(snapshot.executable_path).casefold()
            != ntpath.normcase(executable_path).casefold()
        ):
            return FinalTargetLaunchGuardDecision.AUTHORITY_CHANGED
        if snapshot.exact_instances:
            return FinalTargetLaunchGuardDecision.EXISTING_PROCESS
        if snapshot.unresolved_process_ids:
            return FinalTargetLaunchGuardDecision.PROCESS_STATE_UNKNOWN
        return FinalTargetLaunchGuardDecision.ADMITTED

    def _poll_launched_target(
        self,
        envelope: TargetLaunchEnvelope,
        receipt: RawTargetLaunchReceipt,
        *,
        stop_event: Event | None,
    ) -> tuple[TargetActivationObservation | None, str]:
        observation_port = self._launch_observation
        assert observation_port is not None
        for attempt in range(self._launch_poll_attempts):
            if stop_event is not None and stop_event.is_set():
                return None, "launch_poll_stop_requested"
            try:
                result = observation_port.observe_launch(
                    envelope,
                    raw_receipt=receipt,
                    selected_hwnd=None,
                )
            except Exception:
                return None, "launch_poll_source_unavailable"
            if not isinstance(result, TargetLaunchObservationResult):
                return None, "launch_poll_source_unavailable"
            if result.state is TargetLaunchObservationState.READY:
                observed = result.observation
                assert observed is not None
                target = observed.activated_target
                if (
                    not observed.exact_foreground
                    or target.process_id != receipt.process_id
                    or target.process_identity != receipt.process_identity
                ):
                    return None, "launch_poll_identity_unproved"
                return observed, ""
            if result.state is not TargetLaunchObservationState.NOT_READY:
                return None, f"launch_poll_{result.state.value}"
            if attempt + 1 == self._launch_poll_attempts:
                break
            if stop_event is not None:
                if stop_event.wait(self._launch_poll_interval_s):
                    return None, "launch_poll_stop_requested"
            elif self._launch_poll_interval_s > 0.0:
                try:
                    self._launch_poll_wait(self._launch_poll_interval_s)
                except Exception:
                    return None, "launch_poll_wait_failed"
        return None, "launch_poll_timeout"

    def _run_acquisition_transaction(
        self,
        envelope: MutationEnvelope,
        *,
        target_hwnd: int | None, stop_event: Event | None,
        under_admission: Callable[[RuntimeMutationPermit], object],
        frame_changed: Callable[[], _AcquisitionResult],
        after_pin: Callable[[object], _AcquisitionResult],
    ) -> _AcquisitionResult:
        """Own one queue/pin/permit/post target-acquisition transaction."""
        def under_physical_lease() -> _AcquisitionResult:
            def under_frame_pin() -> object:
                permit = self._permits.begin_admission(envelope)
                try:
                    return under_admission(permit)
                finally:
                    if permit.allowed:
                        self._permits.finish_admission(envelope.decision_id)

            pinned, attempt = self._frames.run_if_current(
                envelope.frame, under_frame_pin,
            )
            if not pinned:
                return frame_changed()
            return after_pin(attempt)

        return self._queue.run(
            envelope.proposal.action,
            under_physical_lease,
            target_hwnd=target_hwnd,
            scope="physical",
            cancel_event=stop_event,
            metadata={"owner": "vnext_atomic_desktop_controller"},
        )

    def _execute_target_activation(
        self,
        envelope: TargetActivationEnvelope,
        *,
        stop_event: Event | None,
    ) -> TargetActivationTransition:
        observation_port = self._activation_observation
        post_port = self._activation_post
        if observation_port is None or post_port is None:
            raise AtomicDesktopControllerError(
                "target activation ports are not configured"
            )
        activation_expected_target: ActivatedTargetIdentity | None = None

        def read_current() -> tuple[
            FinalTargetActivationGuardDecision,
            TargetActivationObservation | None,
        ]:
            if stop_event is not None and stop_event.is_set():
                return FinalTargetActivationGuardDecision.STOP_REQUESTED, None
            try:
                observed = observation_port.observe_activation(envelope)
            except Exception:
                return FinalTargetActivationGuardDecision.TARGET_UNAVAILABLE, None
            if not isinstance(observed, TargetActivationObservation):
                return FinalTargetActivationGuardDecision.TARGET_UNAVAILABLE, None
            decision = self._activation_identity_guard(envelope, observed)
            if decision is not FinalTargetActivationGuardDecision.ADMITTED:
                return decision, observed
            expected = activation_expected_target
            if expected is not None:
                current = observed.activated_target
                if current.root_hwnd != expected.root_hwnd:
                    return (
                        FinalTargetActivationGuardDecision.TARGET_ROOT_CHANGED,
                        observed,
                    )
                if current.window_region != expected.window_region:
                    return (
                        FinalTargetActivationGuardDecision.AUTHORITY_CHANGED,
                        observed,
                    )
            if stop_event is not None and stop_event.is_set():
                return FinalTargetActivationGuardDecision.STOP_REQUESTED, observed
            if not self._permits.active_activation_admission_matches(
                envelope.decision_id,
                frame=envelope.frame,
                pointer=envelope.pointer,
                candidate_fingerprint=envelope.candidate_fingerprint,
                candidate=envelope.candidate,
            ):
                return FinalTargetActivationGuardDecision.AUTHORITY_CHANGED, observed
            return FinalTargetActivationGuardDecision.ADMITTED, observed

        def final_activation_guard() -> FinalTargetActivationGuardDecision:
            decision, _observation = read_current()
            return decision

        def under_admission(
            permit: RuntimeMutationPermit,
        ) -> _TargetAcquisitionAttempt:
            if not isinstance(permit, RuntimeMutationPermit) or not permit.allowed:
                reason = getattr(permit, "reason", None)
                value = getattr(reason, "value", "invalid")
                return _TargetAcquisitionAttempt(
                    failure_reason=f"mutation_permit_{value}"
                )
            if envelope.candidate.state is not TargetState.VISIBLE:
                return _TargetAcquisitionAttempt(
                    failure_reason="candidate_requires_recovery"
                )
            decision, observed = read_current()
            if decision is not FinalTargetActivationGuardDecision.ADMITTED:
                return _TargetAcquisitionAttempt(
                    failure_reason=decision.value
                )
            assert observed is not None
            nonlocal activation_expected_target
            activation_expected_target = observed.activated_target
            if observed.exact_foreground:
                # A zero-mutation completion still requires a final strong
                # identity/foreground read under active authority.
                decision, observed = read_current()
                if decision is not FinalTargetActivationGuardDecision.ADMITTED:
                    return _TargetAcquisitionAttempt(
                        failure_reason=decision.value
                    )
                assert observed is not None
                if observed.exact_foreground:
                    return _TargetAcquisitionAttempt(
                        already_foreground=True
                    )

            candidate = envelope.candidate
            assert candidate.hwnd is not None
            assert candidate.process_id is not None
            assert candidate.process_identity is not None
            context = RawTargetActivationContext(
                target_hwnd=candidate.hwnd,
                target_root_hwnd=activation_expected_target.root_hwnd,
                target_process_id=candidate.process_id,
                target_process_identity=candidate.process_identity,
            )
            receipt = self._raw.activate_target(
                context,
                final_guard=final_activation_guard,
            )
            return _TargetAcquisitionAttempt(raw_receipt=receipt)

        def after_pin(attempt: object) -> TargetActivationTransition:
            assert isinstance(attempt, _TargetAcquisitionAttempt)
            if attempt.failure_reason:
                return self._activation_failure(
                    envelope,
                    unknown=False,
                    reason=attempt.failure_reason,
                )

            receipt = attempt.raw_receipt
            if not attempt.already_foreground:
                if not isinstance(receipt, RawActionReceipt):
                    return self._activation_failure(
                        envelope,
                        unknown=True,
                        reason="raw_activation_result_unavailable",
                    )
                if receipt.outcome is MutationOutcome.OUTCOME_UNKNOWN:
                    return self._activation_failure(
                        envelope,
                        unknown=True,
                        reason=receipt.error_kind or "activation_outcome_unknown",
                    )
                if receipt.outcome is not MutationOutcome.APPLIED:
                    return self._activation_failure(
                        envelope,
                        unknown=False,
                        reason=receipt.error_kind or "activation_not_applied",
                    )

            try:
                post = post_port.observe_after_activation(
                    envelope,
                    raw_receipt=receipt,
                )
                if not isinstance(post, TargetActivationPostAction):
                    raise TypeError(
                        "activation post_action returned an invalid result"
                    )
                post_guard = self._activation_identity_guard(
                    envelope,
                    TargetActivationObservation(
                        post.activated_target,
                        post.exact_foreground,
                    ),
                )
                if (
                    post_guard is not FinalTargetActivationGuardDecision.ADMITTED
                    or activation_expected_target is None
                    or post.activated_target.root_hwnd
                    != activation_expected_target.root_hwnd
                    or post.activated_target.window_region
                    != activation_expected_target.window_region
                    or not post.exact_foreground
                ):
                    raise AtomicDesktopControllerError(
                        "activation post-action target is not exact foreground"
                    )
            except Exception:
                return self._activation_failure(
                    envelope,
                    unknown=not attempt.already_foreground,
                    reason="activation_post_observation_failed",
                )
            return self._activation_completed(
                envelope,
                post,
                mutation_completed=not attempt.already_foreground,
            )

        candidate_hwnd = envelope.candidate.hwnd
        assert candidate_hwnd is not None
        return self._run_acquisition_transaction(
            envelope,
            target_hwnd=candidate_hwnd,
            stop_event=stop_event,
            under_admission=under_admission,
            frame_changed=lambda: self._activation_failure(
                envelope,
                unknown=False,
                reason="frame_changed",
            ),
            after_pin=after_pin,
        )

    def _execute_target_launch(
        self,
        envelope: TargetLaunchEnvelope,
        *,
        stop_event: Event | None,
    ) -> TargetLaunchTransition:
        presence = self._launch_presence
        observation_port = self._launch_observation
        post_port = self._launch_post
        if presence is None or observation_port is None or post_port is None:
            raise AtomicDesktopControllerError(
                "target launch ports are not configured"
            )
        admission_started = False

        def authority_current() -> bool:
            return self._permits.active_launch_admission_matches(
                envelope.decision_id,
                frame=envelope.frame,
                pointer=envelope.pointer,
                candidate_fingerprint=envelope.candidate_fingerprint,
                candidate=envelope.candidate,
            )

        def final_launch_guard() -> FinalTargetLaunchGuardDecision:
            if stop_event is not None and stop_event.is_set():
                return FinalTargetLaunchGuardDecision.STOP_REQUESTED
            if not authority_current():
                return FinalTargetLaunchGuardDecision.AUTHORITY_CHANGED
            decision = self._launch_presence_decision(envelope)
            if decision is not FinalTargetLaunchGuardDecision.ADMITTED:
                return decision
            if stop_event is not None and stop_event.is_set():
                return FinalTargetLaunchGuardDecision.STOP_REQUESTED
            if not authority_current():
                return FinalTargetLaunchGuardDecision.AUTHORITY_CHANGED
            return FinalTargetLaunchGuardDecision.ADMITTED

        def under_admission(
            permit: RuntimeMutationPermit,
        ) -> _TargetAcquisitionAttempt:
            nonlocal admission_started
            admission_started = True
            if not isinstance(permit, RuntimeMutationPermit) or not permit.allowed:
                reason = getattr(permit, "reason", None)
                value = getattr(reason, "value", "invalid")
                return _TargetAcquisitionAttempt(
                    failure_reason=f"mutation_permit_{value}"
                )
            if stop_event is not None and stop_event.is_set():
                return _TargetAcquisitionAttempt(failure_reason="stop_requested")
            decision = self._launch_presence_decision(envelope)
            if decision is not FinalTargetLaunchGuardDecision.ADMITTED:
                return _TargetAcquisitionAttempt(failure_reason=decision.value)
            executable_path = envelope.candidate.launch_target
            assert executable_path is not None
            receipt = self._raw.launch_target(
                RawTargetLaunchContext(executable_path),
                final_guard=final_launch_guard,
            )
            return _TargetAcquisitionAttempt(raw_receipt=receipt)

        def after_pin(attempt: object) -> TargetLaunchTransition:
            assert isinstance(attempt, _TargetAcquisitionAttempt)
            if attempt.failure_reason:
                return self._launch_transition(
                    envelope,
                    request_count=0,
                    reason=attempt.failure_reason,
                )
            receipt = attempt.raw_receipt
            if not isinstance(receipt, RawTargetLaunchReceipt):
                return self._launch_transition(
                    envelope,
                    request_count=None,
                    reason="raw_launch_result_unavailable",
                )
            if receipt.outcome is MutationOutcome.NOT_STARTED:
                return self._launch_transition(
                    envelope,
                    request_count=0,
                    reason=receipt.error_kind or "launch_not_started",
                )
            if receipt.outcome is MutationOutcome.OUTCOME_UNKNOWN:
                return self._launch_transition(
                    envelope,
                    request_count=receipt.launch_request_count,
                    reason=receipt.error_kind or "launch_outcome_unknown",
                )
            assert receipt.outcome is MutationOutcome.APPLIED

            observed, failure_reason = self._poll_launched_target(
                envelope,
                receipt,
                stop_event=stop_event,
            )
            if observed is None:
                return self._launch_transition(
                    envelope,
                    request_count=1,
                    reason=failure_reason,
                )
            selected = observed.activated_target
            try:
                post = post_port.observe_after_launch(
                    envelope,
                    raw_receipt=receipt,
                    selected_target=selected,
                )
                if not isinstance(post, TargetActivationPostAction):
                    raise TypeError(
                        "launch post_action returned an invalid result"
                    )
                actual = post.activated_target
                actual_key = (
                    actual.hwnd, actual.root_hwnd, actual.process_id,
                    actual.process_identity, actual.window_region,
                )
                selected_key = (
                    selected.hwnd, selected.root_hwnd, selected.process_id,
                    selected.process_identity, selected.window_region,
                )
                if (
                    not post.exact_foreground
                    or actual_key != selected_key
                ):
                    raise AtomicDesktopControllerError(
                        "launch post-action target changed"
                    )
            except Exception:
                return self._launch_transition(
                    envelope,
                    request_count=1,
                    reason="launch_post_observation_failed",
                )
            return self._launch_transition(
                envelope,
                request_count=1,
                reason="",
                receipt=receipt,
                post=post,
            )

        try:
            return self._run_acquisition_transaction(
                envelope,
                target_hwnd=None,
                stop_event=stop_event,
                under_admission=under_admission,
                frame_changed=lambda: self._launch_transition(
                    envelope,
                    request_count=0,
                    reason="frame_changed",
                ),
                after_pin=after_pin,
            )
        except DesktopActionCanceled:
            if admission_started:
                raise
            return self._launch_transition(
                envelope,
                request_count=0,
                reason="desktop_action_canceled",
            )

    def execute(
        self,
        envelope: (
            ActorDecisionEnvelope
            | TargetActivationEnvelope
            | TargetLaunchEnvelope
        ),
        *,
        stop_event: Event | None = None,
    ) -> ControllerTransition | TargetActivationTransition | TargetLaunchTransition:
        if not self._enabled:
            raise AtomicDesktopControllerError("AtomicDesktopController is disabled")
        if isinstance(envelope, TargetLaunchEnvelope):
            if stop_event is not None and not isinstance(stop_event, Event):
                raise TypeError("stop_event must be threading.Event or null")
            return self._execute_target_launch(
                envelope,
                stop_event=stop_event,
            )
        if isinstance(envelope, TargetActivationEnvelope):
            if stop_event is not None and not isinstance(stop_event, Event):
                raise TypeError("stop_event must be threading.Event or null")
            return self._execute_target_activation(
                envelope,
                stop_event=stop_event,
            )
        if not isinstance(envelope, ActorDecisionEnvelope):
            raise TypeError("envelope must be a supported Controller envelope")
        if stop_event is not None and not isinstance(stop_event, Event):
            raise TypeError("stop_event must be threading.Event or null")
        prepared = self._request(envelope)
        request = prepared.request
        target = envelope.target
        assert target is not None  # established by _request

        def final_guard(x: int, y: int) -> FinalPointerGuardDecision:
            if stop_event is not None and stop_event.is_set():
                return FinalPointerGuardDecision.STOP_REQUESTED
            decision = self._final_guard.check_before_pointer_input(request, (x, y))
            if stop_event is not None and stop_event.is_set():
                return FinalPointerGuardDecision.STOP_REQUESTED
            return decision

        def final_keyboard_guard() -> FinalKeyboardGuardDecision:
            if stop_event is not None and stop_event.is_set():
                return FinalKeyboardGuardDecision.STOP_REQUESTED
            decision = self._final_guard.check_before_keyboard_input(request)
            if stop_event is not None and stop_event.is_set():
                return FinalKeyboardGuardDecision.STOP_REQUESTED
            return decision

        def under_physical_lease() -> ControllerTransition:
            def under_frame_pin() -> CommitCallbackResult[RawActionReceipt]:
                permit = self._permits.begin_admission(envelope)
                try:
                    def execute_raw() -> RawActionReceipt:
                        context = RawActionContext(target.hwnd, target.hwnd)
                        if prepared.text is not None:
                            return self._raw.type_text(
                                context,
                                prepared.text,
                                final_guard=final_keyboard_guard,
                            )
                        if prepared.chord is not None:
                            return self._raw.hotkey(
                                context,
                                prepared.chord,
                                final_guard=final_keyboard_guard,
                            )
                        assert request.screen_point is not None
                        if prepared.scroll_amount is not None:
                            return self._raw.scroll(
                                context,
                                request.screen_point[0],
                                request.screen_point[1],
                                prepared.scroll_amount,
                                final_guard=final_guard,
                            )
                        assert prepared.click_count is not None
                        return self._raw.click(
                            context,
                            request.screen_point[0],
                            request.screen_point[1],
                            MouseButton.LEFT,
                            prepared.click_count,
                            ClickMechanism.PHYSICAL,
                            final_guard=final_guard,
                        )

                    return self._permit_result(
                        request,
                        permit,
                        self._gate,
                        execute_raw,
                    )
                finally:
                    if permit.allowed:
                        self._permits.finish_admission(envelope.decision_id)

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
                # A separately proven non-execution remains a rejection; the
                # Runtime will discard that decision and observe afresh.
                return self._transition_without_post(envelope, commit)
            return self._transition(envelope, commit, post)

        return self._queue.run(
            envelope.proposal.action,
            under_physical_lease,
            target_hwnd=target.hwnd,
            scope="physical",
            cancel_event=stop_event,
            metadata={"owner": "vnext_atomic_desktop_controller"},
        )


__all__ = [
    "AtomicDesktopController",
    "AtomicDesktopControllerError",
    "AdmittedRawDeviceInventoryPort",
    "ControllerPostAction",
    "ControllerPostActionPort",
    "DesktopCommitObservation",
    "PhysicalInputFinalGuardPort",
    "REQUIRED_RAW_ACTION_NAMES",
    "SUPPORTED_DESKTOP_TOOL_NAMES",
    "TargetActivationObservation",
    "TargetActivationObservationPort",
    "TargetActivationPostAction",
    "TargetActivationPostActionPort",
    "TargetLaunchObservationPort",
    "TargetLaunchObservationResult",
    "TargetLaunchObservationState",
    "TargetLaunchPostActionPort",
    "TargetLaunchPresencePort",
]
