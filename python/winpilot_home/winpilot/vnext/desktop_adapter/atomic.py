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
from ...agent.frame_authority import FrameAuthority, FrameState
from ...utils.screenshot import Region
from ...visual_runtime.frame_observer import FrameObserver
from ...visual_runtime.normalized_frame_coordinates import (
    NormalizedFrameCoordinateMapper,
)
from ...visual_runtime.submitted_image_coordinates import (
    SubmittedImageCoordinateMapper,
)
from ..actor import ActorDecisionEnvelope, DesktopActionProposal
from ..evidence_plane import RuntimeEvidencePlane
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
    {"click", "double_click", "scroll", "type_text", "hotkey"}
)
_POST_FRAME_KIND = "post_action_frame"
_JPEG_MEDIA_TYPE = "image/jpeg"
_MAX_SCROLL_AMOUNT = 1200

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


class RawReceiptTraceSink(Protocol):
    """Best-effort sink for the complete immutable RawDevice receipt."""

    def record(
        self,
        receipt: RawActionReceipt,
        *,
        envelope: ActorDecisionEnvelope,
        metadata: Mapping[str, TraceScalar],
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class _PreparedAction:
    action: str
    point: PhysicalPoint | None = None
    amount: int | None = None
    text: str | None = None
    chord: HotkeyChord | None = None


@dataclass(frozen=True, slots=True)
class _PinnedExecution:
    before_frame: RuntimeFrame | None
    receipt: RawActionReceipt


class AtomicDesktopAdapter:
    """Execute exactly one admitted desktop proposal through existing owners."""

    def __init__(
        self,
        *,
        frame_authority: FrameAuthority,
        frame_observer: FrameObserver,
        frozen_target_binding: TargetBinding,
        target_probe: DesktopTargetProbe,
        mutation_permits: MutationPermitAuthority,
        raw_device: RawDeviceExecutor,
        receipt_trace_sink: RawReceiptTraceSink | None = None,
        runtime_evidence_plane: RuntimeEvidencePlane | None = None,
        post_input_settle_delay_s: float = 0.12,
        wait: Callable[[float], Any] = time.sleep,
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
        if not callable(getattr(target_probe, "snapshot", None)):
            raise TypeError("target_probe must provide snapshot()")
        if not callable(getattr(target_probe, "hit_test_root", None)):
            raise TypeError("target_probe must provide hit_test_root()")
        if type(mutation_permits) is not MutationPermitAuthority:
            raise TypeError(
                "mutation_permits must be MutationPermitAuthority"
            )
        if type(raw_device) is not RawDeviceExecutor:
            raise TypeError("raw_device must be RawDeviceExecutor")
        if set(raw_device.admitted_action_names) != {
            "click",
            "scroll",
            "type_text",
            "hotkey",
        }:
            raise ValueError(
                "RawDevice must expose exactly the four admitted physical primitives"
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
        self._binding = frozen_target_binding
        self._probe = target_probe
        self._permits = mutation_permits
        self._raw = raw_device
        self._trace = receipt_trace_sink
        self._runtime_evidence = runtime_evidence_plane
        self._post_input_settle_delay_s = settle_delay
        self._wait = wait
        self._effect = DeterministicActionEffectVerifier()

    @property
    def mutation_permits(self) -> MutationPermitAuthority:
        """Expose identity only so composition can prove one shared authority."""

        return self._permits

    @property
    def frozen_target_binding(self) -> TargetBinding:
        return self._binding

    @property
    def runtime_evidence_plane(self) -> RuntimeEvidencePlane | None:
        return self._runtime_evidence

    def execute(
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
        self._record_receipt(receipt, envelope, phase="raw_device")
        if receipt.input_completed is False:
            return self._rejected(envelope, action)
        if (
            receipt.input_completed is True
            and self._post_input_settle_delay_s > 0.0
        ):
            try:
                self._wait(self._post_input_settle_delay_s)
            except Exception:
                return self._unknown_transition(
                    envelope,
                    action,
                    input_completed=True,
                )
        if pinned.before_frame is None:
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
            return self._unknown_transition(
                envelope,
                action,
                input_completed=receipt.input_completed,
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
        if prepared.point is not None and not self._point_hits_target(
            prepared.point
        ):
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
        return _PinnedExecution(before_frame, receipt)

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
        if prepared.point is not None:
            if not self._inside(prepared.point, before_frame.region):
                return "point_outside_target_after_queue"
            if not self._point_hits_target(prepared.point):
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

            def pointer_guard(
                cursor_x: int,
                cursor_y: int,
            ) -> FinalPointerGuardDecision:
                if stop_event is not None and stop_event.is_set():
                    return FinalPointerGuardDecision.STOP_REQUESTED
                if (cursor_x, cursor_y) != expected_point:
                    return FinalPointerGuardDecision.POINTER_POSITION_CHANGED
                snapshot = self._safe_snapshot()
                decision = self._pointer_target_decision(
                    snapshot,
                    expected_region=before_frame.region,
                    point=expected_point,
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

    def _capture_post_action(
        self,
        envelope: ActorDecisionEnvelope,
        prepared: _PreparedAction,
        before: RuntimeFrame,
    ) -> tuple[RuntimeFrame, EvidenceRef]:
        first = self._safe_snapshot()
        if not self._target_is_current(
            first,
            expected_region=None,
            require_foreground=True,
        ):
            raise RuntimeError("target changed before post-action capture")
        assert first is not None
        if prepared.point is not None and not self._point_hits_target(
            prepared.point
        ):
            raise RuntimeError("hit test changed before post-action capture")

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
            require_foreground=True,
        ):
            raise RuntimeError("target changed before post-action commit")
        if prepared.point is not None and not self._point_hits_target(
            prepared.point
        ):
            raise RuntimeError("hit test changed before post-action commit")
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
            require_foreground=True,
        ):
            raise RuntimeError("target changed after post-action commit")
        if prepared.point is not None and not self._point_hits_target(
            prepared.point
        ):
            raise RuntimeError("hit test changed after post-action commit")

        identity = self._runtime_frame_identity(committed)
        evidence = self._post_frame_evidence(envelope, identity)
        frame = self._runtime_frame(
            committed,
            evidence_refs=(evidence.id,),
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
    def _require_keys(
        args: Mapping[str, Any],
        expected: set[str],
    ) -> None:
        if set(args) != expected:
            raise ValueError("atomic action arguments do not match exact schema")

    @staticmethod
    def _require_image_point(args: Mapping[str, Any]) -> None:
        if any(
            type(args[name]) not in {int, float}
            for name in ("image_x", "image_y")
        ):
            raise ValueError(
                "image coordinates must be exact JSON numbers"
            )

    @staticmethod
    def _map_point(
        envelope: ActorDecisionEnvelope,
        args: Mapping[str, Any],
    ) -> PhysicalPoint:
        mapped, _ = SubmittedImageCoordinateMapper(
            envelope.submitted_image_size
        ).map_point_pair(
            args,
            image_keys=("image_x", "image_y"),
            frame_keys=("frame_x", "frame_y"),
            context=envelope.proposal.action,
        )
        return NormalizedFrameCoordinateMapper(
            envelope.frame_region
        ).map_point(mapped["frame_x"], mapped["frame_y"])

    def _envelope_matches_frozen_target(
        self,
        envelope: ActorDecisionEnvelope,
    ) -> bool:
        binding = self._binding
        return bool(
            envelope.mission_id == self._permits.mission_id
            and envelope.run_id == self._permits.run_id
            and envelope.target_binding_id == binding.binding_id
            and envelope.frame_hwnd == binding.hwnd
            and envelope.frame_epoch == binding.epoch
            and envelope.frame_scope == "target_window"
            and envelope.allowed_evidence_scope
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

    def _record_receipt(
        self,
        receipt: RawActionReceipt,
        envelope: ActorDecisionEnvelope,
        *,
        phase: str,
    ) -> None:
        sink = self._trace
        metadata: Mapping[str, TraceScalar] = MappingProxyType(
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
            }
        )
        if sink is not None:
            try:
                sink.record(
                    receipt,
                    envelope=envelope,
                    metadata=metadata,
                )
            except Exception:
                pass
        evidence_plane = self._runtime_evidence
        if evidence_plane is not None:
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
                pass


__all__ = [
    "AtomicDesktopAdapter",
    "DesktopTargetProbe",
    "LiveTargetSnapshot",
    "RawReceiptTraceSink",
]
