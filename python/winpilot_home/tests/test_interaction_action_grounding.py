"""Focused contract tests for generic typed visual action grounding."""
from __future__ import annotations

from pathlib import Path

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


def test_interactive_draw_requires_a_visible_legal_target_region():
    from winpilot.tasking.interaction import InteractionAction

    with pytest.raises(ValidationError, match="requires target_region"):
        InteractionAction(
            action="draw_strokes",
            frame_strokes=[[[300, 300], [400, 400]]],
        )


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
    for field in ("source_region", "target_region", "placement_requirement"):
        assert field in schema
    assert "must declare target_region" in prompt
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
