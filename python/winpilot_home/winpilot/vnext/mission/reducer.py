"""Pure MissionReducer for the five events emitted by the live runtime."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping

from .contracts import (
    DuplicateEventError,
    EvidenceRef,
    EvidenceScope,
    EvidenceScopeError,
    FailureSummary,
    IllegalTransitionError,
    InvalidMissionEvent,
    MissionCard,
    MissionDefinition,
    MissionEvent,
    MissionProjection,
    MissionStatus,
    ObligationProjection,
    ObligationSpec,
    ObligationStatus,
    StaleRevisionError,
    TargetBinding,
    TransitionSummary,
    _as_string_tuple,
    _validate_event_payload,
    thaw,
)


_TERMINAL_MISSION_STATUSES = {
    MissionStatus.VERIFIED,
    MissionStatus.CANCELED,
}


def _payload_text(
    payload: Mapping[str, Any],
    key: str,
    default: str = "",
) -> str:
    if key not in payload:
        return default
    value = payload[key]
    if type(value) is not str:
        raise InvalidMissionEvent(
            f"event payload {key!r} must be a literal string"
        )
    return value


def _initial_projection(definition: MissionDefinition) -> MissionProjection:
    return _recompute(
        MissionProjection(
            definition=definition,
            revision=0,
            obligation_states=tuple(
                ObligationProjection(item.id, ObligationStatus.PENDING)
                for item in definition.obligations
            ),
            active_obligation_id=None,
            status=MissionStatus.RUNNING,
        )
    )


def _definition_for(
    projection: MissionProjection,
    obligation_id: str,
) -> ObligationSpec:
    try:
        return projection.obligation_definition(obligation_id)
    except KeyError as exc:
        raise IllegalTransitionError(
            f"unknown obligation: {obligation_id}"
        ) from exc


def _state_map(
    projection: MissionProjection,
) -> dict[str, ObligationProjection]:
    return {item.id: item for item in projection.obligation_states}


def _dependencies_verified(
    projection: MissionProjection,
    definition: ObligationSpec,
) -> bool:
    states = _state_map(projection)
    return all(
        states[dependency].status is ObligationStatus.VERIFIED
        for dependency in definition.depends_on
    )


def _ready(
    projection: MissionProjection,
    definition: ObligationSpec,
) -> bool:
    state = projection.obligation_state(definition.id)
    return bool(
        state.status is ObligationStatus.PENDING
        and _dependencies_verified(projection, definition)
        and all(
            ref in projection.artifact_bindings
            for ref in definition.input_artifact_refs
        )
    )


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


def _recompute(projection: MissionProjection) -> MissionProjection:
    """Derive the one active obligation and mission status from state."""
    if projection.status in _TERMINAL_MISSION_STATUSES:
        return projection
    if projection.status is MissionStatus.BLOCKED:
        return projection
    if _required_verified(projection):
        return replace(
            projection,
            active_obligation_id=None,
            status=MissionStatus.VERIFIED,
        )

    states = _state_map(projection)
    active_id = projection.active_obligation_id
    if active_id:
        active_state = states.get(active_id)
        if (
            active_state is not None
            and active_state.status is ObligationStatus.ACTIVE
        ):
            return replace(projection, status=MissionStatus.RUNNING)

    next_id = _first_ready(projection)
    if next_id:
        states[next_id] = replace(
            states[next_id],
            status=ObligationStatus.ACTIVE,
        )
        return replace(
            projection,
            obligation_states=tuple(
                states[item.id] for item in projection.definition.obligations
            ),
            active_obligation_id=next_id,
            status=MissionStatus.RUNNING,
        )
    if any(
        item.status is ObligationStatus.BLOCKED
        for item in states.values()
    ):
        return replace(
            projection,
            active_obligation_id=None,
            status=MissionStatus.BLOCKED,
        )
    return replace(
        projection,
        active_obligation_id=None,
        status=MissionStatus.WAITING_EXTERNAL,
    )


def _evidence_refs(
    scope: EvidenceScope,
    raw_ids: Iterable[str] | None,
    *,
    obligation_id: str | None = None,
    required: bool = False,
) -> tuple[EvidenceRef, ...]:
    ids = _as_string_tuple(raw_ids)
    if not ids:
        if required:
            raise EvidenceScopeError(
                "event requires run-scoped evidence_refs"
            )
        return ()
    return scope.require(ids, obligation_id=obligation_id)


def _merge_evidence(
    projection: MissionProjection,
    refs: Iterable[EvidenceRef],
) -> MissionProjection:
    merged = dict(projection.evidence_refs)
    for ref in refs:
        merged[ref.id] = ref
    return replace(projection, evidence_refs=merged)


def _parse_target(payload: Mapping[str, Any]) -> TargetBinding:
    raw = payload.get("target_binding")
    if not isinstance(raw, Mapping):
        raise InvalidMissionEvent(
            "target_bound payload requires target_binding"
        )
    return TargetBinding.from_dict(thaw(raw))


def _parse_transition(
    payload: Mapping[str, Any],
    *,
    evidence_refs: tuple[str, ...],
) -> TransitionSummary:
    input_completed = payload.get("input_completed")
    if type(input_completed) is not bool:
        raise InvalidMissionEvent(
            "transition input_completed must be boolean"
        )
    frame_id = payload.get("frame_id")
    if frame_id is not None and (
        type(frame_id) is not int or frame_id < 0
    ):
        raise InvalidMissionEvent(
            "transition frame_id must be a non-negative integer or null"
        )
    error_kind = payload.get("error_kind")
    if error_kind is not None and type(error_kind) is not str:
        raise InvalidMissionEvent(
            "transition error_kind must be a string or null"
        )
    return TransitionSummary(
        action=_payload_text(payload, "action", "unknown"),
        input_completed=input_completed,
        visible_effect=_payload_text(payload, "visible_effect"),
        evidence_refs=evidence_refs,
        frame_id=frame_id,
        obligation_id=_payload_text(payload, "obligation_id"),
        action_id=(
            _payload_text(payload, "action_id")
            if payload.get("action_id") is not None
            else None
        ),
        error_kind=error_kind,
    )


def _event_evidence_ids(
    event: MissionEvent,
    payload: Mapping[str, Any],
) -> tuple[str, ...]:
    if "evidence_refs" in payload:
        raise InvalidMissionEvent(
            "legacy payload field 'evidence_refs' is not accepted; "
            "use MissionEvent.evidence_refs"
        )
    return event.evidence_refs


def _apply_event(
    projection: MissionProjection,
    event: MissionEvent,
    scope: EvidenceScope,
) -> MissionProjection:
    payload = event.payload
    event_type = event.kind
    _validate_event_payload(event_type, payload)
    states = _state_map(projection)

    if event_type == "target_bound":
        target = _parse_target(payload)
        evidence_ids = tuple(
            dict.fromkeys(
                (*event.evidence_refs, target.established_by_evidence_ref)
            )
        )
        refs = _evidence_refs(scope, evidence_ids, required=True)
        established = next(
            ref
            for ref in refs
            if ref.id == target.established_by_evidence_ref
        )
        if established.artifact_id not in (None, ""):
            raise EvidenceScopeError(
                "target evidence cannot identify an artifact"
            )
        old = projection.target_binding
        if old is not None and old.binding_id == target.binding_id and old != target:
            raise IllegalTransitionError(
                "target binding identity changed"
            )
        return _merge_evidence(
            replace(projection, target_binding=target),
            refs,
        )

    if event_type == "transition_recorded":
        obligation_id = _payload_text(payload, "obligation_id")
        if projection.active_obligation_id != obligation_id:
            raise IllegalTransitionError(
                "transition must be bound to the active obligation"
            )
        _definition_for(projection, obligation_id)
        evidence_ids = _event_evidence_ids(event, payload)
        transition = _parse_transition(
            payload,
            evidence_refs=evidence_ids,
        )
        refs = _evidence_refs(
            scope,
            evidence_ids,
            obligation_id=obligation_id,
        )
        return _merge_evidence(
            replace(projection, last_transition=transition),
            refs,
        )

    if event_type == "actor_completed":
        obligation_id = _payload_text(payload, "obligation_id")
        _definition_for(projection, obligation_id)
        if projection.active_obligation_id != obligation_id:
            raise IllegalTransitionError(
                "only the active obligation may be completed"
            )
        if states[obligation_id].status is not ObligationStatus.ACTIVE:
            raise IllegalTransitionError("obligation is not completable")
        states[obligation_id] = replace(
            states[obligation_id],
            status=ObligationStatus.VERIFIED,
            last_reason="actor_completed",
        )
        return replace(
            projection,
            obligation_states=tuple(
                states[item.id] for item in projection.definition.obligations
            ),
            active_obligation_id=None,
            status=MissionStatus.RUNNING,
            failure_summary=None,
        )

    if event_type == "obligation_blocked":
        obligation_id = _payload_text(payload, "obligation_id")
        _definition_for(projection, obligation_id)
        if (
            projection.active_obligation_id != obligation_id
            or states[obligation_id].status is not ObligationStatus.ACTIVE
        ):
            raise IllegalTransitionError(
                "only the active obligation may be blocked"
            )
        reason = _payload_text(
            payload,
            "reason",
            "obligation blocked",
        )
        states[obligation_id] = replace(
            states[obligation_id],
            status=ObligationStatus.BLOCKED,
            last_reason=reason,
        )
        return replace(
            projection,
            active_obligation_id=None,
            obligation_states=tuple(
                states[item.id] for item in projection.definition.obligations
            ),
            failure_summary=FailureSummary(
                _payload_text(payload, "blocker_kind", "blocked"),
                reason,
                obligation_id=obligation_id,
            ),
            status=MissionStatus.BLOCKED,
        )

    if event_type == "mission_canceled":
        return replace(
            projection,
            active_obligation_id=None,
            status=MissionStatus.CANCELED,
            last_error=_payload_text(
                payload,
                "reason",
                "mission canceled",
            ),
        )

    raise InvalidMissionEvent(
        f"unsupported mission event type: {event_type}"
    )


def reduce_projection(
    projection: MissionProjection,
    event: MissionEvent,
    *,
    evidence: EvidenceScope | Iterable[EvidenceRef] | None = None,
) -> MissionProjection:
    """Apply one current-revision event without mutating the input."""
    if (
        event.mission_id != projection.mission_id
        or event.run_id != projection.run_id
    ):
        raise InvalidMissionEvent(
            "event does not belong to the projection mission/run"
        )
    if event.event_id in projection.accepted_event_ids:
        raise DuplicateEventError(
            f"event already accepted: {event.event_id}"
        )
    if event.base_revision != projection.revision:
        raise StaleRevisionError(
            "stale mission revision: "
            f"event={event.base_revision}, current={projection.revision}"
        )
    if projection.status in _TERMINAL_MISSION_STATUSES:
        raise IllegalTransitionError(
            f"mission is terminal: {projection.status.value}"
        )
    scope = EvidenceScope.from_source(
        projection.mission_id,
        projection.run_id,
        evidence,
    )
    if event.evidence_refs:
        scope.require(event.evidence_refs)
    candidate = _apply_event(projection, event, scope)
    candidate = replace(
        candidate,
        revision=projection.revision + 1,
        accepted_event_ids=(
            *projection.accepted_event_ids,
            event.event_id,
        ),
    )
    return _recompute(candidate)


class MissionReducer:
    """Small event-sourced reducer backed by one evidence registry."""

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
        return EvidenceScope.from_source(
            self.definition.mission_id,
            self.definition.run_id,
            self._evidence_source,
        )

    @property
    def evidence_source(self) -> Any:
        return self._evidence_source

    @property
    def projection(self) -> MissionProjection:
        return self._projection

    @property
    def events(self) -> tuple[MissionEvent, ...]:
        return tuple(self._events)

    @property
    def card(self) -> MissionCard:
        return MissionCard.from_projection(self._projection)

    def apply(
        self,
        event: MissionEvent | Mapping[str, Any],
    ) -> MissionProjection:
        if type(event) is not MissionEvent:
            event = MissionEvent.from_dict(event)
        self._projection = reduce_projection(
            self._projection,
            event,
            evidence=self._evidence_source,
        )
        self._events.append(event)
        return self._projection

    def apply_many(
        self,
        events: Iterable[MissionEvent | Mapping[str, Any]],
    ) -> MissionProjection:
        for event in events:
            self.apply(event)
        return self._projection

    def next_ready_obligation_id(self) -> str | None:
        return self._projection.active_obligation_id or _first_ready(
            self._projection
        )

    def event(
        self,
        event_type: str,
        *,
        payload: Mapping[str, Any] | None = None,
        base_revision: int | None = None,
        evidence_refs: Iterable[str] | None = None,
        created_at: float | None = None,
    ) -> MissionEvent:
        if payload is None:
            bound_payload: dict[str, Any] = {}
        elif type(payload) is dict:
            bound_payload = payload.copy()
        else:
            raise InvalidMissionEvent(
                "event payload must be a plain JSON object"
            )
        return MissionEvent.create(
            event_type,
            self.definition,
            base_revision=(
                self._projection.revision
                if base_revision is None
                else base_revision
            ),
            payload=bound_payload,
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
    return MissionReducer.replay(
        definition,
        events,
        evidence=evidence,
    ).projection
