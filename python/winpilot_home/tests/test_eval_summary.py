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
    assert out["usage"]["cost_cny"] == 0.01
    assert out["perception"]["uia"]["avg_ms"] == 1000


def test_aggregate_reports_totals_usage_and_slow_tools():
    reports = [
        {
            "run_id": "r1",
            "status": "done",
            "duration_s": 2,
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "cost_cny": 0.1},
            "slow_tools": [{"tool": "observe", "elapsed_ms": 50}],
            "fast_gui": {"batch_calls": 1, "batch_actions": 3, "round_trips_saved": 2},
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
                "needs_observe": True,
                "metrics": {
                    "action_count": 3,
                    "controller_round_trips_saved": 2,
                    "screen_wait_count": 1,
                },
            },
            "ts": 1.2,
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
    assert out["fast_gui"]["run_state_phases"] == {"acting": 1}


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
