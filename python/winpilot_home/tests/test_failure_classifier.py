from __future__ import annotations

import json


def _classify(tool: str, payload: dict, **kwargs):
    from winpilot.agent.failure_classifier import classify_failure

    return classify_failure(tool, kwargs.pop("args", {}), json.dumps(payload), **kwargs)


def test_scope_guard_foreground_mismatch_is_recorded_as_window_focus_error():
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
    assert cls.confidence > 0.5
    assert cls.normalized_reason


def test_visual_grounding_block_is_not_misreported_as_action_success():
    from winpilot.agent.failure_classifier import FailureType

    cls = _classify(
        "click",
        {
            "ok": False,
            "verified": False,
            "blocked": True,
            "visual_grounding_required": True,
            "error": "Low-level GUI action blocked because no fresh real frame grounds the current target-window epoch.",
        },
    )

    assert cls is not None
    assert cls.failure_type == FailureType.VISUAL_GROUNDING_REQUIRED
    assert cls.evidence["visual_grounding_required"] is True


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
    assert cls.evidence["candidate_click_labels"] == ["Apply", "Search locally"]


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
    assert cls.evidence


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


def test_draw_without_pixel_change_uses_generic_visual_no_effect_classification():
    from winpilot.agent.failure_classifier import FailureType

    cls = _classify(
        "run_action_batch",
        {
            "ok": False,
            "results": [{
                "action": "draw_strokes",
                "ok": False,
                "verified": False,
                "data": {
                    "visual_change_detected": False,
                    "visual_change_ratio": 0.0,
                    "failure_type": "DRAW_NO_VISIBLE_CHANGE",
                },
            }],
        },
    )

    assert cls is not None
    assert cls.failure_type == FailureType.VISUAL_ACTION_NO_EFFECT
    assert cls.evidence["visual_action_failures"]


def test_drag_without_pixel_change_uses_generic_spatial_recovery():
    from winpilot.agent.failure_classifier import FailureType

    cls = _classify(
        "run_action_batch",
        {
            "ok": False,
            "results": [{
                "action": "drag",
                "ok": False,
                "verified": False,
                "data": {
                    "visual_change_detected": False,
                    "visual_change_ratio": 0.0,
                    "failure_type": "VISUAL_ACTION_NO_EFFECT",
                    "failure_subtype": "source_or_target_not_confirmed",
                },
            }],
        },
    )

    assert cls is not None
    assert cls.failure_type == FailureType.VISUAL_ACTION_NO_EFFECT
    assert cls.evidence["visual_action_failures"]


def test_failure_classifier_does_not_infer_constraints_from_task_words():
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

    assert cls is None


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
    tool_payload = json.loads(runner.messages[-1]["content"])
    assert tool_payload["failure_classification"]["failure_type"] == "CONTROL_NOT_FOUND"
    assert tool_payload["failure_recovery"]["executed"] is False


def test_recovery_focus_records_facts_without_touching_session():
    from winpilot.agent.failure_recovery import FailureRecoveryLayer, FailureRecoveryStatus

    class _Session:
        def __getattr__(self, name):
            raise AssertionError(f"recovery classification accessed session.{name}")

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
    assert recovery.status == FailureRecoveryStatus.RECORDED
    assert recovery.classification.failure_type.value == "WINDOW_FOCUS_ERROR"
    assert recovery.to_dict()["executed"] is False


def test_recovery_focus_never_dispatches_or_calls_private_handlers():
    from winpilot.agent.failure_recovery import FailureRecoveryLayer

    calls = []

    class _Session:
        def _action_target_context(self):
            calls.append(("_action_target_context", {}))
            raise AssertionError("classification must not inspect mutable session state")

        def dispatch(self, name, args):
            calls.append((name, dict(args)))
            raise AssertionError("classification must not recursively dispatch")

        def _t_list_windows(self, _args):
            calls.append(("_t_list_windows", {}))
            raise AssertionError("classification must not call private handlers")

        def _t_focus_window(self, _args):
            calls.append(("_t_focus_window", {}))
            raise AssertionError("classification must not call private handlers")

        def _t_observe(self, _args):
            calls.append(("_t_observe", {}))
            raise AssertionError("classification must not call private handlers")

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
    assert calls == []
    assert recovery.status.value == "recorded"
    assert recovery.to_dict()["executed"] is False


def test_wait_recovery_records_timeout_without_touching_watcher():
    from winpilot.agent.failure_recovery import FailureRecoveryLayer

    class _Session:
        def _fast_controller(self):
            raise AssertionError("classification must not access or wait on the controller watcher")

    session = _Session()
    recovery = FailureRecoveryLayer().handle_tool_result(
        tool_name="run_action_batch",
        args={},
        result_text=json.dumps({
            "ok": False,
            "verified": False,
            "results": [{
                "action": "wait_change",
                "ok": False,
                "data": {"ok": False, "changed": False},
            }],
        }),
        session=session,
    )

    assert recovery is not None
    assert recovery.classification.failure_type.value == "WAIT_TIMEOUT"
    assert recovery.to_dict()["executed"] is False


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


def test_legacy_auto_done_payload_is_not_proven_root_success():
    from winpilot.agent.failure_recovery import _root_result_is_proven_success

    assert _root_result_is_proven_success({
        "ok": False,
        "verified": False,
        "auto_done": {"ok": True, "auto_done": True},
    }) is False


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


def test_recovery_does_not_accumulate_hidden_escalation_state():
    from winpilot.agent.failure_recovery import FailureRecoveryLayer, FailureRecoveryStatus

    layer = FailureRecoveryLayer()
    payload = json.dumps({
        "ok": False,
        "verified": False,
        "blocked": True,
        "scope_guard": {"before": {}, "after": {}},
    })

    first = layer.handle_tool_result(tool_name="click", args={}, result_text=payload)
    second = layer.handle_tool_result(tool_name="click", args={}, result_text=payload)
    third = layer.handle_tool_result(tool_name="click", args={}, result_text=payload)

    assert first is not None and first.status == FailureRecoveryStatus.RECORDED
    assert second is not None and second.status == FailureRecoveryStatus.RECORDED
    assert third is not None and third.status == FailureRecoveryStatus.RECORDED
    assert first.to_dict() == second.to_dict() == third.to_dict()


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
