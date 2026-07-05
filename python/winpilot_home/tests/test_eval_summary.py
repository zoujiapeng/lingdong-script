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
            "duration_s": 2,
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "cost_cny": 0.1},
            "slow_tools": [{"tool": "observe", "elapsed_ms": 50}],
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
            "duration_s": 3,
            "usage": {"prompt_tokens": 20, "completion_tokens": 2, "cost_cny": 0.2},
            "slow_tools": [{"tool": "click", "elapsed_ms": 80}],
            "summary": {"fast_gui": {"batch_calls": 2, "batch_actions": 4, "round_trips_saved": 2}},
        },
    ]

    out = summary.aggregate_reports(reports)

    assert out["total"] == 2
    assert out["passed_or_done"] == 1
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
            "type": "desktop_action_queue",
            "state": "start",
            "action": "click",
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
    assert metrics["desktop_action_queue"]["actions"] == 2
    assert metrics["desktop_action_queue"]["errors"] == 1
    assert metrics["desktop_action_queue"]["reentrant"] == 1
    assert metrics["desktop_action_queue"]["wait_latency"]["max_ms"] == 5
    assert metrics["desktop_action_queue"]["execution_latency"]["p50_ms"] == 13.5
    assert metrics["desktop_action_queue"]["execution_latency"]["max_ms"] == 20


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
            "policy_passed": False,
        },
        {
            "run_id": "r2",
            "status": "done",
            "ground_truth_passed": True,
            "policy_passed": True,
        },
    ]

    out = summary.aggregate_reports(reports)

    assert out["passed_or_done"] == 1


def test_aggregate_reports_treats_unverified_done_as_not_passed():
    reports = [
        {
            "run_id": "r1",
            "status": "done",
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
            "done_checks": [{"passed": True, "reason": "fresh visible evidence"}],
        },
    ]

    out = summary.aggregate_reports(reports)

    assert out["passed_or_done"] == 1
    assert out["unverified_done_count"] == 1
