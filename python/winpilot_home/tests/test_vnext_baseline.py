"""P0 observation-only baseline tests."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

from winpilot.vnext.telemetry import LegacyBaselineObserver


def test_p0_observer_only_adds_metrics_to_existing_events() -> None:
    observer = LegacyBaselineObserver()
    start = {"type": "agent_start", "run_id": "r1", "ts": 10.0, "model_tool_count": 3, "model_tool_names": ["observe"]}
    original = copy.deepcopy(start)
    private_copy = copy.deepcopy(start)
    observer.observe(private_copy)
    assert start == original
    assert private_copy["type"] == original["type"]
    assert private_copy["run_id"] == original["run_id"]
    assert "vnext_baseline" in private_copy
    observer.observe({"type": "agent_tool_start", "run_id": "r1", "ts": 11.0, "step": 1, "tool": "click", "args": {"x": 1}})
    observer.observe({"type": "agent_tool", "run_id": "r1", "ts": 11.1, "step": 1, "tool": "click", "elapsed_ms": 8, "result_meta": {"no_progress_streak": 2}})
    done = {"type": "agent_done", "run_id": "r1", "status": "done"}
    observer.observe(done)
    snapshot = done["vnext_baseline"]
    assert snapshot["format"] == "vnext-baseline/0"
    assert snapshot["controller_elapsed_ms"] == 8
    assert snapshot["no_progress_streak"] == 2
    assert snapshot["turns"]


def test_p0_turn_attribution_uses_explicit_step_and_missing_values_stay_none() -> None:
    observer = LegacyBaselineObserver()
    observer.observe({"type": "agent_start", "run_id": "steps", "step": 1, "model_role": "visual"})
    observer.observe({"type": "agent_decision", "run_id": "steps", "step": 1, "model_role": "visual", "elapsed_ms": 12})
    observer.observe({"type": "agent_decision", "run_id": "steps", "step": 2, "model_role": "visual", "elapsed_ms": 7})
    snapshot = observer.snapshot("steps").to_dict()
    assert [turn["step"] for turn in snapshot["turns"]] == [1, 2]
    assert [turn["model_elapsed_ms"] for turn in snapshot["turns"]] == [12, 7]
    assert snapshot["prompt_context_chars"] is None
    assert snapshot["mission_card_chars"] is None
    assert snapshot["frame_refresh_count"] is None


def test_p0_observer_is_thread_safe_and_run_cleanup_is_bounded() -> None:
    observer = LegacyBaselineObserver(max_runs=3)

    def publish(index: int) -> None:
        run_id = f"concurrent-{index % 3}"
        observer.observe({"type": "agent_start", "run_id": run_id, "step": 1})
        observer.observe({"type": "agent_decision", "run_id": run_id, "step": 1, "elapsed_ms": 1})
        observer.observe({"type": "agent_tool", "run_id": run_id, "step": 1, "tool": "click", "elapsed_ms": 1})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(publish, range(60)))

    with observer._lock:  # bounded sidecar state is an intentional contract
        assert len(observer._runs) <= 3
    assert observer.snapshot("concurrent-0").turns
    observer.close_run("concurrent-0")
    assert observer.snapshot("concurrent-0").turns == ()
    observer.reset()
    assert observer.snapshot().turns == ()


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
    assert snapshot["false_done_count"] is None
