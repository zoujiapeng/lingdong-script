"""P0 observation-only baseline tests."""
from __future__ import annotations

import copy

from winpilot.vnext.telemetry import LegacyBaselineObserver


def test_p0_observer_only_adds_metrics_to_existing_events() -> None:
    observer = LegacyBaselineObserver()
    start = {"type": "agent_start", "run_id": "r1", "ts": 10.0, "model_tool_count": 3, "model_tool_names": ["observe"]}
    original = copy.deepcopy(start)
    observer.observe(start)
    assert start["type"] == original["type"]
    assert start["run_id"] == original["run_id"]
    assert "vnext_baseline" in start
    observer.observe({"type": "agent_tool_start", "run_id": "r1", "ts": 11.0, "tool": "click", "args": {"x": 1}})
    observer.observe({"type": "agent_tool", "run_id": "r1", "ts": 11.1, "tool": "click", "elapsed_ms": 8, "result_meta": {"no_progress_streak": 2}})
    done = {"type": "agent_done", "run_id": "r1", "status": "done"}
    observer.observe(done)
    snapshot = done["vnext_baseline"]
    assert snapshot["format"] == "vnext-baseline/0"
    assert snapshot["controller_elapsed_ms"] == 8
    assert snapshot["no_progress_streak"] == 2
    assert snapshot["turns"]


def test_p0_snapshot_exposes_optional_mission_and_profile_metrics() -> None:
    observer = LegacyBaselineObserver()
    observer.observe({
        "type": "agent_start",
        "run_id": "r2",
        "agent": "unified",
        "recommended_execution_depth": "standard",
        "effective_execution_depth": "direct",
        "execution_policy_override_reason": "legacy baseline",
        "required_obligation_count": 2,
        "verified_obligation_count": 1,
    })
    observer.observe({"type": "profile_call", "run_id": "r2"})
    observer.observe({"type": "agent_done", "run_id": "r2", "status": "done"})
    snapshot = observer.snapshot("r2").to_dict()
    assert snapshot["profile_call_count"] == 1
    assert snapshot["recommended_execution_depth"] == "standard"
    assert snapshot["effective_execution_depth"] == "direct"
    assert snapshot["required_obligation_count"] == 2
    assert snapshot["verified_obligation_count"] == 1
    assert snapshot["false_done_count"] == 0
