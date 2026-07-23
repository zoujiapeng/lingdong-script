"""P3 ToolCatalog, grant, pure Broker, and shadow property tests."""
from __future__ import annotations

from dataclasses import replace
import random

import pytest

from winpilot.vnext.capabilities import (
    ArtifactState,
    AuthoritativeFrameState,
    BrokerRuntimeState,
    BrokerShadowEvaluator,
    CapabilityBroker,
    CapabilityContractError,
    CapabilityGrant,
    ExecutionRoute,
    ExecutorOffer,
    MutationKind,
    NecessaryToolExpectation,
    PointerDisposition,
    PointerLeaseState,
    TargetState,
    ToolCatalog,
    ToolPolicy,
    ToolRoute,
    ToolSpec,
    build_tool_catalog,
    CapabilityOwner,
    RuntimeCapabilityRegistry,
    legacy_local_capability_registry,
)
from winpilot.vnext.mission import (
    ArtifactRef,
    EvidenceRef,
    MissionDefinition,
    MissionReducer,
    ObligationSpec,
    target_binding_attestation,
)
from winpilot.vnext.profiling import DEFAULT_CAPABILITY_CATALOG, ExecutionPolicy
from winpilot.vnext.targeting import (
    TargetActivation,
    TargetCandidate,
    TargetState as DiscoveredTargetState,
    candidate_fingerprint_for,
    candidate_ref_for,
    launch_identity_for,
)


ALL_CAPABILITIES = tuple(sorted(DEFAULT_CAPABILITY_CATALOG))
GUI_CAPABILITIES = (
    "visual.interact", "visual.draw", "visual.read_text", "pointer.dynamic",
    "communication.bind_recipient", "communication.send_artifact",
    "communication.send_text", "communication.verify_delivery", "mcp.office",
)
TARGET_FP_EMPTY = candidate_fingerprint_for(())
_TARGET_EXE = r"C:\GenericApps\Example\example.exe"
_TARGET_CANDIDATE = TargetCandidate(
    candidate_ref=candidate_ref_for(
        activation=TargetActivation.OPEN_APP,
        identity=launch_identity_for(kind="exe", target=_TARGET_EXE),
    ),
    activation=TargetActivation.OPEN_APP,
    state=DiscoveredTargetState.INSTALLED,
    label="Example",
    provenance=("installed_catalog",),
    launch_kind="exe",
    launch_target=_TARGET_EXE,
)
TARGET_FP_ONE = candidate_fingerprint_for((_TARGET_CANDIDATE,))


def _schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "parameters": {"type": "object", "additionalProperties": False, "properties": {}},
        },
    }


def _catalog() -> ToolCatalog:
    policies = (
        ToolPolicy("observe", ToolRoute.VISUAL_ACTOR, MutationKind.NONE, order=1),
        ToolPolicy("resolve_target", ToolRoute.VISUAL_ACTOR, MutationKind.NONE,
                   ("application.resolve",), ("target_unbound",), order=2),
        ToolPolicy("click", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
                   GUI_CAPABILITIES, ("target_bound", "authoritative_frame", "pointer_released", "runtime_healthy"), order=3),
        ToolPolicy("type_text", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
                   GUI_CAPABILITIES, ("target_bound", "authoritative_frame", "pointer_released", "runtime_healthy"), order=4),
        ToolPolicy("hotkey", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
                   GUI_CAPABILITIES, ("target_bound", "authoritative_frame", "pointer_released", "runtime_healthy"), order=5),
        ToolPolicy("scroll", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
                   GUI_CAPABILITIES, ("target_bound", "authoritative_frame", "pointer_released", "runtime_healthy"), order=6),
        ToolPolicy("drag", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
                   GUI_CAPABILITIES, ("target_bound", "authoritative_frame", "pointer_released", "runtime_healthy"), order=7),
        ToolPolicy("draw_strokes", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
                   ("visual.draw",), ("target_bound", "authoritative_frame", "pointer_released", "runtime_healthy"), order=8),
        ToolPolicy("mouse_down", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
                   ("pointer.dynamic",), ("target_bound", "authoritative_frame", "pointer_released", "runtime_healthy"), order=9),
        ToolPolicy("move_mouse", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
                   ("pointer.dynamic",), ("pointer_held",), order=10),
        ToolPolicy("mouse_up", ToolRoute.VISUAL_ACTOR, MutationKind.GUI,
                   ("pointer.dynamic",), (), order=11),
        ToolPolicy("wait", ToolRoute.VISUAL_ACTOR, MutationKind.NONE, order=12),
        ToolPolicy("request_capability", ToolRoute.VISUAL_ACTOR, MutationKind.NONE, order=13),
        ToolPolicy("claim_obligation_done", ToolRoute.VISUAL_ACTOR, MutationKind.NONE, order=14),
        ToolPolicy("blocked", ToolRoute.VISUAL_ACTOR, MutationKind.NONE, order=15),
        ToolPolicy("send_text", ToolRoute.PROVIDER, MutationKind.EXTERNAL,
                   ("communication.send_text",),
                   ("recipient_bound", "adapter_live", "receipt_capable", "runtime_healthy"),
                   "external.send", 20),
        ToolPolicy("send_artifact", ToolRoute.PROVIDER, MutationKind.EXTERNAL,
                   ("communication.send_artifact",),
                   ("current_run_artifact", "recipient_bound", "adapter_live", "receipt_capable", "runtime_healthy"),
                   "external.send", 21),
    )
    return build_tool_catalog((_schema(item.name) for item in policies), policies)


def _target_attestation():
    return target_binding_attestation(
        mission_id="m1",
        run_id="r1",
        obligation_id="o1",
        evidence_id="e-target",
        acquisition_action_id="target-acquisition-1",
        hwnd=101,
        root_hwnd=101,
        process_id=7,
        process_identity=f"process-{'1' * 32}",
        title_snapshot="",
        epoch=3,
        frame_id=9,
    )


def _mission(capability: str = "visual.interact", *, bound: bool = True):
    definition = MissionDefinition(
        mission_id="m1",
        run_id="r1",
        original_goal="Complete the current unknown GUI task",
        obligations=(ObligationSpec(
            id="o1",
            desired_state="the requested state exists",
            acceptance_criteria=("the state is visibly or deterministically confirmed",),
            required_capabilities=(capability,),
        ),),
    )
    target_binding = None
    evidence = ()
    if bound:
        target_binding, target_evidence = _target_attestation()
        evidence = (target_evidence,)
    reducer = MissionReducer(definition, evidence=evidence)
    if bound:
        assert target_binding is not None
        reducer.apply(reducer.event(
            "target_bound",
            payload={"target_binding": target_binding.to_dict()},
            evidence_refs=("e-target",),
        ))
    return reducer.projection


def _mission_with_artifact_binding():
    definition = MissionDefinition(
        mission_id="m1", run_id="r1", original_goal="capture and send one artifact",
        obligations=(
            ObligationSpec(
                id="capture", desired_state="an artifact exists",
                acceptance_criteria=("artifact existence is confirmed",),
                required_capabilities=("artifact.capture",),
                output_binding_names=("current_artifact",),
            ),
            ObligationSpec(
                id="send", desired_state="the same artifact is delivered",
                acceptance_criteria=("delivery is confirmed",),
                depends_on=("capture",),
                input_artifact_refs=("artifact-1",),
                required_capabilities=("communication.send_artifact",),
            ),
        ),
    )
    evidence = (
        EvidenceRef(
            id="capture-proof", mission_id="m1", run_id="r1",
            obligation_id="capture", kind="oracle", trust="deterministic",
            payload_hash="capture-proof-hash",
        ),
        EvidenceRef(
            id="artifact-proof", mission_id="m1", run_id="r1",
            obligation_id="capture", kind="artifact", trust="deterministic",
            payload_hash="artifact-proof-hash", artifact_id="artifact-1",
            content_sha256="a" * 64,
        ),
    )
    reducer = MissionReducer(definition, evidence=evidence)
    reducer.apply(reducer.event(
        "obligation_claimed", payload={"obligation_id": "capture"},
    ))
    reducer.apply(reducer.event(
        "verification_recorded",
        payload={
            "obligation_id": "capture", "verified": True,
            "satisfied_criteria": ["artifact existence is confirmed"],
            "verification_id": "capture-verification",
            "evidence_refs": ["capture-proof"],
            "verifier_kind": "deterministic",
        },
    ))
    artifact = ArtifactRef(
        artifact_id="artifact-1", media_type="image/png", sha256="a" * 64,
        producer_obligation_id="capture", evidence_ref="artifact-proof",
    )
    reducer.apply(reducer.event(
        "artifact_bound",
        payload={"binding_name": "current_artifact", "artifact": artifact.to_dict()},
        evidence_refs=("artifact-proof",),
    ))
    return reducer.projection


def _profile(mission):
    return ExecutionPolicy().safe_default(
        mission.mission_id,
        mission.definition.goal_revision,
        available_capabilities=ALL_CAPABILITIES,
    )


def _state(**overrides):
    values = {
        "turn_index": 4,
        "available_capabilities": ALL_CAPABILITIES,
        "target": TargetState(
            _target_attestation()[0].binding_id, 101, 3, True,
        ),
        "frame": AuthoritativeFrameState(
            frame_id=9,
            hwnd=101,
            epoch=3,
            scope="target_window",
            authoritative=True,
            fresh=True,
        ),
        "pointer": PointerLeaseState(PointerDisposition.RELEASED),
    }
    values.update(overrides)
    return BrokerRuntimeState(**values)


def test_catalog_is_strict_versioned_and_order_deterministic() -> None:
    catalog = _catalog()
    restored = ToolCatalog.from_dict(catalog.to_dict())
    assert restored.fingerprint == catalog.fingerprint
    assert ToolCatalog(tuple(reversed(catalog.tools))).fingerprint == catalog.fingerprint
    with pytest.raises(CapabilityContractError):
        ToolSpec("done", {"type": "object"}, ToolRoute.VISUAL_ACTOR, MutationKind.NONE)
    raw = catalog.tools[0].to_dict()
    with pytest.raises(CapabilityContractError, match="format"):
        ToolSpec.from_dict({**raw, "format": "tool-spec/0"})
    with pytest.raises(CapabilityContractError, match="format"):
        ToolCatalog.from_dict({**catalog.to_dict(), "format": "tool-catalog/0"})
    with pytest.raises(CapabilityContractError):
        ToolSpec.from_dict({**raw, "application_name": "anything"})
    state = _state()
    assert BrokerRuntimeState.from_dict(state.to_dict()) == state
    with pytest.raises(CapabilityContractError):
        BrokerRuntimeState.from_dict({**state.to_dict(), "task_text": "benchmark case"})
    projection = CapabilityBroker(catalog).project(
        _mission(), _profile(_mission()), state,
    )
    with pytest.raises(CapabilityContractError, match="format"):
        type(projection).from_dict({
            **projection.to_dict(),
            "format": "broker-projection/0",
        })


def test_scope_and_candidate_contracts_reject_old_or_ambiguous_wire_state() -> None:
    state = _state()
    raw = state.to_dict()
    for old_format in ("broker-runtime-state/0", "broker-runtime-state/2"):
        with pytest.raises(CapabilityContractError, match="runtime state"):
            BrokerRuntimeState.from_dict({**raw, "format": old_format})
    old_frame = dict(raw["frame"])
    old_frame.pop("scope")
    with pytest.raises(CapabilityContractError, match="fields mismatch"):
        BrokerRuntimeState.from_dict({**raw, "frame": old_frame})
    with pytest.raises(CapabilityContractError, match="entirely present"):
        AuthoritativeFrameState(
            frame_id=9,
            hwnd=101,
            epoch=3,
            authoritative=True,
            fresh=True,
        )
    with pytest.raises(CapabilityContractError, match="positive"):
        AuthoritativeFrameState(
            frame_id=0,
            hwnd=1,
            epoch=0,
            scope="full_desktop",
            authoritative=True,
            fresh=True,
        )
    with pytest.raises(CapabilityContractError, match="cannot retain"):
        TargetState(binding_id="target-stale", live=False)

    desktop = AuthoritativeFrameState(
        frame_id=11,
        hwnd=1,
        epoch=0,
        scope="full_desktop",
        authoritative=True,
        fresh=True,
    )
    base = dict(
        turn_index=2,
        available_capabilities=ALL_CAPABILITIES,
        target=TargetState(),
        frame=desktop,
    )
    with pytest.raises(CapabilityContractError, match="present together"):
        BrokerRuntimeState(candidate_fingerprint=TARGET_FP_EMPTY, **base)
    with pytest.raises(CapabilityContractError, match="present together"):
        BrokerRuntimeState(candidate_count=1, **base)
    with pytest.raises(CapabilityContractError, match="count"):
        BrokerRuntimeState(
            candidate_fingerprint=TARGET_FP_EMPTY,
            candidate_count=17,
            **base,
        )
    with pytest.raises(CapabilityContractError, match="unbound full-desktop"):
        _state(candidate_fingerprint=TARGET_FP_ONE, candidate_count=1)


def test_projection_identity_binds_frame_scope_and_candidate_set() -> None:
    mission = _mission(bound=False)
    profile = _profile(mission)
    broker = CapabilityBroker(_catalog())

    def project(*, frame_id: int, fingerprint: str | None, count: int | None):
        return broker.project(
            mission,
            profile,
            BrokerRuntimeState(
                turn_index=2,
                available_capabilities=ALL_CAPABILITIES,
                target=TargetState(),
                frame=AuthoritativeFrameState(
                    frame_id=frame_id,
                    hwnd=1,
                    epoch=0,
                    scope="full_desktop",
                    authoritative=True,
                    fresh=True,
                ),
                candidate_fingerprint=fingerprint,
                candidate_count=count,
            ),
        )

    baseline = project(frame_id=11, fingerprint=None, count=None)
    discovered_empty = project(
        frame_id=11,
        fingerprint=TARGET_FP_EMPTY,
        count=0,
    )
    discovered_one = project(
        frame_id=11,
        fingerprint=TARGET_FP_ONE,
        count=1,
    )
    next_frame = project(frame_id=12, fingerprint=TARGET_FP_ONE, count=1)

    assert len({
        baseline.projection_id,
        discovered_empty.projection_id,
        discovered_one.projection_id,
        next_frame.projection_id,
    }) == 4
    assert discovered_one.candidate_fingerprint == TARGET_FP_ONE
    assert discovered_one.candidate_count == 1


def test_target_acquisition_prerequisites_require_real_desktop_and_candidates() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
        "required": [],
    }
    catalog = ToolCatalog((
        ToolSpec(
            name="discover_target",
            schema=schema,
            route=ToolRoute.VISUAL_ACTOR,
            mutation_kind=MutationKind.NONE,
            required_any_capabilities=("visual.interact",),
            prerequisites=(
                "target_unbound",
                "authoritative_full_desktop_frame",
                "pointer_released",
                "runtime_healthy",
            ),
            order=1,
        ),
        ToolSpec(
            name="activate_target",
            schema=schema,
            route=ToolRoute.VISUAL_ACTOR,
            mutation_kind=MutationKind.GUI,
            required_any_capabilities=("visual.interact",),
            prerequisites=(
                "target_unbound",
                "authoritative_full_desktop_frame",
                "target_candidates_available",
                "pointer_released",
                "runtime_healthy",
            ),
            order=2,
        ),
    ))
    mission = _mission(bound=False)
    profile = _profile(mission)
    broker = CapabilityBroker(catalog)
    desktop = AuthoritativeFrameState(
        frame_id=11,
        hwnd=1,
        epoch=0,
        scope="full_desktop",
        authoritative=True,
        fresh=True,
    )

    undiscovered = BrokerRuntimeState(
        turn_index=0,
        available_capabilities=ALL_CAPABILITIES,
        frame=desktop,
    )
    empty = replace(
        undiscovered,
        candidate_fingerprint=TARGET_FP_EMPTY,
        candidate_count=0,
    )
    selectable = replace(
        undiscovered,
        candidate_fingerprint=TARGET_FP_ONE,
        candidate_count=1,
    )
    assert BrokerRuntimeState.from_dict(selectable.to_dict()) == selectable

    assert broker.project(
        mission, profile, undiscovered,
    ).tool_names == ("discover_target",)
    assert broker.project(mission, profile, empty).tool_names == (
        "discover_target",
    )
    selectable_projection = broker.project(mission, profile, selectable)
    assert type(selectable_projection).from_dict(
        selectable_projection.to_dict()
    ) == selectable_projection
    assert selectable_projection.tool_names == (
        "discover_target", "activate_target",
    )
    assert "activate_target" not in broker.project(
        _mission(),
        _profile(_mission()),
        _state(),
    ).tool_names

    for invalid_frame in (
        replace(desktop, epoch=1),
        replace(desktop, scope="target_window"),
        replace(desktop, authoritative=False),
        replace(desktop, fresh=False),
    ):
        state = replace(undiscovered, frame=invalid_frame)
        assert broker.project(mission, profile, state).tool_names == ()
        with pytest.raises(CapabilityContractError, match="fresh authoritative"):
            replace(selectable, frame=invalid_frame)


def test_runtime_capability_availability_has_one_owner_scoped_registry() -> None:
    snapshot = legacy_local_capability_registry().snapshot()
    assert "visual.interact" in snapshot
    assert "artifact.capture" in snapshot
    assert "mcp.office" not in snapshot
    registry = RuntimeCapabilityRegistry((
        CapabilityOwner("live-owner", ("visual.interact",), True),
        CapabilityOwner("offline-owner", ("visual.draw",), False),
    ))
    assert registry.snapshot() == ("visual.interact",)
    with pytest.raises(CapabilityContractError, match="unique"):
        RuntimeCapabilityRegistry((
            CapabilityOwner("same-owner", ("visual.interact",)),
            CapabilityOwner("same-owner", ("visual.draw",)),
        ))


def test_catalog_schema_is_deeply_immutable_and_cyclic_json_fails_closed() -> None:
    schema = {"type": "object", "properties": {"value": {"type": "string"}}}
    spec = ToolSpec("observe", schema, ToolRoute.VISUAL_ACTOR, MutationKind.NONE)
    schema["properties"]["value"]["type"] = "number"
    assert spec.to_dict()["schema"]["properties"]["value"]["type"] == "string"
    with pytest.raises(TypeError):
        spec.schema["properties"]["value"]["type"] = "boolean"

    cyclic = {"type": "object"}
    cyclic["properties"] = cyclic
    with pytest.raises(CapabilityContractError, match="cyclic"):
        ToolSpec("observe", cyclic, ToolRoute.VISUAL_ACTOR, MutationKind.NONE)


@pytest.mark.parametrize(
    ("route", "mutation_kind"),
    (
        (ToolRoute.VISUAL_ACTOR, MutationKind.EXTERNAL),
        (ToolRoute.PROVIDER, MutationKind.GUI),
        (ToolRoute.DETERMINISTIC, MutationKind.GUI),
    ),
)
def test_tool_route_cannot_create_a_second_gui_writer(route, mutation_kind) -> None:
    with pytest.raises(CapabilityContractError):
        ToolSpec(
            "unsafe_tool",
            {"type": "object"},
            route,
            mutation_kind,
            authorization_scope=(
                "external.write" if mutation_kind is MutationKind.EXTERNAL else None
            ),
        )


@pytest.mark.parametrize(
    ("factory", "values"),
    (
        (TargetState, ("target-1", 101, 3, "true")),
        (
            AuthoritativeFrameState,
            (9, 101, 3, "target_window", 1, True),
        ),
        (PointerLeaseState, (PointerDisposition.RELEASED, None, "false")),
        (ArtifactState, ("artifact-1", "r1", "a" * 64, "binding", 1)),
    ),
)
def test_runtime_state_booleans_are_strict(factory, values) -> None:
    with pytest.raises(CapabilityContractError, match="boolean"):
        factory(*values)


def test_identity_hash_and_authorization_values_do_not_coerce_to_strings() -> None:
    with pytest.raises(CapabilityContractError, match="string"):
        ArtifactState(123, "r1", "a" * 64, "binding", False)
    with pytest.raises(CapabilityContractError, match="string"):
        ArtifactState("artifact-1", "r1", int("1" * 64), "binding", False)
    with pytest.raises(CapabilityContractError, match="string"):
        ExecutorOffer(
            executor_id="provider-1", route=ToolRoute.PROVIDER,
            supported_capabilities=("communication.send_text",),
            tool_names=("send_text",), available=True, live=True,
            authorization_scopes=(999,), receipt_kinds=("receipt",),
        )


def test_physical_pointer_ownership_requires_held_state() -> None:
    with pytest.raises(CapabilityContractError, match="held pointer"):
        PointerLeaseState(PointerDisposition.UNKNOWN, "lease-1", True)


def test_grant_is_bound_to_mission_run_obligation_revision_and_expiry() -> None:
    grant = CapabilityGrant.issue(
        mission_id="m1", run_id="r1", capability="visual.draw",
        obligation_id="o1", mission_revision=0, issued_turn=3,
        expires_after_turns=2, policy_reason="accepted explicit capability request",
    )
    assert grant.valid_for(
        mission_id="m1", run_id="r1", obligation_id="o1",
        mission_revision=0, turn_index=3,
    )
    assert grant.valid_for(
        mission_id="m1", run_id="r1", obligation_id="o1",
        mission_revision=0, turn_index=4,
    )
    assert not grant.valid_for(
        mission_id="m1", run_id="r1", obligation_id="o1",
        mission_revision=0, turn_index=5,
    )
    assert not grant.valid_for(
        mission_id="m1", run_id="other", obligation_id="o1",
        mission_revision=0, turn_index=4,
    )
    with pytest.raises(CapabilityContractError, match="string"):
        CapabilityGrant.issue(
            mission_id=123, run_id="r1", capability="visual.draw",
            obligation_id="o1", mission_revision=0, issued_turn=3,
            expires_after_turns=2, policy_reason="reason",
        )

    mismatched = CapabilityGrant.issue(
        mission_id="m1", run_id="r1", capability="visual.draw",
        obligation_id="other-obligation", mission_revision=0, issued_turn=3,
        expires_after_turns=2, policy_reason="reason",
    )
    projection = CapabilityBroker(_catalog()).project(
        _mission(), _profile(_mission()), _state()
    )
    with pytest.raises(CapabilityContractError, match="scope"):
        replace(projection, active_grants=(mismatched,))


def test_bound_fresh_visual_surface_is_small_and_generic() -> None:
    mission = _mission()
    projection = CapabilityBroker(_catalog()).project(
        mission, _profile(mission), _state()
    )
    assert projection.execution_route is ExecutionRoute.VISUAL_ACTOR
    assert projection.tool_names == (
        "observe", "click", "type_text", "hotkey", "scroll", "drag",
        "wait", "request_capability", "claim_obligation_done", "blocked",
    )
    assert len(projection.tool_names) == 10


@pytest.mark.parametrize(
    "frame",
    (
        AuthoritativeFrameState(9, 999, 3, "target_window", True, True),
        AuthoritativeFrameState(9, 101, 4, "target_window", True, True),
        AuthoritativeFrameState(9, 101, 3, "target_window", False, True),
        AuthoritativeFrameState(9, 101, 3, "target_window", True, False),
    ),
)
def test_stale_or_mismatched_frame_removes_all_gui_mutations(frame) -> None:
    mission = _mission()
    catalog = _catalog()
    projection = CapabilityBroker(catalog).project(
        mission, _profile(mission), _state(frame=frame)
    )
    assert all(
        catalog.get(name).mutation_kind is not MutationKind.GUI
        for name in projection.tool_names
    )


def test_held_pointer_has_exact_continuation_release_surface() -> None:
    mission = _mission("pointer.dynamic")
    projection = CapabilityBroker(_catalog()).project(
        mission,
        _profile(mission),
        _state(pointer=PointerLeaseState(
            PointerDisposition.HELD, "pointer-lease-1", True,
        )),
    )
    assert projection.tool_names == ("move_mouse", "mouse_up", "wait", "blocked")
    assert projection.pointer_lease_id == "pointer-lease-1"


def test_unknown_pointer_state_only_allows_release_wait_and_block() -> None:
    mission = _mission()
    projection = CapabilityBroker(_catalog()).project(
        mission, _profile(mission),
        _state(pointer=PointerLeaseState(PointerDisposition.UNKNOWN)),
    )
    assert projection.execution_route is ExecutionRoute.BLOCKED
    assert projection.tool_names == ("mouse_up", "wait", "blocked")


def test_terminal_mission_exposes_no_mutation() -> None:
    reducer = MissionReducer(_mission(bound=False).definition)
    reducer.apply(reducer.event(
        "mission_failed", payload={"reason": "deterministic test failure"},
    ))
    mission = reducer.projection
    catalog = _catalog()
    projection = CapabilityBroker(catalog).project(
        mission, _profile(mission), _state()
    )
    assert projection.execution_route is ExecutionRoute.BLOCKED
    assert all(catalog.get(name).mutation_kind is MutationKind.NONE for name in projection.tool_names)


def test_live_receipted_provider_route_is_mutually_exclusive_with_gui() -> None:
    mission = _mission("communication.send_text", bound=False)
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


def test_provider_without_receipt_or_authorization_is_not_selected() -> None:
    mission = _mission("communication.send_text", bound=False)
    offer = ExecutorOffer(
        executor_id="provider-1", route=ToolRoute.PROVIDER,
        supported_capabilities=("communication.send_text",),
        tool_names=("send_text",), available=True, live=True,
        authorization_scopes=("external.send",), receipt_kinds=(),
    )
    projection = CapabilityBroker(_catalog()).project(
        mission, _profile(mission), _state(
            target=TargetState(), frame=AuthoritativeFrameState(),
            executor_offers=(offer,), authorization_scopes=("recipient.bound",),
        )
    )
    assert projection.execution_route is ExecutionRoute.VISUAL_ACTOR
    assert "send_text" not in projection.tool_names


def test_provider_offer_cannot_expose_tool_for_an_unrelated_capability() -> None:
    mission = _mission("communication.send_text", bound=False)
    offer = ExecutorOffer(
        executor_id="provider-1", route=ToolRoute.PROVIDER,
        supported_capabilities=(
            "communication.send_text", "communication.send_artifact",
        ),
        tool_names=("send_artifact",), available=True, live=True,
        authorization_scopes=("external.send",),
        receipt_kinds=("delivery.receipt",),
    )
    projection = CapabilityBroker(_catalog()).project(
        mission, _profile(mission), _state(
            target=TargetState(), frame=AuthoritativeFrameState(),
            executor_offers=(offer,),
            authorization_scopes=("external.send", "recipient.bound"),
        )
    )
    assert projection.execution_route is ExecutionRoute.VISUAL_ACTOR
    assert "send_artifact" not in projection.tool_names


def test_executor_offer_identity_is_unique_and_input_order_cannot_choose_route() -> None:
    offers = tuple(
        ExecutorOffer(
            executor_id="duplicate-provider", route=ToolRoute.PROVIDER,
            supported_capabilities=(capability,), tool_names=(tool,),
            available=True, live=True, authorization_scopes=("external.send",),
            receipt_kinds=("delivery.receipt",),
        )
        for capability, tool in (
            ("communication.send_text", "send_text"),
            ("communication.send_artifact", "send_artifact"),
        )
    )
    with pytest.raises(CapabilityContractError, match="unique"):
        _state(executor_offers=offers)


def test_artifact_id_and_hash_must_match_mission_binding() -> None:
    mission = _mission_with_artifact_binding()
    offer = ExecutorOffer(
        executor_id="provider-1", route=ToolRoute.PROVIDER,
        supported_capabilities=("communication.send_artifact",),
        tool_names=("send_artifact",), available=True, live=True,
        authorization_scopes=("external.send",), receipt_kinds=("delivery.receipt",),
    )
    base = dict(
        target=TargetState(), frame=AuthoritativeFrameState(),
        executor_offers=(offer,), authorization_scopes=("external.send", "recipient.bound"),
    )
    broker = CapabilityBroker(_catalog())
    for artifact in (
        ArtifactState("wrong-id", "r1", "a" * 64, "current_artifact", True),
        ArtifactState("artifact-1", "r1", "b" * 64, "current_artifact", True),
        ArtifactState("artifact-1", "wrong-run", "a" * 64, "current_artifact", True),
        ArtifactState("artifact-1", "r1", "a" * 64, "current_artifact", False),
    ):
        projection = broker.project(
            mission, _profile(mission), _state(artifacts=(artifact,), **base)
        )
        assert projection.execution_route is not ExecutionRoute.PROVIDER


def test_projection_is_deterministic_under_input_order_randomization() -> None:
    mission = _mission()
    catalog = _catalog()
    profile = _profile(mission)
    baseline = CapabilityBroker(catalog).project(mission, profile, _state())
    values = list(ALL_CAPABILITIES)
    random.Random(9).shuffle(values)
    replayed = CapabilityBroker(ToolCatalog(tuple(reversed(catalog.tools)))).project(
        mission, profile, _state(available_capabilities=tuple(values))
    )
    assert replayed.to_dict() == baseline.to_dict()


def test_shadow_recall_is_none_without_complete_oracle_label() -> None:
    mission = _mission()
    catalog = _catalog()
    projection = CapabilityBroker(catalog).project(mission, _profile(mission), _state())
    evaluator = BrokerShadowEvaluator(catalog)
    unlabeled = evaluator.evaluate(projection, actual_legacy_tool="click")
    assert unlabeled.actual_tool_allowed is True
    assert unlabeled.necessary_tool_recall is None
    labeled = evaluator.evaluate(
        projection,
        expectation=NecessaryToolExpectation(
            required_groups=(("click",), ("observe", "read_text")),
            forbidden_tools=("draw_strokes",),
            complete_relevance_label=True,
        ),
    )
    assert labeled.necessary_tool_recall == 1.0
    assert labeled.forbidden_tools_exposed == ()


def test_removing_runtime_capability_never_adds_tools_property() -> None:
    mission = _mission()
    broker = CapabilityBroker(_catalog())
    full = broker.project(mission, _profile(mission), _state())
    reduced = broker.project(
        mission,
        _profile(mission),
        _state(available_capabilities=tuple(
            item for item in ALL_CAPABILITIES if item != "visual.interact"
        )),
    )
    assert set(reduced.tool_names) <= set(full.tool_names)
    assert reduced.execution_route is ExecutionRoute.BLOCKED


def test_profile_capability_removed_from_runtime_cannot_expose_its_tool() -> None:
    mission = _mission("visual.interact")
    broker = CapabilityBroker(_catalog())
    state = _state(available_capabilities=tuple(
        item for item in ALL_CAPABILITIES if item != "visual.draw"
    ))
    projection = broker.project(mission, _profile(mission), state)
    assert "draw_strokes" not in projection.tool_names
