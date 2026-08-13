"""Pure ToolCatalog, grant, state, and Capability Broker tests."""
from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import random
from types import MappingProxyType

import pytest

from vnext_import_boundary_ast import analyze_path

from winpilot.vnext.capabilities import (
    ArtifactState,
    AuthoritativeFrameState,
    BrokerProjection,
    BrokerRuntimeState,
    CapabilityBroker,
    CapabilityContractError,
    CapabilityGrant,
    ExecutionMedium,
    ExecutionRoute,
    ExecutorOffer,
    MutationKind,
    PointerDisposition,
    PointerLeaseState,
    TargetState,
    ToolCatalog,
    ToolRoute,
    ToolSpec,
)
from winpilot.vnext.capabilities.state import _broker_projection_id
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
    EvidenceRef,
    MissionDefinition,
    MissionReducer,
    MissionStatus,
    ObligationSpec,
    TargetBinding,
)
from winpilot.vnext.profiling import (
    DEFAULT_CAPABILITY_CATALOG,
    ExecutionDepth,
    ExecutionPolicy,
    TaskDimensions,
    TaskProfileProposal,
)


ALL_CAPABILITIES = tuple(sorted(DEFAULT_CAPABILITY_CATALOG))
GUI_GATES = (
    "target_bound",
    "authoritative_frame",
    "runtime_healthy",
)
DRAG_GATES = (
    "target_bound",
    "authoritative_frame",
    "runtime_healthy",
)


def _schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }


def _tool(
    name: str,
    route: ToolRoute,
    mutation: MutationKind,
    *,
    capabilities: tuple[str, ...] = (),
    prerequisites: tuple[str, ...] = (),
    authorization_scope: str | None = None,
    execution_medium: ExecutionMedium | None = None,
    order: int,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        schema=_schema(),
        route=route,
        mutation_kind=mutation,
        execution_medium=execution_medium,
        required_any_capabilities=capabilities,
        prerequisites=prerequisites,
        authorization_scope=authorization_scope,
        order=order,
    )


def _catalog() -> ToolCatalog:
    return ToolCatalog(
        (
            _tool(
                "observe",
                ToolRoute.VISUAL_ACTOR,
                MutationKind.NONE,
                execution_medium=ExecutionMedium.CONTROL,
                order=1,
            ),
            _tool(
                "read_text",
                ToolRoute.VISUAL_ACTOR,
                MutationKind.NONE,
                capabilities=("visual.read_text",),
                execution_medium=ExecutionMedium.CONTROL,
                order=2,
            ),
            _tool(
                "click",
                ToolRoute.VISUAL_ACTOR,
                MutationKind.GUI,
                capabilities=("visual.interact",),
                prerequisites=GUI_GATES,
                order=3,
            ),
            _tool(
                "type_text",
                ToolRoute.VISUAL_ACTOR,
                MutationKind.GUI,
                capabilities=("visual.interact",),
                prerequisites=GUI_GATES,
                order=4,
            ),
            _tool(
                "hotkey",
                ToolRoute.VISUAL_ACTOR,
                MutationKind.GUI,
                capabilities=("visual.interact",),
                prerequisites=GUI_GATES,
                order=5,
            ),
            _tool(
                "scroll",
                ToolRoute.VISUAL_ACTOR,
                MutationKind.GUI,
                capabilities=("visual.interact",),
                prerequisites=GUI_GATES,
                order=6,
            ),
            _tool(
                "draw_strokes",
                ToolRoute.VISUAL_ACTOR,
                MutationKind.GUI,
                capabilities=("visual.draw",),
                prerequisites=GUI_GATES,
                order=7,
            ),
            _tool(
                "wait",
                ToolRoute.VISUAL_ACTOR,
                MutationKind.NONE,
                execution_medium=ExecutionMedium.CONTROL,
                order=8,
            ),
            _tool(
                "request_capability",
                ToolRoute.VISUAL_ACTOR,
                MutationKind.NONE,
                execution_medium=ExecutionMedium.CONTROL,
                order=9,
            ),
            _tool(
                "claim_obligation_done",
                ToolRoute.VISUAL_ACTOR,
                MutationKind.NONE,
                execution_medium=ExecutionMedium.CONTROL,
                order=10,
            ),
            _tool(
                "blocked",
                ToolRoute.VISUAL_ACTOR,
                MutationKind.NONE,
                execution_medium=ExecutionMedium.CONTROL,
                order=11,
            ),
            _tool(
                "send_text",
                ToolRoute.PROVIDER,
                MutationKind.EXTERNAL,
                capabilities=("communication.send_text",),
                prerequisites=(
                    "recipient_bound",
                    "adapter_live",
                    "receipt_capable",
                    "runtime_healthy",
                ),
                authorization_scope="external.send",
                execution_medium=ExecutionMedium.EXTERNAL,
                order=20,
            ),
            _tool(
                "send_artifact",
                ToolRoute.PROVIDER,
                MutationKind.EXTERNAL,
                capabilities=("communication.send_artifact",),
                prerequisites=(
                    "current_run_artifact",
                    "recipient_bound",
                    "adapter_live",
                    "receipt_capable",
                    "runtime_healthy",
                ),
                authorization_scope="external.send",
                execution_medium=ExecutionMedium.EXTERNAL,
                order=21,
            ),
        )
    )


def _catalog_with_drag() -> ToolCatalog:
    return ToolCatalog(
        (
            *_catalog().tools,
            _tool(
                "drag",
                ToolRoute.VISUAL_ACTOR,
                MutationKind.GUI,
                capabilities=("visual.interact",),
                prerequisites=DRAG_GATES,
                order=12,
            ),
        )
    )


def _mission(capability: str = "visual.interact", *, bound: bool = True):
    definition = MissionDefinition(
        mission_id="m1",
        run_id="r1",
        original_goal="Complete the current unknown GUI task",
        obligations=(
            ObligationSpec(
                id="o1",
                desired_state="the requested state exists",
                acceptance_criteria=(
                    "the state is visibly or deterministically confirmed",
                ),
                required_capabilities=(capability,),
            ),
        ),
    )
    evidence = ()
    if bound:
        evidence = (
            EvidenceRef(
                id="e-target",
                mission_id="m1",
                run_id="r1",
                kind="target",
                trust="deterministic",
                obligation_id="o1",
                payload_hash="target-payload-hash",
                frame_id=9,
            ),
        )
    reducer = MissionReducer(definition, evidence=evidence)
    if bound:
        reducer.apply(
            reducer.event(
                "target_bound",
                payload={
                    "target_binding": TargetBinding(
                        binding_id="target-1",
                        hwnd=101,
                        process_id=7,
                        executable_identity="opaque-process-identity",
                        title_snapshot="",
                        established_by_evidence_ref="e-target",
                        epoch=3,
                    ).to_dict(),
                },
                evidence_refs=("e-target",),
            )
        )
    return reducer.projection


def _mission_with_artifact_binding():
    definition = MissionDefinition(
        mission_id="m1",
        run_id="r1",
        original_goal="capture and send one artifact",
        obligations=(
            ObligationSpec(
                id="capture",
                desired_state="an artifact exists",
                acceptance_criteria=("artifact existence is confirmed",),
                required_capabilities=("artifact.capture",),
                output_binding_names=("current_artifact",),
            ),
            ObligationSpec(
                id="send",
                desired_state="the same artifact is delivered",
                acceptance_criteria=("delivery is confirmed",),
                depends_on=("capture",),
                input_artifact_refs=("artifact-1",),
                required_capabilities=("communication.send_artifact",),
            ),
        ),
    )
    evidence = (
        EvidenceRef(
            id="capture-proof",
            mission_id="m1",
            run_id="r1",
            obligation_id="capture",
            kind="oracle",
            trust="deterministic",
            payload_hash="capture-proof-hash",
        ),
        EvidenceRef(
            id="artifact-proof",
            mission_id="m1",
            run_id="r1",
            obligation_id="capture",
            kind="artifact",
            trust="deterministic",
            payload_hash="artifact-proof-hash",
            artifact_id="artifact-1",
            content_sha256="a" * 64,
        ),
    )
    reducer = MissionReducer(definition, evidence=evidence)
    reducer.apply(
        reducer.event(
            "obligation_claimed",
            payload={"obligation_id": "capture"},
        )
    )
    reducer.apply(
        reducer.event(
            "verification_recorded",
            payload={
                "obligation_id": "capture",
                "verified": True,
                "satisfied_criteria": ["artifact existence is confirmed"],
                "verification_id": "capture-verification",
                "verifier_kind": "deterministic",
            },
            evidence_refs=("capture-proof",),
        )
    )
    artifact = ArtifactRef(
        artifact_id="artifact-1",
        media_type="image/png",
        sha256="a" * 64,
        producer_obligation_id="capture",
        evidence_ref="artifact-proof",
    )
    reducer.apply(
        reducer.event(
            "artifact_bound",
            payload={
                "binding_name": "current_artifact",
                "artifact": artifact.to_dict(),
            },
            evidence_refs=("artifact-proof",),
        )
    )
    return reducer.projection


def _profile(mission, capabilities: tuple[str, ...] | None = None):
    accepted = capabilities
    if accepted is None:
        accepted = mission.obligation_definition(
            mission.active_obligation_id
        ).required_capabilities
    proposal = TaskProfileProposal(
        dimensions=TaskDimensions(),
        recommended_depth=ExecutionDepth.STANDARD,
        minimum_depth=ExecutionDepth.DIRECT,
        broad_capability_hypotheses=accepted,
        confidence=1.0,
        rationale=("deterministic test fixture",),
    )
    return ExecutionPolicy(min_confidence=0.0).evaluate(
        proposal,
        mission_id=mission.mission_id,
        goal_revision=mission.definition.goal_revision,
        available_capabilities=ALL_CAPABILITIES,
    )


def _state(**overrides):
    values = {
        "turn_index": 4,
        "available_capabilities": ALL_CAPABILITIES,
        "target": TargetState("target-1", 101, 3, True),
        "frame": AuthoritativeFrameState(
            9, 101, 3, True, True, "target_window"
        ),
        "pointer": PointerLeaseState(PointerDisposition.RELEASED),
        "runtime_healthy": True,
    }
    values.update(overrides)
    return BrokerRuntimeState(**values)


def _provider_offer(
    *,
    executor_id: str = "provider-1",
    capability: str = "communication.send_text",
    tool_name: str = "send_text",
    receipts: tuple[str, ...] = ("delivery.receipt",),
) -> ExecutorOffer:
    return ExecutorOffer(
        executor_id=executor_id,
        route=ToolRoute.PROVIDER,
        supported_capabilities=(capability,),
        tool_names=(tool_name,),
        available=True,
        live=True,
        authorization_scopes=("external.send",),
        receipt_kinds=receipts,
    )


def _wire_samples():
    catalog = _catalog()
    state = _state(
        artifacts=(
            ArtifactState(
                "artifact-1",
                "r1",
                "a" * 64,
                "current_artifact",
                True,
            ),
        ),
        executor_offers=(_provider_offer(),),
        authorization_scopes=("external.send",),
    )
    mission = _mission()
    projection = CapabilityBroker(catalog).project(
        mission,
        _profile(mission),
        state,
    )
    grant = CapabilityGrant.issue(
        mission_id="m1",
        run_id="r1",
        capability="visual.interact",
        obligation_id="o1",
        mission_revision=mission.revision,
        issued_turn=state.turn_index,
        expires_after_turns=2,
        policy_reason="canonical test grant",
    )
    values = (
        catalog.tools[0],
        catalog,
        grant,
        state.target,
        state.frame,
        state.pointer,
        state.artifacts[0],
        state.executor_offers[0],
        state,
        projection,
    )
    return tuple((type(value), value.to_dict()) for value in values)


@pytest.mark.parametrize(
    ("contract", "raw"),
    _wire_samples(),
    ids=lambda value: value.__name__ if isinstance(value, type) else None,
)
def test_every_capability_wire_entry_requires_plain_exact_complete_objects(
    contract,
    raw,
) -> None:
    assert contract.from_dict(raw).to_dict() == raw

    class DictSubclass(dict):
        pass

    for non_plain in (MappingProxyType(raw), DictSubclass(raw)):
        with pytest.raises(CapabilityContractError, match="plain JSON object"):
            contract.from_dict(non_plain)

    missing = dict(raw)
    missing.pop(next(iter(missing)))
    with pytest.raises(CapabilityContractError, match="missing="):
        contract.from_dict(missing)

    for alias in ("type", "revision", "evidence_id", "description", "criteria"):
        unknown = {**raw, alias: "legacy"}
        with pytest.raises(CapabilityContractError, match="unknown="):
            contract.from_dict(unknown)


@pytest.mark.parametrize(
    ("contract", "raw", "field"),
    (
        (ToolSpec, _catalog().tools[0].to_dict(), "prerequisites"),
        (ToolCatalog, _catalog().to_dict(), "tools"),
        (
            ExecutorOffer,
            _provider_offer().to_dict(),
            "supported_capabilities",
        ),
        (
            BrokerRuntimeState,
            _state().to_dict(),
            "available_capabilities",
        ),
        (
            BrokerProjection,
            _wire_samples()[-1][1],
            "tool_names",
        ),
    ),
)
@pytest.mark.parametrize("replacement", (None, (), "visual.interact"))
def test_wire_array_fields_require_literal_lists(
    contract,
    raw,
    field,
    replacement,
) -> None:
    changed = {**raw, field: replacement}
    with pytest.raises(CapabilityContractError):
        contract.from_dict(changed)


@pytest.mark.parametrize(
    ("contract", "raw", "field", "bad"),
    (
        (TargetState, TargetState("target-1", 101, 3, True).to_dict(), "live", "true"),
        (TargetState, TargetState("target-1", 101, 3, True).to_dict(), "live", 1),
        (TargetState, TargetState("target-1", 101, 3, True).to_dict(), "live", None),
        (TargetState, TargetState("target-1", 101, 3, True).to_dict(), "hwnd", "101"),
        (TargetState, TargetState("target-1", 101, 3, True).to_dict(), "hwnd", True),
        (ToolSpec, _catalog().tools[0].to_dict(), "order", "1"),
        (ToolSpec, _catalog().tools[0].to_dict(), "order", True),
        (ToolSpec, _catalog().tools[0].to_dict(), "order", None),
        (
            ExecutorOffer,
            _provider_offer().to_dict(),
            "route",
            "provider ",
        ),
        (
            ExecutorOffer,
            _provider_offer().to_dict(),
            "supported_capabilities",
            [" communication.send_text"],
        ),
    ),
)
def test_wire_scalars_never_coerce_or_normalize(
    contract,
    raw,
    field,
    bad,
) -> None:
    with pytest.raises(CapabilityContractError):
        contract.from_dict({**raw, field: bad})


def _bounded_tool_wire_cases():
    base = _catalog().tools[0].to_dict()

    cyclic = {**base, "schema": {"type": "object"}}
    cyclic["schema"]["cycle"] = cyclic["schema"]

    deep_schema = {"type": "object"}
    leaf = deep_schema
    for _ in range(MAX_JSON_DEPTH + 2):
        child = {"value": None}
        leaf["child"] = child
        leaf = child

    return (
        ("cycle", cyclic),
        ("depth", {**base, "schema": deep_schema}),
        (
            "nodes",
            {
                **base,
                "schema": {
                    "type": "object",
                    "nodes": {
                        f"k{index}": None
                        for index in range(MAX_JSON_NODES)
                    },
                },
            },
        ),
        (
            "string",
            {
                **base,
                "schema": {
                    "type": "object",
                    "value": "x" * (MAX_JSON_STRING_LENGTH + 1),
                },
            },
        ),
        (
            "array",
            {
                **base,
                "schema": {
                    "type": "object",
                    "values": [None] * (MAX_JSON_ARRAY_ITEMS + 1),
                },
            },
        ),
        (
            "key",
            {
                **base,
                "schema": {
                    "type": "object",
                    "k" * (MAX_JSON_KEY_LENGTH + 1): None,
                },
            },
        ),
        (
            "utf8",
            {
                **base,
                "schema": {
                    "type": "object",
                    "values": [
                        "\u754c" * 900_000
                        for _ in range(2)
                    ],
                },
            },
        ),
        (
            "high-int",
            {
                **base,
                "schema": {
                    "type": "object",
                    "maximum": MAX_SIGNED_64 + 1,
                },
            },
        ),
        (
            "low-int",
            {
                **base,
                "schema": {
                    "type": "object",
                    "minimum": MIN_SIGNED_64 - 1,
                },
            },
        ),
        (
            "nan",
            {
                **base,
                "schema": {"type": "object", "value": float("nan")},
            },
        ),
        (
            "infinity",
            {
                **base,
                "schema": {"type": "object", "value": float("inf")},
            },
        ),
    )


@pytest.mark.parametrize(
    ("case", "raw"),
    _bounded_tool_wire_cases(),
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_tool_spec_wire_rejects_every_shared_json_budget(
    case,
    raw,
) -> None:
    with pytest.raises(CapabilityContractError, match="bounded JSON"):
        ToolSpec.from_dict(raw)


def test_tool_spec_wire_accepts_signed_64_and_finite_float_endpoints() -> None:
    raw = _catalog().tools[0].to_dict()
    raw["schema"] = {
        "type": "object",
        "minimum": MIN_SIGNED_64,
        "maximum": MAX_SIGNED_64,
        "finite": 1e308,
    }
    restored = ToolSpec.from_dict(raw)
    assert restored.to_dict()["schema"] == raw["schema"]


def test_catalog_and_runtime_state_are_strict_content_addressed_values() -> None:
    catalog = _catalog()
    restored = ToolCatalog.from_dict(catalog.to_dict())
    assert restored.fingerprint == catalog.fingerprint
    assert (
        ToolCatalog(tuple(reversed(catalog.tools))).fingerprint
        == catalog.fingerprint
    )
    raw_catalog = catalog.to_dict()
    raw_catalog["fingerprint"] = "0" * 64
    with pytest.raises(CapabilityContractError, match="fingerprint"):
        ToolCatalog.from_dict(raw_catalog)

    state = _state()
    assert BrokerRuntimeState.from_dict(state.to_dict()) == state
    raw_state = state.to_dict()
    raw_state["runtime_healthy"] = False
    with pytest.raises(CapabilityContractError, match="fingerprint"):
        BrokerRuntimeState.from_dict(raw_state)
    with pytest.raises(CapabilityContractError):
        BrokerRuntimeState.from_dict(
            {**state.to_dict(), "task_text": "benchmark case"}
        )


def test_execution_medium_defaults_fail_closed_and_round_trips() -> None:
    local = ToolSpec(
        "run_shell",
        _schema(),
        ToolRoute.VISUAL_ACTOR,
        MutationKind.NONE,
    )
    gui = _tool(
        "click",
        ToolRoute.VISUAL_ACTOR,
        MutationKind.GUI,
        capabilities=("visual.interact",),
        prerequisites=GUI_GATES,
        order=1,
    )
    control = _tool(
        "blocked",
        ToolRoute.VISUAL_ACTOR,
        MutationKind.NONE,
        execution_medium=ExecutionMedium.CONTROL,
        order=2,
    )

    assert local.execution_medium is ExecutionMedium.LOCAL
    assert gui.execution_medium is ExecutionMedium.GUI
    assert control.execution_medium is ExecutionMedium.CONTROL
    for tool in (local, gui, control):
        assert ToolSpec.from_dict(tool.to_dict()) == tool

    local_catalog = ToolCatalog((local,))
    control_catalog = ToolCatalog((control,))
    assert local_catalog.fingerprint != control_catalog.fingerprint

    with pytest.raises(CapabilityContractError, match="execution medium"):
        _tool(
            "unsafe_click",
            ToolRoute.VISUAL_ACTOR,
            MutationKind.GUI,
            capabilities=("visual.interact",),
            prerequisites=GUI_GATES,
            execution_medium=ExecutionMedium.LOCAL,
            order=1,
        )
    invalid = local.to_dict()
    invalid["execution_medium"] = "gui_only"
    with pytest.raises(CapabilityContractError, match="literal string"):
        ToolSpec.from_dict(invalid)
    legacy = local.to_dict()
    legacy["format"] = "tool-spec/0"
    with pytest.raises(CapabilityContractError, match="unsupported"):
        ToolSpec.from_dict(legacy)


def test_catalog_schema_is_immutable_and_bounded_json_fails_closed() -> None:
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
    }
    spec = ToolSpec(
        "observe",
        schema,
        ToolRoute.VISUAL_ACTOR,
        MutationKind.NONE,
    )
    schema["properties"]["value"]["type"] = "number"
    assert spec.to_dict()["schema"]["properties"]["value"]["type"] == "string"
    with pytest.raises(TypeError):
        spec.schema["properties"]["value"]["type"] = "boolean"

    cyclic = {"type": "object"}
    cyclic["properties"] = cyclic
    with pytest.raises(CapabilityContractError, match="cyclic"):
        ToolSpec(
            "observe",
            cyclic,
            ToolRoute.VISUAL_ACTOR,
            MutationKind.NONE,
        )

    deep = leaf = {"type": "object"}
    for index in range(1200):
        nested = {"level": index}
        leaf["properties"] = nested
        leaf = nested
    with pytest.raises(CapabilityContractError, match="depth"):
        ToolSpec(
            "observe",
            deep,
            ToolRoute.VISUAL_ACTOR,
            MutationKind.NONE,
        )
    with pytest.raises(CapabilityContractError, match="signed-64"):
        ToolSpec(
            "observe",
            {"type": "object", "maximum": 10**5000},
            ToolRoute.VISUAL_ACTOR,
            MutationKind.NONE,
        )


@pytest.mark.parametrize(
    ("route", "mutation_kind"),
    (
        (ToolRoute.VISUAL_ACTOR, MutationKind.EXTERNAL),
        (ToolRoute.PROVIDER, MutationKind.GUI),
        (ToolRoute.DETERMINISTIC, MutationKind.GUI),
    ),
)
def test_tool_route_cannot_create_a_second_gui_writer(
    route, mutation_kind
) -> None:
    with pytest.raises(CapabilityContractError):
        ToolSpec(
            "unsafe_tool",
            _schema(),
            route,
            mutation_kind,
            required_any_capabilities=("visual.interact",),
            authorization_scope=(
                "external.write"
                if mutation_kind is MutationKind.EXTERNAL
                else None
            ),
        )


def test_gui_tools_do_not_require_policy_gates_or_capability_tokens() -> None:
    click = _tool(
        "click",
        ToolRoute.VISUAL_ACTOR,
        MutationKind.GUI,
        order=1,
    )
    assert click.prerequisites == ()
    assert click.required_any_capabilities == ()

    drag = _tool(
        "drag",
        ToolRoute.VISUAL_ACTOR,
        MutationKind.GUI,
        order=1,
    )
    assert drag.prerequisites == ()
    assert drag.required_any_capabilities == ()


@pytest.mark.parametrize(
    ("factory", "values"),
    (
        (TargetState, ("target-1", 101, 3, "true")),
        (
            AuthoritativeFrameState,
            (9, 101, 3, 1, True, "target_window"),
        ),
        (
            PointerLeaseState,
            (PointerDisposition.RELEASED, None, "false"),
        ),
        (
            ArtifactState,
            ("artifact-1", "r1", "a" * 64, "binding", 1),
        ),
    ),
)
def test_runtime_state_booleans_are_strict(factory, values) -> None:
    with pytest.raises(CapabilityContractError, match="boolean"):
        factory(*values)


def test_identity_hash_and_authorization_values_do_not_coerce() -> None:
    with pytest.raises(CapabilityContractError, match="string"):
        ArtifactState(123, "r1", "a" * 64, "binding", False)
    with pytest.raises(CapabilityContractError, match="string"):
        ArtifactState(
            "artifact-1", "r1", int("1" * 64), "binding", False
        )
    with pytest.raises(CapabilityContractError, match="string"):
        ExecutorOffer(
            executor_id="provider-1",
            route=ToolRoute.PROVIDER,
            supported_capabilities=("communication.send_text",),
            tool_names=("send_text",),
            available=True,
            live=True,
            authorization_scopes=(999,),
            receipt_kinds=("receipt",),
        )


def test_defaults_and_pointer_ownership_fail_closed() -> None:
    with pytest.raises(CapabilityContractError, match="held pointer"):
        PointerLeaseState(PointerDisposition.UNKNOWN, "lease-1", True)
    state = BrokerRuntimeState(
        turn_index=0,
        available_capabilities=("visual.interact",),
    )
    assert state.pointer.disposition is PointerDisposition.UNKNOWN
    assert not state.runtime_healthy
    mission = _mission()
    projection = CapabilityBroker(_catalog()).project(
        mission, _profile(mission), state
    )
    assert projection.execution_route is ExecutionRoute.BLOCKED
    assert projection.tool_names == ("wait", "blocked")
    with pytest.raises(CapabilityContractError):
        BrokerRuntimeState(
            turn_index=1 << 80,
            available_capabilities=("visual.interact",),
        )


def test_nested_contract_subclasses_and_nonarrays_are_rejected() -> None:
    class SpoofedTool(ToolSpec):
        def to_dict(self):
            return {
                **super().to_dict(),
                "name": "spoofed_tool",
            }

    class SpoofedArtifact(ArtifactState):
        def to_dict(self):
            return {
                **super().to_dict(),
                "artifact_id": "spoofed-artifact",
            }

    with pytest.raises(CapabilityContractError, match="plain JSON"):
        ToolCatalog(
            (
                SpoofedTool(
                    "actual_tool",
                    _schema(),
                    ToolRoute.VISUAL_ACTOR,
                    MutationKind.NONE,
                ),
            )
        )
    with pytest.raises(CapabilityContractError, match="plain JSON"):
        _state(
            artifacts=(
                SpoofedArtifact(
                    "actual-artifact",
                    "r1",
                    "a" * 64,
                    "current_artifact",
                    True,
                ),
            )
        )

    state_raw = _state().to_dict()
    state_raw["artifacts"] = ""
    with pytest.raises(CapabilityContractError, match="array"):
        BrokerRuntimeState.from_dict(state_raw)

    projection = CapabilityBroker(_catalog()).project(
        _mission(), _profile(_mission()), _state()
    )
    projection_raw = projection.to_dict()
    projection_raw["active_grants"] = ""
    with pytest.raises(CapabilityContractError, match="array"):
        BrokerProjection.from_dict(projection_raw)


def test_each_artifact_binding_name_has_one_runtime_owner() -> None:
    artifacts = (
        ArtifactState(
            "artifact-1", "r1", "a" * 64, "current_artifact", True
        ),
        ArtifactState(
            "artifact-2", "r1", "b" * 64, "current_artifact", True
        ),
    )
    with pytest.raises(CapabilityContractError, match="binding names"):
        _state(artifacts=artifacts)


def test_grant_is_bound_to_scope_revision_expiry_and_content() -> None:
    grant = CapabilityGrant.issue(
        mission_id="m1",
        run_id="r1",
        capability="visual.draw",
        obligation_id="o1",
        mission_revision=1,
        issued_turn=3,
        expires_after_turns=2,
        policy_reason="accepted explicit capability request",
    )
    assert grant.valid_for(
        mission_id="m1",
        run_id="r1",
        obligation_id="o1",
        mission_revision=1,
        turn_index=3,
    )
    assert not grant.valid_for(
        mission_id="m1",
        run_id="r1",
        obligation_id="o1",
        mission_revision=1,
        turn_index=5,
    )
    assert not grant.valid_for(
        mission_id="m1",
        run_id="other",
        obligation_id="o1",
        mission_revision=1,
        turn_index=4,
    )
    raw = grant.to_dict()
    raw["policy_reason"] = "tampered"
    with pytest.raises(CapabilityContractError, match="grant_id"):
        CapabilityGrant.from_dict(raw)
    with pytest.raises(CapabilityContractError, match="non-negative"):
        CapabilityGrant.issue(
            mission_id="m1",
            run_id="r1",
            capability="visual.draw",
            obligation_id="o1",
            mission_revision=10**5000,
            issued_turn=3,
            expires_after_turns=2,
            policy_reason="bounded integer regression",
        )


def test_bound_fresh_visual_surface_is_small_and_capability_scoped() -> None:
    mission = _mission()
    projection = CapabilityBroker(_catalog()).project(
        mission, _profile(mission), _state()
    )
    assert projection.execution_route is ExecutionRoute.VISUAL_ACTOR
    assert projection.tool_names == (
        "observe",
        "click",
        "type_text",
        "hotkey",
        "scroll",
        "wait",
        "request_capability",
        "claim_obligation_done",
        "blocked",
    )
    assert "draw_strokes" not in projection.tool_names


@pytest.mark.parametrize(
    "frame",
    (
        AuthoritativeFrameState(
            9, 999, 3, True, True, "target_window"
        ),
        AuthoritativeFrameState(
            9, 101, 4, True, True, "target_window"
        ),
        AuthoritativeFrameState(
            9, 101, 3, False, True, "target_window"
        ),
        AuthoritativeFrameState(
            9, 101, 3, True, False, "target_window"
        ),
        AuthoritativeFrameState(9, 101, 3, True, True, "desktop"),
    ),
)
def test_stale_mismatched_or_wrong_scope_frame_removes_gui(frame) -> None:
    mission = _mission()
    catalog = _catalog()
    projection = CapabilityBroker(catalog).project(
        mission, _profile(mission), _state(frame=frame)
    )
    assert projection.frame_id is None
    assert projection.frame_scope is None
    assert all(
        catalog.get(name).mutation_kind is not MutationKind.GUI
        for name in projection.tool_names
    )


def test_mismatched_target_is_not_echoed_and_removes_gui() -> None:
    mission = _mission()
    catalog = _catalog()
    projection = CapabilityBroker(catalog).project(
        mission,
        _profile(mission),
        _state(target=TargetState("foreign-target", 101, 3, True)),
    )
    assert projection.target_binding_id is None
    assert projection.frame_id is None
    assert all(
        catalog.get(name).mutation_kind is not MutationKind.GUI
        for name in projection.tool_names
    )

    unbound_mission = _mission(bound=False)
    stale_runtime_target = CapabilityBroker(catalog).project(
        unbound_mission,
        _profile(unbound_mission),
        _state(),
    )
    assert stale_runtime_target.target_binding_id is None
    assert stale_runtime_target.frame_id is None
    assert all(
        catalog.get(name).mutation_kind is not MutationKind.GUI
        for name in stale_runtime_target.tool_names
    )


def test_full_desktop_frame_does_not_use_window_binding_as_gui_gate() -> None:
    mission = _mission()
    catalog = _catalog()
    state = _state(
        target=TargetState("foreground-anchor", 999, 4, True),
        frame=AuthoritativeFrameState(
            10, 999, 4, True, True, "full_desktop"
        ),
    )

    projection = CapabilityBroker(catalog).project(
        mission,
        _profile(mission),
        state,
    )

    assert projection.execution_route is ExecutionRoute.VISUAL_ACTOR
    assert "click" in projection.tool_names
    assert projection.frame_id == 10
    assert projection.frame_scope == "full_desktop"
    assert projection.target_binding_id == "target-1"


@pytest.mark.parametrize(
    "frame",
    (
        AuthoritativeFrameState(
            10, 999, 4, False, True, "full_desktop"
        ),
        AuthoritativeFrameState(
            10, 999, 4, True, False, "full_desktop"
        ),
    ),
)
def test_full_desktop_still_requires_authoritative_fresh_frame(frame) -> None:
    mission = _mission()
    catalog = _catalog()
    projection = CapabilityBroker(catalog).project(
        mission,
        _profile(mission),
        _state(
            target=TargetState("foreground-anchor", 999, 4, True),
            frame=frame,
        ),
    )

    assert projection.frame_id is None
    assert projection.frame_scope is None
    assert all(
        catalog.get(name).mutation_kind is not MutationKind.GUI
        for name in projection.tool_names
    )


def test_owned_held_pointer_keeps_visual_mouse_keyboard_tools_available() -> None:
    mission = _mission()
    projection = CapabilityBroker(_catalog_with_drag()).project(
        mission,
        _profile(mission),
        _state(
            pointer=PointerLeaseState(
                PointerDisposition.HELD,
                "pointer-lease-1",
                True,
            )
        ),
    )
    assert projection.execution_route is ExecutionRoute.VISUAL_ACTOR
    # A healthy held button is a normal human input state, not a capability
    # gate. The backend still reports impossible same-button transitions.
    assert "drag" in projection.tool_names
    assert "click" in projection.tool_names
    assert "scroll" in projection.tool_names
    assert "type_text" in projection.tool_names
    assert projection.pointer_lease_id == "pointer-lease-1"


@pytest.mark.parametrize(
    "pointer",
    (
        PointerLeaseState(
            PointerDisposition.HELD, "pointer-lease-1", False
        ),
        PointerLeaseState(PointerDisposition.UNKNOWN),
    ),
)
def test_unowned_or_unknown_pointer_is_blocked(pointer) -> None:
    mission = _mission()
    catalog = _catalog_with_drag()
    projection = CapabilityBroker(catalog).project(
        mission, _profile(mission), _state(pointer=pointer)
    )
    assert projection.execution_route is ExecutionRoute.BLOCKED
    assert projection.tool_names == ("wait", "blocked")
    assert all(
        catalog.get(name).mutation_kind is MutationKind.NONE
        for name in projection.tool_names
    )


@pytest.mark.parametrize(
    "status",
    (
        MissionStatus.WAITING_APPROVAL,
        MissionStatus.WAITING_EXTERNAL,
        MissionStatus.AWAITING_MISSION_VERIFICATION,
        MissionStatus.BLOCKED,
        MissionStatus.VERIFIED,
        MissionStatus.FAILED,
        MissionStatus.CANCELED,
    ),
)
def test_every_nonrunning_mission_status_is_nonmutating(status) -> None:
    mission = replace(_mission(), status=status)
    catalog = _catalog()
    projection = CapabilityBroker(catalog).project(
        mission, _profile(mission), _state()
    )
    assert projection.execution_route is ExecutionRoute.BLOCKED
    assert projection.tool_names == ("wait", "blocked")
    assert all(
        catalog.get(name).mutation_kind is MutationKind.NONE
        for name in projection.tool_names
    )


@pytest.mark.parametrize(
    "state",
    (
        _state(runtime_healthy=False),
        _state(paused=True),
        _state(canceled=True),
    ),
)
def test_unwritable_runtime_is_nonmutating(state) -> None:
    mission = _mission()
    catalog = _catalog()
    projection = CapabilityBroker(catalog).project(
        mission, _profile(mission), state
    )
    assert projection.execution_route is ExecutionRoute.BLOCKED
    assert projection.tool_names == ("wait", "blocked")
    assert all(
        catalog.get(name).mutation_kind is MutationKind.NONE
        for name in projection.tool_names
    )


def test_nonmutation_name_cannot_smuggle_gui_mutation_on_blocked_path() -> None:
    unsafe_named_blocked = _tool(
        "blocked",
        ToolRoute.VISUAL_ACTOR,
        MutationKind.GUI,
        capabilities=("visual.interact",),
        prerequisites=GUI_GATES,
        order=1,
    )
    broker = CapabilityBroker(
        ToolCatalog(
            (
                unsafe_named_blocked,
                _tool(
                    "wait",
                    ToolRoute.VISUAL_ACTOR,
                    MutationKind.NONE,
                    order=2,
                ),
            )
        )
    )
    mission = replace(_mission(), status=MissionStatus.CANCELED)
    projection = broker.project(mission, _profile(mission), _state())
    assert projection.tool_names == ("wait",)


def test_execution_profile_identity_and_acceptance_are_enforced() -> None:
    mission = _mission("communication.send_text", bound=False)
    mismatched = ExecutionPolicy().safe_default(
        "other-mission",
        mission.definition.goal_revision,
        available_capabilities=ALL_CAPABILITIES,
    )
    projection = CapabilityBroker(_catalog()).project(
        mission, mismatched, _state(target=TargetState(), frame=AuthoritativeFrameState())
    )
    assert projection.execution_route is ExecutionRoute.BLOCKED
    assert projection.tool_names == ()
    assert projection.projection_reasons == (
        "execution_profile_identity_mismatch",
    )

    not_accepted = ExecutionPolicy().safe_default(
        mission.mission_id,
        mission.definition.goal_revision,
        available_capabilities=ALL_CAPABILITIES,
    )
    projection = CapabilityBroker(_catalog()).project(
        mission,
        not_accepted,
        _state(target=TargetState(), frame=AuthoritativeFrameState()),
    )
    assert projection.execution_route is ExecutionRoute.BLOCKED
    assert projection.missing_capabilities == ("communication.send_text",)
    assert projection.projection_reasons == (
        "required_capability_not_accepted",
    )


def test_live_receipted_provider_route_is_mutually_exclusive_with_gui() -> None:
    mission = _mission("communication.send_text", bound=False)
    offer = _provider_offer()
    state = _state(
        target=TargetState(),
        frame=AuthoritativeFrameState(),
        executor_offers=(offer,),
        authorization_scopes=("external.send", "recipient.bound"),
    )
    projection = CapabilityBroker(_catalog()).project(
        mission, _profile(mission), state
    )
    assert projection.execution_route is ExecutionRoute.PROVIDER
    assert projection.tool_names == ("send_text",)
    assert projection.executor_id == "provider-1"
    assert projection.target_binding_id is None
    assert projection.frame_id is None


@pytest.mark.parametrize(
    "runtime_changes",
    (
        {"paused": True},
        {"canceled": True},
        {"runtime_healthy": False},
    ),
)
def test_provider_offer_cannot_bypass_runtime_write_gate(
    runtime_changes,
) -> None:
    mission = _mission("communication.send_text", bound=False)
    state = _state(
        target=TargetState(),
        frame=AuthoritativeFrameState(),
        executor_offers=(_provider_offer(),),
        authorization_scopes=("external.send", "recipient.bound"),
        **runtime_changes,
    )
    projection = CapabilityBroker(_catalog()).project(
        mission, _profile(mission), state
    )
    assert projection.execution_route is ExecutionRoute.BLOCKED
    assert projection.tool_names == ("wait", "blocked")

    waiting = replace(mission, status=MissionStatus.WAITING_EXTERNAL)
    waiting_projection = CapabilityBroker(_catalog()).project(
        waiting, _profile(waiting), replace(state, paused=False, canceled=False)
    )
    assert waiting_projection.execution_route is ExecutionRoute.BLOCKED
    assert waiting_projection.tool_names == ("wait", "blocked")


def test_provider_without_receipt_or_authorization_is_not_selected() -> None:
    mission = _mission("communication.send_text", bound=False)
    offer = _provider_offer(receipts=())
    projection = CapabilityBroker(_catalog()).project(
        mission,
        _profile(mission),
        _state(
            target=TargetState(),
            frame=AuthoritativeFrameState(),
            executor_offers=(offer,),
            authorization_scopes=("external.send", "recipient.bound"),
        ),
    )
    assert projection.execution_route is ExecutionRoute.BLOCKED
    assert "send_text" not in projection.tool_names

    fully_declared_offer = _provider_offer()
    projection = CapabilityBroker(_catalog()).project(
        mission,
        _profile(mission),
        _state(
            target=TargetState(),
            frame=AuthoritativeFrameState(),
            executor_offers=(fully_declared_offer,),
            authorization_scopes=("recipient.bound",),
        ),
    )
    assert projection.execution_route is ExecutionRoute.BLOCKED
    assert "send_text" not in projection.tool_names

    offer_without_scope = ExecutorOffer(
        executor_id="provider-no-scope",
        route=ToolRoute.PROVIDER,
        supported_capabilities=("communication.send_text",),
        tool_names=("send_text",),
        available=True,
        live=True,
        authorization_scopes=(),
        receipt_kinds=("delivery.receipt",),
    )
    projection = CapabilityBroker(_catalog()).project(
        mission,
        _profile(mission),
        _state(
            target=TargetState(),
            frame=AuthoritativeFrameState(),
            executor_offers=(offer_without_scope,),
            authorization_scopes=("external.send", "recipient.bound"),
        ),
    )
    assert projection.execution_route is ExecutionRoute.BLOCKED
    assert "send_text" not in projection.tool_names


def test_provider_offer_cannot_expose_unrelated_capability_tool() -> None:
    mission = _mission("communication.send_text", bound=False)
    offer = ExecutorOffer(
        executor_id="provider-1",
        route=ToolRoute.PROVIDER,
        supported_capabilities=(
            "communication.send_text",
            "communication.send_artifact",
        ),
        tool_names=("send_artifact",),
        available=True,
        live=True,
        authorization_scopes=("external.send",),
        receipt_kinds=("delivery.receipt",),
    )
    projection = CapabilityBroker(_catalog()).project(
        mission,
        _profile(mission),
        _state(
            target=TargetState(),
            frame=AuthoritativeFrameState(),
            executor_offers=(offer,),
            authorization_scopes=("external.send", "recipient.bound"),
        ),
    )
    assert projection.execution_route is ExecutionRoute.BLOCKED
    assert "send_artifact" not in projection.tool_names


def test_executor_offer_order_is_canonical_and_identity_is_unique() -> None:
    mission = _mission("communication.send_text", bound=False)
    offers = (
        _provider_offer(executor_id="provider-z"),
        _provider_offer(executor_id="provider-a"),
    )
    common = {
        "target": TargetState(),
        "frame": AuthoritativeFrameState(),
        "authorization_scopes": ("external.send", "recipient.bound"),
    }
    broker = CapabilityBroker(_catalog())
    first = broker.project(
        mission,
        _profile(mission),
        _state(executor_offers=offers, **common),
    )
    second = broker.project(
        mission,
        _profile(mission),
        _state(executor_offers=tuple(reversed(offers)), **common),
    )
    assert first.to_dict() == second.to_dict()
    assert first.executor_id == "provider-a"

    duplicates = (
        _provider_offer(executor_id="duplicate-provider"),
        _provider_offer(executor_id="duplicate-provider"),
    )
    with pytest.raises(CapabilityContractError, match="unique"):
        _state(executor_offers=duplicates)


def test_artifact_id_hash_run_and_sendability_must_match_binding() -> None:
    mission = _mission_with_artifact_binding()
    offer = _provider_offer(
        capability="communication.send_artifact",
        tool_name="send_artifact",
    )
    common = {
        "target": TargetState(),
        "frame": AuthoritativeFrameState(),
        "executor_offers": (offer,),
        "authorization_scopes": ("external.send", "recipient.bound"),
    }
    broker = CapabilityBroker(_catalog())
    invalid = (
        ArtifactState(
            "wrong-id", "r1", "a" * 64, "current_artifact", True
        ),
        ArtifactState(
            "artifact-1", "r1", "b" * 64, "current_artifact", True
        ),
        ArtifactState(
            "artifact-1",
            "wrong-run",
            "a" * 64,
            "current_artifact",
            True,
        ),
        ArtifactState(
            "artifact-1", "r1", "a" * 64, "current_artifact", False
        ),
    )
    for artifact in invalid:
        projection = broker.project(
            mission,
            _profile(mission),
            _state(artifacts=(artifact,), **common),
        )
        assert projection.execution_route is not ExecutionRoute.PROVIDER

    valid = ArtifactState(
        "artifact-1", "r1", "a" * 64, "current_artifact", True
    )
    projection = broker.project(
        mission,
        _profile(mission),
        _state(artifacts=(valid,), **common),
    )
    assert projection.execution_route is ExecutionRoute.PROVIDER


def test_artifact_offer_is_scoped_to_active_obligation_inputs() -> None:
    mission = _mission_with_artifact_binding()
    foreign_ref = ArtifactRef(
        artifact_id="artifact-2",
        media_type="image/png",
        sha256="b" * 64,
        producer_obligation_id="capture",
        evidence_ref="foreign-artifact-proof",
    )
    mission = replace(
        mission,
        artifact_bindings={
            **mission.artifact_bindings,
            "other_artifact": foreign_ref,
        },
    )
    offer = _provider_offer(
        capability="communication.send_artifact",
        tool_name="send_artifact",
    )
    state = _state(
        target=TargetState(),
        frame=AuthoritativeFrameState(),
        executor_offers=(offer,),
        authorization_scopes=("external.send", "recipient.bound"),
        artifacts=(
            ArtifactState(
                "artifact-2",
                "r1",
                "b" * 64,
                "other_artifact",
                True,
            ),
        ),
    )
    projection = CapabilityBroker(_catalog()).project(
        mission, _profile(mission), state
    )
    assert projection.execution_route is not ExecutionRoute.PROVIDER
    assert "send_artifact" not in projection.tool_names


def test_projection_is_deterministic_under_input_order_randomization() -> None:
    mission = _mission()
    catalog = _catalog()
    profile = _profile(mission)
    baseline = CapabilityBroker(catalog).project(
        mission, profile, _state()
    )
    values = list(ALL_CAPABILITIES)
    random.Random(9).shuffle(values)
    replayed = CapabilityBroker(
        ToolCatalog(tuple(reversed(catalog.tools)))
    ).project(
        mission,
        profile,
        _state(available_capabilities=tuple(values)),
    )
    assert replayed.to_dict() == baseline.to_dict()


def test_removing_runtime_capability_never_adds_tools() -> None:
    mission = _mission()
    broker = CapabilityBroker(_catalog())
    full = broker.project(mission, _profile(mission), _state())
    reduced = broker.project(
        mission,
        _profile(mission),
        _state(
            available_capabilities=tuple(
                item
                for item in ALL_CAPABILITIES
                if item != "visual.interact"
            )
        ),
    )
    assert set(reduced.tool_names) <= set(full.tool_names)
    assert reduced.execution_route is ExecutionRoute.BLOCKED


def test_unrelated_profile_capability_does_not_expand_surface() -> None:
    mission = _mission("visual.read_text")
    profile = _profile(
        mission, ("visual.read_text", "visual.interact", "visual.draw")
    )
    projection = CapabilityBroker(_catalog()).project(
        mission, profile, _state()
    )
    assert "read_text" in projection.tool_names
    assert "click" not in projection.tool_names
    assert "draw_strokes" not in projection.tool_names


def test_self_minted_grant_cannot_expand_the_tool_surface() -> None:
    mission = _mission()
    broker = CapabilityBroker(_catalog())
    profile = _profile(
        mission, ("visual.interact", "visual.draw")
    )
    baseline = broker.project(mission, profile, _state())
    assert "draw_strokes" not in baseline.tool_names

    valid = CapabilityGrant.issue(
        mission_id=mission.mission_id,
        run_id=mission.run_id,
        capability="visual.draw",
        obligation_id=mission.active_obligation_id,
        mission_revision=mission.revision,
        issued_turn=4,
        expires_after_turns=2,
        policy_reason="explicit one-obligation grant",
    )
    not_expanded = broker.project(
        mission, profile, _state(), grants=(valid,)
    )
    assert "draw_strokes" not in not_expanded.tool_names
    assert not not_expanded.active_grants

    expired = CapabilityGrant.issue(
        mission_id=mission.mission_id,
        run_id=mission.run_id,
        capability="visual.draw",
        obligation_id=mission.active_obligation_id,
        mission_revision=mission.revision,
        issued_turn=1,
        expires_after_turns=1,
        policy_reason="expired grant",
    )
    expired_result = broker.project(
        mission, profile, _state(), grants=(expired,)
    )
    assert "draw_strokes" not in expired_result.tool_names
    assert not expired_result.active_grants
    with pytest.raises(TypeError, match="CapabilityGrant"):
        broker.project(mission, profile, _state(), grants=({"grant": "fake"},))


def test_projection_rejects_expired_grants_even_with_matching_content_id() -> None:
    mission = _mission()
    profile = _profile(mission)
    state = _state()
    baseline = CapabilityBroker(_catalog()).project(mission, profile, state)
    expired = CapabilityGrant.issue(
        mission_id=mission.mission_id,
        run_id=mission.run_id,
        capability="visual.interact",
        obligation_id=mission.active_obligation_id,
        mission_revision=mission.revision,
        issued_turn=1,
        expires_after_turns=1,
        policy_reason="expired test grant",
    )
    projection_id = _broker_projection_id(
        mission_id=baseline.mission_id,
        run_id=baseline.run_id,
        catalog_fingerprint=baseline.catalog_fingerprint,
        profile_id=baseline.profile_id,
        state_fingerprint=baseline.state_fingerprint,
        mission_revision=baseline.mission_revision,
        obligation_id=baseline.obligation_id,
        turn_index=baseline.turn_index,
        execution_route=baseline.execution_route,
        executor_id=baseline.executor_id,
        target_binding_id=baseline.target_binding_id,
        frame_id=baseline.frame_id,
        frame_scope=baseline.frame_scope,
        pointer_lease_id=baseline.pointer_lease_id,
        tool_names=baseline.tool_names,
        active_grants=(expired,),
        missing_capabilities=baseline.missing_capabilities,
        projection_reasons=baseline.projection_reasons,
    )
    with pytest.raises(CapabilityContractError, match="lifetime"):
        replace(
            baseline,
            projection_id=projection_id,
            active_grants=(expired,),
        )


def test_unknown_required_capability_fails_closed() -> None:
    mission = _mission("future.unknown")
    projection = CapabilityBroker(_catalog()).project(
        mission,
        ExecutionPolicy().safe_default(
            mission.mission_id,
            mission.definition.goal_revision,
            available_capabilities=ALL_CAPABILITIES,
        ),
        _state(),
    )
    assert projection.execution_route is ExecutionRoute.BLOCKED
    assert projection.tool_names == ()
    assert projection.missing_capabilities == ()
    assert projection.projection_reasons == (
        "unknown_required_capability",
    )


def test_projection_identity_binds_catalog_profile_state_frame_and_content() -> None:
    mission = _mission()
    catalog = _catalog()
    broker = CapabilityBroker(catalog)
    baseline = broker.project(mission, _profile(mission), _state())
    assert BrokerProjection.from_dict(baseline.to_dict()) == baseline

    catalog_with_unused_tool = ToolCatalog(
        (
            *catalog.tools,
            _tool(
                "unused_read",
                ToolRoute.VISUAL_ACTOR,
                MutationKind.NONE,
                capabilities=("visual.draw",),
                order=99,
            ),
        )
    )
    changed_catalog = CapabilityBroker(catalog_with_unused_tool).project(
        mission, _profile(mission), _state()
    )
    changed_unused_state = broker.project(
        mission,
        _profile(mission),
        _state(authorization_scopes=("unused.scope",)),
    )
    changed_profile = broker.project(
        mission,
        _profile(mission, ("visual.interact", "visual.draw")),
        _state(),
    )
    changed_frame = broker.project(
        mission,
        _profile(mission),
        _state(
            frame=AuthoritativeFrameState(
                10, 101, 3, True, True, "target_window"
            )
        ),
    )
    changed_turn = broker.project(
        mission,
        _profile(mission),
        _state(turn_index=5),
    )
    assert changed_catalog.tool_names == baseline.tool_names
    assert changed_unused_state.tool_names == baseline.tool_names
    assert changed_catalog.catalog_fingerprint != baseline.catalog_fingerprint
    assert changed_unused_state.state_fingerprint != baseline.state_fingerprint
    assert len(
        {
            baseline.projection_id,
            changed_catalog.projection_id,
            changed_unused_state.projection_id,
            changed_profile.projection_id,
            changed_frame.projection_id,
            changed_turn.projection_id,
        }
    ) == 6

    raw = baseline.to_dict()
    raw["tool_names"] = [*raw["tool_names"], "read_text"]
    with pytest.raises(CapabilityContractError, match="projection_id"):
        BrokerProjection.from_dict(raw)


def test_projection_route_and_identity_structure_is_coherent() -> None:
    baseline = CapabilityBroker(_catalog()).project(
        _mission(), _profile(_mission()), _state()
    )
    with pytest.raises(CapabilityContractError, match="executor route"):
        replace(
            baseline,
            execution_route=ExecutionRoute.PROVIDER,
            executor_id=None,
        )
    with pytest.raises(CapabilityContractError, match="executor route"):
        replace(
            baseline,
            execution_route=ExecutionRoute.BLOCKED,
            executor_id="provider-1",
        )
    with pytest.raises(CapabilityContractError, match="target binding"):
        replace(baseline, target_binding_id=None)
    with pytest.raises(CapabilityContractError, match="at least one tool"):
        replace(baseline, tool_names=())
    for tool_name in ("run_action_batch", "bad name"):
        with pytest.raises(CapabilityContractError, match="invalid or forbidden"):
            replace(baseline, tool_names=(tool_name,))


def test_broker_is_frozen_and_package_is_pure_default_unreachable() -> None:
    broker = CapabilityBroker(_catalog())
    with pytest.raises(FrozenInstanceError):
        broker.catalog = ToolCatalog((_catalog().tools[0],))

    home = Path(__file__).resolve().parents[1]
    package = home / "winpilot" / "vnext" / "capabilities"
    files = tuple(sorted(path.name for path in package.glob("*.py")))
    assert files == (
        "__init__.py",
        "argument_validation.py",
        "broker.py",
        "contracts.py",
        "state.py",
    )
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in package.glob("*.py")
    )
    assert "resolve_target" not in source
    for forbidden in (
        "openai",
        "anthropic",
        "electron",
        "win32",
        "target_acquisition",
        "fastguicontroller",
        "rawdevice",
    ):
        assert forbidden not in source.lower()

    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    alias.name in {"hashlib", "json", "math", "re"}
                    or alias.name.startswith("typing")
                    for alias in node.names
                )
            if isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    assert node.module in {
                        "__future__",
                        "dataclasses",
                        "enum",
                        "jsonschema",
                        "jsonschema.exceptions",
                        "referencing",
                        "referencing.exceptions",
                        "types",
                        "typing",
                    }
                else:
                    root = (node.module or "").split(".", 1)[0]
                    assert root in {
                        "argument_validation",
                        "broker",
                        "capability_names",
                        "contracts",
                        "json_contract",
                        "mission",
                        "profiling",
                        "state",
                    }

    product_root = home / "winpilot"
    vnext_root = product_root / "vnext"
    live_composition_root = (
        product_root / "orchestrator" / "live_agent.py"
    )

    capability_importers = {
        path
        for path in product_root.rglob("*.py")
        if vnext_root not in path.parents
        and analyze_path(
            path,
            module_name=".".join(
                path.relative_to(home).with_suffix("").parts
            ),
        ).static_bindings("winpilot.vnext.capabilities")
    }
    assert capability_importers == {live_composition_root}
