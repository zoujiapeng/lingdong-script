"""Stateless reconciliation of one Controller post-action result."""
from __future__ import annotations

from dataclasses import dataclass

from ...visual_runtime.commit_admission import FrameCommitIdentity
from ..evidence import (
    POST_ACTION_FRAME_EVIDENCE_KIND,
    EvidenceRegistry,
    frame_identity_payload_hash,
)
from ..mission import EvidenceRef
from .contracts import ControllerOutcome, ControllerTransition, RuntimeSnapshot


class PostActionReconciliationError(ValueError):
    """Frozen transition, frame, or evidence authority does not agree."""


@dataclass(frozen=True, slots=True)
class PostActionReconciliation:
    """Exact immutable facts proven by one successful reconciliation."""

    action_id: str
    action: str
    frame: FrameCommitIdentity
    payload_hash: str
    evidence_refs: tuple[EvidenceRef, ...]


def reconcile_post_action(
    transition: ControllerTransition,
    snapshot: RuntimeSnapshot,
    evidence: EvidenceRegistry,
    *,
    mission_id: str,
    run_id: str,
    obligation_id: str,
    action_id: str,
    action: str,
) -> PostActionReconciliation:
    """Prove one transition against the current authoritative post frame.

    Unknown outcomes are intentionally rejected here.  Their recovery policy
    remains a MissionRuntime concern, not a property of evidence matching.
    """

    if not isinstance(transition, ControllerTransition):
        raise TypeError("transition must be ControllerTransition")
    if not isinstance(snapshot, RuntimeSnapshot):
        raise TypeError("snapshot must be RuntimeSnapshot")
    if not isinstance(evidence, EvidenceRegistry):
        raise TypeError("evidence must be canonical EvidenceRegistry")
    expected = (mission_id, run_id, obligation_id, action_id, action)
    if any(
        type(value) is not str or not value.strip()
        for value in expected
    ):
        raise PostActionReconciliationError(
            "expected reconciliation identities must be non-empty strings"
        )
    mission_id, run_id, obligation_id, action_id, action = (
        value.strip() for value in expected
    )
    if transition.outcome is ControllerOutcome.UNKNOWN:
        raise PostActionReconciliationError(
            "unknown mutation outcome is not post-action reconcilable"
        )
    if (
        transition.action_id != action_id
        or transition.action != action
    ):
        raise PostActionReconciliationError(
            "Controller transition action identity changed"
        )

    frame = snapshot.frame
    frame_state = snapshot.broker_state.frame
    if (
        frame is None
        or not frame_state.authoritative
        or not frame_state.fresh
        or transition.post_frame_id != frame.frame_id
    ):
        raise PostActionReconciliationError(
            "Controller post frame is not current authoritative and fresh"
        )
    if transition.evidence_refs != frame.evidence_refs:
        raise PostActionReconciliationError(
            "Controller and current frame evidence identities differ"
        )

    if (
        evidence.mission_id != mission_id
        or evidence.run_id != run_id
    ):
        raise PostActionReconciliationError(
            "EvidenceRegistry is outside the expected mission/run"
        )
    refs_by_id = {ref.id: ref for ref in evidence.snapshot()}
    if any(evidence_id not in refs_by_id for evidence_id in transition.evidence_refs):
        raise PostActionReconciliationError(
            "Controller evidence is not in the run registry"
        )
    refs = tuple(refs_by_id[evidence_id] for evidence_id in transition.evidence_refs)
    payload_hash = frame_identity_payload_hash(frame.commit_identity)
    if any(
        ref.mission_id != mission_id
        or ref.run_id != run_id
        or ref.obligation_id != obligation_id
        or ref.action_id != action_id
        or ref.frame_id != frame.frame_id
        or ref.payload_hash != payload_hash
        or ref.trust != "deterministic"
        or ref.kind != POST_ACTION_FRAME_EVIDENCE_KIND
        or ref.artifact_id is not None
        or ref.content_sha256 is not None
        for ref in refs
    ):
        raise PostActionReconciliationError(
            "post-action evidence does not bind the exact transition and frame"
        )
    return PostActionReconciliation(
        action_id=action_id,
        action=action,
        frame=frame.commit_identity,
        payload_hash=payload_hash,
        evidence_refs=refs,
    )


__all__ = [
    "PostActionReconciliation",
    "PostActionReconciliationError",
    "reconcile_post_action",
]
