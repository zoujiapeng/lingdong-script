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


def _quiz_answers(seed: int) -> dict[str, str]:
    return {
        str(question["id"]): str(question["_answer"])
        for question in server._quiz_questions(seed)
    }


def _quiz_event(
    details: dict,
    *,
    event_type: str = "quiz_submit",
    trusted: bool = True,
) -> dict:
    return {
        "case_id": "quiz-case",
        "scenario": "quiz_all",
        "seed": 23,
        "type": event_type,
        "trusted": trusted,
        "succeeded": details.get("succeeded") is True,
        "received_at": 123.0,
        "details": details,
    }


def test_quiz_spec_is_seeded_mixed_and_does_not_publish_answer_key():
    first = server._public_quiz_spec(23)
    repeated = server._public_quiz_spec(23)
    other = server._public_quiz_spec(24)

    assert first == repeated
    assert first != other
    assert first["question_count"] == 6
    assert {
        question["kind"] for question in first["questions"]
    } == {"single_choice", "text"}
    assert all(
        "_answer" not in question and "answer" not in question
        for question in first["questions"]
    )


def test_quiz_evaluation_is_server_owned_and_omits_submitted_answers():
    answers = _quiz_answers(23)

    accepted = server._evaluate_quiz_submission(23, answers)

    assert accepted == {
        "succeeded": True,
        "server_verified": True,
        "all_correct": True,
        "question_count": 6,
        "answer_count": 6,
        "correct_count": 6,
        "question_ids": [item["id"] for item in server._quiz_questions(23)],
        "question_types": [item["kind"] for item in server._quiz_questions(23)],
    }
    assert "answers" not in accepted

    answers[next(iter(answers))] = "wrong"
    rejected = server._evaluate_quiz_submission(23, answers)
    assert rejected["succeeded"] is False
    assert rejected["all_correct"] is False
    assert rejected["correct_count"] == 5


def test_browser_quiz_claim_is_replaced_by_server_evaluation():
    answers = _quiz_answers(23)
    answers[next(iter(answers))] = "wrong"
    event = {
        "case_id": "quiz-case",
        "scenario": "quiz_all",
        "seed": 23,
        "type": "quiz_submit",
        "trusted": True,
        "succeeded": True,
        "details": {
            "answers": answers,
            "server_verified": True,
            "all_correct": True,
        },
    }

    safe = server._prepare_event(event)

    assert safe["succeeded"] is False
    assert safe["details"]["server_verified"] is True
    assert safe["details"]["all_correct"] is False
    assert safe["details"]["correct_count"] == 5
    assert "answers" not in safe["details"]


def test_quiz_success_requires_trusted_server_verified_submit_event():
    evaluation = server._evaluate_quiz_submission(23, _quiz_answers(23))
    accepted = _quiz_event(evaluation)

    assert server._accepted_success_event([accepted], 0) is True

    forged_details = dict(evaluation, server_verified=False)
    assert server._accepted_success_event([_quiz_event(forged_details)], 0) is False
    assert server._accepted_success_event([
        _quiz_event(evaluation, event_type="scenario_success"),
    ], 0) is False
    assert server._accepted_success_event([
        _quiz_event(evaluation, trusted=False),
    ], 0) is False


def test_case_scoped_state_accepts_only_verified_quiz_completion():
    original = copy.deepcopy(server.STATE)
    try:
        evaluation = server._evaluate_quiz_submission(23, _quiz_answers(23))
        event = _quiz_event(evaluation)
        server.STATE.clear()
        server.STATE.update({
            "ready": True,
            "success": True,
            "events": [event],
            "started_at": 100.0,
        })

        current = server._state_snapshot("quiz-case")

        assert current["success"] is True
        assert current["success_event"] == event
        assert current["scenario"] == "quiz_all"
        assert current["seed"] == 23
    finally:
        server.STATE.clear()
        server.STATE.update(original)


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
