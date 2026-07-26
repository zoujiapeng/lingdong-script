from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest


def _load_oracle():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "pr15_legacy_saved_artifact_oracle.py"
    )
    spec = importlib.util.spec_from_file_location(
        "pr15_legacy_saved_artifact_oracle",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_paired_report():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "pr15_legacy_paired_report.py"
    )
    spec = importlib.util.spec_from_file_location(
        "pr15_legacy_paired_report",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _target() -> dict:
    return {
        "hwnd": 42,
        "reference_system": "screen_physical_px",
        "source_reference_system": "submitted_image_pixels",
        "frame_id": 7,
        "epoch": 3,
        "region": {
            "left": 100,
            "top": 200,
            "width": 101,
            "height": 51,
        },
    }


def test_dynamic_mapping_proof_checks_single_endpoints_and_path_points():
    oracle = _load_oracle()
    single = {
        "type": "agent_tool_start",
        "action_id": "single",
        "action_envelope": {
            "type": "mouse_down",
            "inputs": {
                "x": 150,
                "y": 220,
                "coordinate_space": "current_frame_normalized_0_1000",
                "visual_frame_id": 7,
                "source_frame_coordinates": {
                    "frame_x": 500.0,
                    "frame_y": 400.0,
                },
                "source_image_coordinates": {
                    "image_x": 50.0,
                    "image_y": 20.0,
                    "submitted_image_size": [101, 51],
                },
            },
            "target": _target(),
        },
    }
    drag = {
        "type": "agent_tool_start",
        "action_id": "drag",
        "action_envelope": {
            "type": "drag",
            "inputs": {
                "x1": 100,
                "y1": 200,
                "x2": 200,
                "y2": 250,
                "points": [[150, 220]],
                "coordinate_space": "current_frame_normalized_0_1000",
                "visual_frame_id": 7,
                "source_frame_coordinates": {
                    "frame_x1": 0.0,
                    "frame_y1": 0.0,
                    "frame_x2": 1000.0,
                    "frame_y2": 1000.0,
                    "frame_points": [[500.0, 400.0]],
                },
                "source_image_coordinates": {
                    "image_x1": 0.0,
                    "image_y1": 0.0,
                    "image_x2": 100.0,
                    "image_y2": 50.0,
                    "image_points": [[50.0, 20.0]],
                    "submitted_image_size": [101, 51],
                },
            },
            "target": _target(),
        },
    }

    proof = oracle._dynamic_mapping_proof(
        {"desktop": {"target_hwnd": 42}},
        [single, drag, {
            **drag,
            "type": "agent_batch_action_start",
        }],
    )

    assert proof == {
        "sample_count": 4,
        "action_ids": ["drag", "single"],
        "max_mapping_error_px": 0.0,
        "frame_hwnd_reference_binding_pass": True,
        "violations": [],
    }

    mismatched = copy.deepcopy(single)
    mismatched["action_envelope"]["inputs"]["visual_frame_id"] = 8
    with pytest.raises(
        ValueError,
        match="frame/HWND/reference binding mismatch",
    ):
        oracle._dynamic_mapping_proof(
            {"desktop": {"target_hwnd": 42}},
            [mismatched],
        )


def _pointer_event(
    event_type: str,
    sequence: int,
    received_at: float,
    *,
    screen_x: int,
    screen_y: int,
) -> dict:
    details = {"event_sequence": sequence, "down_count": 1}
    if event_type == "pointerup":
        details.update({
            "checkpoint_reached": True,
            "goal_revealed_after_down": True,
            "inside_goal": True,
            "completion_event": "pointerup",
        })
    return {
        "case_id": "dynamic-case",
        "scenario": "dynamic_hold",
        "seed": 161803,
        "type": event_type,
        "trusted": True,
        "client_x": screen_x - 100,
        "client_y": screen_y - 200,
        "screen_x": screen_x,
        "screen_y": screen_y,
        "move_count": max(0, sequence - 2),
        "details": details,
        "received_at": received_at,
    }


def _dynamic_session(start: float) -> list[dict]:
    ready = {
        "case_id": "dynamic-case",
        "scenario": "dynamic_hold",
        "seed": 161803,
        "type": "page_ready",
        "trusted": False,
        "details": {"event_sequence": 1},
        "received_at": start,
    }
    events = [
        ready,
        _pointer_event(
            "pointerdown",
            2,
            start + 0.1,
            screen_x=110,
            screen_y=210,
        ),
        _pointer_event(
            "pointermove",
            3,
            start + 0.2,
            screen_x=120,
            screen_y=220,
        ),
        _pointer_event(
            "pointermove",
            4,
            start + 0.3,
            screen_x=130,
            screen_y=230,
        ),
        _pointer_event(
            "checkpoint_reached",
            5,
            start + 0.4,
            screen_x=140,
            screen_y=240,
        ),
        _pointer_event(
            "pointerup",
            6,
            start + 0.5,
            screen_x=150,
            screen_y=250,
        ),
    ]
    events[-1]["move_count"] = 3
    return events


def test_dynamic_attempt_count_survives_page_counter_reset():
    oracle = _load_oracle()
    first = _dynamic_session(10.0)
    second = _dynamic_session(20.0)
    hooks = []
    for session in (first, second):
        for event_type, message in (
            ("pointerdown", "left_down"),
            ("pointerup", "left_up"),
        ):
            event = next(
                item for item in session if item["type"] == event_type
            )
            hooks.append({
                "kind": "mouse",
                "message": message,
                "injected": True,
                "x": event["screen_x"],
                "y": event["screen_y"],
                "at": event["received_at"],
            })

    attempts, total_down_count = oracle._dynamic_attempt_proofs({
        "case_id": "dynamic-case",
        "external_state": {"events": first + second},
        "input_evidence": {"events": hooks},
    })

    assert len(attempts) == 2
    assert total_down_count == 2


def test_continuous_attempt_binds_to_one_mapped_executed_drag():
    oracle = _load_oracle()
    attempts = [{
        "down_event_sequence": 2,
        "up_event_sequence": 6,
        "down_screen_xy": [100, 200],
        "up_screen_xy": [200, 250],
        "up_received_at": 10.5,
    }]
    trace_events = [
        {
            "type": "agent_tool_start",
            "ts": 9.0,
            "action_id": "drag-1",
            "action_envelope": {
                "type": "drag",
                "inputs": {
                    "x1": 100,
                    "y1": 200,
                    "x2": 200,
                    "y2": 250,
                    "duration": 1.0,
                },
            },
        },
        {
            "type": "action",
            "ts": 10.45,
            "action": "drag",
            "frm": [100, 200],
            "to": [200, 250],
        },
    ]

    bindings = oracle._bind_continuous_attempts_to_trace(
        attempts,
        trace_events,
        ["drag-1"],
    )

    assert bindings[0]["action_id"] == "drag-1"
    assert bindings[0]["trace_to_target_latency_seconds"] == pytest.approx(0.05)

    with pytest.raises(ValueError, match="no unique mapped action envelope"):
        oracle._bind_continuous_attempts_to_trace(
            attempts,
            trace_events,
            [],
        )


def test_paired_report_counts_only_executed_physical_trace_actions(tmp_path):
    paired = _load_paired_report()
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        "\n".join([
            '{"type":"agent_tool_start","action_envelope":{"type":"click"}}',
            '{"type":"agent_batch_action_start",'
            '"action_envelope":{"type":"click"}}',
            '{"type":"action","action":"click"}',
            '{"type":"action","action":"scroll"}',
            '{"type":"action","action":"wait"}',
        ]),
        encoding="utf-8",
    )

    metrics = paired._trace_action_metrics(str(trace))

    assert metrics == {
        "executed_physical_action_count": 2,
        "executed_physical_actions": ["click", "scroll"],
        "proposed_physical_action_attempt_count": 1,
        "batch_physical_action_start_count": 1,
        "parse_errors": [],
    }
