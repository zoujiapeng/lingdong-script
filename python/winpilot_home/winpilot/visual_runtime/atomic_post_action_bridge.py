"""Opt-in post-action frame/evidence bridge for the atomic Controller.

``AtomicDesktopController`` calls this port while it still owns the physical
desktop lease but after it releases the authoritative-frame pin.  The bridge
only captures, CAS-publishes, and registers immutable evidence; it performs no
GUI mutation, pixel interpretation, BUS publication, or semantic verification.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable

from ..action.raw_device import RawActionReceipt
from ..agent.frame_authority import FrameAuthority, FrameState
from ..vnext.actor import ActorDecisionEnvelope, DesktopActionProposal
from ..vnext.evidence import (
    POST_ACTION_FRAME_EVIDENCE_KIND,
    EvidenceRegistry,
    frame_identity_payload_hash,
)
from ..vnext.mission import EvidenceRef
from .atomic_click_controller import ControllerPostAction
from .commit_admission import (
    CommitAdmissionResult,
    CommitAdmissionStatus,
    FrameCommitIdentity,
)
from .frame_observer import FrameObserver


class AtomicPostActionBridgeError(RuntimeError):
    """Post-action authority or evidence could not be closed mechanically."""


class AtomicPostActionBridge:
    """Implement ``ControllerPostActionPort`` with existing canonical owners."""

    def __init__(
        self,
        *,
        frame_authority: FrameAuthority,
        observer: FrameObserver,
        evidence: EvidenceRegistry,
        related_window: Callable[[int | None, int | None], bool] | None = None,
        post_input_settle_delay_s: float = 0.12,
        wait: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(frame_authority, FrameAuthority):
            raise TypeError("frame_authority must be FrameAuthority")
        if not isinstance(observer, FrameObserver):
            raise TypeError("observer must be FrameObserver")
        if observer.authority is not frame_authority:
            raise TypeError("observer and bridge must share one FrameAuthority")
        if not isinstance(evidence, EvidenceRegistry):
            raise TypeError("evidence must be EvidenceRegistry")
        if related_window is not None and not callable(related_window):
            raise TypeError("related_window must be callable or null")
        if (
            isinstance(post_input_settle_delay_s, bool)
            or not isinstance(post_input_settle_delay_s, (int, float))
            or not math.isfinite(float(post_input_settle_delay_s))
            or not 0.0 <= float(post_input_settle_delay_s) <= 1.0
        ):
            raise ValueError(
                "post_input_settle_delay_s must be finite and in [0, 1]"
            )
        if not callable(wait):
            raise TypeError("wait must be callable")
        self._observer = observer
        self._evidence = evidence
        self._related_window = related_window
        self._post_input_settle_delay_s = float(post_input_settle_delay_s)
        self._wait = wait

    @staticmethod
    def _frame_identity(state: FrameState) -> FrameCommitIdentity:
        return FrameCommitIdentity(
            frame_id=state.frame_id,
            epoch=state.epoch,
            hwnd=state.hwnd,
            region=state.region,
            scope=state.scope,
            submitted_size=state.submitted_size,
            image_sha256=state.image_sha256,
        )

    @staticmethod
    def _evidence_id(
        envelope: ActorDecisionEnvelope,
        payload_hash: str,
    ) -> str:
        identity = {
            "format": "post-action-evidence-identity/0",
            "mission_id": envelope.mission_id,
            "run_id": envelope.run_id,
            "obligation_id": envelope.obligation_id,
            "action_id": envelope.decision_id,
            "payload_hash": payload_hash,
        }
        digest = hashlib.sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        return f"evidence-post-action-{digest}"

    def _validate_call(
        self,
        envelope: ActorDecisionEnvelope,
        admission: CommitAdmissionResult,
        raw_receipt: RawActionReceipt | None,
    ) -> DesktopActionProposal:
        if not isinstance(envelope, ActorDecisionEnvelope):
            raise TypeError("envelope must be ActorDecisionEnvelope")
        proposal = envelope.proposal
        if not isinstance(proposal, DesktopActionProposal):
            raise AtomicPostActionBridgeError(
                "post-action bridge requires a desktop action proposal"
            )
        if not isinstance(admission, CommitAdmissionResult):
            raise TypeError("admission must be CommitAdmissionResult")
        if admission.request_id != envelope.decision_id:
            raise AtomicPostActionBridgeError(
                "admission does not belong to the Runtime action"
            )
        if raw_receipt is not None and not isinstance(raw_receipt, RawActionReceipt):
            raise TypeError("raw_receipt must be RawActionReceipt or null")
        expected_raw_action = "scroll" if proposal.action == "scroll" else "click"
        if admission.status is CommitAdmissionStatus.ADMITTED:
            if raw_receipt is None or raw_receipt.action != expected_raw_action:
                raise AtomicPostActionBridgeError(
                    "admitted point input requires its exact raw receipt"
                )
        elif raw_receipt is not None:
            raise AtomicPostActionBridgeError(
                "non-admitted action cannot carry a raw receipt"
            )
        if (
            envelope.mission_id != self._evidence.mission_id
            or envelope.run_id != self._evidence.run_id
        ):
            raise AtomicPostActionBridgeError(
                "action is outside the evidence registry mission/run"
            )
        return proposal

    def observe_after(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        admission: CommitAdmissionResult,
        raw_receipt: RawActionReceipt | None,
    ) -> ControllerPostAction:
        proposal = self._validate_call(envelope, admission, raw_receipt)
        if (
            raw_receipt is not None
            and raw_receipt.input_completed is True
            and self._post_input_settle_delay_s > 0.0
        ):
            # Input dispatch and visible paint are asynchronous on many desktop
            # surfaces.  Observe only after one bounded generic settle window;
            # this is neither action replay nor an application-specific wait.
            self._wait(self._post_input_settle_delay_s)
        captured = self._observer.capture(
            epoch=envelope.frame.epoch,
            hwnd=envelope.frame.hwnd,
            phase="post_action",
            action=proposal.action,
            related_window=self._related_window,
            expected_previous_frame_id=envelope.frame.frame_id,
        )
        if captured is None:
            raise AtomicPostActionBridgeError("post-action frame capture failed")

        # FrameObserver.commit is a CAS against the exact pre-action frame.
        # AtomicDesktopController deliberately invokes this outside its frame pin.
        post_frame = self._observer.commit(captured)
        frame_identity = self._frame_identity(post_frame)
        payload_hash = frame_identity_payload_hash(frame_identity)
        evidence = EvidenceRef(
            id=self._evidence_id(envelope, payload_hash),
            mission_id=envelope.mission_id,
            run_id=envelope.run_id,
            obligation_id=envelope.obligation_id,
            kind=POST_ACTION_FRAME_EVIDENCE_KIND,
            trust="deterministic",
            payload_hash=payload_hash,
            frame_id=post_frame.frame_id,
            action_id=envelope.decision_id,
            created_at=post_frame.committed_at,
        )
        self._evidence.append(evidence)
        return ControllerPostAction(
            post_frame_id=post_frame.frame_id,
            evidence_refs=(evidence.id,),
            visible_effect="",
        )


__all__ = ["AtomicPostActionBridge", "AtomicPostActionBridgeError"]
