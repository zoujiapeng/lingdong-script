"""One atomic bridge from a bound Actor decision to admitted physical input.

This module is deliberately the only vNext owner allowed to cross from the
pure Actor/Runtime contracts into the existing live desktop primitives.  It
does not discover, select, activate, launch, or recover a target.  The target
and initial frame must already be bound by their existing authorities.
"""
from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import math
import ntpath
import threading
import time
from types import MappingProxyType
from typing import Any, Protocol

from ...action.desktop_queue import (
    DESKTOP_ACTION_QUEUE,
    DesktopActionCanceled,
)
from ...action.raw_device import (
    ClickCount,
    ClickMechanism,
    FinalKeyboardGuardDecision,
    FinalPointerGuardDecision,
    HotkeyChord,
    MAX_TYPE_TEXT_CODEPOINTS,
    MouseButton,
    RawActionContext,
    RawActionReceipt,
    RawDeviceExecutor,
)
from ...action.replay_policy import MutationOutcome
from ...utils.screenshot import Region
from ...visual_runtime.frame_authority import FrameAuthority, FrameState
from ...visual_runtime.frame_observer import FrameObserver
from ...visual_runtime.normalized_frame_coordinates import (
    NormalizedFrameCoordinateMapper,
)
from ...visual_runtime.submitted_image_coordinates import (
    SubmittedImageCoordinateMapper,
)
from ..actor import ActorDecisionEnvelope, DesktopActionProposal
from ..capabilities import (
    PointerDisposition,
    PointerLeaseState,
)
from ..evidence_plane import (
    FrameArtifactReference,
    FrameArtifactRole,
    FrameArtifactWriter,
    RuntimeEvidencePlane,
)
from ..mission import EvidenceRef, TargetBinding
from ..runtime import (
    ControllerOutcome,
    ControllerTransition,
    MutationPermitAuthority,
    RuntimeCommitIdentity,
    RuntimeFrame,
    RuntimeFrameIdentity,
    RuntimeTargetIdentity,
)
from ..verification import (
    ActionEffectVerificationRequest,
    DeterministicActionEffectVerifier,
)


_ACTIONS = frozenset(
    {"click", "double_click", "drag", "scroll", "type_text", "hotkey"}
)
_POST_FRAME_KIND = "post_action_frame"
_JPEG_MEDIA_TYPE = "image/jpeg"
_MAX_SCROLL_AMOUNT = 1200
_MAX_DRAG_INTERMEDIATE_POINTS = 32
# One spatial turn can make an Actor call followed by a Grounder call.  Keep
# the emergency lease longer than their combined production timeout; normal
# release is the next drag segment or session.close(), not this fallback.
_DEFAULT_HELD_POINTER_TIMEOUT_S = 300.0

PhysicalRegion = tuple[int, int, int, int]
PhysicalPoint = tuple[int, int]
TraceScalar = bool | int | float | str | None


@dataclass(frozen=True, slots=True)
class LiveTargetSnapshot:
    """One read-only observation of the already-bound desktop target."""

    hwnd: int
    root_hwnd: int
    process_id: int
    executable_identity: str
    region: PhysicalRegion
    foreground_root_hwnd: int | None

    def __post_init__(self) -> None:
        for name in ("hwnd", "root_hwnd", "process_id"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (
            type(self.executable_identity) is not str
            or not self.executable_identity
        ):
            raise ValueError("executable_identity must be non-empty")
        region = tuple(self.region)
        if (
            len(region) != 4
            or any(type(value) is not int for value in region)
            or region[2] <= 0
            or region[3] <= 0
        ):
            raise ValueError(
                "region must be (left, top, positive width, positive height)"
            )
        foreground = self.foreground_root_hwnd
        if foreground is not None and (
            type(foreground) is not int or foreground <= 0
        ):
            raise ValueError(
                "foreground_root_hwnd must be positive or null"
            )
        object.__setattr__(self, "region", region)


class DesktopTargetProbe(Protocol):
    """Read current mechanical facts without acquiring or changing a target."""

    def snapshot(self, hwnd: int) -> LiveTargetSnapshot | None: ...

    def hit_test_root(self, x: int, y: int) -> int | None: ...


class DesktopWorkspacePort(DesktopTargetProbe, Protocol):
    """Mutable target owner; adoption is allowed only between actions."""

    @property
    def binding(self) -> TargetBinding: ...

    def finish_action(
        self,
        *,
        action_id: str,
        action: str,
        input_completed: bool | None,
    ) -> None: ...


class RawReceiptTraceSink(Protocol):
    """Best-effort sink for the complete immutable RawDevice receipt."""

    def record(
        self,
        receipt: RawActionReceipt,
        *,
        metadata: Mapping[str, TraceScalar],
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class _PreparedAction:
    action: str
    point: PhysicalPoint | None = None
    end_point: PhysicalPoint | None = None
    points: tuple[PhysicalPoint, ...] | None = None
    keep_holding: bool = False
    hold_control_present: bool = False
    started_held: bool = False
    pointer_lease_id: str | None = None
    amount: int | None = None
    text: str | None = None
    chord: HotkeyChord | None = None


@dataclass(frozen=True, slots=True)
class _PinnedExecution:
    before_frame: RuntimeFrame | None
    receipt: RawActionReceipt
    raw_device_called: bool = False


@dataclass(frozen=True, slots=True)
class _HeldPointerLease:
    lease_id: str
    cursor: PhysicalPoint
    expires_at: float


class AtomicDesktopAdapter:
    """Execute exactly one admitted desktop proposal through existing owners."""

    def __init__(
        self,
        *,
        frame_authority: FrameAuthority,
        frame_observer: FrameObserver,
        frozen_target_binding: TargetBinding,
        target_probe: DesktopTargetProbe,
        desktop_workspace: DesktopWorkspacePort | None = None,
        mutation_permits: MutationPermitAuthority,
        raw_device: RawDeviceExecutor,
        receipt_trace_sink: RawReceiptTraceSink | None = None,
        runtime_evidence_plane: RuntimeEvidencePlane | None = None,
        frame_artifact_writer: FrameArtifactWriter | None = None,
        post_input_settle_delay_s: float = 0.12,
        wait: Callable[[float], Any] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        held_pointer_timeout_s: float = _DEFAULT_HELD_POINTER_TIMEOUT_S,
    ) -> None:
        if not isinstance(frame_authority, FrameAuthority):
            raise TypeError("frame_authority must be FrameAuthority")
        if not isinstance(frame_observer, FrameObserver):
            raise TypeError("frame_observer must be FrameObserver")
        if getattr(frame_observer, "_authority", None) is not frame_authority:
            raise TypeError(
                "FrameObserver and AtomicDesktopAdapter must share FrameAuthority"
            )
        if type(frozen_target_binding) is not TargetBinding:
            raise TypeError(
                "frozen_target_binding must be canonical TargetBinding"
            )
        frozen_target_binding = TargetBinding.from_dict(
            frozen_target_binding.to_dict()
        )
        probe: DesktopTargetProbe = (
            desktop_workspace
            if desktop_workspace is not None
            else target_probe
        )
        if not callable(getattr(probe, "snapshot", None)):
            raise TypeError("target_probe must provide snapshot()")
        if not callable(getattr(probe, "hit_test_root", None)):
            raise TypeError("target_probe must provide hit_test_root()")
        if desktop_workspace is not None:
            workspace_binding = getattr(
                desktop_workspace,
                "binding",
                None,
            )
            if (
                type(workspace_binding) is not TargetBinding
                or workspace_binding != frozen_target_binding
                or not callable(
                    getattr(desktop_workspace, "finish_action", None)
                )
            ):
                raise TypeError(
                    "desktop_workspace must own the initial target binding"
                )
        if type(mutation_permits) is not MutationPermitAuthority:
            raise TypeError(
                "mutation_permits must be MutationPermitAuthority"
            )
        if type(raw_device) is not RawDeviceExecutor:
            raise TypeError("raw_device must be RawDeviceExecutor")
        if set(raw_device.admitted_action_names) != {
            "click",
            "drag",
            "scroll",
            "type_text",
            "hotkey",
        }:
            raise ValueError(
                "RawDevice must expose exactly the five admitted physical primitives"
            )
        if receipt_trace_sink is not None and not callable(
            getattr(receipt_trace_sink, "record", None)
        ):
            raise TypeError("receipt_trace_sink must provide record()")
        if (
            runtime_evidence_plane is not None
            and type(runtime_evidence_plane) is not RuntimeEvidencePlane
        ):
            raise TypeError(
                "runtime_evidence_plane must be RuntimeEvidencePlane or null"
            )
        if (
            frame_artifact_writer is not None
            and type(frame_artifact_writer) is not FrameArtifactWriter
        ):
            raise TypeError(
                "frame_artifact_writer must be FrameArtifactWriter or null"
            )
        durable = bool(
            runtime_evidence_plane is not None
            and runtime_evidence_plane.durable
        )
        if durable != (frame_artifact_writer is not None):
            raise TypeError(
                "durable Runtime evidence requires one FrameArtifactWriter"
            )
        if durable:
            assert runtime_evidence_plane is not None
            assert frame_artifact_writer is not None
            journal = runtime_evidence_plane.journal
            assert journal is not None
            if (
                frame_artifact_writer.run_directory
                != journal.run_directory
                or not frame_artifact_writer.writable
            ):
                raise TypeError(
                    "journal and artifact writer must share one writable run"
                )
        if (
            isinstance(post_input_settle_delay_s, bool)
            or not isinstance(post_input_settle_delay_s, (int, float))
        ):
            raise TypeError(
                "post_input_settle_delay_s must be a finite number"
            )
        settle_delay = float(post_input_settle_delay_s)
        if (
            not math.isfinite(settle_delay)
            or settle_delay < 0.0
            or settle_delay > 1.0
        ):
            raise ValueError(
                "post_input_settle_delay_s must be in the range 0..1"
            )
        if not callable(wait):
            raise TypeError("wait must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if (
            isinstance(held_pointer_timeout_s, bool)
            or not isinstance(held_pointer_timeout_s, (int, float))
            or not math.isfinite(float(held_pointer_timeout_s))
            or not 1.0 <= float(held_pointer_timeout_s) <= 300.0
        ):
            raise ValueError(
                "held_pointer_timeout_s must be in the range 1..300"
            )
        if (
            mutation_permits.mission_id == ""
            or mutation_permits.run_id == ""
        ):
            raise ValueError("mutation permit scope must be non-empty")
        if runtime_evidence_plane is not None and (
            runtime_evidence_plane.mission_id
            != mutation_permits.mission_id
            or runtime_evidence_plane.run_id
            != mutation_permits.run_id
        ):
            raise ValueError(
                "Runtime evidence plane and mutation permit scope differ"
            )

        self._authority = frame_authority
        self._observer = frame_observer
        self._fixed_binding = frozen_target_binding
        self._workspace = desktop_workspace
        self._probe = probe
        self._permits = mutation_permits
        self._raw = raw_device
        self._trace = receipt_trace_sink
        self._runtime_evidence = runtime_evidence_plane
        self._frame_artifacts = frame_artifact_writer
        self._durable_evidence_enabled = durable
        self._post_input_settle_delay_s = settle_delay
        self._wait = wait
        self._clock = clock
        self._held_pointer_timeout_s = float(held_pointer_timeout_s)
        self._pointer_lock = threading.RLock()
        self._held_pointer: _HeldPointerLease | None = None
        self._pointer_uncertain = False
        self._closed = False
        self._effect = DeterministicActionEffectVerifier()

    @property
    def mutation_permits(self) -> MutationPermitAuthority:
        """Expose identity only so composition can prove one shared authority."""

        return self._permits

    @property
    def _binding(self) -> TargetBinding:
        workspace = self._workspace
        return (
            self._fixed_binding
            if workspace is None
            else workspace.binding
        )

    @property
    def frozen_target_binding(self) -> TargetBinding:
        return self._binding

    @property
    def runtime_evidence_plane(self) -> RuntimeEvidencePlane | None:
        return self._runtime_evidence

    def pointer_state(self) -> PointerLeaseState:
        with self._pointer_lock:
            lease = self._held_pointer
            if (
                lease is not None
                and not self._pointer_uncertain
                and self._clock() >= lease.expires_at
            ):
                self._release_held_pointer_locked()
                lease = self._held_pointer
            if lease is None:
                return PointerLeaseState(PointerDisposition.RELEASED)
            if self._pointer_uncertain:
                return PointerLeaseState(
                    PointerDisposition.UNKNOWN,
                    lease.lease_id,
                    False,
                )
            return PointerLeaseState(
                PointerDisposition.HELD,
                lease.lease_id,
                True,
            )

    def close(self) -> None:
        with self._pointer_lock:
            if self._closed:
                return
            released = self._release_held_pointer_locked()
            if not released:
                # One bounded retry handles a transient backend uncertainty
                # without turning close into an unbounded input loop.
                released = self._release_held_pointer_locked()
            if released:
                self._closed = True
        if not released:
            raise RuntimeError("held pointer cleanup could not be confirmed")

    def execute(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        stop_event: threading.Event | None = None,
    ) -> ControllerTransition:
        with self._pointer_lock:
            if self._closed:
                raise RuntimeError("atomic desktop adapter is closed")
            return self._execute_open(
                envelope,
                stop_event=stop_event,
            )

    def _execute_open(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        stop_event: threading.Event | None,
    ) -> ControllerTransition:
        transition: ControllerTransition | None = None
        try:
            transition = self._execute_once(
                envelope,
                stop_event=stop_event,
            )
        except Exception:
            self._release_held_pointer()
            raise
        preserves_hold = bool(
            transition.outcome is ControllerOutcome.COMPLETED
            and transition.input_completed
            and type(envelope.proposal) is DesktopActionProposal
            and envelope.proposal.action == "drag"
            and envelope.proposal.args.get("keep_holding") is True
        )
        if not preserves_hold and not self._release_held_pointer():
            return self._unknown_transition(
                envelope,
                transition.action,
                input_completed=transition.input_completed,
            )
        return transition

    def _execute_once(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        stop_event: threading.Event | None = None,
    ) -> ControllerTransition:
        """Execute one envelope once; no branch in this method replays input."""

        if type(envelope) is not ActorDecisionEnvelope:
            raise TypeError(
                "AtomicDesktopAdapter requires ActorDecisionEnvelope"
            )
        envelope = ActorDecisionEnvelope.from_dict(envelope.to_dict())
        proposal = envelope.proposal
        if type(proposal) is not DesktopActionProposal:
            raise ValueError(
                "AtomicDesktopAdapter accepts only desktop_action proposals"
            )
        action = proposal.action
        if action not in _ACTIONS:
            raise ValueError("desktop action is outside the atomic surface")

        try:
            prepared = self._prepare_action(envelope, proposal)
        except Exception:
            receipt = self._not_started(action, "invalid_atomic_action")
            self._record_receipt(
                receipt,
                envelope,
                phase="prepare",
            )
            return self._rejected(envelope, action)

        if not self._envelope_matches_frozen_target(envelope):
            receipt = self._not_started(action, "target_binding_mismatch")
            self._record_receipt(
                receipt,
                envelope,
                phase="binding",
            )
            return self._rejected(envelope, action)

        try:
            matched, pinned = self._authority.run_if_current(
                envelope,
                lambda: self._execute_pinned(
                    envelope,
                    prepared,
                    stop_event=stop_event,
                ),
            )
        except Exception:
            receipt = self._unknown(action, "atomic_callback_exception")
            self._record_receipt(
                receipt,
                envelope,
                phase="authority_callback",
            )
            return self._unknown_transition(
                envelope,
                action,
                input_completed=None,
            )

        if not matched or pinned is None:
            receipt = self._not_started(action, "stale_authoritative_frame")
            self._record_receipt(
                receipt,
                envelope,
                phase="frame_pin",
            )
            return self._rejected(envelope, action)

        receipt = pinned.receipt
        self._record_trace_receipt(receipt, envelope, phase="raw_device")
        if not pinned.raw_device_called:
            evidence_recorded = self._record_evidence_receipt(
                receipt,
                envelope,
            )
            if receipt.input_completed is False:
                return self._rejected(envelope, action)
            return self._unknown_transition(
                envelope,
                action,
                input_completed=(
                    receipt.input_completed
                    if evidence_recorded
                    else None
                ),
            )
        if receipt.input_completed is False:
            if self._record_evidence_receipt(receipt, envelope):
                return self._rejected(envelope, action)
            return self._unknown_transition(
                envelope,
                action,
                input_completed=False,
            )
        settle_failed = False
        if (
            receipt.input_completed is True
            and self._post_input_settle_delay_s > 0.0
        ):
            try:
                self._wait(self._post_input_settle_delay_s)
            except Exception:
                settle_failed = True
        workspace = self._workspace
        if workspace is not None:
            try:
                workspace.finish_action(
                    action_id=envelope.decision_id,
                    action=action,
                    input_completed=receipt.input_completed,
                )
            except Exception:
                # Input has already reached the OS.  Workspace discovery is
                # recovery metadata for the next observation, not authority
                # to rewrite the completed receipt or replay the action.
                pass
        if pinned.before_frame is None:
            self._record_evidence_receipt(receipt, envelope)
            return self._unknown_transition(
                envelope,
                action,
                input_completed=receipt.input_completed,
            )

        post_frame: RuntimeFrame | None = None
        post_evidence: EvidenceRef | None = None
        try:
            post_frame, post_evidence = self._capture_post_action(
                envelope,
                prepared,
                pinned.before_frame,
            )
        except Exception:
            self._record_evidence_receipt(receipt, envelope)
            return self._unknown_transition(
                envelope,
                action,
                input_completed=receipt.input_completed,
            )

        if not self._record_evidence_receipt(receipt, envelope):
            return self._unknown_transition(
                envelope,
                action,
                input_completed=receipt.input_completed,
                post_frame=post_frame,
                post_evidence=post_evidence,
            )
        if settle_failed:
            return self._unknown_transition(
                envelope,
                action,
                input_completed=receipt.input_completed,
                post_frame=post_frame,
                post_evidence=post_evidence,
            )
        if receipt.input_completed is None:
            return self._unknown_transition(
                envelope,
                action,
                input_completed=None,
                post_frame=post_frame,
                post_evidence=post_evidence,
            )

        try:
            before_ref = pinned.before_frame.evidence_refs[0]
            effect_result = self._effect.verify(
                ActionEffectVerificationRequest(
                    mission_id=envelope.mission_id,
                    run_id=envelope.run_id,
                    obligation_id=envelope.obligation_id,
                    action_id=envelope.decision_id,
                    input_completed=True,
                    before_frame=pinned.before_frame,
                    after_frame=post_frame,
                    before_frame_evidence_ref=before_ref,
                    after_frame_evidence_ref=post_evidence.id,
                    before_frame_payload_hash=(
                        pinned.before_frame.identity.payload_hash
                    ),
                    after_frame_payload_hash=post_frame.identity.payload_hash,
                    target_binding_valid=True,
                )
            )
        except Exception:
            return self._unknown_transition(
                envelope,
                action,
                input_completed=True,
                post_frame=post_frame,
                post_evidence=post_evidence,
            )

        return ControllerTransition(
            outcome=ControllerOutcome.COMPLETED,
            action_id=envelope.decision_id,
            action=action,
            input_completed=True,
            visible_effect="",
            post_frame_evidence=(post_evidence,),
            post_frame_id=post_frame.frame_id,
            action_effect=effect_result.summary,
            action_effect_evidence=effect_result.evidence,
        )

    def _execute_pinned(
        self,
        envelope: ActorDecisionEnvelope,
        prepared: _PreparedAction,
        *,
        stop_event: threading.Event | None,
    ) -> _PinnedExecution:
        state = self._authority.current
        if state is None:
            return _PinnedExecution(
                None,
                self._not_started(prepared.action, "frame_unavailable"),
            )
        try:
            before_frame = self._runtime_frame(
                state,
                evidence_refs=envelope.allowed_evidence_scope,
            )
        except Exception:
            return _PinnedExecution(
                None,
                self._not_started(prepared.action, "invalid_pre_frame"),
            )

        initial = self._safe_snapshot()
        if initial is None:
            return _PinnedExecution(
                before_frame,
                self._not_started(prepared.action, "target_unavailable"),
            )
        if not self._target_is_current(
            initial,
            expected_region=before_frame.region,
            require_foreground=True,
        ):
            return _PinnedExecution(
                before_frame,
                self._not_started(prepared.action, "target_identity_changed"),
            )
        for point in self._prepared_points(prepared):
            if not self._point_hits_target(point):
                return _PinnedExecution(
                    before_frame,
                    self._not_started(prepared.action, "hit_test_changed"),
                )
        if stop_event is not None and stop_event.is_set():
            return _PinnedExecution(
                before_frame,
                self._not_started(prepared.action, "stop_requested"),
            )

        commit_identity = RuntimeCommitIdentity(
            before_frame.identity,
            RuntimeTargetIdentity.from_binding(self._binding),
        )
        admission = self._permits.begin_admission(
            envelope,
            current_commit_identity=commit_identity,
        )
        if not admission.allowed:
            return _PinnedExecution(
                before_frame,
                self._not_started(
                    prepared.action,
                    f"permit_{admission.reason.value}",
                ),
            )
        evidence_plane = self._runtime_evidence
        if evidence_plane is not None:
            try:
                if admission.permit_id is None:
                    raise ValueError(
                        "allowed admission is missing permit identity"
                    )
                self._persist_frame_artifact(
                    envelope,
                    before_frame,
                    role=FrameArtifactRole.BEFORE,
                    capture_timestamp_ns=self._seconds_to_ns(
                        state.captured_at
                    ),
                )
                evidence_plane.record_admission(
                    decision_id=envelope.decision_id,
                    mission_id=envelope.mission_id,
                    run_id=envelope.run_id,
                    frame_identity=commit_identity.frame.to_dict(),
                    target_identity=commit_identity.target.to_dict(),
                    mutation_permit_id=admission.permit_id,
                    controller_route="vnext_atomic_desktop_adapter",
                    actor_envelope_sha256=hashlib.sha256(
                        envelope.to_json().encode("utf-8")
                    ).hexdigest(),
                )
            except Exception:
                return _PinnedExecution(
                    before_frame,
                    self._not_started(
                        prepared.action,
                        "evidence_admission_rejected",
                    ),
                )

        raw_started = False

        def invoke_raw() -> RawActionReceipt:
            nonlocal raw_started
            revalidation_error = self._queued_admission_error(
                envelope,
                prepared,
                before_frame,
                stop_event=stop_event,
            )
            if revalidation_error is not None:
                return self._not_started(
                    prepared.action,
                    revalidation_error,
                )
            if evidence_plane is not None:
                try:
                    evidence_plane.reserve_physical_attempt(
                        decision_id=envelope.decision_id,
                        mission_id=envelope.mission_id,
                        run_id=envelope.run_id,
                        actor_envelope_sha256=hashlib.sha256(
                            envelope.to_json().encode("utf-8")
                        ).hexdigest(),
                        submitted_arguments=dict(
                            envelope.proposal.args
                        ),
                        physical_arguments=(
                            self._physical_attempt_arguments(prepared)
                        ),
                        raw_device_route=self._raw_device_route(
                            prepared.action
                        ),
                        mechanism="physical_sendinput",
                    )
                except Exception:
                    return self._not_started(
                        prepared.action,
                        "evidence_attempt_rejected",
                    )
            raw_started = True
            return self._run_raw(
                envelope,
                prepared,
                before_frame,
                initial,
                stop_event=stop_event,
            )

        try:
            receipt = DESKTOP_ACTION_QUEUE.run(
                prepared.action,
                invoke_raw,
                target_hwnd=self._binding.hwnd,
                scope="physical",
                metadata={
                    "owner": "vnext_atomic_desktop_adapter",
                    "mission_id": envelope.mission_id,
                    "run_id": envelope.run_id,
                    "obligation_id": envelope.obligation_id,
                    "decision_id": envelope.decision_id,
                    "frame_id": envelope.frame_id,
                    "frame_epoch": envelope.frame_epoch,
                    "frame_hwnd": envelope.frame_hwnd,
                    "target_binding_id": envelope.target_binding_id,
                },
                cancel_event=stop_event,
            )
        except DesktopActionCanceled:
            receipt = self._not_started(
                prepared.action,
                "desktop_queue_canceled",
            )
        except Exception:
            receipt = (
                self._unknown(prepared.action, "desktop_queue_exception")
                if raw_started
                else self._unknown(
                    prepared.action,
                    "desktop_queue_boundary_exception",
                )
            )
        return _PinnedExecution(before_frame, receipt, raw_started)

    def _queued_admission_error(
        self,
        envelope: ActorDecisionEnvelope,
        prepared: _PreparedAction,
        before_frame: RuntimeFrame,
        *,
        stop_event: threading.Event | None,
    ) -> str | None:
        """Recheck all commit facts after the global queue grants ownership."""

        if stop_event is not None and stop_event.is_set():
            return "stop_requested_after_queue"
        current = self._authority.current
        if current is None or not self._frame_state_matches(
            current,
            before_frame,
        ):
            return "frame_changed_after_queue"
        snapshot = self._safe_snapshot()
        if not self._target_is_current(
            snapshot,
            expected_region=before_frame.region,
            require_foreground=True,
        ):
            return "target_changed_after_queue"
        for point in self._prepared_points(prepared):
            if not self._inside(point, before_frame.region):
                return "point_outside_target_after_queue"
            if not self._point_hits_target(point):
                return "hit_test_changed_after_queue"
        commit_identity = RuntimeCommitIdentity(
            before_frame.identity,
            RuntimeTargetIdentity.from_binding(self._binding),
        )
        if not self._permits.revalidate_active_admission(
            envelope,
            current_commit_identity=commit_identity,
        ):
            return "permit_changed_after_queue"
        return None

    def _run_raw(
        self,
        envelope: ActorDecisionEnvelope,
        prepared: _PreparedAction,
        before_frame: RuntimeFrame,
        initial_target: LiveTargetSnapshot,
        *,
        stop_event: threading.Event | None,
    ) -> RawActionReceipt:
        context = RawActionContext(
            target_hwnd=self._binding.hwnd,
            current_hwnd=initial_target.hwnd,
        )
        active_identity = RuntimeCommitIdentity(
            before_frame.identity,
            RuntimeTargetIdentity.from_binding(self._binding),
        )

        if prepared.point is not None:
            expected_point = prepared.point
            guarded_points = self._prepared_points(prepared)

            def pointer_guard(
                cursor_x: int,
                cursor_y: int,
            ) -> FinalPointerGuardDecision:
                if stop_event is not None and stop_event.is_set():
                    return FinalPointerGuardDecision.STOP_REQUESTED
                if (cursor_x, cursor_y) != expected_point:
                    return FinalPointerGuardDecision.POINTER_POSITION_CHANGED
                snapshot = self._safe_snapshot()
                for guarded_point in guarded_points:
                    decision = self._pointer_target_decision(
                        snapshot,
                        expected_region=before_frame.region,
                        point=guarded_point,
                    )
                    if decision is not FinalPointerGuardDecision.ADMITTED:
                        return decision
                if not self._permits.revalidate_active_admission(
                    envelope,
                    current_commit_identity=active_identity,
                ):
                    return FinalPointerGuardDecision.TARGET_UNAVAILABLE
                return FinalPointerGuardDecision.ADMITTED

            if prepared.action in {"click", "double_click"}:
                return self._raw.click(
                    context,
                    expected_point[0],
                    expected_point[1],
                    MouseButton.LEFT,
                    (
                        ClickCount.DOUBLE
                        if prepared.action == "double_click"
                        else ClickCount.SINGLE
                    ),
                    ClickMechanism.PHYSICAL,
                    final_guard=pointer_guard,
                )
            if prepared.action == "drag":
                assert prepared.end_point is not None
                arguments: dict[str, Any] = {
                    "final_guard": pointer_guard,
                }
                if prepared.points is not None:
                    arguments["points"] = list(prepared.points)
                if prepared.hold_control_present:
                    arguments.update(
                        pointer_already_held=prepared.started_held,
                        keep_holding=prepared.keep_holding,
                        hold_token=prepared.pointer_lease_id,
                    )
                receipt = self._raw.drag(
                    context,
                    expected_point[0],
                    expected_point[1],
                    prepared.end_point[0],
                    prepared.end_point[1],
                    **arguments,
                )
                return self._update_pointer_after_drag(
                    prepared,
                    receipt,
                )
            assert prepared.amount is not None
            return self._raw.scroll(
                context,
                expected_point[0],
                expected_point[1],
                prepared.amount,
                final_guard=pointer_guard,
            )

        def keyboard_guard() -> FinalKeyboardGuardDecision:
            if stop_event is not None and stop_event.is_set():
                return FinalKeyboardGuardDecision.STOP_REQUESTED
            snapshot = self._safe_snapshot()
            decision = self._keyboard_target_decision(
                snapshot,
                expected_region=before_frame.region,
            )
            if decision is not FinalKeyboardGuardDecision.ADMITTED:
                return decision
            if not self._permits.revalidate_active_admission(
                envelope,
                current_commit_identity=active_identity,
            ):
                return FinalKeyboardGuardDecision.TARGET_UNAVAILABLE
            return FinalKeyboardGuardDecision.ADMITTED

        if prepared.action == "type_text":
            assert prepared.text is not None
            return self._raw.type_text(
                context,
                prepared.text,
                final_guard=keyboard_guard,
            )
        assert prepared.chord is not None
        return self._raw.hotkey(
            context,
            prepared.chord,
            final_guard=keyboard_guard,
        )

    def _update_pointer_after_drag(
        self,
        prepared: _PreparedAction,
        receipt: RawActionReceipt,
    ) -> RawActionReceipt:
        if not prepared.hold_control_present:
            return receipt
        facts = (
            {}
            if receipt.backend_result is None
            else dict(receipt.backend_result.facts)
        )
        held_after = facts.get("held_after")
        if receipt.input_completed is True:
            if prepared.keep_holding:
                if (
                    held_after is not True
                    or prepared.pointer_lease_id is None
                    or prepared.end_point is None
                ):
                    with self._pointer_lock:
                        if (
                            self._held_pointer is None
                            and prepared.pointer_lease_id is not None
                        ):
                            self._held_pointer = _HeldPointerLease(
                                lease_id=prepared.pointer_lease_id,
                                cursor=prepared.end_point or (0, 0),
                                expires_at=self._clock(),
                            )
                        self._pointer_uncertain = True
                    return self._unknown(
                        "drag",
                        "held_pointer_state_unconfirmed",
                    )
                with self._pointer_lock:
                    self._held_pointer = _HeldPointerLease(
                        lease_id=prepared.pointer_lease_id,
                        cursor=prepared.end_point,
                        expires_at=(
                            self._clock() + self._held_pointer_timeout_s
                        ),
                    )
                    self._pointer_uncertain = False
            else:
                if prepared.started_held and held_after is not False:
                    with self._pointer_lock:
                        self._pointer_uncertain = True
                    return self._unknown(
                        "drag",
                        "held_pointer_release_unconfirmed",
                    )
                with self._pointer_lock:
                    self._held_pointer = None
                    self._pointer_uncertain = False
            return receipt

        if prepared.started_held or prepared.keep_holding:
            with self._pointer_lock:
                if self._held_pointer is None and prepared.pointer_lease_id:
                    self._held_pointer = _HeldPointerLease(
                        lease_id=prepared.pointer_lease_id,
                        cursor=(
                            prepared.end_point
                            if prepared.end_point is not None
                            else prepared.point
                            if prepared.point is not None
                            else (0, 0)
                        ),
                        expires_at=self._clock(),
                    )
                self._pointer_uncertain = True
        return receipt

    def _release_held_pointer(self) -> bool:
        with self._pointer_lock:
            return self._release_held_pointer_locked()

    def _release_held_pointer_locked(self) -> bool:
        lease = self._held_pointer
        if lease is None:
            self._pointer_uncertain = False
            return True
        try:
            receipt = self._raw.release_held_drag(lease.lease_id)
        except Exception:
            self._pointer_uncertain = True
            return False
        if receipt.input_completed is not True:
            self._pointer_uncertain = True
            return False
        self._held_pointer = None
        self._pointer_uncertain = False
        return True

    def _capture_post_action(
        self,
        envelope: ActorDecisionEnvelope,
        prepared: _PreparedAction,
        before: RuntimeFrame,
    ) -> tuple[RuntimeFrame, EvidenceRef]:
        dynamic_workspace = self._workspace is not None
        first = self._safe_snapshot()
        if not self._target_is_current(
            first,
            expected_region=None,
            require_foreground=not dynamic_workspace,
        ):
            raise RuntimeError("target changed before post-action capture")
        assert first is not None
        if not dynamic_workspace:
            for point in self._prepared_points(prepared):
                if not self._point_hits_target(point):
                    raise RuntimeError(
                        "hit test changed before post-action capture"
                    )

        region = Region(*first.region)
        captured = self._observer.capture(
            epoch=self._binding.epoch,
            hwnd=self._binding.hwnd,
            phase="post_action",
            action=prepared.action,
            provided_region=region,
            expected_previous_frame_id=before.frame_id,
        )
        if captured is None or captured.scope != "target_window":
            raise RuntimeError("post-action target-window capture unavailable")
        self._validate_jpeg(captured.image_b64)

        second = self._safe_snapshot()
        if not self._target_is_current(
            second,
            expected_region=first.region,
            require_foreground=not dynamic_workspace,
        ):
            raise RuntimeError("target changed before post-action commit")
        if not dynamic_workspace:
            for point in self._prepared_points(prepared):
                if not self._point_hits_target(point):
                    raise RuntimeError(
                        "hit test changed before post-action commit"
                    )
        current = self._authority.current
        if current is None or not self._frame_state_matches(current, before):
            raise RuntimeError("frame changed before post-action commit")

        committed = self._observer.commit(captured)
        if (
            committed.frame_id != before.frame_id + 1
            or committed.epoch != self._binding.epoch
            or committed.hwnd != self._binding.hwnd
            or committed.scope != "target_window"
            or committed.region != first.region
        ):
            raise RuntimeError("post-action frame commit identity changed")

        final_target = self._safe_snapshot()
        if not self._target_is_current(
            final_target,
            expected_region=committed.region,
            require_foreground=not dynamic_workspace,
        ):
            raise RuntimeError("target changed after post-action commit")
        if not dynamic_workspace:
            for point in self._prepared_points(prepared):
                if not self._point_hits_target(point):
                    raise RuntimeError(
                        "hit test changed after post-action commit"
                    )

        identity = self._runtime_frame_identity(committed)
        evidence = self._post_frame_evidence(envelope, identity)
        frame = self._runtime_frame(
            committed,
            evidence_refs=(evidence.id,),
        )
        self._persist_frame_artifact(
            envelope,
            frame,
            role=FrameArtifactRole.AFTER,
            capture_timestamp_ns=self._seconds_to_ns(
                committed.captured_at
            ),
        )
        return frame, evidence

    def _prepare_action(
        self,
        envelope: ActorDecisionEnvelope,
        proposal: DesktopActionProposal,
    ) -> _PreparedAction:
        action = proposal.action
        args = dict(proposal.args)
        if action in {"click", "double_click"}:
            self._require_keys(args, {"image_x", "image_y"})
            self._require_image_point(args)
            point = self._map_point(envelope, args)
            return _PreparedAction(
                action=action,
                point=point,
            )
        if action == "scroll":
            self._require_keys(args, {"image_x", "image_y", "amount"})
            self._require_image_point(args)
            amount = args["amount"]
            if (
                type(amount) is not int
                or amount == 0
                or abs(amount) > _MAX_SCROLL_AMOUNT
            ):
                raise ValueError(
                    "scroll amount must be a non-zero integer in -1200..1200"
                )
            point = self._map_point(envelope, args)
            return _PreparedAction(
                action=action,
                point=point,
                amount=amount,
            )
        if action == "drag":
            endpoint_keys = {
                "image_x1",
                "image_y1",
                "image_x2",
                "image_y2",
            }
            optional_keys = {"image_points", "keep_holding"}
            if (
                not endpoint_keys <= set(args)
                or not set(args) <= endpoint_keys | optional_keys
            ):
                raise ValueError(
                    "atomic action arguments do not match exact schema"
                )
            hold_control_present = "keep_holding" in args
            keep_holding = args.get("keep_holding", False)
            if type(keep_holding) is not bool:
                raise ValueError("keep_holding must be boolean")
            started_held = envelope.pointer_disposition == "held"
            if started_held and not hold_control_present:
                raise ValueError(
                    "held drag continuation requires keep_holding"
                )
            self._require_image_point(
                args,
                keys=("image_x1", "image_y1"),
            )
            self._require_image_point(
                args,
                keys=("image_x2", "image_y2"),
            )
            start = self._map_point(
                envelope,
                args,
                image_keys=("image_x1", "image_y1"),
            )
            end = self._map_point(
                envelope,
                args,
                image_keys=("image_x2", "image_y2"),
            )
            pointer_lease_id: str | None = None
            if started_held:
                with self._pointer_lock:
                    lease = self._held_pointer
                    if (
                        lease is None
                        or self._pointer_uncertain
                        or envelope.pointer_lease_id != lease.lease_id
                    ):
                        raise ValueError(
                            "held drag continuation lease is not current"
                        )
                    start = lease.cursor
                    pointer_lease_id = lease.lease_id
            elif keep_holding:
                digest = hashlib.sha256(
                    (
                        f"{envelope.mission_id}|{envelope.run_id}|"
                        f"{envelope.decision_id}"
                    ).encode("utf-8")
                ).hexdigest()
                pointer_lease_id = f"pointer-lease-{digest[:24]}"
            points: tuple[PhysicalPoint, ...] | None = None
            if "image_points" in args:
                raw_points = args["image_points"]
                if (
                    not isinstance(raw_points, (list, tuple))
                    or len(raw_points) > _MAX_DRAG_INTERMEDIATE_POINTS
                ):
                    raise ValueError(
                        "image_points must be an array with at most 32 points"
                    )
                mapped_points: list[PhysicalPoint] = []
                for index, raw_point in enumerate(raw_points):
                    if (
                        not isinstance(raw_point, (list, tuple))
                        or len(raw_point) != 2
                    ):
                        raise ValueError(
                            f"image_points[{index}] must be [x,y]"
                        )
                    point_args = {
                        "image_x": raw_point[0],
                        "image_y": raw_point[1],
                    }
                    self._require_image_point(point_args)
                    mapped_points.append(
                        self._map_point(envelope, point_args)
                    )
                points = tuple(mapped_points)
            stationary_release = bool(
                started_held
                and not keep_holding
                and not points
            )
            if start == end and not stationary_release:
                raise ValueError("drag endpoints must map to distinct pixels")
            return _PreparedAction(
                action=action,
                point=start,
                end_point=end,
                points=points,
                keep_holding=keep_holding,
                hold_control_present=hold_control_present,
                started_held=started_held,
                pointer_lease_id=pointer_lease_id,
            )
        if action == "type_text":
            self._require_keys(args, {"text"})
            text = args["text"]
            if (
                type(text) is not str
                or not 1 <= len(text) <= MAX_TYPE_TEXT_CODEPOINTS
            ):
                raise ValueError("type_text exceeds the strict raw contract")
            return _PreparedAction(
                action=action,
                text=text,
            )
        if action == "hotkey":
            self._require_keys(args, {"keys"})
            keys = args["keys"]
            if not isinstance(keys, (list, tuple)):
                raise ValueError("hotkey keys must be an array")
            return _PreparedAction(
                action=action,
                chord=HotkeyChord(tuple(keys)),
            )
        raise ValueError("unsupported atomic desktop action")

    @staticmethod
    def _physical_attempt_arguments(
        prepared: _PreparedAction,
    ) -> dict[str, Any]:
        """Describe the exact typed payload submitted to RawDevice."""

        if prepared.action in {"click", "double_click"}:
            assert prepared.point is not None
            return {
                "kind": "pointer_click",
                "x": prepared.point[0],
                "y": prepared.point[1],
                "button": "left",
                "click_count": (
                    2 if prepared.action == "double_click" else 1
                ),
            }
        if prepared.action == "scroll":
            assert prepared.point is not None
            assert prepared.amount is not None
            return {
                "kind": "wheel",
                "x": prepared.point[0],
                "y": prepared.point[1],
                "signed_wheel_amount": prepared.amount,
                "event_wheel_delta": prepared.amount * 120,
            }
        if prepared.action == "drag":
            assert prepared.point is not None
            assert prepared.end_point is not None
            arguments = {
                "kind": "pointer_drag",
                "x1": prepared.point[0],
                "y1": prepared.point[1],
                "x2": prepared.end_point[0],
                "y2": prepared.end_point[1],
                "button": "left",
            }
            if prepared.points is not None:
                arguments["points"] = [
                    [point[0], point[1]]
                    for point in prepared.points
                ]
            if prepared.hold_control_present:
                arguments.update(
                    started_held=prepared.started_held,
                    keep_holding=prepared.keep_holding,
                )
            return arguments
        if prepared.action == "type_text":
            assert prepared.text is not None
            return {
                "kind": "type_text",
                "text": prepared.text,
            }
        assert prepared.action == "hotkey"
        assert prepared.chord is not None
        return {
            "kind": "hotkey",
            "modifiers": list(prepared.chord.keys[:-1]),
            "key": prepared.chord.keys[-1],
        }

    @staticmethod
    def _raw_device_route(action: str) -> str:
        return {
            "click": "raw_device.admitted_physical_click/0",
            "double_click": "raw_device.admitted_physical_click/0",
            "drag": "raw_device.admitted_physical_drag/0",
            "scroll": "raw_device.admitted_physical_scroll/0",
            "type_text": "raw_device.admitted_physical_type_text/0",
            "hotkey": "raw_device.admitted_physical_hotkey/0",
        }[action]

    @staticmethod
    def _require_keys(
        args: Mapping[str, Any],
        expected: set[str],
    ) -> None:
        if set(args) != expected:
            raise ValueError("atomic action arguments do not match exact schema")

    @staticmethod
    def _require_image_point(
        args: Mapping[str, Any],
        *,
        keys: tuple[str, str] = ("image_x", "image_y"),
    ) -> None:
        if any(
            type(args[name]) not in {int, float}
            for name in keys
        ):
            raise ValueError(
                "image coordinates must be exact JSON numbers"
            )

    @staticmethod
    def _map_point(
        envelope: ActorDecisionEnvelope,
        args: Mapping[str, Any],
        *,
        image_keys: tuple[str, str] = ("image_x", "image_y"),
    ) -> PhysicalPoint:
        mapped, _ = SubmittedImageCoordinateMapper(
            envelope.submitted_image_size
        ).map_point_pair(
            args,
            image_keys=image_keys,
            frame_keys=("frame_x", "frame_y"),
            context=envelope.proposal.action,
        )
        return NormalizedFrameCoordinateMapper(
            envelope.frame_region
        ).map_point(mapped["frame_x"], mapped["frame_y"])

    @staticmethod
    def _prepared_points(
        prepared: _PreparedAction,
    ) -> tuple[PhysicalPoint, ...]:
        path = prepared.points or ()
        return tuple(
            point
            for point in (
                prepared.point,
                *path,
                prepared.end_point,
            )
            if point is not None
        )

    def _envelope_matches_frozen_target(
        self,
        envelope: ActorDecisionEnvelope,
    ) -> bool:
        binding = self._binding
        pointer = self.pointer_state()
        return bool(
            envelope.mission_id == self._permits.mission_id
            and envelope.run_id == self._permits.run_id
            and envelope.target_binding_id == binding.binding_id
            and envelope.frame_hwnd == binding.hwnd
            and envelope.frame_epoch == binding.epoch
            and envelope.frame_scope == "target_window"
            and envelope.allowed_evidence_scope
            and envelope.pointer_disposition
            == pointer.disposition.value
            and envelope.pointer_lease_id == pointer.lease_id
        )

    def _safe_snapshot(self) -> LiveTargetSnapshot | None:
        try:
            snapshot = self._probe.snapshot(self._binding.hwnd)
        except Exception:
            return None
        return snapshot if type(snapshot) is LiveTargetSnapshot else None

    def _target_is_current(
        self,
        snapshot: LiveTargetSnapshot | None,
        *,
        expected_region: PhysicalRegion | None,
        require_foreground: bool,
    ) -> bool:
        binding = self._binding
        return bool(
            snapshot is not None
            and snapshot.hwnd == binding.hwnd
            and snapshot.root_hwnd == binding.hwnd
            and snapshot.process_id == binding.process_id
            and snapshot.executable_identity
            == binding.executable_identity
            and (
                expected_region is None
                or snapshot.region == expected_region
            )
            and (
                not require_foreground
                or snapshot.foreground_root_hwnd == binding.hwnd
            )
        )

    def _pointer_target_decision(
        self,
        snapshot: LiveTargetSnapshot | None,
        *,
        expected_region: PhysicalRegion,
        point: PhysicalPoint,
    ) -> FinalPointerGuardDecision:
        binding = self._binding
        if snapshot is None:
            return FinalPointerGuardDecision.TARGET_UNAVAILABLE
        if snapshot.hwnd != binding.hwnd or snapshot.root_hwnd != binding.hwnd:
            return FinalPointerGuardDecision.TARGET_ROOT_CHANGED
        if (
            snapshot.process_id != binding.process_id
            or snapshot.executable_identity != binding.executable_identity
        ):
            return FinalPointerGuardDecision.TARGET_PROCESS_CHANGED
        if snapshot.region != expected_region:
            return FinalPointerGuardDecision.TARGET_GEOMETRY_CHANGED
        if snapshot.foreground_root_hwnd != binding.hwnd:
            return FinalPointerGuardDecision.TARGET_UNAVAILABLE
        if not self._inside(point, expected_region):
            return FinalPointerGuardDecision.POINT_OUTSIDE_TARGET
        try:
            hit = self._probe.hit_test_root(*point)
        except Exception:
            return FinalPointerGuardDecision.HIT_TEST_UNAVAILABLE
        if hit is None:
            return FinalPointerGuardDecision.HIT_TEST_UNAVAILABLE
        if type(hit) is not int or hit != binding.hwnd:
            return FinalPointerGuardDecision.HIT_TEST_CHANGED
        return FinalPointerGuardDecision.ADMITTED

    def _keyboard_target_decision(
        self,
        snapshot: LiveTargetSnapshot | None,
        *,
        expected_region: PhysicalRegion,
    ) -> FinalKeyboardGuardDecision:
        binding = self._binding
        if snapshot is None:
            return FinalKeyboardGuardDecision.TARGET_UNAVAILABLE
        if snapshot.hwnd != binding.hwnd or snapshot.root_hwnd != binding.hwnd:
            return FinalKeyboardGuardDecision.TARGET_ROOT_CHANGED
        if (
            snapshot.process_id != binding.process_id
            or snapshot.executable_identity != binding.executable_identity
        ):
            return FinalKeyboardGuardDecision.TARGET_PROCESS_CHANGED
        if snapshot.region != expected_region:
            return FinalKeyboardGuardDecision.TARGET_GEOMETRY_CHANGED
        if snapshot.foreground_root_hwnd is None:
            return FinalKeyboardGuardDecision.FOREGROUND_UNAVAILABLE
        if snapshot.foreground_root_hwnd != binding.hwnd:
            return FinalKeyboardGuardDecision.FOREGROUND_CHANGED
        return FinalKeyboardGuardDecision.ADMITTED

    def _point_hits_target(self, point: PhysicalPoint) -> bool:
        try:
            hit = self._probe.hit_test_root(*point)
        except Exception:
            return False
        return type(hit) is int and hit == self._binding.hwnd

    @staticmethod
    def _inside(point: PhysicalPoint, region: PhysicalRegion) -> bool:
        x, y = point
        left, top, width, height = region
        return left <= x < left + width and top <= y < top + height

    @staticmethod
    def _frame_state_matches(
        state: FrameState,
        frame: RuntimeFrame,
    ) -> bool:
        return state.commit_identity == (
            frame.frame_id,
            frame.epoch,
            frame.hwnd,
            frame.region,
            frame.scope,
            frame.submitted_size,
            frame.image_sha256,
        )

    @classmethod
    def _runtime_frame(
        cls,
        state: FrameState,
        *,
        evidence_refs: tuple[str, ...],
    ) -> RuntimeFrame:
        identity = cls._runtime_frame_identity(state)
        return RuntimeFrame(
            frame_id=identity.frame_id,
            epoch=identity.epoch,
            hwnd=identity.hwnd,
            region=identity.region,
            scope=identity.scope,
            submitted_size=identity.submitted_size,
            image_b64=state.image_b64,
            image_sha256=identity.image_sha256,
            evidence_refs=tuple(sorted(evidence_refs)),
            media_type=identity.media_type,
        )

    @classmethod
    def _runtime_frame_identity(
        cls,
        state: FrameState,
    ) -> RuntimeFrameIdentity:
        cls._validate_jpeg(state.image_b64)
        return RuntimeFrameIdentity(
            frame_id=state.frame_id,
            epoch=state.epoch,
            hwnd=state.hwnd,
            region=state.region,
            scope=state.scope,
            submitted_size=state.submitted_size,
            image_sha256=state.image_sha256,
            media_type=_JPEG_MEDIA_TYPE,
        )

    @staticmethod
    def _validate_jpeg(image_b64: str) -> None:
        try:
            raw = base64.b64decode(image_b64, validate=True)
        except Exception as exc:
            raise ValueError("frame payload is not strict base64") from exc
        if (
            len(raw) < 4
            or not raw.startswith(b"\xff\xd8\xff")
            or not raw.endswith(b"\xff\xd9")
        ):
            raise ValueError("frame payload is not an explicit JPEG")

    @staticmethod
    def _post_frame_evidence(
        envelope: ActorDecisionEnvelope,
        identity: RuntimeFrameIdentity,
    ) -> EvidenceRef:
        payload_hash = identity.payload_hash
        material = {
            "format": "atomic-post-frame-evidence/0",
            "mission_id": envelope.mission_id,
            "run_id": envelope.run_id,
            "obligation_id": envelope.obligation_id,
            "action_id": envelope.decision_id,
            "frame_id": identity.frame_id,
            "payload_hash": payload_hash,
        }
        digest = hashlib.sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        return EvidenceRef(
            id=f"evidence-post-action-frame-{digest[:32]}",
            mission_id=envelope.mission_id,
            run_id=envelope.run_id,
            obligation_id=envelope.obligation_id,
            kind=_POST_FRAME_KIND,
            trust="deterministic",
            payload_hash=payload_hash,
            frame_id=identity.frame_id,
            action_id=envelope.decision_id,
        )

    @staticmethod
    def _raw_action(action: str) -> str:
        return "click" if action == "double_click" else action

    @classmethod
    def _not_started(
        cls,
        action: str,
        error_kind: str,
    ) -> RawActionReceipt:
        return RawActionReceipt(
            action=cls._raw_action(action),
            outcome=MutationOutcome.NOT_STARTED,
            error_kind=error_kind,
        )

    @classmethod
    def _unknown(
        cls,
        action: str,
        error_kind: str,
    ) -> RawActionReceipt:
        return RawActionReceipt(
            action=cls._raw_action(action),
            outcome=MutationOutcome.OUTCOME_UNKNOWN,
            error_kind=error_kind,
        )

    @staticmethod
    def _rejected(
        envelope: ActorDecisionEnvelope,
        action: str,
    ) -> ControllerTransition:
        return ControllerTransition(
            outcome=ControllerOutcome.REJECTED,
            action_id=envelope.decision_id,
            action=action,
            input_completed=False,
        )

    @staticmethod
    def _unknown_transition(
        envelope: ActorDecisionEnvelope,
        action: str,
        *,
        input_completed: bool | None,
        post_frame: RuntimeFrame | None = None,
        post_evidence: EvidenceRef | None = None,
    ) -> ControllerTransition:
        if (post_frame is None) != (post_evidence is None):
            post_frame = None
            post_evidence = None
        return ControllerTransition(
            outcome=ControllerOutcome.UNKNOWN,
            action_id=envelope.decision_id,
            action=action,
            input_completed=input_completed,
            post_frame_evidence=(
                () if post_evidence is None else (post_evidence,)
            ),
            post_frame_id=(
                None if post_frame is None else post_frame.frame_id
            ),
        )

    def _persist_frame_artifact(
        self,
        envelope: ActorDecisionEnvelope,
        frame: RuntimeFrame,
        *,
        role: FrameArtifactRole,
        capture_timestamp_ns: int,
    ) -> None:
        writer = self._frame_artifacts
        evidence_plane = self._runtime_evidence
        if writer is None:
            return
        if evidence_plane is None or not evidence_plane.durable:
            raise RuntimeError(
                "frame artifacts require a durable Runtime evidence plane"
            )
        identity = frame.identity.to_dict()
        result = writer.write_frame(
            mission_id=envelope.mission_id,
            run_id=envelope.run_id,
            decision_id=envelope.decision_id,
            action_id=envelope.decision_id,
            role=role.value,
            frame_identity=identity,
            image_b64=frame.image_b64,
            capture_timestamp_ns=capture_timestamp_ns,
        )
        evidence_plane.record_frame_artifact(
            FrameArtifactReference(
                mission_id=envelope.mission_id,
                run_id=envelope.run_id,
                decision_id=envelope.decision_id,
                action_id=envelope.decision_id,
                frame_id=result.frame_id,
                frame_identity=identity,
                artifact_uri=result.artifact_uri,
                raw_jpeg_sha256=result.sha256,
                runtime_frame_image_sha256=frame.image_sha256,
                capture_timestamp_ns=result.capture_timestamp_ns,
                role=result.role,
            )
        )

    @staticmethod
    def _seconds_to_ns(value: float) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("capture timestamp must be a finite number")
        seconds = float(value)
        if not math.isfinite(seconds) or seconds < 0.0:
            raise ValueError(
                "capture timestamp must be finite and non-negative"
            )
        nanoseconds = int(seconds * 1_000_000_000)
        if nanoseconds > (2**63 - 1):
            raise ValueError("capture timestamp exceeds signed-64 range")
        return nanoseconds

    def _receipt_metadata(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        phase: str,
    ) -> Mapping[str, TraceScalar]:
        binding = self._binding
        process_name = ntpath.basename(binding.executable_identity)
        return MappingProxyType(
            {
                "phase": phase,
                "controller_action": envelope.proposal.action,
                "decision_id": envelope.decision_id,
                "mission_id": envelope.mission_id,
                "run_id": envelope.run_id,
                "obligation_id": envelope.obligation_id,
                "frame_id": envelope.frame_id,
                "frame_epoch": envelope.frame_epoch,
                "target_hwnd": envelope.frame_hwnd,
                "target_binding_id": envelope.target_binding_id,
                "target_title": binding.title_snapshot,
                "target_process": process_name,
            }
        )

    def _record_trace_receipt(
        self,
        receipt: RawActionReceipt,
        envelope: ActorDecisionEnvelope,
        *,
        phase: str,
    ) -> None:
        sink = self._trace
        if sink is None:
            return
        try:
            sink.record(
                receipt,
                metadata=self._receipt_metadata(envelope, phase=phase),
            )
        except Exception:
            pass

    def _record_evidence_receipt(
        self,
        receipt: RawActionReceipt,
        envelope: ActorDecisionEnvelope,
    ) -> bool:
        evidence_plane = self._runtime_evidence
        if evidence_plane is None:
            return True
        try:
            evidence_plane.record_raw_receipt(
                decision_id=envelope.decision_id,
                mission_id=envelope.mission_id,
                run_id=envelope.run_id,
                actor_envelope_sha256=hashlib.sha256(
                    envelope.to_json().encode("utf-8")
                ).hexdigest(),
                receipt=receipt.to_dict(),
            )
        except Exception:
            return False
        return True

    def _record_receipt(
        self,
        receipt: RawActionReceipt,
        envelope: ActorDecisionEnvelope,
        *,
        phase: str,
    ) -> bool:
        self._record_trace_receipt(
            receipt,
            envelope,
            phase=phase,
        )
        return self._record_evidence_receipt(receipt, envelope)


__all__ = [
    "AtomicDesktopAdapter",
    "DesktopTargetProbe",
    "LiveTargetSnapshot",
    "RawReceiptTraceSink",
]
