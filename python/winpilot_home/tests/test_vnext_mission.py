"""P0/P1 sidecar tests; none of these tests start a model or touch a desktop."""
from __future__ import annotations

import random

import pytest

from winpilot.vnext.mission import (
    ArtifactRef,
    DuplicateEventError,
    EvidenceRef,
    EvidencePolicy,
    EvidenceScopeError,
    FailureSummary,
    IllegalTransitionError,
    InvalidMissionDefinition,
    InvalidMissionEvent,
    MissionDefinition,
    MissionEvent,
    MissionReducer,
    MissionStatus,
    MissionCard,
    ObligationSpec,
    ObligationStatus,
    StaleRevisionError,
    TargetBinding,
    TransitionSummary,
    project_legacy_contract,
    project_legacy_trace,
    VerifiedFact,
)


ARTIFACT_SHA256 = "a" * 64
OTHER_ARTIFACT_SHA256 = "b" * 64


def _evidence(
    evidence_id: str,
    obligation_id: str | None = None,
    *,
    mission_id: str = "m1",
    run_id: str = "r1",
) -> EvidenceRef:
    return EvidenceRef(
        id=evidence_id,
        mission_id=mission_id,
        run_id=run_id,
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
            ObligationSpec(
                id="capture",
                desired_state="当前任务产生了图片工件",
                acceptance_criteria=("图片工件存在",),
                required_capabilities=("visual.interact",),
            ),
            ObligationSpec(
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
    criteria = reducer.projection.obligation_definition(obligation_id).acceptance_criteria
    return reducer.event(
        "verification_recorded",
        payload={
            "obligation_id": obligation_id,
            "verified": True,
            "satisfied_criteria": list(criteria),
            "verification_id": f"verification-{obligation_id}-{evidence_id}",
            "evidence_refs": [evidence_id],
            "verifier_kind": "deterministic",
        },
    )


def _required_verification_identity(
    reducer: MissionReducer,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    verification_ids: list[str] = []
    evidence_ids: list[str] = []
    for obligation_id in reducer.definition.stable_topological_order:
        definition = reducer.projection.obligation_definition(obligation_id)
        if not definition.required:
            continue
        state = reducer.projection.obligation_state(obligation_id)
        assert state.verification_id is not None
        verification_ids.append(state.verification_id)
        evidence_ids.extend(state.verified_evidence_refs)
    return tuple(verification_ids), tuple(dict.fromkeys(evidence_ids))


def _mission_verification_payload(
    reducer: MissionReducer,
    evidence_id: str,
    *,
    verification_id: str | None = None,
) -> dict[str, object]:
    accepted_ids, obligation_evidence_ids = _required_verification_identity(reducer)
    return {
        "verified": True,
        "verification_id": verification_id or f"mission-verification-{evidence_id}",
        "accepted_obligation_verification_ids": list(accepted_ids),
        "evidence_refs": [*obligation_evidence_ids, evidence_id],
    }


def _mission_verify_event(reducer: MissionReducer, evidence_id: str) -> MissionEvent:
    return reducer.event(
        "mission_verified",
        payload=_mission_verification_payload(reducer, evidence_id),
    )


def _raw_event(
    reducer: MissionReducer,
    event_type: str,
    payload: dict[str, object],
) -> MissionEvent:
    return MissionEvent(
        kind=event_type,
        mission_id=reducer.definition.mission_id,
        run_id=reducer.definition.run_id,
        base_revision=reducer.projection.revision,
        payload=payload,
    )


def _verified_two_obligation_reducer(
) -> tuple[MissionReducer, tuple[EvidenceRef, ...]]:
    definition = MissionDefinition(
        mission_id="m1",
        run_id="r1",
        original_goal="verify two dependent results",
        obligations=(
            ObligationSpec(
                id="first",
                desired_state="first result",
                acceptance_criteria=("first accepted",),
            ),
            ObligationSpec(
                id="second",
                desired_state="second result",
                acceptance_criteria=("second accepted",),
                depends_on=("first",),
            ),
        ),
    )
    evidence = (
        _evidence("first-proof", "first"),
        _evidence("second-proof", "second"),
        _evidence("mission-proof"),
    )
    reducer = MissionReducer(definition, evidence=evidence)
    for obligation_id, evidence_id in (
        ("first", "first-proof"),
        ("second", "second-proof"),
    ):
        reducer.apply(
            reducer.event(
                "obligation_claimed",
                payload={"obligation_id": obligation_id},
            )
        )
        reducer.apply(_verify_event(reducer, obligation_id, evidence_id))
    assert reducer.projection.status is MissionStatus.AWAITING_MISSION_VERIFICATION
    return reducer, evidence


def test_definition_falls_back_to_one_lossless_obligation() -> None:
    definition = MissionDefinition(
        mission_id="m1", run_id="r1", original_goal="未知的 GUI 目标", obligations=()
    )
    assert len(definition.obligations) == 1
    assert definition.obligations[0].desired_state == "未知的 GUI 目标"
    assert definition.obligations[0].required_capabilities == ("visual.interact",)


def test_definition_wire_metadata_and_event_kind_alias_are_deterministic() -> None:
    definition = _definition()
    assert definition.stable_topological_order == ("capture", "send")
    assert definition.experience_policy == "allow"
    restored = MissionDefinition.from_dict(definition.to_dict())
    assert restored.to_dict() == definition.to_dict()

    event = MissionEvent(
        kind="obligation_claimed",
        mission_id="m1",
        run_id="r1",
        base_revision=0,
        payload={"obligation_id": "capture"},
    )
    assert event.type == event.kind == "obligation_claimed"
    assert MissionEvent.from_dict(event.to_dict()).to_dict() == event.to_dict()


def test_contracts_are_strict_versioned_json_and_canonical_on_wire() -> None:
    with pytest.raises(InvalidMissionDefinition):
        MissionDefinition(mission_id="m1", run_id="r1", original_goal="goal", format="mission-definition/9")
    with pytest.raises(InvalidMissionDefinition):
        MissionDefinition(mission_id="m1", run_id="r1", original_goal="goal", obligations=(
            ObligationSpec(id="optional", desired_state="optional", required=False),
        ))
    with pytest.raises(InvalidMissionDefinition):
        MissionDefinition(mission_id="m1", run_id="r1", original_goal="goal", metadata={"not_json": object()})

    event = MissionEvent(
        kind="transition_recorded",
        mission_id="m1",
        run_id="r1",
        payload={"nested": ["json", 1, True, None]},
    )
    wire = event.to_dict()
    assert "kind" in wire and "type" not in wire
    assert wire["payload"] == {"nested": ["json", 1, True, None]}
    with pytest.raises(InvalidMissionEvent):
        MissionEvent(kind="transition_recorded", mission_id="m1", run_id="r1", payload={"bad": object()})
    with pytest.raises(InvalidMissionEvent):
        MissionEvent(kind="transition_recorded", mission_id="m1", run_id="r1", format="mission-event/9")
    with pytest.raises(InvalidMissionEvent):
        MissionEvent.from_dict({
            "format": "mission-event/0",
            "kind": "transition_recorded",
            "type": "mission_failed",
            "mission_id": "m1",
            "run_id": "r1",
        })


@pytest.mark.parametrize("input_completed", [None, 0, 1, "true", []])
def test_transition_summary_requires_literal_boolean_input_completion(
    input_completed,
) -> None:
    with pytest.raises(InvalidMissionEvent, match="input_completed"):
        TransitionSummary(
            action="click",
            input_completed=input_completed,
            frame_id=1,
            obligation_id="obligation-1",
        )


@pytest.mark.parametrize("frame_id", [-1, True, 1.0, "1"])
def test_transition_summary_requires_non_negative_integer_frame_identity(
    frame_id,
) -> None:
    with pytest.raises(InvalidMissionEvent, match="frame_id"):
        TransitionSummary(
            action="click",
            input_completed=True,
            frame_id=frame_id,
            obligation_id="obligation-1",
        )


@pytest.mark.parametrize("input_completed", [None, 0, 1, "true", []])
def test_raw_transition_event_rejects_non_boolean_input_completion(
    input_completed,
) -> None:
    reducer = MissionReducer(
        MissionDefinition.from_goal("goal", mission_id="m1", run_id="r1")
    )
    before = reducer.projection.to_dict()
    event = _raw_event(
        reducer,
        "transition_recorded",
        {
            "obligation_id": "obligation-1",
            "action": "click",
            "input_completed": input_completed,
            "frame_id": 1,
        },
    )

    with pytest.raises(InvalidMissionEvent, match="input_completed"):
        reducer.apply(event)

    assert reducer.projection.to_dict() == before
    assert reducer.events == ()


def test_raw_transition_event_requires_explicit_input_completion() -> None:
    reducer = MissionReducer(
        MissionDefinition.from_goal("goal", mission_id="m1", run_id="r1")
    )
    before = reducer.projection.to_dict()
    event = _raw_event(
        reducer,
        "transition_recorded",
        {
            "obligation_id": "obligation-1",
            "action": "click",
            "frame_id": 1,
        },
    )

    with pytest.raises(InvalidMissionEvent, match="input_completed"):
        reducer.apply(event)

    assert reducer.projection.to_dict() == before
    assert reducer.events == ()


@pytest.mark.parametrize("frame_id", [-1, True, 1.0, "1"])
def test_raw_transition_event_rejects_noncanonical_frame_identity(frame_id) -> None:
    reducer = MissionReducer(
        MissionDefinition.from_goal("goal", mission_id="m1", run_id="r1")
    )
    before = reducer.projection.to_dict()
    event = _raw_event(
        reducer,
        "transition_recorded",
        {
            "obligation_id": "obligation-1",
            "action": "click",
            "input_completed": True,
            "frame_id": frame_id,
        },
    )

    with pytest.raises(InvalidMissionEvent, match="frame_id"):
        reducer.apply(event)

    assert reducer.projection.to_dict() == before
    assert reducer.events == ()


def test_transition_identity_is_stable_across_wire_replay() -> None:
    definition = MissionDefinition.from_goal("goal", mission_id="m1", run_id="r1")
    reducer = MissionReducer(definition)
    event = _raw_event(
        reducer,
        "transition_recorded",
        {
            "obligation_id": "obligation-1",
            "action": "click",
            "input_completed": True,
            "visible_effect": "dialog opened",
            "frame_id": 7,
        },
    )
    reducer.apply(event)

    replayed = MissionReducer.replay(definition, [event.to_dict()])

    assert replayed.events[0].event_id == event.event_id
    assert replayed.projection.last_transition == reducer.projection.last_transition
    assert replayed.projection.last_transition == TransitionSummary(
        action="click",
        input_completed=True,
        visible_effect="dialog opened",
        frame_id=7,
        obligation_id="obligation-1",
    )


def test_active_selection_uses_dependency_first_stable_topological_order() -> None:
    definition = MissionDefinition(
        mission_id="m1",
        run_id="r1",
        original_goal="ordered goal",
        obligations=(
            ObligationSpec(id="second", desired_state="second", depends_on=("first",)),
            ObligationSpec(id="first", desired_state="first"),
        ),
    )
    assert definition.stable_topological_order == ("first", "second")
    assert MissionReducer(definition).projection.active_obligation_id == "first"


def test_event_level_evidence_refs_are_scoped_by_the_reducer() -> None:
    evidence = [_evidence("capture-proof", "capture")]
    reducer = MissionReducer(_definition(), evidence=evidence)
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "capture"}))
    reducer.apply(
        reducer.event(
            "verification_recorded",
            payload={
                "obligation_id": "capture",
                "verified": True,
                "verification_id": "verification-capture-proof",
                "satisfied_criteria": list(
                    next(item for item in _definition().obligations if item.id == "capture").acceptance_criteria
                ),
            },
            evidence_refs=("capture-proof",),
        )
    )
    assert reducer.projection.verified_obligation_ids == frozenset({"capture"})


def test_reducer_derives_active_obligation_and_completion_from_evidence() -> None:
    definition = _definition()
    evidence = [
        _evidence("capture-proof", "capture"),
        _evidence("send-proof", "send"),
        EvidenceRef(
            id="artifact-proof",
            mission_id="m1",
            run_id="r1",
            obligation_id="capture",
            kind="artifact",
            trust="deterministic",
            payload_hash="hash-artifact-proof",
            artifact_id="artifact-1",
            content_sha256=ARTIFACT_SHA256,
        ),
        _evidence("mission-proof"),
    ]
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
        sha256=ARTIFACT_SHA256,
        producer_obligation_id="capture",
        evidence_ref="artifact-proof",
    )
    reducer.apply(reducer.event("artifact_bound", payload={"binding_name": "artifact-1", "artifact": artifact.to_dict()}))
    assert reducer.projection.active_obligation_id == "send"
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "send"}))
    reducer.apply(_verify_event(reducer, "send", "send-proof"))
    assert reducer.projection.status is MissionStatus.AWAITING_MISSION_VERIFICATION
    reducer.apply(_mission_verify_event(reducer, "mission-proof"))
    assert reducer.projection.status is MissionStatus.VERIFIED
    assert reducer.projection.active_obligation_id is None


def test_claim_without_evidence_cannot_become_verified() -> None:
    reducer = MissionReducer(_definition(), evidence=[])
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "capture"}))
    with pytest.raises(EvidenceScopeError):
        reducer.apply(reducer.event("verification_recorded", payload={"obligation_id": "capture", "verified": True}))
    assert reducer.projection.status is MissionStatus.RUNNING
    assert reducer.projection.obligation_state("capture").status is ObligationStatus.CLAIMED


def test_verification_requires_all_criteria_and_persists_verification_id() -> None:
    definition = MissionDefinition(
        mission_id="m1",
        run_id="r1",
        original_goal="goal",
        obligations=(ObligationSpec(
            id="goal",
            desired_state="goal",
            acceptance_criteria=("first condition", "second condition"),
        ),),
    )
    proof = _evidence("goal-proof", "goal")
    reducer = MissionReducer(definition, evidence=[proof])
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "goal"}))
    with pytest.raises(IllegalTransitionError):
        reducer.apply(reducer.event(
            "verification_recorded",
            payload={
                "obligation_id": "goal",
                "verified": True,
                "verification_id": "v-missing",
                "satisfied_criteria": ["first condition"],
                "evidence_refs": [proof.id],
            },
        ))
    event = reducer.event(
        "verification_recorded",
        payload={
            "obligation_id": "goal",
            "verified": True,
            "verification_id": "v-complete",
            "satisfied_criteria": ["first condition", "second condition"],
            "evidence_refs": [proof.id],
        },
    )
    reducer.apply(event)
    state = reducer.projection.obligation_state("goal")
    assert state.status is ObligationStatus.VERIFIED
    assert state.verification_id == "v-complete"


def test_evidence_registry_is_read_again_on_each_apply() -> None:
    definition = MissionDefinition.from_goal("goal", mission_id="m1", run_id="r1")
    registry: list[EvidenceRef] = []
    reducer = MissionReducer(definition, evidence=registry)
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "obligation-1"}))
    proof = _evidence("late-proof", "obligation-1")
    verify = reducer.event(
        "verification_recorded",
        payload={
            "obligation_id": "obligation-1",
            "verified": True,
            "verification_id": "late-verification",
            "satisfied_criteria": ["goal"],
            "evidence_refs": [proof.id],
        },
    )
    with pytest.raises(EvidenceScopeError):
        reducer.apply(verify)
    registry.append(proof)
    reducer.apply(verify)
    assert reducer.projection.obligation_state("obligation-1").verification_id == "late-verification"


def test_verification_enforces_evidence_kind_and_target_hwnd() -> None:
    definition = MissionDefinition(
        mission_id="m1",
        run_id="r1",
        original_goal="goal",
        obligations=(ObligationSpec(
            id="goal",
            desired_state="goal",
            evidence_policy=EvidencePolicy(required_evidence_kinds=("visual",)),
        ),),
    )
    proof = _evidence("oracle-proof", "goal")
    reducer = MissionReducer(definition, evidence=[proof])
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "goal"}))
    with pytest.raises(EvidenceScopeError):
        reducer.apply(reducer.event(
            "verification_recorded",
            payload={
                "obligation_id": "goal",
                "verified": True,
                "verification_id": "wrong-kind",
                "satisfied_criteria": ["goal"],
                "evidence_refs": [proof.id],
            },
        ))

    with pytest.raises(InvalidMissionEvent):
        reducer.apply(reducer.event(
            "target_bound",
            payload={
                "binding_id": "window",
                "hwnd": 0,
                "process_id": 1,
                "executable_identity": "app",
                "title_snapshot": "window",
                "established_by_evidence_ref": proof.id,
                "epoch": 0,
            },
            evidence_refs=[proof.id],
        ))


@pytest.mark.parametrize("terminal_event", ["mission_failed", "mission_canceled"])
def test_failed_and_canceled_missions_are_terminal(terminal_event: str) -> None:
    reducer = MissionReducer(MissionDefinition.from_goal("goal", mission_id="m1", run_id="r1"))
    reducer.apply(reducer.event(terminal_event, payload={"reason": terminal_event}))
    with pytest.raises(IllegalTransitionError):
        reducer.apply(reducer.event("transition_recorded", payload={"action": "late"}))


def test_verified_mission_is_terminal_and_blocked_status_is_not_recomputed_away() -> None:
    definition = MissionDefinition.from_goal("goal", mission_id="m1", run_id="r1")
    reducer = MissionReducer(
        definition,
        evidence=[
            _evidence("verified-proof", "obligation-1"),
            _evidence("mission-proof"),
        ],
    )
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "obligation-1"}))
    reducer.apply(reducer.event(
        "verification_recorded",
        payload={
            "obligation_id": "obligation-1",
            "verified": True,
            "verification_id": "verified-1",
            "satisfied_criteria": ["goal"],
            "evidence_refs": ["verified-proof"],
        },
    ))
    assert reducer.projection.status is MissionStatus.AWAITING_MISSION_VERIFICATION
    reducer.apply(_mission_verify_event(reducer, "mission-proof"))
    assert reducer.projection.status is MissionStatus.VERIFIED
    with pytest.raises(IllegalTransitionError):
        reducer.apply(reducer.event("transition_recorded", payload={"action": "late"}))

    blocked = MissionReducer(MissionDefinition.from_goal("blocked", mission_id="m2", run_id="r2"))
    blocked.apply(blocked.event("obligation_blocked", payload={"obligation_id": "obligation-1", "reason": "external"}))
    with pytest.raises(IllegalTransitionError):
        blocked.apply(blocked.event("transition_recorded", payload={"action": "diagnostic"}))
    assert blocked.projection.status is MissionStatus.BLOCKED

    waiting = MissionReducer(
        _definition(),
        evidence=[_evidence("capture-proof", "capture")],
    )
    waiting.apply(waiting.event("obligation_claimed", payload={"obligation_id": "capture"}))
    waiting.apply(_verify_event(waiting, "capture", "capture-proof"))
    assert waiting.projection.status is MissionStatus.WAITING_EXTERNAL
    with pytest.raises(IllegalTransitionError):
        waiting.apply(waiting.event("transition_recorded", payload={"action": "diagnostic"}))
    assert waiting.projection.status is MissionStatus.WAITING_EXTERNAL


@pytest.mark.parametrize("event_type", [
    "approval_requested", "approval_decided", "external_change_armed",
    "external_change_recorded", "planner_requested", "planner_patch_proposed",
    "planner_patch_accepted", "planner_patch_rejected", "user_goal_revised",
])
def test_incomplete_cross_role_events_fail_closed(event_type: str) -> None:
    reducer = MissionReducer(MissionDefinition.from_goal("goal", mission_id="m1", run_id="r1"))
    before = reducer.projection.to_dict()
    with pytest.raises(InvalidMissionEvent):
        reducer.apply(reducer.event(event_type, payload={}))
    assert reducer.projection.to_dict() == before


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


def test_mission_card_filters_unrelated_facts_and_artifacts() -> None:
    definition = _definition()
    projection = MissionReducer(definition).projection
    capture_fact = {
        "id": "capture-fact",
        "statement": "capture is visible",
        "evidence_refs": ["capture-proof"],
        "scope": "capture",
        "valid_for_obligation_ids": ["capture"],
    }
    send_fact = {
        "id": "send-fact",
        "statement": "send is visible",
        "evidence_refs": ["send-proof"],
        "scope": "send",
        "valid_for_obligation_ids": ["send"],
    }
    from dataclasses import replace

    projection = replace(
        projection,
        verified_facts=(
            projection.verified_facts
            + (VerifiedFact(**capture_fact), VerifiedFact(**send_fact))
        ),
        artifact_bindings={
            "needed": ArtifactRef("artifact-1", "image/png", ARTIFACT_SHA256, "send", "proof"),
            "unrelated": ArtifactRef("artifact-2", "image/png", OTHER_ARTIFACT_SHA256, "capture", "proof"),
        },
    )
    # Set active obligation to send so the projection rule is observable.
    projection = replace(
        projection,
        active_obligation_id="send",
        obligation_states=tuple(
            replace(item, status=ObligationStatus.ACTIVE if item.id == "send" else item.status)
            for item in projection.obligation_states
        ),
    )
    card = MissionCard.from_projection(projection)
    assert [fact.id for fact in card.verified_facts] == ["send-fact"]
    assert [artifact.artifact_id for artifact in card.artifact_bindings] == ["artifact-1"]


def test_randomized_dag_replay_is_stable() -> None:
    rng = random.Random(20260721)
    for sample in range(12):
        ids = [f"o-{sample}-{index}" for index in range(5)]
        obligations = []
        for index, obligation_id in enumerate(ids):
            dependencies = tuple(item for item in ids[:index] if rng.random() < 0.35)
            obligations.append(ObligationSpec(id=obligation_id, desired_state=obligation_id, depends_on=dependencies))
        rng.shuffle(obligations)
        definition = MissionDefinition(
            mission_id=f"m-{sample}", run_id=f"r-{sample}", original_goal="random goal",
            obligations=tuple(obligations),
        )
        evidence = [
            _evidence(
                f"proof-{sample}-{item}", item,
                mission_id=f"m-{sample}", run_id=f"r-{sample}",
            )
            for item in ids
        ] + [
            _evidence(
                f"mission-proof-{sample}", None,
                mission_id=f"m-{sample}", run_id=f"r-{sample}",
            )
        ]
        reducer = MissionReducer(definition, evidence=evidence)
        events: list[MissionEvent] = []
        for obligation_id in definition.stable_topological_order:
            claim = reducer.event("obligation_claimed", payload={"obligation_id": obligation_id})
            reducer.apply(claim)
            events.append(claim)
            verification = _verify_event(reducer, obligation_id, f"proof-{sample}-{obligation_id}")
            reducer.apply(verification)
            events.append(verification)
        mission_verification = _mission_verify_event(reducer, f"mission-proof-{sample}")
        reducer.apply(mission_verification)
        events.append(mission_verification)
        replayed = MissionReducer.replay(definition, [event.to_dict() for event in events], evidence=evidence)
        assert replayed.projection.to_dict() == reducer.projection.to_dict()
        assert reducer.projection.status is MissionStatus.VERIFIED


def test_unimplemented_planner_patch_fails_closed_without_model_or_gui_side_effect() -> None:
    reducer = MissionReducer(_definition())
    before = reducer.projection.to_dict()
    with pytest.raises(InvalidMissionEvent):
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


def test_real_task_contract_bridge_collapses_unsafe_execute_desktop_split() -> None:
    from winpilot.tasking.contracts import (
        CompletionObligation,
        EvidenceProviderSpec,
        StageType,
        TaskContract,
        TaskIntent,
        TaskStage,
    )

    provider = EvidenceProviderSpec(id="visual", capabilities=["visual.interact"])
    first = CompletionObligation(id="first", description="first", stage_id="s1", evidence_providers=[provider])
    second = CompletionObligation(id="second", description="second", stage_id="s2", evidence_providers=[provider])
    contract = TaskContract(
        task_id="real-contract",
        task="complete two desktop results",
        intent=TaskIntent(original_text="complete two desktop results"),
        stages=[
            TaskStage(id="s1", type=StageType.EXECUTE_DESKTOP_TASK, name="first", obligations=[first]),
            TaskStage(id="s2", type=StageType.EXECUTE_DESKTOP_TASK, name="second", obligations=[second]),
        ],
        obligations=[first, second],
        final_conditions=[],
    )
    definition = project_legacy_contract(contract, mission_id="m1", run_id="r1")
    assert len(definition.obligations) == 1
    assert definition.obligations[0].desired_state == contract.task

    safe_contract = contract.model_copy(deep=True)
    safe_contract.stages[1].depends_on = ["s1"]
    safe_definition = project_legacy_contract(safe_contract, mission_id="m2", run_id="r2")
    assert len(safe_definition.obligations) == 2
    assert safe_definition.obligations[1].depends_on == ("first",)


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


@pytest.mark.parametrize(
    ("claim_fingerprint", "error_type"),
    [
        (None, IllegalTransitionError),
        ("stale-claim-fingerprint", StaleRevisionError),
    ],
)
def test_raw_verification_event_requires_exact_runtime_claim_fingerprint(
    claim_fingerprint,
    error_type,
) -> None:
    definition = MissionDefinition.from_goal("goal", mission_id="m1", run_id="r1")
    proof = _evidence("goal-proof", "obligation-1")
    reducer = MissionReducer(definition, evidence=[proof])
    reducer.apply(
        reducer.event(
            "obligation_claimed",
            payload={"obligation_id": "obligation-1"},
        )
    )
    before = reducer.projection.to_dict()
    payload: dict[str, object] = {
        "obligation_id": "obligation-1",
        "verified": True,
        "verification_id": "goal-verification",
        "satisfied_criteria": ["goal"],
        "evidence_refs": [proof.id],
    }
    if claim_fingerprint is not None:
        payload["claim_fingerprint"] = claim_fingerprint

    with pytest.raises(error_type):
        reducer.apply(_raw_event(reducer, "verification_recorded", payload))

    assert reducer.projection.to_dict() == before
    assert len(reducer.events) == 1


@pytest.mark.parametrize("verified", [None, 0, 1, "true", [], {}])
def test_raw_verification_event_rejects_non_boolean_verdict(verified) -> None:
    definition = MissionDefinition.from_goal("goal", mission_id="m1", run_id="r1")
    proof = _evidence("goal-proof", "obligation-1")
    reducer = MissionReducer(definition, evidence=[proof])
    reducer.apply(
        reducer.event(
            "obligation_claimed",
            payload={"obligation_id": "obligation-1"},
        )
    )
    state = reducer.projection.obligation_state("obligation-1")
    before = reducer.projection.to_dict()
    event = _raw_event(
        reducer,
        "verification_recorded",
        {
            "obligation_id": "obligation-1",
            "claim_fingerprint": state.claim_fingerprint,
            "verified": verified,
            "verification_id": "goal-verification",
            "satisfied_criteria": ["goal"],
            "evidence_refs": [proof.id],
        },
    )

    with pytest.raises(InvalidMissionEvent, match="verdict must be boolean"):
        reducer.apply(event)

    assert reducer.projection.to_dict() == before
    assert len(reducer.events) == 1


@pytest.mark.parametrize(
    "accepted_ids",
    [
        None,
        ["verification-first-first-proof"],
        ["verification-second-second-proof", "verification-first-first-proof"],
        ["verification-first-first-proof", "verification-first-first-proof"],
    ],
    ids=["missing-field", "missing-id", "wrong-order", "duplicate"],
)
def test_raw_mission_verification_requires_exact_ordered_accepted_verification_ids(
    accepted_ids,
) -> None:
    reducer, _evidence_registry = _verified_two_obligation_reducer()
    payload = _mission_verification_payload(reducer, "mission-proof")
    if accepted_ids is None:
        payload.pop("accepted_obligation_verification_ids")
    else:
        payload["accepted_obligation_verification_ids"] = accepted_ids
    before = reducer.projection.to_dict()

    with pytest.raises(IllegalTransitionError, match="exact accepted"):
        reducer.apply(_raw_event(reducer, "mission_verified", payload))

    assert reducer.projection.to_dict() == before


def test_raw_mission_verification_requires_every_obligation_evidence() -> None:
    reducer, _evidence_registry = _verified_two_obligation_reducer()
    payload = _mission_verification_payload(reducer, "mission-proof")
    payload["evidence_refs"] = ["first-proof", "mission-proof"]
    before = reducer.projection.to_dict()

    with pytest.raises(EvidenceScopeError, match="every required obligation"):
        reducer.apply(_raw_event(reducer, "mission_verified", payload))

    assert reducer.projection.to_dict() == before


def test_raw_mission_verification_requires_independent_mission_level_evidence() -> None:
    reducer, _evidence_registry = _verified_two_obligation_reducer()
    payload = _mission_verification_payload(reducer, "mission-proof")
    payload["evidence_refs"] = ["first-proof", "second-proof"]
    before = reducer.projection.to_dict()

    with pytest.raises(EvidenceScopeError, match="mission-level evidence"):
        reducer.apply(_raw_event(reducer, "mission_verified", payload))

    assert reducer.projection.to_dict() == before


@pytest.mark.parametrize("verified", [False, None, 0, 1, "true", []])
def test_raw_mission_verification_requires_literal_true_verdict(verified) -> None:
    reducer, _evidence_registry = _verified_two_obligation_reducer()
    payload = _mission_verification_payload(reducer, "mission-proof")
    payload["verified"] = verified
    before = reducer.projection.to_dict()

    with pytest.raises(IllegalTransitionError, match="boolean true"):
        reducer.apply(_raw_event(reducer, "mission_verified", payload))

    assert reducer.projection.to_dict() == before


def test_mission_verification_identities_are_stable_across_full_wire_replay() -> None:
    reducer, evidence = _verified_two_obligation_reducer()
    mission_event = _raw_event(
        reducer,
        "mission_verified",
        _mission_verification_payload(
            reducer,
            "mission-proof",
            verification_id="mission-verification",
        ),
    )
    reducer.apply(mission_event)
    wire_events = [event.to_dict() for event in reducer.events]

    replayed = MissionReducer.replay(reducer.definition, wire_events, evidence=evidence)

    assert replayed.projection.to_dict() == reducer.projection.to_dict()
    assert [event.event_id for event in replayed.events] == [
        event.event_id for event in reducer.events
    ]
    assert replayed.projection.mission_verification_id == "mission-verification"
    for obligation_id in ("first", "second"):
        expected = reducer.projection.obligation_state(obligation_id)
        actual = replayed.projection.obligation_state(obligation_id)
        assert actual.verification_id == expected.verification_id
        assert actual.claim_fingerprint == expected.claim_fingerprint
        assert actual.verified_claim_fingerprint == expected.verified_claim_fingerprint
        assert actual.verified_evidence_refs == expected.verified_evidence_refs


def test_mission_completion_requires_independent_run_scoped_verification() -> None:
    definition = MissionDefinition.from_goal("goal", mission_id="m1", run_id="r1")
    obligation_proof = _evidence("obligation-proof", "obligation-1")
    mission_proof = _evidence("mission-proof")
    reducer = MissionReducer(definition, evidence=[obligation_proof, mission_proof])
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "obligation-1"}))
    reducer.apply(reducer.event(
        "verification_recorded",
        payload={
            "obligation_id": "obligation-1",
            "verified": True,
            "verification_id": "obligation-verification",
            "satisfied_criteria": ["goal"],
            "evidence_refs": [obligation_proof.id],
        },
    ))
    assert reducer.projection.status is MissionStatus.AWAITING_MISSION_VERIFICATION
    assert reducer.projection.mission_verification_id is None
    missing_evidence_payload = _mission_verification_payload(
        reducer,
        mission_proof.id,
        verification_id="mission-without-proof",
    )
    missing_evidence_payload["evidence_refs"] = []
    with pytest.raises(EvidenceScopeError):
        reducer.apply(
            _raw_event(reducer, "mission_verified", missing_evidence_payload)
        )
    reducer.apply(
        reducer.event(
            "mission_verified",
            payload=_mission_verification_payload(
                reducer,
                mission_proof.id,
                verification_id="mission-verification",
            ),
        )
    )
    assert reducer.projection.status is MissionStatus.VERIFIED
    assert reducer.projection.mission_verification_id == "mission-verification"
    with pytest.raises(IllegalTransitionError):
        reducer.apply(
            _raw_event(
                reducer,
                "mission_verified",
                {
                    "verified": True,
                    "verification_id": "another-mission-verification",
                    "accepted_obligation_verification_ids": [
                        "obligation-verification"
                    ],
                    "evidence_refs": [obligation_proof.id, mission_proof.id],
                },
            )
        )


def test_claim_fingerprint_prevents_duplicate_verification_and_replay_is_stable() -> None:
    definition = MissionDefinition(
        mission_id="m1",
        run_id="r1",
        original_goal="goal",
        obligations=(ObligationSpec(
            id="goal",
            desired_state="goal",
            evidence_policy=EvidencePolicy(max_verification_attempts=2, uncertain_policy="collect_more"),
        ),),
    )
    proof = _evidence("goal-proof", "goal")
    mission_proof = _evidence("mission-proof")
    reducer = MissionReducer(definition, evidence=[proof, mission_proof])
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "goal"}))
    first_state = reducer.projection.obligation_state("goal")
    assert first_state.activation_attempt_count == 1
    assert first_state.claim_fingerprint
    with pytest.raises(DuplicateEventError):
        reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "goal"}))
    reducer.apply(reducer.event(
        "verification_recorded",
        payload={
            "obligation_id": "goal",
            "verified": False,
            "reason": "not enough visual evidence",
        },
    ))
    failed_state = reducer.projection.obligation_state("goal")
    assert failed_state.verification_attempt_count == 1
    with pytest.raises(DuplicateEventError):
        reducer.apply(reducer.event(
            "verification_recorded",
            payload={"obligation_id": "goal", "verified": False},
        ))
    reducer.apply(reducer.event(
        "transition_recorded",
        payload={
            "obligation_id": "goal",
            "action": "observe",
            "input_completed": False,
            "frame_id": 2,
        },
    ))
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "goal"}))
    second_state = reducer.projection.obligation_state("goal")
    assert second_state.claim_fingerprint != first_state.claim_fingerprint
    reducer.apply(reducer.event(
        "verification_recorded",
        payload={
            "obligation_id": "goal",
            "verified": True,
            "verification_id": "goal-verification",
            "satisfied_criteria": ["goal"],
            "evidence_refs": [proof.id],
        },
    ))
    assert reducer.projection.status is MissionStatus.AWAITING_MISSION_VERIFICATION
    reducer.apply(
        reducer.event(
            "mission_verified",
            payload=_mission_verification_payload(
                reducer,
                mission_proof.id,
                verification_id="mission-verification",
            ),
        )
    )
    assert reducer.projection.status is MissionStatus.VERIFIED


@pytest.mark.parametrize("policy", ["blocked", "fail", "ask_user", "collect_more"])
def test_uncertain_verification_policy_is_explicit(policy: str) -> None:
    definition = MissionDefinition(
        mission_id="m1",
        run_id="r1",
        original_goal="goal",
        obligations=(ObligationSpec(
            id="goal",
            desired_state="goal",
            evidence_policy=EvidencePolicy(max_verification_attempts=1, uncertain_policy=policy),
        ),),
    )
    reducer = MissionReducer(definition)
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "goal"}))
    reducer.apply(reducer.event(
        "verification_recorded",
        payload={"obligation_id": "goal", "verified": False, "reason": "uncertain"},
    ))
    state = reducer.projection.obligation_state("goal")
    assert state.verification_attempt_count == 1
    if policy == "fail":
        assert state.status is ObligationStatus.BLOCKED
        assert reducer.projection.status is MissionStatus.FAILED
    elif policy == "blocked":
        assert state.status is ObligationStatus.BLOCKED
        assert reducer.projection.status is MissionStatus.BLOCKED
    else:
        assert state.status is ObligationStatus.ACTIVE
        assert reducer.projection.status is MissionStatus.WAITING_EXTERNAL


def test_fail_policy_waits_until_verification_attempts_are_exhausted() -> None:
    definition = MissionDefinition(
        mission_id="m1",
        run_id="r1",
        original_goal="goal",
        obligations=(ObligationSpec(
            id="goal",
            desired_state="goal",
            evidence_policy=EvidencePolicy(
                max_verification_attempts=2,
                uncertain_policy="fail",
            ),
        ),),
    )
    reducer = MissionReducer(definition)
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "goal"}))
    reducer.apply(reducer.event(
        "verification_recorded",
        payload={"obligation_id": "goal", "verified": False, "reason": "first rejection"},
    ))
    assert reducer.projection.obligation_state("goal").status is ObligationStatus.ACTIVE
    assert reducer.projection.status is MissionStatus.RUNNING

    reducer.apply(reducer.event(
        "transition_recorded",
        payload={
            "obligation_id": "goal",
            "action": "observe",
            "input_completed": False,
            "frame_id": 2,
        },
    ))
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "goal"}))
    reducer.apply(reducer.event(
        "verification_recorded",
        payload={"obligation_id": "goal", "verified": False, "reason": "second rejection"},
    ))
    assert reducer.projection.obligation_state("goal").verification_attempt_count == 2
    assert reducer.projection.obligation_state("goal").status is ObligationStatus.BLOCKED
    assert reducer.projection.status is MissionStatus.FAILED


def test_visual_verifier_permission_and_identity_relationships_are_fail_closed() -> None:
    definition = MissionDefinition(
        mission_id="m1",
        run_id="r1",
        original_goal="goal",
        obligations=(ObligationSpec(
            id="goal",
            desired_state="goal",
            evidence_policy=EvidencePolicy(allow_independent_visual_verifier=False),
        ),),
    )
    proof = _evidence("goal-proof", "goal")
    reducer = MissionReducer(definition, evidence=[proof])
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "goal"}))
    with pytest.raises(EvidenceScopeError):
        reducer.apply(reducer.event(
            "verification_recorded",
            payload={
                "obligation_id": "goal",
                "verified": True,
                "verification_id": "visual-verification",
                "verifier_kind": "visual_verifier",
                "satisfied_criteria": ["goal"],
                "evidence_refs": [proof.id],
            },
        ))
    with pytest.raises(EvidenceScopeError):
        VerifiedFact(
            id="fact",
            statement="fact",
            evidence_refs=(),
            scope="goal",
        )
    with pytest.raises(InvalidMissionEvent):
        ArtifactRef("", "image/png", ARTIFACT_SHA256, "goal", proof.id)
    with pytest.raises(InvalidMissionEvent):
        TargetBinding("", 1, 1, "app", "title", proof.id, 0)


def test_visual_permission_uses_evidence_trust_not_payload_self_report() -> None:
    definition = MissionDefinition(
        mission_id="m1",
        run_id="r1",
        original_goal="goal",
        obligations=(ObligationSpec(
            id="goal",
            desired_state="goal",
            evidence_policy=EvidencePolicy(allow_independent_visual_verifier=True),
        ),),
    )
    deterministic = _evidence("deterministic-proof", "goal")
    reducer = MissionReducer(definition, evidence=[deterministic])
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "goal"}))
    with pytest.raises(EvidenceScopeError, match="not backed"):
        reducer.apply(reducer.event(
            "verification_recorded",
            payload={
                "obligation_id": "goal",
                "verified": True,
                "verification_id": "payload-only-visual",
                "verifier_kind": "visual_verifier",
                "satisfied_criteria": ["goal"],
                "evidence_refs": [deterministic.id],
            },
        ))

    visual = EvidenceRef(
        id="visual-proof",
        mission_id="m1",
        run_id="r1",
        obligation_id="goal",
        kind="visual-assessment",
        trust="independent_visual_model",
        payload_hash="hash-visual-proof",
    )
    visual_reducer = MissionReducer(definition, evidence=[visual])
    visual_reducer.apply(visual_reducer.event("obligation_claimed", payload={"obligation_id": "goal"}))
    visual_reducer.apply(visual_reducer.event(
        "verification_recorded",
        payload={
            "obligation_id": "goal",
            "verified": True,
            "verification_id": "real-visual-proof",
            "verifier_kind": "deterministic",
            "satisfied_criteria": ["goal"],
            "evidence_refs": [visual.id],
        },
    ))
    assert visual_reducer.projection.obligation_state("goal").status is ObligationStatus.VERIFIED


def test_real_visual_evidence_still_requires_policy_permission() -> None:
    definition = MissionDefinition(
        mission_id="m1",
        run_id="r1",
        original_goal="goal",
        obligations=(ObligationSpec(
            id="goal",
            desired_state="goal",
            evidence_policy=EvidencePolicy(allow_independent_visual_verifier=False),
        ),),
    )
    visual = EvidenceRef(
        id="visual-proof",
        mission_id="m1",
        run_id="r1",
        obligation_id="goal",
        kind="visual-assessment",
        trust="independent_visual_model",
        payload_hash="hash-visual-proof",
    )
    reducer = MissionReducer(definition, evidence=[visual])
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "goal"}))
    with pytest.raises(EvidenceScopeError, match="not permitted"):
        reducer.apply(reducer.event(
            "verification_recorded",
            payload={
                "obligation_id": "goal",
                "verified": True,
                "verification_id": "visual-verification",
                "verifier_kind": "visual_verifier",
                "satisfied_criteria": ["goal"],
                "evidence_refs": [visual.id],
            },
        ))


@pytest.mark.parametrize(
    "evidence_artifact_id,evidence_sha256,error",
    (
        ("other-artifact", ARTIFACT_SHA256, "does not identify"),
        ("artifact-1", OTHER_ARTIFACT_SHA256, "content hash"),
    ),
)
def test_artifact_binding_checks_id_and_hash_independently(
    evidence_artifact_id: str,
    evidence_sha256: str,
    error: str,
) -> None:
    artifact_proof = EvidenceRef(
        id="artifact-proof",
        mission_id="m1",
        run_id="r1",
        obligation_id="capture",
        artifact_id=evidence_artifact_id,
        kind="artifact",
        trust="deterministic",
        payload_hash="hash-artifact-proof",
        content_sha256=evidence_sha256,
    )
    capture_proof = _evidence("capture-proof", "capture")
    reducer = MissionReducer(_definition(), evidence=[capture_proof, artifact_proof])
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "capture"}))
    reducer.apply(_verify_event(reducer, "capture", "capture-proof"))
    artifact = ArtifactRef(
        "artifact-1", "image/png", ARTIFACT_SHA256, "capture", "artifact-proof"
    )
    with pytest.raises(EvidenceScopeError, match=error):
        reducer.apply(reducer.event(
            "artifact_bound",
            payload={"binding_name": "artifact-1", "artifact": artifact.to_dict()},
        ))


def test_artifact_target_and_fact_evidence_cannot_cross_identities() -> None:
    artifact_proof = EvidenceRef(
        id="artifact-proof",
        mission_id="m1",
        run_id="r1",
        obligation_id="capture",
        artifact_id="other-artifact",
        kind="artifact",
        trust="deterministic",
        payload_hash="hash-artifact-proof",
        content_sha256=ARTIFACT_SHA256,
    )
    capture_proof = _evidence("capture-proof", "capture")
    reducer = MissionReducer(_definition(), evidence=[capture_proof, artifact_proof])
    reducer.apply(reducer.event("obligation_claimed", payload={"obligation_id": "capture"}))
    reducer.apply(_verify_event(reducer, "capture", "capture-proof"))
    with pytest.raises(EvidenceScopeError):
        reducer.apply(reducer.event(
            "artifact_bound",
            payload={
                "binding_name": "artifact-1",
                "artifact": ArtifactRef(
                    "artifact-1", "image/png", OTHER_ARTIFACT_SHA256, "capture", "artifact-proof"
                ).to_dict(),
            },
        ))

    target_evidence = EvidenceRef(
        id="target-proof",
        mission_id="m2",
        run_id="r2",
        artifact_id="artifact-1",
        content_sha256=OTHER_ARTIFACT_SHA256,
        kind="target",
        trust="deterministic",
        payload_hash="hash-target-proof",
    )
    target_reducer = MissionReducer(
        MissionDefinition.from_goal("goal", mission_id="m2", run_id="r2"),
        evidence=[target_evidence],
    )
    with pytest.raises(EvidenceScopeError):
        target_reducer.apply(target_reducer.event(
            "target_bound",
            payload={
                "binding_id": "window",
                "hwnd": 42,
                "process_id": 1,
                "executable_identity": "app",
                "title_snapshot": "window",
                "established_by_evidence_ref": "target-proof",
            },
        ))

    fact_reducer = MissionReducer(
        MissionDefinition.from_goal("goal", mission_id="m3", run_id="r3"),
        evidence=[_evidence("wrong-obligation-proof", "other", mission_id="m3", run_id="r3")],
    )
    with pytest.raises(EvidenceScopeError):
        fact_reducer.apply(fact_reducer.event(
            "fact_recorded",
            payload={
                "fact": {
                    "id": "fact",
                    "statement": "goal is visible",
                    "evidence_refs": ["wrong-obligation-proof"],
                    "scope": "obligation",
                    "valid_for_obligation_ids": ["obligation-1"],
                },
                "obligation_id": "obligation-1",
            },
        ))


def test_transition_failure_feedback_is_obligation_bound_in_mission_card() -> None:
    from dataclasses import replace

    projection = MissionReducer(_definition()).projection
    projection = replace(
        projection,
        last_transition=TransitionSummary(
            action="old",
            input_completed=True,
            obligation_id="send",
        ),
        failure_summary=FailureSummary("old", "old", obligation_id="send"),
    )
    assert projection.active_obligation_id == "capture"
    card = MissionCard.from_projection(projection)
    assert card.last_transition is None
    assert card.failure_summary is None


def test_legacy_split_requires_all_rows_covered_and_unknown_capabilities_are_metadata() -> None:
    legacy = {
        "task": "three results",
        "obligations": [
            {"id": "one", "description": "one", "depends_on": [], "required_capabilities": ["legacy.magic"]},
            {"id": "two", "description": "two", "depends_on": ["one"]},
            {"id": "three", "description": "three", "depends_on": []},
        ],
    }
    fallback = project_legacy_contract(legacy, mission_id="m1", run_id="r1")
    assert len(fallback.obligations) == 1
    assert fallback.metadata["legacy_split_fallback"] == "full_goal"
    assert fallback.metadata["legacy_unknown_capabilities"] == ("legacy.magic",)
    safe = {
        "task": "three results",
        "obligations": [
            {"id": "one", "description": "one", "required_capabilities": ["legacy.magic"]},
            {"id": "two", "description": "two", "depends_on": ["one"]},
            {"id": "three", "description": "three", "depends_on": ["two"]},
        ],
    }
    split = project_legacy_contract(safe, mission_id="m2", run_id="r2")
    assert len(split.obligations) == 3
    assert all("legacy.magic" not in item.required_capabilities for item in split.obligations)
    assert split.metadata["legacy_unknown_capabilities"] == ("legacy.magic",)
