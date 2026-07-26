"""Negative coverage for the canonical vNext Mission wire boundary."""
from __future__ import annotations

import copy
import math
from types import MappingProxyType
from typing import Any, Callable

import pytest

from winpilot.vnext.json_contract import (
    MAX_JSON_ARRAY_ITEMS,
    MAX_JSON_DEPTH,
    MAX_JSON_KEY_LENGTH,
    MAX_JSON_NODES,
    MAX_JSON_STRING_LENGTH,
    MAX_SIGNED_64,
    MIN_SIGNED_64,
    bounded_json_copy,
)
from winpilot.vnext.mission import (
    ArtifactRef,
    EvidencePolicy,
    EvidenceRef,
    FailureSummary,
    InvalidMissionDefinition,
    InvalidMissionEvent,
    MissionDefinition,
    MissionError,
    MissionEvent,
    MissionProjection,
    MissionReducer,
    ObligationProjection,
    ObligationSpec,
    ObligationStatus,
    TargetBinding,
    VerifiedFact,
)


SHA256 = "a" * 64


def _definition() -> MissionDefinition:
    return MissionDefinition(
        mission_id="mission-1",
        run_id="run-1",
        original_goal="complete the visible task",
        metadata={"source": "test", "nested": [None, True, 7, 1.5]},
        obligations=(
            ObligationSpec(
                id="obligation-1",
                desired_state="the result is visible",
                acceptance_criteria=("the result is visible",),
                required_capabilities=("visual.interact",),
                source_span=(0, 10),
            ),
        ),
    )


def _event() -> MissionEvent:
    return MissionEvent(
        kind="obligation_claimed",
        mission_id="mission-1",
        run_id="run-1",
        base_revision=0,
        payload={"obligation_id": "obligation-1"},
        evidence_refs=("evidence-1",),
        created_at=1.5,
    )


def _evidence() -> EvidenceRef:
    return EvidenceRef(
        id="evidence-1",
        mission_id="mission-1",
        run_id="run-1",
        kind="oracle",
        trust="deterministic",
        obligation_id="obligation-1",
        payload_hash="payload-hash",
        frame_id=7,
        action_id="action-1",
        artifact_id="artifact-1",
        created_at=1.5,
        content_sha256=SHA256,
    )


def _artifact() -> ArtifactRef:
    return ArtifactRef(
        artifact_id="artifact-1",
        media_type="image/png",
        sha256=SHA256,
        producer_obligation_id="obligation-1",
        evidence_ref="evidence-1",
    )


def _target() -> TargetBinding:
    return TargetBinding(
        binding_id="target-1",
        hwnd=42,
        process_id=7,
        executable_identity="sha256:application",
        title_snapshot="Application",
        established_by_evidence_ref="evidence-1",
        epoch=0,
    )


def _fact() -> VerifiedFact:
    return VerifiedFact(
        id="fact-1",
        statement="the result is visible",
        evidence_refs=("evidence-1",),
        scope="obligation",
        valid_for_obligation_ids=("obligation-1",),
        expires_on_epoch_change=True,
    )


WireParser = Callable[[dict[str, Any]], Any]
WireFactory = Callable[[], dict[str, Any]]


WIRE_CASES: tuple[
    tuple[str, WireParser, WireFactory, type[MissionError]],
    ...,
] = (
    (
        "EvidencePolicy",
        EvidencePolicy.from_dict,
        lambda: EvidencePolicy().to_dict(),
        InvalidMissionDefinition,
    ),
    (
        "ObligationSpec",
        ObligationSpec.from_dict,
        lambda: _definition().obligations[0].to_dict(),
        InvalidMissionDefinition,
    ),
    (
        "MissionDefinition",
        MissionDefinition.from_dict,
        lambda: _definition().to_dict(),
        InvalidMissionDefinition,
    ),
    (
        "EvidenceRef",
        EvidenceRef.from_dict,
        lambda: _evidence().to_dict(),
        MissionError,
    ),
    (
        "ArtifactRef",
        ArtifactRef.from_dict,
        lambda: _artifact().to_dict(),
        InvalidMissionEvent,
    ),
    (
        "TargetBinding",
        TargetBinding.from_dict,
        lambda: _target().to_dict(),
        InvalidMissionEvent,
    ),
    (
        "VerifiedFact",
        VerifiedFact.from_dict,
        lambda: _fact().to_dict(),
        InvalidMissionEvent,
    ),
    (
        "MissionEvent",
        MissionEvent.from_dict,
        lambda: _event().to_dict(),
        InvalidMissionEvent,
    ),
)


@pytest.mark.parametrize(
    "name,parser,wire_factory,error_type",
    WIRE_CASES,
    ids=[case[0] for case in WIRE_CASES],
)
def test_all_wire_parsers_round_trip_one_exact_schema(
    name: str,
    parser: WireParser,
    wire_factory: WireFactory,
    error_type: type[MissionError],
) -> None:
    del name, error_type
    wire = wire_factory()
    assert parser(wire).to_dict() == wire


@pytest.mark.parametrize(
    "name,parser,wire_factory,error_type",
    WIRE_CASES,
    ids=[case[0] for case in WIRE_CASES],
)
def test_all_wire_parsers_require_plain_dict_and_exact_complete_fields(
    name: str,
    parser: WireParser,
    wire_factory: WireFactory,
    error_type: type[MissionError],
) -> None:
    wire = wire_factory()
    with pytest.raises(error_type, match="plain JSON object"):
        parser(MappingProxyType(wire))  # type: ignore[arg-type]

    class DictSubclass(dict[str, Any]):
        pass

    with pytest.raises(error_type, match="plain JSON object"):
        parser(DictSubclass(wire))

    unknown = wire_factory()
    unknown["legacy_or_unknown"] = "rejected"
    with pytest.raises(error_type, match="unknown"):
        parser(unknown)

    for field_name in tuple(wire):
        missing = wire_factory()
        missing.pop(field_name)
        with pytest.raises(error_type, match="missing"):
            parser(missing)


@pytest.mark.parametrize(
    ("parser", "wire_factory", "canonical", "alias"),
    (
        (
            ObligationSpec.from_dict,
            lambda: _definition().obligations[0].to_dict(),
            "desired_state",
            "description",
        ),
        (
            ObligationSpec.from_dict,
            lambda: _definition().obligations[0].to_dict(),
            "acceptance_criteria",
            "criteria",
        ),
        (
            EvidenceRef.from_dict,
            lambda: _evidence().to_dict(),
            "id",
            "evidence_id",
        ),
        (
            MissionEvent.from_dict,
            lambda: _event().to_dict(),
            "kind",
            "type",
        ),
        (
            MissionEvent.from_dict,
            lambda: _event().to_dict(),
            "base_revision",
            "revision",
        ),
    ),
)
def test_legacy_wire_aliases_are_not_kernel_inputs(
    parser: WireParser,
    wire_factory: WireFactory,
    canonical: str,
    alias: str,
) -> None:
    wire = wire_factory()
    wire[alias] = wire.pop(canonical)
    with pytest.raises(MissionError, match="missing=.*unknown|unknown=.*missing"):
        parser(wire)


def test_legacy_read_and_constructor_aliases_are_absent() -> None:
    event = _event()
    evidence = _evidence()
    assert not hasattr(event, "type")
    assert not hasattr(event, "event_type")
    assert not hasattr(evidence, "evidence_id")
    with pytest.raises(TypeError):
        MissionEvent(  # type: ignore[call-arg]
            type="obligation_claimed",
            mission_id="mission-1",
            run_id="run-1",
        )


@pytest.mark.parametrize("value", ("false", 0, 1, None))
def test_boolean_fields_reject_truthiness_coercion(value: Any) -> None:
    policy = EvidencePolicy().to_dict()
    policy["allow_independent_visual_verifier"] = value
    with pytest.raises(InvalidMissionDefinition, match="boolean"):
        EvidencePolicy.from_dict(policy)

    obligation = _definition().obligations[0].to_dict()
    obligation["required"] = value
    with pytest.raises(InvalidMissionDefinition, match="boolean"):
        ObligationSpec.from_dict(obligation)


@pytest.mark.parametrize("value", ("1", 1.0, True, None))
def test_integer_fields_reject_numeric_coercion_and_bool_as_int(value: Any) -> None:
    definition = _definition().to_dict()
    definition["goal_revision"] = value
    with pytest.raises(InvalidMissionDefinition, match="integer"):
        MissionDefinition.from_dict(definition)

    event = _event().to_dict()
    event["base_revision"] = value
    with pytest.raises(InvalidMissionEvent, match="integer"):
        MissionEvent.from_dict(event)

    target = _target().to_dict()
    target["hwnd"] = value
    with pytest.raises(InvalidMissionEvent, match="integer"):
        TargetBinding.from_dict(target)


@pytest.mark.parametrize("value", (1, True, "1.5", math.inf, math.nan))
def test_float_fields_require_finite_literal_float(value: Any) -> None:
    event = _event().to_dict()
    event["created_at"] = value
    with pytest.raises(InvalidMissionEvent):
        MissionEvent.from_dict(event)

    evidence = _evidence().to_dict()
    evidence["created_at"] = value
    with pytest.raises(MissionError):
        EvidenceRef.from_dict(evidence)


@pytest.mark.parametrize("value", (None, 7, True))
def test_required_string_fields_reject_none_and_string_coercion(value: Any) -> None:
    definition = _definition().to_dict()
    definition["mission_id"] = value
    with pytest.raises(InvalidMissionDefinition, match="string"):
        MissionDefinition.from_dict(definition)

    event = _event().to_dict()
    event["kind"] = value
    with pytest.raises(InvalidMissionEvent, match="string"):
        MissionEvent.from_dict(event)

    artifact = _artifact().to_dict()
    artifact["media_type"] = value
    with pytest.raises(InvalidMissionEvent, match="string"):
        ArtifactRef.from_dict(artifact)


@pytest.mark.parametrize("item", (None, 1, True))
def test_string_arrays_reject_non_string_items(item: Any) -> None:
    obligation = _definition().obligations[0].to_dict()
    obligation["required_capabilities"] = ["visual.interact", item]
    with pytest.raises(InvalidMissionDefinition, match="string"):
        ObligationSpec.from_dict(obligation)

    event = _event().to_dict()
    event["evidence_refs"] = ["evidence-1", item]
    with pytest.raises(InvalidMissionEvent, match="string"):
        MissionEvent.from_dict(event)


def test_wire_sequences_are_json_lists_not_tuples_or_sets() -> None:
    definition = _definition().to_dict()
    definition["obligations"] = tuple(definition["obligations"])
    with pytest.raises(InvalidMissionDefinition):
        MissionDefinition.from_dict(definition)

    event = _event().to_dict()
    event["evidence_refs"] = ("evidence-1",)
    with pytest.raises(InvalidMissionEvent):
        MissionEvent.from_dict(event)

    with pytest.raises(InvalidMissionDefinition):
        ObligationSpec(
            id="obligation-1",
            desired_state="done",
            required_capabilities=("visual.interact", 1),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "constructor",
    (
        lambda: EvidencePolicy(allow_independent_visual_verifier="false"),  # type: ignore[arg-type]
        lambda: EvidencePolicy(max_verification_attempts=True),
        lambda: ObligationSpec(
            id="obligation-1",
            desired_state="done",
            required="false",  # type: ignore[arg-type]
        ),
        lambda: MissionDefinition(
            mission_id="mission-1",
            run_id="run-1",
            original_goal="done",
            goal_revision=True,
        ),
        lambda: MissionDefinition(
            mission_id="mission-1",
            run_id="run-1",
            original_goal="done",
            metadata=None,  # type: ignore[arg-type]
        ),
        lambda: MissionEvent(
            kind="mission_failed",
            mission_id="mission-1",
            run_id="run-1",
            base_revision=True,
        ),
        lambda: MissionEvent(
            kind="mission_failed",
            mission_id="mission-1",
            run_id="run-1",
            payload=None,
        ),
        lambda: FailureSummary(
            kind="failure",
            count=True,
            obligation_id="obligation-1",
        ),
        lambda: MissionDefinition.from_goal(
            "done",
            mission_id="mission-1",
            run_id="run-1",
            required_capabilities="visual.interact",  # type: ignore[arg-type]
        ),
        lambda: ObligationProjection(
            "obligation-1",
            "pending",  # type: ignore[arg-type]
        ),
        lambda: MissionProjection(
            definition=_definition(),
            revision=0,
            obligation_states=(
                ObligationProjection(
                    "obligation-1",
                    ObligationStatus.PENDING,
                ),
            ),
            active_obligation_id=None,
            status="running",  # type: ignore[arg-type]
        ),
    ),
)
def test_direct_contract_constructors_do_not_coerce_scalars(
    constructor: Callable[[], Any],
) -> None:
    with pytest.raises(MissionError):
        constructor()


@pytest.mark.parametrize(
    "name,parser,wire_factory,error_type",
    WIRE_CASES,
    ids=[case[0] for case in WIRE_CASES],
)
def test_every_wire_parser_rejects_cycles_as_mission_contract_errors(
    name: str,
    parser: WireParser,
    wire_factory: WireFactory,
    error_type: type[MissionError],
) -> None:
    del name
    wire = wire_factory()
    wire["cycle"] = wire
    with pytest.raises(error_type, match="cyclic JSON"):
        parser(wire)


def _nested_object(depth: int) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for _ in range(depth):
        value = {"next": value}
    return value


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        (_nested_object(MAX_JSON_DEPTH + 1), "depth"),
        (
            {f"node-{index}": None for index in range(MAX_JSON_NODES)},
            "node",
        ),
        ({"text": "x" * (MAX_JSON_STRING_LENGTH + 1)}, "string"),
        ({"array": [None] * (MAX_JSON_ARRAY_ITEMS + 1)}, "array"),
        ({"k" * (MAX_JSON_KEY_LENGTH + 1): None}, "key"),
        ({"integer": MAX_SIGNED_64 + 1}, "signed-64"),
        ({"integer": MIN_SIGNED_64 - 1}, "signed-64"),
        ({"float": math.inf}, "finite"),
        ({"float": -math.inf}, "finite"),
        ({"float": math.nan}, "finite"),
        (
            {"strings": ["x" * MAX_JSON_STRING_LENGTH] * 5},
            "UTF-8",
        ),
    ),
)
def test_metadata_and_event_payload_share_all_json_resource_bounds(
    payload: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(InvalidMissionDefinition, match=message):
        MissionDefinition(
            mission_id="mission-1",
            run_id="run-1",
            original_goal="done",
            metadata=payload,
        )
    with pytest.raises(InvalidMissionEvent, match=message):
        MissionEvent(
            kind="mission_failed",
            mission_id="mission-1",
            run_id="run-1",
            payload=payload,
        )


def test_metadata_and_payload_reject_cycles_without_recursion_errors() -> None:
    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic
    with pytest.raises(InvalidMissionDefinition, match="cyclic JSON"):
        MissionDefinition(
            mission_id="mission-1",
            run_id="run-1",
            original_goal="done",
            metadata=cyclic,
        )
    with pytest.raises(InvalidMissionEvent, match="cyclic JSON"):
        MissionEvent(
            kind="mission_failed",
            mission_id="mission-1",
            run_id="run-1",
            payload=cyclic,
        )


def test_plain_json_containers_and_signed_64_endpoints_are_accepted() -> None:
    definition = MissionDefinition(
        mission_id="mission-1",
        run_id="run-1",
        original_goal="done",
        metadata={
            "minimum": MIN_SIGNED_64,
            "maximum": MAX_SIGNED_64,
            "finite": 1.0e308,
        },
    )
    assert definition.to_dict()["metadata"] == {
        "minimum": MIN_SIGNED_64,
        "maximum": MAX_SIGNED_64,
        "finite": 1.0e308,
    }


def test_json_resource_limits_accept_their_exact_endpoints() -> None:
    exact_nodes = {
        f"node-{index}": None for index in range(MAX_JSON_NODES - 1)
    }
    exact_array = [None] * MAX_JSON_ARRAY_ITEMS
    assert len(bounded_json_copy(exact_nodes)) == MAX_JSON_NODES - 1
    assert len(bounded_json_copy(exact_array)) == MAX_JSON_ARRAY_ITEMS
    assert bounded_json_copy(_nested_object(MAX_JSON_DEPTH)) is not None
    assert len(bounded_json_copy("m" * MAX_JSON_STRING_LENGTH)) == (
        MAX_JSON_STRING_LENGTH
    )
    bounded_strings = bounded_json_copy(
        {"k" * MAX_JSON_KEY_LENGTH: "v" * MAX_JSON_STRING_LENGTH}
    )
    assert len(next(iter(bounded_strings))) == MAX_JSON_KEY_LENGTH
    assert len(next(iter(bounded_strings.values()))) == MAX_JSON_STRING_LENGTH


def test_direct_contracts_and_wire_round_trips_use_one_json_budget_scope() -> None:
    over_envelope_budget = {
        f"node-{index}": None for index in range(MAX_JSON_NODES - 1)
    }
    with pytest.raises(InvalidMissionDefinition, match="node"):
        MissionDefinition(
            mission_id="mission-1",
            run_id="run-1",
            original_goal="done",
            metadata=over_envelope_budget,
        )
    definition = _definition()
    event = _event()
    assert MissionDefinition.from_dict(definition.to_dict()) == definition
    assert MissionEvent.from_dict(event.to_dict()) == event


def test_oversized_obligation_graph_fails_before_recursive_or_expensive_work() -> None:
    obligations = tuple(
        ObligationSpec(
            id=f"obligation-{index}",
            desired_state="done",
            depends_on=(
                (f"obligation-{index - 1}",)
                if index
                else ()
            ),
        )
        for index in range(1100)
    )
    with pytest.raises(InvalidMissionDefinition, match="bounded JSON|node"):
        MissionDefinition(
            mission_id="mission-1",
            run_id="run-1",
            original_goal="done",
            obligations=obligations,
        )


@pytest.mark.parametrize(
    "field,value",
    (
        ("kind", "\ud800"),
        ("payload", {"value": "\ud800"}),
        ("payload", {"\ud800": "value"}),
    ),
)
def test_invalid_unicode_is_reported_as_a_mission_contract_error(
    field: str,
    value: Any,
) -> None:
    kwargs: dict[str, Any] = {
        "kind": "mission_failed",
        "mission_id": "mission-1",
        "run_id": "run-1",
        "payload": {},
    }
    kwargs[field] = value
    with pytest.raises(InvalidMissionEvent, match="Unicode"):
        MissionEvent(**kwargs)


def test_event_payload_schemas_reject_missing_unknown_and_legacy_fields() -> None:
    reducer = MissionReducer(_definition())
    with pytest.raises(InvalidMissionEvent, match="unknown"):
        reducer.apply(
            reducer.event(
                "mission_compiled",
                payload={"legacy": True},
            )
        )
    with pytest.raises(InvalidMissionEvent, match="missing"):
        reducer.apply(reducer.event("obligation_activated"))
    with pytest.raises(InvalidMissionEvent, match="unknown"):
        reducer.apply(
            reducer.event(
                "obligation_claimed",
                payload={
                    "obligation_id": "obligation-1",
                    "subject_id": "obligation-1",
                },
            )
        )


def test_mission_event_from_dict_rejects_noncanonical_payload_immediately() -> None:
    alias = _event().to_dict()
    alias["payload"]["subject_id"] = "obligation-1"
    with pytest.raises(InvalidMissionEvent, match="unknown"):
        MissionEvent.from_dict(alias)

    missing = _event().to_dict()
    missing["payload"].pop("obligation_id")
    with pytest.raises(InvalidMissionEvent, match="missing"):
        MissionEvent.from_dict(missing)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("verification_id", 123),
        ("facts", None),
        ("facts", True),
        ("reason", []),
        ("failure_kind", 0),
    ),
)
def test_event_payload_valid_fields_still_require_literal_types(
    field: str,
    value: Any,
) -> None:
    wire = _event().to_dict()
    wire["kind"] = "verification_recorded"
    wire["payload"] = {
        "obligation_id": "obligation-1",
        "verified": False,
        "claim_fingerprint": "claim-1",
        field: value,
    }
    with pytest.raises(InvalidMissionEvent):
        MissionEvent.from_dict(wire)


def test_unknown_event_kinds_and_reserved_payloads_fail_closed() -> None:
    wire = _event().to_dict()
    wire["kind"] = "custom_event"
    wire["payload"] = {}
    with pytest.raises(InvalidMissionEvent, match="unsupported MissionEvent kind"):
        MissionEvent.from_dict(wire)

    reserved = _event().to_dict()
    reserved["kind"] = "planner_requested"
    reserved["payload"] = {"description": "legacy planner input"}
    with pytest.raises(InvalidMissionEvent, match="unknown"):
        MissionEvent.from_dict(reserved)


def test_every_implemented_event_payload_has_an_exact_schema() -> None:
    cases: tuple[tuple[str, dict[str, Any], tuple[str, ...]], ...] = (
        ("mission_compiled", {}, ()),
        (
            "obligation_activated",
            {"obligation_id": "obligation-1"},
            ("obligation_id",),
        ),
        (
            "artifact_bound",
            {"artifact": _artifact().to_dict()},
            ("artifact",),
        ),
        (
            "target_bound",
            {"target_binding": _target().to_dict()},
            ("target_binding",),
        ),
        ("target_invalidated", {}, ()),
        (
            "transition_recorded",
            {
                "obligation_id": "obligation-1",
                "input_completed": True,
            },
            ("obligation_id", "input_completed"),
        ),
        (
            "obligation_claimed",
            {"obligation_id": "obligation-1"},
            ("obligation_id",),
        ),
        (
            "verification_recorded",
            {
                "obligation_id": "obligation-1",
                "verified": True,
                "claim_fingerprint": "claim-1",
                "verification_id": "verification-1",
                "satisfied_criteria": ["visible"],
            },
            (
                "obligation_id",
                "verified",
                "claim_fingerprint",
                "verification_id",
                "satisfied_criteria",
            ),
        ),
        (
            "fact_recorded",
            {"fact": _fact().to_dict()},
            ("fact",),
        ),
        (
            "obligation_blocked",
            {"obligation_id": "obligation-1"},
            ("obligation_id",),
        ),
        (
            "obligation_unblocked",
            {"obligation_id": "obligation-1"},
            ("obligation_id",),
        ),
        ("mission_blocked", {}, ()),
        ("mission_failed", {}, ()),
        ("mission_canceled", {}, ()),
        (
            "mission_verified",
            {
                "verified": True,
                "verification_id": "mission-verification-1",
                "accepted_obligation_verification_ids": ["verification-1"],
                "aggregate_evidence_id": "aggregate-1",
                "aggregate_payload_hash": SHA256,
            },
            (
                "verified",
                "verification_id",
                "accepted_obligation_verification_ids",
                "aggregate_evidence_id",
                "aggregate_payload_hash",
            ),
        ),
    )
    for event_type, payload, required in cases:
        event = MissionEvent(
            kind=event_type,
            mission_id="mission-1",
            run_id="run-1",
            payload=payload,
        )
        assert MissionEvent.from_dict(event.to_dict()) == event

        unknown = event.to_dict()
        unknown["payload"]["legacy_or_unknown"] = "rejected"
        with pytest.raises(InvalidMissionEvent, match="unknown"):
            MissionEvent.from_dict(unknown)

        for field_name in required:
            missing = event.to_dict()
            missing["payload"].pop(field_name)
            with pytest.raises(InvalidMissionEvent, match="missing"):
                MissionEvent.from_dict(missing)


@pytest.mark.parametrize(
    "payload",
    (
        {
            "obligation_id": "obligation-1",
            "verified": True,
            "claim_fingerprint": "claim-1",
        },
        {
            "obligation_id": "obligation-1",
            "verified": False,
            "claim_fingerprint": "claim-1",
            "verification_id": "ignored-verification",
        },
        {
            "obligation_id": "obligation-1",
            "verified": False,
            "claim_fingerprint": "claim-1",
            "facts": [],
        },
    ),
)
def test_verification_event_verdict_variants_reject_missing_or_ignored_fields(
    payload: dict[str, Any],
) -> None:
    wire = _event().to_dict()
    wire["kind"] = "verification_recorded"
    wire["payload"] = payload
    with pytest.raises(InvalidMissionEvent, match="verdict fields"):
        MissionEvent.from_dict(wire)


def test_mission_verified_event_requires_literal_true_at_wire_boundary() -> None:
    wire = _event().to_dict()
    wire["kind"] = "mission_verified"
    wire["payload"] = {
        "verified": False,
        "verification_id": "mission-verification-1",
        "accepted_obligation_verification_ids": ["verification-1"],
        "aggregate_evidence_id": "aggregate-1",
        "aggregate_payload_hash": SHA256,
    }
    with pytest.raises(InvalidMissionEvent, match="literal boolean true"):
        MissionEvent.from_dict(wire)


def test_event_payload_string_arrays_reject_explicit_none() -> None:
    reducer = MissionReducer(_definition())
    reducer.apply(
        reducer.event(
            "obligation_claimed",
            payload={"obligation_id": "obligation-1"},
        )
    )
    with pytest.raises(InvalidMissionEvent, match="JSON array"):
        reducer.apply(
            reducer.event(
                "verification_recorded",
                payload={
                    "obligation_id": "obligation-1",
                    "verified": False,
                    "missing_conditions": None,
                },
            )
        )


def test_non_json_container_subclasses_are_rejected_recursively() -> None:
    class ListSubclass(list[Any]):
        pass

    class DictSubclass(dict[str, Any]):
        pass

    for value in (
        {"nested": ListSubclass()},
        {"nested": DictSubclass()},
        {"nested": (1, 2)},
        {"nested": {1, 2}},
    ):
        with pytest.raises(InvalidMissionDefinition):
            MissionDefinition(
                mission_id="mission-1",
                run_id="run-1",
                original_goal="done",
                metadata=value,
            )
        with pytest.raises(InvalidMissionEvent):
            MissionEvent(
                kind="mission_failed",
                mission_id="mission-1",
                run_id="run-1",
                payload=value,
            )


def test_wire_values_are_detached_before_contract_construction() -> None:
    wire = _event().to_dict()
    restored = MissionEvent.from_dict(copy.deepcopy(wire))
    wire["payload"]["obligation_id"] = "tampered"
    assert restored.to_dict()["payload"]["obligation_id"] == "obligation-1"
