from __future__ import annotations

import base64

import cv2
import numpy as np

from winpilot.agent.graphical_affordances import detect_graphical_affordances


def _jpeg(image: np.ndarray) -> str:
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return base64.b64encode(encoded.tobytes()).decode()


def _glyph(image: np.ndarray, center: tuple[int, int]) -> None:
    x, y = center
    points = np.array([
        [x - 11, y + 7],
        [x - 3, y + 7],
        [x - 3, y - 8],
        [x + 1, y - 14],
        [x + 5, y - 11],
        [x + 4, y - 4],
        [x + 13, y - 4],
        [x + 16, y],
        [x + 11, y + 10],
        [x - 11, y + 10],
    ], dtype=np.int32)
    cv2.polylines(image, [points], True, (95, 95, 95), 2)


def test_repeated_pixel_glyphs_form_one_unlabeled_layout_family() -> None:
    image = np.full((720, 1000, 3), 248, dtype=np.uint8)
    for y in (180, 360, 560):
        _glyph(image, (720, y))
    cv2.putText(
        image,
        "settings profile comment",
        (100, 100),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (80, 80, 80),
        2,
    )

    candidates = detect_graphical_affordances(
        _jpeg(image), max_candidates=20,
    )
    family = [
        item for item in candidates
        if abs(int(item["center_frame"][0]) - 720) <= 30
    ]

    assert len(family) == 3
    assert {item["layout_family"] for item in family} == {"column_720"}
    assert all("repeated_appearance" in item["pixel_evidence"] for item in family)
    assert all(item["role"] == "unlabeled_graphical_candidate" for item in family)
    assert all("label" not in item for item in family)


def test_flat_pixels_do_not_invent_graphical_controls() -> None:
    image = np.full((480, 800, 3), 245, dtype=np.uint8)
    assert detect_graphical_affordances(_jpeg(image)) == []
