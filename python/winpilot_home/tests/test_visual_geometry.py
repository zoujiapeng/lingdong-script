from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from winpilot.tasking.interaction import (
    InteractionAction,
    InteractionDecision,
    ExpectedInteractionEffect,
)
from winpilot.tasking.visual_geometry import (
    VisualGeometryError,
    refine_interaction_geometry,
)


def _decision(
    *,
    source: list[float],
    target: list[float],
    start: tuple[float, float],
    end: tuple[float, float],
    interaction_region: list[float] | None = None,
) -> InteractionDecision:
    region = interaction_region or [0, 0, 1000, 1000]
    return InteractionDecision(
        scene_summary="one visible object must be moved into one visible target",
        mode="direct_visual_manipulation",
        turn_owner="agent",
        agent_should_act=True,
        surface_region=[0, 0, 1000, 1000],
        interaction_region=region,
        roles={"agent": {"role": "operator"}},
        confidence=0.98,
        actions=[InteractionAction(
            action="drag",
            frame_x1=start[0],
            frame_y1=start[1],
            frame_x2=end[0],
            frame_y2=end[1],
            source_region=source,
            target_region=target,
            placement_requirement="source_inside_target",
            expected_effect_region=target,
        )],
        expected_effect={
            "description": "the complete source is visibly inside the target",
            "region": target,
            "semantic_verification_required": True,
        },
        continuation="reobserve",
        frame_id=7,
        observation_artifact_id="art-current",
    )


def _write(path: Path, image: np.ndarray) -> None:
    assert cv2.imwrite(str(path), image)


def test_local_geometry_corrects_coarse_drag_boxes_from_current_pixels(tmp_path):
    image = np.full((700, 1000, 3), 242, dtype=np.uint8)
    for x in range(0, 1000, 50):
        cv2.line(image, (x, 120), (x, 620), (226, 226, 226), 1)
    for y in range(120, 621, 50):
        cv2.line(image, (0, y), (999, y), (226, 226, 226), 1)
    cv2.circle(image, (180, 350), 50, (235, 120, 30), -1)
    cv2.rectangle(image, (680, 280), (880, 480), (50, 150, 55), 8)
    path = tmp_path / "coarse-drag.png"
    _write(path, image)

    decision = _decision(
        source=[130, 350, 230, 450],
        target=[680, 350, 880, 650],
        start=(180, 400),
        end=(780, 500),
    )
    refined, proof = refine_interaction_geometry(decision, path)

    action = refined.actions[0]
    assert proof["verified"] is True
    assert proof["applied"] is True
    assert action.frame_y1 == pytest.approx(500, abs=18)
    assert action.frame_y2 == pytest.approx(543, abs=28)
    assert action.source_region[1] >= 410
    assert action.target_region[1] >= 360
    assert proof["refinements"][0]["source"]["provider"] == "local_geometry_consensus"
    assert len(proof["refinements"][0]["source"]["providers"]) >= 2
    assert len(proof["refinements"][0]["source"]["provider_families"]) >= 2


def test_unique_multi_family_pixels_correct_client_chrome_coordinate_offset(tmp_path):
    image = np.full((700, 1000, 3), 242, dtype=np.uint8)
    cv2.circle(image, (220, 390), 48, (235, 120, 30), -1)
    cv2.circle(image, (220, 390), 48, (20, 70, 150), 5)
    cv2.rectangle(image, (700, 300), (880, 500), (50, 150, 55), 8)
    path = tmp_path / "client-chrome-offset.png"
    _write(path, image)
    decision = _decision(
        source=[175, 400, 265, 490],
        target=[690, 410, 890, 700],
        start=(220, 445),
        end=(790, 555),
    )

    refined, proof = refine_interaction_geometry(decision, path)

    source = proof["refinements"][0]["source"]
    assert source["acceptance_policy"] in {
        "strict_consensus", "unique_multi_family_coordinate_correction",
    }
    assert len(source["provider_families"]) >= 3
    assert refined.actions[0].frame_y1 == pytest.approx(557, abs=28)


def test_explicit_target_effect_binding_follows_refined_geometry(tmp_path):
    image = np.full((700, 1000, 3), 242, dtype=np.uint8)
    cv2.circle(image, (180, 350), 50, (235, 120, 30), -1)
    cv2.rectangle(image, (680, 280), (880, 480), (50, 150, 55), 8)
    path = tmp_path / "bound-effect-drag.png"
    _write(path, image)
    base = _decision(
        source=[130, 350, 230, 450],
        target=[680, 350, 880, 650],
        start=(180, 400),
        end=(780, 500),
    )
    payload = base.model_dump(mode="json")
    # Deliberately make the planner's effect coordinates inconsistent with its
    # target coordinates.  The explicit semantic binding, not IoU guessing,
    # determines where post-action verification must look after refinement.
    payload["actions"][0]["expected_effect_region"] = [20, 20, 80, 80]
    payload["actions"][0]["effect_region_binding"] = "target_region"
    payload["expected_effect"]["region"] = [20, 20, 80, 80]
    payload["expected_effect"]["region_binding"] = "target_region"
    payload["expected_effect"]["binding_action_index"] = 0
    decision = InteractionDecision.model_validate(payload)

    refined, proof = refine_interaction_geometry(decision, path)

    assert refined.actions[0].expected_effect_region == refined.actions[0].target_region
    assert refined.expected_effect.region == refined.actions[0].target_region
    assert proof["refinements"][0]["effect_region_binding"] == "target_region"


def test_drag_target_anchor_is_projected_into_refined_target(tmp_path):
    image = np.full((700, 1000, 3), 242, dtype=np.uint8)
    cv2.circle(image, (180, 350), 50, (235, 120, 30), -1)
    cv2.rectangle(image, (680, 280), (880, 480), (50, 150, 55), 8)
    path = tmp_path / "project-target-anchor.png"
    _write(path, image)
    base = _decision(
        source=[130, 350, 230, 450],
        target=[680, 350, 880, 650],
        start=(180, 400),
        end=(780, 500),
    )
    payload = base.model_dump(mode="json")
    payload["actions"][0]["target_anchor"] = [780, 500]
    decision = InteractionDecision.model_validate(payload)

    refined, proof = refine_interaction_geometry(decision, path)

    action = refined.actions[0]
    left, top, right, bottom = action.target_region
    assert left <= action.target_anchor[0] <= right
    assert top <= action.target_anchor[1] <= bottom
    refinement = proof["refinements"][0]
    assert refinement["original_target_anchor"] == [780.0, 500.0]
    assert refinement["refined_target_anchor"] == pytest.approx(
        action.target_anchor, abs=0.001,
    )


def test_point_placement_uses_refined_semantic_anchor_as_drag_endpoint(tmp_path):
    image = np.full((1000, 1000, 3), 242, dtype=np.uint8)
    cv2.circle(image, (790, 170), 38, (235, 120, 30), -1)
    cv2.circle(image, (790, 170), 38, (20, 70, 150), 4)
    cv2.circle(image, (125, 610), 60, (50, 150, 55), 8)
    path = tmp_path / "semantic-anchor-endpoint.png"
    _write(path, image)
    decision = InteractionDecision(
        scene_summary="one visible object must be moved to one visible target",
        mode="direct_visual_manipulation",
        turn_owner="agent",
        agent_should_act=True,
        surface_region=[0, 0, 1000, 1000],
        interaction_region=[35, 75, 965, 965],
        roles={"agent": {"role": "operator"}},
        confidence=0.98,
        actions=[InteractionAction(
            action="drag",
            frame_x1=790,
            frame_y1=170,
            # Deliberately near the coarse target's lower edge.  The semantic
            # anchor is the intended point placement and must win after refinement.
            frame_x2=125,
            frame_y2=635,
            source_region=[740, 120, 840, 220],
            target_region=[75, 545, 175, 645],
            target_anchor=[125, 595],
            placement_requirement="point_inside_target",
            expected_effect_region=[75, 545, 175, 645],
        )],
        expected_effect={
            "description": "the source overlaps the visible target",
            "region": [75, 545, 175, 645],
            "semantic_verification_required": True,
        },
        continuation="reobserve",
        frame_id=7,
        observation_artifact_id="art-current",
    )

    refined, proof = refine_interaction_geometry(
        decision, path, expected_size=(1000, 1000),
    )

    action = refined.actions[0]
    assert proof["verified"] is True
    assert action.frame_x2 == pytest.approx(action.target_anchor[0], abs=0.001)
    assert action.frame_y2 == pytest.approx(action.target_anchor[1], abs=0.001)
    left, top, right, bottom = action.target_region
    assert left < action.frame_x2 < right
    assert top < action.frame_y2 < bottom


def test_object_anchored_click_is_regrounded_from_current_pixels(tmp_path):
    image = np.full((500, 800, 3), 242, dtype=np.uint8)
    cv2.circle(image, (200, 300), 45, (40, 140, 230), -1)
    path = tmp_path / "anchored-click.png"
    _write(path, image)
    decision = InteractionDecision(
        scene_summary="one visible control",
        mode="direct_visual_manipulation",
        turn_owner="agent",
        agent_should_act=True,
        surface_region=[0, 0, 1000, 1000],
        interaction_region=[50, 100, 950, 950],
        roles={"agent": {"role": "operator"}},
        confidence=0.98,
        actions=[InteractionAction(
            action="click",
            frame_x=240,
            frame_y=520,
            target_region=[180, 430, 300, 620],
            grounding_mode="object_anchor",
            expected_effect_region=[180, 430, 300, 620],
            effect_region_binding="target_region",
        )],
        expected_effect=ExpectedInteractionEffect(
            description="the visible control changes",
            region=[180, 430, 300, 620],
            region_binding="target_region",
            binding_action_index=0,
        ),
        continuation="reobserve",
        frame_id=1,
        observation_artifact_id="art-observation",
    )

    refined, proof = refine_interaction_geometry(decision, path)

    assert refined.actions[0].frame_y == pytest.approx(600, abs=24)
    assert refined.actions[0].expected_effect_region == refined.actions[0].target_region
    assert proof["refinements"][0]["grounding_mode"] == "object_anchor"
    assert len(proof["refinements"][0]["anchor"]["provider_families"]) >= 2


def test_anchored_point_uses_target_semantics_without_losing_action_label(
    monkeypatch,
    tmp_path,
):
    import winpilot.tasking.visual_geometry as geometry_module

    image = np.full((500, 800, 3), 242, dtype=np.uint8)
    cv2.circle(image, (600, 250), 48, (35, 125, 220), -1)
    cv2.circle(image, (600, 250), 48, (20, 65, 145), 5)
    path = tmp_path / "semantic-target-role.png"
    _write(path, image)

    # Isolate the target-only anchored-shape provider. Before the role/label
    # split, point actions passed ``action[0]_target_region`` as the semantic
    # role, so this provider was incorrectly disabled even though the same
    # visible target was valid for a drag destination.
    monkeypatch.setattr(
        geometry_module,
        "_candidate_masks",
        lambda _roi, _rough_width, _rough_height: [],
    )

    def anchored_circle(roi, rough_box):
        left, top, right, bottom = rough_box
        mask = np.zeros(roi.shape[:2], dtype=np.uint8)
        center = (
            int(round((left + right) / 2.0)),
            int(round((top + bottom) / 2.0)),
        )
        radius = max(8, int(round(min(right - left, bottom - top) * 0.45)))
        cv2.circle(mask, center, radius, 255, -1)
        return [("local_circle", mask)]

    monkeypatch.setattr(
        geometry_module, "_anchored_circle_masks", anchored_circle,
    )
    decision = InteractionDecision(
        scene_summary="one visible circular target",
        mode="direct_visual_manipulation",
        turn_owner="agent",
        agent_should_act=True,
        surface_region=[0, 0, 1000, 1000],
        interaction_region=[50, 100, 950, 900],
        roles={"agent": {"role": "operator"}},
        confidence=0.98,
        actions=[InteractionAction(
            action="click",
            frame_x=750,
            frame_y=500,
            target_region=[680, 400, 820, 600],
            target_anchor=[750, 500],
            grounding_mode="object_anchor",
            interaction_mechanism="point_selection",
            affordance_evidence="one visible bounded circular target",
            expected_effect_region=[680, 400, 820, 600],
            effect_region_binding="target_region",
        )],
        expected_effect=ExpectedInteractionEffect(
            description="the visible target changes state",
            region=[680, 400, 820, 600],
            region_binding="target_region",
            binding_action_index=0,
        ),
        continuation="reobserve",
        frame_id=1,
        observation_artifact_id="art-observation",
    )

    refined, proof = refine_interaction_geometry(decision, path)

    anchor = proof["refinements"][0]["anchor"]
    assert anchor["role"] == "action[0]_target_region"
    assert "local_circle" in anchor["providers"]
    assert anchor["acceptance_policy"] == "semantic_target_shape_anchor"
    assert refined.actions[0].target_region != decision.actions[0].target_region


def test_target_circle_prefers_complete_enclosing_boundary_over_nearby_arc(
    monkeypatch,
):
    import winpilot.tasking.visual_geometry as geometry_module

    image = np.full((500, 800, 3), 242, dtype=np.uint8)
    monkeypatch.setattr(
        geometry_module, "_candidate_masks", lambda *_args: [],
    )

    def circle_hypotheses(roi, rough_box):
        left, top, right, bottom = rough_box
        center_x = int(round((left + right) / 2.0))
        center_y = int(round((top + bottom) / 2.0))
        small = np.zeros(roi.shape[:2], dtype=np.uint8)
        enclosing = np.zeros(roi.shape[:2], dtype=np.uint8)
        cv2.circle(small, (center_x, center_y), 28, 255, -1)
        cv2.circle(
            enclosing, (center_x - 5, center_y + 35), 54, 255, -1,
        )
        return [("local_circle", small), ("local_circle", enclosing)]

    monkeypatch.setattr(
        geometry_module, "_anchored_circle_masks", circle_hypotheses,
    )
    monkeypatch.setattr(
        geometry_module,
        "_circle_boundary_support",
        lambda _edges, _x, _y, width, _height: 0.76 if width > 90 else 0.57,
    )

    proof = geometry_module._resolve_region(
        image,
        [400, 300, 500, 420],
        [0, 0, 1000, 1000],
        role="target",
    )

    assert proof["acceptance_policy"] == "semantic_enclosing_target_shape"
    assert proof["pixel_box"][2] - proof["pixel_box"][0] > 100
    assert proof["circle_boundary_support"] == pytest.approx(0.76)
    assert proof["circle_boundary_margin"] >= 0.18


def test_enclosing_target_accepts_substantial_partial_coarse_overlap(
    monkeypatch,
):
    """The planner box is a search anchor and may cover only part of a goal."""
    import winpilot.tasking.visual_geometry as geometry_module

    image = np.full((500, 800, 3), 242, dtype=np.uint8)
    monkeypatch.setattr(
        geometry_module, "_candidate_masks", lambda *_args: [],
    )

    def circle_hypotheses(roi, rough_box):
        left, top, right, bottom = rough_box
        center_x = int(round((left + right) / 2.0))
        center_y = int(round((top + bottom) / 2.0))
        nearby_arc = np.zeros(roi.shape[:2], dtype=np.uint8)
        enclosing = np.zeros(roi.shape[:2], dtype=np.uint8)
        cv2.circle(nearby_arc, (center_x, center_y), 28, 255, -1)
        # Most of the complete outline lies below the coarse semantic box,
        # while still overlapping nearly half of it.
        cv2.circle(enclosing, (center_x - 5, center_y + 55), 54, 255, -1)
        return [("local_circle", nearby_arc), ("local_circle", enclosing)]

    monkeypatch.setattr(
        geometry_module, "_anchored_circle_masks", circle_hypotheses,
    )
    monkeypatch.setattr(
        geometry_module,
        "_circle_boundary_support",
        lambda _edges, _x, _y, width, _height: 0.76 if width > 90 else 0.57,
    )

    proof = geometry_module._resolve_region(
        image,
        [400, 300, 500, 420],
        [0, 0, 1000, 1000],
        role="target",
    )

    assert proof["acceptance_policy"] == "semantic_enclosing_target_shape"
    assert 0.40 <= proof["anchor_containment"] < 0.60
    assert proof["circle_boundary_support"] == pytest.approx(0.76)
    assert proof["circle_boundary_margin"] >= 0.18


def test_anchored_point_projects_semantic_anchor_with_refined_region(
    monkeypatch,
    tmp_path,
):
    import winpilot.tasking.visual_geometry as geometry_module

    image = np.full((500, 800, 3), 242, dtype=np.uint8)
    path = tmp_path / "shifted-point-anchor.png"
    _write(path, image)
    resolved_region = [700.0, 420.0, 800.0, 560.0]
    monkeypatch.setattr(
        geometry_module,
        "_resolve_region",
        lambda *_args, **_kwargs: {
            "resolved_region": list(resolved_region),
            "provider": "component_geometry_provider",
            "providers": ["component_geometry_provider"],
            "provider_families": ["shape"],
            "verified": True,
        },
    )
    decision = InteractionDecision(
        scene_summary="one shifted visible target",
        mode="direct_visual_manipulation",
        turn_owner="agent",
        agent_should_act=True,
        surface_region=[0, 0, 1000, 1000],
        interaction_region=[50, 50, 950, 950],
        roles={"agent": {"role": "operator"}},
        confidence=0.98,
        actions=[InteractionAction(
            action="click",
            frame_x=850,
            frame_y=650,
            target_region=[600, 300, 900, 700],
            target_anchor=[850, 650],
            grounding_mode="object_anchor",
            interaction_mechanism="point_selection",
            affordance_evidence="one visible bounded target",
            expected_effect_region=[600, 300, 900, 700],
            effect_region_binding="target_region",
        )],
        expected_effect=ExpectedInteractionEffect(
            description="the target changes state",
            region=[600, 300, 900, 700],
            region_binding="target_region",
            binding_action_index=0,
        ),
        continuation="reobserve",
        frame_id=1,
        observation_artifact_id="art-observation",
    )

    refined, proof = refine_interaction_geometry(decision, path)

    action = refined.actions[0]
    left, top, right, bottom = action.target_region
    assert left <= action.target_anchor[0] <= right
    assert top <= action.target_anchor[1] <= bottom
    assert action.target_anchor != decision.actions[0].target_anchor
    assert action.frame_x == pytest.approx(action.target_anchor[0])
    assert action.frame_y == pytest.approx(action.target_anchor[1])
    refinement = proof["refinements"][0]
    assert refinement["original_target_anchor"] == [850.0, 650.0]
    assert refinement["refined_target_anchor"] == pytest.approx(
        action.target_anchor, abs=0.001,
    )


def test_free_path_returns_explicit_typed_policy_proof(tmp_path):
    decision = InteractionDecision(
        scene_summary="one bounded free path surface",
        mode="direct_visual_manipulation",
        turn_owner="agent",
        agent_should_act=True,
        surface_region=[0, 0, 1000, 1000],
        interaction_region=[100, 100, 900, 900],
        roles={"agent": {"role": "operator"}},
        confidence=0.98,
        actions=[InteractionAction(
            action="draw_strokes",
            frame_strokes=[[[300, 300], [450, 450], [600, 400]]],
            target_region=[200, 200, 800, 800],
        )],
        expected_effect=ExpectedInteractionEffect(
            description="the bounded path is visible",
            region=[200, 200, 800, 800],
        ),
        continuation="reobserve",
        frame_id=1,
        observation_artifact_id="art-observation",
    )

    refined, proof = refine_interaction_geometry(
        decision, tmp_path / "not-decoded-for-free-path.png",
    )

    assert refined.actions[0].grounding_mode == "free_path"
    assert proof["verified"] is True
    assert proof["applied"] is False
    assert proof["provider"] == "typed_spatial_policy"


def test_target_relative_free_path_records_coordinate_grounding(tmp_path):
    decision = InteractionDecision(
        scene_summary="one bounded local drawing region",
        mode="shared_visual_session",
        turn_owner="agent",
        agent_should_act=True,
        surface_region=[0, 0, 1000, 1000],
        interaction_region=[100, 100, 900, 900],
        roles={"agent": {"role": "participant"}},
        confidence=0.98,
        actions=[InteractionAction(
            action="draw_strokes",
            local_strokes=[[[100, 100], [900, 900]], [[900, 100], [100, 900]]],
            target_region=[300, 300, 500, 500],
        )],
        expected_effect=ExpectedInteractionEffect(
            description="one local mark appears",
            region=[300, 300, 500, 500],
        ),
        continuation="finish",
        frame_id=1,
        observation_artifact_id="art-observation",
    )

    refined, proof = refine_interaction_geometry(
        decision, tmp_path / "not-needed-for-relative-path.png",
    )

    assert refined is decision
    grounding = proof["action_grounding"][0]
    assert grounding["stroke_coordinate_space"] == "target_region_0_1000"
    assert grounding["target_region"] == [300.0, 300.0, 500.0, 500.0]


def test_local_geometry_has_grayscale_edge_and_contrast_fallback(tmp_path):
    image = np.full((600, 900, 3), 228, dtype=np.uint8)
    cv2.circle(image, (180, 330), 42, (65, 65, 65), -1)
    cv2.rectangle(image, (610, 250), (790, 430), (82, 82, 82), 7)
    path = tmp_path / "grayscale-drag.png"
    _write(path, image)
    decision = _decision(
        source=[150, 380, 250, 500],
        target=[650, 330, 850, 600],
        start=(200, 440),
        end=(750, 465),
    )

    refined, proof = refine_interaction_geometry(decision, path)

    assert refined.actions[0].frame_y1 == pytest.approx(550, abs=25)
    assert "local_contrast" in proof["refinements"][0]["source"]["providers"]
    assert "local_contrast" in proof["refinements"][0]["target"]["providers"]


def test_local_geometry_rejects_equally_plausible_candidates(tmp_path):
    image = np.full((600, 900, 3), 240, dtype=np.uint8)
    cv2.circle(image, (360, 300), 35, (200, 90, 30), -1)
    cv2.circle(image, (540, 300), 35, (200, 90, 30), -1)
    cv2.rectangle(image, (680, 230), (840, 390), (40, 150, 60), 6)
    path = tmp_path / "ambiguous-source.png"
    _write(path, image)
    decision = _decision(
        source=[465, 430, 535, 570],
        target=[740, 340, 920, 650],
        start=(500, 500),
        end=(830, 495),
    )

    with pytest.raises(VisualGeometryError, match="source visual grounding is ambiguous"):
        refine_interaction_geometry(decision, path)


def test_local_geometry_never_expands_search_outside_action_roi(tmp_path):
    image = np.full((500, 800, 3), 238, dtype=np.uint8)
    cv2.circle(image, (90, 250), 45, (220, 100, 25), -1)
    cv2.rectangle(image, (560, 180), (720, 340), (35, 145, 50), 7)
    path = tmp_path / "outside-roi.png"
    _write(path, image)
    decision = _decision(
        source=[260, 400, 360, 600],
        target=[700, 320, 900, 680],
        start=(310, 500),
        end=(800, 500),
        interaction_region=[200, 100, 950, 900],
    )

    with pytest.raises(VisualGeometryError, match="source visual grounding is ambiguous"):
        refine_interaction_geometry(decision, path)


def test_drag_without_explicit_source_and_target_is_not_marked_grounded(tmp_path):
    image = np.full((300, 500, 3), 240, dtype=np.uint8)
    path = tmp_path / "missing-regions.png"
    _write(path, image)
    decision = InteractionDecision(
        scene_summary="coarse drag without object regions",
        mode="direct_visual_manipulation",
        turn_owner="agent",
        agent_should_act=True,
        surface_region=[0, 0, 1000, 1000],
        interaction_region=[0, 0, 1000, 1000],
        roles={"agent": {"role": "operator"}},
        confidence=0.98,
        actions=[InteractionAction(
            action="drag",
            frame_x1=100,
            frame_y1=200,
            frame_x2=800,
            frame_y2=200,
        )],
        expected_effect=ExpectedInteractionEffect(
            description="object moved",
            region=[700, 100, 900, 300],
        ),
        continuation="reobserve",
        frame_id=1,
        observation_artifact_id="art-observation",
    )

    with pytest.raises(VisualGeometryError, match="explicit source_region"):
        refine_interaction_geometry(decision, path)


def test_grounding_rejects_artifact_frame_dimension_mismatch(tmp_path):
    image = np.full((600, 900, 3), 228, dtype=np.uint8)
    cv2.circle(image, (180, 330), 42, (65, 65, 65), -1)
    cv2.rectangle(image, (610, 250), (790, 430), (82, 82, 82), 7)
    path = tmp_path / "wrong-size.png"
    _write(path, image)
    decision = _decision(
        source=[150, 380, 250, 500],
        target=[650, 330, 850, 600],
        start=(200, 440),
        end=(750, 465),
    )

    with pytest.raises(VisualGeometryError, match="dimensions do not match"):
        refine_interaction_geometry(decision, path, expected_size=(901, 600))


def test_correlated_masks_do_not_count_as_independent_grounding_families(
    monkeypatch, tmp_path,
):
    import winpilot.tasking.visual_geometry as geometry_module

    image = np.full((400, 600, 3), 235, dtype=np.uint8)
    path = tmp_path / "correlated-only.png"
    _write(path, image)

    def correlated_masks(roi, rough_width, rough_height):
        mask = np.zeros(roi.shape[:2], dtype=np.uint8)
        left = max(0, (roi.shape[1] - rough_width) // 2)
        top = max(0, (roi.shape[0] - rough_height) // 2)
        mask[top:top + rough_height, left:left + rough_width] = 255
        return [
            ("local_contrast", mask.copy()),
            ("local_luminance", mask.copy()),
        ]

    monkeypatch.setattr(geometry_module, "_candidate_masks", correlated_masks)
    decision = _decision(
        source=[200, 200, 400, 400],
        target=[600, 200, 800, 400],
        start=(300, 300),
        end=(700, 300),
    )

    with pytest.raises(VisualGeometryError, match=r"families=1/2"):
        refine_interaction_geometry(decision, path)


def test_unique_high_confidence_empty_target_can_use_boundary_evidence(
    monkeypatch,
):
    import winpilot.tasking.visual_geometry as geometry_module

    image = np.full((400, 600, 3), 235, dtype=np.uint8)

    def one_boundary_mask(roi, rough_width, rough_height):
        mask = np.zeros(roi.shape[:2], dtype=np.uint8)
        center_x = roi.shape[1] // 2 + 135
        center_y = roi.shape[0] // 2 - 40
        left = center_x - rough_width // 2
        top = center_y - rough_height // 2
        cv2.rectangle(
            mask,
            (left, top),
            (left + rough_width - 1, top + rough_height - 1),
            255,
            4,
        )
        return [("local_edges", mask)]

    monkeypatch.setattr(geometry_module, "_candidate_masks", one_boundary_mask)

    proof = geometry_module._resolve_region(
        image,
        [600, 200, 850, 600],
        [0, 0, 1000, 1000],
        role="target",
    )

    assert proof["acceptance_policy"] == "unique_high_confidence_target_boundary"
    assert proof["provider_families"] == ["boundary"]
    assert proof["rough_iou"] >= 0.75


def test_unique_dense_colour_component_can_ground_a_filled_source(monkeypatch):
    import winpilot.tasking.visual_geometry as geometry_module

    image = np.full((400, 600, 3), 235, dtype=np.uint8)

    def one_filled_component(roi, rough_width, rough_height):
        mask = np.zeros(roi.shape[:2], dtype=np.uint8)
        center_x = roi.shape[1] // 2
        center_y = roi.shape[0] // 2
        left = center_x - rough_width // 2
        top = center_y - rough_height // 2
        cv2.rectangle(
            mask,
            (left, top),
            (left + rough_width - 1, top + rough_height - 1),
            255,
            -1,
        )
        return [("local_chroma", mask)]

    monkeypatch.setattr(geometry_module, "_candidate_masks", one_filled_component)

    proof = geometry_module._resolve_region(
        image,
        [400, 400, 600, 600],
        [0, 0, 1000, 1000],
        role="source",
    )

    assert proof["acceptance_policy"] == "unique_high_confidence_filled_region"
    assert proof["provider_families"] == ["color"]
    assert proof["support_ratio"] >= 0.95


def test_nested_modality_boxes_are_one_object_not_false_ambiguity(monkeypatch):
    import winpilot.tasking.visual_geometry as geometry_module

    image = np.full((420, 640, 3), 235, dtype=np.uint8)

    def nested_provider_masks(roi, rough_width, rough_height):
        masks = []
        center_x, center_y = roi.shape[1] // 2, roi.shape[0] // 2
        for provider, scale in (
            ("local_contrast", 0.72),
            ("local_edges", 0.62),
            ("local_chroma", 0.58),
        ):
            mask = np.zeros(roi.shape[:2], dtype=np.uint8)
            half_w = int(rough_width * scale / 2)
            half_h = int(rough_height * scale / 2)
            cv2.rectangle(
                mask,
                (center_x - half_w, center_y - half_h),
                (center_x + half_w, center_y + half_h),
                255,
                -1,
            )
            masks.append((provider, mask))
        return masks

    monkeypatch.setattr(
        geometry_module, "_candidate_masks", nested_provider_masks,
    )
    proof = geometry_module._resolve_region(
        image,
        [400, 350, 600, 650],
        [0, 0, 1000, 1000],
        role="source",
    )

    assert proof["acceptance_policy"] == "strict_consensus"
    assert set(proof["provider_families"]) == {
        "boundary", "color", "photometric",
    }
    assert proof["candidate_count"] == 1


def test_nested_circle_dedup_uses_physical_pixels_on_non_square_frame(
    monkeypatch,
):
    import winpilot.tasking.visual_geometry as geometry_module

    height, width = 753, 1086
    image = np.full((height, width, 3), 242, dtype=np.uint8)
    small_center = (142, 478)
    large_center = (157, 503)
    cv2.circle(image, small_center, 62, (35, 150, 75), 6)
    cv2.circle(image, large_center, 91, (145, 165, 190), 5)

    def nested_circle_masks(roi, rough_width, rough_height):
        # For the rough bounds below, the resolver's search origin is (1,258).
        masks = []
        for center, radius in ((small_center, 62), (large_center, 91)):
            mask = np.zeros(roi.shape[:2], dtype=np.uint8)
            cv2.circle(
                mask, (center[0] - 1, center[1] - 258), radius, 255, -1,
            )
            masks.append(("local_circle", mask))
        return masks

    monkeypatch.setattr(geometry_module, "_candidate_masks", nested_circle_masks)
    monkeypatch.setattr(
        geometry_module, "_anchored_circle_masks", lambda roi, rough_box: [],
    )
    rough = [
        86 / width * 1000.0,
        440 / height * 1000.0,
        207 / width * 1000.0,
        516 / height * 1000.0,
    ]

    proof = geometry_module._resolve_region(
        image, rough, [0, 0, 1000, 1000], role="target",
    )

    assert proof["candidate_count"] == 1
    assert proof["acceptance_policy"] == "semantic_target_shape_anchor"
    assert proof["pixel_box"][0] == pytest.approx(80, abs=2)
    assert proof["provider_scores"]["local_circle"] == pytest.approx(
        proof["score"], abs=1e-6,
    )


def test_shape_anchor_can_expand_semantic_target_interior_to_visible_circle():
    image = np.full((500, 800, 3), 242, dtype=np.uint8)
    cv2.circle(image, (600, 250), 65, (40, 150, 60), 6)

    import winpilot.tasking.visual_geometry as geometry_module

    proof = geometry_module._resolve_region(
        image,
        [700, 420, 800, 580],
        [0, 0, 1000, 1000],
        role="target",
    )

    assert "local_circle" in proof["providers"]
    assert proof["anchor_containment"] == pytest.approx(1.0)
    assert proof["resolved_region"][0] < 700
    assert proof["resolved_region"][2] > 800


def test_shape_anchor_rejects_two_equally_plausible_round_targets():
    image = np.full((500, 800, 3), 242, dtype=np.uint8)
    cv2.circle(image, (540, 250), 55, (40, 150, 60), 6)
    cv2.circle(image, (660, 250), 55, (40, 150, 60), 6)

    import winpilot.tasking.visual_geometry as geometry_module

    with pytest.raises(VisualGeometryError, match="target visual grounding is ambiguous"):
        geometry_module._resolve_region(
            image,
            [700, 420, 800, 580],
            [0, 0, 1000, 1000],
            role="target",
        )


def test_shape_anchor_corrects_bounded_coordinate_offset_to_complete_ring():
    image = np.full((500, 800, 3), 242, dtype=np.uint8)
    cv2.circle(image, (600, 330), 55, (40, 150, 60), 6)

    import winpilot.tasking.visual_geometry as geometry_module

    proof = geometry_module._resolve_region(
        image,
        [700, 420, 800, 580],
        [0, 0, 1000, 1000],
        role="target",
    )

    assert proof["resolved_region"][1] > 540
    assert proof["circle_boundary_support"] >= 0.52
    assert proof["size_area_ratio"] <= 2.2


def test_anchored_circle_pass_finds_dashed_target_inside_larger_curved_surface():
    import winpilot.tasking.visual_geometry as geometry_module

    image = np.full((560, 900, 3), 244, dtype=np.uint8)
    # A larger curved route surrounds the actual target and intentionally
    # competes with a broad circle detector.
    cv2.rectangle(image, (70, 145), (700, 430), (214, 224, 236), -1)
    cv2.rectangle(image, (70, 145), (700, 430), (145, 165, 190), 5)
    center = (155, 245)
    radius = 58
    for angle in range(0, 360, 28):
        cv2.ellipse(
            image, center, (radius, radius), 0, angle, angle + 16,
            (35, 155, 85), 7,
        )
    cv2.putText(
        image, "TARGET", (118, 252), cv2.FONT_HERSHEY_SIMPLEX,
        0.45, (20, 95, 55), 1, cv2.LINE_AA,
    )

    proof = geometry_module._resolve_region(
        image,
        [95, 300, 255, 570],
        [0, 0, 1000, 1000],
        role="target",
    )

    resolved = proof["resolved_region"]
    resolved_center = (
        (resolved[0] + resolved[2]) / 2.0 / 1000.0 * image.shape[1],
        (resolved[1] + resolved[3]) / 2.0 / 1000.0 * image.shape[0],
    )
    assert resolved_center == pytest.approx(center, abs=14)
    assert proof["acceptance_policy"] in {
        "semantic_target_shape_anchor", "strict_consensus",
    }
    assert "local_circle" in proof["providers"]
    assert proof["circle_boundary_support"] >= 0.52


def test_shape_anchor_rejects_incomplete_arc_with_weak_unique_evidence():
    image = np.full((500, 800, 3), 242, dtype=np.uint8)
    cv2.ellipse(image, (600, 330), (55, 55), 0, 20, 190, (40, 40, 40), 6)

    import winpilot.tasking.visual_geometry as geometry_module

    with pytest.raises(VisualGeometryError, match="target visual grounding is ambiguous"):
        geometry_module._resolve_region(
            image,
            [700, 420, 800, 580],
            [0, 0, 1000, 1000],
            role="target",
        )


def test_single_family_target_policy_rejects_multiple_outlined_candidates(
    monkeypatch,
):
    import winpilot.tasking.visual_geometry as geometry_module

    image = np.full((400, 600, 3), 235, dtype=np.uint8)

    def two_boundary_masks(roi, rough_width, rough_height):
        mask = np.zeros(roi.shape[:2], dtype=np.uint8)
        center_y = roi.shape[0] // 2 - 40
        for center_x in (roi.shape[1] // 2 + 55, roi.shape[1] // 2 + 215):
            left = center_x - rough_width // 2
            top = center_y - rough_height // 2
            cv2.rectangle(
                mask,
                (left, top),
                (left + rough_width - 1, top + rough_height - 1),
                255,
                4,
            )
        return [("local_edges", mask)]

    monkeypatch.setattr(geometry_module, "_candidate_masks", two_boundary_masks)

    with pytest.raises(VisualGeometryError, match="target visual grounding is ambiguous"):
        geometry_module._resolve_region(
            image,
            [600, 200, 850, 600],
            [0, 0, 1000, 1000],
            role="target",
        )


def test_dense_rectangular_target_ignores_weak_false_circle_candidate():
    import winpilot.tasking.visual_geometry as geometry_module

    image = np.full((500, 800, 3), 242, dtype=np.uint8)
    cv2.line(image, (110, 250), (700, 250), (155, 155, 155), 14)
    cv2.rectangle(image, (500, 195), (575, 305), (205, 242, 220), -1)
    stripe_mask = np.zeros(image.shape[:2], dtype=np.uint8)
    for offset in range(-20, 100, 16):
        cv2.line(
            stripe_mask,
            (500 + offset, 305),
            (500 + offset + 110, 195),
            255,
            5,
        )
    clip_mask = np.zeros_like(stripe_mask)
    cv2.rectangle(clip_mask, (500, 195), (575, 305), 255, -1)
    stripe_mask = cv2.bitwise_and(stripe_mask, clip_mask)
    image[stripe_mask != 0] = (150, 225, 180)
    cv2.rectangle(image, (500, 195), (575, 305), (30, 135, 75), 5)

    proof = geometry_module._resolve_region(
        image,
        [620, 380, 730, 630],
        [0, 0, 1000, 1000],
        role="target",
    )

    assert proof["resolved_region"][0] >= 600
    assert proof["resolved_region"][2] <= 750
    assert proof["provider_families"] == ["color"]
    assert proof["acceptance_policy"] == "unique_high_confidence_filled_region"
