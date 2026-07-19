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


def test_run_state_owns_repeated_subgoal_progress_evidence():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    result = json.dumps({
        "ok": True,
        "verified": False,
        "partial_output": {"artifact_id": "fictional-7"},
    })

    for _ in range(3):
        state.before_tool("execute_subgoal", {"subgoal": "move visible token"})
        state.after_tool(
            "execute_subgoal",
            {"subgoal": "move visible token"},
            result,
        )

    assert state.phase == "need_observe"
    assert state.last_tool == "execute_subgoal"
    assert state.unverified_actions == 3
    assert state.transition_count == 6


def test_run_state_owns_no_progress_retry_count_and_termination_only():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()

    first = state.register_no_progress_block("click:same-region")
    assert first == 1
    assert state.strategy_block_limit_reached("click:same-region", 2) is False

    second = state.register_no_progress_block("click:same-region")
    assert second == 2
    assert state.strategy_block_limit_reached("click:same-region", 2) is True

    state.clear_no_progress_strategy()
    assert state.strategy_block_limit_reached("click:same-region", 1) is False


def test_run_state_reads_batch_expect_from_params():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    state.after_tool(
        "run_action_batch",
        {"actions": [{
            "action": "click",
            "params": {"x": 1, "y": 2, "expect": {"text": "opened"}},
        }]},
        json.dumps({"ok": True, "verified": False, "verify_detail": "not opened"}),
    )

    assert state.phase == "recovering"
    assert state.unverified_actions == 1


def test_run_state_batch_with_final_plain_observe_keeps_action_debt():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    state.after_tool(
        "run_action_batch",
        {"actions": [{"click": {"x": 1, "y": 2}}, {"observe": {}}]},
        json.dumps({"ok": True, "metrics": {"action_count": 2}}),
    )

    assert state.phase == "visually_grounded"
    assert state.unverified_actions == 1
    assert state.guidance() == (
        "phase=visually_grounded after run_action_batch: "
        "batch refreshed state but did not explicitly verify its action."
    )


def test_run_state_batch_with_explicit_matches_result_is_ready():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    state.after_tool(
        "run_action_batch",
        {"actions": [{"click": {"x": 1, "y": 2}}, {"observe": {}}]},
        json.dumps({"ok": True, "matches": True, "metrics": {"action_count": 2}}),
    )

    assert state.phase == "ready"
    assert state.unverified_actions == 0


def test_run_state_observe_only_batch_preserves_existing_action_debt():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine(unverified_actions=1)
    state.after_tool(
        "run_action_batch",
        {"actions": [{"observe": {}}]},
        json.dumps({"ok": True, "metrics": {"action_count": 1}}),
    )

    assert state.phase == "visually_grounded"
    assert state.unverified_actions == 1


def test_run_state_batch_with_final_zoom_still_needs_semantic_verify():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    state.after_tool(
        "run_action_batch",
        {"actions": [{"draw_strokes": {"strokes": [[[1, 2], [3, 4]]]}}, {"zoom": {"x": 1, "y": 2, "width": 10, "height": 10}}]},
        json.dumps({"ok": True, "metrics": {"action_count": 2}}),
    )

    assert state.phase == "need_observe"
    assert state.unverified_actions == 1


def test_run_state_batch_verified_result_is_ready():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    state.after_tool(
        "run_action_batch",
        {"actions": [{"draw_strokes": {"strokes": [[[1, 2], [3, 4]]]}}]},
        json.dumps({"ok": True, "verified": True, "metrics": {"action_count": 1}}),
    )

    assert state.phase == "ready"
    assert state.unverified_actions == 0


def test_run_state_generic_observe_keeps_pending_causal_debt():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    state.after_tool("click", {"x": 1, "y": 2}, json.dumps({"ok": True, "verified": False}))
    assert state.unverified_actions == 1

    state.after_tool("observe", {}, json.dumps({"ok": True, "text": "real semantic state"}))

    assert state.phase == "visually_grounded"
    assert state.unverified_actions == 1


def test_run_state_zoom_does_not_clear_pending_unverified_count():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    state.after_tool("click", {"x": 1, "y": 2}, json.dumps({"ok": True, "verified": False}))
    assert state.unverified_actions == 1

    state.after_tool("zoom", {"x": 1, "y": 2, "width": 10, "height": 10}, json.dumps({"ok": True}))

    assert state.phase == "visually_grounded"
    assert state.unverified_actions == 1


def test_unrelated_inventory_does_not_clear_pending_action_count():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    state.after_tool("run_shell", {}, json.dumps({"ok": True, "exit_code": 0}))
    assert state.unverified_actions == 1

    state.after_tool(
        "list_installed_apps",
        {"offset": 0, "limit": 200},
        "installed applications total=363 range=1-200",
    )

    assert state.phase == "visually_grounded"
    assert state.unverified_actions == 1


def test_targeted_read_text_does_not_clear_pending_action_without_transition():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    state.after_tool("click", {"x": 1, "y": 2}, json.dumps({"ok": True, "verified": False}))

    state.after_tool(
        "read_text",
        {"query": "saved successfully"},
        "Saved successfully",
    )

    assert state.phase == "visually_grounded"
    assert state.unverified_actions == 1


def test_read_only_shell_verification_clears_pending_action_count():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    state.after_tool("click", {"x": 1, "y": 2}, json.dumps({"ok": True, "verified": False}))
    assert state.unverified_actions == 1

    state.after_tool(
        "run_shell",
        {"command": r"Test-Path 'C:\Users\zou\Desktop\result.txt'"},
        json.dumps({"ok": True, "exit_code": 0, "stdout": "True"}),
    )

    assert state.phase == "ready"
    assert state.unverified_actions == 0


def test_run_state_done_rejection_is_not_terminal_done():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    state.before_tool("done", {"summary": "claimed"})
    state.after_tool("done", {"summary": "claimed"}, "任务完成: claimed", terminal=None)
    state.terminal_rejected("done", "missing visible evidence")

    assert state.phase == "recovering"
    assert state.last_tool == "done"
    assert "missing visible evidence" in state.last_issue
    assert all(item["phase"] != "terminal:done" for item in state.history)


def test_run_state_strong_deterministic_verification_clears_pending_action():
    from winpilot.agent.run_state import RunStateMachine

    state = RunStateMachine()
    state.before_tool("execute_subgoal", {})
    state.after_tool(
        "execute_subgoal",
        {},
        json.dumps({
            "ok": False,
            "verified": False,
            "blocked": True,
            "error": "bad model hint",
        }),
    )
    assert state.unverified_actions == 1

    state.accept_verification(
        "execute_subgoal",
        "fresh screen proves the requested result",
    )

    assert state.unverified_actions == 0
    assert state.phase == "ready"
    assert "fresh screen" in state.last_issue
