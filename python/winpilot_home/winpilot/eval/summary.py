"""Trace/eval summarization utilities.

This module is intentionally dependency-light so both the local CLI and the
FastAPI server can use the same accounting logic. It summarizes what actually
happened in a run: wall time, tool latency, usage, done-gate evidence, and slow
spots. The output is JSON-serializable and small enough for the frontend.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..config import ROOT_DIR, TRACES_DIR

EVALS_DIR = ROOT_DIR / "evals"
_ACTION_TOOL_NAMES = {
    "launch",
    "click",
    "click_element",
    "type_text",
    "hotkey",
    "scroll",
    "drag",
    "draw_strokes",
    "focus_window",
    "wait",
    "tray_click",
    "run_action_batch",
    "execute_subgoal",
    "clipboard_paste",
    "mouse_down",
    "mouse_up",
    "move_mouse",
}


def _read_jsonl(path: Path, limit: int = 10_000) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        return events
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(events) >= limit:
                break
    return events


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * pct)))
    return int(ordered[idx])


def _latency_summary(values: list[int]) -> dict[str, Any]:
    values = [int(v) for v in values if int(v) >= 0]
    return {
        "count": len(values),
        "avg_ms": round(statistics.mean(values), 1) if values else 0,
        "max_ms": max(values) if values else 0,
        "p95_ms": _percentile(values, 0.95),
    }


def _perception_dim_called(name: str, stat: dict[str, Any]) -> bool:
    if str(name) != "vlm":
        return stat.get("ms") is not None
    # Fusion may emit a placeholder VLM dimension when no VLM is configured.
    # Treat only non-empty or non-zero-latency VLM entries as strong VLM calls.
    return int(stat.get("count") or 0) > 0 or int(stat.get("ms") or 0) > 0


def _screen_watch_status(event: dict[str, Any]) -> dict[str, Any]:
    meta = event.get("result_meta")
    if isinstance(meta, dict):
        status = meta.get("screen_watch")
        if isinstance(status, dict):
            return status
    status = event.get("screen_watch")
    return status if isinstance(status, dict) else {}


def _low_latency_metrics(
    events: list[dict[str, Any]],
    tool_events: list[dict[str, Any]],
    batch_action_events: list[dict[str, Any]],
    perception_stats: list[dict[str, Any]],
    duration_s: float,
) -> dict[str, Any]:
    decision_ms: list[int] = []
    boundary_ts: float | None = None
    step_count = 0
    for event in events:
        etype = event.get("type")
        ts = event.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        if etype in {"agent_start", "agent_tool"}:
            boundary_ts = float(ts)
        elif etype == "agent_step":
            step_count += 1
            if boundary_ts is not None and float(ts) >= boundary_ts:
                decision_ms.append(int(round((float(ts) - boundary_ts) * 1000)))
            boundary_ts = float(ts)

    action_event_stream = [
        *[event for event in tool_events if event.get("tool") != "run_action_batch"],
        *batch_action_events,
    ]
    action_ms = [
        int(event.get("elapsed_ms") or 0)
        for event in action_event_stream
        if event.get("tool") in _ACTION_TOOL_NAMES and event.get("elapsed_ms") is not None
    ]
    post_verify_ms: list[int] = []
    screen_capture_ms: list[int] = []
    for event in action_event_stream:
        meta = event.get("result_meta")
        if isinstance(meta, dict):
            delta = meta.get("screen_delta")
            if isinstance(delta, dict) and delta.get("elapsed_ms") is not None:
                post_verify_ms.append(int(delta.get("elapsed_ms") or 0))
        status = _screen_watch_status(event)
        if status.get("capture_latency_ms") is not None:
            screen_capture_ms.append(int(status.get("capture_latency_ms") or 0))

    perception_total_ms: list[int] = []
    vlm_perception_calls = 0
    for event in perception_stats:
        dims = event.get("dims") or {}
        if not isinstance(dims, dict):
            continue
        total = 0
        saw_dim = False
        for name, stat in dims.items():
            if not isinstance(stat, dict):
                continue
            if stat.get("ms") is not None:
                total += int(stat.get("ms") or 0)
                saw_dim = True
            if str(name) == "vlm" and _perception_dim_called(str(name), stat):
                vlm_perception_calls += 1
        if saw_dim:
            perception_total_ms.append(total)

    replan_count = sum(1 for e in events if e.get("type") == "agent_recovery") \
        + sum(1 for e in tool_events if e.get("tool") == "revise_plan")
    vlm_tool_calls = sum(1 for e in tool_events if e.get("tool") == "vlm_describe")
    skill_reuse_count = sum(1 for e in tool_events if e.get("tool") == "reuse_skill")
    skill_suggestions = sum(
        1 for e in events
        if e.get("type") == "memory" and e.get("action") == "reuse_suggested"
    )
    denominator = max(skill_suggestions, skill_reuse_count, 1)
    auto_watch_starts = sum(
        1 for e in events
        if e.get("type") == "screen_watch" and e.get("action") == "auto_start"
    )
    watch_running_events = sum(
        1 for e in tool_events
        if _screen_watch_status(e).get("running") is True
    )

    steps = max(step_count, 1)
    minutes = max(duration_s / 60.0, 1 / 60.0)
    return {
        "screen_capture_latency": _latency_summary(screen_capture_ms),
        "perception_latency": _latency_summary(perception_total_ms),
        "action_decision_latency": _latency_summary(decision_ms),
        "action_execution_latency": _latency_summary(action_ms),
        "post_action_verify_latency": _latency_summary(post_verify_ms),
        "decision_count": step_count,
        "replan_count": replan_count,
        "replan_frequency_per_step": round(replan_count / steps, 3),
        "replan_frequency_per_min": round(replan_count / minutes, 3),
        "vlm_call_count": vlm_tool_calls + vlm_perception_calls,
        "vlm_call_frequency_per_step": round((vlm_tool_calls + vlm_perception_calls) / steps, 3),
        "skill_reuse_count": skill_reuse_count,
        "skill_reuse_suggestions": skill_suggestions,
        "skill_reuse_rate": round(skill_reuse_count / denominator, 3),
        "screen_watch_auto_starts": auto_watch_starts,
        "screen_watch_running_tool_events": watch_running_events,
    }


def _summarize_fast_gui(
    tool_events: list[dict[str, Any]],
    batch_action_events: list[dict[str, Any]],
    run_state_events: list[dict[str, Any]],
) -> dict[str, Any]:
    batch_calls = 0
    batch_actions = 0
    batch_subgoals = 0
    round_trips_saved = 0
    screen_waits = 0
    needs_observe_batches = 0
    verified_batches = 0
    controller_phases: Counter[str] = Counter()
    phases = Counter(str(e.get("phase") or "") for e in run_state_events if e.get("phase"))
    last_state = run_state_events[-1] if run_state_events else {}
    for event in tool_events:
        if event.get("tool") != "run_action_batch":
            continue
        batch_calls += 1
        meta = event.get("result_meta") or {}
        if not isinstance(meta, dict):
            continue
        if str(meta.get("subgoal") or "").strip():
            batch_subgoals += 1
        controller_state = meta.get("controller_state")
        if isinstance(controller_state, dict) and controller_state.get("phase"):
            controller_phases[str(controller_state.get("phase"))] += 1
        metrics = meta.get("metrics") or {}
        if isinstance(metrics, dict):
            batch_actions += int(metrics.get("action_count") or 0)
            round_trips_saved += int(metrics.get("controller_round_trips_saved") or 0)
            screen_waits += int(metrics.get("screen_wait_count") or 0)
        if meta.get("needs_observe") is True:
            needs_observe_batches += 1
        if meta.get("verified") is True:
            verified_batches += 1
    anchor_clicks = 0
    anchor_cache_hits = 0
    anchor_failures = 0
    subgoal_calls = 0
    subgoal_actions = 0
    verified_subgoals = 0
    subgoals_needing_llm = 0
    subgoal_missing_evidence = 0
    subgoal_elapsed_ms = 0
    subgoal_controller_elapsed_ms = 0
    subgoal_overhead_ms = 0
    for event in batch_action_events:
        if event.get("tool") != "click_element":
            continue
        anchor_clicks += 1
        meta = event.get("result_meta") or {}
        if isinstance(meta, dict):
            locator = meta.get("anchor_locator") or {}
            if isinstance(locator, dict) and locator.get("cache_hit") is True:
                anchor_cache_hits += 1
            if event.get("ok") is False or (
                isinstance(locator, dict) and locator.get("found") is False
            ):
                anchor_failures += 1
    for event in tool_events:
        if event.get("tool") != "execute_subgoal":
            continue
        subgoal_calls += 1
        elapsed = int(event.get("elapsed_ms") or 0)
        subgoal_elapsed_ms += elapsed
        meta = event.get("result_meta") or {}
        if not isinstance(meta, dict):
            continue
        actions = meta.get("actions_executed")
        counted_actions = False
        if isinstance(actions, list):
            subgoal_actions += len(actions)
            counted_actions = True
        metrics = meta.get("metrics") or {}
        if isinstance(metrics, dict):
            controller_elapsed = int(metrics.get("elapsed_ms") or 0)
            subgoal_controller_elapsed_ms += controller_elapsed
            subgoal_overhead_ms += max(0, elapsed - controller_elapsed)
            if not counted_actions:
                subgoal_actions += int(metrics.get("action_count") or 0)
            round_trips_saved += int(metrics.get("controller_round_trips_saved") or 0)
            screen_waits += int(metrics.get("screen_wait_count") or 0)
        controller_state = meta.get("controller_state")
        if isinstance(controller_state, dict) and controller_state.get("phase"):
            controller_phases[str(controller_state.get("phase"))] += 1
        if meta.get("verified") is True:
            verified_subgoals += 1
        if meta.get("needs_llm_reason") is True:
            subgoals_needing_llm += 1
        packet = meta.get("evidence_packet")
        if isinstance(packet, dict) and packet.get("missing_texts"):
            subgoal_missing_evidence += 1
    return {
        "batch_calls": batch_calls,
        "batch_actions": batch_actions,
        "batch_subgoals": batch_subgoals,
        "subgoal_calls": subgoal_calls,
        "subgoal_actions": subgoal_actions,
        "verified_subgoals": verified_subgoals,
        "subgoals_needing_llm": subgoals_needing_llm,
        "subgoal_missing_evidence": subgoal_missing_evidence,
        "subgoal_elapsed_ms": subgoal_elapsed_ms,
        "subgoal_controller_elapsed_ms": subgoal_controller_elapsed_ms,
        "subgoal_overhead_ms": subgoal_overhead_ms,
        "round_trips_saved": round_trips_saved,
        "screen_waits": screen_waits,
        "screen_watch_calls": sum(1 for e in tool_events if e.get("tool") == "screen_watch"),
        "needs_observe_batches": needs_observe_batches,
        "verified_batches": verified_batches,
        "controller_phases": dict(controller_phases),
        "anchor_clicks": anchor_clicks,
        "anchor_cache_hits": anchor_cache_hits,
        "anchor_failures": anchor_failures,
        "run_state_events": len(run_state_events),
        "run_state_phases": dict(phases),
        "last_run_state": {
            "phase": last_state.get("phase", ""),
            "last_tool": last_state.get("last_tool", ""),
            "unverified_actions": last_state.get("unverified_actions", 0),
        },
    }


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Return compact performance/evidence metrics for one trace event list."""
    task = ""
    run_id = ""
    status = ""
    result = ""
    started_ts: float | None = None
    ended_ts: float | None = None
    usage_total: dict[str, Any] = {}
    tool_events: list[dict[str, Any]] = []
    batch_action_events: list[dict[str, Any]] = []
    done_checks: list[dict[str, Any]] = []
    screenshots: list[str] = []
    perception_stats: list[dict[str, Any]] = []
    run_state_events: list[dict[str, Any]] = []

    for event in events:
        etype = event.get("type")
        ts = event.get("ts")
        if isinstance(ts, (int, float)):
            started_ts = ts if started_ts is None else min(started_ts, ts)
            ended_ts = ts if ended_ts is None else max(ended_ts, ts)
        if etype == "agent_start":
            task = str(event.get("task", "") or task)
            run_id = str(event.get("run_id", "") or run_id)
        elif etype == "agent_tool":
            tool_events.append(event)
            if event.get("screenshot"):
                screenshots.append(str(event.get("screenshot")))
        elif etype == "agent_batch_action":
            batch_action_events.append(event)
            if event.get("screenshot"):
                screenshots.append(str(event.get("screenshot")))
        elif etype == "done_check":
            done_checks.append({
                "passed": event.get("passed"),
                "reason": str(event.get("reason", ""))[:500],
                "unverified": bool(event.get("unverified")),
            })
        elif etype == "agent_usage":
            total = event.get("total")
            if isinstance(total, dict):
                usage_total = dict(total)
        elif etype == "perception":
            perception_stats.append(event)
        elif etype == "run_state":
            run_state_events.append(event)
        elif etype == "agent_done":
            status = str(event.get("status", "") or status)
            result = str(event.get("result", "") or result)
            run_id = str(event.get("run_id", "") or run_id)

    elapsed_ms = [int(e.get("elapsed_ms") or 0) for e in tool_events if e.get("elapsed_ms") is not None]
    by_tool: dict[str, list[int]] = defaultdict(list)
    for event in tool_events:
        if event.get("elapsed_ms") is not None:
            by_tool[str(event.get("tool") or "")].append(int(event.get("elapsed_ms") or 0))

    tool_counts = Counter(str(e.get("tool") or "") for e in tool_events)
    fast_gui = _summarize_fast_gui(tool_events, batch_action_events, run_state_events)
    tool_latency = {
        tool: {
            "count": len(values),
            "avg_ms": round(statistics.mean(values), 1) if values else 0,
            "max_ms": max(values) if values else 0,
            "p95_ms": _percentile(values, 0.95),
        }
        for tool, values in sorted(by_tool.items())
    }
    slow_tools = sorted(
        (
            {
                "tool": str(event.get("tool") or ""),
                "elapsed_ms": int(event.get("elapsed_ms") or 0),
                "args": event.get("args") or {},
                "preview": str(event.get("result_preview", ""))[:220],
                "screenshot": event.get("screenshot"),
            }
            for event in tool_events
            if event.get("elapsed_ms") is not None
        ),
        key=lambda item: item["elapsed_ms"],
        reverse=True,
    )[:8]

    dim_latency: dict[str, list[int]] = defaultdict(list)
    dim_counts: Counter[str] = Counter()
    for event in perception_stats:
        dims = event.get("dims") or {}
        if not isinstance(dims, dict):
            continue
        for name, stat in dims.items():
            if isinstance(stat, dict):
                dim_counts[str(name)] += int(stat.get("count") or 0)
                if _perception_dim_called(str(name), stat):
                    dim_latency[str(name)].append(int(stat.get("ms") or 0))

    perception = {
        name: {
            "calls": len(values),
            "total_elements": dim_counts.get(name, 0),
            "avg_ms": round(statistics.mean(values), 1) if values else 0,
            "max_ms": max(values) if values else 0,
        }
        for name, values in sorted(dim_latency.items())
    }

    duration_s = round((ended_ts - started_ts), 2) if started_ts and ended_ts else 0
    return {
        "run_id": run_id,
        "task": task,
        "status": status,
        "result": result[:800],
        "duration_s": duration_s,
        "tool_count": len(tool_events),
        "tool_counts": dict(tool_counts),
        "tool_latency": tool_latency,
        "tool_latency_overall": {
            "avg_ms": round(statistics.mean(elapsed_ms), 1) if elapsed_ms else 0,
            "max_ms": max(elapsed_ms) if elapsed_ms else 0,
            "p95_ms": _percentile(elapsed_ms, 0.95),
        },
        "slow_tools": slow_tools,
        "perception": perception,
        "fast_gui": fast_gui,
        "low_latency": _low_latency_metrics(
            events,
            tool_events,
            batch_action_events,
            perception_stats,
            duration_s,
        ),
        "done_checks": done_checks,
        "rejected_done_count": sum(1 for item in done_checks if item.get("passed") is False),
        "screenshots": screenshots,
        "usage": usage_total,
    }


def summarize_trace(run_id: str) -> dict[str, Any]:
    return summarize_events(_read_jsonl(TRACES_DIR / f"{run_id}.jsonl"))


def summarize_eval_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    run_id = str(data.get("run_id") or path.stem)
    trace_summary = summarize_trace(run_id)
    merged = {**trace_summary, **data}
    if trace_summary:
        merged["trace_summary"] = trace_summary
        merged.setdefault("usage", trace_summary.get("usage", {}))
        merged.setdefault("slow_tools", trace_summary.get("slow_tools", []))
        merged.setdefault("tool_latency", trace_summary.get("tool_latency", {}))
    merged["report_file"] = str(path)
    return merged


def list_eval_reports(limit: int = 50) -> list[dict[str, Any]]:
    EVALS_DIR.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, Any]] = []
    paths = [
        path for path in sorted(EVALS_DIR.glob("*.eval.json"), reverse=True)
        if not path.name.endswith(".suite.eval.json")
    ][:limit]
    for path in paths:
        try:
            reports.append(summarize_eval_report(path))
        except (json.JSONDecodeError, OSError):
            continue
    return reports


def aggregate_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    if not reports:
        return {
            "total": 0,
            "passed_or_done": 0,
            "unverified_done_count": 0,
            "total_duration_s": 0,
            "total_cost_cny": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "slow_tools": [],
            "reports": [],
        }
    slow: list[dict[str, Any]] = []
    for report in reports:
        for item in report.get("slow_tools") or report.get("summary", {}).get("slow_tools", []):
            enriched = dict(item)
            enriched["run_id"] = report.get("run_id")
            slow.append(enriched)
    slow = sorted(slow, key=lambda item: int(item.get("elapsed_ms") or 0), reverse=True)[:12]
    usage_items = [r.get("usage") or r.get("status_endpoint", {}).get("usage") or {} for r in reports]
    fast_items = [
        r.get("fast_gui")
        or r.get("summary", {}).get("fast_gui")
        or r.get("trace_summary", {}).get("fast_gui")
        or {}
        for r in reports
    ]
    def unverified_done(report: dict[str, Any]) -> bool:
        checks = report.get("done_checks") or report.get("summary", {}).get("done_checks") or []
        if isinstance(checks, list):
            for check in checks:
                if not isinstance(check, dict):
                    continue
                reason = str(check.get("reason") or "").casefold()
                if check.get("unverified") is True:
                    return True
                if "force-accepted" in reason or "unverified" in reason:
                    return True
        return False

    def passed(report: dict[str, Any]) -> bool:
        if report.get("timed_out"):
            return False
        if report.get("policy_passed") is False:
            return False
        if unverified_done(report):
            return False
        if "ground_truth_passed" in report:
            return bool(report.get("ground_truth_passed"))
        return report.get("status") == "done"

    return {
        "total": len(reports),
        "passed_or_done": sum(1 for r in reports if passed(r)),
        "unverified_done_count": sum(1 for r in reports if unverified_done(r)),
        "total_duration_s": round(sum(float(r.get("duration_s") or 0) for r in reports), 2),
        "total_cost_cny": round(sum(float(u.get("cost_cny") or 0) for u in usage_items), 4),
        "total_prompt_tokens": sum(int(u.get("prompt_tokens") or 0) for u in usage_items),
        "total_completion_tokens": sum(int(u.get("completion_tokens") or 0) for u in usage_items),
        "fast_gui": {
            "batch_calls": sum(int(f.get("batch_calls") or 0) for f in fast_items),
            "batch_actions": sum(int(f.get("batch_actions") or 0) for f in fast_items),
            "batch_subgoals": sum(int(f.get("batch_subgoals") or 0) for f in fast_items),
            "subgoal_calls": sum(int(f.get("subgoal_calls") or 0) for f in fast_items),
            "subgoal_actions": sum(int(f.get("subgoal_actions") or 0) for f in fast_items),
            "verified_subgoals": sum(int(f.get("verified_subgoals") or 0) for f in fast_items),
            "subgoals_needing_llm": sum(int(f.get("subgoals_needing_llm") or 0) for f in fast_items),
            "subgoal_missing_evidence": sum(int(f.get("subgoal_missing_evidence") or 0) for f in fast_items),
            "subgoal_elapsed_ms": sum(int(f.get("subgoal_elapsed_ms") or 0) for f in fast_items),
            "subgoal_controller_elapsed_ms": sum(int(f.get("subgoal_controller_elapsed_ms") or 0) for f in fast_items),
            "subgoal_overhead_ms": sum(int(f.get("subgoal_overhead_ms") or 0) for f in fast_items),
            "round_trips_saved": sum(int(f.get("round_trips_saved") or 0) for f in fast_items),
            "screen_waits": sum(int(f.get("screen_waits") or 0) for f in fast_items),
            "screen_watch_calls": sum(int(f.get("screen_watch_calls") or 0) for f in fast_items),
            "needs_observe_batches": sum(int(f.get("needs_observe_batches") or 0) for f in fast_items),
            "verified_batches": sum(int(f.get("verified_batches") or 0) for f in fast_items),
            "anchor_clicks": sum(int(f.get("anchor_clicks") or 0) for f in fast_items),
            "anchor_cache_hits": sum(int(f.get("anchor_cache_hits") or 0) for f in fast_items),
            "anchor_failures": sum(int(f.get("anchor_failures") or 0) for f in fast_items),
            "run_state_events": sum(int(f.get("run_state_events") or 0) for f in fast_items),
        },
        "slow_tools": slow,
        "reports": reports,
    }
