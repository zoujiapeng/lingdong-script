from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path


def _load_harness():
    path = Path(__file__).resolve().parents[3] / "scripts" / "client_visual_e2e_run.py"
    spec = importlib.util.spec_from_file_location("client_visual_e2e_run", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _state(scenario: str, details: dict, *, moves: int) -> dict:
    ready = {
        "case_id": "case-1",
        "scenario": scenario,
        "seed": 17,
        "type": "page_ready",
        "trusted": False,
        "succeeded": False,
        "details": {"succeeded": False, "event_sequence": 1},
    }
    success = {
        "case_id": "case-1",
        "scenario": scenario,
        "seed": 17,
        "type": "scenario_success",
        "trusted": True,
        "pointer_type": "mouse",
        "move_count": moves,
        "succeeded": True,
        "received_at": 20.0,
        "details": {"succeeded": True, **details, "event_sequence": 2},
    }
    return {
        "ready": True,
        "success": True,
        "case_id": "case-1",
        "scenario": scenario,
        "seed": 17,
        "events": [ready, success],
        "success_event": success,
    }


def _event(
    scenario: str,
    event_type: str,
    sequence: int,
    *,
    trusted: bool = True,
    succeeded: bool = False,
    details: dict | None = None,
) -> dict:
    return {
        "case_id": "case-1",
        "scenario": scenario,
        "seed": 17,
        "type": event_type,
        "trusted": trusted,
        "succeeded": succeeded,
        "details": {
            **(details or {}),
            "event_sequence": sequence,
        },
    }


def test_real_client_visual_matrix_is_generic_and_covers_gesture_shapes():
    harness = _load_harness()

    assert harness.SUITES["smoke"] == (
        "drag-fit", "slider", "dynamic-hold", "game-drag-v2",
    )
    assert {
        item["scenario"] for item in harness.VISUAL_CASES.values()
    } == {
        "drag_fit", "slider", "corridor_drag", "dynamic_hold",
        "click_double_click", "bidirectional_scroll", "text_hotkey",
        "cancel_guard",
    }
    assert harness.SUITES["pr15-legacy"] == (
        "click-double-click",
        "scroll-bidirectional",
        "text-hotkey",
        "dynamic-hold",
        "stop-cancel",
    )
    assert {
        harness.VISUAL_CASES[name]["expected_variant"]
        for name in ("corridor-v0", "corridor-v1", "game-drag-v2", "corridor-v3")
    } == {0, 1, 2, 3}
    assert {
        item["task"] for item in harness.VISUAL_CASES.values()
    } == {"完成当前界面上的测试。"}
    leaked_recipe = " ".join(
        item["task"] for item in harness.VISUAL_CASES.values()
    ).casefold()
    assert all(
        token not in leaked_recipe
        for token in (
            "hold", "checkpoint", "goal", "圆盘", "滑块", "拖", "按住", "释放",
        )
    )
    assert "after_submit" in inspect.signature(
        harness.client_harness.run_case,
    ).parameters
    assert "before_submit" in inspect.signature(
        harness.client_harness.run_case,
    ).parameters
    assert "seed" in inspect.signature(harness.run_visual_case).parameters
    assert "植物大战僵尸" not in " ".join(
        item["task"] for item in harness.VISUAL_CASES.values()
    )


def test_external_verdict_requires_complete_object_placement():
    harness = _load_harness()
    state = _state(
        "drag_fit",
        {
            "token": {
                "left": 30, "top": 30, "right": 60, "bottom": 60,
                "width": 30, "height": 30,
            },
            "target": {
                "left": 20, "top": 20, "right": 70, "bottom": 70,
                "width": 50, "height": 50,
            },
        },
        moves=4,
    )

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="drag_fit", seed=17,
    )

    assert verdict["passed"] is True
    state["success_event"]["details"]["token"]["right"] = 80
    failed = harness.external_verdict(
        state, case_id="case-1", scenario="drag_fit", seed=17,
    )
    assert failed["passed"] is False
    assert "object is not completely inside target" in failed["errors"]


def test_external_verdict_checks_slider_band_and_corridor_path():
    harness = _load_harness()
    slider = _state(
        "slider",
        {"center_x": 55, "goal": {"left": 45, "right": 65}},
        moves=3,
    )
    assert harness.external_verdict(
        slider, case_id="case-1", scenario="slider", seed=17,
    )["passed"] is True

    corridor = _state(
        "corridor_drag",
        {
            "path_valid": True,
            "min_clearance": 7,
            "path_points": [[1, 1], [2, 2], [3, 3], [4, 4]],
        },
        moves=8,
    )
    corridor["events"][0]["details"]["variant"] = 2
    assert harness.external_verdict(
        corridor,
        case_id="case-1",
        scenario="corridor_drag",
        seed=17,
        expected_variant=2,
    )["passed"] is True

    corridor["success_event"]["details"]["path_valid"] = False
    failed = harness.external_verdict(
        corridor,
        case_id="case-1",
        scenario="corridor_drag",
        seed=17,
        expected_variant=2,
    )
    assert failed["passed"] is False
    assert "gesture left the visible corridor" in failed["errors"]


def test_corridor_variant_zero_and_zero_clearance_are_valid():
    harness = _load_harness()
    corridor = _state(
        "corridor_drag",
        {
            "path_valid": True,
            "min_clearance": 0,
            "path_points": [[1, 1], [2, 2], [3, 3], [4, 4]],
        },
        moves=8,
    )
    corridor["events"][0]["details"]["variant"] = 0

    verdict = harness.external_verdict(
        corridor,
        case_id="case-1",
        scenario="corridor_drag",
        seed=17,
        expected_variant=0,
    )

    assert verdict["passed"] is True


def test_external_verdict_requires_one_cross_frame_held_pointer_gesture():
    harness = _load_harness()
    dynamic = _state(
        "dynamic_hold",
        {
            "down_count": 1,
            "checkpoint_reached": True,
            "goal_revealed_after_down": True,
            "inside_goal": True,
            "completion_event": "pointerup",
        },
        moves=3,
    )
    dynamic["events"][1:1] = [
        _event(
            "dynamic_hold",
            "pointerdown",
            2,
            details={"down_count": 1},
        ),
        _event(
            "dynamic_hold",
            "checkpoint_reached",
            3,
            details={"checkpoint_reached": True},
        ),
        _event(
            "dynamic_hold",
            "pointerup",
            4,
            succeeded=True,
            details={
                "succeeded": True,
                "down_count": 1,
                "checkpoint_reached": True,
            "goal_revealed_after_down": True,
            "inside_goal": True,
                "completion_event": "pointerup",
            },
        ),
    ]
    dynamic["success_event"]["details"]["event_sequence"] = 5

    verdict = harness.external_verdict(
        dynamic,
        case_id="case-1",
        scenario="dynamic_hold",
        seed=17,
    )

    assert verdict["passed"] is True
    dynamic["events"].insert(
        1,
        _event(
            "dynamic_hold",
            "pointerdown",
            1,
            details={"down_count": 2},
        ),
    )
    failed = harness.external_verdict(
        dynamic,
        case_id="case-1",
        scenario="dynamic_hold",
        seed=17,
    )
    assert failed["passed"] is False
    assert "dynamic gesture was not completed with exactly one held press" in failed["errors"]


def test_external_verdict_rejects_goal_entry_without_trusted_pointerup():
    harness = _load_harness()
    dynamic = _state(
        "dynamic_hold",
        {
            "down_count": 1,
            "checkpoint_reached": True,
            "goal_revealed_after_down": True,
            "inside_goal": True,
            "completion_event": "pointerup",
        },
        moves=5,
    )
    dynamic["events"][1:1] = [
        _event(
            "dynamic_hold",
            "pointerdown",
            2,
            details={"down_count": 1},
        ),
        _event(
            "dynamic_hold",
            "checkpoint_reached",
            3,
            details={"checkpoint_reached": True},
        ),
    ]
    dynamic["success_event"]["details"]["event_sequence"] = 4

    verdict = harness.external_verdict(
        dynamic,
        case_id="case-1",
        scenario="dynamic_hold",
        seed=17,
    )

    assert verdict["passed"] is False
    assert "dynamic gesture has no unique trusted pointerup evidence" in verdict["errors"]


def test_dynamic_failure_without_success_has_no_fake_after_terminal_input():
    harness = _load_harness()
    state = {
        "ready": True,
        "success": False,
        "case_id": "case-1",
        "scenario": "dynamic_hold",
        "seed": 17,
        "events": [
            _event("dynamic_hold", "page_ready", 1, trusted=False),
            _event(
                "dynamic_hold",
                "pointerdown",
                2,
                details={"down_count": 1},
            ),
            _event(
                "dynamic_hold",
                "pointermove",
                3,
                details={"checkpoint_reached": False},
            ),
            _event(
                "dynamic_hold",
                "pointerup",
                4,
                details={
                    "down_count": 1,
                    "checkpoint_reached": False,
                    "goal_revealed_after_down": False,
                    "inside_goal": False,
                    "completion_event": "pointerup",
                },
            ),
        ],
    }

    verdict = harness.external_verdict(
        state,
        case_id="case-1",
        scenario="dynamic_hold",
        seed=17,
    )

    assert verdict["passed"] is False
    assert "no trusted scenario_success event" in verdict["errors"]
    assert "pointer input arrived after dynamic-hold success" not in verdict["errors"]


def test_success_oracle_rejects_missing_terminal_event_sequence():
    harness = _load_harness()
    state = _state(
        "drag_fit",
        {
            "pointerdown_count": 1,
            "pointerup_count": 1,
            "completion_event": "pointerup",
            "started_in_source": True,
            "inside_goal": True,
            "source_target": "source",
            "drop_target": "goal",
        },
        moves=3,
    )
    state["success_event"]["details"].pop("event_sequence")

    verdict = harness.external_verdict(
        state,
        case_id="case-1",
        scenario="drag_fit",
        seed=17,
    )

    assert "terminal success event sequence is missing" in verdict["errors"]


def test_dynamic_target_persists_pointerup_before_success():
    target = (
        Path(__file__).resolve().parents[1]
        / "eval_targets"
        / "visual_interaction_target.html"
    ).read_text(encoding="utf-8")
    dynamic_setup = target[target.index("function setupDynamicHold()") :]

    assert dynamic_setup.index("await report('pointerup',event,details)") < dynamic_setup.index(
        "markSuccess('DYNAMIC_HOLD_SUCCEEDED'",
    )


def test_external_verdict_rejects_agent_claim_without_trusted_pointer_event():
    harness = _load_harness()
    state = _state(
        "slider",
        {"center_x": 55, "goal": {"left": 45, "right": 65}},
        moves=3,
    )
    state["success_event"]["trusted"] = False

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="slider", seed=17,
    )

    assert verdict["passed"] is False
    assert "success event is not trusted and successful" in verdict["errors"]


def _with_coords(event: dict, x: int, y: int) -> dict:
    event["client_x"] = x
    event["client_y"] = y
    return event


def test_click_double_click_oracle_requires_exact_targets_coords_and_counts():
    harness = _load_harness()
    state = _state(
        "click_double_click",
        {
            "target_id": "double-target",
            "single_click_count": 1,
            "double_click_event_count": 2,
            "dblclick_count": 1,
            "wrong_click_count": 0,
        },
        moves=0,
    )
    single = {
        "left": 10, "top": 10, "right": 110, "bottom": 70,
        "width": 100, "height": 60,
    }
    double = {
        "left": 210, "top": 10, "right": 310, "bottom": 70,
        "width": 100, "height": 60,
    }
    state["events"][0]["details"].update({
        "single_target": single,
        "double_target": double,
    })
    chain = [
        ("pointerdown", "single-target", 50, 40),
        ("pointerup", "single-target", 50, 40),
        ("click", "single-target", 50, 40),
        ("pointerdown", "double-target", 250, 40),
        ("pointerup", "double-target", 250, 40),
        ("click", "double-target", 250, 40),
        ("pointerdown", "double-target", 250, 40),
        ("pointerup", "double-target", 250, 40),
        ("click", "double-target", 250, 40),
        ("dblclick", "double-target", 250, 40),
    ]
    causal = [
        _with_coords(
            _event(
                "click_double_click",
                event_type,
                index,
                details={"target_id": target_id},
            ),
            x,
            y,
        )
        for index, (event_type, target_id, x, y) in enumerate(chain, start=2)
    ]
    state["events"][1:1] = causal
    success = state["success_event"]
    success["details"]["event_sequence"] = 12
    success["client_x"] = 250
    success["client_y"] = 40

    verdict = harness.external_verdict(
        state,
        case_id="case-1",
        scenario="click_double_click",
        seed=17,
    )

    assert verdict["passed"] is True
    assert verdict["duplicate_input_count"] == 0
    duplicate = _with_coords(
        _event(
            "click_double_click",
            "click",
            13,
            details={"target_id": "double-target"},
        ),
        250,
        40,
    )
    state["events"].append(duplicate)
    failed = harness.external_verdict(
        state,
        case_id="case-1",
        scenario="click_double_click",
        seed=17,
    )
    assert failed["passed"] is False
    assert failed["duplicate_input_count"] > 0


def test_bidirectional_scroll_oracle_requires_wheel_sign_and_scrolltop_change():
    harness = _load_harness()
    state = _state(
        "bidirectional_scroll",
        {
            "target_id": "scroll-pane",
            "down_wheel_count": 1,
            "up_wheel_count": 1,
            "observed_max_scroll_top": 900,
            "final_scroll_top": 0,
            "scroll_top_changed": True,
        },
        moves=0,
    )
    pane = {
        "left": 100, "top": 50, "right": 500, "bottom": 350,
        "width": 400, "height": 300,
    }
    state["events"][0]["details"].update({
        "pane": pane,
        "start_scroll_top": 300,
    })
    down = _with_coords(
        _event(
            "bidirectional_scroll",
            "wheel",
            2,
            details={
                "target_id": "scroll-pane",
                "delta_y": 638,
                "direction": "down",
            },
        ),
        250,
        180,
    )
    checkpoint = _event(
        "bidirectional_scroll",
        "scroll_checkpoint",
        3,
        details={"checkpoint": "bottom"},
    )
    bottom_state = _event(
        "bidirectional_scroll",
        "scroll_state",
        4,
        details={"scroll_top": 900},
    )
    up = _with_coords(
        _event(
            "bidirectional_scroll",
            "wheel",
            5,
            details={
                "target_id": "scroll-pane",
                "delta_y": -900,
                "direction": "up",
            },
        ),
        250,
        180,
    )
    top_state = _event(
        "bidirectional_scroll",
        "scroll_state",
        6,
        details={"scroll_top": 0},
    )
    state["events"][1:1] = [down, checkpoint, bottom_state, up, top_state]
    state["success_event"]["details"]["event_sequence"] = 7

    verdict = harness.external_verdict(
        state,
        case_id="case-1",
        scenario="bidirectional_scroll",
        seed=17,
    )

    assert verdict["passed"] is True
    up["details"]["delta_y"] = 100
    failed = harness.external_verdict(
        state,
        case_id="case-1",
        scenario="bidirectional_scroll",
        seed=17,
    )
    assert failed["passed"] is False
    assert "wheel directions are not a single down-then-up transaction" in failed["errors"]


def test_text_hotkey_oracle_rebuilds_exact_multiline_value_and_release():
    harness = _load_harness()
    expected = "A\nB"
    state = _state(
        "text_hotkey",
        {
            "final_text": expected,
            "value": expected,
            "modifier_released": True,
            "hotkey_down_count": 1,
            "hotkey_enter_up_count": 1,
            "control_release_count": 1,
            "duplicate_input_count": 0,
        },
        moves=0,
    )
    state["events"][0]["details"]["expected_text"] = expected
    sequence = 2
    events = []
    for value, input_type in (
        ("A", "insertText"),
        ("A\n", "insertLineBreak"),
        ("A\nB", "insertText"),
    ):
        events.append(_event(
            "text_hotkey",
            "beforeinput",
            sequence,
            details={"target_id": "exact-editor", "input_type": input_type},
        ))
        sequence += 1
        events.append(_event(
            "text_hotkey",
            "input",
            sequence,
            details={
                "target_id": "exact-editor",
                "input_type": input_type,
                "value_after": value,
            },
        ))
        sequence += 1
    for event_type, key, ctrl in (
        ("keydown", "Control", True),
        ("hotkey_keydown", "Enter", True),
        ("hotkey_enter_keyup", "Enter", True),
        ("modifier_keyup", "Control", False),
    ):
        events.append(_event(
            "text_hotkey",
            event_type,
            sequence,
            details={
                "target_id": "exact-editor",
                "key": key,
                "ctrl_key": ctrl,
                "shift_key": False,
                "alt_key": False,
                "meta_key": False,
                "repeat": False,
            },
        ))
        sequence += 1
    state["events"][1:1] = events
    state["success_event"]["details"].update({
        "event_sequence": sequence,
        # The terminal snapshot carries the last key state but is not itself
        # another keyboard event.
        "key": "Control",
        "ctrl_key": False,
        "target_id": "exact-editor",
    })

    verdict = harness.external_verdict(
        state,
        case_id="case-1",
        scenario="text_hotkey",
        seed=17,
    )

    assert verdict["passed"] is True
    state["success_event"]["details"]["final_text"] = "A\nBB"
    failed = harness.external_verdict(
        state,
        case_id="case-1",
        scenario="text_hotkey",
        seed=17,
    )
    assert failed["passed"] is False


def test_cancel_oracle_rejects_late_proposal_and_accepts_continuous_quiet_window(
    monkeypatch,
):
    harness = _load_harness()
    cutoff = 100.0
    physical_cutoff = 100.2
    events = [
        _event("cancel_guard", "page_ready", 1, trusted=False),
        {
            **_event(
                "cancel_guard",
                "cancel_checkpoint",
                2,
                details={
                    "target_id": "cancel-start",
                    "start_click_count": 1,
                },
            ),
            "received_at": 99.0,
        },
    ]
    for index in range(1, 82):
        events.append({
            **_event(
                "cancel_guard",
                "target_heartbeat",
                index + 2,
                trusted=False,
            ),
            "received_at": physical_cutoff + index,
        })
    state = {
        "ready": True,
        "success": False,
        "case_id": "case-1",
        "scenario": "cancel_guard",
        "seed": 17,
        "events": events,
    }
    stream_events = [
        {
            "type": "agent_decision",
            "ts": 101.0,
            "ok": False,
            "canceled": True,
            "_observed_at": 101.0,
        },
        *[
            {"type": "ping", "_observed_at": physical_cutoff + index}
            for index in range(1, 82)
        ],
    ]
    evidence = {
        "stop": {
            "requested_stop": True,
            "run_button_restored": True,
            "runtime_stopped": True,
            "click_started_at": cutoff,
            "click_finished_at": physical_cutoff,
            "confirmed_at": 101.0,
            "stop_control": {
                "name": "停止",
                "control_type": "ButtonControl",
                "visible": True,
                "rectangle": {
                    "left": 10, "top": 10, "right": 80, "bottom": 40,
                },
            },
        },
        "inflight_model_request": {"observed": True},
        "quiet_input_hook": {
            "started_at": 90.0,
            "stopped_at": 181.0,
            "events": [
                {
                    "at": 100.1,
                    "kind": "mouse",
                    "message": "left_down",
                    "x": 40,
                    "y": 20,
                    "injected": True,
                },
                {
                    "at": 100.15,
                    "kind": "mouse",
                    "message": "left_up",
                    "x": 40,
                    "y": 20,
                    "injected": True,
                },
            ],
            "errors": [],
        },
        "foreground_samples": [{"at": 101.0, "hwnd": 22}],
        "expected_foreground_hwnd": 22,
        "quiet_elapsed_seconds": 80.1,
        "target_after_quiet": {
            "print_window_succeeded": True,
            "visually_nonblank": True,
        },
        "modifier_state_after_quiet": {},
        "runtime_event_stream": {"events": stream_events, "errors": []},
    }
    client_result = {
        "result": {"outcome": "terminal", "status": "canceled"},
        "product": {"request_total_timeout_s": 75},
        "trace_binding": {"child_run_ids": ["child-1"]},
    }
    monkeypatch.setattr(
        harness,
        "_debug_timeline",
        lambda _run_id: [
            {"kind": "llm_abandoned", "mtime": 101.0, "index": 3},
        ],
    )

    verdict = harness.cancel_external_verdict(
        state,
        client_result,
        evidence,
        case_id="case-1",
        scenario="cancel_guard",
        seed=17,
        quiet_seconds=80.0,
    )

    assert verdict["passed"] is True
    evidence["quiet_input_hook"]["events"].append({
        "at": 100.18,
        "kind": "keyboard",
        "message": "key_down",
        "vk_code": 65,
        "injected": True,
    })
    late_input = harness.cancel_external_verdict(
        state,
        client_result,
        evidence,
        case_id="case-1",
        scenario="cancel_guard",
        seed=17,
        quiet_seconds=80.0,
    )
    assert late_input["passed"] is False
    assert late_input["post_cancel_injected_input_count"] == 1
    evidence["quiet_input_hook"]["events"].pop()
    evidence["runtime_event_stream"]["events"].append({
        "type": "agent_decision",
        "ts": 102.0,
        "ok": True,
        "canceled": False,
        "_observed_at": 102.0,
    })
    failed = harness.cancel_external_verdict(
        state,
        client_result,
        evidence,
        case_id="case-1",
        scenario="cancel_guard",
        seed=17,
        quiet_seconds=80.0,
    )
    assert failed["passed"] is False
    assert failed["late_model_proposal_count"] == 1


def test_runtime_event_stream_does_not_treat_local_empty_close_as_disconnect():
    harness = _load_harness()
    stream = harness.RuntimeEventStream("http://127.0.0.1:7880", "token")

    class LocallyClosedSocket:
        def recv(self):
            stream._stop.set()
            return ""

    stream._socket = LocallyClosedSocket()
    stream._read_loop()

    assert stream.errors == []
    assert stream.closed_by_harness is True
    assert stream.disconnected_at > 0

    exception_stream = harness.RuntimeEventStream(
        "http://127.0.0.1:7880",
        "token",
    )

    class LocallyClosedExceptionSocket:
        def recv(self):
            exception_stream._stop.set()
            raise RuntimeError("socket closed by local stop")

    exception_stream._socket = LocallyClosedExceptionSocket()
    exception_stream._read_loop()

    assert exception_stream.errors == []
    assert exception_stream.closed_by_harness is True

    remote_stream = harness.RuntimeEventStream(
        "http://127.0.0.1:7880",
        "token",
    )

    class RemotelyClosedSocket:
        def recv(self):
            return ""

    remote_stream._socket = RemotelyClosedSocket()
    remote_stream._read_loop()

    assert remote_stream.errors == [
        "WebSocketConnectionClosedException: empty close frame",
    ]
    assert remote_stream.closed_by_harness is False
