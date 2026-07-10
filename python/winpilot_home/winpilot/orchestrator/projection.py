"""Read-optimized projections of Agent, controller, replay, and ARP events."""
from __future__ import annotations

import copy
import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from ..utils.log import BUS


_TERMINAL = {"done", "failed", "fail", "error", "stopped", "canceled", "reply"}


def _compact(value: Any, limit: int = 600) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


class RunProjectionStore:
    def __init__(self, max_runs: int = 100) -> None:
        self._lock = threading.RLock()
        self._runs: dict[str, dict[str, Any]] = {}
        self._child_to_parent: dict[str, str] = {}
        self._hydrated_paths: set[str] = set()
        self._order: deque[str] = deque(maxlen=max_runs)
        self._current_id: str | None = None
        BUS.add_sink(self.on_event)

    def _new(self, run_id: str, event: dict[str, Any]) -> dict[str, Any]:
        now = float(event.get("ts") or time.time())
        return {
            "run_id": run_id,
            "kind": "arp" if str(event.get("type", "")).startswith("arp_") else "agent",
            "status": "running",
            "phase": "starting",
            "goal": str(event.get("task") or event.get("graph_name") or ""),
            "graph_id": event.get("graph_id"),
            "current_node_id": None,
            "current_tool": None,
            "started_at": now,
            "updated_at": now,
            "finished_at": None,
            "result": None,
            "error": "",
            "usage": None,
            "timeline": [],
            "evidence": [],
            "metrics": {
                "event_count": 0,
                "decision_count": 0,
                "tool_count": 0,
                "batch_action_count": 0,
                "perception_count": 0,
                "vlm_call_count": 0,
                "done_checks": 0,
                "done_check_passed": 0,
                "tool_latency_ms": [],
                "perception_latency_ms": [],
                "decision_latency_ms": [],
                "action_latency_ms": [],
                "post_verify_latency_ms": [],
                "semantic_verify_latency_ms": [],
                "screen_delta_latency_ms": [],
                "screen_capture_latency_ms": [],
                "semantic_verify_count": 0,
                "semantic_verify_passed_count": 0,
                "semantic_verify_uia_query_count": 0,
                "semantic_verify_ocr_count": 0,
                "replan_count": 0,
                "skill_reuse_count": 0,
                "skill_suggestion_count": 0,
                "anchor_cache_lookup_count": 0,
                "anchor_cache_hit_count": 0,
                "anchor_cache_miss_count": 0,
                "anchor_cache_fallback_count": 0,
                "anchor_cache_learned_count": 0,
                "anchor_cache_lookup_latency_total_ms": 0.0,
                "anchor_cache_lookup_latency_max_ms": 0,
                "visual_servo_count": 0,
                "visual_servo_correction_count": 0,
                "visual_servo_failure_count": 0,
                "visual_servo_latency_ms": [],
            },
        }

    @staticmethod
    def _phase(event_type: str, event: dict[str, Any]) -> str | None:
        if event_type in {"agent_start", "arp_run_start"}:
            return "planning"
        if event_type in {"agent_plan", "agent_step", "agent_decision"}:
            return "deciding"
        if event_type in {
            "perception", "perception_prefetch", "screen_watch", "screen_delta",
            "replay_anchor_cache",
        }:
            return "perceiving"
        if event_type in {"agent_tool_start", "agent_batch_action_start", "arp_node_start"}:
            return "acting"
        if event_type in {"done_check", "verify"}:
            return "verifying"
        if event_type == "arp_evidence":
            return "verifying"
        if event_type == "arp_human_gate" and event.get("status") == "waiting":
            return "awaiting_human"
        if event_type in {"agent_done", "agent_error", "arp_run_done"}:
            return "terminal"
        return None

    def on_event(self, event: dict[str, Any]) -> None:
        source_run_id = str(event.get("run_id") or "").strip()
        if not source_run_id:
            return
        event_type = str(event.get("type") or "event")
        with self._lock:
            if event_type == "arp_child_run":
                child_run_id = str(event.get("child_run_id") or "").strip()
                if child_run_id:
                    self._child_to_parent[child_run_id] = source_run_id
            run_id = self._child_to_parent.get(source_run_id, source_run_id)
            is_child_event = run_id != source_run_id
            run = self._runs.get(run_id)
            if run is None:
                run = self._new(run_id, event)
                self._runs[run_id] = run
                self._order.append(run_id)
            self._current_id = run_id
            run["updated_at"] = float(event.get("ts") or time.time())
            run["metrics"]["event_count"] += 1
            phase = self._phase(event_type, event)
            if phase:
                run["phase"] = phase
            if event.get("task") and not run.get("goal"):
                run["goal"] = str(event.get("task"))
            if event.get("graph_name"):
                run["goal"] = str(event.get("graph_name"))
                run["graph_id"] = event.get("graph_id")
                run["kind"] = "arp"
            if event_type in {"agent_step", "agent_plan"}:
                run["metrics"]["decision_count"] += 1
            if event_type == "agent_decision":
                elapsed = int(event.get("elapsed_ms") or 0)
                run["metrics"]["decision_latency_ms"].append(elapsed)
                run["metrics"]["decision_latency_ms"] = run["metrics"]["decision_latency_ms"][-200:]
            if event_type == "agent_tool_start":
                run["current_tool"] = event.get("tool")
            if event_type == "agent_tool":
                run["metrics"]["tool_count"] += 1
                elapsed = int(event.get("elapsed_ms") or 0)
                run["metrics"]["tool_latency_ms"].append(elapsed)
                run["metrics"]["tool_latency_ms"] = run["metrics"]["tool_latency_ms"][-200:]
                run["metrics"]["action_latency_ms"].append(elapsed)
                run["metrics"]["action_latency_ms"] = run["metrics"]["action_latency_ms"][-200:]
            if event_type == "agent_batch_action":
                run["metrics"]["batch_action_count"] += 1
                run["metrics"]["tool_count"] += 1
                elapsed = int(event.get("elapsed_ms") or 0)
                run["metrics"]["action_latency_ms"].append(elapsed)
                run["metrics"]["action_latency_ms"] = run["metrics"]["action_latency_ms"][-200:]
                result_meta = event.get("result_meta") if isinstance(event.get("result_meta"), dict) else {}
                servo = result_meta.get("visual_servo") if isinstance(result_meta.get("visual_servo"), dict) else {}
                if servo.get("enabled"):
                    run["metrics"]["visual_servo_count"] += 1
                    run["metrics"]["visual_servo_correction_count"] += max(
                        0, int(servo.get("correction_count") or 0),
                    )
                    if not servo.get("confirmed"):
                        run["metrics"]["visual_servo_failure_count"] += 1
                    run["metrics"]["visual_servo_latency_ms"].append(
                        max(0, int(servo.get("elapsed_ms") or 0)),
                    )
                    run["metrics"]["visual_servo_latency_ms"] = run["metrics"]["visual_servo_latency_ms"][-200:]
            if event_type == "perception":
                run["metrics"]["perception_count"] += 1
                dims = event.get("dims") if isinstance(event.get("dims"), dict) else {}
                elapsed = sum(int((stat or {}).get("ms") or 0) for stat in dims.values() if isinstance(stat, dict))
                run["metrics"]["perception_latency_ms"].append(elapsed)
                run["metrics"]["perception_latency_ms"] = run["metrics"]["perception_latency_ms"][-200:]
                vlm_stat = dims.get("vlm") if isinstance(dims.get("vlm"), dict) else {}
                if int(vlm_stat.get("count") or 0) > 0:
                    run["metrics"]["vlm_call_count"] += 1
            if event_type == "perception_prefetch":
                run["metrics"]["perception_count"] += 1
                elapsed = int(event.get("elapsed_ms") or 0)
                run["metrics"]["perception_latency_ms"].append(elapsed)
                run["metrics"]["perception_latency_ms"] = run["metrics"]["perception_latency_ms"][-200:]
                texts = [str(item) for item in event.get("texts", []) if str(item).strip()]
                if event.get("transient") and texts:
                    run["evidence"].append({
                        "type": "transient_ocr",
                        "texts": texts[:12],
                        "reason": " | ".join(texts[:4]),
                        "ts": event.get("ts"),
                    })
            if event_type in {"screen_watch", "screen_delta"}:
                capture_ms = int(event.get("capture_latency_ms") or 0)
                run["metrics"]["screen_capture_latency_ms"].append(capture_ms)
                run["metrics"]["screen_capture_latency_ms"] = run["metrics"]["screen_capture_latency_ms"][-200:]
            if event_type == "verify":
                elapsed = max(0, int(event.get("elapsed_ms") or 0))
                method = str(event.get("method") or "").casefold()
                run["metrics"]["semantic_verify_count"] += 1
                if event.get("verified") is True:
                    run["metrics"]["semantic_verify_passed_count"] += 1
                if "uia-query" in method:
                    run["metrics"]["semantic_verify_uia_query_count"] += 1
                if "ocr" in method:
                    run["metrics"]["semantic_verify_ocr_count"] += 1
                run["metrics"]["semantic_verify_latency_ms"].append(elapsed)
                run["metrics"]["semantic_verify_latency_ms"] = run["metrics"]["semantic_verify_latency_ms"][-200:]
                run["metrics"]["post_verify_latency_ms"].append(elapsed)
                run["metrics"]["post_verify_latency_ms"] = run["metrics"]["post_verify_latency_ms"][-200:]
            if (
                (event_type == "agent_recovery" and event.get("kind") == "structured_replan")
                or (event_type == "agent_plan" and event.get("action") == "revise")
            ):
                run["metrics"]["replan_count"] += 1
            if event_type == "screen_delta" and event.get("diff_latency_ms") is not None:
                run["metrics"]["screen_delta_latency_ms"].append(
                    max(0, int(event.get("diff_latency_ms") or 0))
                )
                run["metrics"]["screen_delta_latency_ms"] = run["metrics"]["screen_delta_latency_ms"][-200:]
            if event_type == "memory" and event.get("action") == "reuse":
                run["metrics"]["skill_reuse_count"] += 1
            if event_type == "memory" and event.get("action") == "reuse_suggested":
                run["metrics"]["skill_suggestion_count"] += 1
            if event_type == "arp_child_run" and event.get("child_kind") == "skill_replay":
                run["metrics"]["skill_reuse_count"] += 1
            if event_type == "replay_anchor_cache":
                lookups = max(0, int(event.get("lookup_count") or 0))
                run["metrics"]["anchor_cache_lookup_count"] += lookups
                run["metrics"]["anchor_cache_hit_count"] += max(
                    0, int(event.get("hit_count") or 0)
                )
                run["metrics"]["anchor_cache_miss_count"] += max(
                    0, int(event.get("miss_count") or 0)
                )
                run["metrics"]["anchor_cache_fallback_count"] += max(
                    0, int(event.get("fallback_count") or 0)
                )
                run["metrics"]["anchor_cache_learned_count"] += max(
                    0, int(event.get("learned_count") or 0)
                )
                run["metrics"]["anchor_cache_lookup_latency_total_ms"] += (
                    float(event.get("lookup_latency_avg_ms") or 0) * lookups
                )
                run["metrics"]["anchor_cache_lookup_latency_max_ms"] = max(
                    int(run["metrics"].get("anchor_cache_lookup_latency_max_ms") or 0),
                    int(event.get("lookup_latency_max_ms") or 0),
                )
            if event_type == "done_check":
                run["metrics"]["done_checks"] += 1
                if event.get("passed") is True:
                    run["metrics"]["done_check_passed"] += 1
                run["evidence"].append({
                    "type": "done_check",
                    "passed": bool(event.get("passed")),
                    "reason": _compact(event.get("reason")),
                    "ts": event.get("ts"),
                })
            if event_type == "arp_evidence":
                passed = event.get("passed") is not False
                run["metrics"]["done_checks"] += 1
                if passed:
                    run["metrics"]["done_check_passed"] += 1
                evidence = copy.deepcopy(event.get("evidence"))
                evidence_type = evidence.get("type") if isinstance(evidence, dict) else "arp_evidence"
                evidence_reason = (
                    evidence.get("reason")
                    or evidence.get("subgoal")
                    or evidence.get("script")
                    or evidence_type
                ) if isinstance(evidence, dict) else evidence
                run["evidence"].append({
                    "type": str(evidence_type or "arp_evidence"),
                    "passed": passed,
                    "reason": _compact(evidence_reason),
                    "evidence": evidence,
                    "node_id": event.get("node_id"),
                    "ts": event.get("ts"),
                })
            if event.get("screenshot"):
                run["evidence"].append({
                    "type": "screenshot",
                    "path": event.get("screenshot"),
                    "kind": event.get("screenshot_kind") or "screen",
                    "region": copy.deepcopy(event.get("screenshot_region")),
                    "transient": bool(event.get("transient_observed")),
                    "ts": event.get("ts"),
                })
            if event_type == "arp_node_start":
                run["current_node_id"] = event.get("node_id")
            if event_type == "arp_node_done":
                run["current_node_id"] = event.get("node_id")
                if event.get("ok") is False:
                    run["error"] = str(event.get("error") or "")
            if event_type == "agent_done" and not is_child_event:
                run["status"] = str(event.get("status") or "done")
                run["result"] = _compact(event.get("result"))
                run["usage"] = copy.deepcopy(event.get("usage"))
                run["finished_at"] = run["updated_at"]
            elif event_type == "agent_error" and not is_child_event:
                run["status"] = "error"
                run["error"] = str(event.get("error") or "")
                run["finished_at"] = run["updated_at"]
            elif event_type == "arp_run_done":
                run["status"] = str(event.get("status") or "done")
                run["result"] = _compact(event.get("result"))
                run["error"] = str(event.get("error") or "")
                run["current_node_id"] = None
                run["current_tool"] = None
                run["finished_at"] = run["updated_at"]
            elif run.get("status") in _TERMINAL:
                pass
            else:
                run["status"] = "running" if run["phase"] != "awaiting_human" else "paused"

            timeline_event = {
                "type": event_type,
                "ts": event.get("ts"),
                "phase": run["phase"],
                "node_id": event.get("node_id"),
                "tool": event.get("tool"),
                "status": event.get("status"),
                "summary": _compact(
                    event.get("content")
                    or event.get("reason")
                    or event.get("error")
                    or event.get("result_preview")
                    or event.get("node_name")
                    or event.get("action")
                    or event_type
                ),
                "child_run_id": source_run_id if is_child_event else event.get("child_run_id"),
            }
            run["timeline"].append(timeline_event)
            run["timeline"] = run["timeline"][-200:]
            run["evidence"] = run["evidence"][-100:]

    def hydrate_directory(self, directory: Path, limit: int = 100) -> int:
        """Rebuild persisted read models from recent JSONL traces."""
        try:
            paths = sorted(
                directory.glob("*.jsonl"),
                key=lambda path: path.stat().st_mtime,
            )[-max(1, int(limit)):]
        except OSError:
            return 0
        hydrated = 0
        for path in paths:
            path_key = str(path.resolve())
            with self._lock:
                if path_key in self._hydrated_paths:
                    continue
            try:
                with path.open(encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            event = json.loads(line)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if isinstance(event, dict):
                            self.on_event(event)
                with self._lock:
                    self._hydrated_paths.add(path_key)
                hydrated += 1
            except OSError:
                continue
        return hydrated

    @staticmethod
    def _public(run: dict[str, Any]) -> dict[str, Any]:
        data = copy.deepcopy(run)
        metrics = data["metrics"]
        tool_samples = metrics.pop("tool_latency_ms", [])
        perception_samples = metrics.pop("perception_latency_ms", [])
        decision_samples = metrics.pop("decision_latency_ms", [])
        action_samples = metrics.pop("action_latency_ms", [])
        verify_samples = metrics.pop("post_verify_latency_ms", [])
        semantic_verify_samples = metrics.pop("semantic_verify_latency_ms", [])
        screen_delta_samples = metrics.pop("screen_delta_latency_ms", [])
        capture_samples = metrics.pop("screen_capture_latency_ms", [])
        servo_samples = metrics.pop("visual_servo_latency_ms", [])
        metrics["tool_latency_avg_ms"] = round(sum(tool_samples) / len(tool_samples), 1) if tool_samples else 0
        metrics["tool_latency_max_ms"] = max(tool_samples) if tool_samples else 0
        metrics["perception_latency_avg_ms"] = round(sum(perception_samples) / len(perception_samples), 1) if perception_samples else 0
        metrics["perception_latency_max_ms"] = max(perception_samples) if perception_samples else 0
        metrics["screen_capture_latency_avg_ms"] = round(sum(capture_samples) / len(capture_samples), 1) if capture_samples else 0
        metrics["screen_capture_latency_max_ms"] = max(capture_samples) if capture_samples else 0
        metrics["action_decision_latency_avg_ms"] = round(sum(decision_samples) / len(decision_samples), 1) if decision_samples else 0
        metrics["action_decision_latency_max_ms"] = max(decision_samples) if decision_samples else 0
        metrics["action_execution_latency_avg_ms"] = round(sum(action_samples) / len(action_samples), 1) if action_samples else 0
        metrics["action_execution_latency_max_ms"] = max(action_samples) if action_samples else 0
        post_verify_samples = verify_samples or screen_delta_samples
        metrics["post_action_verify_latency_avg_ms"] = round(sum(post_verify_samples) / len(post_verify_samples), 1) if post_verify_samples else 0
        metrics["post_action_verify_latency_max_ms"] = max(post_verify_samples) if post_verify_samples else 0
        metrics["semantic_verify_latency_avg_ms"] = round(
            sum(semantic_verify_samples) / len(semantic_verify_samples), 1,
        ) if semantic_verify_samples else 0
        metrics["semantic_verify_latency_max_ms"] = max(semantic_verify_samples) if semantic_verify_samples else 0
        metrics["screen_delta_latency_avg_ms"] = round(
            sum(screen_delta_samples) / len(screen_delta_samples), 1,
        ) if screen_delta_samples else 0
        metrics["screen_delta_latency_max_ms"] = max(screen_delta_samples) if screen_delta_samples else 0
        metrics["visual_servo_latency_avg_ms"] = round(
            sum(servo_samples) / len(servo_samples), 1,
        ) if servo_samples else 0
        metrics["visual_servo_latency_max_ms"] = max(servo_samples) if servo_samples else 0
        decisions = max(1, int(metrics.get("decision_count") or 0))
        perceptions = max(1, int(metrics.get("perception_count") or 0))
        suggestions = max(1, int(metrics.get("skill_suggestion_count") or 0))
        tools = max(1, int(metrics.get("tool_count") or 0))
        verify_count = max(1, int(metrics.get("semantic_verify_count") or 0))
        metrics["replan_frequency"] = round(int(metrics.get("replan_count") or 0) / decisions, 4)
        metrics["vlm_call_frequency"] = round(int(metrics.get("vlm_call_count") or 0) / perceptions, 4)
        metrics["skill_reuse_rate"] = round(int(metrics.get("skill_reuse_count") or 0) / suggestions, 4)
        metrics["batch_action_ratio"] = round(min(1.0, int(metrics.get("batch_action_count") or 0) / tools), 4)
        metrics["semantic_verify_fast_path_rate"] = round(
            int(metrics.get("semantic_verify_uia_query_count") or 0) / verify_count,
            4,
        )
        metrics["semantic_verify_ocr_rate"] = round(
            int(metrics.get("semantic_verify_ocr_count") or 0) / verify_count,
            4,
        )
        anchor_lookups = int(metrics.get("anchor_cache_lookup_count") or 0)
        anchor_hits = int(metrics.get("anchor_cache_hit_count") or 0)
        anchor_latency_total = float(metrics.pop("anchor_cache_lookup_latency_total_ms", 0.0) or 0.0)
        metrics["anchor_cache_hit_rate"] = round(
            anchor_hits / anchor_lookups, 4,
        ) if anchor_lookups else 0.0
        metrics["anchor_cache_lookup_latency_avg_ms"] = round(
            anchor_latency_total / anchor_lookups, 1,
        ) if anchor_lookups else 0
        return data

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            return self._public(run) if run else None

    def current(self) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(self._current_id or "")
            return self._public(run) if run else None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(self._order)[-max(1, limit):]
            ids.reverse()
            return [self._public(self._runs[run_id]) for run_id in ids if run_id in self._runs]


RUN_PROJECTIONS = RunProjectionStore()
