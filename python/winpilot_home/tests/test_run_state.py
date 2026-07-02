import json


def test_run_state_unverified_action_needs_observe_once():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    state.before_tool("click", {"x": 1, "y": 2})
    state.after_tool(
        "click",
        {"x": 1, "y": 2},
        json.dumps({"ok": True, "verified": False, "verify_detail": "no expect"}),
    )

    first = state.guidance()
    second = state.guidance()

    assert state.phase == "need_observe"
    assert "need_observe" in first
    assert second == ""


def test_run_state_expected_action_failure_recovers():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    state.after_tool(
        "type_text",
        {"text": "hello", "expect": {"text": "hello"}},
        json.dumps({"ok": True, "verified": False, "verify_detail": "text not visible"}),
    )

    assert state.phase == "recovering"
    assert "text not visible" in state.guidance()


def test_run_state_batch_with_final_observe_is_ready():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    state.after_tool(
        "run_action_batch",
        {"actions": [{"click": {"x": 1, "y": 2}}, {"observe": {}}]},
        json.dumps({"ok": True, "metrics": {"action_count": 2}}),
    )

    assert state.phase == "ready"
    assert state.guidance() == ""


def test_run_state_observe_resets_pending_unverified_count():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    state.after_tool("click", {"x": 1, "y": 2}, json.dumps({"ok": True, "verified": False}))
    assert state.unverified_actions == 1

    state.after_tool("zoom", {"x": 1, "y": 2, "width": 10, "height": 10}, json.dumps({"ok": True}))

    assert state.phase == "ready"
    assert state.unverified_actions == 0
