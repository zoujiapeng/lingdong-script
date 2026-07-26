#!/usr/bin/env python3
"""Adjudicate PR #15 legacy runs from immutable, hash-bound artifacts.

The acquisition reports are never rewritten.  This oracle corrects three
test-only false negatives that are fully decidable from the saved external
events, low-level Windows hooks, screenshots, bound traces, and budget cutoff:

* the terminal text snapshot was mistaken for a fifth keyboard event;
* a timed-out dynamic-hold failure was mistaken for an unsettled observer;
* the harness' own WebSocket close frame was mistaken for an early disconnect.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[1]
VISUAL_HARNESS = WORKSPACE / "scripts" / "client_visual_e2e_run.py"
CLIENT_HARNESS = WORKSPACE / "scripts" / "client_e2e_run.py"
EXPECTED_CASES = (
    "click-double-click",
    "scroll-bidirectional",
    "text-hotkey",
    "dynamic-hold",
    "stop-cancel",
)
EXPECTED_TEXT_SEED = 22025
EXPECTED_DYNAMIC_SEED = 161803
EXPECTED_CANCEL_SEED = 22026
EXPECTED_CLOSE_ERROR = (
    "JSONDecodeError: Expecting value: line 1 column 1 (char 0)"
)
POINTER_TOOLS = {"drag", "move_mouse", "mouse_down", "mouse_up"}
PHYSICAL_ACTIONS = {
    "click",
    "double_click",
    "type_text",
    "hotkey",
    "scroll",
    "move_mouse",
    "mouse_down",
    "mouse_up",
    "drag",
    "draw_strokes",
    "paste_image_file",
}


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _load_visual_harness() -> Any:
    spec = importlib.util.spec_from_file_location(
        "pr15_saved_artifact_visual_harness",
        VISUAL_HARNESS,
    )
    _require(spec is not None and spec.loader is not None, "cannot load harness")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _case_map(suite: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items = [
        item
        for item in (suite.get("results") or [])
        if isinstance(item, dict) and item.get("name")
    ]
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        name = str(item.get("name") or "")
        _require(name not in result, f"duplicate suite case: {name}")
        result[name] = item
    _require(len(items) == len(EXPECTED_CASES), "suite case count mismatch")
    _require(tuple(result) == EXPECTED_CASES, "suite case order/identity mismatch")
    return result


def _harness_identity(report: dict[str, Any]) -> tuple[Any, Any]:
    harness = report.get("harness") or {}
    return harness.get("revision"), harness.get("artifact_sha256")


def _rerun_matches_reference(
    report: dict[str, Any],
    reference: dict[str, Any],
    *,
    case_name: str,
) -> None:
    for key in (
        "name",
        "scenario",
        "seed",
        "task",
        "budget",
        "entrypoint",
        "cold_start_required",
        "ground_truth",
        "product",
    ):
        _require(
            report.get(key) == reference.get(key),
            f"{case_name}: selected rerun {key} differs from paired suite",
        )


def _common_case_checks(
    report: dict[str, Any],
    *,
    expected_commit: str,
    expected_name: str,
    expected_scenario: str,
    expected_seed: int,
    acquisition_harness: tuple[Any, Any],
) -> None:
    product = report.get("product") or {}
    _require(report.get("name") == expected_name, f"{expected_name}: name mismatch")
    _require(
        report.get("scenario") == expected_scenario,
        f"{expected_name}: scenario mismatch",
    )
    _require(
        int(report.get("seed") or 0) == expected_seed,
        f"{expected_name}: seed mismatch",
    )
    _require(
        product.get("product_revision") == expected_commit,
        f"{expected_name}: product commit mismatch",
    )
    _require(
        product.get("tracked_worktree_clean") is True,
        f"{expected_name}: product worktree was not clean",
    )
    _require(
        _harness_identity(report) == acquisition_harness,
        f"{expected_name}: acquisition harness mismatch",
    )
    _require(not report.get("runner_error"), f"{expected_name}: runner error")
    trace_binding = (report.get("client_result") or {}).get("trace_binding") or {}
    _require(
        trace_binding.get("passed") is True
        and not trace_binding.get("parse_errors"),
        f"{expected_name}: trace binding is not exact",
    )
    _require(
        bool(report.get("outer_run_ids"))
        and bool(report.get("child_run_ids"))
        and bool((report.get("trace") or {}).get("file")),
        f"{expected_name}: run/trace identity is incomplete",
    )
    before = report.get("target_before_evidence") or {}
    after = report.get("target_after") or {}
    _require(
        before.get("visually_nonblank") is True
        and after.get("print_window_succeeded") is True
        and after.get("visually_nonblank") is True,
        f"{expected_name}: before/after target evidence is incomplete",
    )
    _require(
        not any((report.get("modifier_state_before") or {}).values())
        and not any((report.get("modifier_state_after") or {}).values()),
        f"{expected_name}: physical modifier state is not released",
    )


def _trace_metrics(report: dict[str, Any]) -> dict[str, Any]:
    trace_path = Path(str((report.get("trace") or {}).get("file") or ""))
    _require(trace_path.is_file(), f"bound trace does not exist: {trace_path}")
    executed: list[str] = []
    proposed: list[str] = []
    batch_starts: list[str] = []
    events: list[dict[str, Any]] = []
    for line_number, raw in enumerate(
        trace_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        try:
            event = json.loads(raw)
        except Exception as error:
            raise ValueError(
                f"trace line {line_number} is invalid: {type(error).__name__}: {error}",
            ) from error
        if not isinstance(event, dict):
            continue
        events.append(event)
        event_type = event.get("type")
        envelope = event.get("action_envelope") or {}
        tool = str(envelope.get("type") or event.get("tool") or "")
        action = str(event.get("action") or "")
        if event_type == "action" and action in PHYSICAL_ACTIONS:
            executed.append(action)
        elif event_type == "agent_tool_start" and tool in PHYSICAL_ACTIONS:
            proposed.append(tool)
        elif (
            event_type == "agent_batch_action_start"
            and tool in PHYSICAL_ACTIONS
        ):
            batch_starts.append(tool)
    return {
        "trace_file": str(trace_path),
        "trace_sha256": _sha256(trace_path),
        "executed_physical_action_count": len(executed),
        "executed_physical_actions": executed,
        "proposed_physical_action_attempt_count": len(proposed),
        "batch_physical_action_start_count": len(batch_starts),
        "_events": events,
    }


def _normalize_case_action_count(report: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(report)
    metrics = _trace_metrics(result)
    legacy_count = result.get(
        "legacy_reported_action_count",
        result.get("action_count"),
    )
    result["legacy_reported_action_count"] = legacy_count
    result["action_count"] = metrics["executed_physical_action_count"]
    verdict = copy.deepcopy(result.get("external_verdict") or {})
    verdict.update({
        "legacy_reported_action_count": legacy_count,
        "action_count": metrics["executed_physical_action_count"],
        "trace_action_metrics": {
            key: value for key, value in metrics.items() if key != "_events"
        },
    })
    result["external_verdict"] = verdict
    return result


def _artifact_binding(
    report_path: Path,
    report: dict[str, Any],
    *,
    extra_paths: tuple[Path, ...] = (),
) -> dict[str, str]:
    paths = [
        report_path,
        Path(str((report.get("trace") or {}).get("file") or "")),
        Path(str(report.get("target_before") or "")),
        Path(str((report.get("target_after") or {}).get("path") or "")),
        *extra_paths,
    ]
    unique: dict[str, str] = {}
    for path in paths:
        _require(path.is_file(), f"bound artifact is unavailable: {path}")
        unique[str(path.resolve())] = _sha256(path)
    return unique


def _debug_timeline_binding(
    product_root: Path,
    run_ids: list[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for run_id in dict.fromkeys(item for item in run_ids if item):
        root = (
            product_root
            / "python"
            / "winpilot_home"
            / "traces"
            / run_id
        )
        _require(root.is_dir(), f"debug timeline is unavailable: {root}")
        paths = sorted(root.glob("*.json"))
        _require(paths, f"debug timeline is empty: {root}")
        for path in paths:
            stem_parts = path.stem.split("_", 1)
            result.append({
                "run_id": run_id,
                "file": str(path.resolve()),
                "index": (
                    int(stem_parts[0])
                    if stem_parts[0].isdigit()
                    else -1
                ),
                "kind": (
                    stem_parts[1]
                    if len(stem_parts) == 2
                    else path.stem
                ),
                "mtime": path.stat().st_mtime,
                "sha256": _sha256(path),
            })
    return result


def _adjudicate_text(
    report_path: Path,
    report: dict[str, Any],
    *,
    expected_commit: str,
    acquisition_harness: tuple[Any, Any],
    visual_harness: Any,
) -> dict[str, Any]:
    _common_case_checks(
        report,
        expected_commit=expected_commit,
        expected_name="text-hotkey",
        expected_scenario="text_hotkey",
        expected_seed=EXPECTED_TEXT_SEED,
        acquisition_harness=acquisition_harness,
    )
    raw_verdict = report.get("external_verdict") or {}
    _require(
        raw_verdict.get("valid") is True
        and raw_verdict.get("passed") is False
        and raw_verdict.get("errors")
        == ["Ctrl+Enter and modifier release sequence is not exact"]
        and not raw_verdict.get("invalid_reasons"),
        "text-hotkey raw failure is not the known terminal-snapshot bug",
    )
    input_evidence = report.get("input_evidence") or {}
    _require(
        not input_evidence.get("errors")
        and not [
            item
            for item in (input_evidence.get("events") or [])
            if item.get("injected") is False
        ],
        "text-hotkey has hook failure or non-injected host input",
    )
    verdict = visual_harness.external_verdict(
        report.get("external_state") or {},
        case_id=str(report.get("case_id") or ""),
        scenario="text_hotkey",
        seed=EXPECTED_TEXT_SEED,
    )
    _require(
        verdict.get("passed") is True
        and verdict.get("duplicate_input_count") == 0
        and not verdict.get("errors"),
        "saved text/hotkey events do not prove exact input",
    )
    metrics = _trace_metrics(report)
    result = copy.deepcopy(report)
    verdict.update({
        "source": "saved_external_browser_state_and_low_level_input_hook",
        "valid": True,
        "oracle_status": "conclusive",
        "passed": True,
        "invalid_reasons": [],
        "first_failure_owner": None,
        "action_count": metrics["executed_physical_action_count"],
        "trace_action_metrics": {
            key: value for key, value in metrics.items() if key != "_events"
        },
    })
    adjudication = {
        "schema": "pr15-saved-artifact-case-adjudication/v1",
        "raw_report": str(report_path.resolve()),
        "raw_report_sha256": _sha256(report_path),
        "raw_oracle_status": raw_verdict.get("oracle_status"),
        "raw_grounded_verdict": report.get("grounded_verdict"),
        "known_oracle_bug": "terminal_snapshot_is_not_a_keyboard_event",
        "adjudicated_status": "conclusive",
        "grounded_outcome": "grounded_pass",
        "exact_text_and_hotkey_proved": True,
        "artifact_sha256": _artifact_binding(report_path, report),
    }
    result.update({
        "report": str(report_path.resolve()),
        "external_verdict": verdict,
        "legacy_reported_action_count": report.get("action_count"),
        "action_count": metrics["executed_physical_action_count"],
        "duplicate_input_count": 0,
        "valid": True,
        "grounded_verdict": True,
        "first_failure_owner": None,
        "passed": True,
        "posthoc_adjudication": adjudication,
    })
    return result


def _hook_match(
    hooks: list[dict[str, Any]],
    message: str,
    event: dict[str, Any],
) -> bool:
    return any(
        item.get("kind") == "mouse"
        and item.get("message") == message
        and item.get("injected") is True
        and int(item.get("x") or 0) == int(event.get("screen_x") or 0)
        and int(item.get("y") or 0) == int(event.get("screen_y") or 0)
        and abs(
            float(item.get("at") or 0)
            - float(event.get("received_at") or 0)
        )
        <= 0.25
        for item in hooks
    )


def _dynamic_attempt_proofs(
    report: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    events = list((report.get("external_state") or {}).get("events") or [])
    hooks = list((report.get("input_evidence") or {}).get("events") or [])
    case_id = report.get("case_id")
    sessions: list[list[dict[str, Any]]] = []
    session: list[dict[str, Any]] = []
    for event in events:
        _require(
            event.get("case_id") == case_id
            and event.get("scenario") == "dynamic_hold"
            and int(event.get("seed") or 0) == EXPECTED_DYNAMIC_SEED,
            "dynamic event identity mismatch",
        )
        if event.get("type") == "page_ready" and session:
            sessions.append(session)
            session = []
        session.append(event)
    if session:
        sessions.append(session)
    proofs: list[dict[str, Any]] = []
    total_down_count = 0
    for session_index, current_session in enumerate(sessions):
        sequences = [
            int((item.get("details") or {}).get("event_sequence") or 0)
            for item in current_session
        ]
        _require(
            all(later > earlier for earlier, later in zip(sequences, sequences[1:])),
            "dynamic event sequence is not monotonic within a page session",
        )
        active: list[dict[str, Any]] | None = None
        for event in current_session:
            if event.get("type") == "pointerdown":
                total_down_count += 1
                active = [event]
            elif active is not None:
                active.append(event)
                if event.get("type") != "pointerup":
                    continue
                down = active[0]
                up = active[-1]
                middle = active[1:-1]
                moves = [item for item in middle if item.get("type") == "pointermove"]
                checkpoints = [
                    item for item in middle
                    if item.get("type") == "checkpoint_reached"
                ]
                details = up.get("details") or {}
                offsets = {
                    (
                        int(item.get("screen_x") or 0)
                        - int(item.get("client_x") or 0),
                        int(item.get("screen_y") or 0)
                        - int(item.get("client_y") or 0),
                    )
                    for item in active
                    if item.get("type")
                    in {"pointerdown", "pointermove", "checkpoint_reached", "pointerup"}
                }
                trusted = all(item.get("trusted") is True for item in active)
                if (
                    trusted
                    and len(moves) >= 2
                    and len(checkpoints) == 1
                    and details.get("checkpoint_reached") is True
                    and details.get("goal_revealed_after_down") is True
                    and details.get("inside_goal") is True
                    and details.get("completion_event") == "pointerup"
                    and int(up.get("move_count") or 0) >= 2
                    and len(offsets) == 1
                    and _hook_match(hooks, "left_down", down)
                    and _hook_match(hooks, "left_up", up)
                ):
                    proofs.append({
                        "session_index": session_index,
                        "down_event_sequence": (
                            down.get("details") or {}
                        ).get("event_sequence"),
                        "up_event_sequence": details.get("event_sequence"),
                        "trusted_pointermove_event_count": len(moves),
                        "final_move_count": int(up.get("move_count") or 0),
                        "screen_client_offset": list(next(iter(offsets))),
                        "down_screen_xy": [
                            int(down.get("screen_x") or 0),
                            int(down.get("screen_y") or 0),
                        ],
                        "up_screen_xy": [
                            int(up.get("screen_x") or 0),
                            int(up.get("screen_y") or 0),
                        ],
                        "down_received_at": float(
                            down.get("received_at") or 0,
                        ),
                        "up_received_at": float(
                            up.get("received_at") or 0,
                        ),
                    })
                active = None
    return proofs, total_down_count


def _dynamic_mapping_proof(
    report: dict[str, Any],
    trace_events: list[dict[str, Any]],
) -> dict[str, Any]:
    expected_hwnd = int((report.get("desktop") or {}).get("target_hwnd") or 0)
    samples: list[dict[str, Any]] = []
    violations: list[str] = []
    for event in trace_events:
        # The model-level tool-start event is the single authoritative copy of
        # an action envelope. Batch-start events repeat the same envelope and
        # would double-count it.
        if event.get("type") != "agent_tool_start":
            continue
        envelope = event.get("action_envelope") or {}
        tool = str(envelope.get("type") or "")
        if tool not in POINTER_TOOLS:
            continue
        inputs = envelope.get("inputs") or {}
        target = envelope.get("target") or {}
        region = target.get("region") or {}
        frame = inputs.get("source_frame_coordinates") or {}
        image = inputs.get("source_image_coordinates") or {}
        size = image.get("submitted_image_size") or []
        sample_id = str(event.get("action_id") or "")
        try:
            width, height = float(size[0]), float(size[1])
            left, top = float(region["left"]), float(region["top"])
        except Exception:
            violations.append(f"{sample_id}: incomplete coordinate provenance")
            continue

        if (
            inputs.get("coordinate_space")
            != "current_frame_normalized_0_1000"
            or inputs.get("visual_frame_id") != target.get("frame_id")
            or target.get("reference_system") != "screen_physical_px"
            or target.get("source_reference_system")
            != "submitted_image_pixels"
            or int(target.get("hwnd") or 0) != expected_hwnd
            or float(region.get("width") or 0) != width
            or float(region.get("height") or 0) != height
        ):
            violations.append(f"{sample_id}: frame/HWND/reference binding mismatch")

        coordinates: list[
            tuple[str, float, float, float, float, float, float]
        ] = []
        try:
            if all(key in inputs for key in ("x", "y")):
                coordinates.append((
                    "point",
                    float(frame["frame_x"]),
                    float(frame["frame_y"]),
                    float(image["image_x"]),
                    float(image["image_y"]),
                    float(inputs["x"]),
                    float(inputs["y"]),
                ))
            elif all(key in inputs for key in ("x1", "y1", "x2", "y2")):
                for suffix in ("1", "2"):
                    coordinates.append((
                        f"endpoint_{suffix}",
                        float(frame[f"frame_x{suffix}"]),
                        float(frame[f"frame_y{suffix}"]),
                        float(image[f"image_x{suffix}"]),
                        float(image[f"image_y{suffix}"]),
                        float(inputs[f"x{suffix}"]),
                        float(inputs[f"y{suffix}"]),
                    ))
                physical_points = list(inputs.get("points") or [])
                frame_points = list(frame.get("frame_points") or [])
                image_points = list(image.get("image_points") or [])
                _require(
                    len(physical_points) == len(frame_points)
                    == len(image_points),
                    f"{sample_id}: path coordinate count mismatch",
                )
                for index, (
                    physical_point,
                    frame_point,
                    image_point,
                ) in enumerate(
                    zip(physical_points, frame_points, image_points),
                    start=1,
                ):
                    coordinates.append((
                        f"path_{index}",
                        float(frame_point[0]),
                        float(frame_point[1]),
                        float(image_point[0]),
                        float(image_point[1]),
                        float(physical_point[0]),
                        float(physical_point[1]),
                    ))
            else:
                violations.append(f"{sample_id}: no pointer coordinates")
                continue
        except (KeyError, TypeError, ValueError, IndexError) as error:
            violations.append(
                f"{sample_id}: incomplete coordinate provenance: {error}",
            )
            continue

        for (
            coordinate_name,
            frame_x,
            frame_y,
            image_x,
            image_y,
            final_x,
            final_y,
        ) in coordinates:
            if not all(math.isfinite(value) for value in (
                frame_x,
                frame_y,
                image_x,
                image_y,
                final_x,
                final_y,
            )):
                violations.append(
                    f"{sample_id}/{coordinate_name}: non-finite coordinate",
                )
                continue
            if not (
                0 <= frame_x <= 1000
                and 0 <= frame_y <= 1000
                and 0 <= image_x < width
                and 0 <= image_y < height
            ):
                violations.append(
                    f"{sample_id}/{coordinate_name}: coordinate out of range",
                )
            checks = {
                "frame_to_image_x_error_px": abs(
                    frame_x * (width - 1.0) / 1000.0 - image_x
                ),
                "frame_to_image_y_error_px": abs(
                    frame_y * (height - 1.0) / 1000.0 - image_y
                ),
                "image_to_screen_x_error_px": abs(left + image_x - final_x),
                "image_to_screen_y_error_px": abs(top + image_y - final_y),
            }
            if any(value > 1e-6 for value in checks.values()):
                violations.append(
                    f"{sample_id}/{coordinate_name}: "
                    "non-zero coordinate mapping error",
                )
            samples.append({
                "action_id": sample_id,
                "tool": tool,
                "coordinate": coordinate_name,
                **checks,
            })
    _require(samples and not violations, f"dynamic mapping proof failed: {violations}")
    return {
        "sample_count": len(samples),
        "action_ids": sorted({
            str(sample["action_id"]) for sample in samples
        }),
        "max_mapping_error_px": max(
            max(
                value
                for key, value in sample.items()
                if key.endswith("_error_px")
            )
            for sample in samples
        ),
        "frame_hwnd_reference_binding_pass": True,
        "violations": [],
    }


def _bind_continuous_attempts_to_trace(
    attempts: list[dict[str, Any]],
    trace_events: list[dict[str, Any]],
    mapped_action_ids: list[str],
) -> list[dict[str, Any]]:
    executed = [
        item
        for item in trace_events
        if item.get("type") == "action" and item.get("action") == "drag"
    ]
    starts = [
        item
        for item in trace_events
        if item.get("type") == "agent_tool_start"
        and (item.get("action_envelope") or {}).get("type") == "drag"
    ]
    mapped_ids = set(mapped_action_ids)
    used_executed: set[int] = set()
    used_starts: set[str] = set()
    bindings: list[dict[str, Any]] = []
    for attempt in attempts:
        down = list(attempt["down_screen_xy"])
        up = list(attempt["up_screen_xy"])
        up_at = float(attempt["up_received_at"])
        executed_matches = [
            (index, item)
            for index, item in enumerate(executed)
            if list(item.get("frm") or []) == down
            and list(item.get("to") or []) == up
            and abs(float(item.get("ts") or 0) - up_at) <= 0.25
            and index not in used_executed
        ]
        _require(
            len(executed_matches) == 1,
            "trusted continuous chain has no unique executed trace drag",
        )
        executed_index, executed_event = executed_matches[0]
        executed_at = float(executed_event.get("ts") or 0)
        start_matches: list[dict[str, Any]] = []
        for item in starts:
            envelope = item.get("action_envelope") or {}
            inputs = envelope.get("inputs") or {}
            action_id = str(item.get("action_id") or "")
            duration = float(inputs.get("duration") or 0)
            elapsed = executed_at - float(item.get("ts") or 0)
            if (
                [inputs.get("x1"), inputs.get("y1")] == down
                and [inputs.get("x2"), inputs.get("y2")] == up
                and 0 <= elapsed <= duration + 2.0
                and action_id in mapped_ids
                and action_id not in used_starts
            ):
                start_matches.append(item)
        _require(
            len(start_matches) == 1,
            "trusted continuous chain has no unique mapped action envelope",
        )
        start_event = start_matches[0]
        action_id = str(start_event.get("action_id") or "")
        used_executed.add(executed_index)
        used_starts.add(action_id)
        bindings.append({
            "down_event_sequence": attempt["down_event_sequence"],
            "up_event_sequence": attempt["up_event_sequence"],
            "action_id": action_id,
            "executed_trace_ts": executed_at,
            "target_pointerup_received_at": up_at,
            "trace_to_target_latency_seconds": up_at - executed_at,
            "down_screen_xy": down,
            "up_screen_xy": up,
        })
    return bindings


def _adjudicate_dynamic(
    report_path: Path,
    report: dict[str, Any],
    *,
    expected_commit: str,
    acquisition_harness: tuple[Any, Any],
) -> dict[str, Any]:
    _common_case_checks(
        report,
        expected_commit=expected_commit,
        expected_name="dynamic-hold",
        expected_scenario="dynamic_hold",
        expected_seed=EXPECTED_DYNAMIC_SEED,
        acquisition_harness=acquisition_harness,
    )
    raw_verdict = report.get("external_verdict") or {}
    _require(
        raw_verdict.get("valid") is False
        and raw_verdict.get("invalid_reasons")
        == ["external event stream did not settle"],
        "dynamic raw result is not the known failure-settlement bug",
    )
    client = report.get("client_result") or {}
    budget = report.get("budget") or {}
    timeout_seconds = float(budget.get("timeout_seconds") or 0)
    duration_seconds = float(client.get("duration_seconds") or 0)
    client_state = client.get("result") or {}
    _require(
        client_state.get("outcome") == "timeout"
        and client_state.get("status") == "timeout"
        and timeout_seconds == 420
        and timeout_seconds <= duration_seconds <= timeout_seconds + 15,
        "dynamic run did not end at the exact configured budget",
    )
    state = report.get("external_state") or {}
    _require(
        state.get("ready") is True
        and state.get("success") is False
        and not [
            item
            for item in (state.get("events") or [])
            if item.get("type") == "scenario_success"
        ],
        "dynamic saved final state is not a grounded failure",
    )
    hook = report.get("input_evidence") or {}
    _require(
        not hook.get("errors")
        and not [
            item
            for item in (hook.get("events") or [])
            if item.get("injected") is False
        ],
        "dynamic run has hook failure or non-injected host input",
    )
    event_times = [
        float(item.get("received_at") or 0)
        for item in (state.get("events") or [])
        if item.get("received_at")
    ]
    _require(
        event_times
        and float(hook.get("stopped_at") or 0) - max(event_times) >= 0.8,
        "dynamic final event stream did not become quiet before capture",
    )
    attempts, total_down_count = _dynamic_attempt_proofs(report)
    _require(attempts, "dynamic run has no trusted continuous pointer proof")
    metrics = _trace_metrics(report)
    mapping = _dynamic_mapping_proof(report, metrics["_events"])
    attempt_trace_bindings = _bind_continuous_attempts_to_trace(
        attempts,
        metrics["_events"],
        mapping["action_ids"],
    )
    duplicate_input_count = 2 * max(0, total_down_count - 1)
    extra_gesture_count = max(0, total_down_count - 1)
    result = copy.deepcopy(report)
    raw_errors = list(raw_verdict.get("errors") or [])
    verdict = copy.deepcopy(raw_verdict)
    verdict.update({
        "source": (
            "saved_trusted_pointer_events_low_level_hook_bound_trace_"
            "and_budget_cutoff"
        ),
        "valid": True,
        "oracle_status": "conclusive",
        "passed": False,
        "errors": [
            "dynamic-hold did not complete exactly once within the 420-second budget",
        ],
        "invalid_reasons": [],
        "first_failure_owner": "model_action_sequence",
        "duplicate_input_count": duplicate_input_count,
        "extra_gesture_count": extra_gesture_count,
        "continuous_pointer_mapping_proved": True,
        "qualified_continuous_attempt_count": len(attempts),
        "continuous_attempts": attempts,
        "continuous_attempt_trace_bindings": attempt_trace_bindings,
        "coordinate_mapping": mapping,
        "action_count": metrics["executed_physical_action_count"],
        "trace_action_metrics": {
            key: value for key, value in metrics.items() if key != "_events"
        },
        "legacy_reported_action_count": report.get("action_count"),
        "legacy_reported_duplicate_input_count": report.get(
            "duplicate_input_count",
        ),
    })
    adjudication = {
        "schema": "pr15-saved-artifact-case-adjudication/v1",
        "raw_report": str(report_path.resolve()),
        "raw_report_sha256": _sha256(report_path),
        "raw_oracle_status": raw_verdict.get("oracle_status"),
        "raw_invalid_reasons": raw_verdict.get("invalid_reasons"),
        "raw_errors": raw_errors,
        "known_oracle_bug": "failure_state_was_not_settled",
        "adjudicated_status": "conclusive",
        "grounded_outcome": "grounded_fail",
        "continuous_pointer_mapping_proved": True,
        "artifact_sha256": _artifact_binding(report_path, report),
    }
    result.update({
        "report": str(report_path.resolve()),
        "external_verdict": verdict,
        "legacy_reported_action_count": report.get("action_count"),
        "action_count": metrics["executed_physical_action_count"],
        "duplicate_input_count": duplicate_input_count,
        "valid": True,
        "grounded_verdict": False,
        "first_failure_owner": "model_action_sequence",
        "passed": False,
        "posthoc_adjudication": adjudication,
    })
    return result


def _adjudicate_cancel(
    report_path: Path,
    report: dict[str, Any],
    observation_path: Path,
    *,
    expected_commit: str,
    acquisition_harness: tuple[Any, Any],
    visual_harness: Any,
) -> dict[str, Any]:
    _common_case_checks(
        report,
        expected_commit=expected_commit,
        expected_name="stop-cancel",
        expected_scenario="cancel_guard",
        expected_seed=EXPECTED_CANCEL_SEED,
        acquisition_harness=acquisition_harness,
    )
    raw_verdict = report.get("external_verdict") or {}
    _require(
        raw_verdict.get("valid") is False
        and raw_verdict.get("invalid_reasons")
        == ["runtime event stream disconnected before audit finished"]
        and not raw_verdict.get("errors"),
        "cancel raw result is not the known local-close bug",
    )
    evidence = _read(observation_path)
    client_report_path = Path(
        str((report.get("client_result") or {}).get("report") or ""),
    )
    _require(
        client_report_path.is_file()
        and observation_path.name == "cancel-observation.json"
        and observation_path.parent.resolve()
        == client_report_path.parent.resolve(),
        "cancel observation is not bound to the selected client run",
    )
    report_state = report.get("external_state") or {}
    report_events = list(report_state.get("events") or [])
    checkpoint = evidence.get("checkpoint") or {}
    quiet_state = evidence.get("target_state_after_quiet") or {}
    quiet_events = list(quiet_state.get("events") or [])
    expected_hwnd = int((report.get("desktop") or {}).get("target_hwnd") or 0)
    _require(
        int(evidence.get("target_hwnd") or 0) == expected_hwnd
        and int(
            (
                (report.get("client_result") or {})
                .get("after_submit_handoff", {})
                .get("target_hwnd")
            )
            or 0
        )
        == expected_hwnd,
        "cancel observation target HWND mismatch",
    )
    _require(
        checkpoint in report_events
        and checkpoint.get("case_id") == report.get("case_id")
        and checkpoint.get("scenario") == report.get("scenario")
        and int(checkpoint.get("seed") or 0) == int(report.get("seed") or 0),
        "cancel checkpoint is not bound to the selected external state",
    )
    _require(
        quiet_state.get("case_id") == report.get("case_id")
        and quiet_state.get("scenario") == report.get("scenario")
        and int(quiet_state.get("seed") or 0) == int(report.get("seed") or 0)
        and quiet_events
        and quiet_events == report_events[:len(quiet_events)],
        "cancel quiet state is not an exact prefix of the final external state",
    )
    stream = evidence.get("runtime_event_stream") or {}
    _require(
        stream.get("errors") == [EXPECTED_CLOSE_ERROR],
        "cancel stream does not have exactly the known close-frame error",
    )
    stop = evidence.get("stop") or {}
    cutoff = float(stop.get("click_started_at") or 0)
    physical_cutoff = float(stop.get("click_finished_at") or 0)
    quiet_seconds = 125.0
    quiet_end = physical_cutoff + quiet_seconds
    observations = sorted(
        float(item.get("_observed_at") or 0)
        for item in (stream.get("events") or [])
        if physical_cutoff
        <= float(item.get("_observed_at") or 0)
        <= quiet_end + 2
    )
    gaps = [
        later - earlier
        for earlier, later in zip(observations, observations[1:])
    ]
    hook = evidence.get("quiet_input_hook") or {}
    disconnect_after_hook = (
        float(stream.get("disconnected_at") or 0)
        - float(hook.get("stopped_at") or 0)
    )
    _require(
        cutoff > 0
        and physical_cutoff >= cutoff
        and float(stream.get("connected_at") or 0) <= cutoff
        and float(hook.get("started_at") or 0) <= cutoff
        and float(evidence.get("quiet_elapsed_seconds") or 0) >= quiet_seconds
        and observations
        and observations[0] <= physical_cutoff + 2.5
        and observations[-1] >= quiet_end - 2.5
        and max(gaps, default=0.0) <= 2.5
        and float(stream.get("disconnected_at") or 0) >= quiet_end
        and 0 <= disconnect_after_hook <= 0.25,
        "cancel runtime stream did not actually span the full quiet window",
    )
    cleaned = copy.deepcopy(evidence)
    cleaned["runtime_event_stream"]["errors"] = []
    product_root = Path(
        str((report.get("product") or {}).get("product_workspace") or ""),
    )
    visual_harness.client_harness.TRACE_ROOT = (
        product_root / "python" / "winpilot_home" / "traces"
    )
    child_run_ids = [
        str(item)
        for item in (
            ((report.get("client_result") or {}).get("trace_binding") or {})
            .get("child_run_ids")
            or []
        )
    ]
    trigger_run_id = str(
        (evidence.get("inflight_model_request") or {}).get("run_id") or "",
    )
    _require(
        trigger_run_id and trigger_run_id in child_run_ids,
        "cancel in-flight trigger is not one of the bound child runs",
    )
    debug_binding = _debug_timeline_binding(product_root, child_run_ids)
    debug_by_file = {
        item["file"]: item for item in debug_binding
    }
    inflight_timeline = list(
        (evidence.get("inflight_model_request") or {}).get("timeline") or [],
    )
    _require(inflight_timeline, "cancel in-flight trigger timeline is missing")
    for item in inflight_timeline:
        bound = debug_by_file.get(str(Path(str(item.get("file") or "")).resolve()))
        _require(
            bound is not None
            and bound["index"] == int(item.get("index") or -1)
            and bound["kind"] == item.get("kind")
            and bound["mtime"] == float(item.get("mtime") or 0),
            "cancel in-flight timeline differs from the bound debug files",
        )
    verdict = visual_harness.cancel_external_verdict(
        report.get("external_state") or {},
        report.get("client_result") or {},
        cleaned,
        case_id=str(report.get("case_id") or ""),
        scenario="cancel_guard",
        seed=EXPECTED_CANCEL_SEED,
        quiet_seconds=quiet_seconds,
    )
    _require(
        debug_binding == _debug_timeline_binding(product_root, child_run_ids),
        "cancel debug timeline changed while it was adjudicated",
    )
    _require(
        verdict.get("valid") is True
        and verdict.get("passed") is True
        and not verdict.get("errors")
        and not verdict.get("invalid_reasons")
        and verdict.get("late_model_proposal_count") == 0
        and verdict.get("late_action_start_count") == 0
        and verdict.get("post_cancel_injected_input_count") == 0
        and verdict.get("post_cancel_human_input_count") == 0,
        "saved cancel evidence does not prove a full cancellation barrier",
    )
    metrics = _trace_metrics(report)
    verdict.update({
        "source": (
            "saved_external_browser_hook_live_runtime_stream_bound_trace_"
            "and_known_local_close"
        ),
        "action_count": metrics["executed_physical_action_count"],
        "trace_action_metrics": {
            key: value for key, value in metrics.items() if key != "_events"
        },
        "late_provider_response_semantics": (
            "arrival_allowed_but_not_accepted_published_or_consumed"
        ),
    })
    adjudication = {
        "schema": "pr15-saved-artifact-case-adjudication/v1",
        "raw_report": str(report_path.resolve()),
        "raw_report_sha256": _sha256(report_path),
        "cancel_observation": str(observation_path.resolve()),
        "cancel_observation_sha256": _sha256(observation_path),
        "raw_oracle_status": raw_verdict.get("oracle_status"),
        "raw_invalid_reasons": raw_verdict.get("invalid_reasons"),
        "known_oracle_bug": "harness_local_websocket_close_frame",
        "ignored_error": EXPECTED_CLOSE_ERROR,
        "disconnect_after_quiet_end_seconds": (
            float(stream.get("disconnected_at") or 0) - quiet_end
        ),
        "disconnect_after_hook_stop_seconds": disconnect_after_hook,
        "last_stream_observation_after_quiet_end_seconds": (
            observations[-1] - quiet_end
        ),
        "debug_timeline_binding": debug_binding,
        "adjudicated_status": "conclusive",
        "grounded_outcome": "grounded_pass",
        "artifact_sha256": _artifact_binding(
            report_path,
            report,
            extra_paths=(
                client_report_path,
                observation_path,
                Path(str(evidence.get("desktop_after_quiet") or "")),
                Path(
                    str(
                        (evidence.get("target_after_quiet") or {}).get(
                            "path",
                        )
                        or "",
                    ),
                ),
                *(
                    Path(str(item["file"]))
                    for item in debug_binding
                ),
            ),
        ),
    }
    result = copy.deepcopy(report)
    result.update({
        "report": str(report_path.resolve()),
        "external_verdict": verdict,
        "legacy_reported_action_count": report.get("action_count"),
        "action_count": metrics["executed_physical_action_count"],
        "duplicate_input_count": verdict.get("duplicate_input_count"),
        "valid": True,
        "grounded_verdict": True,
        "first_failure_owner": None,
        "passed": True,
        "posthoc_adjudication": adjudication,
    })
    return result


def build_adjudicated_suite(
    *,
    suite_path: Path,
    expected_product_sha: str,
    dynamic_rerun_path: Path,
    cancel_rerun_path: Path,
    cancel_observation_path: Path,
) -> dict[str, Any]:
    suite = _read(suite_path)
    cases = _case_map(suite)
    product = suite.get("product") or {}
    _require(
        product.get("product_revision") == expected_product_sha
        and product.get("tracked_worktree_clean") is True,
        "suite product identity/cleanliness mismatch",
    )
    acquisition_harness = _harness_identity(suite)
    _require(
        acquisition_harness[0] and acquisition_harness[1],
        "suite acquisition harness identity is missing",
    )
    visual_harness = _load_visual_harness()
    text_report_path = Path(
        str(cases["text-hotkey"].get("report") or ""),
    )
    text_report = _read(text_report_path)
    embedded_text = copy.deepcopy(cases["text-hotkey"])
    embedded_text.pop("report", None)
    _require(
        embedded_text == text_report,
        "text-hotkey suite entry differs from its hash-bound child report",
    )
    text_report["report"] = str(text_report_path.resolve())
    text = _adjudicate_text(
        text_report_path,
        text_report,
        expected_commit=expected_product_sha,
        acquisition_harness=acquisition_harness,
        visual_harness=visual_harness,
    )
    dynamic_raw = _read(dynamic_rerun_path)
    _rerun_matches_reference(
        dynamic_raw,
        cases["dynamic-hold"],
        case_name="dynamic-hold",
    )
    dynamic = _adjudicate_dynamic(
        dynamic_rerun_path,
        dynamic_raw,
        expected_commit=expected_product_sha,
        acquisition_harness=acquisition_harness,
    )
    cancel_raw = _read(cancel_rerun_path)
    _rerun_matches_reference(
        cancel_raw,
        cases["stop-cancel"],
        case_name="stop-cancel",
    )
    cancel = _adjudicate_cancel(
        cancel_rerun_path,
        cancel_raw,
        cancel_observation_path,
        expected_commit=expected_product_sha,
        acquisition_harness=acquisition_harness,
        visual_harness=visual_harness,
    )
    replacements = {
        "text-hotkey": text,
        "dynamic-hold": dynamic,
        "stop-cancel": cancel,
    }
    result = copy.deepcopy(suite)
    result["results"] = [
        _normalize_case_action_count(
            replacements.get(str(item.get("name") or ""), item),
        )
        for item in (suite.get("results") or [])
    ]
    script_sha = _sha256(Path(__file__).resolve())
    dependency_sha = {
        str(path.relative_to(WORKSPACE)): _sha256(path)
        for path in (
            Path(__file__).resolve(),
            VISUAL_HARNESS,
            CLIENT_HARNESS,
        )
    }
    result["posthoc_adjudication"] = {
        "schema": "pr15-saved-artifact-suite-adjudication/v1",
        "source_suite": str(suite_path.resolve()),
        "source_suite_sha256": _sha256(suite_path),
        "acquisition_harness_revision": acquisition_harness[0],
        "acquisition_harness_artifact_sha256": acquisition_harness[1],
        "oracle_script": str(Path(__file__).resolve()),
        "oracle_script_sha256": script_sha,
        "adjudication_dependency_sha256": dependency_sha,
        "original_invalid_runs_preserved": {
            name: str((cases[name] or {}).get("report") or "")
            for name in ("dynamic-hold", "stop-cancel")
        },
        "selected_same_seed_reruns": {
            "dynamic-hold": str(dynamic_rerun_path.resolve()),
            "stop-cancel": str(cancel_rerun_path.resolve()),
        },
        "adjudicated_cases": {
            name: {
                "valid": replacements[name].get("valid"),
                "grounded_verdict": replacements[name].get(
                    "grounded_verdict",
                ),
                "posthoc_adjudication": replacements[name].get(
                    "posthoc_adjudication",
                ),
            }
            for name in replacements
        },
    }
    result["case_count"] = len(result["results"])
    result["invalid_count"] = sum(
        item.get("valid") is not True for item in result["results"]
    )
    result["passed_count"] = sum(
        item.get("grounded_verdict") is True for item in result["results"]
    )
    result["valid"] = result["invalid_count"] == 0
    result["passed"] = result["passed_count"] == result["case_count"]
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--expected-product-sha", required=True)
    parser.add_argument("--dynamic-rerun", type=Path, required=True)
    parser.add_argument("--cancel-rerun", type=Path, required=True)
    parser.add_argument("--cancel-observation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_adjudicated_suite(
        suite_path=args.suite.resolve(),
        expected_product_sha=str(args.expected_product_sha),
        dynamic_rerun_path=args.dynamic_rerun.resolve(),
        cancel_rerun_path=args.cancel_rerun.resolve(),
        cancel_observation_path=args.cancel_observation.resolve(),
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "report": str(output),
        "product_revision": (report.get("product") or {}).get(
            "product_revision",
        ),
        "valid": report.get("valid"),
        "grounded_cases": {
            item.get("name"): item.get("grounded_verdict")
            for item in (report.get("results") or [])
        },
    }, ensure_ascii=False))
    return 0 if report.get("valid") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
