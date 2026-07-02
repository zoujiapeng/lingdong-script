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


def _summarize_fast_gui(
    tool_events: list[dict[str, Any]],
    run_state_events: list[dict[str, Any]],
) -> dict[str, Any]:
    batch_calls = 0
    batch_actions = 0
    round_trips_saved = 0
    screen_waits = 0
    needs_observe_batches = 0
    verified_batches = 0
    phases = Counter(str(e.get("phase") or "") for e in run_state_events if e.get("phase"))
    last_state = run_state_events[-1] if run_state_events else {}
    for event in tool_events:
        if event.get("tool") != "run_action_batch":
            continue
        batch_calls += 1
        meta = event.get("result_meta") or {}
        if not isinstance(meta, dict):
            continue
        metrics = meta.get("metrics") or {}
        if isinstance(metrics, dict):
            batch_actions += int(metrics.get("action_count") or 0)
            round_trips_saved += int(metrics.get("controller_round_trips_saved") or 0)
            screen_waits += int(metrics.get("screen_wait_count") or 0)
        if meta.get("needs_observe") is True:
            needs_observe_batches += 1
        if meta.get("verified") is True:
            verified_batches += 1
    return {
        "batch_calls": batch_calls,
        "batch_actions": batch_actions,
        "round_trips_saved": round_trips_saved,
        "screen_waits": screen_waits,
        "screen_watch_calls": sum(1 for e in tool_events if e.get("tool") == "screen_watch"),
        "needs_observe_batches": needs_observe_batches,
        "verified_batches": verified_batches,
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
        elif etype == "done_check":
            done_checks.append({
                "passed": event.get("passed"),
                "reason": str(event.get("reason", ""))[:500],
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
    fast_gui = _summarize_fast_gui(tool_events, run_state_events)
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
                if stat.get("ms") is not None:
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

    return {
        "run_id": run_id,
        "task": task,
        "status": status,
        "result": result[:800],
        "duration_s": round((ended_ts - started_ts), 2) if started_ts and ended_ts else 0,
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
    def passed(report: dict[str, Any]) -> bool:
        if report.get("timed_out"):
            return False
        if report.get("policy_passed") is False:
            return False
        if "ground_truth_passed" in report:
            return bool(report.get("ground_truth_passed"))
        return report.get("status") == "done"

    return {
        "total": len(reports),
        "passed_or_done": sum(1 for r in reports if passed(r)),
        "total_duration_s": round(sum(float(r.get("duration_s") or 0) for r in reports), 2),
        "total_cost_cny": round(sum(float(u.get("cost_cny") or 0) for u in usage_items), 4),
        "total_prompt_tokens": sum(int(u.get("prompt_tokens") or 0) for u in usage_items),
        "total_completion_tokens": sum(int(u.get("completion_tokens") or 0) for u in usage_items),
        "fast_gui": {
            "batch_calls": sum(int(f.get("batch_calls") or 0) for f in fast_items),
            "batch_actions": sum(int(f.get("batch_actions") or 0) for f in fast_items),
            "round_trips_saved": sum(int(f.get("round_trips_saved") or 0) for f in fast_items),
            "screen_waits": sum(int(f.get("screen_waits") or 0) for f in fast_items),
            "screen_watch_calls": sum(int(f.get("screen_watch_calls") or 0) for f in fast_items),
            "needs_observe_batches": sum(int(f.get("needs_observe_batches") or 0) for f in fast_items),
            "verified_batches": sum(int(f.get("verified_batches") or 0) for f in fast_items),
            "run_state_events": sum(int(f.get("run_state_events") or 0) for f in fast_items),
        },
        "slow_tools": slow,
        "reports": reports,
    }
