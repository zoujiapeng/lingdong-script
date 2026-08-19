"""Tests for the live five-event Mission reducer."""
from __future__ import annotations

from dataclasses import replace

import pytest

from winpilot.vnext.mission import (
    ArtifactRef,
    DuplicateEventError,
    EvidenceRef,
    IllegalTransitionError,
    InvalidMissionEvent,
    MissionDefinition,
    MissionEvent,
    MissionReducer,
    MissionStatus,
    ObligationProjection,
    ObligationSpec,
    ObligationStatus,
    StaleRevisionError,
    TargetBinding,
)


def _definition(*, mission_id: str = "mission-1") -> MissionDefinition:
    return MissionDefinition(
        mission_id=mission_id,
        run_id="run-1",
        original_goal="Open Notepad and type hello",
        obligations=(
            ObligationSpec(
                id="full-goal",
                desired_state="Open Notepad and type hello",
            ),
        ),
    )


def _proof(
    evidence_id: str = "target-proof",
    *,
    obligation_id: str | None = None,
) -> EvidenceRef:
    return EvidenceRef(
        id=evidence_id,
        mission_id="mission-1",
        run_id="run-1",
        obligation_id=obligation_id,
        kind="current_frame",
        trust="deterministic",
        payload_hash=f"payload-{evidence_id}",
    )


def _target() -> TargetBinding:
    return TargetBinding(
        binding_id="target-1",
        hwnd=101,
        process_id=202,
        executable_identity="notepad.exe",
        title_snapshot="Untitled - Notepad",
        epoch=0,
    )


def test_definition_falls_back_to_one_lossless_obligation() -> None:
    definition = MissionDefinition.from_goal(
        "Open Notepad and type hello",
        mission_id="mission-1",
        run_id="run-1",
    )

    assert len(definition.obligations) == 1
    assert definition.obligations[0].desired_state == definition.original_goal


def test_reducer_activates_the_first_ready_obligation() -> None:
    reducer = MissionReducer(_definition())

    assert reducer.projection.status is MissionStatus.RUNNING
    assert reducer.projection.active_obligation_id == "full-goal"
    assert (
        reducer.projection.obligation_state("full-goal").status
        is ObligationStatus.ACTIVE
    )


def test_target_bound_uses_direct_target_identity() -> None:
    reducer = MissionReducer(_definition())
    event = reducer.event(
        "target_bound",
        payload={"target_binding": _target().to_dict()},
    )

    projection = reducer.apply(event)

    assert projection.target_binding == _target()
    assert projection.evidence_refs == {}
    assert projection.revision == 1


def test_transition_recorded_keeps_mechanical_feedback() -> None:
    proof = _proof("post-frame", obligation_id="full-goal")
    reducer = MissionReducer(_definition(), evidence=(proof,))

    projection = reducer.apply(
        reducer.event(
            "transition_recorded",
            payload={
                "obligation_id": "full-goal",
                "action": "click",
                "action_id": "decision-1",
                "input_completed": True,
                "visible_effect": "menu opened",
                "frame_id": 4,
            },
            evidence_refs=(proof.id,),
        )
    )

    transition = projection.last_transition
    assert transition is not None
    assert transition.action == "click"
    assert transition.action_id == "decision-1"
    assert transition.input_completed is True
    assert transition.visible_effect == "menu opened"
    assert transition.frame_id == 4
    assert transition.evidence_refs == (proof.id,)


def test_transition_must_belong_to_the_active_obligation() -> None:
    reducer = MissionReducer(_definition())

    with pytest.raises(IllegalTransitionError):
        reducer.apply(
            reducer.event(
                "transition_recorded",
                payload={
                    "obligation_id": "not-active",
                    "input_completed": False,
                },
            )
        )


def test_actor_completed_verifies_the_mission() -> None:
    reducer = MissionReducer(_definition())

    projection = reducer.apply(
        reducer.event(
            "actor_completed",
            payload={"obligation_id": "full-goal"},
        )
    )

    assert projection.status is MissionStatus.VERIFIED
    assert projection.active_obligation_id is None
    assert (
        projection.obligation_state("full-goal").status
        is ObligationStatus.VERIFIED
    )
    assert projection.obligation_state("full-goal").last_reason == "actor_completed"

    with pytest.raises(IllegalTransitionError):
        reducer.apply(
            reducer.event(
                "mission_canceled",
                payload={"reason": "too late"},
            )
        )


def test_actor_completed_advances_a_dependency_chain() -> None:
    definition = MissionDefinition(
        mission_id="mission-1",
        run_id="run-1",
        original_goal="complete two steps",
        obligations=(
            ObligationSpec(id="first", desired_state="first complete"),
            ObligationSpec(
                id="second",
                desired_state="second complete",
                depends_on=("first",),
            ),
        ),
    )
    reducer = MissionReducer(definition)

    projection = reducer.apply(
        reducer.event(
            "actor_completed",
            payload={"obligation_id": "first"},
        )
    )

    assert projection.status is MissionStatus.RUNNING
    assert projection.active_obligation_id == "second"
    assert (
        projection.obligation_state("first").status
        is ObligationStatus.VERIFIED
    )


def test_obligation_blocked_is_a_stable_terminal_hold() -> None:
    reducer = MissionReducer(_definition())

    projection = reducer.apply(
        reducer.event(
            "obligation_blocked",
            payload={
                "obligation_id": "full-goal",
                "blocker_kind": "external_state",
                "reason": "the required window is unavailable",
            },
        )
    )

    assert projection.status is MissionStatus.BLOCKED
    assert projection.active_obligation_id is None
    assert projection.failure_summary is not None
    assert projection.failure_summary.kind == "external_state"

    with pytest.raises(IllegalTransitionError):
        reducer.apply(
            reducer.event(
                "actor_completed",
                payload={"obligation_id": "full-goal"},
            )
        )


def test_mission_canceled_is_terminal() -> None:
    reducer = MissionReducer(_definition())

    projection = reducer.apply(
        reducer.event(
            "mission_canceled",
            payload={"reason": "user canceled"},
        )
    )

    assert projection.status is MissionStatus.CANCELED
    assert projection.last_error == "user canceled"


def test_stale_revision_and_duplicate_event_fail_closed() -> None:
    reducer = MissionReducer(_definition())
    event = reducer.event(
        "transition_recorded",
        payload={
            "obligation_id": "full-goal",
            "input_completed": False,
        },
    )
    reducer.apply(event)

    with pytest.raises(DuplicateEventError):
        reducer.apply(event)

    stale = MissionEvent.create(
        "transition_recorded",
        reducer.definition,
        base_revision=0,
        payload={
            "obligation_id": "full-goal",
            "input_completed": False,
            "error_kind": "hit_test_rejected",
        },
    )
    with pytest.raises(StaleRevisionError):
        reducer.apply(stale)


def test_replay_accepts_json_records_and_is_deterministic() -> None:
    reducer = MissionReducer(_definition())
    transition = reducer.event(
        "transition_recorded",
        payload={
            "obligation_id": "full-goal",
            "action": "click",
            "input_completed": True,
        },
    )
    reducer.apply(transition)
    completed = reducer.event(
        "actor_completed",
        payload={"obligation_id": "full-goal"},
    )
    reducer.apply(completed)

    replayed = MissionReducer.replay(
        reducer.definition,
        [transition.to_dict(), completed.to_dict()],
    )

    assert replayed.projection == reducer.projection
    assert replayed.card.to_dict() == reducer.card.to_dict()


def test_card_keeps_only_artifacts_relevant_to_the_active_obligation() -> None:
    definition = MissionDefinition(
        mission_id="mission-1",
        run_id="run-1",
        original_goal="use the needed artifact",
        obligations=(
            ObligationSpec(
                id="full-goal",
                desired_state="use the needed artifact",
                input_artifact_refs=("needed",),
            ),
        ),
    )
    reducer = MissionReducer(definition)
    needed = ArtifactRef(
        "needed",
        "image/png",
        "a" * 64,
        "full-goal",
        "proof-needed",
    )
    unrelated = ArtifactRef(
        "unrelated",
        "image/png",
        "b" * 64,
        "other",
        "proof-other",
    )
    reducer._projection = replace(  # projection-only Broker/Card fixture
        reducer.projection,
        obligation_states=(
            ObligationProjection("full-goal", ObligationStatus.ACTIVE),
        ),
        active_obligation_id="full-goal",
        artifact_bindings={"needed": needed, "unrelated": unrelated},
        status=MissionStatus.RUNNING,
    )

    assert reducer.card.artifact_bindings == (needed,)


@pytest.mark.parametrize(
    "event_type",
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
        "approval_requested",
        "approval_decided",
        "external_change_armed",
        "external_change_recorded",
        "planner_requested",
        "planner_patch_proposed",
        "planner_patch_accepted",
        "planner_patch_rejected",
        "inspection_requested",
        "inspection_recorded",
        "capability_requested",
        "capability_granted",
        "capability_denied",
        "execution_profile_accepted",
        "execution_profile_revised",
        "user_goal_revised",
    ),
)
def test_removed_event_kinds_are_not_wire_compatible(event_type: str) -> None:
    with pytest.raises(InvalidMissionEvent):
        MissionEvent.create(
            event_type,
            _definition(),
            base_revision=0,
        )
