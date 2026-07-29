"""Stateless reconciliation of one completed Controller transition."""
from __future__ import annotations

from dataclasses import dataclass

from ..capabilities import PointerDisposition
from ..evidence import EvidenceRegistry
from ..mission import (
    ACTION_EFFECT_EVIDENCE_KIND,
    EvidenceRef,
)
from .contracts import (
    CURRENT_FRAME_EVIDENCE_KIND,
    POST_ACTION_FRAME_EVIDENCE_KIND,
    ControllerOutcome,
    ControllerTransition,
    RuntimeFrameIdentity,
    RuntimeSnapshot,
    RuntimeTargetIdentity,
    frame_identity_payload_hash,
)


class PostActionReconciliationError(ValueError):
    """Controller, frame, target, or evidence authority does not agree."""


@dataclass(frozen=True, slots=True)
class PostActionReconciliation:
    action_id: str
    action: str
    before_frame: RuntimeFrameIdentity
    after_frame: RuntimeFrameIdentity
    target: RuntimeTargetIdentity
    event_evidence_refs: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]


def validate_controller_evidence_scope(
    transition: ControllerTransition,
    evidence: EvidenceRegistry,
    *,
    mission_id: str,
    run_id: str,
    obligation_id: str,
    action_id: str,
) -> None:
    """Validate produced identities before Runtime performs the sole append."""

    if type(transition) is not ControllerTransition:
        raise TypeError(
            "transition must be canonical ControllerTransition"
        )
    if type(evidence) is not EvidenceRegistry:
        raise TypeError("evidence must be canonical EvidenceRegistry")
    if evidence.mission_id != mission_id or evidence.run_id != run_id:
        raise PostActionReconciliationError(
            "EvidenceRegistry is outside mission/run"
        )
    if any(
        ref.mission_id != mission_id
        or ref.run_id != run_id
        or ref.obligation_id != obligation_id
        or ref.kind != POST_ACTION_FRAME_EVIDENCE_KIND
        or ref.trust != "deterministic"
        or ref.action_id != action_id
        or ref.frame_id != transition.post_frame_id
        for ref in transition.post_frame_evidence
    ):
        raise PostActionReconciliationError(
            "produced post-frame evidence is outside action scope"
        )
    effect = transition.action_effect
    effect_ref = transition.action_effect_evidence
    if effect is not None and (
        effect_ref is None
        or effect_ref.mission_id != mission_id
        or effect_ref.run_id != run_id
        or effect_ref.obligation_id != obligation_id
        or effect_ref.kind != ACTION_EFFECT_EVIDENCE_KIND
        or effect_ref.trust != "deterministic"
        or effect_ref.action_id != action_id
        or effect_ref.frame_id != transition.post_frame_id
        or effect_ref.payload_hash != effect.payload_hash
    ):
        raise PostActionReconciliationError(
            "produced action-effect evidence is outside action scope"
        )


def _registry_refs(
    registry: EvidenceRegistry,
) -> dict[str, EvidenceRef]:
    return {ref.id: ref for ref in registry.snapshot()}


def reconcile_post_action(
    transition: ControllerTransition,
    before: RuntimeSnapshot,
    after: RuntimeSnapshot,
    evidence: EvidenceRegistry,
    *,
    mission_id: str,
    run_id: str,
    obligation_id: str,
    action_id: str,
    action: str,
    keep_holding: bool = False,
) -> PostActionReconciliation:
    """Prove one completed mutation against exact immutable Runtime facts."""

    if type(transition) is not ControllerTransition:
        raise TypeError(
            "transition must be canonical ControllerTransition"
        )
    if type(before) is not RuntimeSnapshot or type(after) is not RuntimeSnapshot:
        raise TypeError("before and after must be RuntimeSnapshot")
    if type(evidence) is not EvidenceRegistry:
        raise TypeError("evidence must be canonical EvidenceRegistry")
    expected_text = (
        mission_id,
        run_id,
        obligation_id,
        action_id,
        action,
    )
    if any(type(value) is not str or not value.strip() for value in expected_text):
        raise PostActionReconciliationError(
            "reconciliation identities must be non-empty strings"
        )
    if type(keep_holding) is not bool:
        raise PostActionReconciliationError(
            "keep_holding must be boolean"
        )
    if keep_holding and action != "drag":
        raise PostActionReconciliationError(
            "only drag may keep the pointer held"
        )
    if transition.outcome is not ControllerOutcome.COMPLETED:
        raise PostActionReconciliationError(
            "only completed mutation is post-action reconcilable"
        )
    if (
        transition.action_id != action_id
        or transition.action != action
    ):
        raise PostActionReconciliationError(
            "Controller changed the action identity"
        )
    if before.target != after.target:
        raise PostActionReconciliationError(
            "target identity changed across the mutation"
        )
    if (
        after.frame.frame_id != before.frame.frame_id + 1
        or transition.post_frame_id != after.frame.frame_id
        or after.broker_state.turn_index
        != before.broker_state.turn_index
        or not after.broker_state.frame.authoritative
        or not after.broker_state.frame.fresh
        or after.broker_state.paused
        or after.broker_state.canceled
        or not after.broker_state.runtime_healthy
    ):
        raise PostActionReconciliationError(
            "Controller post frame is not current authoritative and fresh"
        )
    before_pointer = before.broker_state.pointer
    after_pointer = after.broker_state.pointer
    if (
        before_pointer.disposition is PointerDisposition.UNKNOWN
        or after_pointer.disposition is PointerDisposition.UNKNOWN
        or (
            before_pointer.disposition is PointerDisposition.HELD
            and (
                action != "drag"
                or not before_pointer.physically_owned
                or before_pointer.lease_id is None
            )
        )
    ):
        raise PostActionReconciliationError(
            "pointer state is not safely reconcilable"
        )
    if keep_holding:
        held_after = bool(
            after_pointer.disposition is PointerDisposition.HELD
            and after_pointer.physically_owned
            and after_pointer.lease_id is not None
        )
        same_continuation_lease = bool(
            before_pointer.disposition is PointerDisposition.RELEASED
            or after_pointer.lease_id == before_pointer.lease_id
        )
        if not held_after or not same_continuation_lease:
            raise PostActionReconciliationError(
                "held drag did not preserve one owned pointer lease"
            )
    elif (
        after_pointer.disposition is not PointerDisposition.RELEASED
        or after_pointer.lease_id is not None
        or after_pointer.physically_owned
    ):
        raise PostActionReconciliationError(
            "completed action did not release the pointer"
        )
    if (
        transition.post_frame_evidence_refs
        != after.frame.evidence_refs
    ):
        raise PostActionReconciliationError(
            "Controller and post frame evidence identities differ"
        )
    if evidence.mission_id != mission_id or evidence.run_id != run_id:
        raise PostActionReconciliationError(
            "EvidenceRegistry is outside mission/run"
        )
    refs_by_id = _registry_refs(evidence)
    for produced in transition.produced_evidence:
        previous = refs_by_id.get(produced.id)
        if previous is not None and previous != produced:
            raise PostActionReconciliationError(
                "Controller evidence conflicts with the run registry"
            )
        refs_by_id[produced.id] = produced
    event_ids = transition.event_evidence_refs
    if any(evidence_id not in refs_by_id for evidence_id in event_ids):
        raise PostActionReconciliationError(
            "Controller evidence is absent from the run registry"
        )
    post_payload_hash = frame_identity_payload_hash(after.frame.identity)
    post_refs = tuple(
        refs_by_id[evidence_id]
        for evidence_id in transition.post_frame_evidence_refs
    )
    if any(
        ref.mission_id != mission_id
        or ref.run_id != run_id
        or ref.obligation_id != obligation_id
        or ref.kind != POST_ACTION_FRAME_EVIDENCE_KIND
        or ref.trust != "deterministic"
        or ref.action_id != action_id
        or ref.frame_id != after.frame.frame_id
        or ref.payload_hash != post_payload_hash
        for ref in post_refs
    ):
        raise PostActionReconciliationError(
            "post-frame evidence does not bind the exact action and frame"
        )

    effect = transition.action_effect
    if effect is not None:
        before_ref = refs_by_id[effect.before_frame_evidence_ref]
        after_ref = refs_by_id[effect.after_frame_evidence_ref]
        effect_ref = refs_by_id[effect.evidence_ref]
        if effect.before_frame_evidence_ref not in before.frame.evidence_refs:
            raise PostActionReconciliationError(
                "action effect before evidence is outside pre-frame"
            )
        if effect.after_frame_evidence_ref not in after.frame.evidence_refs:
            raise PostActionReconciliationError(
                "action effect after evidence is outside post-frame"
            )
        if (
            effect.before_frame_id != before.frame.frame_id
            or effect.after_frame_id != after.frame.frame_id
            or effect.before_frame_payload_hash
            != frame_identity_payload_hash(before.frame.identity)
            or effect.after_frame_payload_hash
            != frame_identity_payload_hash(after.frame.identity)
            or effect.input_completed is not True
            or effect.target_binding_valid is not True
            or transition.action_effect_evidence != effect_ref
            or before_ref.kind
            not in {CURRENT_FRAME_EVIDENCE_KIND, POST_ACTION_FRAME_EVIDENCE_KIND}
            or before_ref.trust != "deterministic"
            or before_ref.frame_id != before.frame.frame_id
            or before_ref.payload_hash
            != frame_identity_payload_hash(before.frame.identity)
            or after_ref not in post_refs
            or effect_ref.kind != ACTION_EFFECT_EVIDENCE_KIND
            or effect_ref.trust != "deterministic"
            or effect_ref.obligation_id != obligation_id
            or effect_ref.action_id != action_id
            or effect_ref.frame_id != after.frame.frame_id
            or effect_ref.payload_hash != effect.payload_hash
        ):
            raise PostActionReconciliationError(
                "action effect evidence does not bind the canonical comparison"
            )
    return PostActionReconciliation(
        action_id=action_id,
        action=action,
        before_frame=before.frame.identity,
        after_frame=after.frame.identity,
        target=after.target,
        event_evidence_refs=event_ids,
        evidence_refs=tuple(refs_by_id[item] for item in event_ids),
    )


__all__ = [
    "PostActionReconciliation",
    "PostActionReconciliationError",
    "reconcile_post_action",
    "validate_controller_evidence_scope",
]
