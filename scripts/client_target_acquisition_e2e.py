"""Independent contracts and oracle for target-acquisition client E2E."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from client_target_acquisition_fixture import AcquisitionFixture, same_path


def _binding_identity_sha256(binding: dict[str, Any]) -> str:
    material = {
        "format": "target-binding-identity/1",
        "hwnd": binding.get("hwnd"),
        "root_hwnd": binding.get("root_hwnd"),
        "process_id": binding.get("process_id"),
        "process_identity": binding.get("process_identity"),
        "epoch": binding.get("epoch"),
    }
    return hashlib.sha256(json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def acquisition_trace_evidence(
    trace_file: object, *, expected_run_id: str, mode: str,
) -> dict[str, Any]:
    path = Path(str(trace_file or ""))
    if not path.is_file():
        return {
            "available": False, "passed": False,
            "status": "source_unavailable",
            "errors": ["bound trace unavailable"],
        }
    try:
        events = [
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except Exception as exc:
        return {
            "available": False, "passed": False,
            "status": "harness_invalid",
            "errors": [f"trace parse failed: {type(exc).__name__}"],
        }
    selected = [item for item in events if item.get("type") == "desktop_runtime_selected"]
    started = [
        item for item in events
        if item.get("type") == "vnext_mission_runtime_started"
    ]
    turns = [item for item in events if item.get("type") == "vnext_mission_runtime_turn"]
    discoveries = [item for item in turns if item.get("proposal_kind") == "discover_target"]
    queue_action = "activate_target" if mode in {"existing", "cold"} else None
    acquisition_turns = [
        item for item in turns
        if item.get("accepted_tool_name") in {"activate_target", "open_app"}
    ]
    acquisitions = [
        item for item in acquisition_turns
        if item.get("accepted_tool_name") == "activate_target"
    ]
    accepted = [
        item for turn in turns for item in (turn.get("accepted_events") or [])
        if item.get("kind") == "target_bound"
    ]
    bindings = [
        item for item in events if item.get("type") == "vnext_target_binding_accepted"
    ]
    queues = [
        (index, item) for index, item in enumerate(events)
        if item.get("type") == "desktop_action_queue"
        and item.get("action") in {"activate_target", "open_app"}
    ]
    jobs = {item.get("job_id") for _index, item in queues if item.get("job_id")}
    errors = []
    if type(expected_run_id) is not str or not expected_run_id.strip():
        errors.append("bound trace has no exact run identity")
    if (
        len(selected) != 1
        or selected[0].get("runtime_version") != "vnext"
    ):
        errors.append("trace does not contain one vnext runtime selection")
    if any(
        item.get("run_id") != expected_run_id
        or item.get("runtime_version") != "vnext"
        for item in (*started, *turns, *bindings)
    ):
        errors.append("vnext trace events are not bound to the exact run")
    if (
        len(started) != 1
        or started[0].get("run_id") != expected_run_id
        or started[0].get("runtime_version") != "vnext"
        or started[0].get("initial_target_mode") != "targetless"
        or started[0].get("initial_target_reason") != "absent"
    ):
        errors.append("runtime did not accept exact targetless handoff absence")
    if queue_action is None:
        errors.append("trace acquisition mode is invalid")
    if (
        len(discoveries) != 1
        or len(acquisitions) != 1
        or acquisitions != acquisition_turns
    ):
        errors.append(
            "trace does not contain one discover then one activate_target",
        )
    if len(accepted) != 1 or len(bindings) != 1:
        errors.append("trace does not contain one proved target binding")
    else:
        binding = bindings[0]
        acquisition = acquisitions[0] if len(acquisitions) == 1 else None
        turn_bindings = [
            item for item in (acquisition or {}).get("accepted_events") or []
            if item.get("kind") == "target_bound"
        ]
        trust = binding.get("evidence_trust")
        if (
            binding.get("format") != "vnext-target-binding-accepted/1"
            or binding.get("runtime_version") != "vnext"
            or binding.get("run_id") != expected_run_id
            or any(
                type(binding.get(key)) is not str
                or not binding[key].strip()
                for key in (
                    "event_id", "mission_id", "obligation_id", "binding_id",
                    "process_identity", "canonical_identity_sha256",
                    "acquisition_action_id",
                )
            )
            or binding.get("event_id") != accepted[0].get("event_id")
            or type(binding.get("epoch")) is not int
            or binding["epoch"] < 0
            or any(
                type(binding.get(key)) is not int or binding[key] <= 0
                for key in ("hwnd", "root_hwnd", "process_id")
            )
            or not isinstance(binding.get("region"), list)
            or len(binding["region"]) != 4
            or len(binding.get("evidence_refs") or []) != 1
            or not isinstance(trust, list)
            or len(trust) != 1
            or trust[0] != {
                "evidence_id": binding["evidence_refs"][0],
                "kind": "authoritative_target_binding",
                "trust": "deterministic",
            }
        ):
            errors.append("target binding sidecar identity is invalid")
        if (
            acquisition is None
            or type(acquisition.get("decision_id")) is not str
            or not acquisition["decision_id"].strip()
            or acquisition.get("decision_id")
            != binding.get("acquisition_action_id")
            or len(turn_bindings) != 1
            or type(turn_bindings[0].get("event_id")) is not str
            or not turn_bindings[0]["event_id"].strip()
            or turn_bindings[0].get("event_id") != binding.get("event_id")
            or events.index(acquisition) >= events.index(binding)
        ):
            errors.append("target binding is not caused by the acquisition turn")
    if (
        len(queues) != 2
        or len(jobs) != 1
        or [item.get("state") for _index, item in queues] != ["start", "done"]
        or any(item.get("action") != queue_action for _index, item in queues)
    ):
        errors.append("trace does not contain one completed acquisition queue job")
    if discoveries and acquisitions:
        first_queue = min((index for index, _item in queues), default=-1)
        last_queue = max((index for index, _item in queues), default=-1)
        if not (
            events.index(discoveries[0])
            < first_queue <= last_queue
            < events.index(acquisitions[0])
        ):
            errors.append("trace acquisition ordering is invalid")
    runtime_terminals = [
        item for item in events
        if item.get("type") == "vnext_mission_runtime_finished"
        and item.get("run_id") == expected_run_id
    ]
    outer_terminals = [
        item for item in events
        if item.get("type") in {
            "arp_run_done", "arp_run_failed", "arp_run_canceled",
        }
        and item.get("run_id") == expected_run_id
    ]
    if (
        len(runtime_terminals) != 1
        or runtime_terminals[0].get("mission_status") != "verified"
        or runtime_terminals[0].get("runtime_version") != "vnext"
        or len(outer_terminals) != 1
        or outer_terminals[0].get("type") != "arp_run_done"
        or outer_terminals[0].get("status") != "done"
        or (
            len(bindings) == 1
            and events.index(bindings[0]) >= events.index(runtime_terminals[0])
        )
        or events.index(runtime_terminals[0]) >= events.index(outer_terminals[0])
    ):
        errors.append("trace mission terminal is incomplete")
    return {
        "available": True, "passed": not errors, "errors": errors,
        "status": "passed" if not errors else "harness_invalid",
        "binding": bindings[0] if len(bindings) == 1 else None,
        "initial_target_mode": (
            started[0].get("initial_target_mode") if len(started) == 1 else None
        ),
        "target_bound_count": len(accepted), "queue_job_ids": sorted(jobs),
    }


def target_acquisition_verdict(
    *,
    mode: str,
    fixture: AcquisitionFixture,
    baseline: dict[str, Any],
    client_result: dict[str, Any],
) -> dict[str, Any]:
    invalid: list[str] = []
    configuration = client_result.get("configuration") or {}
    trace_binding = client_result.get("trace_binding") or {}
    if configuration.get("runtime_version") != "vnext":
        invalid.append("client did not explicitly select vnext")
    if configuration.get("cold_start_toggle") != 1:
        invalid.append("client cold-start toggle is not enabled")
    if configuration.get("start_paused_toggle") != 1:
        invalid.append("client start-paused toggle is not enabled")
    if (client_result.get("session_reset") or {}).get("after") != 0:
        invalid.append("client session reset is not empty")
    if (client_result.get("memory_isolation") or {}).get("passed") is not True:
        invalid.append("run memory isolation is incomplete")
    run_id = str((client_result.get("result") or {}).get("run_id") or "")
    before_handoff = client_result.get("before_submit_handoff") or {}
    after_handoff = client_result.get("after_submit_handoff") or {}
    pause_barrier = after_handoff.get("pause_barrier") or {}
    resume_barrier = after_handoff.get("resume_barrier") or {}
    guarded = before_handoff.get("guarded_fixture_hwnds")
    verified = after_handoff.get("verified_fixture_hwnds")
    client = client_result.get("client") or {}
    expected_fixture_hwnds = sorted(
        int(window.get("hwnd") or 0)
        for row in baseline.get("instances") or []
        for window in row.get("windows") or []
    )
    before_queue = (
        (client_result.get("runtime_before") or {}).get("desktop_action_queue")
        or {}
    )
    paused_queue = pause_barrier.get("desktop_action_queue") or {}
    queue_keys = (
        "submitted", "completed", "failed",
        "async_submitted", "async_completed", "async_failed",
    )
    queue_unchanged = all(
        before_queue.get(key) == paused_queue.get(key) for key in queue_keys
    )
    queue_observed = all(
        type(before_queue.get(key)) is int
        and before_queue.get(key, -1) >= 0
        and type(paused_queue.get(key)) is int
        and paused_queue.get(key, -1) >= 0
        for key in queue_keys
    )
    if (
        before_handoff.get("guard_started") is not True
        or before_handoff.get("guard_armed") is not True
        or type(before_handoff.get("shell_hwnd")) is not int
        or before_handoff.get("shell_hwnd", 0) <= 0
        or type(before_handoff.get("control_surface_hwnd")) is not int
        or before_handoff.get("control_surface_hwnd", 0) <= 0
        or before_handoff.get("control_surface_hwnd") != client.get("hwnd")
        or type(before_handoff.get("control_surface_pid")) is not int
        or before_handoff.get("control_surface_pid", 0) <= 0
        or before_handoff.get("control_surface_pid") != client.get("pid")
        or not isinstance(guarded, list)
        or any(type(hwnd) is not int or hwnd <= 0 for hwnd in guarded)
        or sorted(guarded) != expected_fixture_hwnds
        or not isinstance(verified, list)
        or any(type(hwnd) is not int or hwnd <= 0 for hwnd in verified)
        or after_handoff.get("focus_strategy") != "shell_foreground_guard"
        or after_handoff.get("shell_hwnd") != before_handoff.get("shell_hwnd")
        or after_handoff.get("foreground_hwnd") != before_handoff.get("shell_hwnd")
        or type(after_handoff.get("shell_observation_count")) is not int
        or after_handoff.get("shell_observation_count", 0) <= 0
        or sorted(verified or []) != expected_fixture_hwnds
        or pause_barrier.get("run_id") != run_id
        or pause_barrier.get("status") != "paused"
        or pause_barrier.get("manual_paused") is not True
        or pause_barrier.get("started_at") is not None
        or pause_barrier.get("history_count") != 0
        or not queue_observed
        or not queue_unchanged
        or resume_barrier.get("run_id") != run_id
        or resume_barrier.get("status") not in {"queued", "running"}
        or resume_barrier.get("manual_paused") is not False
        or resume_barrier.get("control_method") != "click"
        or resume_barrier.get("control_surface_iconic") is not True
        or type(resume_barrier.get("foreground_hwnd")) is not int
        or resume_barrier.get("foreground_hwnd", 0) <= 0
        or resume_barrier.get("foreground_hwnd") != before_handoff.get("shell_hwnd")
        or resume_barrier.get("foreground_hwnd") == client.get("hwnd")
    ):
        invalid.append("targetless client handoff proof is incomplete")
    if (
        trace_binding.get("passed") is not True
        or str(trace_binding.get("run_id") or "") != run_id
        or list(trace_binding.get("parse_errors") or [])
    ):
        invalid.append("client trace binding is incomplete or not run-scoped")
    trace = acquisition_trace_evidence(
        trace_binding.get("trace_file"),
        expected_run_id=run_id, mode=mode,
    )
    if not trace["passed"]:
        invalid.extend(trace["errors"])
    if invalid:
        return {
            "source": "external_target_acquisition_oracle",
            "oracle_status": "unavailable",
            "status": (
                "source_unavailable"
                if trace.get("status") == "source_unavailable" and len(invalid) == 1
                else "harness_invalid"
            ),
            "passed": False, "errors": invalid, "trace": trace,
        }
    try:
        after, records = fixture.instances(), fixture.launch_records()
    except Exception as exc:
        return {
            "source": "external_target_acquisition_oracle",
            "oracle_status": "unavailable", "status": "oracle_unavailable",
            "passed": False,
            "errors": [f"independent OS facts unavailable: {type(exc).__name__}"],
            "trace": trace,
        }
    before, before_records = baseline["instances"], baseline["launch_records"]
    errors = list(trace["errors"])
    if mode == "existing":
        if (
            len(before) != 1
            or len(before_records) != 1
            or after != before
            or records != before_records
        ):
            errors.append("existing fixture was not reused without a new launch")
    elif mode == "cold":
        if before or before_records or len(after) != 1 or len(records) != 1:
            errors.append("cold fixture did not produce exactly one launch")
        elif not fixture.records_match(records, after):
            errors.append("cold launch log path/hash/PID identity disagrees")
    else:
        errors.append("unknown acquisition mode")
    binding = trace.get("binding") or {}
    if len(after) == 1:
        window = (after[0].get("windows") or [{}])[0]
        if (
            not same_path(after[0].get("exe_path"), fixture.executable)
            or after[0].get("create_time_ns", 0) <= 0
            or len(after[0].get("windows") or []) != 1
            or binding.get("process_id") != after[0].get("process_id")
            or binding.get("hwnd") != window.get("hwnd")
            or binding.get("root_hwnd") != window.get("root_hwnd")
            or binding.get("process_identity") != after[0].get("process_identity")
            or binding.get("region") != window.get("region")
            or binding.get("canonical_identity_sha256")
            != _binding_identity_sha256(binding)
        ):
            errors.append("trace and fixture strong target identity disagree")
    return {
        "source": "external_target_acquisition_oracle",
        "oracle_status": "conclusive", "status": "failed" if errors else "passed",
        "passed": not errors, "errors": errors, "trace": trace,
        "before_instance_count": len(before), "after_instance_count": len(after),
        "launch_record_count": len(records),
    }


__all__ = [
    "acquisition_trace_evidence", "target_acquisition_verdict",
]
