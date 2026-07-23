"""Stateless acceptance of one already-observed target acquisition."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json

from ..evidence import EvidenceRegistry
from ..mission import EvidenceRef, MissionReducer, TargetBinding
from ..mission import target_binding_attestation
from .post_action_reconcile import (
    PostActionReconciliation,
    PostActionTransition,
    reconcile_post_action,
)
from .ports import RuntimeStatePort
from .target_activation import (
    ActivatedTargetIdentity,
    TargetActivationTransition,
)
from .target_launch import TargetLaunchTransition


@dataclass(frozen=True, slots=True)
class TargetBindingCommit:
    binding: TargetBinding
    evidence: EvidenceRef
    event_id: str
    post_action: PostActionReconciliation


def target_binding_evidence_id(
    mission_id: str,
    run_id: str,
    acquisition_action_id: str,
    target: ActivatedTargetIdentity,
) -> str:
    if not isinstance(target, ActivatedTargetIdentity):
        raise TypeError("target must be ActivatedTargetIdentity")
    identities = (mission_id, run_id, acquisition_action_id)
    if any(type(value) is not str or not value.strip() for value in identities):
        raise ValueError("target binding identities must be non-empty strings")
    encoded = json.dumps(
        {
            "format": "vnext-target-binding-evidence-id/1",
            "mission_id": mission_id,
            "run_id": run_id,
            "acquisition_action_id": acquisition_action_id,
            "hwnd": target.hwnd,
            "root_hwnd": target.root_hwnd,
            "process_id": target.process_id,
            "process_identity": target.process_identity,
        },
        ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    )
    return "evidence-target-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def commit_target_binding(
    *,
    reducer: MissionReducer,
    registry: EvidenceRegistry,
    state: RuntimeStatePort,
    mission_id: str,
    run_id: str,
    base_revision: int,
    obligation_id: str,
    acquisition_action_id: str,
    target: ActivatedTargetIdentity,
    transition: PostActionTransition,
    authority_epoch: int,
    turn_index: int,
) -> TargetBindingCommit:
    """Read current authority, append one attestation, then accept one event."""
    if not isinstance(reducer, MissionReducer):
        raise TypeError("reducer must be MissionReducer")
    if type(registry) is not EvidenceRegistry or registry is not reducer.evidence_source:
        raise ValueError("target binding requires the Reducer's canonical EvidenceRegistry")
    if not callable(getattr(state, "current", None)):
        raise TypeError("state must provide current()")
    if not isinstance(target, ActivatedTargetIdentity):
        raise TypeError("target must use the canonical contract")
    if not isinstance(
        transition,
        (TargetActivationTransition, TargetLaunchTransition),
    ):
        raise TypeError("transition must be a target acquisition transition")
    if type(authority_epoch) is not int or authority_epoch < 0:
        raise ValueError("authority_epoch must be a non-negative integer")
    if (
        transition.action_id != acquisition_action_id
        or transition.activated_target != target
        or transition.post_frame_id is None
    ):
        raise ValueError("target acquisition transition identity is inconsistent")

    mission = reducer.projection
    if (
        mission.mission_id != mission_id
        or mission.run_id != run_id
        or mission.revision != base_revision
        or mission.active_obligation_id != obligation_id
        or mission.target_binding is not None
    ):
        raise ValueError("target binding authority is stale or inconsistent")

    binding, evidence = target_binding_attestation(
        mission_id=mission_id,
        run_id=run_id,
        obligation_id=obligation_id,
        evidence_id=target_binding_evidence_id(
            mission_id,
            run_id,
            acquisition_action_id,
            target,
        ),
        acquisition_action_id=acquisition_action_id,
        hwnd=target.hwnd,
        root_hwnd=target.root_hwnd,
        process_id=target.process_id,
        process_identity=target.process_identity,
        title_snapshot=target.title_snapshot,
        epoch=authority_epoch,
        frame_id=transition.post_frame_id,
    )
    shadow_mission = replace(mission, target_binding=binding)
    post = state.current(shadow_mission, turn_index=turn_index)
    current_target = post.target
    if (
        post.broker_state.turn_index != turn_index
        or not post.broker_state.frame.authoritative
        or not post.broker_state.frame.fresh
        or post.frame is None
        or post.frame.frame_id != transition.post_frame_id
        or post.frame.epoch != authority_epoch
        or post.frame.hwnd != target.hwnd
        or post.frame.region != target.window_region
        or post.frame.scope != "target_window"
        or current_target is None
        or current_target.binding_id != binding.binding_id
        or current_target.hwnd != target.hwnd
        or current_target.root_hwnd != target.root_hwnd
        or current_target.process_id != target.process_id
        or current_target.process_identity != target.process_identity
        or current_target.epoch != authority_epoch
        or current_target.window_region != target.window_region
    ):
        raise ValueError("post frame is not current target authority")
    assert post.frame is not None
    post_action = reconcile_post_action(
        transition,
        post,
        registry,
        mission_id=mission_id,
        run_id=run_id,
        obligation_id=obligation_id,
        action_id=acquisition_action_id,
        action=transition.action,
    )

    current = reducer.projection
    if (
        current.revision != base_revision
        or current.active_obligation_id != obligation_id
        or current.target_binding is not None
    ):
        raise ValueError("Mission authority changed before target binding commit")
    event = reducer.event(
        "target_bound",
        payload={"target_binding": binding.to_dict()},
        evidence_refs=(evidence.id,),
        base_revision=base_revision,
    )
    registry.append(evidence)
    reducer.apply(event)
    if reducer.projection.target_binding != binding:
        raise ValueError("Reducer did not derive the accepted target binding")
    commit = TargetBindingCommit(binding, evidence, event.event_id, post_action)
    return commit


__all__ = ["TargetBindingCommit", "commit_target_binding", "target_binding_evidence_id"]
