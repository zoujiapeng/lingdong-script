"""P0/P1 sidecar tests; none of these tests start a model or touch a desktop."""
from __future__ import annotations

import pytest

from winpilot.vnext.mission import (
    ArtifactRef,
    DuplicateEventError,
    EvidenceRef,
    EvidenceScopeError,
    IllegalTransitionError,
    MissionDefinition,
    MissionEvent,
    MissionReducer,
    MissionStatus,
    ObligationDefinition,
    ObligationStatus,
    StaleRevisionError,
    project_legacy_contract,
    project_legacy_trace,
)


def _evidence(evidence_id: str, obligation_id: str | None = None) -> EvidenceRef:
    return EvidenceRef(
        id=evidence_id,
        mission_id="m1",
        run_id="r1",
        obligation_id=obligation_id,
        kind="oracle",
        trust="deterministic",
        payload_hash=f"hash-{evidence_id}",
    )


def _definition() -> MissionDefinition:
    return MissionDefinition(
        mission_id="m1",
        run_id="r1",
        original_goal="先产生工件，再交付同一个工件",
        obligations=(
            ObligationDefinition(
                id="capture",
                desired_state="当前任务产生了图片工件",
                acceptance_criteria=("图片工件存在",),
                required_capabilities=("visual.interact",),
            ),
            ObligationDefinition(
                id="send",
                desired_state="同一图片已经交付",
                acceptance_criteria=("接收方和工件一致",),
                depends_on=("capture",),
                input_artifact_refs=("artifact-1",),
                required_capabilities=("communication.send_artifact",),
            ),
        ),
    )


def _verify_event(reducer: MissionReducer, obligation_id: str, evidence_id: str) -> MissionEvent:
    return reducer.event(
        "verification_recorded",
        payload={
            "obligation_id": obligation_id,
            "verified": True,
            "satisfied_conditions": ["evidence supplied"],
            "evidence_refs": [evidence_id],
            "verifier_kind": "deterministic",
        },
    )


def test_definition_falls_back_to_one_lossless_obligation() -> None:
    definition = MissionDefinition(
        mission_id="m1", run_id="r1", original_goal="未知的 GUI 目标", obligations=()
    )
    assert len(definition.obligations) == 1
    assert definition.obligations[0].desired_state == "未知的 GUI 目标"
    assert definition.obligations[0].required_capabilities == ("visual.interact",)


def test_reducer_derives_active_obligation_and_completion_from_evidence() -> None:
    definition = _definition()
    evidence = [_evidence("capture-proof", "capture"), _evidence("send-proof", "send"), _evidence("artifact-proof", "capture")]
    reducer = MissionReducer(definition, evidence=evidence)
    assert reducer.projection.active_obligation_id == "capture"
    assert reducer.projection.status is MissionStatus.RUNNING

    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "capture"}))
    reducer.apply(_verify_event(reducer, "capture", "capture-proof"))
    assert reducer.projection.verified_obligation_ids == frozenset({"capture"})
    assert reducer.projection.status is MissionStatus.WAITING_EXTERNAL

    artifact = ArtifactRef(
        artifact_id="artifact-1",
        media_type="image/png",
        sha256="abc",
        producer_obligation_id="capture",
        evidence_ref="artifact-proof",
    )
    reducer.apply(reducer.event("artifact_bound", payload={"binding_name": "artifact-1", "artifact": artifact.to_dict()}))
    assert reducer.projection.active_obligation_id == "send"
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "send"}))
    reducer.apply(_verify_event(reducer, "send", "send-proof"))
    assert reducer.projection.status is MissionStatus.VERIFIED
    assert reducer.projection.active_obligation_id is None


def test_claim_without_evidence_cannot_become_verified() -> None:
    reducer = MissionReducer(_definition(), evidence=[])
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "capture"}))
    with pytest.raises(EvidenceScopeError):
        reducer.apply(reducer.event("verification_recorded", payload={"obligation_id": "capture", "verified": True}))
    assert reducer.projection.status is MissionStatus.RUNNING
    assert reducer.projection.obligation_state("capture").status is ObligationStatus.CLAIMED


def test_stale_revision_and_duplicate_event_are_fail_closed() -> None:
    reducer = MissionReducer(_definition())
    event = reducer.event("obligation_claimed", payload={"obligation_id": "capture"})
    reducer.apply(event)
    with pytest.raises(DuplicateEventError):
        reducer.apply(event)
    stale = MissionEvent(
        type="obligation_claimed",
        mission_id="m1",
        run_id="r1",
        base_revision=0,
        payload={"obligation_id": "capture"},
        event_id="different-event",
    )
    with pytest.raises(StaleRevisionError):
        reducer.apply(stale)


def test_replay_is_deterministic_and_card_only_exposes_active_obligation() -> None:
    definition = _definition()
    evidence = [_evidence("capture-proof", "capture")]
    first = MissionReducer(definition, evidence=evidence)
    events = [
        first.event("obligation_claimed", payload={"obligation_id": "capture"}),
    ]
    first.apply_many(events)
    replayed = MissionReducer.replay(definition, events, evidence=evidence)
    assert replayed.projection.to_dict() == first.projection.to_dict()
    card = replayed.card.to_dict()
    assert card["original_goal"] == definition.original_goal
    assert card["active_obligation"]["id"] == "capture"
    assert "send" not in card["active_obligation"]


def test_replay_accepts_json_event_records() -> None:
    definition = MissionDefinition.from_goal("goal", mission_id="m1", run_id="r1")
    reducer = MissionReducer(definition)
    event = reducer.event("obligation_claimed", payload={"obligation_id": "obligation-1"})
    replayed = MissionReducer.replay(definition, [event.to_dict()])
    assert replayed.projection.obligation_state("obligation-1").status is ObligationStatus.CLAIMED


def test_planner_patch_rejects_route_details_without_model_or_gui_side_effect() -> None:
    reducer = MissionReducer(_definition())
    before = reducer.projection.to_dict()
    with pytest.raises(IllegalTransitionError):
        reducer.apply(reducer.event("planner_patch_proposed", payload={"steps": [{"click": [1, 2]}]}))
    assert reducer.projection.to_dict() == before


def test_legacy_shadow_preserves_goal_and_does_not_copy_gui_steps() -> None:
    legacy = {
        "format": "task-contract/1",
        "task_id": "task-1",
        "task": "打开一个未知应用并完成用户目标",
        "stages": [
            {"id": "s1", "required_capabilities": ["visual.adaptive_interaction"], "obligations": []},
        ],
        "obligations": [{
            "id": "goal",
            "stage_id": "s1",
            "description": "目标状态已经满足",
            "predicate": "goal_verified",
        }],
    }
    definition = project_legacy_contract(legacy, mission_id="m1", run_id="r1")
    assert definition.source == "legacy_shadow"
    assert definition.original_goal == legacy["task"]
    assert definition.obligations[0].required_capabilities == ("visual.interact",)
    assert "steps" not in definition.to_dict()


def test_legacy_trace_shadow_never_turns_done_or_ok_into_verified() -> None:
    definition = MissionDefinition.from_goal("完成未知目标", mission_id="m1", run_id="r1")
    projection = project_legacy_trace(
        definition,
        [
            {"type": "agent_tool", "run_id": "r1", "tool": "click", "ok": True},
            {"type": "agent_done", "run_id": "r1", "status": "done", "result": "done"},
        ],
    )
    assert projection.status is MissionStatus.RUNNING
    assert not projection.verified_obligation_ids
