"""Focused contract tests for generic typed visual action grounding."""
from __future__ import annotations

from pathlib import Path
import random

import pytest
from pydantic import ValidationError


def _decision(action, *, frame_id: int = 7):
    from winpilot.tasking.interaction import (
        ExpectedInteractionEffect,
        InteractionDecision,
    )

    return InteractionDecision(
        scene_summary="one visually grounded interaction",
        mode="direct_visual_manipulation",
        turn_owner="agent",
        agent_should_act=True,
        interaction_region=[100, 100, 900, 900],
        roles={"agent": {"role": "operator"}},
        rules=["stay within the bound surface"],
        confidence=0.94,
        ambiguity=[],
        actions=[action],
        expected_effect=ExpectedInteractionEffect(
            description="the requested local state becomes visible",
            region=[300, 300, 800, 800],
            semantic_verification_required=True,
        ),
        continuation="finish",
        frame_id=frame_id,
        observation_artifact_id="frame-artifact",
    )


def _mapping(action):
    from winpilot.perception.surface_binding import InteractionRegion
    from winpilot.tasking.interaction import controller_actions

    return controller_actions(
        _decision(action),
        frame_region={"left": 100, "top": 200, "width": 1001, "height": 1001},
        bound_region=InteractionRegion(
            (100, 100, 900, 900),
            coordinate_space="frame_0_1000",
            label="bound",
            source="test",
        ),
        frame_epoch=7,
    )


@pytest.mark.parametrize(
    "action_name",
    ["key_down", "key_up"],
)
def test_unexecutable_press_primitives_fail_closed_at_typed_boundary(action_name):
    from winpilot.tasking.interaction import InteractionAction

    with pytest.raises(ValidationError, match="not an executable run_action_batch capability"):
        InteractionAction.model_validate({
            "action": action_name,
            "frame_x": 400,
            "frame_y": 400,
            "keys": ["shift"],
            "duration_ms": 300,
        })


def test_bounded_press_release_and_hold_primitives_are_executable_capabilities():
    from winpilot.tasking.interaction import InteractionAction

    down = InteractionAction(action="mouse_down", frame_x=400, frame_y=400)
    up = InteractionAction(action="mouse_up", frame_x=500, frame_y=500)
    hold = InteractionAction(action="hold_key", keys=["shift"], duration_ms=300)

    assert down.action == "mouse_down"
    assert up.action == "mouse_up"
    assert hold.action == "hold_key"


def test_unbalanced_mouse_press_sequence_fails_closed_before_controller_mapping():
    from winpilot.tasking.interaction import InteractionAction

    with pytest.raises(ValidationError, match="balanced continuous transaction"):
        _decision(InteractionAction(
            action="mouse_down",
            frame_x=400,
            frame_y=400,
        ))


def test_double_click_maps_to_real_controller_click_with_double_flag():
    from winpilot.fast.action_contract import validate_batch_actions
    from winpilot.tasking.interaction import InteractionAction

    actions = _mapping(InteractionAction(
        action="double_click",
        frame_x=400,
        frame_y=500,
        target_region=[350, 450, 500, 600],
        button="right",
        expected_effect_region=[360, 460, 520, 620],
    ))

    assert len(actions) == 1
    assert actions[0]["action"] == "click"
    assert actions[0]["typed_action"] == "double_click"
    assert actions[0]["double"] is True
    assert actions[0]["button"] == "right"
    assert (actions[0]["x"], actions[0]["y"]) == (500, 700)
    assert actions[0]["source_frame_grounding"] == {
        "target_region": [350.0, 450.0, 500.0, 600.0],
        "placement_requirement": "point_inside_target",
        "grounding_mode": "object_anchor",
    }
    assert actions[0]["effect_region"] == {
        "x": 460, "y": 660, "width": 161, "height": 161,
    }
    assert validate_batch_actions(actions) == []


def test_hover_maps_to_pointer_move_and_bounded_visual_dwell():
    from winpilot.fast.action_contract import validate_batch_actions
    from winpilot.tasking.interaction import InteractionAction

    actions = _mapping(InteractionAction(
        action="hover",
        frame_x=420,
        frame_y=430,
        target_region=[380, 390, 480, 490],
        duration_ms=420,
        expected_effect_region=[380, 390, 700, 600],
    ))

    assert [item["action"] for item in actions] == ["move_mouse", "wait_stable"]
    assert actions[0]["typed_action"] == "hover"
    assert (actions[0]["x"], actions[0]["y"]) == (520, 630)
    assert actions[1]["typed_action"] == "hover_dwell"
    assert actions[1]["stable_ms"] == 420
    assert actions[1]["effect_region"] == actions[0]["effect_region"]
    assert validate_batch_actions(actions) == []


def test_move_mouse_maps_without_fabricating_click_or_press():
    from winpilot.tasking.interaction import InteractionAction

    actions = _mapping(InteractionAction(
        action="move_mouse",
        frame_x=410,
        frame_y=420,
        target_region=[350, 350, 500, 500],
        expected_effect_region=[300, 300, 800, 800],
    ))

    assert len(actions) == 1
    assert actions[0]["action"] == "move_mouse"
    assert actions[0]["typed_action"] == "move_mouse"
    assert "double" not in actions[0]
    assert "button" not in actions[0]


def test_point_actions_must_be_inside_every_declared_local_grounding_region():
    from winpilot.tasking.interaction import InteractionAction

    with pytest.raises(ValidationError, match="inside source_region"):
        InteractionAction(
            action="click",
            frame_x=450,
            frame_y=450,
            source_region=[200, 200, 300, 300],
        )
    with pytest.raises(ValidationError, match="inside target_region"):
        InteractionAction(
            action="hover",
            frame_x=450,
            frame_y=450,
            target_region=[500, 500, 600, 600],
        )


def test_drag_rejects_center_only_drop_and_accepts_complete_source_footprint():
    from winpilot.tasking.interaction import InteractionAction

    with pytest.raises(ValidationError, match="complete source_region"):
        InteractionAction(
            action="drag",
            frame_x1=250,
            frame_y1=250,
            frame_x2=620,
            frame_y2=620,
            source_region=[200, 200, 300, 300],
            target_region=[600, 600, 720, 720],
            placement_requirement="source_inside_target",
        )

    action = InteractionAction(
        action="drag",
        frame_x1=250,
        frame_y1=250,
        frame_x2=660,
        frame_y2=660,
        source_region=[200, 200, 300, 300],
        target_region=[600, 600, 720, 720],
        placement_requirement="source_inside_target",
    )
    assert action.normalized_points() == [(250.0, 250.0), (660.0, 660.0)]

    # Some interfaces intentionally accept an overlap/hotspot smaller than
    # the dragged object.  Point containment remains available explicitly;
    # complete-footprint containment is never imposed on every GUI.
    overlap_drop = InteractionAction(
        action="drag",
        frame_x1=250,
        frame_y1=250,
        frame_x2=620,
        frame_y2=620,
        source_region=[200, 200, 300, 300],
        target_region=[600, 600, 640, 640],
        placement_requirement="point_inside_target",
    )
    assert overlap_drop.frame_x2 == 620


def test_action_grounding_regions_cannot_escape_semantic_interaction_region():
    from winpilot.tasking.interaction import InteractionAction

    action = InteractionAction(
        action="click",
        frame_x=220,
        frame_y=250,
        source_region=[50, 200, 250, 300],
    )
    with pytest.raises(ValidationError, match="source_region escaped"):
        _decision(action)


def test_stable_surface_and_per_turn_action_roi_have_distinct_boundaries():
    from winpilot.tasking.interaction import (
        InteractionAction,
        InteractionDecision,
    )

    payload = _decision(InteractionAction(
        action="click",
        frame_x=300,
        frame_y=300,
        expected_effect_region=[600, 600, 700, 700],
    )).model_dump(mode="python")
    payload.update({
        "surface_region": [100, 100, 900, 900],
        "interaction_region": [200, 200, 500, 500],
    })
    payload["expected_effect"]["region"] = [600, 600, 800, 800]

    decision = InteractionDecision.model_validate(payload)

    assert decision.surface_region == [100.0, 100.0, 900.0, 900.0]
    assert decision.interaction_region == [200.0, 200.0, 500.0, 500.0]
    # Effects may be outside the current action ROI because a remote status,
    # score, or validation message can be the causal postcondition.
    assert decision.expected_effect.region == [600.0, 600.0, 800.0, 800.0]


def test_action_roi_and_effect_cannot_escape_the_stable_surface():
    from winpilot.tasking.interaction import (
        InteractionAction,
        InteractionDecision,
    )

    payload = _decision(InteractionAction(
        action="click",
        frame_x=300,
        frame_y=300,
    )).model_dump(mode="python")
    payload.update({
        "surface_region": [200, 200, 800, 800],
        "interaction_region": [100, 100, 700, 700],
    })
    with pytest.raises(ValidationError, match="interaction region escaped"):
        InteractionDecision.model_validate(payload)

    payload.update({
        "surface_region": [100, 100, 800, 800],
        "interaction_region": [200, 200, 700, 700],
    })
    payload["expected_effect"]["region"] = [700, 700, 900, 900]
    with pytest.raises(ValidationError, match="expected effect escaped"):
        InteractionDecision.model_validate(payload)


def test_legacy_decision_without_surface_region_preserves_old_meaning():
    from winpilot.tasking.interaction import InteractionAction, InteractionDecision

    payload = _decision(InteractionAction(
        action="click",
        frame_x=400,
        frame_y=400,
    )).model_dump(mode="python")
    payload.pop("surface_region")

    decision = InteractionDecision.model_validate(payload)

    assert decision.surface_region == decision.interaction_region


def test_draw_points_are_bounded_by_declared_local_region():
    from winpilot.tasking.interaction import InteractionAction

    with pytest.raises(ValidationError, match="inside target_region"):
        InteractionAction(
            action="draw_strokes",
            frame_strokes=[[[300, 300], [700, 700]]],
            target_region=[200, 200, 500, 500],
        )


def test_target_relative_draw_path_maps_into_current_region_with_safe_inset():
    from winpilot.fast.action_contract import validate_batch_actions
    from winpilot.tasking.interaction import InteractionAction

    action = InteractionAction(
        action="draw_strokes",
        local_strokes=[[[0, 0], [500, 500], [1000, 1000]]],
        target_region=[200, 300, 600, 700],
        expected_effect_region=[200, 300, 600, 700],
        effect_region_binding="target_region",
    )

    assert action.normalized_points() == [
        (200.0, 300.0), (400.0, 500.0), (600.0, 700.0),
    ]
    actions = _mapping(action)

    assert actions[0]["strokes"] == [[[316, 516], [500, 700], [684, 884]]]
    source = actions[0]["source_frame_coordinates"]
    assert source["stroke_coordinate_space"] == "target_region_0_1000"
    assert source["target_inset_fraction"] == 0.04
    assert source["local_strokes"] == [[[0.0, 0.0], [500.0, 500.0], [1000.0, 1000.0]]]
    assert validate_batch_actions(actions) == []


def test_target_relative_paths_remain_inside_randomized_regions():
    """Coordinate safety is geometric, not tied to one board or screen."""
    from winpilot.tasking.interaction import InteractionAction

    rng = random.Random(20260716)
    for _ in range(250):
        left = rng.uniform(100, 650)
        top = rng.uniform(100, 650)
        width = rng.uniform(40, min(240, 900 - left))
        height = rng.uniform(40, min(240, 900 - top))
        target = [left, top, left + width, top + height]
        local = [
            [rng.uniform(0, 1000), rng.uniform(0, 1000)]
            for _ in range(rng.randint(2, 12))
        ]
        action = InteractionAction(
            action="draw_strokes",
            local_strokes=[local],
            target_region=target,
        )

        resolved = action.resolved_frame_strokes(inset_fraction=0.04)[0]
        for x, y in resolved:
            assert left + width * 0.04 <= x <= left + width * 0.96
            assert top + height * 0.04 <= y <= top + height * 0.96


def test_draw_path_coordinate_spaces_are_mutually_exclusive():
    from winpilot.tasking.interaction import InteractionAction

    with pytest.raises(ValidationError, match="exactly one"):
        InteractionAction(
            action="draw_strokes",
            frame_strokes=[[[300, 300], [400, 400]]],
            local_strokes=[[[100, 100], [900, 900]]],
            target_region=[200, 200, 800, 800],
        )

    with pytest.raises(ValidationError, match="exactly one"):
        InteractionAction(
            action="draw_strokes",
            target_region=[200, 200, 800, 800],
        )


def test_local_paths_cannot_be_smuggled_into_non_draw_actions():
    from winpilot.tasking.interaction import InteractionAction

    with pytest.raises(ValidationError, match="valid only for draw_strokes"):
        InteractionAction(
            action="click",
            frame_x=400,
            frame_y=400,
            local_strokes=[[[100, 100], [900, 900]]],
        )


def test_interactive_draw_requires_a_visible_legal_target_region():
    from winpilot.tasking.interaction import InteractionAction

    with pytest.raises(ValidationError, match="requires target_region"):
        InteractionAction(
            action="draw_strokes",
            frame_strokes=[[[300, 300], [400, 400]]],
        )


def test_physical_primitive_cannot_contradict_declared_input_mechanism():
    from winpilot.tasking.interaction import InteractionAction

    with pytest.raises(ValidationError, match="incompatible"):
        InteractionAction(
            action="click",
            frame_x=400,
            frame_y=400,
            interaction_mechanism="freehand_strokes",
        )
    action = InteractionAction(
        action="draw_strokes",
        target_region=[200, 200, 800, 800],
        local_strokes=[[[100, 100], [900, 900]]],
        interaction_mechanism="freehand_strokes",
        affordance_evidence="visible writable surface",
    )
    assert action.interaction_mechanism == "freehand_strokes"


def test_typed_stroke_paths_preserve_distinct_pointer_lifecycles():
    from winpilot.tasking.interaction import InteractionAction

    action = InteractionAction(
        action="draw_strokes",
        target_region=[200, 300, 600, 700],
        stroke_paths=[
            {
                "coordinate_space": "target_region_0_1000",
                "points": [[100, 100], [900, 900]],
            },
            {
                "coordinate_space": "target_region_0_1000",
                "points": [[900, 100], [100, 900]],
            },
        ],
    )

    strokes = action.resolved_frame_strokes(inset_fraction=0.04)
    assert len(strokes) == 2
    assert strokes[0][0] == pytest.approx([252.8, 352.8])
    assert strokes[0][-1] == pytest.approx([547.2, 647.2])
    assert strokes[1][0] == pytest.approx([547.2, 352.8])
    assert strokes[1][-1] == pytest.approx([252.8, 647.2])


def test_flat_points_are_not_guessed_into_multiple_strokes():
    from winpilot.tasking.interaction import InteractionAction

    with pytest.raises(ValidationError):
        InteractionAction.model_validate({
            "action": "draw_strokes",
            "target_region": [200, 300, 600, 700],
            "stroke_paths": [[100, 100], [900, 900]],
        })


def test_model_percent_like_stroke_scale_is_normalized_with_auditable_repair():
    from winpilot.tasking.interaction import _canonicalize_model_decision

    raw = {
        "actions": [{
            "action": "draw_strokes",
            "source_region": [200, 200, 400, 400],
            "target_region": [200, 200, 400, 400],
            "stroke_paths": [
                {
                    "coordinate_space": "target_region_0_1000",
                    "points": [[10, 10], [90, 90]],
                },
                {
                    "coordinate_space": "target_region_0_1000",
                    "points": [[90, 10], [10, 90]],
                },
            ],
        }],
    }

    normalized, repairs = _canonicalize_model_decision(raw, prior_state={})

    action = normalized["actions"][0]
    assert action["source_region"] == []
    assert action["stroke_paths"][0]["points"] == [
        [100.0, 100.0], [900.0, 900.0],
    ]
    assert "actions[0].source_region:removed_for_free_path" in repairs
    assert "actions[0].stroke_paths:percent_like->0_1000" in repairs


def test_model_single_nested_regions_are_unwrapped_without_guessing_geometry():
    from winpilot.tasking.interaction import _canonicalize_model_decision

    raw = {
        "actions": [{
            "action": "draw_strokes",
            "target_region": [[200, 200, 400, 400]],
            "target_anchor": [300, 300],
            "expected_effect_region": [[200, 200, 400, 400]],
            "stroke_paths": [{
                "coordinate_space": "target_region_0_1000",
                "points": [[150, 150], [850, 850]],
            }],
        }],
        "expected_effect": {
            "description": "the requested mark is visible",
            "region": [[200, 200, 400, 400]],
        },
    }

    normalized, repairs = _canonicalize_model_decision(raw, prior_state={})

    action = normalized["actions"][0]
    assert action["target_region"] == [200, 200, 400, 400]
    assert action["expected_effect_region"] == [200, 200, 400, 400]
    assert normalized["expected_effect"]["region"] == [200, 200, 400, 400]
    assert "actions[0].target_region:shape->flat_region" in repairs
    assert (
        "actions[0].expected_effect_region:shape->flat_region" in repairs
    )
    assert "expected_effect.region:shape->flat_region" in repairs


def test_model_multiple_nested_regions_remain_invalid_and_are_not_selected():
    from winpilot.tasking.interaction import _canonicalize_model_decision

    value = [[100, 100, 200, 200], [300, 300, 400, 400]]
    normalized, repairs = _canonicalize_model_decision({
        "actions": [{
            "action": "click",
            "frame_x": 150,
            "frame_y": 150,
            "target_region": value,
        }],
    }, prior_state={})

    assert normalized["actions"][0]["target_region"] == value
    assert not any("target_region:shape" in item for item in repairs)


def test_model_drag_points_make_contradictory_direct_label_guided():
    from winpilot.tasking.interaction import _canonicalize_model_decision

    normalized, repairs = _canonicalize_model_decision({
        "actions": [{
            "action": "drag",
            "path_mode": "direct",
            "frame_points": [[100, 100], [500, 300], [900, 900]],
        }],
    }, prior_state={})

    assert normalized["actions"][0]["path_mode"] == "guided"
    assert normalized["actions"][0]["frame_points"] == [
        [100, 100], [500, 300], [900, 900],
    ]
    assert "actions[0].path_mode:direct->guided_from_points" in repairs


def test_model_direct_drag_discards_only_redundant_collinear_samples():
    from winpilot.tasking.interaction import _canonicalize_model_decision

    normalized, repairs = _canonicalize_model_decision({
        "actions": [{
            "action": "drag",
            "path_mode": "direct",
            "frame_x1": 204,
            "frame_y1": 396,
            "frame_x2": 784,
            "frame_y2": 396,
            "frame_points": [[204, 396], [500, 397], [784, 396]],
        }],
    }, prior_state={})

    action = normalized["actions"][0]
    assert action["path_mode"] == "direct"
    assert action["frame_points"] == []
    assert (
        "actions[0].frame_points:removed_redundant_direct_path" in repairs
    )


def test_model_no_action_terminal_effect_is_unbound_without_guessing_region():
    from winpilot.tasking.interaction import _canonicalize_model_decision

    normalized, repairs = _canonicalize_model_decision({
        "agent_should_act": False,
        "actions": [],
        "continuation": "finish",
        "expected_effect": {
            "description": "the latest frame is terminal",
            "region": [100, 200, 300, 400],
            "region_binding": "target_region",
            "binding_action_index": 0,
        },
    }, prior_state={})

    effect = normalized["expected_effect"]
    assert effect["region"] == [100, 200, 300, 400]
    assert effect["region_binding"] == "independent_region"
    assert effect["binding_action_index"] == -1
    assert "expected_effect.region_binding:unbound_without_actions" in repairs


def test_planning_prompt_projects_only_executable_typed_actions_and_grounding():
    from winpilot.tasking.interaction import _planning_prompt

    prompt = _planning_prompt(
        "perform the visible interaction",
        {},
        {},
        immutable_roles={"agent": {"assignment": "red"}},
        primary_action_modality="drag",
    )
    schema = prompt.split("Required JSON shape:", 1)[1]

    for action in (
        "double_click", "hover", "move_mouse", "mouse_down", "mouse_up", "hold_key",
    ):
        assert action in schema
    for field in (
        "source_region", "target_region", "placement_requirement", "stroke_paths",
        "interaction_mechanism", "affordance_evidence",
    ):
        assert field in schema
    assert "must declare target_region" in prompt
    assert "target_region_0_1000" in prompt
    assert "Two separate pen strokes require two separate objects" in prompt
    for unavailable in ("key_down", "key_up"):
        assert unavailable not in schema
        assert f"Do not emit {unavailable}" in prompt or unavailable in prompt
    assert "finish|reobserve|wait_external|blocked" in schema
    assert "Never use reobserve" in prompt
    assert "continuation=blocked" in prompt
    assert "surface_region is the stable outer task surface" in prompt
    assert "only this turn's legal action ROI" in prompt
    assert '"assignment": "red"' in prompt
    assert "Primary action modality constraint: drag" in prompt
    assert "Prefer semantic Controller actions such as select_color" not in prompt


def test_real_drag_eval_target_requires_complete_token_containment():
    target = (
        Path(__file__).resolve().parents[1]
        / "eval_targets"
        / "visual_interaction_target.html"
    ).read_text(encoding="utf-8")

    assert "tokenRect.left >= targetRect.left" in target
    assert "tokenRect.right <= targetRect.right" in target
    assert "tokenRect.top >= targetRect.top" in target
    assert "tokenRect.bottom <= targetRect.bottom" in target
    assert "centerX >= targetRect.left && centerX <= targetRect.right" not in target
