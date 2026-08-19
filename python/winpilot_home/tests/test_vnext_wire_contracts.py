"""Wire-boundary coverage for current vNext Mission contracts."""
from __future__ import annotations

import math
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
)
from winpilot.vnext.mission import (
    ArtifactRef,
    EvidencePolicy,
    EvidenceRef,
    InvalidMissionDefinition,
    InvalidMissionEvent,
    MissionDefinition,
    MissionError,
    MissionEvent,
    ObligationSpec,
    TargetBinding,
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
        kind="transition_recorded",
        mission_id="mission-1",
        run_id="run-1",
        base_revision=0,
        payload={
            "obligation_id": "obligation-1",
            "input_completed": False,
        },
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
        executable_identity="notepad.exe",
        title_snapshot="Notepad",
        epoch=0,
    )


WireParser = Callable[[dict[str, Any]], Any]
WIRE_CASES: tuple[tuple[str, WireParser, Callable[[], dict[str, Any]]], ...] = (
    ("EvidencePolicy", EvidencePolicy.from_dict, lambda: EvidencePolicy().to_dict()),
    ("ObligationSpec", ObligationSpec.from_dict, lambda: _definition().obligations[0].to_dict()),
    ("MissionDefinition", MissionDefinition.from_dict, lambda: _definition().to_dict()),
    ("EvidenceRef", EvidenceRef.from_dict, lambda: _evidence().to_dict()),
    ("ArtifactRef", ArtifactRef.from_dict, lambda: _artifact().to_dict()),
    ("TargetBinding", TargetBinding.from_dict, lambda: _target().to_dict()),
    ("MissionEvent", MissionEvent.from_dict, lambda: _event().to_dict()),
)


@pytest.mark.parametrize(
    "name,parser,wire_factory",
    WIRE_CASES,
    ids=[case[0] for case in WIRE_CASES],
)
def test_wire_parsers_round_trip_one_exact_schema(
    name: str,
    parser: WireParser,
    wire_factory: Callable[[], dict[str, Any]],
) -> None:
    del name
    wire = wire_factory()
    assert parser(wire).to_dict() == wire


@pytest.mark.parametrize(
    "name,parser,wire_factory",
    WIRE_CASES,
    ids=[case[0] for case in WIRE_CASES],
)
def test_wire_parsers_require_plain_complete_exact_objects(
    name: str,
    parser: WireParser,
    wire_factory: Callable[[], dict[str, Any]],
) -> None:
    del name
    wire = wire_factory()
    first = next(iter(wire))

    missing = dict(wire)
    missing.pop(first)
    with pytest.raises(MissionError):
        parser(missing)

    unknown = dict(wire)
    unknown["legacy"] = None
    with pytest.raises(MissionError):
        parser(unknown)

    with pytest.raises(MissionError):
        parser(tuple(wire.items()))  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ("false", 0, 1, None))
def test_boolean_fields_reject_truthiness_coercion(value: Any) -> None:
    policy = EvidencePolicy().to_dict()
    policy["allow_independent_visual_verifier"] = value
    with pytest.raises(InvalidMissionDefinition, match="boolean"):
        EvidencePolicy.from_dict(policy)

    event = _event().to_dict()
    event["payload"]["input_completed"] = value
    with pytest.raises(InvalidMissionEvent, match="boolean"):
        MissionEvent.from_dict(event)


@pytest.mark.parametrize("value", ("1", 1.0, True, None))
def test_integer_fields_reject_numeric_coercion(value: Any) -> None:
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


def test_current_event_surface_has_exact_payload_schemas() -> None:
    cases: tuple[tuple[str, dict[str, Any], tuple[str, ...]], ...] = (
        ("target_bound", {"target_binding": _target().to_dict()}, ("target_binding",)),
        (
            "transition_recorded",
            {"obligation_id": "obligation-1", "input_completed": True},
            ("obligation_id", "input_completed"),
        ),
        ("actor_completed", {"obligation_id": "obligation-1"}, ("obligation_id",)),
        ("obligation_blocked", {"obligation_id": "obligation-1"}, ("obligation_id",)),
        ("mission_canceled", {}, ()),
    )
    for kind, payload, required in cases:
        event = MissionEvent(
            kind=kind,
            mission_id="mission-1",
            run_id="run-1",
            payload=payload,
        )
        assert MissionEvent.from_dict(event.to_dict()) == event

        unknown = event.to_dict()
        unknown["payload"]["legacy"] = True
        with pytest.raises(InvalidMissionEvent, match="unknown"):
            MissionEvent.from_dict(unknown)

        for field in required:
            missing = event.to_dict()
            missing["payload"].pop(field)
            with pytest.raises(InvalidMissionEvent, match="missing"):
                MissionEvent.from_dict(missing)


@pytest.mark.parametrize(
    "kind",
    (
        "mission_compiled",
        "obligation_activated",
        "artifact_bound",
        "target_invalidated",
        "obligation_claimed",
        "verification_recorded",
        "fact_recorded",
        "obligation_unblocked",
        "mission_verified",
        "mission_blocked",
        "mission_failed",
        "planner_requested",
        "custom_event",
    ),
)
def test_removed_and_unknown_event_kinds_fail_at_the_wire(kind: str) -> None:
    wire = _event().to_dict()
    wire["kind"] = kind
    wire["payload"] = {}
    with pytest.raises(InvalidMissionEvent, match="unsupported MissionEvent kind"):
        MissionEvent.from_dict(wire)


@pytest.mark.parametrize(
    "payload,message",
    (
        ({"next": {"next": {}}}, None),
        ({"text": "x" * (MAX_JSON_STRING_LENGTH + 1)}, "string"),
        ({"array": [None] * (MAX_JSON_ARRAY_ITEMS + 1)}, "array"),
        ({"k" * (MAX_JSON_KEY_LENGTH + 1): None}, "key"),
        ({"integer": MAX_SIGNED_64 + 1}, "signed-64"),
        ({"integer": MIN_SIGNED_64 - 1}, "signed-64"),
    ),
)
def test_json_resource_bounds_apply_before_event_schema(
    payload: dict[str, Any],
    message: str | None,
) -> None:
    if message is None:
        pytest.skip("small nested object is intentionally valid JSON")
    with pytest.raises(InvalidMissionEvent, match=message):
        MissionEvent(
            kind="mission_canceled",
            mission_id="mission-1",
            run_id="run-1",
            payload=payload,
        )


def test_json_cycles_fail_without_recursion_errors() -> None:
    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic
    with pytest.raises(InvalidMissionEvent, match="cyclic JSON"):
        MissionEvent(
            kind="mission_canceled",
            mission_id="mission-1",
            run_id="run-1",
            payload=cyclic,
        )


def test_signed_64_endpoints_are_valid_definition_metadata() -> None:
    definition = MissionDefinition(
        mission_id="mission-1",
        run_id="run-1",
        original_goal="done",
        metadata={"minimum": MIN_SIGNED_64, "maximum": MAX_SIGNED_64},
    )
    assert definition.to_dict()["metadata"] == {
        "minimum": MIN_SIGNED_64,
        "maximum": MAX_SIGNED_64,
    }
