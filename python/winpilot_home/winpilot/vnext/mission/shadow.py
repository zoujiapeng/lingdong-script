"""Read-only projections from the existing legacy contract/trace shapes.

The shadow never changes ``task-contract/1`` and never treats a legacy
``ok``/``done`` event as business completion.  It only provides an offline
MissionDefinition/event stream for comparison and replay tests.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from .contracts import (
    EvidencePolicy,
    EvidenceRef,
    MissionDefinition,
    MissionEvent,
    MissionProjection,
    ObligationSpec,
)
from .reducer import MissionReducer


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _items(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (value,)
    try:
        return tuple(value)
    except TypeError:
        return (value,)


_KNOWN_CAPABILITIES = {
    # These are capability classes, not application or task rules.  Unknown
    # legacy labels remain metadata and can never silently become requirements.
    "visual.interact",
    "visual.observe",
    "visual.read",
    "visual.read_text",
    "visual.spatial",
    "pointer.dynamic",
    "artifact.capture",
    "artifact.read",
    "artifact.transform",
    "application.resolve",
    "application.open",
    "communication.send_text",
    "communication.send_artifact",
    "mcp.discover",
    "mcp.office",
    "external.wait",
}


def _partition_capabilities(values: Iterable[Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return known broad capabilities and isolate unknown legacy metadata."""
    aliases = {
        "visual.adaptive_interaction": "visual.interact",
        "visual.execute": "visual.interact",
        "desktop.execute": "visual.interact",
        "desktop.interact": "visual.interact",
        "visual.spatial_interaction": "visual.spatial",
    }
    known: list[str] = []
    unknown: list[str] = []
    for raw in values:
        name = str(raw or "").strip()
        if not name:
            continue
        name = aliases.get(name, name)
        target = known if name in _KNOWN_CAPABILITIES else unknown
        if name not in target:
            target.append(name)
    return tuple(known or ("visual.interact",)), tuple(unknown)


def _broad_capabilities(values: Iterable[Any]) -> tuple[str, ...]:
    """Compatibility helper returning only canonical known capabilities."""
    return _partition_capabilities(values)[0]


def _stage_maps(contract: Any) -> tuple[dict[str, Any], dict[str, str]]:
    stages = _items(_get(contract, "stages", ()))
    by_id: dict[str, Any] = {}
    for stage in stages:
        stage_id = str(_get(stage, "id", "") or "")
        if stage_id:
            by_id[stage_id] = stage
    return by_id, {}


def _legacy_obligation_rows(contract: Any) -> tuple[tuple[Any, Any], ...]:
    stages, _ = _stage_maps(contract)
    rows: list[tuple[Any, Any]] = []
    top_level = _items(_get(contract, "obligations", ()))
    if top_level:
        for obligation in top_level:
            rows.append((obligation, stages.get(str(_get(obligation, "stage_id", "") or ""))))
        return tuple(rows)
    for stage in stages.values():
        for obligation in _items(_get(stage, "obligations", ())):
            rows.append((obligation, stage))
    return tuple(rows)


def _artifact_names(value: Any) -> set[str]:
    names: set[str] = set()
    for item in _items(value):
        if isinstance(item, Mapping):
            name = _get(item, "name", "") or _get(item, "artifact_id", "")
        else:
            name = _get(item, "name", "") or _get(item, "artifact_id", "") or item
        if str(name or "").strip():
            names.add(str(name).strip())
    return names


def _row_edges(rows: tuple[tuple[Any, Any], ...]) -> tuple[set[tuple[int, int]], bool]:
    """Return only explicit or cross-row artifact dependency edges.

    A legacy list of obligations is not evidence that the rows form a safe
    mission DAG.  Independent rows therefore collapse to one full-goal
    obligation.  An edge is reliable only when it is declared by the legacy
    dependency fields or when one row consumes an artifact named as another
    row's output.
    """
    edges: set[tuple[int, int]] = set()
    ids = [str(_get(raw, "id", "") or f"obligation-{index}") for index, (raw, _) in enumerate(rows, start=1)]
    id_to_index = {item: index for index, item in enumerate(ids)}
    for index, (_, stage) in enumerate(rows):
        stage_id = str(_get(stage, "id", "") or "") if stage else ""
        if stage_id:
            id_to_index.setdefault(stage_id, index)
    outputs: list[set[str]] = []
    inputs: list[set[str]] = []
    for raw, stage in rows:
        raw_outputs = _artifact_names(_get(raw, "output_artifact_refs", ()))
        raw_artifact = str(_get(raw, "artifact_ref", "") or "").strip()
        if raw_artifact:
            raw_outputs.add(raw_artifact)
        stage_outputs = _artifact_names(_get(stage, "outputs", ())) if stage else set()
        outputs.append(raw_outputs | stage_outputs)
        raw_inputs = _artifact_names(_get(raw, "input_artifact_refs", ()))
        stage_inputs = _artifact_names(_get(stage, "inputs", ())) if stage else set()
        inputs.append(raw_inputs | stage_inputs)
    for index, (raw, stage) in enumerate(rows):
        explicit = [
            *_items(_get(raw, "depends_on", ())),
            *(_items(_get(stage, "depends_on", ())) if stage else ()),
        ]
        for dependency in explicit:
            dep_index = id_to_index.get(str(dependency))
            if dep_index is not None and dep_index != index:
                edges.add((dep_index, index))
        for producer_index, produced in enumerate(outputs):
            if producer_index != index and produced.intersection(inputs[index]):
                edges.add((producer_index, index))
    # Every split row must participate in at least one reliable relation.  A
    # single independent row is otherwise enough to make the generated DAG
    # look more certain than the legacy contract actually is.
    covered: set[int] = set()
    for producer, consumer in edges:
        covered.add(producer)
        covered.add(consumer)
    return edges, len(covered) == len(rows)


def _safe_to_split(rows: tuple[tuple[Any, Any], ...]) -> bool:
    """Only preserve a split when the legacy contract exposes hard edges."""
    return len(rows) <= 1 or _row_edges(rows)[1]


def _enum_text(value: Any) -> str:
    """Read a typed enum or its JSON value without importing task-contract/1."""

    return str(getattr(value, "value", value) or "").strip()


def _row_capability_values(raw: Any, stage: Any) -> tuple[Any, ...]:
    values: list[Any] = []
    for owner in (
        raw,
        stage,
        _get(raw, "execution", None),
        _get(stage, "execution", None) if stage else None,
    ):
        values.extend(_items(_get(owner, "required_capabilities", ())) if owner else ())
    for provider in _items(_get(raw, "evidence_providers", ())):
        values.extend(_items(_get(provider, "capabilities", ())))
    return tuple(values)


def _allows_independent_visual_verifier(raw: Any, stage: Any) -> bool:
    """Project only an explicit typed visual-verification requirement."""

    declared = set()
    execution = _get(stage, "execution", None) if stage else None
    declared.update(
        str(item or "").strip()
        for item in _items(_get(execution, "verifier_requirements", ()))
    )
    for provider in _items(_get(raw, "evidence_providers", ())):
        declared.update(
            str(item or "").strip()
            for item in _items(_get(provider, "capabilities", ()))
        )
    return "visual.semantic_verify" in declared


def _is_lossless_desktop_goal(rows: tuple[tuple[Any, Any], ...]) -> bool:
    """A generic desktop stage delegates one complete goal to the Visual Actor."""

    if len(rows) != 1:
        return False
    _raw, stage = rows[0]
    return bool(stage) and _enum_text(_get(stage, "type", "")) == "ExecuteDesktopTask"


def project_legacy_contract(
    contract: Any,
    *,
    mission_id: str | None = None,
    run_id: str | None = None,
) -> MissionDefinition:
    """Project any legacy TaskContract-shaped value without importing it.

    The function is intentionally duck-typed so unit tests and old trace
    readers can use a plain dict.  It copies result obligations only; stage
    names, application names, parameters, coordinates and tool sequences are
    never converted into model instructions.
    """
    task_id = str(_get(contract, "task_id", "legacy") or "legacy")
    original_goal = str(_get(contract, "task", "") or "").strip()
    if not original_goal:
        original_goal = str(_get(contract, "original_goal", "") or "").strip()
    if not original_goal:
        raise ValueError("legacy contract has no original task text")
    mission_id = str(mission_id or _get(contract, "mission_id", "") or f"legacy-shadow-{task_id}")
    run_id = str(run_id or _get(contract, "run_id", "") or f"legacy-shadow-run-{task_id}")
    stages, _ = _stage_maps(contract)
    rows = _legacy_obligation_rows(contract)
    unknown_capabilities: list[str] = []
    for raw, stage in rows:
        _, unknown = _partition_capabilities(_row_capability_values(raw, stage))
        for item in unknown:
            if item not in unknown_capabilities:
                unknown_capabilities.append(item)

    experience_policy = str(_get(contract, "experience_policy", "allow") or "allow")
    if _is_lossless_desktop_goal(rows):
        raw, stage = rows[0]
        capabilities, _unknown = _partition_capabilities(
            _row_capability_values(raw, stage)
        )
        return MissionDefinition(
            mission_id=mission_id,
            run_id=run_id,
            original_goal=original_goal,
            obligations=(ObligationSpec(
                id="obligation-1",
                desired_state=original_goal,
                acceptance_criteria=(original_goal,),
                required_capabilities=capabilities,
                evidence_policy=EvidencePolicy(
                    allow_independent_visual_verifier=(
                        _allows_independent_visual_verifier(raw, stage)
                    ),
                ),
                source_span=(0, len(original_goal)),
            ),),
            source="legacy_shadow",
            experience_policy=experience_policy,
            metadata={
                "legacy_contract_format": str(
                    _get(contract, "format", "task-contract/1")
                    or "task-contract/1"
                ),
                "legacy_task_id": task_id,
                "legacy_projection": "full_goal_execute_desktop",
                **(
                    {"legacy_unknown_capabilities": tuple(unknown_capabilities)}
                    if unknown_capabilities else {}
                ),
            },
        )
    # A contract with no required row cannot prove any user-visible completion
    # condition.  Keep the original goal as one required obligation instead
    # of manufacturing an apparently successful optional graph.
    if not _safe_to_split(rows) or rows and not any(bool(_get(raw, "required", True)) for raw, _ in rows):
        return MissionDefinition(
            mission_id=mission_id,
            run_id=run_id,
            original_goal=original_goal,
            obligations=(),
            source="legacy_shadow",
            experience_policy=experience_policy,
            metadata={
                "legacy_contract_format": str(_get(contract, "format", "task-contract/1") or "task-contract/1"),
                "legacy_task_id": task_id,
                "legacy_split_fallback": "full_goal",
                **({"legacy_unknown_capabilities": tuple(unknown_capabilities)} if unknown_capabilities else {}),
            },
        )

    drafts: list[ObligationSpec] = []
    used_ids: set[str] = set()
    stage_to_ids: dict[str, list[str]] = {}
    for index, (raw, stage) in enumerate(rows, start=1):
        raw_id = str(_get(raw, "id", "") or f"obligation-{index}")
        obligation_id = raw_id
        suffix = 2
        while obligation_id in used_ids:
            obligation_id = f"{raw_id}-{suffix}"
            suffix += 1
        used_ids.add(obligation_id)
        stage_id = str(_get(raw, "stage_id", "") or _get(stage, "id", "") or "")
        description = str(
            _get(raw, "description", "")
            or _get(raw, "desired_state", "")
            or original_goal
        ).strip()
        predicate = str(_get(raw, "predicate", "") or "").strip()
        criteria = (predicate or description,)
        if _get(raw, "acceptance_criteria", None):
            criteria = tuple(str(item) for item in _items(_get(raw, "acceptance_criteria")))
        stage_capabilities = _items(_get(stage, "required_capabilities", ())) if stage else ()
        raw_capabilities = _items(_get(raw, "required_capabilities", ()))
        execution_capabilities = _items(_get(_get(stage, "execution", None), "required_capabilities", ())) if stage else ()
        provider_capabilities: list[str] = []
        for provider in _items(_get(raw, "evidence_providers", ())):
            provider_capabilities.extend(_items(_get(provider, "capabilities", ())))
        capabilities, _unknown = _partition_capabilities(
            (*raw_capabilities, *stage_capabilities, *execution_capabilities, *provider_capabilities)
        )
        artifact_ref = str(_get(raw, "artifact_ref", "") or "").strip()
        outputs = [str(_get(item, "name", "") or "") for item in _items(_get(stage, "outputs", ())) if _get(item, "name", "")]
        if artifact_ref and artifact_ref not in outputs:
            outputs.append(artifact_ref)
        inputs = [str(_get(item, "name", "") or "") for item in _items(_get(stage, "inputs", ())) if _get(item, "name", "")]
        raw_inputs = _items(_get(raw, "input_artifact_refs", ()))
        inputs.extend(str(item) for item in raw_inputs if str(item))
        draft = ObligationSpec(
            id=obligation_id,
            desired_state=description,
            acceptance_criteria=criteria,
            depends_on=(),
            forbidden_effects=_get(raw, "forbidden_effects", ()) or (),
            required_capabilities=capabilities,
            input_artifact_refs=tuple(dict.fromkeys(inputs)),
            output_binding_names=tuple(dict.fromkeys(outputs)),
            required=bool(_get(raw, "required", True)),
            source_span=(
                tuple(int(item) for item in _get(raw, "source_span"))
                if _get(raw, "source_span", None) else None
            ),
        )
        drafts.append(draft)
        if stage_id:
            stage_to_ids.setdefault(stage_id, []).append(obligation_id)

    # Preserve only dependency edges that are explicit in the legacy graph.
    # If the legacy payload has no reliable obligation rows, the Definition's
    # constructor supplies the documented single-obligation fallback.
    if drafts:
        artifact_edges, _ = _row_edges(rows)
        rewritten: list[ObligationSpec] = []
        for row_index, (draft, (raw, stage)) in enumerate(zip(drafts, rows)):
            dependency_ids: list[str] = []
            for stage_dependency in _items(_get(stage, "depends_on", ())) if stage else ():
                dependency_ids.extend(stage_to_ids.get(str(stage_dependency), ()))
            explicit = _items(_get(raw, "depends_on", ()))
            for item in explicit:
                dependency_ids.extend(stage_to_ids.get(str(item), ()))
                dependency_ids.append(str(item))
            dependency_ids.extend(
                drafts[producer_index].id
                for producer_index, consumer_index in artifact_edges
                if consumer_index == row_index
            )
            dependency_ids = [item for item in dict.fromkeys(dependency_ids) if item in used_ids and item != draft.id]
            rewritten.append(ObligationSpec(
                id=draft.id,
                desired_state=draft.desired_state,
                acceptance_criteria=draft.acceptance_criteria,
                depends_on=tuple(dependency_ids),
                forbidden_effects=draft.forbidden_effects,
                required_capabilities=draft.required_capabilities,
                input_artifact_refs=draft.input_artifact_refs,
                output_binding_names=draft.output_binding_names,
                evidence_policy=draft.evidence_policy,
                required=draft.required,
                source_span=draft.source_span,
            ))
        drafts = rewritten

    return MissionDefinition(
        mission_id=mission_id,
        run_id=run_id,
        original_goal=original_goal,
        obligations=tuple(drafts),
        source="legacy_shadow",
        experience_policy=experience_policy,
        metadata={
            "legacy_contract_format": str(_get(contract, "format", "task-contract/1") or "task-contract/1"),
            "legacy_task_id": task_id,
            "legacy_stage_ids": tuple(stages),
            **({"legacy_unknown_capabilities": tuple(unknown_capabilities)} if unknown_capabilities else {}),
        },
    )


def legacy_trace_to_events(
    definition: MissionDefinition,
    trace_events: Iterable[Mapping[str, Any]],
) -> tuple[MissionEvent, ...]:
    """Create conservative, non-completion events from a legacy trace.

    A legacy tool ``ok`` or ``done`` is not accepted evidence.  The shadow only
    preserves executed transition facts and explicit terminal failures so that
    the resulting projection can be inspected offline.
    """
    events: list[MissionEvent] = []
    revision = 0
    active = definition.stable_topological_order[0] if definition.stable_topological_order else None
    for raw in trace_events:
        event_type = str(raw.get("type") or "")
        if event_type == "agent_tool" and active:
            result_meta = raw.get("result_meta") if isinstance(raw.get("result_meta"), Mapping) else {}
            event = MissionEvent.create(
                "transition_recorded",
                definition,
                base_revision=revision,
                payload={
                    "obligation_id": active,
                    "action": str(raw.get("tool") or raw.get("action") or "unknown"),
                    "input_completed": bool(result_meta.get("action_executed", raw.get("ok", False))),
                    "visible_effect": str(result_meta.get("visible_effect") or ""),
                },
            )
            events.append(event)
            revision += 1
        elif event_type == "agent_done" and str(raw.get("status") or "") in {"error", "fail", "stopped"}:
            event = MissionEvent.create(
                "mission_failed",
                definition,
                base_revision=revision,
                payload={"reason": str(raw.get("result") or raw.get("status") or "legacy failure")},
            )
            events.append(event)
            revision += 1
    return tuple(events)


def project_legacy_trace(
    definition: MissionDefinition,
    trace_events: Iterable[Mapping[str, Any]],
    *,
    evidence: Iterable[EvidenceRef] = (),
) -> MissionProjection:
    """Replay a conservative legacy shadow without inventing completion."""
    reducer = MissionReducer(definition, evidence=evidence)
    reducer.apply_many(legacy_trace_to_events(definition, trace_events))
    return reducer.projection


# Friendly names for callers that use the phrase from the migration plan.
legacy_shadow_projection = project_legacy_contract


__all__ = [
    "project_legacy_contract",
    "legacy_shadow_projection",
    "legacy_trace_to_events",
    "project_legacy_trace",
]
