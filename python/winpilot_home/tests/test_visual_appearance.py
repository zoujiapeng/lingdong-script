"""Current-pixel appearance evidence stays geometric and role-safe."""
from __future__ import annotations

import cv2
import numpy as np

from winpilot.tasking.visual_appearance import (
    inventory_frame_appearance,
    match_role_colours,
)


def _shared_surface(tmp_path):
    image = np.full((600, 800, 3), 26, dtype=np.uint8)
    cv2.rectangle(image, (80, 60), (720, 540), (250, 250, 250), -1)
    # Neutral repeated dividers and one closed dark mark.
    for x in (300, 500):
        cv2.line(image, (x, 140), (x, 460), (15, 15, 15), 5)
    for y in (245, 355):
        cv2.line(image, (190, y), (610, y), (15, 15, 15), 5)
    cv2.circle(image, (400, 300), 34, (15, 15, 15), 5)
    # A red crossed mark in a different partition.
    cv2.line(image, (215, 165), (275, 225), (0, 0, 230), 7)
    cv2.line(image, (275, 165), (215, 225), (0, 0, 230), 7)
    path = tmp_path / "appearance.png"
    assert cv2.imwrite(str(path), image)
    return path


def test_inventory_reports_current_colour_without_claiming_ownership(tmp_path):
    result = inventory_frame_appearance(
        _shared_surface(tmp_path),
        surface_bounds=[100, 100, 900, 900],
    )

    assert result["ok"] is True
    assert result["provider"] == "current_frame_pixel_appearance"
    assert result["semantic_ownership"] == "unassigned"
    red = [
        item for item in result["colour_components"]
        if item["colour_family"] == "red"
    ]
    assert len(red) == 1
    assert red[0]["bounds"][0] < 400
    assert red[0]["bounds"][1] < 450
    assert result["dark_components"]


def test_explicit_role_colours_match_components_but_do_not_infer_actor(tmp_path):
    appearance = inventory_frame_appearance(
        _shared_surface(tmp_path),
        surface_bounds=[100, 100, 900, 900],
    )

    matches = match_role_colours({
        "agent": {"assignment": "red / 红色"},
        "user": {"assignment": "black / 黑色"},
    }, appearance)

    assert matches["agent"]["declared_colour_families"] == ["red"]
    assert matches["agent"]["matching_current_component_ids"] == [
        appearance["colour_components"][0]["component_id"],
    ]
    assert matches["user"]["declared_colour_families"] == ["black"]
    assert matches["user"]["matching_current_component_ids"]
    assert all(
        value["semantic_ownership"] == "must_be_decided_from_task_and_scene"
        for value in matches.values()
    )
