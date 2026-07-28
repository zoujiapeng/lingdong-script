"""Read-optimized projections of Agent, controller, replay, and ARP events."""
from __future__ import annotations

import copy
import json
import math
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from ..utils.log import BUS


_TERMINAL = {
    "done", "failed", "fail", "error", "stopped", "canceled", "reply",
    "interrupted",
}


def _compact(value: Any, limit: int = 600) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _nonnegative_latency_ms(value: Any) -> int | None:
    """Parse an optional latency sample without letting telemetry break a run."""
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric < 0:
        return None
    return int(numeric)


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * pct)))
    return int(ordered[idx])


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
        runtime_version = str(event.get("runtime_version") or "")
        if runtime_version not in {"legacy", "vnext"}:
            runtime_version = ""
        return {
            "run_id": run_id,
            "kind": "arp" if str(event.get("type", "")).startswith("arp_") else "agent",
            "runtime_version": runtime_version,
            "runtime_budget": copy.deepcopy(
                event.get("runtime_budget")
                if isinstance(event.get("runtime_budget"), dict)
                else {}
            ),
            "no_legacy_fallback": (
                event.get("no_legacy_fallback")
                if type(event.get("no_legacy_fallback")) is bool
                else None
            ),
            "status": "running",
            "manual_paused": False,
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
            # Audit data is kept separately from the lightweight run list.
            # It is rebuilt from persisted ARP events when the runtime starts,
            # so historical stage/capability/evidence views do not disappear
            # merely because the in-memory ArpExecution was released.
            "task_contract": None,
            "satisfiability": None,
            "experience_policy": "allow",
            "stage_trace": [],
            "contract_evidence": [],
            "completion_contract": None,
            "arp_snapshot": None,
            "metrics": {
                "event_count": 0,
                "decision_count": 0,
                "model_call_count": 0,
                "stage_count": 0,
                "stage_failure_count": 0,
                "retry_count": 0,
                "controller_elapsed_total_ms": 0,
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
                "visual_feedback_frame_count": 0,
                "visual_feedback_capture_latency_ms": [],
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
        if event_type in {"agent_tool_start", "agent_batch_action_start", "arp_node_start", "task_stage_start"}:
            return "acting"
        if event_type in {"done_check", "verify", "task_stage_done"}:
            return "verifying"
        if event_type == "arp_evidence":
            return "verifying"
        if event_type == "arp_human_gate" and event.get("status") == "waiting":
            return "awaiting_human"
        if event_type == "arp_run_paused":
            return "paused"
        if event_type == "arp_run_resumed":
            return "deciding"
        if event_type in {"agent_done", "agent_error", "arp_run_done"}:
            return "terminal"
        if event_type == "schedule" and event.get("action") == "finished":
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
            is_new_run = run is None
            background_event = event_type in {"schedule", "user_notification"}
            if run is None:
                run = self._new(run_id, event)
                self._runs[run_id] = run
                self._order.append(run_id)
            if (
                (is_new_run and not background_event)
                or self._current_id is None
                or event_type in {"agent_start", "arp_run_start"}
            ):
                self._current_id = run_id
            run["updated_at"] = float(event.get("ts") or time.time())
            run["metrics"]["event_count"] += 1
            event_runtime_version = str(
                event.get("runtime_version") or ""
            )
            if event_runtime_version in {"legacy", "vnext"}:
                frozen_runtime_version = str(
                    run.get("runtime_version") or ""
                )
                if (
                    frozen_runtime_version
                    and frozen_runtime_version != event_runtime_version
                ):
                    run["runtime_version_mismatch"] = {
                        "frozen": frozen_runtime_version,
                        "observed": event_runtime_version,
                        "event_type": event_type,
                    }
                else:
                    run["runtime_version"] = event_runtime_version
            event_no_legacy_fallback = event.get("no_legacy_fallback")
            if type(event_no_legacy_fallback) is bool:
                frozen_no_legacy_fallback = run.get(
                    "no_legacy_fallback"
                )
                if (
                    type(frozen_no_legacy_fallback) is bool
                    and frozen_no_legacy_fallback
                    != event_no_legacy_fallback
                ):
                    run["no_legacy_fallback_mismatch"] = {
                        "frozen": frozen_no_legacy_fallback,
                        "observed": event_no_legacy_fallback,
                        "event_type": event_type,
                    }
                else:
                    run["no_legacy_fallback"] = (
                        event_no_legacy_fallback
                    )
            phase = self._phase(event_type, event)
            terminal_before_event = run.get("status") in _TERMINAL
            if (
                phase
                and not (terminal_before_event and phase != "terminal")
                and not (is_child_event and phase == "terminal")
                and not (
                    run.get("manual_paused")
                    and event_type not in {"arp_run_resumed", "arp_run_done"}
                )
            ):
                run["phase"] = phase
            if event.get("task") and not run.get("goal"):
                run["goal"] = str(event.get("task"))
            if not run.get("goal") and event_type in {"schedule", "user_notification"}:
                run["goal"] = str(event.get("name") or event.get("title") or event.get("body") or "")
            if event.get("graph_name"):
                run["goal"] = str(event.get("graph_name"))
                run["graph_id"] = event.get("graph_id")
                run["kind"] = "arp"
            if event_type == "arp_run_start":
                runtime_budget = event.get("runtime_budget")
                if isinstance(runtime_budget, dict):
                    run["runtime_budget"] = copy.deepcopy(runtime_budget)
                task_contract = event.get("task_contract")
                if isinstance(task_contract, dict):
                    run["task_contract"] = copy.deepcopy(task_contract)
                satisfiability = event.get("satisfiability")
                if isinstance(satisfiability, dict):
                    run["satisfiability"] = copy.deepcopy(satisfiability)
                if event.get("experience_policy"):
                    run["experience_policy"] = str(event.get("experience_policy"))
            runtime_snapshot = event.get("runtime_snapshot")
            if isinstance(runtime_snapshot, dict):
                run["arp_snapshot"] = copy.deepcopy(runtime_snapshot)
                snapshot_runtime_version = str(
                    runtime_snapshot.get("runtime_version") or ""
                )
                if snapshot_runtime_version in {"legacy", "vnext"}:
                    frozen_runtime_version = str(
                        run.get("runtime_version") or ""
                    )
                    if (
                        frozen_runtime_version
                        and frozen_runtime_version
                        != snapshot_runtime_version
                    ):
                        run["runtime_version_mismatch"] = {
                            "frozen": frozen_runtime_version,
                            "observed": snapshot_runtime_version,
                            "event_type": event_type,
                        }
                    else:
                        run["runtime_version"] = snapshot_runtime_version
                snapshot_runtime_budget = runtime_snapshot.get(
                    "runtime_budget"
                )
                if isinstance(snapshot_runtime_budget, dict):
                    run["runtime_budget"] = copy.deepcopy(
                        snapshot_runtime_budget
                    )
                snapshot_no_legacy_fallback = runtime_snapshot.get(
                    "no_legacy_fallback"
                )
                if type(snapshot_no_legacy_fallback) is bool:
                    frozen_no_legacy_fallback = run.get(
                        "no_legacy_fallback"
                    )
                    if (
                        type(frozen_no_legacy_fallback) is bool
                        and frozen_no_legacy_fallback
                        != snapshot_no_legacy_fallback
                    ):
                        run["no_legacy_fallback_mismatch"] = {
                            "frozen": frozen_no_legacy_fallback,
                            "observed": snapshot_no_legacy_fallback,
                            "event_type": event_type,
                        }
                    else:
                        run["no_legacy_fallback"] = (
                            snapshot_no_legacy_fallback
                        )
                if isinstance(runtime_snapshot.get("task_contract"), dict):
                    run["task_contract"] = copy.deepcopy(runtime_snapshot["task_contract"])
                if isinstance(runtime_snapshot.get("satisfiability"), dict):
                    run["satisfiability"] = copy.deepcopy(runtime_snapshot["satisfiability"])
                if runtime_snapshot.get("experience_policy"):
                    run["experience_policy"] = str(runtime_snapshot["experience_policy"])
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
                if event.get("tool") in {"vlm_describe", "vlm_verify"}:
                    run["metrics"]["vlm_call_count"] += 1
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
            if event_type == "visual_feedback":
                metrics = run["metrics"]
                metrics["visual_feedback_frame_count"] = (
                    max(0, int(metrics.get("visual_feedback_frame_count") or 0)) + 1
                )
                capture_ms = _nonnegative_latency_ms(event.get("capture_latency_ms"))
                if capture_ms is not None:
                    visual_samples = metrics.setdefault(
                        "visual_feedback_capture_latency_ms", [],
                    )
                    visual_samples.append(capture_ms)
                    metrics["visual_feedback_capture_latency_ms"] = visual_samples[-200:]
                    # Preserve the existing aggregate capture metric so current
                    # dashboards see native-vision frame costs without an update.
                    capture_samples = metrics.setdefault("screen_capture_latency_ms", [])
                    capture_samples.append(capture_ms)
                    metrics["screen_capture_latency_ms"] = capture_samples[-200:]
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
                if isinstance(evidence, dict):
                    metadata = evidence.get("metadata")
                    metadata = copy.deepcopy(metadata) if isinstance(metadata, dict) else {}
                    provider = str(
                        metadata.get("provider")
                        or evidence.get("provider")
                        or evidence.get("source")
                        or ""
                    )
                    stage_id = str(
                        metadata.get("stage_id")
                        or evidence.get("stage_id")
                        or evidence.get("node_id")
                        or event.get("node_id")
                        or ""
                    )
                    contract_record = {
                        "id": str(evidence.get("id") or ""),
                        "predicate": str(evidence.get("predicate") or ""),
                        "source": str(evidence.get("source") or provider),
                        "provider": provider,
                        "verified": bool(evidence.get("verified", passed)),
                        "value": copy.deepcopy(evidence.get("value")),
                        "stage_id": stage_id,
                        "artifact_id": str(
                            metadata.get("artifact_id")
                            or evidence.get("artifact_id")
                            or ""
                        ),
                        "recipient": str(
                            metadata.get("recipient")
                            or evidence.get("recipient")
                            or ""
                        ),
                        "run_id": str(evidence.get("run_id") or run_id),
                        "captured_at": float(
                            evidence.get("captured_at") or event.get("ts") or time.time()
                        ),
                        "metadata": metadata,
                        "text": str(evidence.get("text") or ""),
                    }
                    records = run["contract_evidence"]
                    evidence_id = contract_record["id"]
                    if evidence_id:
                        records[:] = [item for item in records if item.get("id") != evidence_id]
                    records.append(contract_record)
                    run["contract_evidence"] = records[-100:]
                    if (
                        contract_record["predicate"] == "contract.complete"
                        and isinstance(metadata, dict)
                    ):
                        run["completion_contract"] = copy.deepcopy(metadata)
            if event.get("screenshot"):
                screenshot_path = str(event.get("screenshot") or "")
                already_projected = any(
                    item.get("type") == "screenshot"
                    and str(item.get("path") or "") == screenshot_path
                    for item in run["evidence"]
                    if isinstance(item, dict)
                )
                if screenshot_path and not already_projected:
                    run["evidence"].append({
                        "type": "screenshot",
                        "path": screenshot_path,
                        "kind": event.get("screenshot_kind") or "screen",
                        "region": copy.deepcopy(event.get("screenshot_region")),
                        "transient": bool(event.get("transient_observed")),
                        "ts": event.get("ts"),
                    })
            if event_type == "arp_node_start":
                run["current_node_id"] = event.get("node_id")
            if event_type == "task_stage_start":
                run["current_node_id"] = event.get("stage_id") or event.get("node_id")
                run["current_stage_type"] = event.get("stage_type")
                run["current_capabilities"] = copy.deepcopy(event.get("capabilities") or {})
            if event_type == "task_stage_done":
                run["current_stage_type"] = event.get("stage_type")
                run["last_stage_elapsed_ms"] = int(event.get("elapsed_ms") or 0)
                run["last_stage_artifact_ids"] = list(event.get("artifact_ids") or [])
                run["metrics"]["stage_count"] += 1
                run["metrics"]["model_call_count"] += max(0, int(event.get("model_calls") or 0))
                run["metrics"]["controller_elapsed_total_ms"] += max(0, int(event.get("elapsed_ms") or 0))
            if event_type == "task_stage_failed":
                run["current_stage_type"] = event.get("stage_type")
                run["metrics"]["stage_failure_count"] += 1
                run["failed_stage"] = {
                    "stage_id": event.get("stage_id"),
                    "stage_type": event.get("stage_type"),
                    "failure_type": event.get("failure_type"),
                    "recovery": copy.deepcopy(event.get("recovery") or {}),
                    "error": str(event.get("error") or ""),
                }
            if event_type == "arp_node_done" and event.get("kind") == "task_stage":
                output = event.get("output") if isinstance(event.get("output"), dict) else {}
                artifact = output.get("artifact") if isinstance(output.get("artifact"), dict) else {}
                interaction_state = (
                    output.get("interaction_state")
                    if isinstance(output.get("interaction_state"), dict)
                    else {}
                )
                runtime_metrics = (
                    event.get("runtime_metrics")
                    if isinstance(event.get("runtime_metrics"), dict)
                    else {}
                )
                stage_id = str(output.get("stage_id") or event.get("node_id") or "")
                stage_trace = {
                    "stage_id": stage_id,
                    "stage_type": str(output.get("stage_type") or ""),
                    "status": str(
                        output.get("status")
                        or ("done" if event.get("ok") is not False else "failed")
                    ),
                    "attempts": int(event.get("attempts") or 0),
                    "tools": list(output.get("tools") or []),
                    "model_tools": list(output.get("model_tools") or []),
                    "controller_tools": list(output.get("controller_tools") or []),
                    "required_capabilities": list(output.get("required_capabilities") or []),
                    "verifier_requirements": list(output.get("verifier_requirements") or []),
                    "exposed_capabilities": list(output.get("exposed_capabilities") or []),
                    "missing_capabilities": list(output.get("missing_capabilities") or []),
                    "satisfiable": bool(output.get("satisfiable")),
                    "adapter": str(output.get("adapter") or ""),
                    "model_role": str(output.get("model_role") or "none"),
                    "selected_evidence_providers": copy.deepcopy(
                        output.get("selected_evidence_providers") or {}
                    ),
                    "artifact_id": str(
                        output.get("artifact_id") or artifact.get("artifact_id") or ""
                    ),
                    "model_calls": int(
                        runtime_metrics.get("model_calls") or output.get("model_calls") or 0
                    ),
                    "model_elapsed_ms": int(
                        runtime_metrics.get("model_elapsed_ms")
                        or output.get("model_elapsed_ms")
                        or 0
                    ),
                    "controller_elapsed_ms": int(
                        runtime_metrics.get("controller_elapsed_ms")
                        or output.get("controller_elapsed_ms")
                        or 0
                    ),
                    "capture_elapsed_ms": int(
                        runtime_metrics.get("capture_elapsed_ms")
                        or output.get("capture_elapsed_ms")
                        or 0
                    ),
                    "budget": copy.deepcopy(
                        runtime_metrics.get("budget")
                        or output.get("budget")
                        or interaction_state.get("budget")
                        or {}
                    ),
                    "failure_type": str(output.get("failure_type") or ""),
                    "recovery": copy.deepcopy(output.get("recovery") or {}),
                    "error": str(event.get("error") or output.get("error") or ""),
                    "interaction_state": copy.deepcopy(interaction_state),
                }
                traces = run["stage_trace"]
                traces[:] = [item for item in traces if item.get("stage_id") != stage_id]
                traces.append(stage_trace)
                run["stage_trace"] = traces[-100:]
            if event_type == "arp_node_retry":
                run["metrics"]["retry_count"] += 1
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
                run["manual_paused"] = False
                run["status"] = str(event.get("status") or "done")
                run["result"] = _compact(event.get("result"))
                run["error"] = str(event.get("error") or "")
                run["current_node_id"] = None
                run["current_tool"] = None
                run["finished_at"] = run["updated_at"]
            elif event_type == "schedule" and event.get("action") == "finished":
                run["status"] = str(event.get("status") or "done")
                run["result"] = _compact(event.get("result"))
                run["error"] = str(event.get("error") or "")
                run["current_tool"] = None
                run["finished_at"] = run["updated_at"]
            elif event_type == "arp_run_paused":
                run["manual_paused"] = True
                run["status"] = "paused"
            elif event_type == "arp_run_resumed":
                run["manual_paused"] = False
                run["status"] = "running"
            elif run.get("status") in _TERMINAL:
                pass
            elif run.get("manual_paused"):
                run["status"] = "paused"
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

    def hydrate_directory(
        self,
        directory: Path,
        limit: int = 100,
        *,
        mark_incomplete_interrupted: bool = False,
    ) -> int:
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
        if mark_incomplete_interrupted:
            now = time.time()
            with self._lock:
                for run in self._runs.values():
                    if run.get("status") in _TERMINAL:
                        continue
                    prior_phase = str(run.get("phase") or "")
                    prior_tool = run.get("current_tool")
                    run["status"] = "interrupted"
                    run["phase"] = "terminal"
                    run["error"] = (
                        "Runtime exited before this persisted run emitted a terminal event."
                    )
                    run["current_tool"] = None
                    run["updated_at"] = now
                    run["finished_at"] = now
                    run["timeline"].append({
                        "type": "run_interrupted",
                        "ts": now,
                        "phase": "terminal",
                        "node_id": run.get("current_node_id"),
                        "tool": prior_tool,
                        "status": "interrupted",
                        "summary": f"Recovered incomplete persisted run from {prior_phase or 'unknown'} phase",
                        "child_run_id": None,
                    })
                    run["timeline"] = run["timeline"][-200:]
        return hydrated

    @staticmethod
    def _public(run: dict[str, Any], *, include_details: bool = True) -> dict[str, Any]:
        data = copy.deepcopy(run)
        if data.get("runtime_version") not in {"legacy", "vnext"}:
            # Keep the internal value unfrozen until an authoritative event
            # arrives, while preserving the public legacy-compatible wire type.
            data["runtime_version"] = "legacy"
        for internal_key in (
            "task_contract",
            "satisfiability",
            "experience_policy",
            "stage_trace",
            "contract_evidence",
            "completion_contract",
            "arp_snapshot",
        ):
            data.pop(internal_key, None)
        if not include_details:
            # The list endpoint feeds navigation, not forensic inspection.
            # Returning every timeline and nested evidence record for up to 200
            # runs made a single frontend refresh dwarf all control traffic.
            data["timeline"] = []
            data["evidence"] = []
            data["result"] = None
        metrics = data["metrics"]
        tool_samples = metrics.pop("tool_latency_ms", [])
        perception_samples = metrics.pop("perception_latency_ms", [])
        decision_samples = metrics.pop("decision_latency_ms", [])
        action_samples = metrics.pop("action_latency_ms", [])
        verify_samples = metrics.pop("post_verify_latency_ms", [])
        semantic_verify_samples = metrics.pop("semantic_verify_latency_ms", [])
        screen_delta_samples = metrics.pop("screen_delta_latency_ms", [])
        capture_samples = metrics.pop("screen_capture_latency_ms", [])
        visual_feedback_samples = metrics.pop("visual_feedback_capture_latency_ms", [])
        servo_samples = metrics.pop("visual_servo_latency_ms", [])
        metrics["tool_latency_avg_ms"] = round(sum(tool_samples) / len(tool_samples), 1) if tool_samples else 0
        metrics["tool_latency_max_ms"] = max(tool_samples) if tool_samples else 0
        metrics["perception_latency_avg_ms"] = round(sum(perception_samples) / len(perception_samples), 1) if perception_samples else 0
        metrics["perception_latency_max_ms"] = max(perception_samples) if perception_samples else 0
        metrics["screen_capture_latency_avg_ms"] = round(sum(capture_samples) / len(capture_samples), 1) if capture_samples else 0
        metrics["screen_capture_latency_max_ms"] = max(capture_samples) if capture_samples else 0
        metrics["visual_feedback_capture_latency_latest_ms"] = (
            visual_feedback_samples[-1] if visual_feedback_samples else 0
        )
        metrics["visual_feedback_capture_latency_avg_ms"] = round(
            sum(visual_feedback_samples) / len(visual_feedback_samples), 1,
        ) if visual_feedback_samples else 0
        metrics["visual_feedback_capture_latency_p95_ms"] = _percentile(
            visual_feedback_samples, 0.95,
        )
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

    @staticmethod
    def _arp_public(run: dict[str, Any]) -> dict[str, Any] | None:
        if run.get("kind") != "arp":
            return None
        persisted = run.get("arp_snapshot")
        snapshot = copy.deepcopy(persisted) if isinstance(persisted, dict) else {}
        snapshot.update({
            "run_id": str(run.get("run_id") or ""),
            "runtime_version": str(
                run.get("runtime_version")
                or snapshot.get("runtime_version")
                or "legacy"
            ),
            "runtime_budget": copy.deepcopy(
                run.get("runtime_budget")
                or snapshot.get("runtime_budget")
                or {}
            ),
            "no_legacy_fallback": (
                run.get("no_legacy_fallback")
                if type(run.get("no_legacy_fallback")) is bool
                else snapshot.get("no_legacy_fallback") is True
            ),
            "graph_id": str(run.get("graph_id") or snapshot.get("graph_id") or ""),
            "graph_name": str(run.get("goal") or snapshot.get("graph_name") or ""),
            "status": str(run.get("status") or snapshot.get("status") or ""),
            "current_node_id": run.get("current_node_id"),
            "error": str(run.get("error") or snapshot.get("error") or ""),
            "experience_policy": str(
                run.get("experience_policy") or snapshot.get("experience_policy") or "allow"
            ),
            "audit_source": "runtime_snapshot" if isinstance(persisted, dict) else "trace_projection",
        })
        if isinstance(run.get("task_contract"), dict):
            snapshot["task_contract"] = copy.deepcopy(run["task_contract"])
        if isinstance(run.get("satisfiability"), dict):
            snapshot["satisfiability"] = copy.deepcopy(run["satisfiability"])
        if run.get("stage_trace"):
            snapshot["stage_trace"] = copy.deepcopy(run["stage_trace"])
        records = copy.deepcopy(run.get("contract_evidence") or [])
        if records:
            snapshot["contract_evidence"] = {
                "run_id": str(run.get("run_id") or ""),
                "count": len(records),
                "records": records,
            }
        if isinstance(run.get("completion_contract"), dict):
            snapshot["completion_contract"] = copy.deepcopy(run["completion_contract"])
        return snapshot

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            return self._public(run) if run else None

    def arp_snapshot(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            return self._arp_public(run) if run else None

    def current(self) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(self._current_id or "")
            return self._public(run) if run else None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(self._order)[-max(1, limit):]
            ids.reverse()
            return [
                self._public(self._runs[run_id], include_details=False)
                for run_id in ids
                if run_id in self._runs
            ]


RUN_PROJECTIONS = RunProjectionStore()
