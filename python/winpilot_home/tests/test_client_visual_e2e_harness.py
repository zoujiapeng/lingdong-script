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
    assert {
        item["scenario"] for item in harness.VISUAL_CASES.values()
    } == {"precise_click", "drag_fit", "slider", "corridor_drag", "dynamic_hold"}
    assert harness.SUITES["vnext-canary"] == ("precise-click",)
    assert harness.VISUAL_CASES["precise-click"]["runtime_version"] == "vnext"
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
    assert "seed" in inspect.signature(harness.run_visual_case).parameters
    assert "植物大战僵尸" not in " ".join(
        item["task"] for item in harness.VISUAL_CASES.values()
    )


def test_oracle_wait_ignores_page_ready_until_success_is_persisted():
    harness = _load_harness()

    assert harness._trusted_scenario_succeeded({
        "success": False,
        "events": [{"type": "page_ready"}],
    }) is False
    assert harness._trusted_scenario_succeeded({
        "success": True,
        "events": [
            {"type": "page_ready"},
            {"type": "scenario_success", "trusted": True},
        ],
    }) is True


def test_external_verdict_requires_exact_precise_click_identity_and_geometry():
    harness = _load_harness()
    target = {
        "left": 120,
        "top": 80,
        "right": 182,
        "bottom": 142,
        "width": 62,
        "height": 62,
    }
    state = _state(
        "precise_click",
        {"target_index": 7, "target": dict(target)},
        moves=0,
    )
    state["success_event"]["client_x"] = 151
    state["success_event"]["client_y"] = 111
    state["events"].insert(0, {
        "case_id": "case-1",
        "scenario": "precise_click",
        "seed": 17,
        "type": "page_ready",
        "trusted": False,
        "details": {
            "target_index": 7,
            "target": dict(target),
            "candidate_count": 12,
            "distractor_count": 11,
        },
    })

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="precise_click", seed=17,
    )

    assert verdict["passed"] is True
    state["success_event"]["details"]["target"]["left"] += 1
    failed = harness.external_verdict(
        state, case_id="case-1", scenario="precise_click", seed=17,
    )
    assert failed["passed"] is False
    assert "success target geometry does not match page-ready identity" in failed["errors"]


def test_external_verdict_rejects_precise_click_movement_and_wrong_target_identity():
    harness = _load_harness()
    target = {
        "left": 120,
        "top": 80,
        "right": 182,
        "bottom": 142,
        "width": 62,
        "height": 62,
    }
    state = _state(
        "precise_click",
        {"target_index": 12, "target": dict(target)},
        moves=1,
    )
    state["success_event"]["client_x"] = 151
    state["success_event"]["client_y"] = 111
    state["events"].insert(0, {
        "case_id": "case-1",
        "scenario": "precise_click",
        "seed": 17,
        "type": "page_ready",
        "trusted": False,
        "details": {
            "target_index": 7,
            "target": dict(target),
            "candidate_count": 12,
            "distractor_count": 11,
        },
    })

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="precise_click", seed=17,
    )

    assert verdict["passed"] is False
    assert "precise click unexpectedly recorded pointer movement" in verdict["errors"]
    assert "precise click target identity is invalid" in verdict["errors"]


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
