"""Pure MissionReducer and deterministic event replay."""
from __future__ import annotations

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
    ObligationDefinition,
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
    MissionStatus.FAILED,
    MissionStatus.CANCELED,
}
_NON_MUTATING_EVENTS = {"mission_compiled", "obligation_activated", "mission_verified"}


def _payload_text(payload: Mapping[str, Any], key: str, default: str = "") -> str:
    return str(payload.get(key) or default)


def _payload_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    return value if isinstance(value, Mapping) else {}


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


def _definition_for(projection: MissionProjection, obligation_id: str) -> ObligationDefinition:
    try:
        return projection.obligation_definition(obligation_id)
    except KeyError as exc:
        raise IllegalTransitionError(f"unknown obligation: {obligation_id}") from exc


def _state_map(projection: MissionProjection) -> dict[str, ObligationProjection]:
    return {item.id: item for item in projection.obligation_states}


def _dependencies_verified(
    projection: MissionProjection,
    definition: ObligationDefinition,
) -> bool:
    states = _state_map(projection)
    return all(states[dependency].status is ObligationStatus.VERIFIED for dependency in definition.depends_on)


def _ready(projection: MissionProjection, definition: ObligationDefinition) -> bool:
    state = projection.obligation_state(definition.id)
    if state.status is not ObligationStatus.PENDING:
        return False
    if not _dependencies_verified(projection, definition):
        return False
    return all(ref in projection.artifact_bindings for ref in definition.input_artifact_refs)


def _first_ready(projection: MissionProjection) -> str | None:
    for definition in projection.definition.obligations:
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


def _recompute(projection: MissionProjection) -> MissionProjection:
    """Derive active obligation and mission completion from state facts."""
    if projection.status in _TERMINAL_MISSION_STATUSES:
        return projection
    if projection.pending_approval is not None:
        return replace(projection, status=MissionStatus.WAITING_APPROVAL)
    if _required_verified(projection):
        return replace(projection, active_obligation_id=None, status=MissionStatus.VERIFIED)
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
        states[next_id] = replace(states[next_id], status=ObligationStatus.ACTIVE)
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
    return TransitionSummary(
        action=_payload_text(payload, "action", "unknown"),
        input_completed=bool(payload.get("input_completed", False)),
        visible_effect=_payload_text(payload, "visible_effect"),
        evidence_refs=payload.get("evidence_refs") or (),
        frame_id=(int(payload["frame_id"]) if payload.get("frame_id") is not None else None),
    )


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


def _validate_patch_payload(payload: Mapping[str, Any]) -> None:
    """Reject route-shaped planner patches before they can reach state."""
    forbidden = {
        "steps", "actions", "clicks", "coordinates", "selectors", "tool_order",
        "route", "workflow", "ui_steps", "micro_steps",
    }
    if any(payload.get(key) for key in forbidden):
        raise IllegalTransitionError("mission patch contains GUI route details")
    for raw in payload.get("obligations_to_add") or ():
        if not isinstance(raw, Mapping):
            raise IllegalTransitionError("obligation draft must be an object")
        if any(raw.get(key) for key in forbidden):
            raise IllegalTransitionError("obligation draft contains GUI route details")


def _apply_event(
    projection: MissionProjection,
    event: MissionEvent,
    scope: EvidenceScope,
) -> MissionProjection:
    payload = event.payload
    event_type = event.type
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
        states[obligation_id] = replace(states[obligation_id], status=ObligationStatus.ACTIVE)
        return replace(projection, obligation_states=tuple(states[item.id] for item in projection.definition.obligations), active_obligation_id=obligation_id)

    if event_type == "artifact_bound":
        artifact = _parse_artifact(payload)
        producer = _definition_for(projection, artifact.producer_obligation_id)
        if not artifact.artifact_id or not artifact.evidence_ref:
            raise IllegalTransitionError("artifact binding requires identity and evidence_ref")
        refs = _evidence_refs(
            projection, scope, (artifact.evidence_ref,), obligation_id=producer.id, required=True,
        )
        if projection.obligation_state(producer.id).status is not ObligationStatus.VERIFIED:
            raise IllegalTransitionError("artifact producer obligation is not verified")
        binding_name = _payload_text(payload, "binding_name", artifact.artifact_id)
        bindings = {**projection.artifact_bindings, binding_name: artifact}
        # Definitions normally refer to the artifact identity.  Keeping the
        # optional human binding name as a second index preserves both forms
        # without introducing another state owner.
        bindings.setdefault(artifact.artifact_id, artifact)
        updated = replace(projection, artifact_bindings=bindings)
        return _merge_evidence(updated, refs)

    if event_type == "target_bound":
        target = _parse_target(payload)
        refs = _evidence_refs(
            projection, scope, (target.established_by_evidence_ref,), required=True,
        )
        if not target.binding_id or target.hwnd < 0 or target.epoch < 0:
            raise IllegalTransitionError("invalid target binding")
        return _merge_evidence(replace(projection, target_binding=target), refs)

    if event_type == "target_invalidated":
        return replace(projection, target_binding=None, last_error=_payload_text(payload, "reason"))

    if event_type == "transition_recorded":
        transition = _parse_transition(payload)
        refs = _evidence_refs(projection, scope, transition.evidence_refs)
        return _merge_evidence(replace(projection, last_transition=transition), refs)

    if event_type == "obligation_claimed":
        obligation_id = _payload_text(payload, "obligation_id")
        _definition_for(projection, obligation_id)
        if projection.active_obligation_id != obligation_id:
            raise IllegalTransitionError("only the active obligation may be claimed")
        previous = states[obligation_id]
        if previous.status not in {ObligationStatus.ACTIVE, ObligationStatus.CLAIMED}:
            raise IllegalTransitionError("obligation is not claimable")
        evidence_ids = _as_string_tuple(payload.get("evidence_refs") or ())
        if previous.status is ObligationStatus.CLAIMED and evidence_ids and set(evidence_ids) <= set(previous.claim_evidence_refs):
            raise DuplicateEventError("the same obligation evidence was already claimed")
        refs = _evidence_refs(projection, scope, evidence_ids, obligation_id=obligation_id)
        updated = replace(
            projection,
            obligation_states=tuple(
                replace(item, status=ObligationStatus.CLAIMED, claim_evidence_refs=evidence_ids)
                if item.id == obligation_id else item
                for item in projection.obligation_states
            ),
        )
        return _merge_evidence(updated, refs)

    if event_type == "verification_recorded":
        obligation_id = _payload_text(payload, "obligation_id", _payload_text(payload, "subject_id"))
        definition = _definition_for(projection, obligation_id)
        previous = states[obligation_id]
        if previous.status not in {ObligationStatus.ACTIVE, ObligationStatus.CLAIMED}:
            raise IllegalTransitionError("obligation is not awaiting verification")
        verified = bool(payload.get("verified", False))
        missing = _as_string_tuple(payload.get("missing_conditions") or ())
        contradictions = _as_string_tuple(payload.get("contradictions") or ())
        refs = _evidence_refs(
            projection,
            scope,
            payload.get("evidence_refs") or (),
            obligation_id=obligation_id,
            required=verified,
        )
        updated = _merge_evidence(projection, refs)
        if verified:
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
            facts: list[VerifiedFact] = []
            for raw in payload.get("facts") or ():
                fact = _parse_fact(raw)
                if fact.evidence_refs:
                    _evidence_refs(updated, scope, fact.evidence_refs, obligation_id=obligation_id, required=True)
                facts.append(fact)
            return replace(
                updated,
                obligation_states=tuple(
                    replace(
                        item,
                        status=ObligationStatus.VERIFIED,
                        verified_evidence_refs=tuple(ref.id for ref in refs),
                        last_reason=_payload_text(payload, "verifier_kind", "verified"),
                    )
                    if item.id == obligation_id else item
                    for item in updated.obligation_states
                ),
                verified_facts=updated.verified_facts + tuple(facts),
                failure_summary=None,
            )
        reason = _payload_text(payload, "reason") or ", ".join(missing or contradictions) or "verification rejected"
        failure = FailureSummary(
            kind=_payload_text(payload, "failure_kind", "verification_rejected"),
            message=reason,
            evidence_refs=tuple(ref.id for ref in refs),
            count=previous.failure_count + 1,
        )
        return replace(
            updated,
            obligation_states=tuple(
                replace(item, status=ObligationStatus.ACTIVE, failure_count=item.failure_count + 1, last_reason=reason)
                if item.id == obligation_id else item
                for item in updated.obligation_states
            ),
            failure_summary=failure,
            status=MissionStatus.RUNNING,
        )

    if event_type == "fact_recorded":
        fact = _parse_fact(payload.get("fact") or payload)
        refs = _evidence_refs(projection, scope, fact.evidence_refs, required=True)
        return _merge_evidence(
            replace(projection, verified_facts=projection.verified_facts + (fact,)), refs,
        )

    if event_type == "obligation_blocked":
        obligation_id = _payload_text(payload, "obligation_id")
        _definition_for(projection, obligation_id)
        if states[obligation_id].status not in {ObligationStatus.ACTIVE, ObligationStatus.CLAIMED}:
            raise IllegalTransitionError("only active obligation may be blocked")
        reason = _payload_text(payload, "reason", "obligation blocked")
        return replace(
            projection,
            obligation_states=tuple(
                replace(item, status=ObligationStatus.BLOCKED, last_reason=reason)
                if item.id == obligation_id else item
                for item in projection.obligation_states
            ),
            failure_summary=FailureSummary(_payload_text(payload, "blocker_kind", "blocked"), reason),
            status=MissionStatus.BLOCKED,
        )

    if event_type == "obligation_unblocked":
        obligation_id = _payload_text(payload, "obligation_id")
        definition = _definition_for(projection, obligation_id)
        if states[obligation_id].status is not ObligationStatus.BLOCKED or not _dependencies_verified(projection, definition):
            raise IllegalTransitionError("blocked obligation cannot be resumed from current facts")
        return replace(
            projection,
            obligation_states=tuple(
                replace(item, status=ObligationStatus.ACTIVE)
                if item.id == obligation_id else item
                for item in projection.obligation_states
            ),
            active_obligation_id=obligation_id,
            status=MissionStatus.RUNNING,
            failure_summary=None,
        )

    if event_type == "approval_requested":
        approval = _payload_mapping(payload, "approval") or dict(payload)
        return replace(projection, pending_approval=approval, status=MissionStatus.WAITING_APPROVAL)

    if event_type == "approval_decided":
        approved = bool(payload.get("approved", False))
        if not approved:
            return replace(projection, pending_approval=None, status=MissionStatus.BLOCKED, last_error="approval denied")
        return replace(projection, pending_approval=None, status=MissionStatus.RUNNING)

    if event_type == "external_change_armed":
        return replace(projection, status=MissionStatus.WAITING_EXTERNAL)

    if event_type == "external_change_recorded":
        return replace(projection, status=MissionStatus.RUNNING, failure_summary=None)

    if event_type == "mission_blocked":
        return replace(projection, status=MissionStatus.BLOCKED, last_error=_payload_text(payload, "reason", "mission blocked"))

    if event_type == "mission_failed":
        return replace(projection, status=MissionStatus.FAILED, last_error=_payload_text(payload, "reason", "mission failed"))

    if event_type == "mission_canceled":
        return replace(projection, status=MissionStatus.CANCELED, last_error=_payload_text(payload, "reason", "mission canceled"))

    if event_type == "mission_verified":
        if not _required_verified(projection):
            raise IllegalTransitionError("mission_verified is not derivable from required obligations")
        return replace(projection, status=MissionStatus.VERIFIED)

    if event_type in {"planner_patch_proposed", "planner_patch_rejected"}:
        _validate_patch_payload(payload)
        return projection

    if event_type == "planner_patch_accepted":
        _validate_patch_payload(payload)
        additions = tuple(
            item if isinstance(item, ObligationDefinition) else ObligationDefinition.from_dict(item)
            for item in (payload.get("obligations_to_add") or ())
        )
        existing_ids = {item.id for item in projection.definition.obligations}
        if existing_ids.intersection(item.id for item in additions):
            raise IllegalTransitionError("planner patch adds an existing obligation id")
        new_definition = replace(
            projection.definition,
            obligations=projection.definition.obligations + additions,
        )
        new_states = projection.obligation_states + tuple(
            ObligationProjection(item.id, ObligationStatus.PENDING) for item in additions
        )
        return replace(projection, definition=new_definition, obligation_states=new_states)

    if event_type == "user_goal_revised":
        new_goal = _payload_text(payload, "original_goal")
        new_revision = int(payload.get("goal_revision", projection.definition.goal_revision + 1))
        if not new_goal or new_revision <= projection.definition.goal_revision:
            raise IllegalTransitionError("goal revision must advance and preserve original_goal")
        new_definition = replace(
            projection.definition,
            original_goal=new_goal,
            goal_revision=new_revision,
        )
        return replace(projection, definition=new_definition, status=MissionStatus.RUNNING)

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
    if event.event_id in projection.accepted_event_ids:
        raise DuplicateEventError(f"event already accepted: {event.event_id}")
    if event.base_revision != projection.revision:
        raise StaleRevisionError(
            f"stale mission revision: event={event.base_revision}, current={projection.revision}"
        )
    if projection.status in _TERMINAL_MISSION_STATUSES and event.type not in {"mission_canceled"}:
        raise IllegalTransitionError(f"mission is terminal: {projection.status.value}")
    scope = evidence if isinstance(evidence, EvidenceScope) else EvidenceScope.from_refs(
        projection.mission_id, projection.run_id, evidence,
    )
    candidate = _apply_event(projection, event, scope)
    candidate = replace(
        candidate,
        revision=projection.revision + 1,
        accepted_event_ids=projection.accepted_event_ids + (event.event_id,),
    )
    return _recompute(candidate)


class MissionReducer:
    """Small event-sourced reducer with an explicit immutable evidence scope."""

    def __init__(
        self,
        definition: MissionDefinition,
        *,
        evidence: EvidenceScope | Iterable[EvidenceRef] | None = None,
    ) -> None:
        self.definition = definition
        self.evidence_scope = evidence if isinstance(evidence, EvidenceScope) else EvidenceScope.from_refs(
            definition.mission_id, definition.run_id, evidence,
        )
        self._projection = _initial_projection(definition)
        self._events: list[MissionEvent] = []

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
            evidence=self.evidence_scope,
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
    ) -> MissionEvent:
        return MissionEvent.create(
            event_type,
            self.definition,
            base_revision=self._projection.revision if base_revision is None else base_revision,
            payload=payload,
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
