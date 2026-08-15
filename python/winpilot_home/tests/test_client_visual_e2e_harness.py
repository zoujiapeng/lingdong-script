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
        "drag-fit", "slider", "dynamic-hold",
    )
    assert harness.SUITES["coordination"] == (
        "draw-shared-turn", "modifier-drag", "held-device-chord",
    )
    assert harness.SUITES["input-basics"] == (
        "click-double", "scroll-roundtrip", "type-hotkey",
    )
    assert harness.SUITES["goal-admission"] == (
        "precise-click", "ordered-clicks",
    )
    assert harness.SUITES["exam"] == ("answer-all-questions",)
    assert harness.SUITES["stress"] == (
        "corridor-v0", "corridor-v1", "game-drag-v2", "corridor-v3",
    )
    assert {
        item["scenario"] for item in harness.VISUAL_CASES.values()
    } == {
        "drag_fit",
        "precise_click",
        "ordered_clicks",
        "slider",
        "corridor_drag",
        "dynamic_hold",
        "shared_turn",
        "modifier_drag",
        "held_device_chord",
        "click_double",
        "scroll_roundtrip",
        "type_hotkey",
        "quiz_all",
        "calibration_grid",
        "calibration_feedback",
    }
    assert all(
        harness.VISUAL_CASES[name]["runtime_version"] == "vnext"
        for name in harness.SUITES["goal-admission"]
    )
    assert {
        name: harness.VISUAL_CASES[name]["seed"]
        for name in ("corridor-v0", "corridor-v1", "game-drag-v2", "corridor-v3")
    } == {
        "corridor-v0": 7,
        "corridor-v1": 12,
        "game-drag-v2": 1,
        "corridor-v3": 4,
    }
    assert {
        item["task"] for item in harness.VISUAL_CASES.values()
    } == {
        "完成当前界面上的测试。",
        "必须使用GUI和真实鼠标完成当前界面上的测试。",
        "必须使用GUI和真实鼠标键盘，帮我在当前开启的页面里答完所有题。",
        "必须使用GUI和真实鼠标完成当前界面上的测试。这个测试测量的是你用鼠标"
        "点击紫色方块中心的精度，每次真实点击的位置都会被记录并在任务结束后"
        "验证，所以请直接用鼠标点击当前页面上每个紫色方块的中心，直到全部"
        "方块都变成绿色。",
        "必须使用GUI和真实鼠标完成当前界面上的测试。这个测试测量的是你用鼠标"
        "点击紫色方块中心的精度，每次真实点击的位置都会被记录并在任务结束后"
        "验证；页面状态行会提示上次点击的偏差，请根据提示直接用鼠标修正点击，"
        "直到全部方块都变成绿色。",
    }
    for name in ("calibration-grid", "calibration-feedback"):
        task = harness.VISUAL_CASES[name]["task"]
        assert "真实鼠标" in task, f"{name} must demand real mouse input"
        assert "紫色方块" in task, f"{name} must name the visible page target"
        assert "绿色" in task, f"{name} must state the visible completion state"
        assert "精度" in task, f"{name} must say it measures click precision"
        assert "验证" in task, f"{name} must say clicks are verified"
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


def test_target_focus_waits_for_electron_desktop_lease_minimize(monkeypatch):
    harness = _load_harness()
    electron_hwnd = 711
    target_hwnd = 4242
    minimize_checks = 0
    focus_calls: list[int] = []

    def is_iconic(hwnd: int) -> bool:
        nonlocal minimize_checks
        assert hwnd == electron_hwnd
        minimize_checks += 1
        return minimize_checks >= 3

    def focus(hwnd: int) -> dict[str, object]:
        assert minimize_checks >= 3
        focus_calls.append(hwnd)
        return {"target_hwnd": hwnd, "focused": True}

    monkeypatch.setattr(harness.win32gui, "IsIconic", is_iconic)
    monkeypatch.setattr(harness, "_focus_window", focus)
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)

    evidence = harness._focus_target_after_desktop_yield(
        electron_hwnd,
        target_hwnd,
        timeout=1.0,
        poll_interval=0.0,
    )

    assert minimize_checks == 3
    assert focus_calls == [target_hwnd]
    assert evidence["electron_hwnd"] == electron_hwnd
    assert evidence["electron_minimized_observed"] is True


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
            "started_at_origin": True,
            "path_valid": True,
            "min_clearance": 7,
            "path_points": [[1, 1], [2, 2], [3, 3], [4, 4]],
        },
        moves=8,
    )
    assert harness.external_verdict(
        corridor,
        case_id="case-1",
        scenario="corridor_drag",
        seed=17,
    )["passed"] is True

    corridor["success_event"]["details"]["path_valid"] = False
    failed = harness.external_verdict(
        corridor,
        case_id="case-1",
        scenario="corridor_drag",
        seed=17,
    )
    assert failed["passed"] is False
    assert "gesture left the visible corridor" in failed["errors"]

    corridor["success_event"]["details"]["path_valid"] = True
    corridor["success_event"]["details"]["started_at_origin"] = False
    failed_start = harness.external_verdict(
        corridor,
        case_id="case-1",
        scenario="corridor_drag",
        seed=17,
    )
    assert failed_start["passed"] is False
    assert "corridor gesture did not start from the original token" in failed_start["errors"]


def test_corridor_zero_clearance_is_valid():
    harness = _load_harness()
    corridor = _state(
        "corridor_drag",
        {
            "started_at_origin": True,
            "path_valid": True,
            "min_clearance": 0,
            "path_points": [[1, 1], [2, 2], [3, 3], [4, 4]],
        },
        moves=8,
    )
    verdict = harness.external_verdict(
        corridor,
        case_id="case-1",
        scenario="corridor_drag",
        seed=17,
    )

    assert verdict["passed"] is True


def test_external_verdict_accepts_a_complete_held_pointer_retry():
    harness = _load_harness()
    dynamic = _state(
        "dynamic_hold",
        {
            "down_count": 7,
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
        "details": {"down_count": 7},
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
            "down_count": 7,
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
    dynamic["events"][:0] = [{
        "type": "pointerdown",
        "trusted": True,
        "details": {"down_count": 6},
    }, {
        "type": "pointerup",
        "trusted": True,
        "succeeded": False,
        "details": {"succeeded": False, "down_count": 6},
    }]
    retried = harness.external_verdict(
        dynamic,
        case_id="case-1",
        scenario="dynamic_hold",
        seed=17,
    )
    assert retried["passed"] is True


def test_external_verdict_accepts_trusted_two_stroke_drawing_with_short_final_stroke():
    harness = _load_harness()
    state = _state(
        "shared_turn",
        {"user_cell": 1, "selected_cell": 4, "valid_stroke_count": 2},
        moves=1,
    )
    state["events"][:0] = [
        {"type": "page_ready", "details": {"user_cell": 1}},
        {"type": "pointerdown", "trusted": True},
        {"type": "pointerup", "trusted": True},
        {"type": "pointerdown", "trusted": True},
        {"type": "pointerup", "trusted": True},
    ]

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="shared_turn", seed=17,
    )

    assert verdict["passed"] is True


def test_external_verdict_accepts_keyboard_mouse_interleaving():
    harness = _load_harness()
    state = _state(
        "modifier_drag",
        {
            "shift_at_pointerdown": True,
            "shift_during_drag": True,
            "shift_at_pointerup": True,
            "inside_target": True,
            "mouse_released_before_shift": True,
        },
        moves=9,
    )
    state["events"][:0] = [
        {"type": "pointerdown", "trusted": True, "details": {"shift_key": False}},
        {"type": "pointerup", "trusted": True, "details": {"inside_target": True}},
        {"type": "keydown", "trusted": True, "details": {"key": "Shift"}},
        {"type": "keyup", "trusted": True, "details": {"key": "Shift"}},
        {"type": "keydown", "trusted": True, "details": {"key": "Shift"}},
        {
            "type": "pointerdown",
            "trusted": True,
            "details": {"shift_key": True, "shift_held": True},
        },
        {
            "type": "pointerup",
            "trusted": True,
            "details": {"inside_target": True, "shift_at_pointerup": True},
        },
        {
            "type": "keyup",
            "trusted": True,
            "details": {"key": "Shift", "succeeded": True},
        },
    ]

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="modifier_drag", seed=17,
    )

    assert verdict["passed"] is True


def test_external_verdict_requires_one_held_mouse_keyboard_wheel_timeline():
    harness = _load_harness()
    state = _state(
        "held_device_chord",
        {
            "down_count": 7,
            "checkpoint_reached": True,
            "key_during_hold": True,
            "wheel_during_hold": True,
            "goal_revealed_after_coordination": True,
            "post_coordination_moves": 2,
            "inside_goal": True,
            "completion_event": "pointerup",
        },
        moves=12,
    )
    timeline = [
        {"type": "pointerdown", "trusted": True},
        {"type": "checkpoint_reached", "trusted": True},
        {"type": "keydown", "trusted": True},
        {"type": "wheel", "trusted": True},
        {"type": "coordination_ready", "trusted": True},
        {"type": "post_coordination_move", "trusted": True},
        {"type": "pointerup", "trusted": True},
    ]
    state["events"][:0] = timeline

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="held_device_chord", seed=17,
    )
    assert verdict["passed"] is True

    state["events"] = [
        item for item in state["events"] if item.get("type") != "wheel"
    ]
    failed = harness.external_verdict(
        state, case_id="case-1", scenario="held_device_chord", seed=17,
    )
    assert failed["passed"] is False
    assert any("one ordered gesture" in item for item in failed["errors"])


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
    assert "dynamic gesture has no successful trusted pointerup evidence" in verdict["errors"]


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


def test_dynamic_server_accepts_a_complete_retry_with_cumulative_press_count():
    path = (
        Path(__file__).resolve().parents[1]
        / "eval_targets"
        / "visual_interaction_server.py"
    )
    spec = importlib.util.spec_from_file_location("visual_interaction_server", path)
    assert spec is not None and spec.loader is not None
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    details = {
        "succeeded": True,
        "down_count": 7,
        "checkpoint_reached": True,
        "goal_revealed_after_down": True,
        "inside_goal": True,
        "completion_event": "pointerup",
    }
    events = [
        {
            "case_id": "case-1",
            "scenario": "dynamic_hold",
            "seed": 17,
            "type": "pointerup",
            "trusted": True,
            "succeeded": True,
            "details": dict(details),
        },
        {
            "case_id": "case-1",
            "scenario": "dynamic_hold",
            "seed": 17,
            "type": "scenario_success",
            "trusted": True,
            "succeeded": True,
            "details": dict(details),
        },
    ]

    assert server._accepted_success_event(events, 1) is True


def test_calibration_hits_mutate_the_shared_point_object():
    # A spread copy (best={...p}) would set hit on the copy only, so
    # points.every(p=>p.hit) could never become true and the page could
    # never report CALIBRATION_SUCCEEDED no matter how precise the clicks.
    target = (
        Path(__file__).resolve().parents[1]
        / "eval_targets"
        / "visual_interaction_target.html"
    ).read_text(encoding="utf-8")
    for setup_name in ("setupCalibrationGrid", "setupCalibrationFeedback"):
        setup = target[target.index(f"function {setup_name}()") :]
        next_setup = target.index(
            "function setup", target.index(f"function {setup_name}()") + 1
        )
        setup = setup[:next_setup]
        assert "best=p;" in setup, f"{setup_name} must reference the point"
        hit_branch = setup[setup.index("if(best){") :]
        assert "best.hit=true;" in hit_branch
        assert "best.el.classList.add('hit')" in hit_branch
        assert "points.every(p=>p.hit)" in hit_branch
        assert "markSuccess('CALIBRATION_SUCCEEDED'" in hit_branch


def test_calibration_clicks_are_judged_in_arena_coordinates():
    # event.clientX/Y are viewport coordinates while the calibration points
    # are arena-relative.  Judging dx = clientX - center.x without removing
    # the arena offset scores a click that lands exactly on square 0 as a hit
    # on square 6 and marks every real center-click on row 0 as a miss; the
    # square that visually turns green then never matches the recorded index.
    target = (
        Path(__file__).resolve().parents[1]
        / "eval_targets"
        / "visual_interaction_target.html"
    ).read_text(encoding="utf-8")
    for setup_name in ("setupCalibrationGrid", "setupCalibrationFeedback"):
        setup = target[target.index(f"function {setup_name}()") :]
        next_setup = target.index(
            "function setup", target.index(f"function {setup_name}()") + 1
        )
        setup = setup[:next_setup]
        pointer_branch = setup[setup.index("arena.addEventListener('pointerdown'") :]
        assert "el.addEventListener('pointerdown'" not in setup
        assert "getBoundingClientRect" in pointer_branch, (
            f"{setup_name} must convert viewport to arena coordinates"
        )
        assert "event.clientX-arenaRect.left" in pointer_branch
        assert "event.clientY-arenaRect.top" in pointer_branch


def test_calibration_acceptance_area_is_not_clipped_to_the_visible_square():
    target = (
        Path(__file__).resolve().parents[1]
        / "eval_targets"
        / "visual_interaction_target.html"
    ).read_text(encoding="utf-8")

    grid = target[target.index("function setupCalibrationGrid()") :]
    grid = grid[: grid.index("function setupCalibrationFeedback()")]
    assert "size=28,tolerance=20" in grid
    assert "arena.addEventListener('pointerdown'" in grid
    assert "Math.abs(dx)<=tolerance" in grid
    assert "tolerance_px:tolerance" in grid

    feedback = target[target.index("function setupCalibrationFeedback()") :]
    feedback = feedback[: feedback.index("function setupQuizAll()")]
    assert "arena.addEventListener('pointerdown'" in feedback
    assert "Math.abs(dx)<=tolerance" in feedback


def test_visual_target_builds_geometry_after_browser_layout_stabilizes():
    target = (
        Path(__file__).resolve().parents[1]
        / "eval_targets"
        / "visual_interaction_target.html"
    ).read_text(encoding="utf-8")

    setup_call = "Promise.resolve((setups[scenario]||setupDragFit)())"
    assert target.index("requestAnimationFrame(()=>requestAnimationFrame(()=>") < target.index(setup_call)
    assert target.index(setup_call) < target.index("report('page_ready'")


def test_basic_scroll_target_allows_a_normal_focus_click():
    target = (
        Path(__file__).resolve().parents[1]
        / "eval_targets"
        / "vnext_canary_target.html"
    ).read_text(encoding="utf-8")

    assert "click_forbidden_in_scroll_case" not in target
    assert "keyboard_forbidden_in_scroll_case" not in target


def test_corridor_origin_check_stays_in_arena_local_coordinates():
    target = (
        Path(__file__).resolve().parents[1]
        / "eval_targets"
        / "visual_interaction_target.html"
    ).read_text(encoding="utf-8")
    corridor = target[
        target.index("function setupCorridor()") :
        target.index("function setupDynamicHold()")
    ]

    assert "Number.parseFloat(token.style.left)" in corridor
    assert "Number.parseFloat(token.style.top)" in corridor
    assert "tokenLeft-(sx-22)" in corridor
    assert "tokenTop-(sy-22)" in corridor


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


def _calibration_state(scenario: str, point_count: int, *, misses: int = 0) -> dict:
    state = _state(
        scenario,
        {"succeeded": True},
        moves=0,
    )
    state["events"][:0] = [
        {
            "type": "page_ready",
            "details": {
                "point_count": point_count,
                "tolerance_px": 8 if scenario == "calibration_feedback" else 20,
            },
        }
    ]
    for index in range(point_count):
        state["events"].insert(
            index + 1,
            {
                "type": "calibration_hit",
                "trusted": True,
                "scenario": scenario,
                "details": {"index": index},
            },
        )
    for index in range(misses):
        state["events"].append(
            {
                "type": "calibration_miss",
                "trusted": True,
                "scenario": scenario,
                "details": {"nearest_index": 0},
            }
        )
    return state


def test_external_verdict_accepts_complete_calibration_grid():
    harness = _load_harness()

    state = _calibration_state("calibration_grid", 12)

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="calibration_grid", seed=17,
    )
    assert verdict["passed"] is True

    state["events"] = [
        item for item in state["events"]
        if not (item.get("type") == "calibration_hit" and (item.get("details") or {}).get("index") == 7)
    ]
    failed = harness.external_verdict(
        state, case_id="case-1", scenario="calibration_grid", seed=17,
    )
    assert failed["passed"] is False
    assert "calibration did not hit every declared point" in failed["errors"][0]


def test_external_verdict_accepts_calibration_feedback_after_corrected_misses():
    harness = _load_harness()

    state = _calibration_state("calibration_feedback", 3, misses=1)

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="calibration_feedback", seed=17,
    )
    assert verdict["passed"] is True

    state["events"] = [
        item for item in state["events"]
        if not (
            item.get("type") == "calibration_hit"
            and (item.get("details") or {}).get("index") == 1
        )
    ]
    failed = harness.external_verdict(
        state, case_id="case-1", scenario="calibration_feedback", seed=17,
    )
    assert failed["passed"] is False
    assert "calibration did not hit every declared point" in failed["errors"][0]
