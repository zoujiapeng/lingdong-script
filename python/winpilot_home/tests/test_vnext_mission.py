"""Tests for the small state carried between live Visual Actor turns."""
from __future__ import annotations

import pytest

from winpilot.vnext.mission import (
    IllegalTransitionError,
    MissionState,
    MissionStatus,
    TargetBinding,
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


def _mission() -> MissionState:
    return MissionState(
        mission_id="mission-1",
        run_id="run-1",
        original_goal="Open Notepad and type hello",
        target_binding=_target(),
    )


def test_target_binding_round_trips_one_exact_mechanical_identity() -> None:
    target = _target()

    assert TargetBinding.from_dict(target.to_dict()) == target
    with pytest.raises(Exception, match="fields"):
        TargetBinding.from_dict({"binding_id": target.binding_id})


def test_state_keeps_one_original_goal_and_bound_target() -> None:
    mission = _mission()

    assert mission.status is MissionStatus.RUNNING
    assert mission.revision == 0
    assert mission.card.original_goal == "Open Notepad and type hello"
    assert mission.card.target_binding == _target()


def test_last_transition_keeps_only_mechanical_feedback() -> None:
    mission = _mission()

    mission.record_transition(
        action="click",
        action_id="decision-1",
        input_completed=False,
        visible_effect="",
        error_kind="hit_test_rejected",
    )

    transition = mission.last_transition
    assert transition is not None
    assert transition.action == "click"
    assert transition.action_id == "decision-1"
    assert transition.input_completed is False
    assert transition.error_kind == "hit_test_rejected"
    assert mission.revision == 1


def test_actor_completion_is_terminal() -> None:
    mission = _mission()

    mission.complete()

    assert mission.status is MissionStatus.VERIFIED
    assert mission.revision == 1
    with pytest.raises(IllegalTransitionError):
        mission.cancel("too late")


def test_block_and_cancel_are_terminal_states() -> None:
    blocked = _mission()
    blocked.block(kind="external_state", message="window is unavailable")

    assert blocked.status is MissionStatus.BLOCKED
    assert blocked.failure_summary is not None
    assert blocked.failure_summary.kind == "external_state"
    with pytest.raises(IllegalTransitionError):
        blocked.complete()

    canceled = _mission()
    canceled.cancel("user canceled")
    assert canceled.status is MissionStatus.CANCELED
    assert canceled.last_error == "user canceled"
