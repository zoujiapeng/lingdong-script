"""Identity-free Actor proposal and Runtime-owned envelope tests."""
from __future__ import annotations

import ast
from dataclasses import replace
import json
import math
from pathlib import Path
import random
from types import MappingProxyType

import pytest
from pydantic import BaseModel, ValidationError

from winpilot.agent.frame_authority import FrameAuthority
from winpilot.vnext.actor import (
    ACTOR_ENVELOPE_FORMAT,
    ACTOR_PROPOSAL_FORMAT,
    ActorDecisionEnvelope,
    BlockedReport,
    CapabilityRequest,
    ClaimObligationDone,
    DesktopActionProposal,
    ImageRegion,
    InspectionRequest,
    InvalidActorAdmission,
    InvalidActorEnvelope,
    InvalidActorProposal,
    ObserveProposal,
    ReplanRequest,
    actor_proposal_to_json,
    bind_actor_decision,
    parse_actor_proposal,
    proposal_tool_name,
)
from winpilot.vnext.capabilities import (
    AuthoritativeFrameState,
    BrokerProjection,
    BrokerRuntimeState,
    CapabilityBroker,
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
from winpilot.vnext.mission import (
    EvidenceRef,
    MissionDefinition,
    MissionReducer,
    ObligationSpec,
    TargetBinding,
)
from winpilot.vnext.profiling import (
    DEFAULT_CAPABILITY_CATALOG,
    ExecutionPolicy,
    TaskDimensions,
    TaskProfileProposal,
)


ALL_CAPABILITIES = tuple(sorted(DEFAULT_CAPABILITY_CATALOG))
GUI_GATES = (
    "target_bound",
    "authoritative_frame",
    "pointer_released",
    "runtime_healthy",
)


def _module_name(path: Path, package_root: Path) -> str:
    relative = path.relative_to(package_root.parent).with_suffix("")
    return ".".join(relative.parts)


def _imports(path: Path, package_root: Path) -> tuple[str, ...]:
    tree = ast.parse(
        path.read_text(encoding="utf-8"),
        filename=str(path),
    )
    package = _module_name(path, package_root).rpartition(".")[0]
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                keep = max(0, len(parts) - node.level + 1)
                prefix = ".".join(parts[:keep])
                imported = ".".join(
                    part
                    for part in (prefix, node.module or "")
                    if part
                )
            else:
                imported = node.module or ""
            found.append(imported)
            found.extend(
                f"{imported}.{alias.name}"
                for alias in node.names
                if imported and alias.name != "*"
            )
    return tuple(found)


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }


def _point_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "image_x": {"type": "number", "minimum": 0},
            "image_y": {"type": "number", "minimum": 0},
        },
        "required": ["image_x", "image_y"],
    }


def _tool(
    name: str,
    mutation: MutationKind,
    *,
    route: ToolRoute = ToolRoute.VISUAL_ACTOR,
    capabilities: tuple[str, ...] = (),
    prerequisites: tuple[str, ...] = (),
    authorization_scope: str | None = None,
    schema: dict | None = None,
    order: int,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        schema=schema or _schema(),
        route=route,
        mutation_kind=mutation,
        required_any_capabilities=capabilities,
        prerequisites=prerequisites,
        authorization_scope=authorization_scope,
        order=order,
    )


def _catalog() -> ToolCatalog:
    tools = []
    for order, name in enumerate(
        ("click", "double_click", "scroll", "type_text", "hotkey"),
        start=1,
    ):
        tools.append(
            _tool(
                name,
                MutationKind.GUI,
                capabilities=("visual.interact",),
                prerequisites=GUI_GATES,
                schema=(
                    _point_schema()
                    if name in {"click", "double_click"}
                    else _schema()
                ),
                order=order,
            )
        )
    for order, name in enumerate(
        (
            "observe",
            "request_capability",
            "claim_obligation_done",
            "blocked",
        ),
        start=20,
    ):
        tools.append(_tool(name, MutationKind.NONE, order=order))
    return ToolCatalog(tuple(tools))


def _mission_reducer(
    capability: str = "visual.interact",
    *,
    bound: bool = True,
) -> MissionReducer:
    definition = MissionDefinition(
        mission_id="mission-actor",
        run_id="run-actor",
        original_goal="Complete the visible task",
        obligations=(
            ObligationSpec(
                id="obligation-current",
                desired_state="the requested visible state exists",
                acceptance_criteria=("the visible result is confirmed",),
                required_capabilities=(capability,),
            ),
        ),
    )
    evidence = ()
    if bound:
        evidence = (
            EvidenceRef(
                id="e-target",
                mission_id=definition.mission_id,
                run_id=definition.run_id,
                kind="target",
                trust="deterministic",
                obligation_id="obligation-current",
                payload_hash="target-evidence-payload",
                frame_id=82,
            ),
        )
    reducer = MissionReducer(definition, evidence=evidence)
    if bound:
        reducer.apply(
            reducer.event(
                "target_bound",
                payload=TargetBinding(
                    binding_id="binding-current",
                    hwnd=9123,
                    process_id=44,
                    executable_identity="opaque-process",
                    title_snapshot="",
                    established_by_evidence_ref="e-target",
                    epoch=7,
                ).to_dict(),
                evidence_refs=("e-target",),
            )
        )
    return reducer


def _mission(
    capability: str = "visual.interact",
    *,
    bound: bool = True,
):
    return _mission_reducer(
        capability,
        bound=bound,
    ).projection


def _profile(mission):
    required = mission.obligation_definition(
        mission.active_obligation_id
    ).required_capabilities
    proposal = TaskProfileProposal(
        dimensions=TaskDimensions(),
        broad_capability_hypotheses=required,
        confidence=1.0,
        rationale=("deterministic actor test fixture",),
    )
    return ExecutionPolicy(min_confidence=0.0).evaluate(
        proposal,
        mission_id=mission.mission_id,
        goal_revision=mission.definition.goal_revision,
        available_capabilities=ALL_CAPABILITIES,
    )


def _state(**overrides) -> BrokerRuntimeState:
    values = {
        "turn_index": 5,
        "available_capabilities": ALL_CAPABILITIES,
        "target": TargetState("binding-current", 9123, 7, True),
        "frame": AuthoritativeFrameState(
            82, 9123, 7, True, True, "target_window"
        ),
        "pointer": PointerLeaseState(PointerDisposition.RELEASED),
        "runtime_healthy": True,
    }
    values.update(overrides)
    return BrokerRuntimeState(**values)


def _runtime():
    reducer = _mission_reducer()
    mission = reducer.projection
    profile = _profile(mission)
    state = _state()
    catalog = _catalog()
    projection = CapabilityBroker(catalog).project(
        mission, profile, state
    )
    assert projection.execution_route is ExecutionRoute.VISUAL_ACTOR
    return reducer, mission, profile, state, catalog, projection


def _action(**overrides) -> DesktopActionProposal:
    values = {
        "format": ACTOR_PROPOSAL_FORMAT,
        "kind": "desktop_action",
        "action": "click",
        "args": {"image_x": 10, "image_y": 20},
        "local_intent": "activate the visible target",
    }
    values.update(overrides)
    return parse_actor_proposal(values)


def _bind(**overrides) -> ActorDecisionEnvelope:
    reducer, mission, profile, state, catalog, projection = _runtime()
    values = {
        "mission_reducer": reducer,
        "profile": profile,
        "state": state,
        "catalog": catalog,
        "projection": projection,
        "proposal": _action(),
        "frame_region": (-1200, 0, 1200, 800),
        "submitted_image_size": (900, 600),
        "frame_image_sha256": "b" * 64,
        "allowed_evidence_scope": ("e-target",),
    }
    values.update(overrides)
    return bind_actor_decision(**values)


def _all_proposals():
    return (
        _action(),
        ObserveProposal(
            format=ACTOR_PROPOSAL_FORMAT,
            observation_kind="full",
            image_region=None,
            question="What changed?",
        ),
        ObserveProposal(
            format=ACTOR_PROPOSAL_FORMAT,
            observation_kind="image_region",
            image_region=ImageRegion(x=1, y=2, width=30, height=40),
            question="Describe the small control",
        ),
        CapabilityRequest(
            format=ACTOR_PROPOSAL_FORMAT,
            capability="pointer.dynamic",
            reason="a future non-canary path may require another capability",
        ),
        InspectionRequest(
            format=ACTOR_PROPOSAL_FORMAT,
            region=ImageRegion(x=2, y=3, width=4, height=5),
            question="Describe visible topology only",
        ),
        ClaimObligationDone(
            format=ACTOR_PROPOSAL_FORMAT,
            concise_claim="The requested state now appears visible",
        ),
        BlockedReport(
            format=ACTOR_PROPOSAL_FORMAT,
            blocker_kind="visual_uncertainty",
            observed_facts=(
                "Two visible candidates remain indistinguishable",
            ),
        ),
        ReplanRequest(
            format=ACTOR_PROPOSAL_FORMAT,
            reason_kind="strategy_conflict",
            observed_conflicts=(
                "The observed state conflicts with the obligation",
            ),
            failed_strategy_summaries=(
                "The first distinct route made no progress",
            ),
        ),
    )


def test_all_proposal_variants_are_strict_identity_free_round_trips() -> None:
    expected_tools = (
        "click",
        "observe",
        "observe",
        "request_capability",
        "request_inspection",
        "claim_obligation_done",
        "blocked",
        "request_replan",
    )
    for proposal, tool_name in zip(_all_proposals(), expected_tools):
        raw = proposal.model_dump(mode="json")
        replayed = parse_actor_proposal(raw)
        assert replayed == proposal
        assert json.loads(actor_proposal_to_json(replayed)) == raw
        assert proposal_tool_name(proposal) == tool_name
        assert set(raw).isdisjoint({
            "mission_id",
            "run_id",
            "mission_revision",
            "obligation_id",
            "frame_id",
            "evidence_refs",
            "broker_projection_id",
            "grant_ids",
        })


def test_model_wire_requires_plain_object_version_and_one_outcome() -> None:
    with pytest.raises(InvalidActorProposal, match="plain object"):
        parse_actor_proposal({
            "kind": "blocked",
            "blocker_kind": "unknown",
            "observed_facts": [],
        })
    with pytest.raises(InvalidActorProposal):
        parse_actor_proposal({
            **_action().model_dump(mode="json"),
            "concise_claim": "also claim completion",
        })
    with pytest.raises(InvalidActorProposal, match="plain object"):
        parse_actor_proposal(type("MappingLike", (dict,), {})(
            _action().model_dump(mode="json")
        ))
    for kind in ("done", "fail", "action_batch", "desktop_actions"):
        with pytest.raises(
            InvalidActorProposal,
            match="unsupported actor proposal kind",
        ):
            parse_actor_proposal({
                "format": ACTOR_PROPOSAL_FORMAT,
                "kind": kind,
            })


@pytest.mark.parametrize(
    "field",
    (
        "mission_id",
        "missionId",
        "mission-id",
        "FRAME_ID",
        "catalogFingerprint",
        "profile-id",
        "state_fingerprint",
        "turnIndex",
        "frameScope",
        "executor_id",
        "eventId",
        "receipt-id",
        "payload_sha256",
        "evidence_refs",
        "target_binding_id",
        "pointerLeaseId",
        "artifact_id",
        "hwnd",
    ),
)
def test_runtime_identity_is_rejected_at_any_argument_depth(
    field: str,
) -> None:
    with pytest.raises(InvalidActorProposal, match="Runtime-owned identity"):
        parse_actor_proposal({
            **_action().model_dump(mode="json"),
            "args": {"outer": [{"inner": {field: "invented"}}]},
        })


@pytest.mark.parametrize(
    "field",
    (
        "physical_x",
        "screenY",
        "desktop-x",
        "frame_x1",
        "absoluteCoordinates",
        "client_x",
        "windowY",
        "native-x",
        "global_point",
        "monitorCoordinates",
        "cursor_x",
    ),
)
def test_non_image_coordinate_aliases_are_rejected(field: str) -> None:
    with pytest.raises(
        InvalidActorProposal,
        match="submitted-image coordinate",
    ):
        _action(args={field: 9})
    with pytest.raises(
        InvalidActorProposal,
        match="must refer to the submitted image",
    ):
        _action(args={"coordinateSpace": "client"})


def test_action_batches_and_completion_mutations_are_rejected() -> None:
    for field in (
        "actions",
        "tool_calls",
        "actionBatch",
        "operations",
    ):
        with pytest.raises(
            InvalidActorProposal,
            match="multiple action outcomes",
        ):
            _action(args={field: [{"action": "click"}]})
    for action in (
        "done",
        "fail",
        "claim_obligation_done",
        "run_action_batch",
        "finish_task",
    ):
        with pytest.raises(
            InvalidActorProposal,
            match="not one GUI proposal",
        ):
            _action(action=action)


def test_action_args_are_bounded_finite_json_and_deeply_immutable() -> None:
    source = {"nested": [{"image_x": 1}], "enabled": True}
    proposal = _action(args=source)
    source["nested"][0]["image_x"] = 999
    assert proposal.args["nested"][0]["image_x"] == 1
    with pytest.raises(TypeError):
        proposal.args["new"] = 1
    with pytest.raises(TypeError):
        proposal.args["nested"][0]["image_x"] = 2

    for bad in (math.nan, math.inf, 1e308, object(), 1 << 80):
        with pytest.raises(InvalidActorProposal):
            _action(args={"value": bad})
    with pytest.raises(InvalidActorProposal):
        _action(args={1: "non-string key"})
    with pytest.raises(InvalidActorProposal):
        _action(args={"text": "x" * 1_048_577})
    with pytest.raises(InvalidActorProposal, match="node limit"):
        _action(args={"values": [0] * 4097})

    cyclic: dict = {}
    cyclic["self"] = cyclic
    with pytest.raises(InvalidActorProposal, match="cyclic"):
        _action(args=cyclic)

    deep = leaf = {}
    for index in range(1200):
        nested = {"level": index}
        leaf["next"] = nested
        leaf = nested
    with pytest.raises(InvalidActorProposal, match="depth"):
        _action(args=deep)

    class ExecutableMapping(dict):
        def items(self):
            raise AssertionError("mapping subclass was executed")

    hostile = ExecutableMapping({"image_x": 1, "image_y": 2})
    for value in (hostile, MappingProxyType(hostile)):
        with pytest.raises(InvalidActorProposal, match="non-JSON"):
            _action(args=value)


def test_semantic_text_and_arrays_have_explicit_resource_bounds() -> None:
    with pytest.raises(ValidationError, match="text length"):
        DesktopActionProposal(
            action="click",
            args={},
            local_intent="x" * 16_385,
        )
    with pytest.raises(ValidationError, match="array item"):
        BlockedReport(
            blocker_kind="unknown",
            observed_facts=tuple(
                f"fact-{index}" for index in range(4097)
            ),
        )


def test_image_regions_only_validate_bounded_image_local_shape() -> None:
    region = ImageRegion(x=50_000, y=40_000, width=20, height=10)
    assert region.x == 50_000
    for bad in (-1, math.nan, math.inf, True, 1 << 80, 10**5000):
        with pytest.raises(ValidationError):
            ImageRegion(x=bad, y=0, width=1, height=1)


def test_proposal_serialization_is_canonical_for_order_and_signed_zero() -> None:
    rng = random.Random(41)
    items = [(f"key_{index}", index) for index in range(30)]
    baseline = None
    for _ in range(20):
        rng.shuffle(items)
        serialized = actor_proposal_to_json(
            _action(args={**dict(items), "zero": -0.0})
        )
        baseline = serialized if baseline is None else baseline
        assert serialized == baseline
        assert json.loads(serialized)["args"]["zero"] == 0.0


def test_runtime_binder_derives_all_authority_and_round_trips() -> None:
    envelope = _bind()
    raw = envelope.to_dict()
    assert raw["format"] == ACTOR_ENVELOPE_FORMAT
    assert raw["mission_id"] == "mission-actor"
    assert raw["run_id"] == "run-actor"
    assert len(raw["mission_projection_sha256"]) == 64
    assert raw["accepted_mission_event_ids"]
    assert raw["obligation_id"] == "obligation-current"
    assert raw["turn_index"] == 5
    assert raw["frame_id"] == 82
    assert raw["frame_scope"] == "target_window"
    assert raw["target_binding_id"] == "binding-current"
    assert raw["pointer_disposition"] == "released"
    assert raw["pointer_lease_id"] is None
    assert raw["capability_grant_ids"] == []
    assert raw["allowed_evidence_scope"] == ["e-target"]
    assert ActorDecisionEnvelope.from_dict(raw) == envelope
    assert json.loads(envelope.to_json()) == raw
    assert envelope.epoch == raw["frame_epoch"]
    assert envelope.hwnd == raw["frame_hwnd"]
    assert envelope.region == tuple(raw["frame_region"])
    assert envelope.submitted_size == tuple(raw["submitted_image_size"])
    assert envelope.image_sha256 == raw["frame_image_sha256"]
    assert set(raw["proposal"]).isdisjoint({
        "mission_id",
        "frame_id",
        "broker_projection_id",
    })


def test_envelope_is_content_addressed_and_tamper_evident() -> None:
    envelope = _bind()
    raw = envelope.to_dict()
    changes = (
        {"turn_index": raw["turn_index"] + 1},
        {"mission_projection_sha256": "f" * 64},
        {"accepted_mission_event_ids": []},
        {"catalog_fingerprint": "a" * 64},
        {"profile_id": "profile-other"},
        {"state_fingerprint": "c" * 64},
        {"frame_id": raw["frame_id"] + 1},
        {"frame_image_sha256": "d" * 64},
        {"target_binding_id": "binding-other"},
        {"broker_projection_id": "projection-other"},
        {"allowed_evidence_scope": []},
        {
            "proposal": {
                **raw["proposal"],
                "action": "double_click",
            }
        },
    )
    for change in changes:
        with pytest.raises(
            InvalidActorEnvelope,
            match="decision_id",
        ):
            ActorDecisionEnvelope.from_dict({**raw, **change})
    changed_frame = _bind(frame_image_sha256="e" * 64)
    changed_action = _bind(proposal=_action(action="double_click"))
    assert len({
        envelope.decision_id,
        changed_frame.decision_id,
        changed_action.decision_id,
    }) == 3


def test_envelope_wire_is_strict_bounded_and_frozen() -> None:
    envelope = _bind()
    raw = envelope.to_dict()
    with pytest.raises(InvalidActorEnvelope, match="explicit format"):
        ActorDecisionEnvelope.from_dict({
            key: value
            for key, value in raw.items()
            if key != "format"
        })
    for change in (
        {"frame_id": 0},
        {"frame_scope": "desktop"},
        {"frame_region": [0, 0, 0, 1]},
        {"submitted_image_size": [0, 1]},
        {"frame_image_sha256": "A" * 64},
        {"mission_revision": True},
        {"unexpected": "authority"},
    ):
        with pytest.raises(InvalidActorEnvelope):
            ActorDecisionEnvelope.from_dict({**raw, **change})
    with pytest.raises(InvalidActorEnvelope, match="array item"):
        ActorDecisionEnvelope.from_dict({
            **raw,
            "allowed_evidence_scope": [
                f"evidence-{index}" for index in range(4097)
            ],
        })
    with pytest.raises(ValidationError):
        envelope.turn_index = 9


def test_constructed_envelope_cannot_serialize_without_revalidation() -> None:
    valid = _bind()
    values = {
        name: getattr(valid, name)
        for name in ActorDecisionEnvelope.model_fields
    }
    with pytest.raises(
        InvalidActorEnvelope,
        match="cannot bypass",
    ):
        ActorDecisionEnvelope.model_construct(**values)

    forged = BaseModel.model_construct.__func__(
        ActorDecisionEnvelope,
        **{
            **values,
            "decision_id": "decision-forged",
            "frame_id": 0,
            "frame_scope": "desktop",
            "pointer_disposition": "held",
        }
    )
    for serialize in (
        forged.to_dict,
        forged.to_json,
        forged.model_dump,
        forged.model_dump_json,
        lambda: dict(forged),
    ):
        with pytest.raises(
            InvalidActorEnvelope,
            match="instance is not canonical",
        ):
            serialize()
    with pytest.raises(InvalidActorEnvelope, match="model_copy update"):
        valid.model_copy(update={"frame_id": 0})


def test_envelope_structurally_pins_the_real_frame_authority() -> None:
    authority = FrameAuthority()
    frame = authority.commit(
        epoch=7,
        hwnd=9123,
        region=(-1200, 0, 1200, 800),
        scope="target_window",
        reason="actor contract test",
        submitted_size=(900, 600),
        image_b64="c3VibWl0dGVkLWZyYW1l",
        captured_at=1.0,
    )
    reducer = _mission_reducer()
    mission = reducer.projection
    profile = _profile(mission)
    state = _state(frame=AuthoritativeFrameState(
        frame.frame_id,
        frame.hwnd,
        frame.epoch,
        True,
        True,
        frame.scope,
    ))
    catalog = _catalog()
    projection = CapabilityBroker(catalog).project(
        mission,
        profile,
        state,
    )
    envelope = bind_actor_decision(
        mission_reducer=reducer,
        profile=profile,
        state=state,
        catalog=catalog,
        projection=projection,
        proposal=_action(),
        frame_region=frame.region,
        submitted_image_size=frame.submitted_size,
        frame_image_sha256=frame.image_sha256,
        allowed_evidence_scope=("e-target",),
    )

    called, result = authority.run_if_current(
        envelope,
        lambda: "pinned",
    )
    assert called is True
    assert result == "pinned"


def test_binder_rejects_projection_catalog_and_tool_misalignment() -> None:
    reducer, mission, profile, state, catalog, projection = _runtime()
    common = {
        "mission_reducer": reducer,
        "profile": profile,
        "state": state,
        "catalog": catalog,
        "projection": projection,
        "frame_region": (0, 0, 1200, 800),
        "submitted_image_size": (1200, 800),
        "frame_image_sha256": "b" * 64,
    }
    with pytest.raises(
        InvalidActorAdmission,
        match="outside BrokerProjection",
    ):
        bind_actor_decision(
            **common,
            proposal=_action(action="drag"),
        )
    substituted_catalog = ToolCatalog((
        *catalog.tools,
        _tool("unused_tool", MutationKind.NONE, order=99),
    ))
    with pytest.raises(
        InvalidActorAdmission,
        match="fingerprints",
    ):
        bind_actor_decision(
            **{**common, "catalog": substituted_catalog},
            proposal=_action(),
        )
    with pytest.raises(
        InvalidActorAdmission,
        match="outside BrokerProjection",
    ):
        bind_actor_decision(
            **common,
            proposal=InspectionRequest(
                region=ImageRegion(x=1, y=1, width=2, height=2),
                question="Inspect this future-only region",
            ),
        )


def test_binder_replays_the_unique_mission_event_owner() -> None:
    reducer, mission, profile, state, catalog, projection = _runtime()
    target_evidence = mission.evidence_refs["e-target"]
    invented = replace(target_evidence, id="e-invented")
    forged_projection = replace(
        mission,
        evidence_refs={
            **mission.evidence_refs,
            invented.id: invented,
        },
    )
    common = {
        "profile": profile,
        "state": state,
        "catalog": catalog,
        "projection": projection,
        "proposal": _action(),
        "frame_region": (0, 0, 1200, 800),
        "submitted_image_size": (1200, 800),
        "frame_image_sha256": "b" * 64,
        "allowed_evidence_scope": (invented.id,),
    }
    with pytest.raises(
        InvalidActorAdmission,
        match="MissionReducer",
    ):
        bind_actor_decision(
            **common,
            mission_reducer=forged_projection,  # type: ignore[arg-type]
        )

    reducer._projection = forged_projection
    with pytest.raises(
        InvalidActorAdmission,
        match="not rebuildable from accepted events",
    ):
        bind_actor_decision(
            **common,
            mission_reducer=reducer,
        )


def test_binder_rejects_stale_and_canonical_broker_forgery() -> None:
    reducer = _mission_reducer()
    mission = reducer.projection
    profile = _profile(mission)
    state = _state()
    catalog = ToolCatalog((
        *_catalog().tools,
        _tool(
            "drag",
            MutationKind.GUI,
            capabilities=("visual.draw",),
            prerequisites=GUI_GATES,
            schema=_point_schema(),
            order=99,
        ),
    ))
    projection = CapabilityBroker(catalog).project(
        mission,
        profile,
        state,
    )
    assert "drag" not in projection.tool_names
    common = {
        "mission_reducer": reducer,
        "profile": profile,
        "state": state,
        "catalog": catalog,
        "proposal": _action(action="drag"),
        "frame_region": (0, 0, 1200, 800),
        "submitted_image_size": (1200, 800),
        "frame_image_sha256": "b" * 64,
    }

    stale = replace(projection)
    object.__setattr__(
        stale,
        "tool_names",
        (*stale.tool_names, "drag"),
    )
    with pytest.raises(InvalidActorAdmission):
        bind_actor_decision(
            **common,
            projection=stale,
        )

    forged_names = (*projection.tool_names, "drag")
    forged_id = _broker_projection_id(
        mission_id=projection.mission_id,
        run_id=projection.run_id,
        catalog_fingerprint=projection.catalog_fingerprint,
        profile_id=projection.profile_id,
        state_fingerprint=projection.state_fingerprint,
        mission_revision=projection.mission_revision,
        obligation_id=projection.obligation_id,
        turn_index=projection.turn_index,
        execution_route=projection.execution_route,
        executor_id=projection.executor_id,
        target_binding_id=projection.target_binding_id,
        frame_id=projection.frame_id,
        frame_scope=projection.frame_scope,
        pointer_lease_id=projection.pointer_lease_id,
        tool_names=forged_names,
        active_grants=projection.active_grants,
        missing_capabilities=projection.missing_capabilities,
        projection_reasons=projection.projection_reasons,
    )
    forged = BrokerProjection(
        **{
            **projection.to_dict(),
            "projection_id": forged_id,
            "tool_names": forged_names,
        }
    )
    with pytest.raises(
        InvalidActorAdmission,
        match="canonical Broker decision",
    ):
        bind_actor_decision(
            **common,
            projection=forged,
        )


@pytest.mark.parametrize(
    "args",
    (
        {"image_x": 10},
        {"image_x": 10, "image_y": 20, "extra": True},
        {"image_x": True, "image_y": 20},
        {"image_x": -1, "image_y": 20},
    ),
)
def test_binder_validates_desktop_args_against_exact_tool_schema(
    args,
) -> None:
    with pytest.raises(
        InvalidActorAdmission,
        match="schema_mismatch",
    ):
        _bind(proposal=_action(args=args))


def test_binder_enforces_exact_submitted_image_bounds() -> None:
    accepted = _bind(proposal=_action(args={
        "image_x": 899,
        "image_y": 599,
    }))
    assert accepted.proposal.args["image_x"] == 899
    for args in (
        {"image_x": 900, "image_y": 599},
        {"image_x": 899, "image_y": 600},
    ):
        with pytest.raises(
            InvalidActorAdmission,
            match="exact submitted image",
        ):
            _bind(proposal=_action(args=args))

    outside_region = ObserveProposal(
        observation_kind="image_region",
        image_region=ImageRegion(
            x=895,
            y=590,
            width=10,
            height=10,
        ),
        question="Inspect the submitted-image edge",
    )
    with pytest.raises(
        InvalidActorAdmission,
        match="exact submitted image",
    ):
        _bind(proposal=outside_region)


@pytest.mark.parametrize(
    ("tool", "proposal"),
    (
        (
            _tool(
                "click",
                MutationKind.NONE,
                capabilities=("visual.interact",),
                order=1,
            ),
            _action(),
        ),
        (
            _tool(
                "blocked",
                MutationKind.GUI,
                capabilities=("visual.interact",),
                prerequisites=GUI_GATES,
                order=1,
            ),
            BlockedReport(
                blocker_kind="unknown",
                observed_facts=("The task cannot proceed",),
            ),
        ),
    ),
)
def test_binder_rejects_tool_mutation_kind_spoofing(tool, proposal) -> None:
    reducer = _mission_reducer()
    mission = reducer.projection
    profile = _profile(mission)
    state = _state()
    catalog = ToolCatalog((tool,))
    projection = CapabilityBroker(catalog).project(
        mission, profile, state
    )
    with pytest.raises(
        InvalidActorAdmission,
        match="mutation contract",
    ):
        bind_actor_decision(
            mission_reducer=reducer,
            profile=profile,
            state=state,
            catalog=catalog,
            projection=projection,
            proposal=proposal,
            frame_region=(0, 0, 100, 100),
            submitted_image_size=(100, 100),
            frame_image_sha256="b" * 64,
        )


def test_binder_rejects_non_actor_route_and_stale_runtime() -> None:
    reducer = _mission_reducer(
        "communication.send_text",
        bound=False,
    )
    mission = reducer.projection
    profile = _profile(mission)
    offer = ExecutorOffer(
        executor_id="provider-1",
        route=ToolRoute.PROVIDER,
        supported_capabilities=("communication.send_text",),
        tool_names=("send_text",),
        available=True,
        live=True,
        authorization_scopes=("external.send",),
        receipt_kinds=("delivery.receipt",),
    )
    catalog = ToolCatalog((
        _tool(
            "send_text",
            MutationKind.EXTERNAL,
            route=ToolRoute.PROVIDER,
            capabilities=("communication.send_text",),
            prerequisites=(
                "recipient_bound",
                "adapter_live",
                "receipt_capable",
                "runtime_healthy",
            ),
            authorization_scope="external.send",
            order=1,
        ),
    ))
    state = _state(
        target=TargetState(),
        frame=AuthoritativeFrameState(),
        executor_offers=(offer,),
        authorization_scopes=("external.send", "recipient.bound"),
    )
    projection = CapabilityBroker(catalog).project(
        mission, profile, state
    )
    assert projection.execution_route is ExecutionRoute.PROVIDER
    with pytest.raises(InvalidActorAdmission, match="not a Visual Actor"):
        bind_actor_decision(
            mission_reducer=reducer,
            profile=profile,
            state=state,
            catalog=catalog,
            projection=projection,
            proposal=_action(),
            frame_region=(0, 0, 100, 100),
            submitted_image_size=(100, 100),
            frame_image_sha256="b" * 64,
        )

    reducer, mission, profile, state, catalog, projection = _runtime()
    stale_state = replace(state, authorization_scopes=("unused.scope",))
    with pytest.raises(InvalidActorAdmission, match="fingerprints"):
        bind_actor_decision(
            mission_reducer=reducer,
            profile=profile,
            state=stale_state,
            catalog=catalog,
            projection=projection,
            proposal=_action(),
            frame_region=(0, 0, 100, 100),
            submitted_image_size=(100, 100),
            frame_image_sha256="b" * 64,
        )


def test_binder_revalidates_forged_pydantic_proposal_and_evidence_scope() -> None:
    forged = DesktopActionProposal.model_construct(
        format=ACTOR_PROPOSAL_FORMAT,
        kind="desktop_action",
        action="done",
        args={},
        local_intent="forge completion",
    )
    with pytest.raises(InvalidActorProposal, match="not an ActorProposal"):
        actor_proposal_to_json(forged)
    with pytest.raises(InvalidActorAdmission, match="canonical"):
        _bind(proposal=forged)
    with pytest.raises(
        InvalidActorAdmission,
        match="outside the active obligation",
    ):
        _bind(allowed_evidence_scope=("e-invented",))


def test_actor_package_is_exact_pure_and_default_unreachable() -> None:
    home = Path(__file__).resolve().parents[1]
    package_root = home / "winpilot"
    actor_dir = home / "winpilot" / "vnext" / "actor"
    assert tuple(sorted(path.name for path in actor_dir.glob("*.py"))) == (
        "__init__.py",
        "envelope.py",
        "proposals.py",
    )
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in actor_dir.glob("*.py")
    )
    for forbidden in (
        "openai",
        "anthropic",
        "electron",
        "win32",
        "pyautogui",
        "desktopactionqueue",
        "rawdevice",
        "fastguicontroller",
        "target_acquisition",
        "visual_runtime",
        "from .model",
        "from .views",
        "from .runtime",
    ):
        assert forbidden not in source.lower()

    allowed_product_prefixes = (
        "winpilot.vnext.actor",
        "winpilot.vnext.capabilities",
        "winpilot.vnext.capability_names",
        "winpilot.vnext.mission",
        "winpilot.vnext.profiling",
    )
    for path in actor_dir.glob("*.py"):
        product_imports = tuple(
            imported
            for imported in _imports(path, package_root)
            if _matches_prefix(imported, "winpilot")
        )
        assert all(
            any(
                _matches_prefix(imported, prefix)
                for prefix in allowed_product_prefixes
            )
            for imported in product_imports
        )

    outside_actor = (
        path
        for path in package_root.rglob("*.py")
        if "actor" not in path.parts
    )
    violations = tuple(
        (path, imported)
        for path in outside_actor
        for imported in _imports(path, package_root)
        if _matches_prefix(imported, "winpilot.vnext.actor")
    )
    assert violations == ()


def test_import_scanner_expands_absolute_and_relative_aliases(
    tmp_path,
) -> None:
    package_root = tmp_path / "winpilot"
    consumer = package_root / "vnext" / "bridge" / "consumer.py"
    consumer.parent.mkdir(parents=True)
    consumer.write_text(
        "from winpilot.vnext import actor\n"
        "from .. import actor as relative_actor\n",
        encoding="utf-8",
    )
    imports = _imports(consumer, package_root)
    assert imports.count("winpilot.vnext.actor") == 2
