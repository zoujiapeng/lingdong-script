from __future__ import annotations

import cv2
import numpy as np


def _decision():
    from winpilot.tasking.interaction import (
        ExpectedInteractionEffect,
        InteractionAction,
        InteractionDecision,
    )

    region = [200, 200, 800, 800]
    return InteractionDecision(
        scene_summary="a generic pointer-authored surface",
        mode="direct_visual_manipulation",
        turn_owner="agent",
        agent_should_act=True,
        surface_region=[0, 0, 1000, 1000],
        interaction_region=[0, 0, 1000, 1000],
        roles={},
        rules=[],
        confidence=0.95,
        ambiguity=[],
        actions=[InteractionAction(
            action="draw_strokes",
            target_region=region,
            target_anchor=[500, 500],
            stroke_paths=[
                {
                    "coordinate_space": "target_region_0_1000",
                    "points": [[150, 150], [850, 850]],
                },
                {
                    "coordinate_space": "target_region_0_1000",
                    "points": [[850, 150], [150, 850]],
                },
            ],
            expected_effect_region=region,
            effect_region_binding="target_region",
            interaction_mechanism="freehand_strokes",
            affordance_evidence="visible pointer-authored surface",
        )],
        expected_effect=ExpectedInteractionEffect(
            description="the requested visible mark appears",
            region=region,
            region_binding="target_region",
            binding_action_index=0,
        ),
        continuation="finish",
        frame_id=1,
        observation_artifact_id="before",
    )


def _write_pair(tmp_path, *, complete: bool):
    before = np.full((300, 420, 3), 245, dtype=np.uint8)
    after = before.copy()
    decision = _decision()
    paths = decision.actions[0].resolved_frame_strokes(inset_fraction=0.04)
    for index, path in enumerate(paths):
        pixels = np.asarray([
            [
                round(point[0] / 1000 * (after.shape[1] - 1)),
                round(point[1] / 1000 * (after.shape[0] - 1)),
            ]
            for point in path
        ], dtype=np.int32)
        if complete or index == 0:
            cv2.polylines(after, [pixels], False, (20, 20, 220), 6)
        else:
            cv2.circle(after, tuple(pixels[0]), 2, (20, 20, 220), -1)
    before_path = tmp_path / "before.png"
    after_path = tmp_path / "after.png"
    assert cv2.imwrite(str(before_path), before)
    assert cv2.imwrite(str(after_path), after)
    return before_path, after_path, decision


def test_pointer_gesture_realization_proves_all_declared_paths(tmp_path):
    from winpilot.tasking.visual_effects import verify_pointer_gesture_realization

    before, after, decision = _write_pair(tmp_path, complete=True)
    proof = verify_pointer_gesture_realization(before, after, decision)

    assert proof["applicable"] is True
    assert proof["verified"] is True
    assert proof["actions"][0]["minimum_stroke_coverage"] >= 0.58
    assert proof["actions"][0]["extent_ratio"] >= 0.55


def test_pointer_gesture_realization_rejects_partial_or_missing_path(tmp_path):
    from winpilot.tasking.visual_effects import verify_pointer_gesture_realization

    before, after, decision = _write_pair(tmp_path, complete=False)
    proof = verify_pointer_gesture_realization(before, after, decision)

    assert proof["applicable"] is True
    assert proof["verified"] is False
    assert min(proof["actions"][0]["stroke_coverages"]) < 0.58


def _direct_drag_decision():
    from winpilot.tasking.interaction import (
        ExpectedInteractionEffect,
        InteractionAction,
        InteractionDecision,
    )

    source = [100, 350, 260, 650]
    target = [650, 250, 900, 750]
    return InteractionDecision(
        scene_summary="one visible object and one destination",
        mode="direct_visual_manipulation",
        turn_owner="agent",
        agent_should_act=True,
        surface_region=[0, 0, 1000, 1000],
        interaction_region=[0, 0, 1000, 1000],
        roles={},
        rules=[],
        confidence=0.95,
        actions=[InteractionAction(
            action="drag",
            frame_x1=180,
            frame_y1=500,
            frame_x2=775,
            frame_y2=500,
            source_region=source,
            target_region=target,
            interaction_mechanism="direct_manipulation",
            affordance_evidence="one visible movable object and destination",
            placement_requirement="source_inside_target",
            expected_effect_region=target,
            effect_region_binding="target_region",
        )],
        expected_effect=ExpectedInteractionEffect(
            description="the object is inside the destination",
            region=target,
            region_binding="target_region",
            binding_action_index=0,
        ),
        continuation="reobserve",
        frame_id=1,
        observation_artifact_id="before",
    )


def _write_direct_transition(tmp_path, *, matching: bool):
    before = np.full((300, 500, 3), 242, dtype=np.uint8)
    after = before.copy()
    cv2.rectangle(before, (325, 75), (450, 225), (40, 150, 60), 5)
    cv2.rectangle(after, (325, 75), (450, 225), (40, 150, 60), 5)
    cv2.circle(before, (90, 150), 28, (220, 90, 25), -1)
    cv2.circle(
        after,
        (387, 150),
        28,
        (220, 90, 25) if matching else (25, 25, 220),
        -1,
    )
    before_path = tmp_path / ("direct-before-match.png" if matching else "direct-before-mismatch.png")
    after_path = tmp_path / ("direct-after-match.png" if matching else "direct-after-mismatch.png")
    assert cv2.imwrite(str(before_path), before)
    assert cv2.imwrite(str(after_path), after)
    return before_path, after_path


def test_direct_manipulation_transition_proves_appearance_transfer(tmp_path):
    from winpilot.tasking.visual_effects import (
        verify_direct_manipulation_transition,
    )

    before, after = _write_direct_transition(tmp_path, matching=True)
    proof = verify_direct_manipulation_transition(
        before, after, _direct_drag_decision(),
    )

    assert proof["applicable"] is True
    assert proof["verified"] is True
    action = proof["actions"][0]
    assert action["source_change_ratio"] >= 0.06
    assert action["target_change_ratio"] >= 0.012
    assert action["appearance_histogram_intersection"] >= 0.24
    assert action["placement_geometry_valid"] is True
    assert action["terminal_verified"] is True
    assert proof["terminal_verified"] is True


def test_typed_direct_manipulation_completion_composes_transition_and_geometry(
    tmp_path,
):
    from winpilot.tasking.visual_effects import (
        verify_direct_manipulation_transition,
        verify_typed_direct_manipulation_completion,
    )

    decision = _direct_drag_decision()
    before, after = _write_direct_transition(tmp_path, matching=True)
    transition = verify_direct_manipulation_transition(before, after, decision)
    geometry = {
        "verified": True,
        "provider": "local_visual_geometry",
        "refinements": [{
            "action_index": 0,
            "source": {"acceptance_policy": "strict_consensus"},
            "target": {"acceptance_policy": "strict_consensus"},
            "path_grounding": {},
        }],
    }

    proof = verify_typed_direct_manipulation_completion(
        decision,
        transition_evidence=transition,
        geometry_evidence=geometry,
    )

    assert proof["verified"] is True
    assert proof["provider"] == "typed_direct_manipulation_completion"
    assert proof["actions"][0]["object_grounding_verified"] is True


def test_typed_direct_manipulation_completion_requires_guided_path_proof(
    tmp_path,
):
    from winpilot.tasking.visual_effects import (
        verify_direct_manipulation_transition,
        verify_typed_direct_manipulation_completion,
    )

    base = _direct_drag_decision()
    payload = base.model_dump(mode="json")
    payload["actions"][0]["path_mode"] = "guided"
    payload["actions"][0]["frame_points"] = [[400, 300], [600, 300]]
    decision = type(base).model_validate(payload)
    before, after = _write_direct_transition(tmp_path, matching=True)
    transition = verify_direct_manipulation_transition(before, after, decision)
    geometry = {
        "verified": True,
        "provider": "local_visual_geometry",
        "refinements": [{
            "action_index": 0,
            "source": {"acceptance_policy": "strict_consensus"},
            "target": {"acceptance_policy": "strict_consensus"},
            "path_grounding": {"verified": False},
        }],
    }

    rejected = verify_typed_direct_manipulation_completion(
        decision,
        transition_evidence=transition,
        geometry_evidence=geometry,
    )
    assert rejected["verified"] is False
    assert rejected["actions"][0]["path_verified"] is False

    geometry["refinements"][0]["path_grounding"]["verified"] = True
    accepted = verify_typed_direct_manipulation_completion(
        decision,
        transition_evidence=transition,
        geometry_evidence=geometry,
    )
    assert accepted["verified"] is True


def test_direct_manipulation_transition_rejects_unmatched_target_change(tmp_path):
    from winpilot.tasking.visual_effects import (
        verify_direct_manipulation_transition,
    )

    before, after = _write_direct_transition(tmp_path, matching=False)
    proof = verify_direct_manipulation_transition(
        before, after, _direct_drag_decision(),
    )

    assert proof["applicable"] is True
    assert proof["verified"] is False
    assert proof["actions"][0]["appearance_histogram_intersection"] < 0.24
