from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import cv2
import numpy as np

from winpilot.agent.scene_coverage import SceneCoverageLedger
from winpilot.agent.loop import AgentRunner


def _jpeg(image: np.ndarray) -> str:
    ok, encoded = cv2.imencode(
        ".jpg",
        image,
        [cv2.IMWRITE_JPEG_QUALITY, 90],
    )
    assert ok
    return base64.b64encode(encoded.tobytes()).decode()


def _scene(marker_y: int) -> str:
    image = np.full((360, 640, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (120, marker_y), (520, marker_y + 70), (35, 35, 35), -1)
    cv2.putText(
        image,
        f"viewport-{marker_y}",
        (150, marker_y + 45),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (230, 230, 230),
        2,
    )
    return _jpeg(image)


def _tool_call(call_id: str, name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name=name,
            arguments=json.dumps(args, ensure_ascii=False),
        ),
    )


def test_exact_scene_cycle_records_real_revisit() -> None:
    ledger = SceneCoverageLedger()
    first = _scene(55)
    second = _scene(220)

    initial = ledger.record_frame(
        image_b64=first,
        hwnd=77,
        frame_region=(0, 0, 1920, 1080),
        frame_id=1,
        phase="initial",
    )
    moved = ledger.record_frame(
        image_b64=second,
        hwnd=77,
        frame_region=(0, 0, 1920, 1080),
        frame_id=2,
        phase="post_action",
        action="scroll",
        action_args={"amount": -5},
        action_signature="scroll-down-physical-variant",
    )
    returned = ledger.record_frame(
        image_b64=first,
        hwnd=77,
        frame_region=(0, 0, 1920, 1080),
        frame_id=3,
        phase="post_action",
        action="scroll",
        action_args={"amount": 5},
        action_signature="scroll-up-physical-variant",
    )

    assert initial["new_scene"] is True
    assert moved["new_scene"] is True
    assert returned["scene_id"] == initial["scene_id"]
    assert returned["revisit"] is True
    assert returned["visit_count"] == 2
    assert returned["cycle_detected"] is True
    assert returned["cycle_scene_path"] == [
        initial["scene_id"], moved["scene_id"], initial["scene_id"],
    ]


def test_revisited_scene_blocks_only_already_explored_scroll_edge() -> None:
    ledger = SceneCoverageLedger()
    first = _scene(55)
    second = _scene(220)
    common = {"hwnd": 77, "frame_region": (0, 0, 1920, 1080)}
    ledger.record_frame(
        image_b64=first, frame_id=1, phase="initial", **common,
    )
    ledger.record_frame(
        image_b64=second,
        frame_id=2,
        phase="post_action",
        action="scroll",
        action_args={"amount": -3, "frame_x": 500, "frame_y": 500},
        action_signature="scroll-down-a",
        **common,
    )
    ledger.record_frame(
        image_b64=first,
        frame_id=3,
        phase="post_action",
        action="scroll",
        action_args={"amount": 7, "frame_x": 510, "frame_y": 500},
        action_signature="scroll-up-a",
        **common,
    )

    blocked = ledger.guard_repeated_edge(
        action="scroll",
        # Magnitude and small point changes cannot disguise the old edge.
        action_args={"amount": -8, "frame_x": 525, "frame_y": 525},
        action_signature="scroll-down-jittered",
    )

    assert blocked is not None
    assert blocked["scene_coverage_guard"] is True
    assert blocked["action_executed"] is False
    assert blocked["proposed_edge"] == "scroll:down:r22"
    assert blocked["prior_target_scene_ids"]
    # The other frontier and explicit repeated-input tasks stay available.
    assert ledger.guard_repeated_edge(
        action="scroll",
        action_args={"amount": 4},
        action_signature="scroll-up-new-frontier",
    ) is None
    assert ledger.guard_repeated_edge(
        action="scroll",
        action_args={"amount": -4, "frame_x": 100, "frame_y": 500},
        action_signature="scroll-down-different-pane",
    ) is None
    assert ledger.guard_repeated_edge(
        action="scroll",
        action_args={"amount": -4},
        action_signature="scroll-down-repeat",
        explicit_repeat=True,
    ) is None


def test_unexecuted_action_does_not_create_coverage_edge() -> None:
    ledger = SceneCoverageLedger()
    first = _scene(55)
    common = {"hwnd": 77, "frame_region": (0, 0, 1920, 1080)}
    ledger.record_frame(
        image_b64=first, frame_id=1, phase="initial", **common,
    )
    ledger.record_frame(
        image_b64=first,
        frame_id=2,
        phase="post_action",
        action="scroll",
        action_args={"amount": -5},
        action_signature="not-executed",
        action_executed=False,
        **common,
    )

    assert ledger.guard_repeated_edge(
        action="scroll",
        action_args={"amount": -5},
        action_signature="later-real-attempt",
    ) is None


def test_click_edge_requires_cross_scene_cycle_before_blocking() -> None:
    ledger = SceneCoverageLedger()
    first = _scene(55)
    second = _scene(220)
    common = {"hwnd": 90, "frame_region": (0, 0, 1920, 1080)}
    ledger.record_frame(
        image_b64=first, frame_id=1, phase="initial", **common,
    )
    ledger.record_frame(
        image_b64=second,
        frame_id=2,
        phase="post_action",
        action="click",
        action_args={"frame_x": 500, "frame_y": 500},
        action_signature="click:same-coarse-point",
        **common,
    )
    ledger.record_frame(
        image_b64=first,
        frame_id=3,
        phase="post_action",
        action="click",
        action_args={"frame_x": 200, "frame_y": 200},
        action_signature="click:return-route",
        **common,
    )

    blocked = ledger.guard_repeated_edge(
        action="click",
        action_args={"frame_x": 508, "frame_y": 497},
        action_signature="click:same-coarse-point",
    )
    assert blocked is not None
    assert blocked["scene_coverage_guard"] is True
    assert blocked["proposed_scroll_direction"] == ""

    stable = SceneCoverageLedger()
    stable.record_frame(
        image_b64=first, frame_id=1, phase="initial", **common,
    )
    for frame_id in (2, 3, 4):
        stable.record_frame(
            image_b64=first,
            frame_id=frame_id,
            phase="post_action",
            action="click",
            action_args={"frame_x": 500, "frame_y": 500},
            action_signature="click:repeat-in-one-scene",
            **common,
        )
    # No cross-scene cycle: repeated click/game input is not suppressed.
    assert stable.guard_repeated_edge(
        action="click",
        action_args={"frame_x": 500, "frame_y": 500},
        action_signature="click:repeat-in-one-scene",
    ) is None


def test_small_system_border_change_still_matches_same_content_scene() -> None:
    image = np.full((360, 640, 3), 240, dtype=np.uint8)
    cv2.rectangle(image, (120, 80), (520, 260), (40, 90, 170), -1)
    changed_border = image.copy()
    # Simulate a taskbar clock/tray update outside the content ROI.
    cv2.rectangle(changed_border, (600, 342), (639, 359), (10, 10, 10), -1)
    ledger = SceneCoverageLedger(revisit_change_ratio=0.0012)
    first = ledger.record_frame(
        image_b64=_jpeg(image),
        hwnd=11,
        frame_region=(0, 0, 1920, 1080),
        frame_id=1,
        phase="initial",
    )
    second = ledger.record_frame(
        image_b64=_jpeg(changed_border),
        hwnd=11,
        frame_region=(0, 0, 1920, 1080),
        frame_id=2,
        phase="post_action",
        action="click",
        action_signature="click:clock-unrelated",
    )

    assert second["scene_id"] == first["scene_id"]
    assert second["revisit"] is True


def test_materially_different_content_is_a_new_scene() -> None:
    ledger = SceneCoverageLedger()
    first = ledger.record_frame(
        image_b64=_scene(55),
        hwnd=33,
        frame_region=(0, 0, 1920, 1080),
        frame_id=1,
        phase="initial",
    )
    second = ledger.record_frame(
        image_b64=_scene(220),
        hwnd=33,
        frame_region=(0, 0, 1920, 1080),
        frame_id=2,
        phase="post_action",
        action="scroll",
        action_args={"amount": -5},
        action_signature="scroll:down",
    )

    assert second["scene_id"] != first["scene_id"]
    assert second["new_scene"] is True
    summary = ledger.context_summary()
    assert "application-agnostic" in summary
    assert "unique_viewports=2" in summary


def test_successful_downward_scroll_keeps_a_measured_frontier() -> None:
    ledger = SceneCoverageLedger()
    common = {"hwnd": 44, "frame_region": (0, 0, 1920, 1080)}
    ledger.record_frame(
        image_b64=_scene(55), frame_id=1, phase="initial", **common,
    )
    moved = ledger.record_frame(
        image_b64=_scene(220),
        frame_id=2,
        phase="post_action",
        action="scroll",
        action_args={"amount": -3, "frame_x": 500, "frame_y": 500},
        **common,
    )

    frontier = ledger.frontier_recommendation()
    assert moved["scroll_direction"] == "down"
    assert frontier["last_scroll_novel"] is True
    assert frontier["recommended_direction"] == "down"
    assert frontier["recommended_amount"] == -3
    assert frontier["successful_novel_scrolls"] == 1
    assert "Measured scroll frontier" in ledger.context_summary()


def test_two_stationary_scrolls_measure_boundary_and_reverse_sign() -> None:
    ledger = SceneCoverageLedger()
    still = _scene(55)
    common = {"hwnd": 45, "frame_region": (0, 0, 1920, 1080)}
    ledger.record_frame(
        image_b64=still, frame_id=1, phase="initial", **common,
    )
    for frame_id in (2, 3):
        ledger.record_frame(
            image_b64=still,
            frame_id=frame_id,
            phase="post_action",
            action="scroll",
            action_args={"amount": -3, "frame_x": 500, "frame_y": 500},
            **common,
        )

    frontier = ledger.frontier_recommendation()
    assert frontier["boundary_reached"] is True
    assert frontier["recommended_direction"] == "up"
    assert frontier["recommended_amount"] == 3
    assert frontier["reason"] == "measured_same_direction_boundary"


def test_pixel_motion_estimator_reads_vertical_content_direction() -> None:
    rng = np.random.default_rng(7)
    first = np.full((320, 480, 3), 245, dtype=np.uint8)
    for _ in range(80):
        x = int(rng.integers(40, 440))
        y = int(rng.integers(50, 270))
        cv2.circle(first, (x, y), 3, (30, 30, 30), -1)
    current = cv2.warpAffine(
        first,
        np.float32([[1, 0, 0], [0, 1, -45]]),
        (first.shape[1], first.shape[0]),
        borderValue=(245, 245, 245),
    )

    motion = SceneCoverageLedger._measure_vertical_translation(first, current)

    assert motion["content_motion"] == "up"
    assert motion["content_shift_y_px"] < -30
    assert motion["inlier_count"] >= 6


def test_agent_loop_never_dispatches_scene_cycle_edge(monkeypatch) -> None:
    runner = AgentRunner("explore the visible interface")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner._visual_closed_loop_active = True
    runner.session.hwnd = 77
    runner.session._screen_epoch = 4
    runner._visual_frame_id = 6
    runner._visual_frame_epoch = 4
    runner._visual_frame_hwnd = 77
    runner._visual_frame_region = (0, 0, 1920, 1080)
    monkeypatch.setattr(
        "winpilot.agent.loop._main_model_has_vision", lambda: True,
    )
    monkeypatch.setattr(runner, "_has_fresh_visual_grounding", lambda: True)
    monkeypatch.setattr(
        runner,
        "_scene_coverage_guard",
        lambda *_args: {
            "ok": False,
            "verified": False,
            "blocked": True,
            "action_executed": False,
            "scene_coverage_guard": True,
            "current_scene_id": "S4",
            "current_scene_visit_count": 3,
            "proposed_edge": "scroll:down:r22",
            "prior_target_scene_ids": ["S5"],
            "cycle_scene_path": ["S4", "S5", "S4"],
            "error": "measured visual cycle",
            "needs_replan": True,
        },
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a proven repeated scene edge must not dispatch")
        ),
    )

    runner._handle_tool_calls([
        _tool_call(
            "coverage-loop",
            "scroll",
            {"amount": -5, "frame_x": 500, "frame_y": 500},
        ),
    ])

    payload = json.loads(next(
        message["content"]
        for message in reversed(runner.messages)
        if message.get("role") == "tool"
    ))
    assert payload["scene_coverage_guard"] is True
    assert payload["action_executed"] is False
    assert payload["current_scene_id"] == "S4"
