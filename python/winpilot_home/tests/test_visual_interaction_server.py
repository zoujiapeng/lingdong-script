from __future__ import annotations

import copy

from eval_targets import visual_interaction_server as server


def _event(case_id: str, *, succeeded: bool = False) -> dict:
    return {
        "case_id": case_id,
        "scenario": "ordered_clicks",
        "seed": 17,
        "type": "scenario_success" if succeeded else "page_ready",
        "trusted": succeeded,
        "succeeded": succeeded,
        "received_at": 123.0,
        "details": {"succeeded": succeeded},
    }


def _dynamic_event(event_type: str, *, inside_goal: bool = True) -> dict:
    succeeded = event_type in {"pointerup", "scenario_success"} and inside_goal
    return {
        "case_id": "dynamic-case",
        "scenario": "dynamic_hold",
        "seed": 17,
        "type": event_type,
        "trusted": True,
        "succeeded": succeeded,
        "received_at": 123.0,
        "details": {
            "succeeded": succeeded,
            "down_count": 1,
            "checkpoint_reached": True,
            "goal_revealed_after_down": True,
            "inside_goal": inside_goal,
            "completion_event": "pointerup",
        },
    }


def test_case_scoped_state_cannot_inherit_another_cases_success():
    original = copy.deepcopy(server.STATE)
    try:
        server.STATE.clear()
        server.STATE.update({
            "ready": True,
            "success": True,
            "events": [_event("old-case", succeeded=True), _event("new-case")],
            "started_at": 100.0,
            "case_id": "old-case",
            "success_event": _event("old-case", succeeded=True),
        })

        current = server._state_snapshot("new-case")

        assert current["ready"] is True
        assert current["success"] is False
        assert current["case_id"] == "new-case"
        assert [item["case_id"] for item in current["events"]] == ["new-case"]
        assert "success_event" not in current
    finally:
        server.STATE.clear()
        server.STATE.update(original)


def test_case_scoped_state_reports_only_its_own_trusted_success():
    original = copy.deepcopy(server.STATE)
    try:
        own_success = _event("new-case", succeeded=True)
        server.STATE.clear()
        server.STATE.update({
            "ready": True,
            "success": True,
            "events": [_event("old-case", succeeded=True), own_success],
            "started_at": 100.0,
        })

        current = server._state_snapshot("new-case")

        assert current["success"] is True
        assert current["success_event"] == own_success
        assert current["scenario"] == "ordered_clicks"
        assert current["seed"] == 17
    finally:
        server.STATE.clear()
        server.STATE.update(original)


def test_dynamic_success_requires_preceding_trusted_in_goal_pointerup():
    original = copy.deepcopy(server.STATE)
    try:
        forged_success = _dynamic_event("scenario_success")
        server.STATE.clear()
        server.STATE.update({
            "ready": True,
            "success": True,
            "events": [forged_success],
            "started_at": 100.0,
        })

        without_release = server._state_snapshot("dynamic-case")

        assert without_release["success"] is False
        assert "success_event" not in without_release

        server.STATE["events"] = [
            _dynamic_event("pointerup", inside_goal=False),
            forged_success,
        ]
        outside_release = server._state_snapshot("dynamic-case")
        assert outside_release["success"] is False

        server.STATE["events"] = [_dynamic_event("pointerup"), forged_success]
        valid = server._state_snapshot("dynamic-case")
        assert valid["success"] is True
        assert valid["success_event"] == forged_success
    finally:
        server.STATE.clear()
        server.STATE.update(original)
