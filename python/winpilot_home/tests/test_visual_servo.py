import time

import cv2
import numpy as np

from winpilot.fast.visual_servo import ServoConfig, track_visual_target
from winpilot.perception.model import UIElement


def _moving_scene(disappear: bool = False, delay_frames: int = 0):
    state = {
        "left": 70,
        "top": 85,
        "moved": False,
        "applied": False,
        "visible": True,
        "captures_since_move": 0,
    }
    moves = []

    def render():
        image = np.full((320, 600, 3), 245, dtype=np.uint8)
        if state["visible"]:
            left, top = state["left"], state["top"]
            cv2.rectangle(image, (left, top), (left + 60, top + 30), (30, 30, 30), 2)
            cv2.line(image, (left + 8, top + 8), (left + 52, top + 22), (0, 0, 0), 2)
            cv2.circle(image, (left + 15, top + 15), 4, (0, 0, 0), -1)
        return image

    def capture(region):
        if state["moved"] and not state["applied"]:
            if state["captures_since_move"] >= delay_frames:
                state["applied"] = True
                if disappear:
                    state["visible"] = False
                else:
                    state.update(left=190, top=125)
            state["captures_since_move"] += 1
        frame = render()
        return frame[region.top:region.bottom, region.left:region.right].copy()

    def move(x, y):
        moves.append((x, y))
        if not state["moved"] and abs(x - 100) <= 10 and abs(y - 100) <= 10:
            state["moved"] = True

    return state, moves, capture, move


def _initial_element():
    return UIElement(
        elem_id=5,
        role="button",
        text="Export",
        cx=100,
        cy=100,
        left=70,
        top=85,
        width=60,
        height=30,
    )


def test_visual_servo_tracks_one_hover_move_and_confirms_before_click():
    _state, moves, capture, move = _moving_scene()

    target, trace = track_visual_target(
        _initial_element(),
        ["Export"],
        capture_fn=capture,
        move_fn=move,
        ocr_fn=lambda *_args, **_kwargs: [],
        config=ServoConfig(iterations=4, settle_ms=5, hover_guard_ms=5),
    )

    assert target is not None
    assert (target.cx, target.cy) == (220, 140)
    assert trace["confirmed"] is True
    assert trace["correction_count"] == 1
    assert trace["source_counts"]["servo-template"] >= 1
    assert moves[0] == (100, 100)
    assert moves[-1] == (220, 140)


def test_visual_servo_fails_closed_when_target_disappears():
    _state, moves, capture, move = _moving_scene(disappear=True)

    target, trace = track_visual_target(
        _initial_element(),
        ["Export"],
        capture_fn=capture,
        move_fn=move,
        ocr_fn=lambda *_args, **_kwargs: [],
        config=ServoConfig(iterations=3, settle_ms=5, hover_guard_ms=5, strict=True),
    )

    assert target is None
    assert trace["confirmed"] is False
    assert trace["lost_reason"] == "target_not_visible_in_local_roi"
    assert moves == [(100, 100)]


def test_visual_servo_waits_through_stale_frame_after_hover_trigger():
    _state, moves, capture, move = _moving_scene(delay_frames=1)

    target, trace = track_visual_target(
        _initial_element(),
        ["Export"],
        capture_fn=capture,
        move_fn=move,
        ocr_fn=lambda *_args, **_kwargs: [],
        config=ServoConfig(iterations=5, settle_ms=5, hover_guard_ms=5),
    )

    assert target is not None
    assert (target.cx, target.cy) == (220, 140)
    assert trace["confirmed"] is True
    assert trace["iterations"][0]["corrected"] is False
    assert any(item["corrected"] for item in trace["iterations"][1:])
    assert max(item["stable_streak"] for item in trace["iterations"]) >= 2
    assert len(trace["iterations"]) >= 3
    assert (220, 140) in moves


def test_visual_servo_does_not_converge_before_hover_guard_expires():
    initial = _initial_element()
    state = {"triggered_at": None, "left": 70, "top": 85}

    def render():
        image = np.full((320, 600, 3), 245, dtype=np.uint8)
        left, top = state["left"], state["top"]
        cv2.rectangle(image, (left, top), (left + 60, top + 30), (30, 30, 30), 2)
        cv2.line(image, (left + 8, top + 8), (left + 52, top + 22), (0, 0, 0), 2)
        cv2.circle(image, (left + 15, top + 15), 4, (0, 0, 0), -1)
        return image

    def capture(region):
        if state["triggered_at"] is not None:
            elapsed = (time.monotonic() - state["triggered_at"]) * 1000
            if elapsed >= 55:
                state.update(left=190, top=125)
        frame = render()
        return frame[region.top:region.bottom, region.left:region.right].copy()

    def move(_x, _y):
        if state["triggered_at"] is None:
            state["triggered_at"] = time.monotonic()

    target, trace = track_visual_target(
        initial,
        ["Export"],
        capture_fn=capture,
        move_fn=move,
        ocr_fn=lambda *_args, **_kwargs: [],
        config=ServoConfig(
            iterations=6,
            settle_ms=20,
            hover_guard_ms=60,
        ),
    )

    assert target is not None
    assert (target.cx, target.cy) == (220, 140)
    assert trace["confirmed"] is True
    assert trace["iterations"][0]["hover_guard_complete"] is False
    assert any(item["corrected"] for item in trace["iterations"])


def test_servo_config_can_force_semantic_ocr_cross_check():
    config = ServoConfig.from_action({"servo_always_ocr": True})

    assert config.always_ocr is True
