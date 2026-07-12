from __future__ import annotations

import json


def _classify(tool: str, payload: dict, **kwargs):
    from winpilot.agent.failure_classifier import classify_failure

    return classify_failure(tool, kwargs.pop("args", {}), json.dumps(payload), **kwargs)


def test_scope_guard_foreground_mismatch_is_window_focus_without_llm():
    from winpilot.agent.failure_classifier import FailureType

    cls = _classify(
        "click",
        {
            "ok": False,
            "verified": False,
            "blocked": True,
            "scope_guard": {
                "before": {"target_title": "Target", "foreground_title": "Other"},
                "after": {"target_title": "Target", "foreground_title": "Other"},
                "recovery_attempted": True,
            },
        },
    )

    assert cls is not None
    assert cls.failure_type == FailureType.WINDOW_FOCUS_ERROR
    assert cls.llm_required is False
    assert cls.retryable is True
    assert "focus_window" in cls.patch_hint


def test_execute_subgoal_needs_target_window_is_window_recovery_failure():
    from winpilot.agent.failure_classifier import FailureType

    cls = _classify(
        "execute_subgoal",
        {
            "ok": False,
            "verified": False,
            "blocked": True,
            "needs_target_window": True,
            "controller_state": {"phase": "blocked", "needs_target_window": True},
            "error": "verify-only routine requires an existing target window/session hwnd.",
        },
    )

    assert cls is not None
    assert cls.failure_type in {FailureType.WINDOW_NOT_FOUND, FailureType.WINDOW_FOCUS_ERROR}
    assert cls.llm_required is False


def test_focus_window_missing_live_hwnd_is_window_not_found():
    from winpilot.agent.failure_classifier import FailureType

    cls = _classify(
        "focus_window",
        {
            "ok": False,
            "error": "focus_window requires a live hwnd or matching title/query/name",
            "query": "Missing Target",
        },
    )

    assert cls is not None
    assert cls.failure_type == FailureType.WINDOW_NOT_FOUND


def test_click_element_anchor_missing_with_candidates_is_control_not_found():
    from winpilot.agent.failure_classifier import FailureType

    cls = _classify(
        "execute_subgoal",
        {
            "ok": False,
            "verified": False,
            "anchor_locator": {"found": False, "cache_hit": False, "score": 0.12, "reason": "no match"},
            "evidence_packet": {"candidate_click_labels": ["Apply", "Search locally"]},
        },
    )

    assert cls is not None
    assert cls.failure_type == FailureType.CONTROL_NOT_FOUND
    assert cls.llm_required is False
    assert cls.evidence["candidate_click_labels"] == ["Apply", "Search locally"]
    assert "candidate" in cls.strategy


def test_successful_pending_mutation_is_not_reclassified_as_failure():
    cls = _classify(
        "run_action_batch",
        {
            "ok": True,
            "verified": False,
            "mutation_applied": True,
            "postcondition_pending": True,
            "needs_llm_reason": False,
            "controller_state": {"phase": "executed", "verified": False},
            "evidence_packet": {
                "matched_texts": ["Name"],
                "missing_texts": [],
                "candidate_click_labels": ["Save", "Name"],
            },
        },
    )

    assert cls is None


def test_candidate_labels_do_not_mean_control_not_found_when_root_action_succeeded():
    cls = _classify(
        "execute_subgoal",
        {
            "ok": True,
            "verified": False,
            "needs_llm_reason": False,
            "controller_state": {"phase": "executed", "verified": False},
            "evidence_packet": {
                "candidate_click_labels": ["Save", "Name"],
                "missing_texts": [],
            },
        },
    )

    assert cls is None


def test_located_visual_servo_needing_observe_is_not_control_not_found():
    cls = _classify(
        "run_action_batch",
        {
            "ok": True,
            "verified": False,
            "needs_observe": True,
            "results": [{
                "action": "click_element",
                "ok": True,
                "verified": False,
                "data": {"visual_change_detected": True},
            }],
            "controller_state": {
                "phase": "needs_observe",
                "verified": False,
                "failed_at": None,
            },
            "anchor_locator": {
                "found": True,
                "reason": "located_from_snapshot",
                "element": {"role": "button", "text": "Moving target"},
            },
            "metrics": {
                "visual_servo_count": 1,
                "visual_servo_correction_count": 3,
                "visual_servo_failure_count": 0,
            },
        },
    )

    assert cls is None


def test_format_postcondition_handoff_is_not_a_recovery_failure():
    cls = _classify(
        "execute_subgoal",
        {
            "ok": True,
            "verified": False,
            "format_applied": True,
            "save_dispatched": True,
            "postcondition_pending": True,
            "needs_llm_reason": False,
            "controller_state": {"phase": "needs_observe", "verified": False},
            "results": [{"action": "hotkey", "ok": True, "verified": False}],
        },
    )

    assert cls is None


def test_ocr_missing_text_verify_detail_is_ocr_or_verify_failed():
    from winpilot.agent.failure_classifier import FailureType

    cls = _classify(
        "click",
        {
            "ok": False,
            "verified": False,
            "verify_detail": '\u672a\u627e\u5230\u6587\u5b57 "READY_STATE"',
            "verify_method": "ocr",
        },
    )

    assert cls is not None
    assert cls.failure_type in {
        FailureType.OCR_MISREAD,
        FailureType.ACTION_SUCCEEDED_VERIFICATION_FAILED,
    }
    assert cls.llm_required is False


def test_screen_delta_foreground_mismatch_is_window_focus():
    from winpilot.agent.failure_classifier import FailureType

    cls = _classify(
        "click",
        {
            "ok": False,
            "verified": False,
            "screen_delta": {
                "changed": True,
                "changed_in_target_region": False,
                "foreground_matches_target": False,
            },
        },
    )

    assert cls is not None
    assert cls.failure_type == FailureType.WINDOW_FOCUS_ERROR


def test_wait_change_or_wait_stable_unmet_is_wait_timeout():
    from winpilot.agent.failure_classifier import FailureType

    cls = _classify(
        "run_action_batch",
        {
            "ok": False,
            "verified": False,
            "metrics": {"failed_at": 1},
            "results": [
                {
                    "index": 1,
                    "action": "wait_change",
                    "ok": False,
                    "data": {"ok": False, "changed": False},
                }
            ],
        },
    )

    assert cls is not None
    assert cls.failure_type == FailureType.WAIT_TIMEOUT
    assert "bare sleep" in cls.patch_hint


def test_successful_wait_stable_timeout_setting_is_not_wait_timeout():
    cls = _classify(
        "run_action_batch",
        {
            "ok": True,
            "results": [{
                "index": 1,
                "action": "wait_stable",
                "ok": True,
                "data": {"ok": True, "stable": True, "timeout_s": 2.0},
            }],
        },
    )

    assert cls is None


def test_ok_true_verified_false_missing_texts_is_action_succeeded_verify_failed():
    from winpilot.agent.failure_classifier import FailureType

    cls = _classify(
        "execute_subgoal",
        {
            "ok": True,
            "verified": False,
            "evidence_packet": {
                "matched_texts": [],
                "missing_texts": ["READY_STATE"],
            },
        },
    )

    assert cls is not None
    assert cls.failure_type == FailureType.ACTION_SUCCEEDED_VERIFICATION_FAILED
    assert cls.evidence["missing_texts"] == ["READY_STATE"]


def test_required_task_literal_missing_is_task_constraint_lost():
    from winpilot.agent.failure_classifier import FailureType

    cls = _classify(
        "execute_subgoal",
        {
            "ok": True,
            "verified": True,
            "evidence_packet": {"matched_texts": [], "missing_texts": []},
        },
        task='Type "SECRET_TOKEN_42" into the field and confirm it is visible',
        recent_results=[],
        visible_evidence_ledger=[],
        tool_history=[],
    )

    assert cls is not None
    assert cls.failure_type == FailureType.TASK_CONSTRAINT_LOST
    assert cls.llm_required is False
    assert cls.evidence["missing_literals"] == ["SECRET_TOKEN_42"]


def test_successful_intermediate_batch_does_not_require_final_task_literal():
    cls = _classify(
        "run_action_batch",
        {
            "ok": True,
            "verified": False,
            "needs_observe": True,
            "controller_state": {"phase": "needs_observe"},
            "results": [
                {
                    "index": 0,
                    "action": "click",
                    "ok": True,
                    "verified": True,
                    "data": {"ok": True, "verified": True},
                },
                {
                    "index": 1,
                    "action": "click",
                    "ok": True,
                    "verified": True,
                    "data": {"ok": True, "verified": True},
                },
            ],
        },
        task="Answer the questions and finish only after EXAM_PASS is visible",
        recent_results=[],
        visible_evidence_ledger=[],
        tool_history=[],
    )

    assert cls is None


def test_runner_injects_failure_metadata_without_replacing_tool_result():
    from types import SimpleNamespace

    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("click the Apply button")
    raw = {
        "ok": False,
        "verified": False,
        "anchor_locator": {"found": False, "reason": "no anchor"},
        "evidence_packet": {"candidate_click_labels": ["Apply", "Apply changes"]},
    }
    runner.session.dispatch = lambda _name, _args: ToolResult(json.dumps(raw))

    terminal = runner._handle_tool_calls([
        SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name="execute_subgoal", arguments=json.dumps({"subgoal": "click Apply"})),
        )
    ])

    assert terminal is None
    history_payload = json.loads(runner._tool_history[-1][2])
    assert history_payload["anchor_locator"]["found"] is False
    tool_payload = json.loads(runner.messages[-2]["content"])
    assert tool_payload["failure_classification"]["failure_type"] == "CONTROL_NOT_FOUND"
    assert "failure recovery" in runner.messages[-1]["content"]


def test_recovery_focus_enumerates_windows_focuses_and_observes():
    from types import SimpleNamespace

    from winpilot.agent.failure_recovery import FailureRecoveryLayer, FailureRecoveryStatus

    class _Session:
        def _action_target_context(self):
            return {
                "target_hwnd": 7,
                "foreground_hwnd": 99,
                "foreground_matches_target": False,
            }

        def _t_list_windows(self, _args):
            return SimpleNamespace(text='hwnd=7 "Target" class=AppWindow')

        def _t_focus_window(self, args):
            assert args == {"hwnd": 7}
            return SimpleNamespace(text='{"ok": true}')

        def _t_observe(self, args):
            assert args["hwnd"] == 7
            return SimpleNamespace(text="Target window content")

    layer = FailureRecoveryLayer()
    recovery = layer.handle_tool_result(
        tool_name="click",
        args={"x": 10, "y": 20},
        result_text=json.dumps({
            "ok": False,
            "verified": False,
            "blocked": True,
            "scope_guard": {"before": {}, "after": {}},
        }),
        session=_Session(),
    )

    assert recovery is not None
    assert recovery.status == FailureRecoveryStatus.LOCAL_RECOVERY_SUGGESTED
    assert recovery.llm_required is False
    assert [item["action"] for item in recovery.local_actions] == [
        "inspect_window_context",
        "list_windows",
        "focus_window",
        "observe_after_focus",
    ]


def test_recovery_focus_prefers_session_dispatch_over_private_handlers():
    from types import SimpleNamespace

    from winpilot.agent.failure_recovery import FailureRecoveryLayer

    calls = []

    class _Session:
        def _action_target_context(self):
            return {
                "target_hwnd": 7,
                "foreground_hwnd": 99,
                "foreground_matches_target": False,
            }

        def dispatch(self, name, args):
            calls.append((name, dict(args)))
            if name == "list_windows":
                return SimpleNamespace(text='hwnd=7 "Target" class=AppWindow')
            if name == "focus_window":
                return SimpleNamespace(text='{"ok": true}')
            if name == "observe":
                return SimpleNamespace(text="Target window content")
            raise AssertionError(name)

        def _t_list_windows(self, _args):
            raise AssertionError("recovery should use dispatch for list_windows")

        def _t_focus_window(self, _args):
            raise AssertionError("recovery should use dispatch for focus_window")

        def _t_observe(self, _args):
            raise AssertionError("recovery should use dispatch for observe")

    layer = FailureRecoveryLayer()
    recovery = layer.handle_tool_result(
        tool_name="click",
        args={"x": 10, "y": 20},
        result_text=json.dumps({
            "ok": False,
            "verified": False,
            "blocked": True,
            "scope_guard": {"before": {}, "after": {}},
        }),
        session=_Session(),
    )

    assert recovery is not None
    assert calls == [
        ("list_windows", {}),
        ("focus_window", {"hwnd": 7}),
        ("observe", {"hwnd": 7, "include_icons": False, "max_elements": 80}),
    ]
    assert [item.get("via") for item in recovery.local_actions[1:]] == [
        "dispatch",
        "dispatch",
        "dispatch",
    ]


def test_recovery_layer_does_not_attach_failure_to_verified_root_result():
    from winpilot.agent.failure_recovery import FailureRecoveryLayer

    layer = FailureRecoveryLayer()
    recovery = layer.handle_tool_result(
        tool_name="execute_subgoal",
        args={},
        result_text=json.dumps({
            "ok": True,
            "verified": True,
            "evidence_packet": {
                "matched_texts": ["VISIBLE_DONE"],
                "missing_texts": [],
            },
            "failure_classification": {
                "failure_type": "CONTROL_NOT_FOUND",
            },
        }),
        task="click target and verify VISIBLE_DONE",
    )

    assert recovery is None


def test_recovery_layer_ignores_read_only_verify_miss():
    from winpilot.agent.failure_recovery import FailureRecoveryLayer

    layer = FailureRecoveryLayer()
    recovery = layer.handle_tool_result(
        tool_name="execute_subgoal",
        args={"verify_texts": ["Hidden target action"]},
        result_text=json.dumps({
            "ok": False,
            "verified": False,
            "actions_executed": [{"action": "read_text"}],
            "evidence_packet": {
                "matched_texts": [],
                "missing_texts": ["Hidden target action"],
                "candidate_click_labels": ["Reveal hidden menu"],
            },
        }),
        task="check whether Hidden target action is visible",
    )

    assert recovery is None


def test_read_only_tool_missing_task_literal_is_not_constraint_lost():
    cls = _classify(
        "coordinate_diagnostics",
        {"ok": True, "screen_size": {"width": 1920, "height": 1080}},
        task="verify CALIBRATION_OK appears",
    )

    assert cls is None


def test_successful_focus_before_literal_entry_is_not_constraint_lost():
    cls = _classify(
        "focus_window",
        {
            "ok": True,
            "wrapped_by_fast_controller": True,
            "source_batch_ok": True,
            "needs_observe": True,
            "results": [{"action": "focus_window", "ok": True}],
        },
        task="Type DAQ_PERCEPTION_E2E_811 into the field and verify it",
        recent_results=[],
        visible_evidence_ledger=[],
        tool_history=[],
    )

    assert cls is None


def test_successful_unverified_click_before_literal_entry_is_not_constraint_lost():
    cls = _classify(
        "click",
        {"ok": True, "verified": False, "needs_observe": True},
        task="Type SECRET_TOKEN_42 and click Apply",
    )

    assert cls is None


def test_recovery_escalates_window_focus_after_consecutive_failures():
    from winpilot.agent.failure_recovery import FailureRecoveryLayer, FailureRecoveryStatus

    layer = FailureRecoveryLayer(consecutive_llm_threshold=3)
    payload = json.dumps({
        "ok": False,
        "verified": False,
        "blocked": True,
        "scope_guard": {"before": {}, "after": {}},
    })

    first = layer.handle_tool_result(tool_name="click", args={}, result_text=payload)
    second = layer.handle_tool_result(tool_name="click", args={}, result_text=payload)
    third = layer.handle_tool_result(tool_name="click", args={}, result_text=payload)

    assert first is not None and first.llm_required is False
    assert second is not None and second.llm_required is False
    assert third is not None
    assert third.status == FailureRecoveryStatus.NEEDS_LLM
    assert third.llm_required is True
    assert third.retryable is False


def test_attach_recovery_metadata_preserves_non_json_tool_text():
    from winpilot.agent.failure_recovery import FailureRecoveryLayer, attach_recovery_metadata

    layer = FailureRecoveryLayer()
    recovery = layer.handle_tool_result(
        tool_name="execute_subgoal",
        args={},
        result_text=json.dumps({
            "ok": False,
            "verified": False,
            "anchor_locator": {"found": False},
        }),
    )

    assert recovery is not None
    mixed_text = 'prefix {"ok": false, "anchor_locator": {"found": false}} suffix'
    assert attach_recovery_metadata(mixed_text, recovery) == mixed_text
