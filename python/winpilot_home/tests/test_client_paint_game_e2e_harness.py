from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np


def _load_script(name: str, filename: str):
    path = Path(__file__).resolve().parents[3] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_paint_harness():
    return _load_script(
        "client_paint_game_e2e_run_test",
        "client_paint_game_e2e_run.py",
    )


def _board() -> np.ndarray:
    board = np.full((360, 360, 3), 255, dtype=np.uint8)
    for offset in (120, 240):
        cv2.line(board, (offset, 15), (offset, 345), (0, 0, 0), 7)
        cv2.line(board, (15, offset), (345, offset), (0, 0, 0), 7)
    cv2.circle(board, (60, 60), 32, (0, 0, 0), 7)
    return board


def _draw_red_x(
    board: np.ndarray,
    cell: int,
    *,
    both_diagonals: bool = True,
) -> np.ndarray:
    image = board.copy()
    row, column = divmod(cell, 3)
    left = column * 120
    top = row * 120
    cv2.line(
        image,
        (left + 30, top + 30),
        (left + 90, top + 90),
        (255, 0, 0),
        9,
    )
    if both_diagonals:
        cv2.line(
            image,
            (left + 90, top + 30),
            (left + 30, top + 90),
            (255, 0, 0),
            9,
        )
    return image


def test_client_receives_only_the_unchanged_natural_language_goal():
    harness = _load_paint_harness()

    assert harness.TASK == (
        "跟我玩这个游戏，你打开画板就知道了，现在到你了，"
        "你用红色我用黑色，轮流来"
    )
    spec = harness.client_case_spec(timeout=480, max_steps=30)

    assert spec == {
        "task": harness.TASK,
        "max_steps": 30,
        "timeout": 480,
        "runtime_version": "vnext",
        "expected": "independent_success",
    }
    assert "expected_cell" not in spec
    assert "board_bbox" not in spec
    assert "center" not in spec["task"].casefold()
    assert "中心" not in spec["task"]
    assert "X" not in spec["task"]


def test_logical_board_bbox_scales_with_the_target_window_dpi():
    harness = _load_paint_harness()
    logical = (298, 223, 571, 497)

    assert harness.logical_relative_bbox_to_physical(logical, 96) == logical
    assert harness.logical_relative_bbox_to_physical(logical, 120) == (
        373,
        279,
        714,
        621,
    )
    assert harness.logical_relative_bbox_to_physical(logical, 144) == (
        447,
        335,
        857,
        746,
    )


def test_paint_capture_adds_scaled_relative_bbox_to_physical_window_origin(
    monkeypatch,
    tmp_path,
):
    harness = _load_paint_harness()
    identity = {
        "hwnd": 101,
        "process_id": 202,
        "executable": "mspaint.exe",
        "title": "Paint",
        "window_rect": [495, 204, 1519, 932],
        "window_rect_physical": [495, 204, 1519, 932],
        "dpi": 120,
        "scale_from_96dpi": 1.25,
    }
    monkeypatch.setattr(
        harness,
        "_window_identity",
        lambda _hwnd: dict(identity),
    )
    captures: list[dict] = []

    def capture(**kwargs):
        captures.append(kwargs)
        left, top, right, bottom = kwargs["bbox"]
        return np.full(
            (bottom - top, right - left, 3),
            255,
            dtype=np.uint8,
        )

    oracle = harness.PaintWindowOracle(
        101,
        (298, 223, 571, 497),
        4,
        tmp_path,
        capture=capture,
    )
    monkeypatch.setattr(oracle, "_validate_target", lambda: None)

    frame = oracle._capture()
    evidence = oracle._bbox_evidence()

    assert oracle.relative_bbox == (298, 223, 571, 497)
    assert oracle.relative_bbox_physical == (373, 279, 714, 621)
    assert oracle.screen_bbox == (868, 483, 1209, 825)
    assert captures == [
        {
            "bbox": (868, 483, 1209, 825),
            "all_screens": True,
        }
    ]
    assert frame.shape == (342, 341, 3)
    assert evidence["board_bbox_window_relative_logical"] == [
        298,
        223,
        571,
        497,
    ]
    assert evidence["board_bbox_window_relative_physical"] == [
        373,
        279,
        714,
        621,
    ]
    assert evidence["board_bbox_screen_physical"] == [
        868,
        483,
        1209,
        825,
    ]
    assert evidence["dpi"] == 120


def test_baseline_accepts_the_empty_center_and_rejects_an_occupied_cell():
    harness = _load_paint_harness()
    board = _board()

    assert harness.baseline_contract(board, 4)["passed"] is True
    occupied = harness.baseline_contract(board, 0)

    assert occupied["passed"] is False
    assert "expected legal cell is not visibly empty" in occupied["errors"]


def test_transient_mouse_pointer_does_not_fail_empty_cell_or_retention():
    harness = _load_paint_harness()
    board = _board()
    baseline_with_pointer = board.copy()
    pointer = np.array(
        [[167, 155], [167, 185], [180, 174]],
        dtype=np.int32,
    )
    cv2.fillConvexPoly(
        baseline_with_pointer,
        pointer,
        (0, 0, 0),
    )
    cv2.polylines(
        baseline_with_pointer,
        [pointer],
        True,
        (240, 240, 240),
        2,
    )

    baseline = harness.baseline_contract(baseline_with_pointer, 4)
    after_pointer_moved, _mask = harness.evaluate_board_frame(
        baseline_with_pointer,
        _draw_red_x(board, 4),
        4,
    )

    assert baseline["passed"] is True
    assert baseline["expected_cell_dark_ratio"] < 0.035
    assert after_pointer_moved["candidate_passed"] is True
    assert after_pointer_moved["baseline_dark_retention"] > 0.98


def test_complete_red_x_in_the_expected_cell_passes_one_frame_evaluation():
    harness = _load_paint_harness()
    board = _board()

    verdict, mask = harness.evaluate_board_frame(
        board,
        _draw_red_x(board, 4),
        4,
    )

    assert verdict["candidate_passed"] is True
    assert verdict["positive_diagonal_count"] > 0
    assert verdict["negative_diagonal_count"] > 0
    assert verdict["red_pixels_outside_expected_cell"] == 0
    assert verdict["baseline_dark_retention"] == 1.0
    assert int(cv2.countNonZero(mask)) > 0


def test_one_red_diagonal_is_not_a_complete_x():
    harness = _load_paint_harness()
    board = _board()

    verdict, _mask = harness.evaluate_board_frame(
        board,
        _draw_red_x(board, 4, both_diagonals=False),
        4,
    )

    assert verdict["candidate_passed"] is False
    assert (
        "expected cell does not contain a complete red X"
        in verdict["errors"]
    )
    assert not (
        verdict["positive_diagonal_count"]
        and verdict["negative_diagonal_count"]
    )


def test_wrong_cell_red_ink_is_a_sticky_failure():
    harness = _load_paint_harness()
    board = _board()
    oracle = harness.BoardOracle(board, 4, stable_frames=2)

    wrong, _mask = oracle.observe(_draw_red_x(board, 6))
    corrected, _mask = oracle.observe(_draw_red_x(board, 4))
    corrected_again, _mask = oracle.observe(_draw_red_x(board, 4))

    assert wrong["wrong_red"] is True
    assert wrong["sticky_wrong_red_seen"] is True
    assert corrected["candidate_passed"] is True
    assert corrected["sticky_wrong_red_seen"] is True
    assert corrected_again["passed"] is False
    assert corrected_again["stable_frame_count"] == 0


def test_erasing_black_board_structure_fails_retention():
    harness = _load_paint_harness()
    board = _board()
    changed = _draw_red_x(board, 4)
    changed[115:126, :, :] = 255
    changed[:, 115:126, :] = 255

    verdict, _mask = harness.evaluate_board_frame(board, changed, 4)

    assert verdict["candidate_passed"] is False
    assert verdict["baseline_dark_retention"] < 0.82
    assert (
        "baseline black board structure was not retained"
        in verdict["errors"]
    )


def test_red_x_must_remain_stable_for_the_configured_frame_count():
    harness = _load_paint_harness()
    board = _board()
    current = _draw_red_x(board, 4)
    oracle = harness.BoardOracle(board, 4, stable_frames=3)

    first, _mask = oracle.observe(current)
    second, _mask = oracle.observe(current)
    third, _mask = oracle.observe(current)

    assert first["passed"] is False
    assert first["stable_frame_count"] == 1
    assert second["passed"] is False
    assert second["stable_frame_count"] == 2
    assert third["passed"] is True
    assert third["stable_frame_count"] == 3


def test_client_wait_can_accept_independent_success_while_run_is_active():
    harness = _load_script(
        "client_e2e_run_paint_probe_test",
        "client_e2e_run.py",
    )
    driver = object.__new__(harness.ElectronClientDriver)
    driver.runtime_status = lambda: {
        "running": True,
        "current_run": {
            "run_id": "arp-paint-test",
            "goal": "play",
            "runtime_version": "vnext",
            "status": "running",
        },
        "runtime_jobs": [],
    }
    contexts: list[dict] = []

    result = driver.wait_for_result(
        "play",
        5,
        accepted_run_id="arp-paint-test",
        independent_completion_probe=lambda context: (
            contexts.append(context)
            or {"passed": True, "status": "stable_visible_x"}
        ),
    )

    assert result["outcome"] == "independent_success"
    assert result["status"] == "running"
    assert result["run_id"] == "arp-paint-test"
    assert result["independent_verdict"] == {
        "passed": True,
        "status": "stable_visible_x",
    }
    assert contexts[0]["run_id"] == "arp-paint-test"
    assert contexts[0]["active_seen"] is True


def test_independent_success_is_opt_in_for_case_validity():
    harness = _load_script(
        "client_e2e_run_paint_validity_test",
        "client_e2e_run.py",
    )
    evidence = {
        "result": {
            "outcome": "independent_success",
            "status": "running",
        },
        "configuration": {},
        "memory_isolation": {"passed": True},
        "trace_binding": {"passed": True},
    }

    assert harness._case_execution_valid(
        {**evidence, "expected": "independent_success"}
    )
    assert not harness._case_execution_valid(
        {**evidence, "expected": "terminal"}
    )
