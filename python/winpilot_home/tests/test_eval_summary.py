from winpilot.eval import summary


def test_summarize_events_counts_tools_usage_and_slowest():
    events = [
        {"type": "agent_start", "run_id": "r1", "task": "demo", "ts": 10.0},
        {"type": "agent_tool", "tool": "observe", "elapsed_ms": 1200, "ts": 11.0},
        {"type": "perception", "dims": {"uia": {"count": 3, "ms": 1000}}, "ts": 11.1},
        {"type": "agent_tool", "tool": "click", "elapsed_ms": 80, "ts": 12.0},
        {"type": "done_check", "passed": False, "reason": "not enough", "ts": 12.2},
        {"type": "agent_usage", "total": {"prompt_tokens": 100, "completion_tokens": 5, "cost_cny": 0.01}, "ts": 12.3},
        {"type": "agent_done", "run_id": "r1", "status": "done", "result": "ok", "ts": 13.0},
    ]

    out = summary.summarize_events(events)

    assert out["run_id"] == "r1"
    assert out["task"] == "demo"
    assert out["duration_s"] == 3.0
    assert out["tool_count"] == 2
    assert out["tool_counts"] == {"observe": 1, "click": 1}
    assert out["slow_tools"][0]["tool"] == "observe"
    assert out["rejected_done_count"] == 1
    assert out["done_checks"][0]["unverified"] is False
    assert out["usage"]["cost_cny"] == 0.01
    assert out["perception"]["uia"]["avg_ms"] == 1000
    assert out["perception"]["uia"]["p50_ms"] == 1000
    assert out["tool_latency_overall"]["p50_ms"] == 640


def test_aggregate_reports_totals_usage_and_slow_tools():
    reports = [
        {
            "run_id": "r1",
            "status": "done",
            "ground_truth_status": "grounded",
            "ground_truth_passed": True,
            "policy_passed": True,
            "performance_budget": {"passed": True},
            "duration_s": 2,
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "cost_cny": 0.1},
            "slow_tools": [{"tool": "observe", "elapsed_ms": 50}],
            "provenance": {"model": {"model": "mimo-v2.5"}},
            "low_latency": {
                "native_visual_feedback": {
                    "frames": 3,
                    "capture_scopes": {"target_window": 2, "full_desktop": 1},
                },
                "runtime_proof": {"visual_preflight_checks": 2, "stale_frame_blocks": 1},
            },
            "fast_gui": {
                "batch_calls": 1,
                "batch_actions": 3,
                "subgoal_calls": 1,
                "subgoal_actions": 4,
                "subgoal_elapsed_ms": 120,
                "subgoal_controller_elapsed_ms": 90,
                "subgoal_overhead_ms": 30,
                "round_trips_saved": 2,
            },
        },
        {
            "run_id": "r2",
            "status": "fail",
            "ground_truth_status": "grounded",
            "ground_truth_passed": True,
            "policy_passed": True,
            "performance_budget": {"passed": True},
            "duration_s": 3,
            "usage": {"prompt_tokens": 20, "completion_tokens": 2, "cost_cny": 0.2},
            "slow_tools": [{"tool": "click", "elapsed_ms": 80}],
            "provenance": {"model": {"model": "mimo-v2.5-pro"}},
            "summary": {
                "fast_gui": {"batch_calls": 2, "batch_actions": 4, "round_trips_saved": 2},
                "low_latency": {
                    "native_visual_feedback": {
                        "frames": 2,
                        "capture_scopes": {"full_desktop": 2},
                    },
                    "runtime_proof": {"visual_preflight_checks": 1},
                },
            },
        },
    ]

    out = summary.aggregate_reports(reports)

    assert out["total"] == 2
    assert out["passed_or_done"] == 1
    assert out["verified_successes"] == 1
    assert out["verified_success_rate"] == 0.5
    assert out["grounded_total"] == 2
    assert out["ungrounded_count"] == 0
    assert out["total_duration_s"] == 5
    assert out["total_cost_cny"] == 0.3
    assert out["total_prompt_tokens"] == 30
    assert out["slow_tools"][0]["tool"] == "click"
    assert out["fast_gui"]["batch_calls"] == 3
    assert out["fast_gui"]["batch_actions"] == 7
    assert out["fast_gui"]["subgoal_calls"] == 1
    assert out["fast_gui"]["subgoal_actions"] == 4
    assert out["fast_gui"]["subgoal_elapsed_ms"] == 120
    assert out["fast_gui"]["subgoal_controller_elapsed_ms"] == 90
    assert out["fast_gui"]["subgoal_overhead_ms"] == 30
    assert out["fast_gui"]["round_trips_saved"] == 4
    assert out["native_visual_feedback"] == {
        "frames": 5,
        "capture_scopes": {"target_window": 2, "full_desktop": 3},
    }
    assert out["runtime_proof"]["visual_preflight_checks"] == 3
    assert out["runtime_proof"]["stale_frame_blocks"] == 1
    assert out["models"]["mimo-v2.5"]["verified_success_rate"] == 1.0
    assert out["models"]["mimo-v2.5-pro"]["verified_success_rate"] == 0.0


def test_summarize_events_extracts_fast_gui_metrics():
    events = [
        {"type": "agent_start", "run_id": "r1", "task": "demo", "ts": 1.0},
        {
            "type": "run_state",
            "phase": "acting",
            "last_tool": "run_action_batch",
            "unverified_actions": 0,
            "ts": 1.1,
        },
        {
            "type": "agent_tool",
            "tool": "run_action_batch",
            "elapsed_ms": 50,
            "result_meta": {
                "ok": True,
                "subgoal": "open menu",
                "controller_state": {"phase": "needs_observe"},
                "needs_observe": True,
                "metrics": {
                    "action_count": 3,
                    "controller_round_trips_saved": 2,
                    "screen_wait_count": 1,
                },
            },
            "ts": 1.2,
        },
        {
            "type": "agent_batch_action",
            "tool": "click_element",
            "ok": True,
            "elapsed_ms": 30,
            "result_meta": {
                "via": "click_element",
                "screen_delta": {"elapsed_ms": 9},
                "screen_watch": {"running": True, "capture_latency_ms": 4},
                "anchor_locator": {
                    "found": True,
                    "cache_hit": True,
                    "score": 1.2,
                    "reason": "cache_revalidated",
                },
            },
            "ts": 1.25,
        },
        {"type": "agent_tool", "tool": "screen_watch", "elapsed_ms": 5, "ts": 1.3},
        {"type": "agent_done", "run_id": "r1", "status": "done", "ts": 2.0},
    ]

    out = summary.summarize_events(events)

    assert out["fast_gui"]["batch_calls"] == 1
    assert out["fast_gui"]["batch_actions"] == 3
    assert out["fast_gui"]["round_trips_saved"] == 2
    assert out["fast_gui"]["screen_waits"] == 1
    assert out["fast_gui"]["screen_watch_calls"] == 1
    assert out["fast_gui"]["needs_observe_batches"] == 1
    assert out["fast_gui"]["batch_subgoals"] == 1
    assert out["fast_gui"]["controller_phases"] == {"needs_observe": 1}
    assert out["fast_gui"]["anchor_clicks"] == 1
    assert out["fast_gui"]["anchor_cache_hits"] == 1
    assert out["fast_gui"]["anchor_failures"] == 0
    assert out["fast_gui"]["run_state_phases"] == {"acting": 1}
    assert out["low_latency"]["action_execution_latency"]["avg_ms"] == 30
    assert out["low_latency"]["post_action_verify_latency"]["avg_ms"] == 9
    assert out["low_latency"]["screen_capture_latency"]["avg_ms"] == 4


def test_summarize_events_includes_native_visual_frame_latency():
    from winpilot.eval import summary

    events = [
        {"type": "agent_start", "run_id": "vision", "task": "demo", "ts": 1.0},
        {
            "type": "visual_feedback",
            "run_id": "vision",
            "frame_id": 1,
            "capture_latency_ms": 6,
            "capture_scope": "target_window",
            "capture_scope_reason": "verified_foreground_target",
            "ts": 1.1,
        },
        {
            "type": "visual_feedback",
            "run_id": "vision",
            "frame_id": 2,
            "capture_latency_ms": 10,
            "capture_scope": "full_desktop",
            "capture_scope_reason": "foreground_target_mismatch",
            "ts": 1.2,
        },
        {
            "type": "visual_feedback",
            "run_id": "vision",
            "frame_id": 3,
            "capture_latency_ms": "invalid",
            "ts": 1.3,
        },
        {"type": "agent_done", "run_id": "vision", "status": "done", "ts": 1.4},
    ]

    metrics = summary.summarize_events(events)["low_latency"]

    assert metrics["native_visual_feedback"]["frames"] == 3
    assert metrics["native_visual_feedback"]["capture_latency"]["avg_ms"] == 8
    assert metrics["native_visual_feedback"]["capture_scopes"] == {
        "target_window": 1,
        "full_desktop": 1,
        "unspecified": 1,
    }
    assert metrics["native_visual_feedback"]["capture_scope_reasons"] == {
        "verified_foreground_target": 1,
        "foreground_target_mismatch": 1,
        "unspecified": 1,
    }
    assert metrics["screen_capture_latency"]["avg_ms"] == 8


def test_summarize_events_extracts_execute_subgoal_metrics():
    events = [
        {"type": "agent_start", "run_id": "r1", "task": "demo", "ts": 1.0},
        {
            "type": "agent_tool",
            "tool": "execute_subgoal",
            "elapsed_ms": 90,
            "result_meta": {
                "verified": True,
                "needs_llm_reason": False,
                "actions_executed": [
                    {"action": "type_into_element"},
                    {"action": "click_element"},
                    {"action": "read_text"},
                ],
                "controller_state": {"phase": "verified"},
                "metrics": {
                    "elapsed_ms": 70,
                    "controller_round_trips_saved": 4,
                    "screen_wait_count": 1,
                },
                "evidence_packet": {
                    "matched_texts": ["OK"],
                    "missing_texts": [],
                },
            },
            "ts": 1.2,
        },
        {"type": "agent_done", "run_id": "r1", "status": "done", "ts": 2.0},
    ]

    out = summary.summarize_events(events)

    assert out["fast_gui"]["subgoal_calls"] == 1
    assert out["fast_gui"]["subgoal_actions"] == 3
    assert out["fast_gui"]["verified_subgoals"] == 1
    assert out["fast_gui"]["subgoals_needing_llm"] == 0
    assert out["fast_gui"]["subgoal_missing_evidence"] == 0
    assert out["fast_gui"]["subgoal_elapsed_ms"] == 90
    assert out["fast_gui"]["subgoal_controller_elapsed_ms"] == 70
    assert out["fast_gui"]["subgoal_overhead_ms"] == 20
    assert out["fast_gui"]["round_trips_saved"] == 4
    assert out["fast_gui"]["screen_waits"] == 1
    assert out["fast_gui"]["controller_phases"] == {"verified": 1}
    assert out["low_latency"]["action_execution_latency"]["avg_ms"] == 90


def test_summarize_events_includes_batch_action_screenshots():
    events = [
        {"type": "agent_start", "run_id": "r1", "task": "demo", "ts": 1.0},
        {
            "type": "agent_batch_action",
            "tool": "draw_strokes",
            "screenshot": "r1/batch_after_001.jpg",
            "ts": 1.1,
        },
        {"type": "agent_done", "run_id": "r1", "status": "done", "ts": 2.0},
    ]

    out = summary.summarize_events(events)

    assert out["screenshots"] == ["r1/batch_after_001.jpg"]


def test_summarize_events_reports_low_latency_metrics():
    events = [
        {"type": "agent_start", "run_id": "r1", "task": "demo", "ts": 1.0},
        {"type": "agent_step", "step": 1, "ts": 1.4},
        {
            "type": "agent_tool",
            "tool": "click",
            "elapsed_ms": 120,
            "result_meta": {
                "screen_delta": {"elapsed_ms": 18},
                "screen_watch": {"running": True, "capture_latency_ms": 7},
            },
            "ts": 1.55,
        },
        {
            "type": "perception",
            "dims": {
                "ocr": {"count": 3, "ms": 80},
                "vlm": {"count": 1, "ms": 200},
            },
            "ts": 1.6,
        },
        {"type": "screen_watch", "action": "auto_start", "ts": 1.61},
        {
            "type": "perception_prefetch",
            "source": "perception_daemon",
            "dimension": "incremental_ocr_win32",
            "action_delta": True,
            "transient": True,
            "count": 4,
            "elapsed_ms": 90,
            "ts": 1.612,
        },
        {
            "type": "desktop_action_queue",
            "state": "start",
            "action": "click",
            "scope": "physical",
            "reentrant": False,
            "wait_ms": 1,
            "ts": 1.615,
        },
        {
            "type": "desktop_action_queue",
            "state": "done",
            "action": "click",
            "wait_ms": 5,
            "elapsed_ms": 20,
            "ts": 1.62,
        },
        {
            "type": "desktop_action_queue",
            "state": "start",
            "action": "type_text",
            "scope": "physical_clipboard",
            "reentrant": True,
            "wait_ms": 2,
            "ts": 1.625,
        },
        {
            "type": "desktop_action_queue",
            "state": "error",
            "action": "type_text",
            "reentrant": True,
            "wait_ms": 2,
            "elapsed_ms": 7,
            "ts": 1.63,
        },
        {
            "type": "desktop_action_queue_job",
            "state": "queued",
            "job_id": "job-1",
            "action": "click",
            "ts": 1.64,
        },
        {
            "type": "desktop_action_queue_job",
            "state": "done",
            "job_id": "job-1",
            "action": "click",
            "wait_ms": 4,
            "elapsed_ms": 11,
            "ts": 1.65,
        },
        {
            "type": "desktop_action_queue_job",
            "state": "queued",
            "job_id": "job-2",
            "action": "drag",
            "ts": 1.66,
        },
        {
            "type": "desktop_action_queue_job",
            "state": "canceled",
            "job_id": "job-2",
            "action": "drag",
            "wait_ms": 6,
            "ts": 1.67,
        },
        {
            "type": "desktop_action_queue_job",
            "state": "queued",
            "job_id": "job-3",
            "action": "type_text",
            "ts": 1.68,
        },
        {
            "type": "desktop_action_queue_job",
            "state": "error",
            "job_id": "job-3",
            "action": "type_text",
            "wait_ms": 3,
            "elapsed_ms": 5,
            "ts": 1.69,
        },
        {"type": "agent_recovery", "kind": "structured_replan", "ts": 1.7},
        {"type": "memory", "action": "reuse_suggested", "ts": 1.8},
        {"type": "agent_step", "step": 2, "ts": 2.0},
        {"type": "agent_tool", "tool": "reuse_skill", "elapsed_ms": 20, "ts": 2.05},
        {"type": "agent_tool", "tool": "vlm_describe", "elapsed_ms": 300, "ts": 2.1},
        {"type": "agent_done", "run_id": "r1", "status": "done", "ts": 2.2},
    ]

    out = summary.summarize_events(events)
    metrics = out["low_latency"]

    assert metrics["action_decision_latency"]["count"] == 2
    assert metrics["action_decision_latency"]["p50_ms"] == 425
    assert metrics["action_decision_latency"]["max_ms"] == 450
    assert metrics["action_execution_latency"]["avg_ms"] == 120
    assert metrics["post_action_verify_latency"]["avg_ms"] == 18
    assert metrics["screen_capture_latency"]["avg_ms"] == 7
    assert metrics["perception_latency"]["avg_ms"] == 280
    assert metrics["replan_count"] == 1
    assert metrics["vlm_call_count"] == 2
    assert metrics["skill_reuse_count"] == 1
    assert metrics["skill_reuse_rate"] == 1.0
    assert metrics["screen_watch_auto_starts"] == 1
    assert metrics["screen_watch_running_tool_events"] == 1
    assert metrics["incremental_perception"]["prefetches"] == 1
    assert metrics["incremental_perception"]["action_delta_prefetches"] == 1
    assert metrics["incremental_perception"]["transient_prefetches"] == 1
    assert metrics["incremental_perception"]["elements"] == 4
    assert metrics["incremental_perception"]["latency"]["avg_ms"] == 90
    assert metrics["desktop_action_queue"]["actions"] == 2
    assert metrics["desktop_action_queue"]["errors"] == 1
    assert metrics["desktop_action_queue"]["reentrant"] == 1
    assert metrics["desktop_action_queue"]["scopes"] == {
        "physical": 1,
        "physical_clipboard": 1,
    }
    assert metrics["desktop_action_queue"]["wait_latency"]["max_ms"] == 5
    assert metrics["desktop_action_queue"]["execution_latency"]["p50_ms"] == 13.5
    assert metrics["desktop_action_queue"]["execution_latency"]["max_ms"] == 20
    assert metrics["desktop_action_queue"]["async_jobs"]["submitted"] == 3
    assert metrics["desktop_action_queue"]["async_jobs"]["completed"] == 1
    assert metrics["desktop_action_queue"]["async_jobs"]["errors"] == 1
    assert metrics["desktop_action_queue"]["async_jobs"]["canceled"] == 1
    assert metrics["desktop_action_queue"]["async_jobs"]["wait_latency"]["max_ms"] == 6
    assert metrics["desktop_action_queue"]["async_jobs"]["execution_latency"]["p50_ms"] == 8


def test_summarize_events_ignores_empty_vlm_placeholder():
    events = [
        {"type": "agent_start", "run_id": "r1", "task": "demo", "ts": 1.0},
        {
            "type": "perception",
            "dims": {
                "ocr": {"count": 2, "ms": 80},
                "vlm": {"count": 0, "ms": 0},
            },
            "ts": 1.1,
        },
        {"type": "agent_done", "run_id": "r1", "status": "done", "ts": 1.2},
    ]

    out = summary.summarize_events(events)

    assert out["low_latency"]["vlm_call_count"] == 0
    assert "vlm" not in out["perception"]


def test_aggregate_reports_treats_policy_failure_as_not_passed():
    reports = [
        {
            "run_id": "r1",
            "status": "done",
            "ground_truth_passed": True,
            "ground_truth_status": "grounded",
            "policy_passed": False,
            "performance_budget": {"passed": True},
        },
        {
            "run_id": "r2",
            "status": "done",
            "ground_truth_passed": True,
            "ground_truth_status": "grounded",
            "policy_passed": True,
            "performance_budget": {"passed": True},
        },
    ]

    out = summary.aggregate_reports(reports)

    assert out["passed_or_done"] == 1


def test_aggregate_reports_treats_unverified_done_as_not_passed():
    reports = [
        {
            "run_id": "r1",
            "status": "done",
            "ground_truth_status": "grounded",
            "ground_truth_passed": True,
            "policy_passed": True,
            "performance_budget": {"passed": True},
            "done_checks": [
                {
                    "passed": True,
                    "unverified": True,
                    "reason": "Done force-accepted after done_max_rejects",
                }
            ],
        },
        {
            "run_id": "r2",
            "status": "done",
            "ground_truth_status": "grounded",
            "ground_truth_passed": True,
            "policy_passed": True,
            "performance_budget": {"passed": True},
            "done_checks": [{"passed": True, "reason": "fresh visible evidence"}],
        },
    ]

    out = summary.aggregate_reports(reports)

    assert out["passed_or_done"] == 1
    assert out["unverified_done_count"] == 1


def test_aggregate_reports_requires_passing_performance_budget():
    reports = [
        {
            "run_id": "fast",
            "status": "done",
            "ground_truth_status": "grounded",
            "ground_truth_passed": True,
            "policy_passed": True,
            "performance_budget": {"passed": True},
        },
        {
            "run_id": "slow",
            "status": "done",
            "ground_truth_status": "grounded",
            "ground_truth_passed": True,
            "policy_passed": True,
            "performance_budget": {"passed": False},
        },
        {
            "run_id": "missing-budget",
            "status": "done",
            "ground_truth_status": "grounded",
            "ground_truth_passed": True,
            "policy_passed": True,
        },
    ]

    out = summary.aggregate_reports(reports)

    assert out["verified_successes"] == 1
    assert out["performance_budget_failed_count"] == 2


def test_aggregate_reports_never_counts_ungrounded_or_stopped_runs():
    reports = [
        {
            "run_id": "self-reported",
            "status": "done",
            "ground_truth_status": "ungrounded",
            "policy_passed": True,
            "performance_budget": {"passed": True},
        },
        {
            "run_id": "legacy-missing-ground-truth",
            "status": "done",
            "policy_passed": True,
            "performance_budget": {"passed": True},
        },
        {
            "run_id": "stopped-after-check",
            "status": "stopped",
            "ground_truth_status": "grounded",
            "ground_truth_passed": True,
            "policy_passed": True,
            "performance_budget": {"passed": True},
        },
    ]

    out = summary.aggregate_reports(reports)

    assert out["verified_successes"] == 0
    assert out["grounded_total"] == 1
    assert out["ungrounded_count"] == 2


def test_gui_eval_provenance_is_reproducible_and_excludes_secrets(monkeypatch):
    import json

    from scripts import gui_eval_run

    config = {
        "api": {
            "provider": "example-provider",
            "model": "vision-model",
            "temperature": 0.2,
            "max_tokens": 2048,
            "thinking": False,
            "api_key": "top-secret-api-key",
            "base_url": "https://secret-endpoint.invalid",
        },
        "vlm": {
            "enabled": True,
            "model": "vlm-model",
            "thinking": True,
            "api_key": "top-secret-vlm-key",
            "base_url": "https://secret-vlm.invalid",
        },
        "perception": {
            "uia": {"enabled": False, "priority": 5, "secret": "do-not-copy"},
            "vlm": {"enabled": True, "priority": 1},
        },
        "fusion": {"native_vision_direct": True, "unknown_secret": "do-not-copy"},
        "controller": {
            "target_window_capture_enabled": True,
            "multi_input_cooperation": False,
            "cooperative_cursor_tolerance_px": 12,
            "visual_closed_loop": True,
            "visual_preflight_change_ratio": 0.0025,
            "visual_preflight_target_change_ratio": 0.01,
            "visual_preflight_target_margin": 60,
        },
        "ocr": {"use_gpu": True, "min_confidence": 0.6},
    }
    monkeypatch.setattr(gui_eval_run, "_request_json", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(
        gui_eval_run,
        "_git_metadata",
        lambda: {"commit": "harness-commit", "branch": "harness", "dirty": False},
    )
    monkeypatch.setattr(gui_eval_run, "_display_metadata", lambda: {"system_dpi": 144})
    status = {
        "runtime": {"git": {"commit": "server-commit", "branch": "master", "dirty": False}},
        "enabled_dimensions": ["vlm"],
        "ocr_provider": "DirectML",
        "vlm_configured": True,
    }

    out = gui_eval_run._collect_provenance("http://127.0.0.1:8765", "control-secret", status)
    serialized = json.dumps(out, ensure_ascii=False)

    assert out["git"] == {"commit": "server-commit", "branch": "master", "dirty": False}
    assert out["config_captured"] is True
    assert out["eval_harness_git"]["commit"] == "harness-commit"
    assert out["model"]["model"] == "vision-model"
    assert out["model"]["resolved_provider"] == "example-provider"
    assert out["model"]["supports_vision"] is True
    assert out["model"]["endpoint_sha256"] == gui_eval_run._endpoint_fingerprint(
        "https://secret-endpoint.invalid"
    )
    assert out["vlm"]["endpoint_sha256"] == gui_eval_run._endpoint_fingerprint(
        "https://secret-vlm.invalid"
    )
    assert out["visual_config"]["perception"]["uia"] == {"enabled": False, "priority": 5}
    assert out["visual_config"]["fusion"]["native_vision_direct"] is True
    assert out["visual_config"]["controller"] == {
        "target_window_capture_enabled": True,
        "multi_input_cooperation": False,
        "cooperative_cursor_tolerance_px": 12,
        "visual_closed_loop": True,
        "visual_preflight_change_ratio": 0.0025,
        "visual_preflight_target_change_ratio": 0.01,
        "visual_preflight_target_margin": 60,
    }
    assert out["display"]["system_dpi"] == 144
    assert len(out["visual_config_sha256"]) == 64
    for secret in (
        "top-secret-api-key",
        "top-secret-vlm-key",
        "control-secret",
        "secret-endpoint",
        "secret-vlm",
        "do-not-copy",
    ):
        assert secret not in serialized


def test_gui_eval_marks_missing_checks_as_ungrounded():
    from scripts.gui_eval_run import _record_ground_truth

    report = {"status": "done", "ground_truth_passed": True}
    _record_ground_truth(report, [])
    assert report["ground_truth_status"] == "ungrounded"
    assert "ground_truth_passed" not in report

    _record_ground_truth(report, [{"name": "external", "passed": True}])
    assert report["ground_truth_status"] == "grounded"
    assert report["ground_truth_passed"] is True


def test_gui_eval_trace_check_can_require_visible_literal():
    from scripts.gui_eval_run import _run_trace_checks

    events = [
        {
            "type": "agent_tool",
            "tool": "observe",
            "result_preview": '{"visible":"CALIBRATION_OK screen=71,324"}',
        }
    ]
    checks = _run_trace_checks(events, [{
        "name": "visible_result",
        "event_type": "agent_tool",
        "field": "result_preview",
        "contains": "CALIBRATION_OK",
    }])

    assert checks[0]["passed"] is True
    assert checks[0]["expected"] == {"contains": "CALIBRATION_OK"}


def test_gui_eval_trace_check_can_match_event_type_and_where_only():
    from scripts.gui_eval_run import _run_trace_checks

    events = [
        {"type": "action", "action": "observe"},
        {"type": "action", "action": "click"},
    ]
    checks = _run_trace_checks(events, [{
        "name": "real_gui_action",
        "event_type": "action",
        "where": {"action": ["click", "type_text", "hotkey"]},
    }])

    assert checks[0]["passed"] is True
    assert checks[0]["match_count"] == 1


def test_gui_eval_trace_check_rejects_task_echo_and_requires_prior_action():
    from scripts.gui_eval_run import _run_trace_checks

    echo = {
        "type": "agent_tool",
        "tool": "revise_plan",
        "result_preview": "Verify CALIBRATION_OK after clicking",
    }
    normalized = {
        "type": "agent_tool",
        "tool": "click",
        "args": {
            "coordinate_space": "current_frame_normalized_0_1000",
            "source_frame_coordinates": {"frame_x": 42, "frame_y": 300},
        },
    }
    visible = {
        "type": "agent_tool",
        "tool": "observe",
        "result_preview": "CALIBRATION_OK screen=123,456 client=20,30",
    }
    spec = [{
        "name": "causal_visible_result",
        "event_type": "agent_tool",
        "where": {"tool": ["observe", "read_text"]},
        "contains": "CALIBRATION_OK screen=",
        "requires_prior": {
            "event_type": "agent_tool",
            "where": {"tool": ["click", "run_action_batch"]},
            "field": "args",
            "contains": "current_frame_normalized_0_1000",
        },
    }]

    assert _run_trace_checks([echo, visible], spec)[0]["passed"] is False
    result = _run_trace_checks([echo, normalized, visible], spec)[0]
    assert result["passed"] is True
    assert result["match_count"] == 1
    assert result["where"] == {"tool": ["observe", "read_text"]}


def test_gui_eval_external_uia_check_reads_final_target_window(monkeypatch):
    from types import SimpleNamespace

    from scripts.gui_eval_run import _run_uia_checks
    from winpilot.perception import uia

    monkeypatch.setattr(uia, "collect", lambda hwnd: [
        SimpleNamespace(text=f"CALIBRATION_OK screen={hwnd},456 client=20,30"),
    ])
    events = [
        {"type": "visual_feedback", "hwnd": 77, "phase": "initial"},
        {"type": "visual_feedback", "hwnd": 88, "phase": "post_action"},
    ]

    checks = _run_uia_checks(events, [{
        "name": "external_visible_result",
        "contains": "CALIBRATION_OK screen=",
    }])

    assert checks[0]["passed"] is True
    assert checks[0]["source"] == "external_uia"
    assert checks[0]["hwnd"] == 88


def test_calculator_suites_require_real_action_and_external_visible_result():
    from scripts.gui_eval_run import SUITES

    cases = {
        case["id"]: case
        for suite in SUITES.values()
        for case in suite
        if case["id"] in {"calculator_sum", "deep_calc_chain"}
    }

    assert set(cases) == {"calculator_sum", "deep_calc_chain"}
    for case in cases.values():
        assert case["uia_checks"]
        assert all(
            check.get("event_type") == "action"
            and check.get("where", {}).get("action") == ["click", "type_text", "hotkey"]
            for check in case["trace_checks"]
        )
        assert all(check.get("field") != "result_preview" for check in case["trace_checks"])

    smoke = cases["calculator_sum"]
    assert any("CalculatorApp.exe" in command for command in smoke.get("setup", []))
    assert any("CalculatorApp.exe" in command for command in smoke.get("cleanup", []))


def test_gui_eval_budget_does_not_treat_missing_metrics_as_zero():
    from scripts.gui_eval_run import _budget_result

    budgets = {
        "duration_s": 45.0,
        "tool_count": 6,
        "observe_avg_ms": 500.0,
        "cost_cny": 1.5,
    }
    report = {
        "duration_s": 10.0,
        "tool_count": 2,
        "summary": {
            "tool_counts": {"observe": 1},
            "tool_latency": {},
        },
        "status_endpoint": {
            "usage": {
                "prompt_tokens": 100,
                "cost_cny": 0.0,
                "cost_estimated": False,
            },
        },
    }

    result = _budget_result(report, budgets)

    assert result["checks"]["observe_avg_ms"] is False
    assert result["checks"]["cost_cny"] is None
    assert result["availability"] == {"observe_avg_ms": False, "cost_cny": False}
    assert result["passed"] is False


def test_gui_eval_budget_does_not_pass_when_only_cost_is_unknown():
    from scripts.gui_eval_run import _budget_result

    budgets = {
        "duration_s": 45.0,
        "tool_count": 6,
        "observe_avg_ms": 500.0,
        "cost_cny": 1.5,
    }
    report = {
        "duration_s": 10.0,
        "tool_count": 2,
        "summary": {
            "tool_counts": {"observe": 1},
            "tool_latency": {"observe": {"avg_ms": 120}},
        },
        "status_endpoint": {
            "usage": {
                "prompt_tokens": 100,
                "cost_cny": 0.0,
                "cost_estimated": False,
            },
        },
    }

    result = _budget_result(report, budgets)

    assert result["checks"] == {
        "duration_s": True,
        "tool_count": True,
        "observe_avg_ms": True,
        "cost_cny": None,
    }
    assert result["availability"] == {"observe_avg_ms": True, "cost_cny": False}
    assert result["passed"] is False


def test_gui_eval_budget_can_omit_unavailable_cost_dimension():
    from scripts.gui_eval_run import _budget_result

    report = {
        "duration_s": 10.0,
        "tool_count": 2,
        "summary": {"tool_counts": {"observe": 0}, "tool_latency": {}},
        "status_endpoint": {
            "usage": {"cost_cny": 0.0, "cost_estimated": False},
        },
    }
    budgets = {
        "duration_s": 45.0,
        "tool_count": 6,
        "observe_avg_ms": 500.0,
    }

    result = _budget_result(report, budgets)

    assert result["checks"] == {
        "duration_s": True,
        "tool_count": True,
        "observe_avg_ms": True,
    }
    assert result["availability"]["cost_cny"] is False
    assert result["passed"] is True


def test_runtime_proof_metrics_cover_stale_frames_interventions_and_input_arbitration():
    from winpilot.eval import summary

    events = [
        {"type": "agent_start", "run_id": "proof-1", "ts": 1.0},
        {
            "type": "visual_preflight",
            "changed": True,
            "capture_latency_ms": 4,
            "ts": 1.1,
        },
        {"type": "chat", "interject": True, "ts": 1.2},
        {
            "type": "agent_batch_action_start",
            "event_name": "action.started",
            "action_id": "batch-1:1",
            "action_envelope": {"action_id": "batch-1:1"},
            "ts": 1.3,
        },
        {
            "type": "agent_batch_action",
            "event_name": "action.finished",
            "action_id": "batch-1:1",
            "action_envelope": {"action_id": "batch-1:1"},
            "tool": "type_text",
            "result_meta": {"cooperative_input": True, "blocked": True},
            "elapsed_ms": 1,
            "ts": 1.4,
        },
        {
            "type": "cooperative_input",
            "state": "human_cursor_won",
            "ts": 1.5,
        },
        {"type": "agent_done", "status": "reply", "ts": 1.6},
    ]

    proof = summary.summarize_events(events)["low_latency"]["runtime_proof"]

    assert proof == {
        "visual_preflight_checks": 1,
        "stale_frame_blocks": 1,
        "user_interventions": 1,
        "cooperative_input_blocks": 1,
        "human_cursor_wins": 1,
        "action_lifecycle_started": 1,
        "action_lifecycle_finished": 1,
        "action_lifecycle_paired": 1,
    }


def test_runtime_proof_consumes_real_controller_result_metadata():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.eval import summary
    from winpilot.fast.controller import _result_meta

    result = ToolResult(
        '{"ok": false, "verified": false, "blocked": true, '
        '"cooperative_input": true, "error": "user owns another window"}'
    )
    meta = _result_meta(result)
    proof = summary.summarize_events([{
        "type": "agent_batch_action",
        "event_name": "action.finished",
        "action_id": "batch-1:1",
        "tool": "type_text",
        "result_meta": meta,
        "ts": 1.0,
    }])["low_latency"]["runtime_proof"]

    assert meta["blocked"] is True
    assert meta["cooperative_input"] is True
    assert "user owns" in meta["error"]
    assert proof["cooperative_input_blocks"] == 1


def test_visual_judge_network_call_is_fresh_and_mockable(monkeypatch):
    import json

    from scripts import gui_eval_run

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            content = json.dumps({
                "passed": True,
                "confidence": 0.97,
                "reason": "The requested state is visibly present.",
                "observed": ["Visible result"],
                "issues": [],
            })
            return json.dumps({
                "choices": [{"message": {"content": content}}],
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(gui_eval_run.urllib.request, "urlopen", fake_urlopen)
    verdict = gui_eval_run._request_visual_judge(
        {
            "base_url": "https://judge.invalid/v1",
            "api_key": "judge-secret",
            "model": "independent-vlm",
            "thinking": False,
        },
        "The visible result says OK.",
        "YWJj",
    )

    assert verdict["passed"] is True
    assert captured["url"] == "https://judge.invalid/v1/chat/completions"
    assert captured["timeout"] == 120
    assert captured["payload"]["model"] == "independent-vlm"
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["payload"]["messages"][0]["content"][0]["image_url"]["url"].endswith("YWJj")
    assert captured["headers"]["Authorization"] == "Bearer judge-secret"


def test_visual_ground_truth_persists_exact_judge_frame(monkeypatch, tmp_path):
    import hashlib
    from pathlib import Path

    import numpy as np

    from scripts import gui_eval_run
    from winpilot.utils import screenshot
    from winpilot.utils.screenshot import Region

    seen = {}
    monkeypatch.setattr(screenshot, "virtual_screen_region", lambda: Region(0, 0, 100, 80))

    def fake_capture(region):
        seen["region"] = region
        return np.zeros((16, 16, 3), dtype=np.uint8)

    monkeypatch.setattr(screenshot, "capture", fake_capture)
    evidence = gui_eval_run._capture_visual_judge_frame(
        {"criteria": "visible state", "region": [10, 12, 16, 16]},
        [],
        tmp_path,
        "run-1",
        "region-case",
    )

    path = Path(evidence["screenshot_path"])
    payload = path.read_bytes()
    assert seen["region"] == Region(10, 12, 16, 16)
    assert evidence["scope"] == "region"
    assert evidence["capture_dimensions"] == {"width": 16, "height": 16}
    assert evidence["judge_dimensions"] == {"width": 16, "height": 16}
    assert evidence["frame_sha256"] == hashlib.sha256(payload).hexdigest()
    assert evidence["image_b64"]


def test_visual_ground_truth_verified_and_failed_are_strict(monkeypatch, tmp_path):
    from scripts import gui_eval_run

    execution_events = [{"type": "agent_decision", "model": "executor-model"}]

    def fake_frame(*_args, **_kwargs):
        return {
            "scope": "target_window",
            "target_hwnd": 55,
            "region": {"left": 1, "top": 2, "width": 3, "height": 4},
            "frame_sha256": "a" * 64,
            "screenshot_path": "evidence.jpg",
            "image_b64": "YWJj",
        }

    monkeypatch.setattr(gui_eval_run, "_capture_visual_judge_frame", fake_frame)
    monkeypatch.setattr(gui_eval_run, "_visual_judge_settings", lambda: {
        "enabled": True,
        "base_url": "https://judge.invalid/v1",
        "api_key": "secret",
        "model": "judge-vlm",
        "thinking": False,
        "verify_min_confidence": 0.8,
        "configuration_issue": "",
    })
    monkeypatch.setattr(gui_eval_run, "_request_visual_judge", lambda *_args: {
        "passed": True,
        "confidence": 0.95,
        "reason": "All requested pixels are visibly present.",
        "observed": ["Visible state"],
        "issues": [],
    })

    verified = gui_eval_run._run_visual_check(
        {"criteria": "The state is visible."},
        execution_events,
        tmp_path,
        "run-verified",
        "case",
        execution_model="executor-model",
    )
    assert verified["status"] == "verified"
    assert verified["passed"] is True
    assert verified["judge_model"] == "judge-vlm"
    assert verified["same_as_execution_model"] is False
    assert verified["frame_sha256"] == "a" * 64
    assert verified["verdict"]["passed"] is True

    monkeypatch.setattr(gui_eval_run, "_request_visual_judge", lambda *_args: {
        "passed": False,
        "confidence": 0.99,
        "reason": "The required visible state is missing.",
        "observed": ["Blank area"],
        "issues": ["Missing result"],
    })
    failed = gui_eval_run._run_visual_check(
        "The state is visible.",
        execution_events,
        tmp_path,
        "run-failed",
        "case",
        execution_model="executor-model",
    )
    assert failed["status"] == "failed"
    assert failed["passed"] is False
    assert failed["reason"] == "The required visible state is missing."


def test_visual_judge_error_keeps_whole_report_ungrounded(monkeypatch, tmp_path):
    from scripts import gui_eval_run

    execution_events = [{"type": "agent_decision", "model": "executor-model"}]

    monkeypatch.setattr(gui_eval_run, "_capture_visual_judge_frame", lambda *_args, **_kwargs: {
        "scope": "full_desktop",
        "target_hwnd": None,
        "region": {"left": 0, "top": 0, "width": 10, "height": 10},
        "frame_sha256": "b" * 64,
        "screenshot_path": "terminal.jpg",
        "image_b64": "YWJj",
    })
    monkeypatch.setattr(gui_eval_run, "_visual_judge_settings", lambda: {
        "enabled": True,
        "base_url": "https://judge.invalid/v1",
        "model": "judge-vlm",
        "verify_min_confidence": 0.8,
        "configuration_issue": "",
    })

    def fail_network(*_args):
        raise TimeoutError("judge timed out")

    monkeypatch.setattr(gui_eval_run, "_request_visual_judge", fail_network)
    visual = gui_eval_run._run_visual_check(
        {"criteria": "Visible success", "scope": "full_desktop"},
        execution_events,
        tmp_path,
        "run-timeout",
        "case",
        execution_model="executor-model",
    )
    report = {}
    gui_eval_run._record_ground_truth(
        report,
        [{"name": "deterministic", "passed": True}, visual],
    )

    assert visual["status"] == "ungrounded"
    assert "passed" not in visual
    assert visual["judge_model"] == "judge-vlm"
    assert visual["frame_sha256"] == "b" * 64
    assert "TimeoutError" in visual["reason"]
    assert report["ground_truth_status"] == "ungrounded"
    assert "ground_truth_passed" not in report


def test_latest_target_hwnd_uses_newest_authoritative_event():
    from scripts.gui_eval_run import _latest_target_hwnd

    events = [
        {"type": "visual_feedback", "hwnd": 11},
        {
            "type": "agent_tool",
            "event_name": "action.finished",
            "action_envelope": {"target": {"hwnd": 22}},
        },
    ]

    assert _latest_target_hwnd(events) == 22


def test_visual_evidence_path_sanitizes_run_id(monkeypatch, tmp_path):
    from pathlib import Path

    import numpy as np

    from scripts import gui_eval_run
    from winpilot.utils import screenshot
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(screenshot, "virtual_screen_region", lambda: Region(0, 0, 100, 100))
    monkeypatch.setattr(
        screenshot,
        "capture",
        lambda _region: np.zeros((16, 16, 3), dtype=np.uint8),
    )

    evidence = gui_eval_run._capture_visual_judge_frame(
        {"criteria": "visible", "region": [1, 1, 16, 16]},
        [],
        tmp_path,
        "../../outside",
        "case",
    )

    path = Path(evidence["screenshot_path"]).resolve()
    assert path.parent == (tmp_path / "visual_ground_truth").resolve()
    assert ".." not in path.name


def test_execution_model_cannot_judge_itself(monkeypatch, tmp_path):
    from scripts import gui_eval_run

    monkeypatch.setattr(gui_eval_run, "_capture_visual_judge_frame", lambda *_args, **_kwargs: {
        "scope": "full_desktop",
        "target_hwnd": None,
        "region": {"left": 0, "top": 0, "width": 20, "height": 20},
        "frame_sha256": "c" * 64,
        "screenshot_path": "terminal.jpg",
        "image_b64": "YWJj",
    })
    monkeypatch.setattr(gui_eval_run, "_visual_judge_settings", lambda: {
        "enabled": True,
        "base_url": "https://judge.invalid/v1",
        "model": "vendor/same-model:latest",
        "verify_min_confidence": 0.8,
        "configuration_issue": "",
    })
    called = {"judge": False}

    def should_not_run(*_args):
        called["judge"] = True
        raise AssertionError("self judge must not run")

    monkeypatch.setattr(gui_eval_run, "_request_visual_judge", should_not_run)
    visual = gui_eval_run._run_visual_check(
        {"criteria": "visible", "scope": "full_desktop"},
        [{"type": "agent_decision", "model": "same-model"}],
        tmp_path,
        "run-self",
        "case",
        execution_model="same-model",
    )

    assert visual["status"] == "ungrounded"
    assert visual["same_as_execution_model"] is True
    assert visual["judge_identity"]["canonical_model"] == "same-model"
    assert called["judge"] is False


def test_visual_judge_requires_trace_model_to_match_provenance(monkeypatch, tmp_path):
    from scripts import gui_eval_run

    called = {"capture": False}

    def should_not_capture(*_args, **_kwargs):
        called["capture"] = True
        raise AssertionError("a mismatched execution trace must fail before capture")

    monkeypatch.setattr(gui_eval_run, "_capture_visual_judge_frame", should_not_capture)
    visual = gui_eval_run._run_visual_check(
        {"criteria": "visible", "scope": "full_desktop"},
        [{"type": "agent_decision", "model": "mimo-v2.5-pro"}],
        tmp_path,
        "run-model-mismatch",
        "case",
        execution_model="mimo-v2.5",
        execution_identity={
            "resolved_provider": "openai_compatible",
            "endpoint_sha256": "d" * 64,
        },
    )

    assert visual["status"] == "ungrounded"
    assert visual["trace_execution_models"] == ["mimo-v2.5-pro"]
    assert visual["execution_identity"] == {
        "provider": "openai_compatible",
        "canonical_model": "mimo-v2.5",
        "endpoint_sha256": "d" * 64,
    }
    assert "stable configured execution model" in visual["reason"]
    assert called["capture"] is False


def test_visual_action_causality_requires_successful_changed_screenshot_pair(monkeypatch):
    import cv2
    import numpy as np

    from scripts import gui_eval_run

    before = np.full((80, 100, 3), 255, dtype=np.uint8)
    after = before.copy()
    cv2.line(after, (10, 10), (80, 60), (0, 0, 0), 4)
    ok_before, before_buf = cv2.imencode(".jpg", before)
    ok_after, after_buf = cv2.imencode(".jpg", after)
    assert ok_before and ok_after
    payloads = {
        "before.jpg": before_buf.tobytes(),
        "after.jpg": after_buf.tobytes(),
    }
    monkeypatch.setattr(
        gui_eval_run,
        "_request_trace_screenshot",
        lambda _base, _token, _run, name: payloads[name],
    )
    region = {"left": 10, "top": 20, "width": 100, "height": 80}
    envelope = {
        "action_id": "a1",
        "type": "draw_strokes",
        "inputs": {},
        "target": {"hwnd": 55, "region": region},
        "policy": {"requires_post_frame": True},
    }
    events = [
        {
            "type": "agent_tool_start",
            "event_name": "action.started",
            "action_id": "a1",
            "tool": "draw_strokes",
            "action_envelope": envelope,
            "screenshot": "run-1/before.jpg",
            "screenshot_region": region,
            "baseline_capture_source": "sync_pre_action",
        },
        {
            "type": "agent_tool",
            "event_name": "action.finished",
            "action_id": "a1",
            "tool": "draw_strokes",
            "action_envelope": envelope,
            "result_meta": {"ok": True, "blocked": False},
            "screenshot": "run-1/after.jpg",
            "screenshot_region": region,
            "post_capture_source": "sync_post_action",
        },
    ]

    evidence = gui_eval_run._visual_action_causality(
        events,
        {"required_actions": ["draw_strokes"], "require_visual_change": True},
        base_url="http://server.invalid",
        token="secret",
        run_id="run-1",
    )

    assert evidence["status"] == "verified"
    assert evidence["passed"] is True
    assert evidence["action_id"] == "a1"
    assert evidence["changed_pixel_ratio"] > 0.0005


def test_visual_action_causality_rejects_async_post_action_capture():
    from scripts import gui_eval_run

    region = {"left": 10, "top": 20, "width": 100, "height": 80}
    envelope = {
        "action_id": "a1",
        "type": "draw_strokes",
        "inputs": {},
        "target": {"hwnd": 55, "region": region},
        "policy": {"requires_post_frame": True},
    }
    events = [
        {
            "type": "agent_tool_start",
            "event_name": "action.started",
            "action_id": "a1",
            "tool": "draw_strokes",
            "action_envelope": envelope,
            "screenshot": "run-1/before.jpg",
            "screenshot_region": region,
            "baseline_capture_source": "sync_pre_action",
        },
        {
            "type": "agent_tool",
            "event_name": "action.finished",
            "action_id": "a1",
            "tool": "draw_strokes",
            "action_envelope": envelope,
            "result_meta": {"ok": True, "blocked": False},
            "screenshot": "run-1/after.jpg",
            "screenshot_region": region,
            "post_capture_source": "perception_daemon",
        },
    ]

    evidence = gui_eval_run._visual_action_causality(
        events,
        {"required_actions": ["draw_strokes"], "require_visual_change": False},
        base_url="http://server.invalid",
        token="secret",
        run_id="run-1",
    )

    assert evidence["status"] == "ungrounded"
    assert evidence["successful_action_count"] == 1
    assert evidence["valid_lifecycle_pair_count"] == 0
    assert evidence["validation_errors"] == [
        "a1: result was not synchronously post-captured"
    ]


def test_visual_action_causality_handles_missing_target_region_fail_closed():
    from scripts import gui_eval_run

    envelope = {
        "action_id": "a1",
        "type": "draw_strokes",
        "inputs": {},
        "target": {"hwnd": 55, "region": None},
        "policy": {"requires_post_frame": True},
    }
    events = [
        {
            "type": "agent_tool_start",
            "event_name": "action.started",
            "action_id": "a1",
            "tool": "draw_strokes",
            "action_envelope": envelope,
        },
        {
            "type": "agent_tool",
            "event_name": "action.finished",
            "action_id": "a1",
            "tool": "draw_strokes",
            "action_envelope": envelope,
            "result_meta": {"ok": True, "blocked": False},
        },
    ]

    evidence = gui_eval_run._visual_action_causality(
        events,
        {"required_actions": ["draw_strokes"], "require_visual_change": True},
        base_url="http://server.invalid",
        token="secret",
        run_id="run-1",
    )

    assert evidence["status"] == "ungrounded"
    assert evidence["successful_action_count"] == 1
    assert evidence["valid_lifecycle_pair_count"] == 0
    assert evidence["validation_errors"] == [
        "a1: immutable target hwnd/region is missing"
    ]


def test_visual_action_causality_does_not_skip_malformed_first_required_action():
    from scripts import gui_eval_run

    region = {"left": 10, "top": 20, "width": 100, "height": 80}
    malformed = {
        "action_id": "bad",
        "type": "run_action_batch",
        "inputs": {},
        "target": {"hwnd": 55, "region": region},
        "policy": {"requires_post_frame": True},
    }
    valid = {
        "action_id": "good",
        "type": "draw_strokes",
        "inputs": {},
        "target": {"hwnd": 55, "region": region},
        "policy": {"requires_post_frame": True},
    }
    events = [
        {
            "type": "agent_tool_start",
            "event_name": "action.started",
            "action_id": "bad",
            "tool": "draw_strokes",
            "action_envelope": malformed,
            "screenshot": "run-1/bad-before.jpg",
            "screenshot_region": region,
            "baseline_capture_source": "sync_pre_action",
        },
        {
            "type": "agent_tool",
            "event_name": "action.finished",
            "action_id": "bad",
            "tool": "run_action_batch",
            "action_envelope": malformed,
            "result_meta": {"ok": True, "blocked": False},
            "screenshot": "run-1/bad-after.jpg",
            "screenshot_region": region,
            "post_capture_source": "sync_post_action",
        },
        {
            "type": "agent_tool_start",
            "event_name": "action.started",
            "action_id": "good",
            "tool": "draw_strokes",
            "action_envelope": valid,
            "screenshot": "run-1/good-before.jpg",
            "screenshot_region": region,
            "baseline_capture_source": "sync_pre_action",
        },
        {
            "type": "agent_tool",
            "event_name": "action.finished",
            "action_id": "good",
            "tool": "draw_strokes",
            "action_envelope": valid,
            "result_meta": {"ok": True, "blocked": False},
            "screenshot": "run-1/good-after.jpg",
            "screenshot_region": region,
            "post_capture_source": "sync_post_action",
        },
    ]

    evidence = gui_eval_run._visual_action_causality(
        events,
        {"required_actions": ["draw_strokes"], "require_visual_change": False},
        base_url="http://server.invalid",
        token="secret",
        run_id="run-1",
    )

    assert evidence["status"] == "ungrounded"
    assert evidence["successful_action_count"] == 1
    assert evidence["valid_lifecycle_pair_count"] == 0
    assert evidence["validation_errors"] == [
        "bad: tool and envelope type do not match"
    ]


def test_visual_action_causality_rejects_duplicate_action_ids():
    from scripts import gui_eval_run

    region = {"left": 10, "top": 20, "width": 100, "height": 80}
    envelope = {
        "action_id": "duplicate",
        "type": "draw_strokes",
        "inputs": {},
        "target": {"hwnd": 55, "region": region},
        "policy": {"requires_post_frame": True},
    }
    start = {
        "type": "agent_tool_start",
        "event_name": "action.started",
        "action_id": "duplicate",
        "tool": "draw_strokes",
        "action_envelope": envelope,
        "screenshot": "run-1/before.jpg",
        "screenshot_region": region,
        "baseline_capture_source": "sync_pre_action",
    }
    events = [
        start,
        {**start, "screenshot": "run-1/other-before.jpg"},
        {
            "type": "agent_tool",
            "event_name": "action.finished",
            "action_id": "duplicate",
            "tool": "draw_strokes",
            "action_envelope": envelope,
            "result_meta": {"ok": True, "blocked": False},
            "screenshot": "run-1/after.jpg",
            "screenshot_region": region,
            "post_capture_source": "sync_post_action",
        },
    ]

    evidence = gui_eval_run._visual_action_causality(
        events,
        {"required_actions": ["draw_strokes"], "require_visual_change": False},
        base_url="http://server.invalid",
        token="secret",
        run_id="run-1",
    )

    assert evidence["status"] == "ungrounded"
    assert evidence["validation_errors"] == [
        "duplicate: duplicate action.started events"
    ]


def test_visual_action_causality_uses_nested_batch_child_as_first_finished_pair(monkeypatch):
    import cv2
    import numpy as np

    from scripts import gui_eval_run

    before = np.full((80, 100, 3), 255, dtype=np.uint8)
    after = before.copy()
    cv2.line(after, (10, 10), (80, 60), (0, 0, 0), 4)
    ok_before, before_buf = cv2.imencode(".jpg", before)
    ok_after, after_buf = cv2.imencode(".jpg", after)
    assert ok_before and ok_after
    requested = []
    payloads = {
        "child-before.jpg": before_buf.tobytes(),
        "child-after.jpg": after_buf.tobytes(),
    }

    def screenshot(_base, _token, _run, name):
        requested.append(name)
        return payloads[name]

    monkeypatch.setattr(gui_eval_run, "_request_trace_screenshot", screenshot)
    region = {"left": 10, "top": 20, "width": 100, "height": 80}

    def envelope(action_id):
        return {
            "action_id": action_id,
            "type": "draw_strokes",
            "inputs": {},
            "target": {"hwnd": 55, "region": region},
            "policy": {"requires_post_frame": True},
        }

    outer = envelope("outer")
    child = envelope("batch:1")
    events = [
        {
            "type": "agent_tool_start",
            "event_name": "action.started",
            "action_id": "outer",
            "tool": "draw_strokes",
            "action_envelope": outer,
            "screenshot": "run-1/outer-before.jpg",
            "screenshot_region": region,
            "baseline_capture_source": "sync_pre_action",
        },
        {
            "type": "agent_batch_action_start",
            "event_name": "action.started",
            "action_id": "batch:1",
            "tool": "draw_strokes",
            "action_envelope": child,
            "screenshot": "run-1/child-before.jpg",
            "screenshot_region": region,
            "baseline_capture_source": "sync_pre_action",
        },
        {
            "type": "agent_batch_action",
            "event_name": "action.finished",
            "action_id": "batch:1",
            "tool": "draw_strokes",
            "action_envelope": child,
            "result_meta": {"ok": True, "blocked": False},
            "screenshot": "run-1/child-after.jpg",
            "screenshot_region": region,
            "post_capture_source": "sync_post_action",
        },
        {
            "type": "agent_tool",
            "event_name": "action.finished",
            "action_id": "outer",
            "tool": "draw_strokes",
            "action_envelope": outer,
            "result_meta": {"ok": True, "blocked": False},
            "screenshot": "run-1/outer-after.jpg",
            "screenshot_region": region,
            "post_capture_source": "sync_post_action",
        },
    ]

    evidence = gui_eval_run._visual_action_causality(
        events,
        {"required_actions": ["draw_strokes"], "require_visual_change": True},
        base_url="http://server.invalid",
        token="secret",
        run_id="run-1",
    )

    assert evidence["status"] == "verified"
    assert evidence["action_id"] == "batch:1"
    assert requested == ["child-before.jpg", "child-after.jpg"]


def test_visual_action_success_rejects_conflicting_or_terminal_metadata():
    from scripts.gui_eval_run import _successful_action

    assert _successful_action({"ok": True, "result_meta": {"ok": True}}) is True
    assert _successful_action({"ok": True, "result_meta": {"ok": False}}) is False
    assert _successful_action({"result_meta": {"ok": True, "blocked": True}}) is False
    assert _successful_action({"result_meta": {"ok": True}, "terminal": "fail"}) is False


def test_eval_frontend_graph_matches_agent_cockpit_contract():
    from scripts.gui_eval_run import _frontend_goal_graph

    graph = _frontend_goal_graph("type a date, then draw a cat", 40)

    assert graph["format"] == "arp/1"
    assert graph["metadata"] == {
        "source": "agent-cockpit",
        "routing": "verified-skill-first-agent-on-miss-or-failure",
    }
    assert [node["kind"] for node in graph["nodes"]] == [
        "start", "skill_replay", "agent_goal", "end",
    ]
    goal = next(node for node in graph["nodes"] if node["id"] == "goal")
    assert goal["config"] == {
        "goal": "type a date, then draw a cat",
        "max_steps": 40,
    }
    assert graph["policy"]["allow_shell"] is False
    assert graph["policy"]["require_evidence_on_done"] is True
