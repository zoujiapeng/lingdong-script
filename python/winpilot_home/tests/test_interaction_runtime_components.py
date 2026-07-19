"""Focused component tests for generic typed visual interaction runtime.

These tests deliberately do not claim to be end-to-end tests: live window
capture, the VLM service, and physical input are replaced at component seams.
"""
from __future__ import annotations

import threading
import time

import pytest


def _decision(
    *,
    action=None,
    turn_owner="agent",
    continuation="finish",
    frame_id=4,
    observation_artifact_id="frame-1",
    semantic_verification_required=True,
):
    from winpilot.tasking.interaction import (
        ExpectedInteractionEffect,
        InteractionDecision,
    )

    should_act = action is not None
    return InteractionDecision(
        scene_summary="one bound visual target",
        mode="direct_visual_manipulation",
        turn_owner=turn_owner,
        agent_should_act=should_act,
        interaction_region=[200, 200, 800, 800],
        roles={"agent": {"role": "operator"}},
        rules=["mutate only the bound target"],
        confidence=0.94,
        ambiguity=[],
        actions=[action] if action is not None else [],
        expected_effect=ExpectedInteractionEffect(
            description="the target changes" if should_act else "",
            region=[300, 300, 500, 500] if should_act else [],
            minimum_change_ratio=0.01,
            semantic_verification_required=semantic_verification_required,
        ),
        continuation=continuation,
        wait_for_external_change=continuation == "wait_external",
        frame_id=frame_id,
        observation_artifact_id=observation_artifact_id,
    )


def _stage_output(_stage, _started, **payload):
    return payload


def _legacy_interaction_contract(
    *,
    persistent: bool = False,
    max_steps: int = 8,
    task: str = "perform the visible interaction",
    primary_action_modality: str = "infer_from_scene",
    include_capture: bool = False,
):
    """Deserialize one frozen task-contract/1 interaction fixture.

    The current compiler must not manufacture this legacy five-stage planner.
    These tests retain the historical wire shape only so saved contracts can
    still be deserialized and executed by the compatibility runtime.
    """
    from winpilot.tasking.contracts import TaskContract

    requested = max(1, min(400, int(max_steps)))
    session_id = "legacy-session-persistent" if persistent else "legacy-session-bounded"

    def evidence(predicate, stage_id, *provider_ids):
        return {
            "id": predicate,
            "predicate": predicate,
            "description": f"legacy compatibility evidence for {predicate}",
            "stage_id": stage_id,
            "evidence_providers": [
                {"id": provider_id} for provider_id in provider_ids
            ],
        }

    obligations = {
        "scene": evidence(
            "interaction.scene_bound", "legacy-observe", "bound_scene_observer",
        ),
        "protocol": evidence(
            "interaction.protocol_inferred",
            "legacy-infer",
            "interaction_protocol_inference",
        ),
        "turn": evidence(
            "interaction.turn_resolved",
            "legacy-perform",
            "bound_interaction_transaction",
        ),
        "effect": evidence(
            "interaction.local_effect_verified",
            "legacy-verify",
            "grounded_transaction_progress",
            "local_semantic_effect_verifier",
            "typed_freehand_turn_completion",
        ),
        "complete": evidence(
            "interaction.task_completed",
            "legacy-verify",
            "interaction_terminal_verifier",
        ),
    }
    execution = {
        "controller": "legacy_task_contract_runtime",
        "satisfiable": True,
    }

    def stage(stage_id, stage_type, step_limit, depends_on, obligation_keys, **parameters):
        return {
            "id": stage_id,
            "type": stage_type,
            "name": f"Legacy {stage_type}",
            "parameters": {
                "original_task": task,
                "max_steps": min(step_limit, requested),
                **parameters,
            },
            "depends_on": depends_on,
            "obligations": [obligations[key] for key in obligation_keys],
            "execution": execution,
        }

    interaction_parameters = {
        "session_id": session_id,
        "primary_action_modality": primary_action_modality,
        "action_modalities": [primary_action_modality],
    }
    stages = [
        stage("legacy-observe", "ObserveScene", 2, [], ["scene"]),
        stage(
            "legacy-infer", "InferInteraction", 3, ["legacy-observe"],
            ["protocol"], **interaction_parameters,
        ),
        stage(
            "legacy-perform", "PerformTurn", 4, ["legacy-infer"],
            ["turn"], turn_scope="one_semantic_turn", max_agent_turns=1,
            **interaction_parameters,
        ),
        stage(
            "legacy-verify", "VerifyLocalEffect", 2, ["legacy-perform"],
            ["effect", "complete"], session_id=session_id,
            verification_policy="semantic_visual_effect",
        ),
    ]
    if persistent:
        stages.append(stage(
            "legacy-wait", "WaitForExternalChange", 1, ["legacy-verify"],
            [], session_id=session_id, timeout_s=300.0,
        ))
    if include_capture:
        stages.append(stage(
            "legacy-capture", "CaptureArtifact", 1, [stages[-1]["id"]], [],
        ))

    all_obligations = list(obligations.values())
    return TaskContract.model_validate({
        "format": "task-contract/1",
        "task_id": "legacy-interaction-fixture",
        "task": task,
        "intent": {
            "original_text": task,
            "operation": ["interact"],
            "domain": "desktop",
            "interaction": {
                "active": True,
                "mode": "turn_based" if persistent else "direct_visual_manipulation",
                "session_mode": "persistent" if persistent else "bounded",
                "primary_action_modality": primary_action_modality,
            },
        },
        "stages": stages,
        "obligations": all_obligations,
        "final_conditions": [item["id"] for item in all_obligations],
        "session": {
            "session_id": session_id,
            "mode": "turn_based" if persistent else "direct_visual_manipulation",
            "lifecycle": "persistent" if persistent else "bounded",
            "initial_turn_owner": "agent",
            "primary_action_modality": primary_action_modality,
            "continuation_policy": (
                "wait_for_external_change"
                if persistent
                else "reobserve_until_goal_complete"
            ),
            "wait_for_external_change": persistent,
            "requested_max_steps": requested,
            "max_semantic_turns": max(1, min(200, requested // 2)),
            "max_replans": max(0, min(100, requested // 4)),
            "max_model_calls": max(2, requested),
            "external_change_timeout_s": 300.0,
        },
        "satisfiable": True,
        "experience_policy": "disabled",
        "compiler_version": "frozen-legacy-interaction-fixture",
    })


def _legacy_interaction_graph(**kwargs):
    from winpilot.orchestrator.graph_compiler import task_contract_to_arp

    contract = _legacy_interaction_contract(**kwargs)
    return contract, task_contract_to_arp(contract)


def _legacy_stage(contract, stage_type: str):
    return next(item for item in contract.stages if item.type.value == stage_type)


def test_persistent_stage_graph_accepts_post_action_reobserve_but_not_idle_loop():
    from winpilot.tasking.interaction import InteractionAction
    from winpilot.tasking.interaction_stage_runtime import (
        _persistent_plan_has_causal_continuation,
    )

    action = InteractionAction(
        action="click",
        frame_x=400,
        frame_y=400,
        target_region=[350, 350, 450, 450],
    )
    assert _persistent_plan_has_causal_continuation(
        _decision(action=action, continuation="reobserve"),
    ) is True
    invalid_idle = _decision(action=None, continuation="finish").model_copy(
        update={"continuation": "reobserve"},
    )
    assert _persistent_plan_has_causal_continuation(invalid_idle) is False


def test_compiler_routes_visual_interaction_to_one_model_owned_desktop_stage():
    from winpilot.orchestrator.contracts import validate_graph
    from winpilot.tasking.compiler import TaskContractCompiler

    task = "drag the visible item to its indicated destination"
    contract, graph = TaskContractCompiler().compile_graph(task, cold_start=True)

    assert [stage.type.value for stage in contract.stages] == [
        "ExecuteDesktopTask",
    ]
    assert contract.session is None
    assert contract.task == task
    assert contract.stages[0].parameters["original_task"] == task
    validation = validate_graph(graph)
    assert validation.valid, validation.errors


def test_frozen_legacy_contract_preserves_serialized_budgets():
    contract, graph = _legacy_interaction_graph(max_steps=6)

    assert contract.format == "task-contract/1"
    assert contract.session is not None
    assert contract.session.requested_max_steps == 6
    assert contract.session.max_semantic_turns == 3
    assert contract.session.max_replans == 1
    assert contract.session.max_model_calls == 6
    assert graph.metadata["interaction_budget"] == {
        "requested_max_steps": 6,
        "max_semantic_turns": 3,
        "max_replans": 1,
        "max_model_calls": 6,
        "external_change_timeout_s": 300.0,
    }
    assert graph.policy.max_node_visits == 9
    assert all(
        int(stage.parameters["max_steps"]) <= 6
        for stage in contract.stages
    )


def test_model_and_semantic_turn_budgets_fail_with_precise_dimensions():
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine

    contract = _legacy_interaction_contract(max_steps=1)
    assert contract.session is not None
    engine = InteractionStageEngine(stage_output=_stage_output)
    state = {}

    for _ in range(contract.session.max_model_calls):
        engine._reserve_model_call(state, contract, stage="test")
    with pytest.raises(RuntimeError) as model_exhausted:
        engine._reserve_model_call(state, contract, stage="test")
    assert getattr(model_exhausted.value, "failure_type", "") == "interaction_budget_exhausted"
    assert model_exhausted.value.stage_details["budget_dimension"] == "model_calls"
    assert state["budget"]["model_calls_used"] == contract.session.max_model_calls

    state["turn_index"] = contract.session.max_semantic_turns
    with pytest.raises(RuntimeError) as turn_exhausted:
        engine._assert_mutation_budget(state, contract)
    assert getattr(turn_exhausted.value, "failure_type", "") == "interaction_budget_exhausted"
    assert turn_exhausted.value.stage_details["budget_dimension"] == "semantic_turns"


def test_frozen_legacy_graph_routes_recovery_through_fresh_observe():
    from winpilot.orchestrator.contracts import validate_graph

    contract, graph = _legacy_interaction_graph(max_steps=12)
    observe = next(
        stage.id for stage in contract.stages
        if stage.type.value == "ObserveScene"
    )
    recoverable = {
        stage.id for stage in contract.stages
        if stage.type.value in {
            "ObserveScene", "InferInteraction", "PerformTurn",
            "VerifyLocalEffect",
        }
    }
    routed = {
        edge.source for edge in graph.edges
        if edge.branch == "recover_reobserve" and edge.target == observe
    }
    assert routed == recoverable
    validation = validate_graph(graph)
    assert validation.valid, validation.errors


def test_frozen_persistent_contract_keeps_wait_nonterminal():
    from winpilot.orchestrator.contracts import validate_graph

    contract, graph = _legacy_interaction_graph(persistent=True)
    assert contract.session is not None
    assert contract.session.lifecycle == "persistent"
    wait_stage = _legacy_stage(contract, "WaitForExternalChange")
    verify_stage = _legacy_stage(contract, "VerifyLocalEffect")
    infer = _legacy_stage(contract, "InferInteraction").id
    assert {
        (edge.branch, edge.target)
        for edge in graph.edges
        if edge.source == wait_stage.id
    } == {("changed", infer), ("complete", "end")}
    assert wait_stage.obligations == []
    assert "interaction.external_watch_armed" not in contract.final_conditions
    assert "interaction.task_completed" in contract.final_conditions
    terminal = next(
        item for item in contract.obligations
        if item.id == "interaction.task_completed"
    )
    assert terminal.stage_id == verify_stage.id
    assert terminal.evidence_predicate == "interaction.task_completed"
    validation = validate_graph(graph)
    assert validation.valid, validation.errors


@pytest.mark.parametrize(
    ("record_terminal", "expected_status"),
    [(True, "done"), (False, "failed")],
)
def test_persistent_first_turn_end_requires_exact_terminal_evidence(
    record_terminal,
    expected_status,
):
    from winpilot.orchestrator.contracts import ArpNodeKind
    from winpilot.orchestrator.runtime import ArpExecution, NodeResult
    from winpilot.tasking.contracts import StageType, TaskStage
    from winpilot.tasking.evidence import EvidenceRegistry

    contract, graph = _legacy_interaction_graph(persistent=True)
    wait_stage = _legacy_stage(contract, "WaitForExternalChange")

    def stage_handler(node, context, _stop_event):
        registry = context["runtime_state"].get("evidence_registry")
        if not isinstance(registry, EvidenceRegistry):
            registry = EvidenceRegistry(context["run_id"])
            context["runtime_state"]["evidence_registry"] = registry
        stage = TaskStage.model_validate(node.config["stage"])
        for obligation in stage.obligations:
            if obligation.id == "interaction.task_completed" and not record_terminal:
                continue
            registry.record(
                obligation.evidence_predicate,
                provider=obligation.evidence_providers[0].id,
                verified=True,
                stage_id=stage.id,
            )
        return NodeResult(
            branch="complete" if stage.type == StageType.VERIFY_LOCAL_EFFECT else "next",
        )

    result = ArpExecution(
        graph,
        {ArpNodeKind.TASK_STAGE: stage_handler},
        run_id=f"persistent-terminal-{record_terminal}",
    ).run()

    assert result["status"] == expected_status
    visited = {item["node_id"] for item in result["history"]}
    assert wait_stage.id not in visited
    if record_terminal:
        assert result["completion_contract"]["ok"] is True
    else:
        assert result["completion_contract"]["ok"] is False
        assert {
            item["obligation_id"]
            for item in result["completion_contract"]["missing"]
        } == {"interaction.task_completed"}


def test_interaction_complete_branch_reaches_later_typed_stages():
    from winpilot.orchestrator.contracts import validate_graph

    contract, graph = _legacy_interaction_graph(include_capture=True)
    verify = _legacy_stage(contract, "VerifyLocalEffect").id
    capture = _legacy_stage(contract, "CaptureArtifact").id
    assert any(
        edge.source == verify
        and edge.branch == "complete"
        and edge.target == capture
        for edge in graph.edges
    )
    assert any(edge.source == capture and edge.target == "end" for edge in graph.edges)
    validation = validate_graph(graph)
    assert validation.valid, validation.errors


def test_role_and_turn_ownership_cannot_authorize_the_other_actor_action():
    from pydantic import ValidationError
    from winpilot.tasking.interaction import InteractionAction, InteractionDecision

    action = InteractionAction(
        action="click",
        frame_x=400,
        frame_y=400,
        expected_effect_region=[350, 350, 450, 450],
    )
    payload = _decision(action=action).model_dump(mode="python")
    payload.update({"turn_owner": "user", "agent_should_act": True})
    with pytest.raises(ValidationError, match="turn_owner=agent"):
        InteractionDecision.model_validate(payload)


def test_visual_planner_derives_external_watch_from_typed_continuation(
    monkeypatch,
    tmp_path,
):
    from winpilot.tasking import interaction

    raw = _decision(continuation="finish").model_dump(mode="python")
    raw.pop("frame_id", None)
    raw.pop("observation_artifact_id", None)
    # Simulate a provider returning a stale/redundant compatibility flag.  The
    # typed continuation is the single model-owned source of truth.
    raw["wait_for_external_change"] = True
    monkeypatch.setattr(
        interaction.vlm,
        "structured_json_from_image",
        lambda *_args, **_kwargs: dict(raw),
    )

    image = tmp_path / "frame.png"
    image.write_bytes(b"frame")
    decision = interaction.plan_interaction(
        image,
        task="perform the visible bounded interaction",
        intent={},
        frame_id=7,
        observation_artifact_id="art-current-frame",
    )

    assert decision.continuation == "finish"
    assert decision.wait_for_external_change is False
    assert decision.frame_id == 7
    assert decision.observation_artifact_id == "art-current-frame"


def test_visual_planner_schema_rejection_exposes_structured_bounded_feedback(
    monkeypatch,
    tmp_path,
):
    from winpilot.tasking import interaction
    from winpilot.tasking.interaction import (
        InteractionAction,
        InteractionPlanOutputError,
    )

    raw = _decision(action=InteractionAction(
        action="click",
        frame_x=400,
        frame_y=400,
        target_region=[350, 350, 450, 450],
    )).model_dump(mode="python")
    raw.pop("frame_id", None)
    raw.pop("observation_artifact_id", None)
    raw["actions"][0]["unknown_provider_field"] = "x" * 1000
    monkeypatch.setattr(
        interaction.vlm,
        "structured_json_from_image",
        lambda *_args, **_kwargs: raw,
    )
    image = tmp_path / "invalid-plan-frame.png"
    image.write_bytes(b"frame")

    with pytest.raises(InteractionPlanOutputError) as rejected:
        interaction.plan_interaction(
            image,
            task="perform the visible bounded interaction",
            intent={},
            frame_id=12,
            observation_artifact_id="art-invalid-plan",
        )

    error = rejected.value
    assert error.code == "schema_validation_failed"
    assert error.raw_payload is not None
    assert error.validation_errors == ({
        "location": ["actions", "0", "unknown_provider_field"],
        "type": "extra_forbidden",
        "message": "Extra inputs are not permitted",
    },)
    feedback = error.correction_feedback(max_payload_chars=256)
    assert feedback["previous_payload_json"].endswith("...[truncated]")
    assert len(feedback["previous_payload_json"]) <= 270


def test_shared_session_requires_explicit_current_frame_affordance(
    monkeypatch,
    tmp_path,
):
    from winpilot.tasking import interaction
    from winpilot.tasking.interaction import (
        InteractionAction,
        InteractionPlanOutputError,
    )

    raw = _decision(action=InteractionAction(
        action="click",
        frame_x=400,
        frame_y=400,
        target_region=[350, 350, 450, 450],
    )).model_dump(mode="python")
    raw.pop("frame_id", None)
    raw.pop("observation_artifact_id", None)
    monkeypatch.setattr(
        interaction.vlm,
        "structured_json_from_image",
        lambda *_args, **_kwargs: dict(raw),
    )
    image = tmp_path / "shared-frame.png"
    image.write_bytes(b"frame")

    with pytest.raises(InteractionPlanOutputError) as missing:
        interaction.plan_interaction(
            image,
            task="continue the visible shared session",
            intent={"mode": "shared_visual_session"},
            frame_id=1,
            observation_artifact_id="shared-artifact",
        )
    assert missing.value.code == "interaction_mechanism_missing"

    raw["actions"][0].update({
        "interaction_mechanism": "click_to_place",
        "affordance_evidence": "the current frame explicitly labels this as click-to-place",
    })
    decision = interaction.plan_interaction(
        image,
        task="continue the visible shared session",
        intent={"mode": "shared_visual_session"},
        frame_id=2,
        observation_artifact_id="shared-artifact-2",
    )
    assert decision.actions[0].interaction_mechanism == "click_to_place"


def test_visual_planner_binds_selected_current_pixel_candidate(
    monkeypatch,
    tmp_path,
):
    from winpilot.tasking import interaction
    from winpilot.tasking.interaction import InteractionAction

    raw = _decision(action=InteractionAction(
        action="click",
        frame_x=650,
        frame_y=650,
        target_region=[550, 550, 750, 750],
        target_anchor=[650, 650],
        grounding_mode="object_anchor",
        interaction_mechanism="point_selection",
        affordance_evidence="the chosen visible bounded object",
        expected_effect_region=[550, 550, 750, 750],
        effect_region_binding="target_region",
    )).model_dump(mode="python")
    raw["actions"][0]["target_candidate_id"] = "visible-region-07"
    raw["expected_effect"].update({
        "region": [300, 300, 400, 400],
        "region_binding": "target_region",
        "binding_action_index": 0,
    })
    raw.pop("frame_id", None)
    raw.pop("observation_artifact_id", None)
    inventory = [{
        "candidate_id": "visible-region-07",
        "bounds": [600.0, 600.0, 700.0, 700.0],
        "center": [650.0, 650.0],
        "shape": "rectangular_boundary",
        "pixel_size": [80, 80],
        "peer_group": "peer-set-01",
        "peer_count": 9,
        "row_index": 2,
        "column_index": 3,
    }]
    monkeypatch.setattr(
        interaction, "inventory_visible_regions", lambda _path: inventory,
    )
    monkeypatch.setattr(
        interaction.vlm,
        "structured_json_from_image",
        lambda *_args, **_kwargs: raw,
    )
    image = tmp_path / "candidate-frame.png"
    image.write_bytes(b"frame")

    decision = interaction.plan_interaction(
        image,
        task="activate the semantically selected visible region",
        intent={},
        frame_id=21,
        observation_artifact_id="candidate-frame-artifact",
        primary_action_modality="click",
    )

    action = decision.actions[0]
    assert action.target_candidate_id == "visible-region-07"
    assert action.target_region == [600.0, 600.0, 700.0, 700.0]
    assert action.target_anchor == [650.0, 650.0]
    assert [action.frame_x, action.frame_y] == [650.0, 650.0]
    assert action.expected_effect_region == action.target_region
    assert decision.expected_effect.region == action.target_region
    assert decision.region_inventory == tuple(inventory)
    assert any(
        "target_region:bound->visible-region-07" in item
        for item in decision.canonicalization_repairs
    )


def test_infer_from_scene_keeps_candidates_for_declared_spatial_modalities(
    monkeypatch,
    tmp_path,
):
    from winpilot.tasking import interaction
    from winpilot.tasking.interaction import InteractionAction

    raw = _decision(action=InteractionAction(
        action="click",
        frame_x=600,
        frame_y=330,
        target_region=[550, 280, 650, 380],
        target_anchor=[600, 330],
        grounding_mode="object_anchor",
        interaction_mechanism="point_selection",
        affordance_evidence="the semantically selected current-frame object",
        expected_effect_region=[550, 280, 650, 380],
        effect_region_binding="target_region",
    )).model_dump(mode="python")
    raw["actions"][0]["target_candidate_id"] = "visible-region-03"
    raw.pop("frame_id", None)
    raw.pop("observation_artifact_id", None)
    inventory = [{
        "candidate_id": "visible-region-03",
        "bounds": [580.0, 300.0, 620.0, 355.0],
        "center": [600.0, 327.5],
        "shape": "closed_shape",
        "pixel_size": [52, 50],
        "peer_group": "peer-set-01",
        "peer_count": 12,
        "row_index": 1,
        "column_index": 5,
    }]
    monkeypatch.setattr(
        interaction, "inventory_visible_regions", lambda *_args, **_kwargs: inventory,
    )
    monkeypatch.setattr(
        interaction, "inventory_visible_partition_regions", lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        interaction.vlm, "structured_json_from_image",
        lambda *_args, **_kwargs: dict(raw),
    )
    image = tmp_path / "inferred-spatial-frame.png"
    image.write_bytes(b"frame")

    decision = interaction.plan_interaction(
        image,
        task="perform the selected visible interaction",
        intent={
            "mode": "direct_visual_manipulation",
            "session_mode": "bounded",
            "action_modalities": ["click", "type"],
        },
        frame_id=22,
        observation_artifact_id="inferred-spatial-artifact",
        primary_action_modality="infer_from_scene",
    )

    assert decision.actions[0].target_candidate_id == "visible-region-03"
    assert decision.actions[0].target_region == [580.0, 300.0, 620.0, 355.0]
    assert decision.region_inventory == tuple(inventory)


def test_partition_candidate_binds_locator_and_geometry_as_one_contract():
    from winpilot.tasking.interaction import _bind_visible_region_candidates

    payload = {
        "actions": [{
            "action": "draw_strokes",
            "target_candidate_id": "partition-region-01-r01-c03",
            "target_region": [560, 260, 740, 440],
            "target_anchor": [650, 350],
            "expected_effect_region": [560, 260, 740, 440],
            "effect_region_binding": "target_region",
        }],
        "expected_effect": {
            "region": [560, 260, 740, 440],
            "region_binding": "target_region",
            "binding_action_index": 0,
        },
    }
    inventory = [{
        "candidate_id": "partition-region-01-r01-c03",
        "bounds": [600.0, 300.0, 700.0, 400.0],
        "center": [650.0, 350.0],
        "shape": "open_partition_cell",
        "partition_locator": {
            "layout": "repeated_partition", "row": 1, "column": 3,
        },
    }]
    repairs = []

    bound = _bind_visible_region_candidates(
        payload, inventory, repairs=repairs,
    )

    action = bound["actions"][0]
    assert action["target_region"] == [600.0, 300.0, 700.0, 400.0]
    assert action["target_anchor"] == [650.0, 350.0]
    assert action["partition_locator"] == {
        "layout": "repeated_partition", "row": 1, "column": 3,
    }
    assert action["expected_effect_region"] == action["target_region"]
    assert bound["expected_effect"]["region"] == action["target_region"]
    assert "actions[0].partition_locator:candidate_bound" in repairs


def test_unique_partition_locator_binds_current_pixel_candidate_without_id():
    from winpilot.tasking.interaction import _bind_visible_region_candidates

    payload = {
        "actions": [{
            "action": "draw_strokes",
            "target_candidate_id": "",
            # Coarse model geometry is deliberately poor.  The unique typed
            # row/column locator is a stronger current-frame topology proof.
            "target_region": [700, 700, 800, 800],
            "target_anchor": [750, 750],
            "partition_locator": {
                "layout": "repeated_partition", "row": 2, "column": 1,
            },
            "expected_effect_region": [],
            "effect_region_binding": "target_region",
        }],
    }
    inventory = [
        {
            "candidate_id": "partition-region-01-r01-c01",
            "bounds": [100.0, 100.0, 200.0, 200.0],
            "partition_locator": {
                "layout": "repeated_partition", "row": 1, "column": 1,
            },
        },
        {
            "candidate_id": "partition-region-01-r02-c01",
            "bounds": [100.0, 200.0, 200.0, 300.0],
            "partition_locator": {
                "layout": "repeated_partition", "row": 2, "column": 1,
            },
        },
    ]
    repairs = []

    bound = _bind_visible_region_candidates(
        payload, inventory, repairs=repairs,
    )

    action = bound["actions"][0]
    assert action["target_candidate_id"] == "partition-region-01-r02-c01"
    assert action["target_region"] == [100.0, 200.0, 200.0, 300.0]
    assert action["target_anchor"] == [150.0, 250.0]
    assert (
        "actions[0].target_candidate_id:unique_partition_locator" in repairs
    )


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (
            {
                "action": "click",
                "target_candidate_id": "target-region",
                "target_region": [500, 300, 700, 500],
                "target_anchor": None,
                "frame_x": None,
                "frame_y": None,
            },
            {"frame_x": 600.0, "frame_y": 400.0},
        ),
        (
            {
                "action": "drag",
                "source_candidate_id": "source-region",
                "target_candidate_id": "target-region",
                "source_region": [100, 300, 200, 400],
                "target_region": [500, 300, 700, 500],
                "target_anchor": None,
                "frame_x1": None,
                "frame_y1": None,
                "frame_x2": None,
                "frame_y2": None,
            },
            {
                "frame_x1": 150.0,
                "frame_y1": 350.0,
                "frame_x2": 600.0,
                "frame_y2": 400.0,
            },
        ),
    ],
)
def test_candidate_binding_resolves_null_optional_points_from_current_pixels(
    action,
    expected,
):
    from winpilot.tasking.interaction import _bind_visible_region_candidates

    inventory = [
        {
            "candidate_id": "source-region",
            "bounds": [100.0, 300.0, 200.0, 400.0],
            "center": [150.0, 350.0],
        },
        {
            "candidate_id": "target-region",
            "bounds": [500.0, 300.0, 700.0, 500.0],
            "center": [600.0, 400.0],
        },
    ]

    bound = _bind_visible_region_candidates(
        {"actions": [action]},
        inventory,
        repairs=[],
    )

    for key, value in expected.items():
        assert bound["actions"][0][key] == value


def test_region_inventory_does_not_offer_frame_spanning_structural_band():
    from winpilot.tasking.interaction import _merge_current_region_inventories

    bounded = [
        {
            "candidate_id": "real-object",
            "bounds": [120.0, 180.0, 240.0, 330.0],
            "center": [180.0, 255.0],
            "shape": "closed_shape",
        },
        {
            "candidate_id": "spanning-band",
            "bounds": [60.0, 760.0, 940.0, 825.0],
            "center": [500.0, 792.5],
            "shape": "rectangular_boundary",
        },
    ]

    merged = _merge_current_region_inventories(bounded, [])

    assert [item["candidate_id"] for item in merged] == ["real-object"]


def test_candidate_binding_rejects_semantically_distant_pixel_candidate():
    from winpilot.tasking.interaction import (
        InteractionPlanOutputError,
        _bind_visible_region_candidates,
    )

    payload = {
        "actions": [{
            "action": "click",
            "target_candidate_id": "visible-region-01",
            "target_region": [100, 100, 220, 220],
            "target_anchor": [160, 160],
        }],
    }
    inventory = [{
        "candidate_id": "visible-region-01",
        "bounds": [700.0, 700.0, 800.0, 800.0],
        "center": [750.0, 750.0],
    }]

    with pytest.raises(InteractionPlanOutputError) as raised:
        _bind_visible_region_candidates(payload, inventory, repairs=[])

    assert raised.value.code == "interaction_region_candidate_semantic_conflict"


def test_source_inside_target_drag_rejects_one_candidate_for_both_roles():
    from winpilot.tasking.interaction import (
        InteractionPlanOutputError,
        _bind_visible_region_candidates,
    )

    payload = {
        "actions": [{
            "action": "drag",
            "placement_requirement": "source_inside_target",
            "source_candidate_id": "visible-region-01",
            "target_candidate_id": "visible-region-01",
            "source_region": [100, 100, 200, 200],
            "target_region": [100, 100, 200, 200],
        }],
    }
    inventory = [{
        "candidate_id": "visible-region-01",
        "bounds": [100.0, 100.0, 200.0, 200.0],
        "center": [150.0, 150.0],
    }]

    with pytest.raises(InteractionPlanOutputError) as raised:
        _bind_visible_region_candidates(payload, inventory, repairs=[])

    assert raised.value.code == "interaction_region_candidate_role_conflict"


def test_visual_planner_centers_complete_source_in_target_deterministically(
    monkeypatch,
    tmp_path,
):
    from winpilot.tasking import interaction
    from winpilot.tasking.interaction import InteractionAction

    action = InteractionAction(
        action="drag",
        frame_x1=250,
        frame_y1=250,
        frame_x2=660,
        frame_y2=660,
        source_region=[200, 200, 300, 300],
        target_region=[600, 600, 720, 720],
        placement_requirement="source_inside_target",
        expected_effect_region=[600, 600, 720, 720],
    )
    raw = _decision(action=action).model_dump(mode="python")
    raw.pop("frame_id", None)
    raw.pop("observation_artifact_id", None)
    # The semantic regions are sound, but the provider proposes a boundary
    # endpoint that would leave part of the source outside the destination.
    raw["actions"][0]["frame_x2"] = 620
    raw["actions"][0]["frame_y2"] = 620
    monkeypatch.setattr(
        interaction.vlm,
        "structured_json_from_image",
        lambda *_args, **_kwargs: dict(raw),
    )

    image = tmp_path / "frame.png"
    image.write_bytes(b"frame")
    decision = interaction.plan_interaction(
        image,
        task="move the complete visible source into the visible destination",
        intent={},
        frame_id=8,
        observation_artifact_id="art-current-frame-2",
    )

    planned = decision.actions[0]
    assert planned.frame_x2 == pytest.approx(660.0)
    assert planned.frame_y2 == pytest.approx(660.0)


def test_visual_planner_recomputes_drag_after_current_pixel_candidate_binding(
    monkeypatch,
    tmp_path,
):
    from winpilot.tasking import interaction
    from winpilot.tasking.interaction import InteractionAction

    raw = _decision(action=InteractionAction(
        action="drag",
        frame_x1=250,
        frame_y1=250,
        frame_x2=660,
        frame_y2=660,
        source_region=[200, 200, 300, 300],
        target_region=[600, 600, 720, 720],
        placement_requirement="source_inside_target",
        expected_effect_region=[600, 600, 720, 720],
        effect_region_binding="target_region",
    )).model_dump(mode="python")
    raw.pop("frame_id", None)
    raw.pop("observation_artifact_id", None)
    raw["actions"][0].update({
        "source_candidate_id": "visible-region-01",
        "target_candidate_id": "visible-region-02",
    })
    raw["expected_effect"].update({
        "region_binding": "target_region",
        "binding_action_index": 0,
    })
    inventory = [
        {
            "candidate_id": "visible-region-01",
            "bounds": [220.0, 220.0, 320.0, 320.0],
            "center": [270.0, 270.0],
            "shape": "closed_shape",
            "pixel_size": [90, 90],
        },
        {
            "candidate_id": "visible-region-02",
            "bounds": [500.0, 500.0, 800.0, 800.0],
            "center": [650.0, 650.0],
            "shape": "fragmented_boundary",
            "pixel_size": [270, 270],
        },
    ]
    monkeypatch.setattr(
        interaction, "inventory_visible_regions", lambda *_args, **_kwargs: inventory,
    )
    monkeypatch.setattr(
        interaction, "inventory_visible_partition_regions", lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        interaction.vlm,
        "structured_json_from_image",
        lambda *_args, **_kwargs: dict(raw),
    )
    image = tmp_path / "candidate-drag-frame.png"
    image.write_bytes(b"frame")

    decision = interaction.plan_interaction(
        image,
        task="move the complete visible object into its visible destination",
        intent={"action_modalities": ["drag"]},
        frame_id=9,
        observation_artifact_id="art-candidate-drag",
        primary_action_modality="drag",
    )

    planned = decision.actions[0]
    assert planned.source_region == [220.0, 220.0, 320.0, 320.0]
    assert planned.target_region == [500.0, 500.0, 800.0, 800.0]
    # The start is 20 units above/left of the newly bound source centre, so
    # the maximum-margin drop preserves that grab offset around target centre.
    assert planned.frame_x2 == pytest.approx(630.0)
    assert planned.frame_y2 == pytest.approx(630.0)
    assert planned.expected_effect_region == planned.target_region


def test_visual_planner_canonicalizes_minor_provider_schema_drift_once(
    monkeypatch,
    tmp_path,
):
    from winpilot.tasking import interaction

    calls = []
    raw = {
        "sceneSummary": "one visible bounded control",
        "mode": "direct_visual_manipulation",
        "interactionRegion": {
            "left": 100, "top": 100, "right": 900, "bottom": 900,
        },
        "roles": None,
        "rules": "operate only on the visible target",
        "confidence": 0.93,
        "ambiguity": None,
        "agentShouldAct": True,
        "actions": {
            "action": "double-click",
            "frame_x": 400,
            "frame_y": 400,
            "target_region": {
                "left": 350, "top": 350, "right": 500, "bottom": 500,
            },
            "expected_effect_region": [340, 340, 520, 520],
        },
        "expectedEffect": {
            "description": "",
            "region": [0, 0, 0, 0],
        },
        "continuation": "complete",
    }

    def _structured(*_args, **_kwargs):
        calls.append(True)
        return dict(raw)

    monkeypatch.setattr(interaction.vlm, "structured_json_from_image", _structured)
    image = tmp_path / "frame.png"
    image.write_bytes(b"frame")

    decision = interaction.plan_interaction(
        image,
        task="perform the visible bounded interaction",
        intent={},
        frame_id=11,
        observation_artifact_id="art-11",
    )

    assert len(calls) == 1
    assert decision.agent_should_act is True
    assert decision.turn_owner == "agent"
    assert decision.actions[0].action == "double_click"
    assert decision.expected_effect.description
    assert decision.expected_effect.region == [340.0, 340.0, 520.0, 520.0]
    assert decision.continuation == "finish"
    assert "continuation:normalized_alias" in decision.canonicalization_repairs
    assert "expected_effect.description:derived_from_grounded_action" in (
        decision.canonicalization_repairs
    )


def test_visual_planner_normalizes_only_omitted_stable_batch_dependencies(
    monkeypatch,
    tmp_path,
):
    from winpilot.tasking import interaction
    from winpilot.tasking.interaction import InteractionAction

    actions = [
        InteractionAction(
            action="click",
            frame_x=x,
            frame_y=y,
            target_region=[x - 35, y - 35, x + 35, y + 35],
            target_anchor=[x, y],
            grounding_mode="object_anchor",
            interaction_mechanism="point_selection",
            affordance_evidence=f"visible target {index}",
            expected_effect_region=[x - 35, y - 35, x + 35, y + 35],
        )
        for index, (x, y) in enumerate(
            ((320.0, 350.0), (500.0, 500.0), (680.0, 650.0)),
            start=1,
        )
    ]
    raw = _decision(action=actions[0], continuation="finish").model_dump(
        mode="python"
    )
    raw["actions"] = [item.model_dump(mode="python") for item in actions]
    for item in raw["actions"]:
        item.pop("requires_fresh_frame_after", None)
    raw["expected_effect"].update({
        "description": "all three visible targets register in order",
        "region": [280, 300, 720, 700],
        "region_binding": "independent_region",
        "binding_action_index": -1,
    })
    raw.pop("frame_id", None)
    raw.pop("observation_artifact_id", None)
    monkeypatch.setattr(
        interaction.vlm,
        "structured_json_from_image",
        lambda *_args, **_kwargs: raw,
    )
    image = tmp_path / "stable-batch.png"
    image.write_bytes(b"frame")

    decision = interaction.plan_interaction(
        image,
        task="activate the three simultaneously visible stable targets in order",
        intent={},
        frame_id=12,
        observation_artifact_id="stable-batch-frame",
    )

    assert [
        item.requires_fresh_frame_after for item in decision.actions
    ] == [False, False, True]
    assert (
        "actions[0].requires_fresh_frame_after:derived_stable_batch"
        in decision.canonicalization_repairs
    )
    assert (
        "actions[1].requires_fresh_frame_after:derived_stable_batch"
        in decision.canonicalization_repairs
    )

    # An explicit dependency remains semantic and must never be silently
    # downgraded merely to force a larger transaction. The planner compiler
    # keeps only the safe prefix and routes it through a fresh frame.
    raw["actions"][0]["requires_fresh_frame_after"] = True
    bounded = interaction.plan_interaction(
        image,
        task="activate the visible targets",
        intent={},
        frame_id=13,
        observation_artifact_id="explicit-dependency-frame",
    )
    assert len(bounded.actions) == 1
    assert len(bounded.deferred_actions) == 2
    assert [item.frame_x for item in bounded.deferred_actions] == [500.0, 680.0]
    assert bounded.program_continuation == "finish"
    assert bounded.actions[0].requires_fresh_frame_after is True
    assert bounded.continuation == "reobserve"
    assert bounded.expected_effect.region == actions[0].expected_effect_region
    assert (
        "actions:truncated_at_explicit_fresh_frame_dependency"
        in bounded.canonicalization_repairs
    )


def test_visual_planner_replans_stale_release_after_held_pointer_frame_boundary(
    monkeypatch, tmp_path,
):
    from winpilot.tasking import interaction

    raw = {
        "scene_summary": "the held pointer reached the visible checkpoint",
        "mode": "direct_visual_manipulation",
        "turn_owner": "agent",
        "agent_should_act": True,
        "surface_region": [0, 0, 1000, 1000],
        "interaction_region": [0, 0, 1000, 1000],
        "roles": {},
        "rules": ["keep holding until a later frame proves release is valid"],
        "confidence": 0.95,
        "ambiguity": [],
        "actions": [
            {
                "action": "move_mouse",
                "target_region": [700, 200, 900, 400],
                "target_anchor": [800, 300],
                "grounding_mode": "object_anchor",
                "interaction_mechanism": "pointer_motion",
                "affordance_evidence": "visible destination in this frame",
                "requires_fresh_frame_after": True,
            },
            {
                "action": "mouse_up",
                "interaction_mechanism": "press_hold",
                "affordance_evidence": (
                    "release only if the next authoritative frame still proves it"
                ),
                "requires_fresh_frame_after": False,
            },
        ],
        "expected_effect": {
            "description": "the held pointer moves to the visible destination",
            "region": [700, 200, 900, 400],
            "region_binding": "target_region",
            "binding_action_index": 0,
            "minimum_change_ratio": 0.001,
            "semantic_verification_required": True,
        },
        "continuation": "finish",
    }
    monkeypatch.setattr(
        interaction.vlm,
        "structured_json_from_image",
        lambda *_args, **_kwargs: raw,
    )
    image = tmp_path / "held-pointer-frame.png"
    image.write_bytes(b"frame")

    decision = interaction.plan_interaction(
        image,
        task="continue the currently held pointer using each new visual frame",
        intent={},
        frame_id=14,
        observation_artifact_id="held-pointer-frame",
        prior_state={"pointer_hold": {"active": True, "button": "left"}},
        primary_action_modality="drag",
    )

    assert [item.action for item in decision.actions] == ["move_mouse"]
    assert decision.pointer_hold_before == "left"
    assert decision.pointer_hold_after == "left"
    assert decision.continuation == "reobserve"
    assert decision.deferred_actions == ()
    assert decision.program_continuation == "reobserve"
    assert (
        "deferred_actions:discarded_at_held_pointer_frame_boundary"
        in decision.canonicalization_repairs
    )


def test_visual_planner_enforces_immutable_contract_roles(monkeypatch, tmp_path):
    from winpilot.tasking import interaction
    from winpilot.tasking.interaction import (
        InteractionAction,
        InteractionPlanOutputError,
    )

    image = tmp_path / "role-frame.png"
    image.write_bytes(b"frame")
    raw = _decision(action=InteractionAction(
        action="click",
        frame_x=400,
        frame_y=400,
    )).model_dump(mode="python")
    raw.pop("frame_id", None)
    raw.pop("observation_artifact_id", None)
    raw["roles"] = {"agent": {"assignment": "black"}}
    monkeypatch.setattr(
        interaction.vlm,
        "structured_json_from_image",
        lambda *_args, **_kwargs: dict(raw),
    )

    with pytest.raises(InteractionPlanOutputError) as conflict:
        interaction.plan_interaction(
            image,
            task="perform the visible turn",
            intent={},
            frame_id=7,
            observation_artifact_id="role-frame-7",
            immutable_roles={"agent": {"assignment": "red"}},
        )
    assert conflict.value.code == "contract_role_conflict"

    raw["roles"] = {"agent": {}}
    decision = interaction.plan_interaction(
        image,
        task="perform the visible turn",
        intent={},
        frame_id=8,
        observation_artifact_id="role-frame-8",
        immutable_roles={"agent": {"assignment": "red"}},
    )
    assert decision.roles["agent"]["assignment"] == "red"
    assert "roles.agent.assignment:contract_injected" in (
        decision.canonicalization_repairs
    )


@pytest.mark.parametrize(
    ("primary", "main_action", "setup_action", "expected_code"),
    (
        ("drag", "drag", "hotkey", ""),
        ("type", "type_text", "hotkey", ""),
        ("drag", "click", None, "primary_modality_missing"),
        # A setup key chord is neutral.  It cannot stand in for typed input.
        ("type", "hotkey", None, "primary_modality_missing"),
    ),
)
def test_visual_planner_preserves_primary_modality_while_allowing_neutral_setup(
    monkeypatch,
    tmp_path,
    primary,
    main_action,
    setup_action,
    expected_code,
):
    from winpilot.tasking import interaction
    from winpilot.tasking.interaction import (
        InteractionAction,
        InteractionPlanOutputError,
    )

    def action(name):
        if name == "drag":
            return InteractionAction(
                action="drag",
                frame_x1=300,
                frame_y1=400,
                frame_x2=700,
                frame_y2=400,
            )
        if name == "click":
            return InteractionAction(
                action="click", frame_x=400, frame_y=400,
            )
        if name == "type_text":
            return InteractionAction(action="type_text", text="visible input")
        if name == "hotkey":
            return InteractionAction(action="hotkey", keys=["ctrl", "a"])
        raise AssertionError(name)

    actions = [action(main_action)]
    if setup_action:
        actions.insert(0, action(setup_action))
    raw = _decision(action=actions[-1]).model_dump(mode="python")
    raw["actions"] = [item.model_dump(mode="python") for item in actions]
    raw.pop("frame_id", None)
    raw.pop("observation_artifact_id", None)
    monkeypatch.setattr(
        interaction.vlm,
        "structured_json_from_image",
        lambda *_args, **_kwargs: dict(raw),
    )
    image = tmp_path / f"primary-{primary}-{main_action}.png"
    image.write_bytes(b"frame")

    if expected_code:
        with pytest.raises(InteractionPlanOutputError) as rejected:
            interaction.plan_interaction(
                image,
                task="perform the requested interaction",
                intent={},
                frame_id=9,
                observation_artifact_id="primary-frame",
                primary_action_modality=primary,
            )
        assert rejected.value.code == expected_code
    else:
        decision = interaction.plan_interaction(
            image,
            task="perform the requested interaction",
            intent={},
            frame_id=9,
            observation_artifact_id="primary-frame",
            primary_action_modality=primary,
        )
        assert [item.action for item in decision.actions] == [
            item.action for item in actions
        ]


def test_visual_planner_turns_causeless_reobserve_into_explicit_blocked_result(
    monkeypatch,
    tmp_path,
):
    from pydantic import ValidationError
    from winpilot.tasking import interaction

    raw = {
        "scene_summary": "the authoritative frame has no grounded controls",
        "mode": "direct_visual_manipulation",
        "turn_owner": "agent",
        "agent_should_act": False,
        "interaction_region": [0, 0, 1000, 1000],
        "roles": {},
        "rules": [],
        "confidence": 0.99,
        "ambiguity": ["required visible controls are absent"],
        "actions": [],
        "expected_effect": {
            "description": "no action",
            "region": [0, 0, 0, 0],
        },
        "continuation": "reobserve",
    }
    monkeypatch.setattr(
        interaction.vlm,
        "structured_json_from_image",
        lambda *_args, **_kwargs: dict(raw),
    )
    image = tmp_path / "blank.png"
    image.write_bytes(b"frame")

    decision = interaction.plan_interaction(
        image,
        task="perform the currently visible task",
        intent={},
        frame_id=2,
        observation_artifact_id="blank-artifact",
    )

    assert decision.continuation == "blocked"
    assert decision.blocked_reason == "required visible controls are absent"
    assert decision.expected_effect.region == []
    assert "continuation:causeless_reobserve->blocked" in (
        decision.canonicalization_repairs
    )

    direct = decision.model_dump(mode="python")
    direct.update({"continuation": "reobserve", "blocked_reason": ""})
    with pytest.raises(ValidationError, match="reobserve requires a causal action"):
        interaction.InteractionDecision.model_validate(direct)


def test_controller_mapping_rejects_stale_frame_and_out_of_roi_action():
    from pydantic import ValidationError
    from winpilot.perception.surface_binding import InteractionRegion
    from winpilot.tasking.interaction import InteractionAction, controller_actions

    bound = InteractionRegion(
        (200, 200, 800, 800),
        coordinate_space="frame_0_1000",
        label="bound",
        source="test",
    )
    drag = InteractionAction(
        action="drag",
        frame_x1=250,
        frame_y1=300,
        frame_x2=750,
        frame_y2=700,
        expected_effect_region=[300, 300, 500, 500],
    )
    decision = _decision(action=drag, frame_id=9)
    frame_region = {"left": 100, "top": 200, "width": 1000, "height": 500}

    with pytest.raises(ValueError, match="stale authoritative frame"):
        controller_actions(
            decision,
            frame_region=frame_region,
            bound_region=bound,
            frame_epoch=8,
        )

    outside = InteractionAction(
        action="click",
        frame_x=950,
        frame_y=400,
        expected_effect_region=[300, 300, 500, 500],
    )
    # The typed decision boundary now rejects this before physical-coordinate
    # mapping, which is strictly earlier than the Controller's legacy guard.
    with pytest.raises(ValidationError, match="action path escaped"):
        _decision(action=outside, frame_id=9)

    mapped = controller_actions(
        decision,
        frame_region=frame_region,
        bound_region=bound,
        frame_epoch=9,
    )
    assert mapped[0]["action"] == "drag"
    assert (mapped[0]["x1"], mapped[0]["y1"]) == (350, 350)
    assert (mapped[0]["x2"], mapped[0]["y2"]) == (849, 549)


def test_controller_mapping_keeps_press_move_release_in_one_local_transaction():
    from winpilot.perception.surface_binding import InteractionRegion
    from winpilot.tasking.interaction import (
        InteractionAction,
        InteractionDecision,
        controller_actions,
    )

    actions = [
        InteractionAction(action="mouse_down", frame_x=300, frame_y=400),
        InteractionAction(action="move_mouse", frame_x=500, frame_y=500),
        InteractionAction(action="mouse_up", frame_x=700, frame_y=600),
        InteractionAction(action="hold_key", keys=["shift"], duration_ms=200),
    ]
    decision = InteractionDecision(
        scene_summary="one grounded continuous input transaction",
        mode="direct_visual_manipulation",
        turn_owner="agent",
        agent_should_act=True,
        interaction_region=[200, 200, 800, 800],
        roles={"agent": {"role": "operator"}},
        rules=["remain in the bound region"],
        confidence=0.94,
        ambiguity=[],
        actions=actions,
        expected_effect={
            "description": "the bounded target visibly responds",
            "region": [250, 250, 750, 750],
            "minimum_change_ratio": 0.001,
            "semantic_verification_required": True,
        },
        continuation="finish",
        frame_id=9,
        observation_artifact_id="frame-9",
    )
    bound = InteractionRegion(
        (200, 200, 800, 800),
        coordinate_space="frame_0_1000",
        label="bound",
        source="test",
    )

    mapped = controller_actions(
        decision,
        frame_region={"left": 100, "top": 200, "width": 1000, "height": 500},
        bound_region=bound,
        frame_epoch=9,
    )

    assert [item["action"] for item in mapped] == [
        "move_mouse", "mouse_down", "move_mouse", "move_mouse", "mouse_up", "hold_key",
    ]
    assert mapped[0]["typed_action"] == "mouse_down_preposition"
    assert mapped[4]["typed_action"] == "mouse_up"
    assert mapped[5]["duration_ms"] == 200


def test_external_turn_perform_stage_makes_zero_controller_calls(monkeypatch, tmp_path):
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine
    import winpilot.tasking.interaction_stage_runtime as runtime_module

    contract = _legacy_interaction_contract(
        persistent=True,
        task="轮到我了，等我操作后你再根据当前界面继续",
    )
    stage = _legacy_stage(contract, "PerformTurn")
    image_path = tmp_path / "external-turn.png"
    _write_frame(image_path, changed_box=(20, 20, 30, 30))
    store = ArtifactStore("run-zero-mutation", tmp_path / "artifacts")
    artifact = store.import_file(
        image_path, purpose="observation", sendable=False,
    )
    decision = _decision(
        action=None,
        turn_owner="user",
        continuation="wait_external",
        frame_id=4,
        observation_artifact_id=artifact.artifact_id,
    )
    context = {
        "run_id": "run-zero-mutation",
        "runtime_state": {"interaction_state": {
            "frame_id": 4,
            "current_artifact_id": artifact.artifact_id,
            "target_binding": {"source": "active_window"},
            "planned_action": decision.model_dump(mode="json"),
        }},
    }

    class ForbiddenToolSession:
        def __init__(self, *args, **kwargs):
            raise AssertionError("external turn must not instantiate a mutation session")

    monkeypatch.setattr(runtime_module, "ToolSession", ForbiddenToolSession)
    engine = InteractionStageEngine(stage_output=_stage_output)
    captures = []
    engine._capture_scene = lambda **_kwargs: captures.append(True) or artifact
    result = engine.execute(
        stage,
        contract,
        context,
        threading.Event(),
        time.monotonic(),
        store=store,
        registry=EvidenceRegistry("run-zero-mutation"),
    )
    assert result.ok is True
    assert captures == [True]
    executed = context["runtime_state"]["interaction_state"]["executed_action"]
    assert executed["deferred"] is True
    assert executed["continuation"] == "wait_external"


@pytest.mark.parametrize("continuation", ["wait_external", "finish"])
def test_no_action_plan_revalidates_fresh_frame_before_wait_or_finish(
    monkeypatch,
    tmp_path,
    continuation,
):
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine
    import winpilot.tasking.interaction_stage_runtime as runtime_module

    baseline_path = tmp_path / f"{continuation}-baseline.png"
    fresh_path = tmp_path / f"{continuation}-fresh.png"
    _write_frame(baseline_path)
    _write_frame(fresh_path, changed_box=(35, 35, 65, 65))
    store = ArtifactStore(f"run-no-action-{continuation}", tmp_path / "artifacts")
    baseline = store.import_file(
        baseline_path, purpose="observation", sendable=False,
    )
    fresh = store.import_file(
        fresh_path, purpose="observation", sendable=False,
    )
    contract = _legacy_interaction_contract(
        persistent=True,
        task="play with me in the current window; I have already started",
    )
    stage = _legacy_stage(contract, "PerformTurn")
    decision = _decision(
        action=None,
        turn_owner="user" if continuation == "wait_external" else "agent",
        continuation=continuation,
        frame_id=4,
        observation_artifact_id=baseline.artifact_id,
    )
    target = {
        "hwnd": 77,
        "title": "generic target",
        "source": "active_window",
        "capture_scope": "target_window",
        "frame_region": {"left": 0, "top": 0, "width": 100, "height": 100},
        "client_region": {"left": 0, "top": 0, "width": 100, "height": 100},
        "client_frame_bounds": [0, 0, 1000, 1000],
    }
    context = {
        "run_id": f"run-no-action-{continuation}",
        "runtime_state": {"interaction_state": {
            "frame_id": 4,
            "current_artifact_id": baseline.artifact_id,
            "target_binding": dict(target),
            "planned_action": decision.model_dump(mode="json"),
        }},
    }

    class ForbiddenToolSession:
        def __init__(self, *args, **kwargs):
            raise AssertionError("stale no-action plan must not dispatch input")

    monkeypatch.setattr(runtime_module, "ToolSession", ForbiddenToolSession)
    engine = InteractionStageEngine(stage_output=_stage_output)

    def capture_fresh(**_kwargs):
        state = context["runtime_state"]["interaction_state"]
        state.update({
            "frame_id": 5,
            "current_artifact_id": fresh.artifact_id,
            "target_binding": dict(target),
            "last_capture_timing": {"capture_elapsed_ms": 3},
        })
        return fresh

    engine._capture_scene = capture_fresh
    result = engine.execute(
        stage,
        contract,
        context,
        threading.Event(),
        time.monotonic(),
        store=store,
        registry=EvidenceRegistry(f"run-no-action-{continuation}"),
    )

    assert result.ok is True
    assert result.branch == "reobserve"
    state = result.output["interaction_state"]
    assert state["status"] == "no_action_plan_stale"
    assert state["planned_action"] is None
    assert state["no_action_revalidation"]["changed"] is True
    assert int(state.get("replan_count") or 0) == 0


@pytest.mark.parametrize(
    ("change_kind", "expected_reason"),
    (
        ("pixels", "authoritative_pixels_changed"),
        ("long_drag_endpoint", "authoritative_pixels_changed"),
        ("geometry", "window_or_client_geometry_changed"),
    ),
)
def test_perform_preflight_recovers_changed_same_hwnd_without_dispatch(
    monkeypatch, tmp_path, change_kind, expected_reason,
):
    """Inference coordinates are never replayed across a fresh-frame drift."""
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.tasking.interaction import InteractionAction
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine
    import winpilot.tasking.interaction_stage_runtime as runtime_module

    baseline_path = tmp_path / "planned.png"
    fresh_path = tmp_path / "fresh.png"
    _write_frame(baseline_path)
    _write_frame(
        fresh_path,
        changed_box=(
            (20, 20, 45, 45) if change_kind == "pixels"
            else (23, 48, 27, 52) if change_kind == "long_drag_endpoint"
            else None
        ),
    )
    store = ArtifactStore(f"run-stale-{change_kind}", tmp_path / "artifacts")
    planned = store.import_file(baseline_path, purpose="observation", sendable=False)
    fresh = store.import_file(fresh_path, purpose="observation", sendable=False)
    action = (
        InteractionAction(
            action="drag",
            frame_x1=250,
            frame_y1=500,
            frame_x2=750,
            frame_y2=500,
            expected_effect_region=[300, 300, 500, 500],
        )
        if change_kind == "long_drag_endpoint"
        else InteractionAction(
            action="click",
            frame_x=400,
            frame_y=400,
            expected_effect_region=[350, 350, 450, 450],
        )
    )
    decision = _decision(
        action=action,
        continuation="reobserve",
        frame_id=4,
        observation_artifact_id=planned.artifact_id,
    )
    contract = _legacy_interaction_contract(
        task="click the visible control in the current window",
    )
    stage = _legacy_stage(contract, "PerformTurn")
    target = {
        "hwnd": 77,
        "title": "generic target",
        "source": "active_window",
        "capture_scope": "target_window",
        "frame_region": {"left": 100, "top": 100, "width": 800, "height": 600},
        "client_region": {"left": 108, "top": 130, "width": 784, "height": 562},
        "client_frame_bounds": [10, 50, 990, 987],
        "foreground_verified": True,
    }
    context = {
        "run_id": f"run-stale-{change_kind}",
        "runtime_state": {"interaction_state": {
            "frame_id": 4,
            "current_artifact_id": planned.artifact_id,
            "baseline_artifact_id": planned.artifact_id,
            "planned_action": decision.model_dump(mode="json"),
            "target_binding": dict(target),
        }},
    }
    fresh_target = dict(target)
    if change_kind == "geometry":
        fresh_target = {
            **target,
            "frame_region": {"left": 130, "top": 100, "width": 820, "height": 600},
            "client_region": {"left": 138, "top": 130, "width": 804, "height": 562},
        }

    engine = InteractionStageEngine(stage_output=_stage_output)
    monkeypatch.setattr(engine, "_foreground", lambda: 77)

    def capture_fresh(**_kwargs):
        state = context["runtime_state"]["interaction_state"]
        state.update({
            "frame_id": 5,
            "current_artifact_id": fresh.artifact_id,
            "baseline_artifact_id": fresh.artifact_id,
            "target_binding": fresh_target,
        })
        return fresh

    monkeypatch.setattr(engine, "_capture_scene", capture_fresh)

    class ForbiddenToolSession:
        def __init__(self, *args, **kwargs):
            raise AssertionError("stale coordinates must not reach the controller")

    monkeypatch.setattr(runtime_module, "ToolSession", ForbiddenToolSession)
    result = engine.execute(
        stage,
        contract,
        context,
        threading.Event(),
        time.monotonic(),
        store=store,
        registry=EvidenceRegistry(f"run-stale-{change_kind}"),
    )

    assert result.ok is True
    assert result.branch == "reobserve"
    state = context["runtime_state"]["interaction_state"]
    assert state["frame_id"] == 5
    assert state["current_artifact_id"] == fresh.artifact_id
    assert state["planned_action"] is None
    assert expected_reason in state["action_preflight"]["stale_reasons"]
    assert state["action_preflight"]["fresh_artifact_id"] == fresh.artifact_id
    if change_kind in {"pixels", "long_drag_endpoint"}:
        pixel_change = state["action_preflight"]["pixel_change"]
        assert (
            pixel_change["interaction_layout"]["changed"] is True
            or pixel_change["action_guard"]["changed"] is True
        )
        assert (
            pixel_change["interaction_layout"]["change_ratio"] > 0
            or pixel_change["action_guard"]["change_ratio"] > 0
        )
        if change_kind == "long_drag_endpoint":
            assert pixel_change["interaction_layout"]["changed"] is False
            assert any(
                item["changed"] is True
                for item in pixel_change["action_guards"]
            )


def test_perform_preflight_tolerates_bounded_pixel_noise(monkeypatch, tmp_path):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.perception.surface_binding import InteractionRegion
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.tasking.interaction import InteractionAction
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine
    import winpilot.tasking.interaction_stage_runtime as runtime_module

    store = ArtifactStore("run-small-noise", tmp_path / "artifacts-noise")
    baseline_path = store.root / "planned-noise.png"
    fresh_path = store.root / "fresh-noise.png"
    _write_frame(baseline_path)
    _write_frame(fresh_path, changed_box=(40, 40, 40, 40))
    contract = _legacy_interaction_contract(
        task="click the visible control in the current window",
    )
    assert contract.session is not None
    target = {
        "hwnd": 77,
        "title": "generic target",
        "source": "active_window",
        "capture_scope": "target_window",
        "frame_region": {"left": 100, "top": 100, "width": 100, "height": 100},
        "client_region": {"left": 101, "top": 105, "width": 98, "height": 94},
        "client_frame_bounds": [10, 50, 990, 987],
        "foreground_verified": True,
    }
    def observation_frame(path, frame_id):
        # This fixture represents a real target-window observation, not an
        # externally selected file.  Keep the full provenance required by the
        # production spatial-coordinate lineage gate.
        return store._register_path(
            path,
            source_window="generic target",
            source_hwnd=77,
            source_scope="window",
            purpose="observation",
            sendable=False,
            metadata={
                "session_id": contract.session.session_id,
                "frame_id": frame_id,
                "frame_region": dict(target["frame_region"]),
                "region": dict(target["frame_region"]),
            },
        )

    planned = observation_frame(baseline_path, 4)
    fresh = observation_frame(fresh_path, 5)
    decision = _decision(
        action=InteractionAction(
            action="click",
            frame_x=400,
            frame_y=400,
            expected_effect_region=[350, 350, 450, 450],
        ),
        continuation="reobserve",
        frame_id=4,
        observation_artifact_id=planned.artifact_id,
    )
    stage = _legacy_stage(contract, "PerformTurn")
    context = {
        "run_id": "run-small-noise",
        "runtime_state": {"interaction_state": {
            "frame_id": 4,
            "current_artifact_id": planned.artifact_id,
            "baseline_artifact_id": planned.artifact_id,
            "planned_action": decision.model_dump(mode="json"),
            "target_binding": dict(target),
        }},
    }
    engine = InteractionStageEngine(stage_output=_stage_output)
    monkeypatch.setattr(engine, "_foreground", lambda: 77)
    binding = engine._binding(context)
    binding.update(
        InteractionRegion((200, 200, 800, 800), source="component-test"),
        hwnd=77,
        frame_region=(100, 100, 100, 100),
        capture_scope="target_window",
        client_region=InteractionRegion(
            (10, 50, 990, 987),
            label="window_client_authority",
            source="component-test",
        ),
        frame_id=4,
        initial=True,
    )

    def capture_fresh(**_kwargs):
        state = context["runtime_state"]["interaction_state"]
        state.update({
            "frame_id": 5,
            "current_artifact_id": fresh.artifact_id,
            "baseline_artifact_id": fresh.artifact_id,
            "target_binding": dict(target),
        })
        return fresh

    monkeypatch.setattr(engine, "_capture_scene", capture_fresh)
    dispatches = []

    class FakeToolSession:
        def __init__(self, *args, **kwargs):
            self.run_id = ""

        def _remember_target(self, hwnd, title):
            self.hwnd = hwnd

        def dispatch(self, name, args):
            dispatches.append((name, args))
            return ToolResult('{"ok": true}')

        def close(self):
            return None

    monkeypatch.setattr(runtime_module, "ToolSession", FakeToolSession)
    result = engine.execute(
        stage,
        contract,
        context,
        threading.Event(),
        time.monotonic(),
        store=store,
        registry=EvidenceRegistry("run-small-noise"),
    )

    assert result.ok is True
    assert result.branch == "next"
    assert len(dispatches) == 1
    preflight = context["runtime_state"]["interaction_state"]["action_preflight"]
    assert preflight["verified"] is True
    assert preflight["pixel_change"]["byte_identical"] is False
    assert preflight["pixel_change"]["interaction_layout"]["changed"] is False
    assert preflight["pixel_change"]["action_guard"]["changed"] is False


def test_input_mode_and_color_prepare_then_rebind_spatial_action_to_fresh_frame(
    monkeypatch,
    tmp_path,
):
    """Preparation mutations can never share the planner frame with drawing."""
    from winpilot.agent.tool_result import ToolResult
    from winpilot.perception.surface_binding import InteractionRegion
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.tasking.interaction import InteractionAction
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine
    import winpilot.tasking.interaction_stage_runtime as runtime_module

    contract = _legacy_interaction_contract(
        persistent=True,
        task="play with me on the current canvas, use red, your turn",
    )
    stage = _legacy_stage(contract, "PerformTurn")
    store = ArtifactStore("run-preparation-frame", tmp_path / "artifacts")
    target = {
        "hwnd": 77,
        "title": "generic target",
        "source": "active_window",
        "capture_scope": "target_window",
        "frame_region": {"left": 100, "top": 100, "width": 100, "height": 100},
        "client_region": {"left": 101, "top": 105, "width": 98, "height": 94},
        "client_frame_bounds": [10, 50, 990, 987],
        "foreground_verified": True,
    }

    def observation(name: str, frame_id: int):
        path = store.root / f"{name}.png"
        _write_frame(path)
        return store._register_path(
            path,
            source_window="generic target",
            source_hwnd=77,
            source_scope="window",
            purpose="observation",
            sendable=False,
            metadata={
                "session_id": contract.session.session_id,
                "frame_id": frame_id,
                "frame_region": dict(target["frame_region"]),
                "region": dict(target["frame_region"]),
            },
        )

    planned = observation("planned", 4)
    preflight = observation("preflight", 5)
    prepared = observation("prepared", 6)
    decision = _decision(
        action=InteractionAction(
            action="draw_strokes",
            target_region=[250, 250, 750, 750],
            target_anchor=[500, 500],
            stroke_paths=[{
                "coordinate_space": "target_region_0_1000",
                "points": [[180, 180], [820, 820]],
            }],
            color="red",
            interaction_mechanism="freehand_strokes",
            affordance_evidence="visible writable pointer surface",
            expected_effect_region=[250, 250, 750, 750],
        ),
        continuation="reobserve",
        frame_id=4,
        observation_artifact_id=planned.artifact_id,
    )
    context = {
        "run_id": "run-preparation-frame",
        "runtime_state": {"interaction_state": {
            "session_id": contract.session.session_id,
            "frame_id": 4,
            "current_artifact_id": planned.artifact_id,
            "baseline_artifact_id": planned.artifact_id,
            "planned_action": decision.model_dump(mode="json"),
            "target_binding": dict(target),
        }},
    }
    engine = InteractionStageEngine(stage_output=_stage_output)
    monkeypatch.setattr(engine, "_foreground", lambda: 77)
    binding = engine._binding(context)
    binding.update(
        InteractionRegion((200, 200, 800, 800), source="component-test"),
        hwnd=77,
        frame_region=(100, 100, 100, 100),
        capture_scope="target_window",
        client_region=InteractionRegion(
            (10, 50, 990, 987),
            label="window_client_authority",
            source="component-test",
        ),
        frame_id=4,
        initial=True,
    )
    captures = iter(((5, preflight), (6, prepared)))

    def capture_fresh(**_kwargs):
        frame_id, artifact = next(captures)
        state = context["runtime_state"]["interaction_state"]
        state.update({
            "frame_id": frame_id,
            "current_artifact_id": artifact.artifact_id,
            "baseline_artifact_id": artifact.artifact_id,
            "target_binding": dict(target),
            "last_capture_timing": {"capture_elapsed_ms": 2},
        })
        return artifact

    monkeypatch.setattr(engine, "_capture_scene", capture_fresh)
    monkeypatch.setattr(
        runtime_module,
        "refine_interaction_geometry",
        lambda current, *_args, **_kwargs: (
            current,
            {"applied": False, "verified": True, "provider": "identity-test"},
        ),
    )
    batches = []

    class FakeToolSession:
        def __init__(self, *args, **kwargs):
            self.run_id = ""

        def _remember_target(self, hwnd, title):
            self.hwnd = hwnd

        def dispatch(self, name, args):
            assert name == "run_action_batch"
            batches.append(args)
            if len(batches) == 1:
                return ToolResult(
                    '{"ok":true,"results":['
                    '{"action":"ensure_input_mode","data":{'
                    '"verified":true,"input_mode":"continuous_freehand",'
                    '"provider":"component-test"}},'
                    '{"action":"select_color","data":{'
                    '"verified":true,"color":"red",'
                    '"provider":"component-test"}}]}'
                )
            return ToolResult('{"ok":true,"results":[{"action":"draw_strokes","ok":true}]}')

        def close(self):
            return None

    monkeypatch.setattr(runtime_module, "ToolSession", FakeToolSession)
    result = engine.execute(
        stage,
        contract,
        context,
        threading.Event(),
        time.monotonic(),
        store=store,
        registry=EvidenceRegistry("run-preparation-frame"),
    )

    assert result.ok is True
    assert len(batches) == 2
    assert [item["action"] for item in batches[0]["actions"]] == [
        "ensure_input_mode", "select_color",
    ]
    assert [item["action"] for item in batches[1]["actions"]] == [
        "draw_strokes",
    ]
    state = context["runtime_state"]["interaction_state"]
    assert state["input_preparation"]["verified"] is True
    assert state["input_preparation"]["planned_artifact_id"] == preflight.artifact_id
    assert state["input_preparation"]["prepared_artifact_id"] == prepared.artifact_id
    assert state["planned_action"]["frame_id"] == 6
    assert state["planned_action"]["observation_artifact_id"] == prepared.artifact_id


def test_region_watcher_requires_change_then_stability():
    import numpy as np

    from winpilot.fast.watcher import ScreenWatcher
    from winpilot.utils.screenshot import Region

    baseline = np.zeros((40, 40, 3), dtype=np.uint8)
    first_motion = baseline.copy()
    first_motion[8:18, 8:18] = 255
    final = baseline.copy()
    final[8:26, 8:26] = 255
    frames = [baseline, first_motion, final, final, final, final, final]
    captures = []

    def capture(_region):
        index = min(len(captures), len(frames) - 1)
        captures.append(index)
        return frames[index].copy()

    watcher = ScreenWatcher(capture_fn=capture, fps=1000)
    delta = watcher.wait_for_region_change(
        Region(0, 0, 40, 40),
        timeout_s=0.25,
        min_ratio=0.01,
        require_persistent=True,
        stable_ms=2,
        stable_max_ratio=0.001,
    )

    assert delta.changed is True
    assert delta.ratio >= 0.01
    # Baseline + first motion is insufficient: the watcher observed the later
    # state and consecutive stable samples before returning.
    assert len(captures) >= 5


def test_region_watcher_long_wait_is_quickly_cancelable():
    import numpy as np

    from winpilot.fast.watcher import ScreenWatcher
    from winpilot.utils.screenshot import Region

    stop = threading.Event()
    timer = threading.Timer(0.04, stop.set)
    watcher = ScreenWatcher(
        capture_fn=lambda _region: np.zeros((20, 20, 3), dtype=np.uint8),
        fps=1,
    )
    started = time.monotonic()
    timer.start()
    try:
        delta = watcher.wait_for_region_change(
            Region(0, 0, 20, 20),
            timeout_s=60,
            min_ratio=0.01,
            stop_event=stop,
            require_persistent=True,
        )
    finally:
        timer.cancel()

    assert delta.changed is False
    assert time.monotonic() - started < 0.35


def test_controller_propagates_run_cancel_into_persistent_visual_wait():
    import numpy as np

    from winpilot.fast.controller import FastGuiController
    from winpilot.fast.watcher import ScreenWatcher

    stop = threading.Event()
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    watcher = ScreenWatcher(capture_fn=lambda _region: frame.copy(), fps=1)

    class Session:
        hwnd = 11
        run_id = "cancel-persistent-wait"
        snapshot = None
        stop_event = stop

    controller = FastGuiController(
        Session(),
        watcher=watcher,
        capture_fn=lambda _region: frame.copy(),
    )
    timer = threading.Timer(0.04, stop.set)
    started = time.monotonic()
    timer.start()
    try:
        result = controller.execute([{
            "action": "wait_for_visual_change",
            "region": {"x": 0, "y": 0, "width": 20, "height": 20},
            "timeout_s": 60,
            "require_persistent": True,
        }], sync=False)
    finally:
        timer.cancel()

    assert result["ok"] is False
    data = result["results"][0]["data"]
    assert data["canceled"] is True
    assert data["changed"] is False
    assert time.monotonic() - started < 0.35


def _write_frame(path, *, changed_box=None):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (100, 100), "white")
    if changed_box is not None:
        ImageDraw.Draw(image).rectangle(changed_box, fill="black")
    image.save(path)


def _run_roi_verify(
    tmp_path,
    changed_box,
    *,
    persistent=False,
    task_complete=True,
    deferred_program=None,
):
    from unittest.mock import patch
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.tasking.interaction import InteractionAction
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine

    baseline_path = tmp_path / "baseline.png"
    current_path = tmp_path / "current.png"
    _write_frame(baseline_path)
    _write_frame(current_path, changed_box=changed_box)
    store = ArtifactStore("run-roi", tmp_path / "artifacts")
    baseline = store.import_file(baseline_path, purpose="observation", sendable=False)
    current = store.import_file(current_path, purpose="observation", sendable=False)
    decision = _decision(
        action=InteractionAction(
            action="click",
            frame_x=400,
            frame_y=400,
            expected_effect_region=[300, 300, 500, 500],
        ),
        continuation="reobserve",
        observation_artifact_id=baseline.artifact_id,
    )
    contract = _legacy_interaction_contract(
        persistent=persistent,
        task="点击当前页面中的蓝色按钮",
    )
    stage = _legacy_stage(contract, "VerifyLocalEffect")
    context = {
        "run_id": "run-roi",
        "runtime_state": {"interaction_state": {
            "planned_action": decision.model_dump(mode="json"),
            "executed_action": {"deferred": False},
            "target_binding": {"source": "active_window"},
            "current_artifact_id": baseline.artifact_id,
            "frame_id": decision.frame_id,
            "deferred_action_program": deferred_program,
        }},
    }
    engine = InteractionStageEngine(stage_output=_stage_output)
    engine._capture_scene = lambda **_kwargs: current
    with patch(
        "winpilot.tasking.interaction_stage_runtime.verify_interaction_effect",
        return_value={
            "ok": True,
            "verified": True,
            "semantic_visual_verified": True,
            "effect_region": [300, 300, 500, 500],
            "task_complete": task_complete,
        },
    ) as verifier:
        result = engine.execute(
            stage,
            contract,
            context,
            threading.Event(),
            time.monotonic(),
            store=store,
            registry=EvidenceRegistry("run-roi"),
        )
    if deferred_program is not None:
        verifier.assert_not_called()
    return result


def test_local_effect_verification_uses_action_roi_not_unrelated_pixels(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(RuntimeError) as error:
        _run_roi_verify(outside, (80, 80, 95, 95))
    assert getattr(error.value, "failure_type", "") == "local_effect_missing"

    inside = tmp_path / "inside"
    inside.mkdir()
    result = _run_roi_verify(inside, (32, 32, 48, 48))
    assert result.ok is True
    assert result.branch == "complete"
    change = result.output["interaction_state"]["local_verification"]["local_change"]
    assert change["changed"] is True
    assert change["target_region"] == {
        "left": 30,
        "top": 30,
        "width": 20,
        "height": 20,
    }
    assert result.output["model_calls"] == 1
    assert result.output["controller_elapsed_ms"] == 0
    assert result.output["interaction_state"]["budget"]["model_calls_used"] == 1


def test_point_selection_can_verify_remote_feedback_on_bound_surface(tmp_path):
    from unittest.mock import patch
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.tasking.interaction import InteractionAction
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine

    baseline_path = tmp_path / "point-before.png"
    current_path = tmp_path / "point-after.png"
    _write_frame(baseline_path)
    # The clicked target is at 30..50, while its visible acknowledgement is a
    # remote banner at the bottom of the same bound surface.
    _write_frame(current_path, changed_box=(65, 65, 76, 76))
    store = ArtifactStore("run-remote-effect", tmp_path / "artifacts")
    baseline = store.import_file(baseline_path, purpose="observation", sendable=False)
    current = store.import_file(current_path, purpose="observation", sendable=False)
    decision = _decision(
        action=InteractionAction(
            action="click",
            frame_x=400,
            frame_y=400,
            target_region=[300, 300, 500, 500],
            expected_effect_region=[300, 300, 500, 500],
            effect_region_binding="target_region",
            interaction_mechanism="point_selection",
            affordance_evidence="unique visible selectable object",
        ),
        observation_artifact_id=baseline.artifact_id,
    )
    contract = _legacy_interaction_contract(
        task="click the unique visible target",
    )
    stage = _legacy_stage(contract, "VerifyLocalEffect")
    context = {
        "run_id": "run-remote-effect",
        "runtime_state": {"interaction_state": {
            "planned_action": decision.model_dump(mode="json"),
            "executed_action": {"deferred": False},
            "target_binding": {"source": "active_window"},
            "current_artifact_id": baseline.artifact_id,
        }},
    }
    engine = InteractionStageEngine(stage_output=_stage_output)
    engine._capture_scene = lambda **_kwargs: current
    with patch(
        "winpilot.tasking.interaction_stage_runtime.verify_interaction_effect",
        return_value={
            "ok": True,
            "verified": True,
            "semantic_visual_verified": True,
            "effect_region": [650, 650, 760, 760],
            "task_complete": True,
        },
    ) as verifier:
        result = engine.execute(
            stage,
            contract,
            context,
            threading.Event(),
            time.monotonic(),
            store=store,
            registry=EvidenceRegistry("run-remote-effect"),
        )

    assert result.ok is True
    assert result.branch == "complete"
    regions = verifier.call_args.kwargs["authoritative_effect_regions"]
    assert [200.0, 200.0, 800.0, 800.0] in regions
    verification = result.output["interaction_state"]["local_verification"]
    assert any(item["changed"] is True for item in verification["local_changes"])


def test_persistent_interaction_can_finish_when_semantic_terminal_state_is_proved(tmp_path):
    result = _run_roi_verify(
        tmp_path,
        (32, 32, 48, 48),
        persistent=True,
    )

    assert result.ok is True
    assert result.branch == "complete"
    assert result.output["interaction_state"]["status"] == "completed"
    completion = result.output["completion_evidence"]
    assert completion["predicate"] == "interaction.task_completed"
    assert completion["provider"] == "interaction_terminal_verifier"
    assert completion["artifact_id"]
    assert completion["metadata"]["local_effect_evidence_id"]


def test_persistent_grounded_freehand_turn_skips_second_semantic_model_call(
    tmp_path,
    monkeypatch,
):
    import cv2
    import numpy as np

    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.tasking.interaction import InteractionAction
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine

    before_image = np.full((180, 240, 3), 245, dtype=np.uint8)
    after_image = before_image.copy()
    action = InteractionAction(
        action="draw_strokes",
        target_region=[250, 250, 750, 750],
        target_anchor=[500, 500],
        stroke_paths=[
            {
                "coordinate_space": "target_region_0_1000",
                "points": [[180, 180], [820, 820]],
            },
            {
                "coordinate_space": "target_region_0_1000",
                "points": [[820, 180], [180, 820]],
            },
        ],
        color="red",
        expected_effect_region=[250, 250, 750, 750],
        effect_region_binding="target_region",
        interaction_mechanism="freehand_strokes",
        affordance_evidence="visible pointer-authored surface",
    )
    decision = _decision(
        action=action,
        continuation="wait_external",
        frame_id=4,
        observation_artifact_id="placeholder",
    )
    for path in action.resolved_frame_strokes(inset_fraction=0.04):
        points = np.asarray([
            [
                round(point[0] / 1000 * (after_image.shape[1] - 1)),
                round(point[1] / 1000 * (after_image.shape[0] - 1)),
            ]
            for point in path
        ], dtype=np.int32)
        cv2.polylines(after_image, [points], False, (20, 20, 220), 5)
    before_path = tmp_path / "persistent-before.png"
    after_path = tmp_path / "persistent-after.png"
    assert cv2.imwrite(str(before_path), before_image)
    assert cv2.imwrite(str(after_path), after_image)

    store = ArtifactStore("run-freehand-fast", tmp_path / "artifacts")
    baseline = store.import_file(
        before_path, purpose="observation", sendable=False,
    )
    current = store.import_file(
        after_path, purpose="observation", sendable=False,
    )
    decision = decision.model_copy(update={
        "observation_artifact_id": baseline.artifact_id,
    })
    contract = _legacy_interaction_contract(
        persistent=True,
        task="play with me on this canvas, I already started, your turn",
    )
    stage = _legacy_stage(contract, "VerifyLocalEffect")
    assert any(
        provider.id == "typed_freehand_turn_completion"
        for obligation in stage.obligations
        for provider in obligation.evidence_providers
    )
    context = {
        "run_id": "run-freehand-fast",
        "runtime_state": {"interaction_state": {
            "planned_action": decision.model_dump(mode="json"),
            "executed_action": {"deferred": False},
            "target_binding": {"source": "active_window"},
            "current_artifact_id": baseline.artifact_id,
            "frame_id": 4,
            "visual_geometry_grounding": {
                "verified": True,
                "provider": "local_visual_geometry",
                "refinements": [{
                    "action_index": 0,
                    "applied": True,
                    "target": {"provider": "current_partition_pixels"},
                }],
            },
            "input_mode_evidence": [{
                "verified": True,
                "input_mode": "continuous_freehand",
                "provider": "accessibility_active_mode",
            }],
        }},
    }
    engine = InteractionStageEngine(stage_output=_stage_output)
    monkeypatch.setattr(engine, "_capture_scene", lambda **_kwargs: current)
    monkeypatch.setattr(
        "winpilot.tasking.interaction_stage_runtime.verify_interaction_effect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("typed persistent turn must not call semantic verifier")
        ),
    )
    registry = EvidenceRegistry("run-freehand-fast")

    result = engine.execute(
        stage,
        contract,
        context,
        threading.Event(),
        time.monotonic(),
        store=store,
        registry=registry,
    )

    assert result.ok is True
    assert result.branch == "next"
    assert result.output["model_calls"] == 0
    verification = result.output["interaction_state"]["local_verification"]
    assert verification["semantic"]["verify_method"] == (
        "typed-freehand-turn-completion"
    )
    assert result.output["evidence"]["provider"] == (
        "typed_freehand_turn_completion"
    )


def test_persistent_verified_turn_advances_to_external_wait(tmp_path):
    result = _run_roi_verify(
        tmp_path,
        (32, 32, 48, 48),
        persistent=True,
        task_complete=False,
    )

    assert result.ok is True
    assert result.branch == "next"
    state = result.output["interaction_state"]
    assert state["status"] == "waiting_external"
    assert state["turn_index"] == 1
    assert state["agent_turns_since_external_change"] == 1
    assert result.output.get("completion_evidence") is None


def test_verified_nonterminal_continuation_does_not_consume_recovery_budget(tmp_path):
    result = _run_roi_verify(
        tmp_path,
        (32, 32, 48, 48),
        task_complete=False,
    )

    assert result.ok is True
    assert result.branch == "continue"
    state = result.output["interaction_state"]
    assert state["status"] == "turn_verified_followup"
    assert int(state.get("replan_count") or 0) == 0
    assert len(state["verified_turn_history"]) == 1
    progress = state["verified_turn_history"][0]
    assert progress["turn_index"] == 1
    assert progress["artifact_id"]
    assert progress["actions"][0]["action"] == "click"
    assert progress["task_complete"] is False


def test_verified_local_program_step_advances_without_another_model_call(tmp_path):
    from winpilot.tasking.interaction import InteractionAction

    next_action = InteractionAction(
        action="click",
        frame_x=650,
        frame_y=650,
        target_region=[600, 600, 700, 700],
        target_anchor=[650, 650],
        grounding_mode="object_anchor",
        interaction_mechanism="point_selection",
        affordance_evidence="a second stable visible marker",
        expected_effect_region=[600, 600, 700, 700],
    )
    result = _run_roi_verify(
        tmp_path,
        (32, 32, 48, 48),
        task_complete=False,
        deferred_program={
            "program_id": "interaction-program-component-test",
            "origin_frame_id": 1,
            "next_step_index": 1,
            "remaining_actions": [next_action.model_dump(mode="json")],
            "terminal_continuation": "finish",
        },
    )

    assert result.ok is True
    assert result.branch == "continue_local"
    assert result.output["model_calls"] == 0
    state = result.output["interaction_state"]
    assert state["status"] == "local_program_followup"
    assert state["planned_action"]["actions"][0]["target_anchor"] == [650.0, 650.0]
    assert state["planned_action"]["observation_artifact_id"] == state["current_artifact_id"]
    assert state["deferred_action_program"] is None
    assert state["last_local_verification"]["semantic"]["verify_method"] == (
        "grounded-local-program-progress"
    )
    assert result.output["evidence"]["provider"] == "grounded_transaction_progress"


def test_effect_verifier_accepts_task_complete_only_after_strict_effect_proof(monkeypatch):
    from winpilot.tasking.interaction import InteractionAction, verify_interaction_effect
    import winpilot.tasking.interaction as interaction_module

    decision = _decision(
        action=InteractionAction(
            action="click",
            frame_x=400,
            frame_y=400,
            expected_effect_region=[300, 300, 500, 500],
        ),
        continuation="reobserve",
    )
    payload = {
        "passed": True,
        "confidence": 0.95,
        "observed": ["the requested visible state is present"],
        "issues": [],
        "effect_region": [320, 320, 480, 480],
        "task_complete": True,
    }
    monkeypatch.setattr(
        interaction_module.vlm,
        "structured_json_from_image",
        lambda *_args, **_kwargs: dict(payload),
    )

    verified = verify_interaction_effect("unused.png", task="complete the visible task", decision=decision)
    assert verified["verified"] is True
    assert verified["task_complete"] is True
    assert verified["verifier_contract"]["effect_region_valid"] is True
    assert verified["raw_verdict"]["passed"] is True

    payload["passed"] = False
    rejected = verify_interaction_effect("unused.png", task="complete the visible task", decision=decision)
    assert rejected["verified"] is False
    assert rejected["task_complete"] is False


def test_effect_verifier_separates_successful_local_turn_from_terminal_completion(
    monkeypatch,
):
    from winpilot.tasking.interaction import InteractionAction, verify_interaction_effect
    import winpilot.tasking.interaction as interaction_module

    decision = _decision(
        action=InteractionAction(
            action="click",
            frame_x=400,
            frame_y=400,
            target_region=[300, 300, 500, 500],
            target_anchor=[400, 400],
            grounding_mode="object_anchor",
            interaction_mechanism="point_selection",
            affordance_evidence="visible numbered marker",
            expected_effect_region=[300, 300, 500, 500],
        ),
        continuation="reobserve",
    )
    monkeypatch.setattr(
        interaction_module.vlm,
        "structured_json_from_image",
        lambda *_args, **_kwargs: {
            "local_effect_passed": True,
            "task_complete": False,
            "confidence": 0.98,
            "observed": ["the selected marker visibly changed state"],
            "local_effect_issues": [],
            "completion_issues": ["two requested selections remain"],
            "effect_region": [320, 320, 480, 480],
        },
    )

    result = verify_interaction_effect(
        "unused.png",
        task="select all requested visible targets in order",
        decision=decision,
    )

    assert result["verified"] is True
    assert result["task_complete"] is False
    assert result["issues"] == []
    assert result["completion_issues"] == ["two requested selections remain"]
    assert result["verifier_contract"]["verdict_schema"] == (
        "local-effect-and-terminal-v2"
    )


def test_effect_verifier_never_promotes_terminal_claim_with_completion_issues(
    monkeypatch,
):
    from winpilot.tasking.interaction import InteractionAction, verify_interaction_effect
    import winpilot.tasking.interaction as interaction_module

    decision = _decision(
        action=InteractionAction(
            action="click",
            frame_x=400,
            frame_y=400,
            expected_effect_region=[300, 300, 500, 500],
        ),
        continuation="finish",
    )
    monkeypatch.setattr(
        interaction_module.vlm,
        "structured_json_from_image",
        lambda *_args, **_kwargs: {
            "local_effect_passed": True,
            "task_complete": True,
            "confidence": 0.98,
            "observed": ["the clicked control changed state"],
            "local_effect_issues": [],
            "completion_issues": ["the requested final state is not visible"],
            "effect_region": [320, 320, 480, 480],
        },
    )

    result = verify_interaction_effect(
        "unused.png", task="complete the visible task", decision=decision,
    )

    assert result["verified"] is True
    assert result["task_complete"] is False
    assert result["verifier_contract"]["completion_issues_valid"] is False


def test_effect_verifier_can_adjudicate_vlm_spatial_doubt_with_causal_transfer(
    monkeypatch,
):
    from winpilot.tasking.interaction import InteractionAction, verify_interaction_effect
    import winpilot.tasking.interaction as interaction_module

    decision = _decision(
        action=InteractionAction(
            action="drag",
            frame_x1=260,
            frame_y1=500,
            frame_x2=690,
            frame_y2=500,
            source_region=[220, 400, 300, 600],
            target_region=[600, 350, 780, 650],
            interaction_mechanism="direct_manipulation",
            affordance_evidence="visible movable object and destination",
            expected_effect_region=[600, 350, 780, 650],
            effect_region_binding="target_region",
        ),
        continuation="reobserve",
    )
    monkeypatch.setattr(
        interaction_module.vlm,
        "structured_json_from_image",
        lambda *_args, **_kwargs: {
            "passed": True,
            "confidence": 0.95,
            "observed": ["the object is visibly inside the typed target"],
            "issues": ["its former position is visually ambiguous"],
            "effect_region": [610, 360, 770, 640],
            "task_complete": True,
        },
    )

    rejected = verify_interaction_effect(
        "unused.png", task="move the object", decision=decision,
    )
    assert rejected["verified"] is False

    verified = verify_interaction_effect(
        "unused.png",
        task="move the object",
        decision=decision,
        transition_evidence={
            "applicable": True,
            "verified": True,
            "provider": "local_direct_manipulation_transition",
            "actions": [{
                "action_index": 0,
                "verified": True,
                "source_change_ratio": 0.3,
                "target_change_ratio": 0.2,
                "appearance_histogram_intersection": 0.8,
                "placement_geometry_valid": True,
            }],
        },
    )
    assert verified["verified"] is True
    assert verified["task_complete"] is True
    assert (
        verified["verifier_contract"]["issues_adjudicated_by_transition"]
        is True
    )


def test_model_change_ratio_is_capped_for_causal_detection_not_semantics():
    from winpilot.tasking.interaction import InteractionAction
    from winpilot.tasking.interaction_stage_runtime import (
        _local_causal_change_ratio,
    )

    decision = _decision(
        action=InteractionAction(
            action="click",
            frame_x=400,
            frame_y=400,
            expected_effect_region=[300, 300, 500, 500],
        ),
    )
    decision.expected_effect.minimum_change_ratio = 0.9

    assert _local_causal_change_ratio(
        decision, remote_feedback=False,
    ) == 0.05
    assert _local_causal_change_ratio(
        decision, remote_feedback=True,
    ) == 0.0025


def test_bounded_no_action_finish_requires_independent_visual_proof(monkeypatch, tmp_path):
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine
    import winpilot.tasking.interaction_stage_runtime as runtime_module

    image_path = tmp_path / "frame.png"
    _write_frame(image_path)
    store = ArtifactStore("run-noop", tmp_path / "artifacts")
    artifact = store.import_file(image_path, purpose="observation", sendable=False)
    decision = _decision(
        action=None,
        turn_owner="agent",
        continuation="finish",
        observation_artifact_id=artifact.artifact_id,
    )
    contract = _legacy_interaction_contract(task="点击当前页面中的蓝色按钮")
    stage = _legacy_stage(contract, "VerifyLocalEffect")
    context = {
        "run_id": "run-noop",
        "runtime_state": {"interaction_state": {
            "planned_action": decision.model_dump(mode="json"),
            "executed_action": {"deferred": True},
            "current_artifact_id": artifact.artifact_id,
        }},
    }
    monkeypatch.setattr(
        runtime_module,
        "verify_interaction_completion",
        lambda *_args, **_kwargs: {"ok": True, "verified": False, "issues": ["target still unchanged"]},
    )
    engine = InteractionStageEngine(stage_output=_stage_output)
    engine._capture_scene = lambda **_kwargs: artifact
    with pytest.raises(RuntimeError) as error:
        engine.execute(
            stage,
            contract,
            context,
            threading.Event(),
            time.monotonic(),
            store=store,
            registry=EvidenceRegistry("run-noop"),
        )
    assert getattr(error.value, "failure_type", "") == "semantic_verification_failed"


def test_persistent_no_action_terminal_proof_emits_completion_evidence(monkeypatch, tmp_path):
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine
    import winpilot.tasking.interaction_stage_runtime as runtime_module

    image_path = tmp_path / "terminal-frame.png"
    _write_frame(image_path, changed_box=(20, 20, 40, 40))
    store = ArtifactStore("run-persistent-noop", tmp_path / "artifacts")
    artifact = store.import_file(image_path, purpose="observation", sendable=False)
    contract = _legacy_interaction_contract(
        persistent=True,
        task="play with me in the current window; I have already started",
    )
    assert contract.session is not None
    assert contract.session.lifecycle == "persistent"
    stage = _legacy_stage(contract, "VerifyLocalEffect")
    decision = _decision(
        action=None,
        turn_owner="agent",
        continuation="finish",
        observation_artifact_id=artifact.artifact_id,
    )
    context = {
        "run_id": "run-persistent-noop",
        "runtime_state": {"interaction_state": {
            "planned_action": decision.model_dump(mode="json"),
            "executed_action": {"deferred": True},
            "current_artifact_id": artifact.artifact_id,
        }},
    }
    monkeypatch.setattr(
        runtime_module,
        "verify_interaction_completion",
        lambda *_args, **_kwargs: {
            "ok": True,
            "verified": True,
            "confidence": 0.96,
            "observed": ["the original interaction is visibly terminal"],
            "issues": [],
        },
    )
    registry = EvidenceRegistry("run-persistent-noop")
    engine = InteractionStageEngine(stage_output=_stage_output)
    engine._capture_scene = lambda **_kwargs: artifact

    result = engine.execute(
        stage,
        contract,
        context,
        threading.Event(),
        time.monotonic(),
        store=store,
        registry=registry,
    )

    assert result.ok is True
    assert result.branch == "complete"
    assert result.output["interaction_state"]["status"] == "completed"
    assert [item["predicate"] for item in registry.records()] == [
        "interaction.local_effect_verified",
        "interaction.task_completed",
    ]
    terminal = result.output["completion_evidence"]
    assert terminal["artifact_id"] == artifact.artifact_id
    assert terminal["metadata"]["proof"] == "independent_terminal_visual_verification"


def test_interaction_recovery_discards_old_plan_and_consumes_replan_budget():
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine

    contract = _legacy_interaction_contract(
        task="drag the visible item to its indicated destination",
        max_steps=4,
    )
    stage = _legacy_stage(contract, "PerformTurn")
    context = {
        "run_id": "run-recovery",
        "runtime_state": {"interaction_state": {
            "planned_action": {"unsafe": "old coordinates"},
            "executed_action": {"outcome": "uncertain"},
            "local_verification": {"verified": False},
            "last_local_verification": {"verified": True},
            "verified_turn_history": [{
                "turn_index": 1,
                "artifact_id": "verified-frame",
                "actions": [{"action": "click"}],
                "task_complete": False,
            }],
        }},
    }
    engine = InteractionStageEngine(stage_output=_stage_output)

    result = engine.recover_failure(
        stage,
        contract,
        context,
        time.monotonic(),
        failure_type="interaction_action_failed",
        recovery_action="reobserve_then_replan_current_turn",
        recovery_reason="interaction_action_failed",
        retryable=True,
        failure_details={
            "controller_elapsed_ms": 17,
            "error": "the rejected primitive had no visible object anchor",
        },
    )

    assert result is not None
    assert result.ok is True
    assert result.branch == "recover_reobserve"
    state = result.output["interaction_state"]
    assert state["planned_action"] is None
    assert state["executed_action"] is None
    assert state["local_verification"] is None
    assert state["last_local_verification"] == {"verified": True}
    assert state["verified_turn_history"] == [{
        "turn_index": 1,
        "artifact_id": "verified-frame",
        "actions": [{"action": "click"}],
        "task_complete": False,
    }]
    assert state["last_failure"]["failure_type"] == "interaction_action_failed"
    assert "no visible object anchor" in state["last_failure"]["reason"]
    assert state["replan_count"] == 1
    assert state["budget"]["replans_used"] == 1
    assert result.output["controller_elapsed_ms"] == 17

    with pytest.raises(RuntimeError) as exhausted:
        engine.recover_failure(
            stage,
            contract,
            context,
            time.monotonic(),
            failure_type="interaction_action_failed",
            recovery_action="reobserve_then_replan_current_turn",
            recovery_reason="interaction_action_failed",
            retryable=True,
            failure_details={
                "model_calls": 1,
                "model_elapsed_ms": 29,
                "controller_elapsed_ms": 17,
                "capture_elapsed_ms": 5,
            },
        )
    assert getattr(exhausted.value, "failure_type", "") == "interaction_budget_exhausted"
    assert exhausted.value.stage_details["budget_dimension"] == "replans"
    assert exhausted.value.stage_details["model_calls"] == 1
    assert exhausted.value.stage_details["model_elapsed_ms"] == 29
    assert exhausted.value.stage_details["controller_elapsed_ms"] == 17
    assert exhausted.value.stage_details["capture_elapsed_ms"] == 5


def test_extreme_uniform_compositor_frame_is_rejected_before_model_use(
    monkeypatch,
    tmp_path,
):
    import cv2
    import numpy as np

    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine
    import winpilot.tasking.interaction_stage_runtime as runtime_module

    image_path = tmp_path / "uniform.png"
    assert cv2.imwrite(str(image_path), np.full((240, 320, 3), 54, dtype=np.uint8))
    store = ArtifactStore("run-uniform", tmp_path / "artifacts")
    artifact = store.import_file(image_path, purpose="observation", sendable=False)
    control_path = tmp_path / "minimal-control.png"
    minimal_control = np.full((240, 320, 3), 54, dtype=np.uint8)
    cv2.rectangle(minimal_control, (145, 105), (175, 135), (220, 220, 220), -1)
    cv2.rectangle(minimal_control, (145, 105), (175, 135), (245, 245, 245), 2)
    assert cv2.imwrite(str(control_path), minimal_control)
    control_artifact = store.import_file(
        control_path, purpose="observation", sendable=False,
    )
    assert runtime_module._authoritative_frame_quality(
        control_artifact, [0, 0, 1000, 1000],
    )["usable"] is True
    contract = _legacy_interaction_contract(
        task="interact with the visible control in the current window",
    )
    context = {
        "run_id": "run-uniform",
        "runtime_state": {"interaction_state": {
            "frame_id": 1,
            "current_artifact_id": artifact.artifact_id,
            "target_binding": {
                "hwnd": 91,
                "title": "target",
                "source": "active_window",
                "frame_region": {
                    "left": 0, "top": 0, "width": 320, "height": 240,
                },
                "client_frame_bounds": [0, 0, 1000, 1000],
            },
        }},
    }

    class _Delta:
        changed = False
        ratio = 0.0

    class _Watcher:
        def __init__(self, **_kwargs):
            pass

        def wait_for_region_change(self, *_args, **_kwargs):
            return _Delta()

    monkeypatch.setattr(runtime_module, "ScreenWatcher", _Watcher)
    engine = InteractionStageEngine(stage_output=_stage_output)
    engine._capture_scene = lambda **_kwargs: artifact

    with pytest.raises(RuntimeError) as unusable:
        engine._ensure_usable_inference_frame(
            artifact=artifact,
            context=context,
            contract=contract,
            store=store,
            stop_event=threading.Event(),
        )

    assert getattr(unusable.value, "failure_type", "") == "interaction_frame_unusable"
    assert unusable.value.stage_details["model_calls"] == 0
    refreshed = unusable.value.stage_details["frame_quality"]["refreshed"]
    assert refreshed["reason"] == "extreme_uniform_client"


@pytest.mark.parametrize("second_attempt_valid", [True, False])
def test_infer_stage_retries_invalid_model_schema_once_on_same_authoritative_frame(
    monkeypatch,
    tmp_path,
    second_attempt_valid,
):
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.tasking.interaction import (
        InteractionAction,
        InteractionPlanOutputError,
    )
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine
    import winpilot.tasking.interaction_stage_runtime as runtime_module

    contract = _legacy_interaction_contract(
        persistent=True,
        task="continue the visible shared turn-based interface; it is your turn",
    )
    assert contract.session is not None
    assert contract.session.wait_for_external_change is True
    stage = _legacy_stage(contract, "InferInteraction")
    image_path = tmp_path / "authoritative-frame.png"
    _write_frame(image_path, changed_box=(50, 50, 120, 120))
    store = ArtifactStore("run-schema-retry", tmp_path / "artifacts")
    artifact = store.import_file(
        image_path, purpose="observation", sendable=False,
    )
    decision = _decision(
        action=InteractionAction(
            action="click",
            frame_x=400,
            frame_y=400,
            target_region=[350, 350, 450, 450],
            target_anchor=[400, 400],
            grounding_mode="object_anchor",
            interaction_mechanism="point_selection",
            affordance_evidence="a bounded selectable target in the current frame",
            expected_effect_region=[350, 350, 450, 450],
        ),
        continuation="wait_external",
        frame_id=9,
        observation_artifact_id=artifact.artifact_id,
    )
    context = {
        "run_id": "run-schema-retry",
        "runtime_state": {"interaction_state": {
            "frame_id": 9,
            "current_artifact_id": artifact.artifact_id,
            "target_binding": {
                "hwnd": 91,
                "title": "generic target",
                "source": "active_window",
                "frame_region": {
                    "left": 0, "top": 0, "width": 320, "height": 240,
                },
                "client_frame_bounds": [0, 0, 1000, 1000],
            },
        }},
    }
    calls = []

    def _plan(path, **kwargs):
        calls.append({"path": str(path), **kwargs})
        if len(calls) == 1 or not second_attempt_valid:
            raise InteractionPlanOutputError(
                "provider placed one field at the wrong schema level",
                raw_payload={
                    "actions": [{
                        "action": "click", "unexpected_schema_field": True,
                    }],
                },
                validation_errors=[{
                    "location": ["actions", "0", "unexpected_schema_field"],
                    "type": "extra_forbidden",
                    "message": "Extra inputs are not permitted",
                }],
            )
        assert kwargs["validation_feedback"]["error_code"] == (
            "schema_validation_failed"
        )
        return decision

    events = []
    monkeypatch.setattr(runtime_module, "plan_interaction", _plan)
    monkeypatch.setattr(
        runtime_module.BUS,
        "publish",
        lambda event, **payload: events.append((event, payload)),
    )
    engine = InteractionStageEngine(stage_output=_stage_output)
    engine._ensure_usable_inference_frame = lambda **_kwargs: (
        artifact, {"usable": True, "reason": "test_frame"}, 0,
    )

    if second_attempt_valid:
        result = engine.execute(
            stage,
            contract,
            context,
            threading.Event(),
            time.monotonic(),
            store=store,
            registry=EvidenceRegistry("run-schema-retry"),
        )
        assert result.ok is True
        assert result.output["model_calls"] == 2
    else:
        with pytest.raises(RuntimeError) as rejected:
            engine.execute(
                stage,
                contract,
                context,
                threading.Event(),
                time.monotonic(),
                store=store,
                registry=EvidenceRegistry("run-schema-retry"),
            )
        assert getattr(rejected.value, "failure_type", "") == (
            "interaction_plan_invalid"
        )
        assert rejected.value.stage_details["model_calls"] == 2
    assert len(calls) == 2
    assert calls[0]["path"] == calls[1]["path"] == artifact.managed_path
    assert calls[0]["observation_artifact_id"] == artifact.artifact_id
    assert calls[1]["observation_artifact_id"] == artifact.artifact_id
    state = context["runtime_state"]["interaction_state"]
    assert state["model_calls"] == 2
    assert len(state["inference_validation_attempts"]) == (
        1 if second_attempt_valid else 2
    )
    if second_attempt_valid:
        assert (
            "schema_validation_feedback_retry:accepted"
            in state["inference_canonicalization_repairs"]
        )
    validation_events = [
        item for item in events if item[0] == "interaction_plan_validation"
    ]
    assert len(validation_events) == (1 if second_attempt_valid else 2)
    assert validation_events[0][1]["will_retry_same_frame"] is True
    assert validation_events[-1][1]["will_retry_same_frame"] is (
        True if second_attempt_valid else False
    )
    assert validation_events[-1][1]["artifact_id"] == artifact.artifact_id
    assert validation_events[-1][1]["frame_id"] == 9


def test_blocked_inference_is_fail_fast_not_reobserve_recovery(monkeypatch):
    from winpilot.tasking.interaction_stage_runtime import _failure
    from winpilot.tasking.stage_runtime import StageRuntime

    contract, graph = _legacy_interaction_graph(
        task="drag the visible item to its indicated destination",
        max_steps=8,
    )
    infer_stage = _legacy_stage(contract, "InferInteraction")
    node = next(item for item in graph.nodes if item.id == infer_stage.id)
    runtime = StageRuntime()

    def _blocked(*_args, **_kwargs):
        raise _failure(
            "current authoritative frame does not ground a causal action",
            "interaction_scene_blocked",
            model_calls=1,
            model_elapsed_ms=17,
            controller_elapsed_ms=0,
        )

    monkeypatch.setattr(runtime.interactions, "execute", _blocked)
    context = {
        "run_id": "run-blocked-frame",
        "graph": graph.model_dump(mode="json"),
        "runtime_state": {},
    }
    result = runtime.execute(node, context, threading.Event())

    assert result.ok is False
    assert result.branch == "fail"
    assert result.output["failure_type"] == "interaction_scene_blocked"
    assert result.output["recovery"]["action"] == "fail_stage"
    state = context["runtime_state"]["interaction_state"]
    assert state["failure_type"] == "interaction_scene_blocked"
    assert int(state.get("replan_count") or 0) == 0


def test_stage_runtime_executes_recovery_policy_as_graph_branch(monkeypatch):
    from winpilot.tasking.interaction_stage_runtime import _failure
    from winpilot.tasking.stage_runtime import StageRuntime

    contract, graph = _legacy_interaction_graph(
        task="drag the visible item to its indicated destination",
        max_steps=8,
    )
    infer_stage = _legacy_stage(contract, "InferInteraction")
    node = next(item for item in graph.nodes if item.id == infer_stage.id)
    runtime = StageRuntime()

    def _fail(*_args, **_kwargs):
        raise _failure(
            "scene meaning was ambiguous",
            "semantic_ambiguity",
            model_calls=1,
            model_elapsed_ms=23,
            controller_elapsed_ms=0,
        )

    monkeypatch.setattr(runtime.interactions, "execute", _fail)
    context = {
        "run_id": "run-policy-recovery",
        "graph": graph.model_dump(mode="json"),
        "runtime_state": {},
    }
    result = runtime.execute(node, context, threading.Event())

    assert result.ok is True
    assert result.branch == "recover_reobserve"
    assert result.output["status"] == "recovering"
    assert result.output["model_calls"] == 1
    assert result.output["model_elapsed_ms"] == 23
    assert result.output["interaction_state"]["replan_count"] == 1

    # The same failure after the graph returns to this stage is attempt two,
    # so a one-shot low-risk semantic fallback cannot loop forever.
    second = runtime.execute(node, context, threading.Event())
    assert second.ok is False
    assert second.branch == "fail"
    assert second.output["failure_type"] == "semantic_ambiguity"
    assert second.output["recovery_attempt"] == 2
    assert second.output["recovery"]["retryable"] is False


def test_external_wait_uses_contract_timeout_and_reports_controller_time(
    monkeypatch,
    tmp_path,
):
    import json
    from types import SimpleNamespace

    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine
    import winpilot.tasking.interaction_stage_runtime as runtime_module

    image_path = tmp_path / "external.png"
    post_path = tmp_path / "external-post.png"
    _write_frame(image_path, changed_box=(20, 20, 30, 30))
    _write_frame(post_path, changed_box=(55, 55, 90, 90))
    store = ArtifactStore("run-wait-budget", tmp_path / "artifacts")
    artifact = store.import_file(
        image_path,
        purpose="observation",
        sendable=False,
    )
    post_artifact = store.import_file(
        post_path,
        purpose="observation",
        sendable=False,
    )
    contract = _legacy_interaction_contract(
        persistent=True,
        task="play with me in the current window; I have already started",
        max_steps=8,
    )
    assert contract.session is not None
    contract.session.external_change_timeout_s = 7.0
    stage = _legacy_stage(contract, "WaitForExternalChange")
    decision = _decision(
        action=None,
        turn_owner="user",
        continuation="wait_external",
        observation_artifact_id=artifact.artifact_id,
    )
    context = {
        "run_id": "run-wait-budget",
        "runtime_state": {"interaction_state": {
            "frame_id": 4,
            "planned_action": decision.model_dump(mode="json"),
            "target_binding": {
                "hwnd": 99,
                "title": "target",
                "source": "active_window",
                "frame_region": {
                    "left": 0, "top": 0, "width": 1000, "height": 1000,
                },
            },
        }},
    }
    captured_actions = []

    class _Response:
        text = json.dumps({
            "ok": True,
            "results": [{"data": {"changed": True}}],
        })

    class _Session:
        def __init__(self, **_kwargs):
            self.run_id = ""

        def _remember_target(self, *_args):
            return None

        def dispatch(self, name, payload):
            assert name == "run_action_batch"
            captured_actions.extend(payload["actions"])
            return _Response()

        def close(self):
            return None

    monkeypatch.setattr(runtime_module, "ToolSession", _Session)
    controller_clock = iter((100.0, 100.125))
    monkeypatch.setattr(
        runtime_module,
        "time",
        SimpleNamespace(monotonic=lambda: next(controller_clock)),
    )
    monkeypatch.setattr(
        runtime_module,
        "_normalized_roi_screen",
        lambda *_args, **_kwargs: runtime_module.Region(0, 0, 100, 100),
    )
    engine = InteractionStageEngine(stage_output=_stage_output)
    captures = iter((artifact, post_artifact))
    engine._capture_scene = lambda **_kwargs: next(captures)

    registry = EvidenceRegistry("run-wait-budget")
    result = engine.execute(
        stage,
        contract,
        context,
        threading.Event(),
        time.monotonic(),
        store=store,
        registry=registry,
    )

    assert result.branch == "changed"
    assert captured_actions[0]["timeout_s"] == 7.0
    assert captured_actions[0]["require_persistent"] is True
    assert captured_actions[0]["region"] == {
        "x": 0, "y": 0, "width": 100, "height": 100,
    }
    assert result.output["controller_elapsed_ms"] == 125
    assert result.output["model_elapsed_ms"] == 0
    assert result.output["interaction_state"]["external_change"][
        "authoritative_watch_delta"
    ]["changed"] is True
    records = registry.records()
    assert records[-1]["predicate"] == "interaction.external_change_observed"
    assert records[-1]["verified"] is True


def test_external_wait_rearms_after_transient_watcher_without_artifact_delta(
    monkeypatch,
    tmp_path,
):
    import json
    from types import SimpleNamespace

    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine
    import winpilot.tasking.interaction_stage_runtime as runtime_module

    baseline_path = tmp_path / "transient-baseline.png"
    changed_path = tmp_path / "transient-real-change.png"
    _write_frame(baseline_path, changed_box=(20, 20, 30, 30))
    _write_frame(changed_path, changed_box=(55, 55, 90, 90))
    store = ArtifactStore("run-wait-transient", tmp_path / "artifacts")
    baseline = store.import_file(
        baseline_path, purpose="observation", sendable=False,
    )
    changed_artifact = store.import_file(
        changed_path, purpose="observation", sendable=False,
    )
    contract = _legacy_interaction_contract(
        persistent=True,
        task="play with me in the current window; I have already started",
    )
    assert contract.session is not None
    contract.session.external_change_timeout_s = 7.0
    stage = _legacy_stage(contract, "WaitForExternalChange")
    decision = _decision(
        action=None,
        turn_owner="user",
        continuation="wait_external",
        observation_artifact_id=baseline.artifact_id,
    )
    context = {
        "run_id": "run-wait-transient",
        "runtime_state": {"interaction_state": {
            "frame_id": 4,
            "current_artifact_id": baseline.artifact_id,
            "planned_action": decision.model_dump(mode="json"),
            "target_binding": {
                "hwnd": 99,
                "title": "target",
                "source": "active_window",
                "frame_region": {
                    "left": 0, "top": 0, "width": 1000, "height": 1000,
                },
            },
        }},
    }
    waits = []

    class _Response:
        text = json.dumps({
            "ok": True,
            "results": [{"data": {"changed": True}}],
        })

    class _Session:
        def __init__(self, **_kwargs):
            self.run_id = ""

        def _remember_target(self, *_args):
            return None

        def dispatch(self, name, payload):
            assert name == "run_action_batch"
            waits.extend(payload["actions"])
            return _Response()

        def close(self):
            return None

    monkeypatch.setattr(runtime_module, "ToolSession", _Session)
    clock = iter((100.0, 100.05, 101.0, 101.075))
    monkeypatch.setattr(
        runtime_module,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock)),
    )
    monkeypatch.setattr(
        runtime_module,
        "_normalized_roi_screen",
        lambda *_args, **_kwargs: runtime_module.Region(0, 0, 100, 100),
    )
    engine = InteractionStageEngine(stage_output=_stage_output)
    captures = iter((baseline, baseline, changed_artifact))
    engine._capture_scene = lambda **_kwargs: next(captures)

    result = engine.execute(
        stage,
        contract,
        context,
        threading.Event(),
        time.monotonic(),
        store=store,
        registry=EvidenceRegistry("run-wait-transient"),
    )

    assert result.ok is True
    assert result.branch == "changed"
    assert len(waits) == 2
    assert waits[0]["timeout_s"] == 7.0
    assert waits[1]["timeout_s"] < waits[0]["timeout_s"]
    external = result.output["interaction_state"]["external_change"]
    assert external["wait_result"]["watcher_false_positive_count"] == 1
    assert external["authoritative_watch_delta"]["changed"] is True
    assert external["artifact_id"] == changed_artifact.artifact_id


def test_external_watch_uses_pixel_surface_bound_by_last_verified_action(
    monkeypatch,
    tmp_path,
):
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.interaction_stage_runtime import _external_watch_bounds
    import winpilot.tasking.interaction_stage_runtime as runtime_module

    frame_path = tmp_path / "surface-baseline.png"
    _write_frame(frame_path)
    store = ArtifactStore("run-watch-surface", tmp_path / "artifacts")
    baseline = store.import_file(
        frame_path,
        purpose="observation",
        sendable=False,
    )
    decision = _decision(
        action=None,
        turn_owner="user",
        continuation="wait_external",
        observation_artifact_id=baseline.artifact_id,
    )
    detected = {
        "ok": True,
        "detected_work_surface_frame_bounds": [100, 300, 900, 900],
        "work_surface_frame_bounds": [125, 325, 875, 875],
        "light_ratio": 0.96,
        "source": "authoritative_frame_pixels",
    }
    monkeypatch.setattr(
        runtime_module.image_capture,
        "credible_work_surface_frame_bounds",
        lambda *_args, **_kwargs: dict(detected),
    )
    state = {
        "target_binding": {"client_frame_bounds": [0, 100, 1000, 1000]},
        "last_executed_action": {
            "actions": [{"target_region": [200, 400, 350, 550]}],
        },
    }

    bounds, proof = _external_watch_bounds(state, decision, baseline)

    assert bounds == [100.0, 300.0, 900.0, 900.0]
    assert proof["source"] == "current_pixel_surface_bound_by_verified_action"
    assert state["external_watch_region"]["bounds"] == bounds

    outside_state = {
        "target_binding": {"client_frame_bounds": [0, 0, 1000, 1000]},
        "last_executed_action": {
            "actions": [{"target_region": [5, 5, 50, 50]}],
        },
    }
    fallback, fallback_proof = _external_watch_bounds(
        outside_state,
        decision,
        baseline,
    )
    assert fallback == list(decision.interaction_region)
    assert fallback_proof["reason"] == (
        "detected_surface_not_bound_by_last_verified_action"
    )


def test_external_wait_detects_change_against_previous_authoritative_frame(
    monkeypatch,
    tmp_path,
):
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.evidence import EvidenceRegistry
    from winpilot.tasking.interaction_stage_runtime import InteractionStageEngine
    import winpilot.tasking.interaction_stage_runtime as runtime_module

    baseline_path = tmp_path / "wait-baseline.png"
    fresh_path = tmp_path / "wait-first-fresh.png"
    _write_frame(baseline_path)
    _write_frame(fresh_path, changed_box=(35, 35, 65, 65))
    store = ArtifactStore("run-wait-first-frame", tmp_path / "artifacts")
    baseline = store.import_file(
        baseline_path, purpose="observation", sendable=False,
    )
    fresh = store.import_file(
        fresh_path, purpose="observation", sendable=False,
    )
    contract = _legacy_interaction_contract(
        persistent=True,
        task="play with me in the current window; I have already started",
    )
    stage = _legacy_stage(contract, "WaitForExternalChange")
    decision = _decision(
        action=None,
        turn_owner="user",
        continuation="wait_external",
        frame_id=4,
        observation_artifact_id=baseline.artifact_id,
    )
    target = {
        "hwnd": 99,
        "title": "target",
        "source": "active_window",
        "capture_scope": "target_window",
        "frame_region": {"left": 0, "top": 0, "width": 100, "height": 100},
        "client_region": {"left": 0, "top": 0, "width": 100, "height": 100},
        "client_frame_bounds": [0, 0, 1000, 1000],
    }
    context = {
        "run_id": "run-wait-first-frame",
        "runtime_state": {"interaction_state": {
            "frame_id": 4,
            "current_artifact_id": baseline.artifact_id,
            "target_binding": dict(target),
            "planned_action": decision.model_dump(mode="json"),
        }},
    }

    class ForbiddenToolSession:
        def __init__(self, *args, **kwargs):
            raise AssertionError("already changed first frame must not start a new wait")

    monkeypatch.setattr(runtime_module, "ToolSession", ForbiddenToolSession)
    monkeypatch.setattr(
        runtime_module,
        "_normalized_roi_screen",
        lambda *_args, **_kwargs: runtime_module.Region(0, 0, 100, 100),
    )
    engine = InteractionStageEngine(stage_output=_stage_output)

    def capture_fresh(**_kwargs):
        state = context["runtime_state"]["interaction_state"]
        state.update({
            "frame_id": 5,
            "current_artifact_id": fresh.artifact_id,
            "target_binding": dict(target),
            "last_capture_timing": {"capture_elapsed_ms": 4},
        })
        return fresh

    engine._capture_scene = capture_fresh
    registry = EvidenceRegistry("run-wait-first-frame")
    result = engine.execute(
        stage,
        contract,
        context,
        threading.Event(),
        time.monotonic(),
        store=store,
        registry=registry,
    )

    assert result.ok is True
    assert result.branch == "changed"
    assert result.output["controller_elapsed_ms"] == 0
    assert result.output["artifact_id"] == fresh.artifact_id
    change = result.output["interaction_state"]["external_change"]
    assert change["detection_source"] == "authoritative_baseline_delta"
    assert change["baseline_delta"]["changed"] is True
    assert result.output["interaction_state"][
        "agent_turns_since_external_change"
    ] == 0


def test_controller_no_effect_detection_finds_nested_generic_drag_failure():
    from winpilot.tasking.interaction_stage_runtime import (
        _controller_visual_no_effect,
    )

    assert _controller_visual_no_effect({
        "ok": False,
        "results": [{
            "action": "drag",
            "data": {
                "failure_type": "VISUAL_ACTION_NO_EFFECT",
                "visual_change_detected": False,
            },
        }],
    }) is True
    assert _controller_visual_no_effect({
        "ok": False,
        "failure_type": "WINDOW_FOCUS_ERROR",
    }) is False


def test_controller_path_failure_is_recoverable_only_without_mutation_evidence():
    from winpilot.tasking.interaction_stage_runtime import (
        _controller_visual_no_effect,
    )

    assert _controller_visual_no_effect({
        "ok": False,
        "failure_type": "DRAW_PATH_NOT_REALIZED",
        "visual_change_detected": True,
        "mutation_applied": False,
    }) is True
    assert _controller_visual_no_effect({
        "ok": False,
        "failure_type": "DRAW_PATH_NOT_REALIZED",
        "visual_change_detected": True,
        "mutation_applied": True,
    }) is False


def test_identical_failed_frame_is_detected_before_another_model_plan():
    from types import SimpleNamespace

    from winpilot.tasking.interaction_stage_runtime import (
        _failed_no_effect_repeats_frame,
    )

    state = {"failed_no_effect_mutation": {
        "frame_sha256": "same-pixels",
        "action_signature": "failed-action",
    }}

    assert _failed_no_effect_repeats_frame(
        state, SimpleNamespace(sha256="same-pixels"),
    ) is True
    assert _failed_no_effect_repeats_frame(
        state, SimpleNamespace(sha256="new-pixels"),
    ) is False


def test_spatial_frame_lineage_binds_run_session_epoch_hwnd_and_origin():
    from winpilot.tasking.artifacts import Artifact
    from winpilot.tasking.interaction_stage_runtime import (
        _prove_artifact_frame_lineage,
    )

    target = {
        "hwnd": 77,
        "frame_region": {"left": 10, "top": 20, "width": 640, "height": 480},
    }
    state = {"session_id": "session-a", "frame_id": 5}
    artifact = Artifact(
        artifact_id="art-frame",
        run_id="run-a",
        managed_path="unused.png",
        sha256="pixels",
        mime_type="image/png",
        width=640,
        height=480,
        source_hwnd=77,
        source_scope="window",
        purpose="observation",
        sendable=False,
        metadata={
            "session_id": "session-a",
            "frame_id": 5,
            "frame_region": {"left": 10, "top": 20, "width": 640, "height": 480},
            "region": {"left": 10, "top": 20, "width": 640, "height": 480},
        },
    )

    proof = _prove_artifact_frame_lineage(
        artifact, target=target, state=state, run_id="run-a",
    )

    assert proof["verified"] is True
    assert proof["capture_region"] == target["frame_region"]
    with pytest.raises(ValueError, match="source_hwnd"):
        _prove_artifact_frame_lineage(
            artifact.model_copy(update={"source_hwnd": 78}),
            target=target,
            state=state,
            run_id="run-a",
        )


def test_unrelated_window_change_is_not_new_failed_action_evidence(tmp_path):
    from winpilot.tasking.artifacts import ArtifactStore
    from winpilot.tasking.interaction_stage_runtime import (
        _failed_no_effect_evidence,
    )

    baseline_path = tmp_path / "failed-baseline.png"
    unrelated_path = tmp_path / "failed-unrelated-change.png"
    causal_path = tmp_path / "failed-causal-change.png"
    _write_frame(baseline_path)
    _write_frame(unrelated_path, changed_box=(70, 70, 90, 90))
    _write_frame(causal_path, changed_box=(15, 15, 25, 25))
    store = ArtifactStore("run-no-effect-roi", tmp_path / "artifacts")
    baseline = store.import_file(
        baseline_path, purpose="observation", sendable=False,
    )
    unrelated = store.import_file(
        unrelated_path, purpose="observation", sendable=False,
    )
    causal = store.import_file(
        causal_path, purpose="observation", sendable=False,
    )
    state = {"failed_no_effect_mutation": {
        "artifact_id": baseline.artifact_id,
        "frame_sha256": baseline.sha256,
        "action_signature": "failed-action",
        "relevant_regions": [[100, 100, 300, 300]],
    }}

    unrelated_result = _failed_no_effect_evidence(
        state, unrelated, store=store,
    )
    causal_result = _failed_no_effect_evidence(state, causal, store=store)

    assert unrelated_result["repeats"] is True
    assert unrelated_result["method"] == "causal_regions_unchanged"
    assert causal_result["repeats"] is False
    assert causal_result["method"] == "causal_regions_changed"


def test_pointer_cleanup_uses_run_registry_if_graph_context_loses_local_object():
    from winpilot.tasking.interaction_stage_runtime import (
        InteractionStageEngine,
        _POINTER_SESSIONS,
    )

    closed = []

    class Session:
        def close(self):
            closed.append(True)

    session = Session()
    context = {"run_id": "run-pointer-registry"}
    state = {"pointer_hold": {
        "active": True,
        "button": "left",
        "since_frame_id": 3,
    }}
    _POINTER_SESSIONS[context["run_id"]] = session

    InteractionStageEngine._release_pointer_session(
        context, state, reason="model_plan_invalid",
    )

    assert closed == [True]
    assert context["run_id"] not in _POINTER_SESSIONS
    assert state["pointer_hold"] == {
        "active": False,
        "button": "",
        "since_frame_id": 0,
        "release_reason": "model_plan_invalid",
    }
