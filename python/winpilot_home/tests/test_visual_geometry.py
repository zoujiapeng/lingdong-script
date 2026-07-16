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
