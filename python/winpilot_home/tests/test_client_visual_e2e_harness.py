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
    success = {
        "case_id": "case-1",
        "scenario": scenario,
        "seed": 17,
        "type": "scenario_success",
        "trusted": True,
        "pointer_type": "mouse",
        "move_count": moves,
        "succeeded": True,
        "details": {"succeeded": True, **details},
    }
    return {
        "ready": True,
        "success": True,
        "case_id": "case-1",
        "scenario": scenario,
        "seed": 17,
        "events": [success],
        "success_event": success,
    }


def test_real_client_visual_matrix_is_generic_and_covers_gesture_shapes():
    harness = _load_harness()

    assert harness.SUITES["smoke"] == (
        "drag-fit", "slider", "dynamic-hold", "game-drag-v2",
    )
    assert harness.SUITES["goal-admission"] == (
        "precise-click", "ordered-clicks",
    )
    assert harness.SUITES["exam"] == ("answer-all-questions",)
    assert {
        item["scenario"] for item in harness.VISUAL_CASES.values()
    } == {
        "drag_fit",
        "precise_click",
        "ordered_clicks",
        "slider",
        "corridor_drag",
        "dynamic_hold",
        "quiz_all",
        "calibration_grid",
    }
    assert all(
        harness.VISUAL_CASES[name]["runtime_version"] == "vnext"
        for name in harness.SUITES["goal-admission"]
    )
    assert {
        harness.VISUAL_CASES[name]["expected_variant"]
        for name in ("corridor-v0", "corridor-v1", "game-drag-v2", "corridor-v3")
    } == {0, 1, 2, 3}
    assert {
        item["task"] for item in harness.VISUAL_CASES.values()
    } == {
        "完成当前界面上的测试。",
        "必须使用GUI和真实鼠标完成当前界面上的测试。",
        "必须使用GUI和真实鼠标键盘，帮我在当前开启的页面里答完所有题。",
    }
    quiz_case = harness.VISUAL_CASES["answer-all-questions"]
    assert quiz_case["runtime_version"] == "vnext"
    assert quiz_case["ground_truth"] == (
        "trusted_browser_form_submit_and_server_evaluation"
    )
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
    assert "seed" in inspect.signature(harness.run_visual_case).parameters
    assert "植物大战僵尸" not in " ".join(
        item["task"] for item in harness.VISUAL_CASES.values()
    )


def test_client_case_specs_preserve_spoken_gui_requirements():
    harness = _load_harness()

    source = harness.VISUAL_CASES["precise-click"]
    spec = harness._client_case_spec(source)

    assert spec == {
        "task": "必须使用GUI和真实鼠标完成当前界面上的测试。",
        "max_steps": 12,
        "timeout": 360,
        "runtime_version": "vnext",
    }

    quiz_spec = harness._client_case_spec(
        harness.VISUAL_CASES["answer-all-questions"],
    )
    assert quiz_spec == {
        "task": "必须使用GUI和真实鼠标键盘，帮我在当前开启的页面里答完所有题。",
        "max_steps": 40,
        "timeout": 600,
        "runtime_version": "vnext",
    }
    assert "scenario" not in quiz_spec
    assert "seed" not in quiz_spec


def test_target_focus_preserves_bound_geometry_unless_minimized(monkeypatch):
    harness = _load_harness()
    hwnd = 4242
    restore_calls: list[tuple[int, int]] = []

    monkeypatch.setattr(harness.win32gui, "IsWindow", lambda value: value == hwnd)
    monkeypatch.setattr(harness.win32gui, "IsIconic", lambda _value: False)
    monkeypatch.setattr(
        harness.win32gui,
        "ShowWindow",
        lambda value, mode: restore_calls.append((value, mode)),
    )
    monkeypatch.setattr(harness.win32gui, "BringWindowToTop", lambda _value: None)
    monkeypatch.setattr(harness.win32gui, "SetForegroundWindow", lambda _value: None)
    monkeypatch.setattr(harness.win32gui, "GetForegroundWindow", lambda: hwnd)
    monkeypatch.setattr(harness.win32gui, "GetWindowText", lambda _value: "target")
    monkeypatch.setattr(harness.win32api, "keybd_event", lambda *_args: None)
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)

    assert harness._focus_window(hwnd)["focused"] is True
    assert restore_calls == []

    monkeypatch.setattr(harness.win32gui, "IsIconic", lambda _value: True)
    assert harness._focus_window(hwnd)["focused"] is True
    assert restore_calls == [(hwnd, harness.win32con.SW_RESTORE)]


def test_external_verdict_requires_precise_click_geometry_and_no_wrong_click():
    harness = _load_harness()
    state = _state(
        "precise_click",
        {
            "target_index": 0,
            "target": {"left": 30, "top": 40, "right": 92, "bottom": 102},
        },
        moves=0,
    )

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="precise_click", seed=17,
    )

    assert verdict["passed"] is True
    state["events"].insert(0, {
        "type": "scenario_failure",
        "trusted": True,
        "details": {"clicked_index": 4, "target_index": 0},
    })
    failed = harness.external_verdict(
        state, case_id="case-1", scenario="precise_click", seed=17,
    )
    assert failed["passed"] is False
    assert "a trusted wrong-object click preceded success" in failed["errors"]


def test_external_verdict_requires_complete_trusted_ordered_click_progression():
    harness = _load_harness()
    box = {"left": 30, "top": 40, "right": 98, "bottom": 108}
    state = _state(
        "ordered_clicks",
        {
            "click_sequence": [1, 2, 3],
            "required_sequence": [1, 2, 3],
            "targets": [dict(box), dict(box), dict(box)],
        },
        moves=0,
    )
    state["events"][:0] = [
        {
            "type": "sequence_click",
            "trusted": True,
            "details": {"clicked": [1], "expected_next": 2},
        },
        {
            "type": "sequence_click",
            "trusted": True,
            "details": {"clicked": [1, 2], "expected_next": 3},
        },
        {
            "type": "sequence_click",
            "trusted": True,
            "details": {"clicked": [1, 2, 3], "expected_next": None},
        },
    ]

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="ordered_clicks", seed=17,
    )

    assert verdict["passed"] is True
    state["events"].pop(1)
    failed = harness.external_verdict(
        state, case_id="case-1", scenario="ordered_clicks", seed=17,
    )
    assert failed["passed"] is False
    assert "trusted sequence-click progression is incomplete" in failed["errors"]


def test_external_verdict_requires_server_verified_mixed_scroll_quiz():
    harness = _load_harness()
    question_ids = [f"q-{index}" for index in range(6)]
    question_types = [
        "single_choice",
        "text",
        "single_choice",
        "text",
        "single_choice",
        "text",
    ]
    state = _state(
        "quiz_all",
        {
            "server_verified": True,
            "all_correct": True,
            "question_count": 6,
            "answer_count": 6,
            "correct_count": 6,
            "question_ids": question_ids,
            "question_types": question_types,
        },
        moves=0,
    )
    state["success_event"]["type"] = "quiz_submit"
    state["events"].insert(0, {
        "type": "page_ready",
        "details": {
            "requires_page_scroll": True,
            "question_ids": question_ids,
            "question_types": question_types,
        },
    })

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="quiz_all", seed=17,
    )

    assert verdict["passed"] is True
    state["success_event"]["details"]["server_verified"] = False
    failed = harness.external_verdict(
        state, case_id="case-1", scenario="quiz_all", seed=17,
    )
    assert failed["passed"] is False
    assert "quiz submission was not independently evaluated" in failed["errors"]


def test_external_verdict_requires_complete_object_placement():
    harness = _load_harness()
    state = _state(
        "drag_fit",
        {
            "token": {"left": 30, "top": 30, "right": 60, "bottom": 60},
            "target": {"left": 20, "top": 20, "right": 70, "bottom": 70},
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
    corridor["events"].insert(0, {
        "type": "page_ready",
        "details": {"variant": 2},
    })
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
    corridor["events"].insert(0, {
        "type": "page_ready",
        "details": {"variant": 0},
    })

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
    dynamic["events"].insert(0, {
        "type": "pointerdown",
        "trusted": True,
        "details": {"down_count": 1},
    })
    dynamic["events"].insert(1, {
        "type": "checkpoint_reached",
        "trusted": True,
        "details": {"checkpoint_reached": True},
    })
    dynamic["events"].insert(2, {
        "type": "pointerup",
        "trusted": True,
        "succeeded": True,
        "details": {
            "succeeded": True,
            "down_count": 1,
            "checkpoint_reached": True,
            "goal_revealed_after_down": True,
            "inside_goal": True,
            "completion_event": "pointerup",
        },
    })

    verdict = harness.external_verdict(
        dynamic,
        case_id="case-1",
        scenario="dynamic_hold",
        seed=17,
    )

    assert verdict["passed"] is True
    dynamic["events"].insert(0, {
        "type": "pointerdown",
        "trusted": True,
        "details": {"down_count": 2},
    })
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
    dynamic["events"][:0] = [
        {"type": "pointerdown", "trusted": True, "details": {"down_count": 1}},
        {
            "type": "checkpoint_reached",
            "trusted": True,
            "details": {"checkpoint_reached": True},
        },
    ]

    verdict = harness.external_verdict(
        dynamic,
        case_id="case-1",
        scenario="dynamic_hold",
        seed=17,
    )

    assert verdict["passed"] is False
    assert "dynamic gesture has no unique trusted pointerup evidence" in verdict["errors"]


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
