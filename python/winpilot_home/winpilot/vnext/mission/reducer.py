"""Pure MissionReducer and deterministic event replay."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Iterable, Mapping

from .contracts import (
    ArtifactRef,
    EvidenceRef,
    EvidenceScope,
    FailureSummary,
    IllegalTransitionError,
    InvalidMissionEvent,
    MissionDefinition,
    MissionEvent,
    MissionProjection,
    MissionStatus,
    MissionCard,
    ObligationSpec,
    ObligationProjection,
    ObligationStatus,
    TargetBinding,
    TransitionSummary,
    VerifiedFact,
    EvidenceScopeError,
    DuplicateEventError,
    StaleRevisionError,
    _as_string_tuple,
    thaw,
)


_TERMINAL_MISSION_STATUSES = {
    MissionStatus.VERIFIED,
    MissionStatus.FAILED,
    MissionStatus.CANCELED,
}
_EXPLICIT_HOLD_EVENTS = {
    "mission_blocked",
    "obligation_blocked",
}
_HOLD_STATUSES = {
    MissionStatus.WAITING_APPROVAL,
    MissionStatus.WAITING_EXTERNAL,
    MissionStatus.AWAITING_MISSION_VERIFICATION,
    MissionStatus.BLOCKED,
}
_UNIMPLEMENTED_EVENTS = {
    "approval_requested",
    "approval_decided",
    "external_change_armed",
    "external_change_recorded",
    "planner_requested",
    "planner_patch_proposed",
    "planner_patch_accepted",
    "planner_patch_rejected",
    "inspection_requested",
    "inspection_recorded",
    "capability_requested",
    "capability_granted",
    "capability_denied",
    "execution_profile_accepted",
    "execution_profile_revised",
    "user_goal_revised",
}


def _payload_text(payload: Mapping[str, Any], key: str, default: str = "") -> str:
    return str(payload.get(key) or default)


def _initial_projection(definition: MissionDefinition) -> MissionProjection:
    projection = MissionProjection(
        definition=definition,
        revision=0,
        obligation_states=tuple(
            ObligationProjection(item.id, ObligationStatus.PENDING)
            for item in definition.obligations
        ),
        active_obligation_id=None,
        status=MissionStatus.RUNNING,
    )
    return _recompute(projection)


def _definition_for(projection: MissionProjection, obligation_id: str) -> ObligationSpec:
    try:
        return projection.obligation_definition(obligation_id)
    except KeyError as exc:
        raise IllegalTransitionError(f"unknown obligation: {obligation_id}") from exc


def _state_map(projection: MissionProjection) -> dict[str, ObligationProjection]:
    return {item.id: item for item in projection.obligation_states}


def _dependencies_verified(
    projection: MissionProjection,
    definition: ObligationSpec,
) -> bool:
    states = _state_map(projection)
    return all(states[dependency].status is ObligationStatus.VERIFIED for dependency in definition.depends_on)


def _ready(projection: MissionProjection, definition: ObligationSpec) -> bool:
    state = projection.obligation_state(definition.id)
    if state.status is not ObligationStatus.PENDING:
        return False
    if not _dependencies_verified(projection, definition):
        return False
    return all(ref in projection.artifact_bindings for ref in definition.input_artifact_refs)


def _first_ready(projection: MissionProjection) -> str | None:
    for obligation_id in projection.definition.stable_topological_order:
        definition = projection.obligation_definition(obligation_id)
        if _ready(projection, definition):
            return definition.id
    return None


def _required_verified(projection: MissionProjection) -> bool:
    states = _state_map(projection)
    return all(
        not definition.required
        or states[definition.id].status is ObligationStatus.VERIFIED
        for definition in projection.definition.obligations
    )


def _recompute(
    projection: MissionProjection,
    *,
    preserve_explicit_status: bool = False,
) -> MissionProjection:
    """Derive active obligation and mission completion from state facts."""
    if projection.status in _TERMINAL_MISSION_STATUSES:
        return projection
    if projection.status in _HOLD_STATUSES:
        return projection
    if preserve_explicit_status:
        return projection
    if projection.pending_approval is not None:
        return replace(projection, status=MissionStatus.WAITING_APPROVAL)
    if _required_verified(projection):
        # Obligation verification is not mission verification.  A separate,
        # run-scoped mission_verified event is the only path to VERIFIED.
        return replace(
            projection,
            active_obligation_id=None,
            status=MissionStatus.AWAITING_MISSION_VERIFICATION,
        )
    states = _state_map(projection)
    active_id = projection.active_obligation_id
    if active_id:
        active_state = states.get(active_id)
        if active_state and active_state.status in {
            ObligationStatus.ACTIVE,
            ObligationStatus.CLAIMED,
        }:
            return replace(projection, status=MissionStatus.RUNNING)
        active_id = None
    next_id = _first_ready(projection)
    if next_id:
        states[next_id] = replace(
            states[next_id],
            status=ObligationStatus.ACTIVE,
            activation_attempt_count=states[next_id].activation_attempt_count + 1,
        )
        return replace(
            projection,
            obligation_states=tuple(states[item.id] for item in projection.definition.obligations),
            active_obligation_id=next_id,
            status=MissionStatus.RUNNING,
        )
    if any(item.status is ObligationStatus.BLOCKED for item in states.values()):
        return replace(projection, active_obligation_id=None, status=MissionStatus.BLOCKED)
    pending_required = [
        item for item in projection.definition.obligations
        if item.required and states[item.id].status is ObligationStatus.PENDING
    ]
    if pending_required:
        return replace(projection, active_obligation_id=None, status=MissionStatus.WAITING_EXTERNAL)
    return replace(projection, active_obligation_id=None, status=MissionStatus.BLOCKED)


def _evidence_refs(
    projection: MissionProjection,
    scope: EvidenceScope,
    raw_ids: Iterable[str] | None,
    *,
    obligation_id: str | None = None,
    required: bool = False,
) -> tuple[EvidenceRef, ...]:
    ids = _as_string_tuple(raw_ids)
    if not ids:
        if required:
            raise EvidenceScopeError("event requires run-scoped evidence_refs")
        return ()
    refs = scope.require(ids, obligation_id=obligation_id)
    for ref in refs:
        if ref.id not in projection.evidence_refs:
            continue
    return refs


def _merge_evidence(
    projection: MissionProjection,
    refs: Iterable[EvidenceRef],
) -> MissionProjection:
    merged = dict(projection.evidence_refs)
    for ref in refs:
        merged[ref.id] = ref
    return replace(projection, evidence_refs=merged)


def _parse_artifact(payload: Mapping[str, Any]) -> ArtifactRef:
    raw = payload.get("artifact")
    if isinstance(raw, ArtifactRef):
        return raw
    if not isinstance(raw, Mapping):
        raw = payload
    return ArtifactRef.from_dict(raw)


def _parse_target(payload: Mapping[str, Any]) -> TargetBinding:
    raw = payload.get("target_binding")
    if isinstance(raw, TargetBinding):
        return raw
    if not isinstance(raw, Mapping):
        raw = payload
    return TargetBinding.from_dict(raw)


def _parse_transition(payload: Mapping[str, Any]) -> TransitionSummary:
    obligation_id = _payload_text(payload, "obligation_id")
    return TransitionSummary(
        action=_payload_text(payload, "action", "unknown"),
        input_completed=bool(payload.get("input_completed", False)),
        visible_effect=_payload_text(payload, "visible_effect"),
        evidence_refs=payload.get("evidence_refs") or (),
        frame_id=(int(payload["frame_id"]) if payload.get("frame_id") is not None else None),
        obligation_id=obligation_id,
    )


def _claim_fingerprint(
    projection: MissionProjection,
    obligation_id: str,
    evidence_ids: Iterable[str],
) -> str:
    """Derive a stable identity from runtime facts, never model prose.

    A claim is the observation/action boundary immediately preceding it.  The
    reducer therefore hashes the active obligation, the evidence IDs and the
    last transition summary.  Revision and event IDs are intentionally absent
    so replay and a duplicate delivery produce the same fingerprint.
    """
    canonical = {
        "obligation_id": obligation_id,
        "evidence_refs": list(_as_string_tuple(evidence_ids)),
        "last_transition": (
            projection.last_transition.to_dict()
            if projection.last_transition is not None
            and projection.last_transition.obligation_id == obligation_id
            else None
        ),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _event_evidence_ids(
    event: MissionEvent,
    payload: Mapping[str, Any],
    key: str = "evidence_refs",
) -> tuple[str, ...]:
    """Merge wire-level evidence_refs with the legacy payload location."""
    return tuple(dict.fromkeys((*event.evidence_refs, *_as_string_tuple(payload.get(key) or ()))))


def _parse_fact(raw: Any) -> VerifiedFact:
    if isinstance(raw, VerifiedFact):
        return raw
    if not isinstance(raw, Mapping):
        raise InvalidMissionEvent("verified fact must be an object")
    return VerifiedFact(
        id=str(raw.get("id") or ""),
        statement=str(raw.get("statement") or ""),
        evidence_refs=raw.get("evidence_refs") or (),
        scope=str(raw.get("scope") or "obligation"),
        valid_for_obligation_ids=raw.get("valid_for_obligation_ids") or (),
        expires_on_epoch_change=bool(raw.get("expires_on_epoch_change", True)),
    )


def _validate_fact_identity(
    projection: MissionProjection,
    scope: EvidenceScope,
    fact: VerifiedFact,
    *,
    obligation_id: str | None = None,
) -> tuple[EvidenceRef, ...]:
    """Validate fact identity and ensure every proof belongs to its scope."""
    known_ids = {item.id for item in projection.definition.obligations}
    unknown = set(fact.valid_for_obligation_ids) - known_ids
    if unknown:
        raise IllegalTransitionError(
            f"verified fact references unknown obligations: {sorted(unknown)}"
        )
    if obligation_id and fact.valid_for_obligation_ids and obligation_id not in fact.valid_for_obligation_ids:
        raise IllegalTransitionError("verified fact is not valid for the current obligation")
    refs = _evidence_refs(
        projection,
        scope,
        fact.evidence_refs,
        obligation_id=obligation_id,
        required=True,
    )
    valid_ids = set(fact.valid_for_obligation_ids)
    if valid_ids:
        invalid_refs = [
            ref.id
            for ref in refs
            if ref.obligation_id not in (None, "") and ref.obligation_id not in valid_ids
        ]
        if invalid_refs:
            raise EvidenceScopeError(
                f"verified fact evidence belongs to another obligation: {invalid_refs}"
            )
    return refs


def _append_fact(
    projection: MissionProjection,
    fact: VerifiedFact,
) -> MissionProjection:
    """Append a fact only when its stable identity has not been used."""
    for existing in projection.verified_facts:
        if existing.id != fact.id:
            continue
        if existing != fact:
            raise IllegalTransitionError("verified fact identity was reused with different evidence")
        raise DuplicateEventError("verified fact was already accepted")
    return replace(projection, verified_facts=projection.verified_facts + (fact,))


def _visual_verifier_requested(payload: Mapping[str, Any]) -> bool:
    verifier_kind = _payload_text(payload, "verifier_kind").casefold()
    return verifier_kind in {
        "visual",
        "visual_verifier",
        "independent_visual_model",
        "visual_model",
    } or (
        "visual" in verifier_kind
        and ("verif" in verifier_kind or "model" in verifier_kind)
    )


def _verification_exhausted_status(
    policy: str,
) -> tuple[ObligationStatus, MissionStatus, str]:
    """Map an uncertain verification policy to an explicit recovery state."""
    policy = str(policy)
    if policy == "fail":
        return ObligationStatus.BLOCKED, MissionStatus.FAILED, "verification_uncertain_failed"
    if policy == "blocked":
        return ObligationStatus.BLOCKED, MissionStatus.BLOCKED, "verification_uncertain_blocked"
    # There is no user/external event implementation in P1.5.  Keep the
    # mission visibly waiting instead of silently retrying or marking success.
    return ObligationStatus.ACTIVE, MissionStatus.WAITING_EXTERNAL, f"verification_uncertain_{policy}"


def _criteria_covered(
    required: Iterable[str],
    supplied: Iterable[str],
) -> bool:
    required_set = {" ".join(str(item).split()).casefold() for item in required if str(item).strip()}
    supplied_set = {" ".join(str(item).split()).casefold() for item in supplied if str(item).strip()}
    return required_set.issubset(supplied_set)


def _apply_event(
    projection: MissionProjection,
    event: MissionEvent,
    scope: EvidenceScope,
) -> MissionProjection:
    payload = event.payload
    event_type = event.kind
    states = _state_map(projection)

    if event_type == "mission_compiled":
        if projection.revision != 0:
            raise IllegalTransitionError("mission_compiled is only valid at revision zero")
        return projection

    if event_type == "obligation_activated":
        obligation_id = _payload_text(payload, "obligation_id")
        definition = _definition_for(projection, obligation_id)
        if projection.active_obligation_id not in (None, obligation_id):
            raise IllegalTransitionError("another obligation is already active")
        if projection.active_obligation_id == obligation_id:
            return projection
        if not _ready(projection, definition):
            raise IllegalTransitionError("obligation is not mechanically ready")
        states[obligation_id] = replace(
            states[obligation_id],
            status=ObligationStatus.ACTIVE,
            activation_attempt_count=states[obligation_id].activation_attempt_count + 1,
        )
        return replace(projection, obligation_states=tuple(states[item.id] for item in projection.definition.obligations), active_obligation_id=obligation_id)

    if event_type == "artifact_bound":
        artifact = _parse_artifact(payload)
        producer = _definition_for(projection, artifact.producer_obligation_id)
        artifact_evidence_ids = tuple(
            dict.fromkeys((*event.evidence_refs, artifact.evidence_ref))
        )
        refs = _evidence_refs(
            projection,
            scope,
            artifact_evidence_ids,
            obligation_id=producer.id,
            required=True,
        )
        if projection.obligation_state(producer.id).status is not ObligationStatus.VERIFIED:
            raise IllegalTransitionError("artifact producer obligation is not verified")
        artifact_ref = next(ref for ref in refs if ref.id == artifact.evidence_ref)
        if artifact_ref.artifact_id != artifact.artifact_id:
            raise EvidenceScopeError("artifact evidence does not identify the bound artifact")
        if artifact_ref.content_sha256 != artifact.sha256:
            raise EvidenceScopeError("artifact evidence content hash does not match the bound artifact")
        binding_name = _payload_text(payload, "binding_name", artifact.artifact_id)
        existing = projection.artifact_bindings.get(binding_name)
        if existing is not None and existing != artifact:
            raise IllegalTransitionError("artifact binding identity changed without invalidation")
        bindings = {**projection.artifact_bindings, binding_name: artifact}
        # Definitions normally refer to the artifact identity.  Keeping the
        # optional human binding name as a second index preserves both forms
        # without introducing another state owner.
        bindings.setdefault(artifact.artifact_id, artifact)
        # Binding the awaited external artifact is the explicit resume signal;
        # the next recompute may now activate its consumer.
        updated = replace(projection, artifact_bindings=bindings, status=MissionStatus.RUNNING)
        return _merge_evidence(updated, refs)

    if event_type == "target_bound":
        target = _parse_target(payload)
        target_evidence_ids = tuple(
            dict.fromkeys((*event.evidence_refs, target.established_by_evidence_ref))
        )
        refs = _evidence_refs(projection, scope, target_evidence_ids, required=True)
        established = next(ref for ref in refs if ref.id == target.established_by_evidence_ref)
        if established.artifact_id not in (None, ""):
            raise EvidenceScopeError("target evidence cannot identify an artifact")
        if projection.target_binding is not None:
            old = projection.target_binding
            if old.binding_id == target.binding_id and old != target:
                raise IllegalTransitionError("target binding identity changed without invalidation")
        return _merge_evidence(replace(projection, target_binding=target), refs)

    if event_type == "target_invalidated":
        return replace(projection, target_binding=None, last_error=_payload_text(payload, "reason"))

    if event_type == "transition_recorded":
        obligation_id = _payload_text(payload, "obligation_id", projection.active_obligation_id or "")
        if not obligation_id or projection.active_obligation_id != obligation_id:
            raise IllegalTransitionError("transition must be bound to the active obligation")
        _definition_for(projection, obligation_id)
        transition = _parse_transition({**payload, "obligation_id": obligation_id})
        transition_refs = _event_evidence_ids(event, payload)
        if transition_refs != transition.evidence_refs:
            transition = TransitionSummary(
                action=transition.action,
                input_completed=transition.input_completed,
                visible_effect=transition.visible_effect,
                evidence_refs=transition_refs,
                frame_id=transition.frame_id,
                obligation_id=obligation_id,
            )
        refs = _evidence_refs(projection, scope, transition_refs, obligation_id=obligation_id)
        return _merge_evidence(replace(projection, last_transition=transition), refs)

    if event_type == "obligation_claimed":
        obligation_id = _payload_text(payload, "obligation_id")
        _definition_for(projection, obligation_id)
        if projection.active_obligation_id != obligation_id:
            raise IllegalTransitionError("only the active obligation may be claimed")
        previous = states[obligation_id]
        if previous.status not in {ObligationStatus.ACTIVE, ObligationStatus.CLAIMED}:
            raise IllegalTransitionError("obligation is not claimable")
        evidence_ids = _event_evidence_ids(event, payload)
        refs = _evidence_refs(projection, scope, evidence_ids, obligation_id=obligation_id)
        fingerprint = _claim_fingerprint(projection, obligation_id, evidence_ids)
        if previous.claim_fingerprint == fingerprint:
            raise DuplicateEventError("the same claim fingerprint was already accepted")
        updated = replace(
            projection,
            obligation_states=tuple(
                replace(
                    item,
                    status=ObligationStatus.CLAIMED,
                    claim_evidence_refs=evidence_ids,
                    claim_fingerprint=fingerprint,
                )
                if item.id == obligation_id else item
                for item in projection.obligation_states
            ),
        )
        return _merge_evidence(updated, refs)

    if event_type == "verification_recorded":
        obligation_id = _payload_text(payload, "obligation_id", _payload_text(payload, "subject_id"))
        definition = _definition_for(projection, obligation_id)
        previous = states[obligation_id]
        if projection.active_obligation_id != obligation_id:
            raise IllegalTransitionError("only the active obligation may be verified")
        if previous.status not in {ObligationStatus.ACTIVE, ObligationStatus.CLAIMED}:
            raise IllegalTransitionError("obligation is not awaiting verification")
        if not previous.claim_fingerprint:
            raise IllegalTransitionError("verification must reference a runtime claim")
        supplied_fingerprint = _payload_text(payload, "claim_fingerprint")
        if supplied_fingerprint and supplied_fingerprint != previous.claim_fingerprint:
            raise StaleRevisionError("verification claim_fingerprint does not match the active claim")
        if previous.verified_claim_fingerprint == previous.claim_fingerprint:
            raise DuplicateEventError("the active claim fingerprint was already verified")
        policy = definition.evidence_policy
        if previous.verification_attempt_count >= policy.max_verification_attempts:
            raise IllegalTransitionError("maximum verification attempts exceeded")
        verified = bool(payload.get("verified", False))
        missing = _as_string_tuple(payload.get("missing_conditions") or ())
        contradictions = _as_string_tuple(payload.get("contradictions") or ())
        satisfied = _as_string_tuple(
            payload.get("satisfied_criteria")
            or payload.get("satisfied_conditions")
            or ()
        )
        refs = _evidence_refs(
            projection,
            scope,
            _event_evidence_ids(event, payload),
            obligation_id=obligation_id,
            required=verified,
        )
        visual_refs = tuple(ref for ref in refs if ref.trust == "independent_visual_model")
        visual_claim = _visual_verifier_requested(payload)
        if visual_claim and not visual_refs:
            raise EvidenceScopeError(
                "visual verifier claim is not backed by independent_visual_model evidence trust"
            )
        if visual_refs and not policy.allow_independent_visual_verifier:
            raise EvidenceScopeError("visual verification is not permitted by the evidence policy")
        updated = _merge_evidence(projection, refs)
        attempt_count = previous.verification_attempt_count + 1
        if verified:
            verification_id = _payload_text(payload, "verification_id")
            if not verification_id:
                raise IllegalTransitionError("verified result requires verification_id")
            if verification_id in {
                state.verification_id
                for state in states.values()
                if state.verification_id
            } or verification_id == projection.mission_verification_id:
                raise DuplicateEventError("verification_id was already accepted")
            if not _criteria_covered(definition.acceptance_criteria, satisfied):
                raise IllegalTransitionError(
                    "verification does not mechanically cover every acceptance criterion"
                )
            if missing or contradictions:
                raise IllegalTransitionError("verified result still has missing or contradictory conditions")
            if len(refs) < definition.evidence_policy.minimum_evidence:
                raise EvidenceScopeError(
                    f"verification requires at least {definition.evidence_policy.minimum_evidence} evidence ref(s)"
                )
            allowed = set(definition.evidence_policy.allowed_trust)
            disallowed = [ref.id for ref in refs if ref.trust not in allowed]
            if disallowed:
                raise EvidenceScopeError(f"verification evidence is not allowed: {disallowed}")
            required_kinds = set(definition.evidence_policy.required_evidence_kinds)
            if required_kinds and not required_kinds.issubset({ref.kind for ref in refs}):
                raise EvidenceScopeError(
                    "verification evidence does not cover required evidence kinds"
                )
            facts: list[VerifiedFact] = []
            for raw in payload.get("facts") or ():
                fact = _parse_fact(raw)
                fact_refs = _validate_fact_identity(
                    updated,
                    scope,
                    fact,
                    obligation_id=obligation_id,
                )
                updated = _merge_evidence(updated, fact_refs)
                facts.append(fact)
            with_facts = updated
            for fact in facts:
                with_facts = _append_fact(with_facts, fact)
            return replace(
                with_facts,
                obligation_states=tuple(
                    replace(
                        item,
                        status=ObligationStatus.VERIFIED,
                        verified_evidence_refs=tuple(ref.id for ref in refs),
                        verification_id=verification_id,
                        verified_claim_fingerprint=previous.claim_fingerprint,
                        verification_attempt_count=attempt_count,
                        last_reason=_payload_text(payload, "verifier_kind", "verified"),
                    )
                    if item.id == obligation_id else item
                    for item in with_facts.obligation_states
                ),
                failure_summary=None,
            )
        reason = _payload_text(payload, "reason") or ", ".join(missing or contradictions) or "verification rejected"
        failure = FailureSummary(
            kind=_payload_text(payload, "failure_kind", "verification_rejected"),
            message=reason,
            evidence_refs=tuple(ref.id for ref in refs),
            count=previous.failure_count + 1,
            obligation_id=obligation_id,
        )
        exhausted = attempt_count >= policy.max_verification_attempts
        if exhausted:
            next_obligation_status, next_mission_status, policy_reason = _verification_exhausted_status(
                policy.uncertain_policy
            )
        else:
            next_obligation_status, next_mission_status, policy_reason = (
                ObligationStatus.ACTIVE,
                MissionStatus.RUNNING,
                "",
            )
        if policy_reason:
            reason = f"{reason}; {policy_reason}"
            failure = replace(failure, message=reason)
        return replace(
            updated,
            obligation_states=tuple(
                replace(
                    item,
                    status=next_obligation_status,
                    failure_count=item.failure_count + 1,
                    verified_claim_fingerprint=previous.claim_fingerprint,
                    verification_attempt_count=attempt_count,
                    last_reason=reason,
                )
                if item.id == obligation_id else item
                for item in updated.obligation_states
            ),
            failure_summary=failure,
            active_obligation_id=(obligation_id if next_obligation_status is ObligationStatus.ACTIVE else None),
            status=next_mission_status,
        )

    if event_type == "fact_recorded":
        fact = _parse_fact(payload.get("fact") or payload)
        wire_refs = _as_string_tuple(event.evidence_refs)
        payload_refs = _as_string_tuple(fact.evidence_refs)
        if wire_refs and payload_refs and set(wire_refs) != set(payload_refs):
            raise EvidenceScopeError("fact evidence_refs disagree with event evidence_refs")
        fact_refs = tuple(dict.fromkeys((*wire_refs, *payload_refs)))
        if fact_refs != fact.evidence_refs:
            fact = VerifiedFact(
                id=fact.id,
                statement=fact.statement,
                evidence_refs=fact_refs,
                scope=fact.scope,
                valid_for_obligation_ids=fact.valid_for_obligation_ids,
                expires_on_epoch_change=fact.expires_on_epoch_change,
            )
        obligation_id = _payload_text(payload, "obligation_id", projection.active_obligation_id or "")
        fact_obligation_id = obligation_id if obligation_id else None
        refs = _validate_fact_identity(
            projection,
            scope,
            fact,
            obligation_id=fact_obligation_id,
        )
        return _append_fact(_merge_evidence(projection, refs), fact)

    if event_type == "obligation_blocked":
        obligation_id = _payload_text(payload, "obligation_id")
        _definition_for(projection, obligation_id)
        if projection.active_obligation_id != obligation_id:
            raise IllegalTransitionError("only the active obligation may be blocked")
        if states[obligation_id].status not in {ObligationStatus.ACTIVE, ObligationStatus.CLAIMED}:
            raise IllegalTransitionError("only active obligation may be blocked")
        reason = _payload_text(payload, "reason", "obligation blocked")
        return replace(
            projection,
            active_obligation_id=None,
            obligation_states=tuple(
                replace(item, status=ObligationStatus.BLOCKED, last_reason=reason)
                if item.id == obligation_id else item
                for item in projection.obligation_states
            ),
            failure_summary=FailureSummary(
                _payload_text(payload, "blocker_kind", "blocked"),
                reason,
                obligation_id=obligation_id,
            ),
            status=MissionStatus.BLOCKED,
        )

    if event_type == "obligation_unblocked":
        obligation_id = _payload_text(payload, "obligation_id")
        definition = _definition_for(projection, obligation_id)
        if projection.active_obligation_id not in (None, obligation_id):
            raise IllegalTransitionError("another obligation is already active")
        if states[obligation_id].status is not ObligationStatus.BLOCKED or not _dependencies_verified(projection, definition):
            raise IllegalTransitionError("blocked obligation cannot be resumed from current facts")
        return replace(
            projection,
            obligation_states=tuple(
                replace(
                    item,
                    status=ObligationStatus.ACTIVE,
                    activation_attempt_count=item.activation_attempt_count + 1,
                )
                if item.id == obligation_id else item
                for item in projection.obligation_states
            ),
            active_obligation_id=obligation_id,
            status=MissionStatus.RUNNING,
            failure_summary=None,
        )

    if event_type == "mission_blocked":
        return replace(
            projection,
            active_obligation_id=None,
            status=MissionStatus.BLOCKED,
            last_error=_payload_text(payload, "reason", "mission blocked"),
        )

    if event_type == "mission_failed":
        return replace(projection, status=MissionStatus.FAILED, last_error=_payload_text(payload, "reason", "mission failed"))

    if event_type == "mission_canceled":
        return replace(projection, status=MissionStatus.CANCELED, last_error=_payload_text(payload, "reason", "mission canceled"))

    if event_type == "mission_verified":
        if projection.status is not MissionStatus.AWAITING_MISSION_VERIFICATION:
            raise IllegalTransitionError(
                "mission_verified requires awaiting_mission_verification"
            )
        if not _required_verified(projection):
            raise IllegalTransitionError(
                "mission_verified requires every required obligation to be verified"
            )
        if "verified" in payload and not bool(payload.get("verified")):
            raise IllegalTransitionError("mission_verified cannot carry a false verdict")
        verification_id = _payload_text(payload, "verification_id")
        if not verification_id:
            raise IllegalTransitionError("mission_verified requires an independent verification_id")
        if verification_id in {
            state.verification_id
            for state in states.values()
            if state.verification_id
        } or verification_id == projection.mission_verification_id:
            raise DuplicateEventError("mission verification identity was already accepted")
        evidence_ids = _event_evidence_ids(event, payload)
        refs = _evidence_refs(projection, scope, evidence_ids, required=True)
        # A mission verifier may aggregate obligation evidence, but it may not
        # use an actor/model claim as its only identity proof.  The evidence
        # must still be in this exact mission/run scope (checked above).
        disallowed = [
            ref.id
            for ref in refs
            if ref.trust in {"actor_claim", "model_self", "telemetry_only"}
            or ref.kind in {"claim", "model_claim"}
        ]
        if disallowed:
            raise EvidenceScopeError(
                f"mission verification evidence is not independent: {disallowed}"
            )
        return replace(
            _merge_evidence(projection, refs),
            status=MissionStatus.VERIFIED,
            mission_verification_id=verification_id,
        )

    raise InvalidMissionEvent(f"unsupported mission event type: {event_type}")


def reduce_projection(
    projection: MissionProjection,
    event: MissionEvent,
    *,
    evidence: EvidenceScope | Iterable[EvidenceRef] | None = None,
) -> MissionProjection:
    """Pure state transition.  The input projection is never mutated."""
    if event.mission_id != projection.mission_id or event.run_id != projection.run_id:
        raise InvalidMissionEvent("event does not belong to the projection mission/run")
    if event.kind in _UNIMPLEMENTED_EVENTS:
        raise InvalidMissionEvent(
            f"mission event is not implemented in P1: {event.kind}"
        )
    if event.event_id in projection.accepted_event_ids:
        raise DuplicateEventError(f"event already accepted: {event.event_id}")
    if event.base_revision != projection.revision:
        raise StaleRevisionError(
            f"stale mission revision: event={event.base_revision}, current={projection.revision}"
        )
    if projection.status in _TERMINAL_MISSION_STATUSES:
        raise IllegalTransitionError(f"mission is terminal: {projection.status.value}")
    if (
        projection.status is MissionStatus.AWAITING_MISSION_VERIFICATION
        and event.kind != "mission_verified"
    ):
        raise IllegalTransitionError(
            "only an independent mission_verified event may close the mission"
        )
    scope = EvidenceScope.from_source(
        projection.mission_id, projection.run_id, evidence,
    )
    if event.evidence_refs:
        scope.require(event.evidence_refs)
    candidate = _apply_event(projection, event, scope)
    candidate = replace(
        candidate,
        revision=projection.revision + 1,
        accepted_event_ids=projection.accepted_event_ids + (event.event_id,),
    )
    return _recompute(
        candidate,
        preserve_explicit_status=event.kind in _EXPLICIT_HOLD_EVENTS,
    )


class MissionReducer:
    """Small event-sourced reducer with an explicit immutable evidence scope."""

    def __init__(
        self,
        definition: MissionDefinition,
        *,
        evidence: Any = None,
    ) -> None:
        self.definition = definition
        self._evidence_source = evidence
        self._projection = _initial_projection(definition)
        self._events: list[MissionEvent] = []

    @property
    def evidence_scope(self) -> EvidenceScope:
        """Resolve the current external registry snapshot, never a startup copy."""
        return EvidenceScope.from_source(
            self.definition.mission_id,
            self.definition.run_id,
            self._evidence_source,
        )

    @property
    def projection(self) -> MissionProjection:
        return self._projection

    @property
    def events(self) -> tuple[MissionEvent, ...]:
        return tuple(self._events)

    @property
    def card(self) -> MissionCard:
        return MissionCard.from_projection(self._projection)

    def apply(self, event: MissionEvent | Mapping[str, Any]) -> MissionProjection:
        if not isinstance(event, MissionEvent):
            event = MissionEvent.from_dict(event)
        next_projection = reduce_projection(
            self._projection,
            event,
            evidence=self._evidence_source,
        )
        self._projection = next_projection
        self._events.append(event)
        return next_projection

    # ``reduce`` is intentionally an alias with no separate semantics; it is
    # useful to callers that think of the reducer as a state machine function.
    reduce = apply

    def apply_many(self, events: Iterable[MissionEvent | Mapping[str, Any]]) -> MissionProjection:
        for event in events:
            self.apply(event)
        return self._projection

    def next_ready_obligation_id(self) -> str | None:
        if self._projection.active_obligation_id:
            return self._projection.active_obligation_id
        return _first_ready(self._projection)

    def event(
        self,
        event_type: str,
        *,
        payload: Mapping[str, Any] | None = None,
        base_revision: int | None = None,
        evidence_refs: Iterable[str] | None = None,
        created_at: float | None = None,
    ) -> MissionEvent:
        return MissionEvent.create(
            event_type,
            self.definition,
            base_revision=self._projection.revision if base_revision is None else base_revision,
            payload=payload,
            evidence_refs=evidence_refs,
            created_at=created_at,
        )

    @classmethod
    def replay(
        cls,
        definition: MissionDefinition,
        events: Iterable[MissionEvent | Mapping[str, Any]],
        *,
        evidence: EvidenceScope | Iterable[EvidenceRef] | None = None,
    ) -> "MissionReducer":
        reducer = cls(definition, evidence=evidence)
        reducer.apply_many(events)
        return reducer


def replay(
    definition: MissionDefinition,
    events: Iterable[MissionEvent | Mapping[str, Any]],
    *,
    evidence: EvidenceScope | Iterable[EvidenceRef] | None = None,
) -> MissionProjection:
    """Replay accepted events and return only the derived projection."""
    return MissionReducer.replay(definition, events, evidence=evidence).projection


__all__ = ["MissionReducer", "reduce_projection", "replay"]
