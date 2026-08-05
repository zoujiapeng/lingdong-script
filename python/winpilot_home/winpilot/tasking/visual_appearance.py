"""Deterministic current-frame appearance evidence for semantic planning.

This module deliberately stops short of naming domain objects.  It reports
where coloured and dark connected components are visible inside one proven
work surface, together with simple topology/shape measurements.  The semantic
planner can then respect role constraints such as colours without guessing
from a compressed full-window image or relying on game/application rules.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..perception import vision_cv


_ROLE_COLOUR_TERMS: dict[str, tuple[str, ...]] = {
    "red": ("red", "红", "紅"),
    "orange": ("orange", "橙"),
    "yellow": ("yellow", "黄", "黃"),
    "green": ("green", "绿", "綠"),
    "cyan": ("cyan", "青", "蓝绿", "藍綠"),
    "blue": ("blue", "蓝", "藍"),
    "purple": ("purple", "violet", "紫"),
    "magenta": ("magenta", "pink", "品红", "粉", "洋红"),
    "black": ("black", "黑"),
    "white": ("white", "白"),
    "gray": ("gray", "grey", "灰"),
}


def _colour_family(hue: float) -> str:
    value = float(hue) % 180.0
    if value < 8.0 or value >= 172.0:
        return "red"
    if value < 20.0:
        return "orange"
    if value < 36.0:
        return "yellow"
    if value < 78.0:
        return "green"
    if value < 100.0:
        return "cyan"
    if value < 132.0:
        return "blue"
    if value < 150.0:
        return "purple"
    return "magenta"


def _pixel_box(
    bounds: list[float] | tuple[float, float, float, float],
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = [float(value) for value in bounds]
    x1 = max(0, min(width - 1, int(round(left / 1000.0 * (width - 1)))))
    y1 = max(0, min(height - 1, int(round(top / 1000.0 * (height - 1)))))
    x2 = max(x1 + 1, min(width, int(round(right / 1000.0 * (width - 1))) + 1))
    y2 = max(y1 + 1, min(height, int(round(bottom / 1000.0 * (height - 1))) + 1))
    return x1, y1, x2, y2


def _frame_bounds(
    x: int,
    y: int,
    width: int,
    height: int,
    frame_width: int,
    frame_height: int,
) -> list[int]:
    return [
        int(round(x * 1000.0 / max(1, frame_width - 1))),
        int(round(y * 1000.0 / max(1, frame_height - 1))),
        int(round((x + width - 1) * 1000.0 / max(1, frame_width - 1))),
        int(round((y + height - 1) * 1000.0 / max(1, frame_height - 1))),
    ]


def _component_topology(component: np.ndarray) -> dict[str, Any]:
    contours, hierarchy = cv2.findContours(
        component,
        cv2.RETR_CCOMP,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    hole_count = 0
    if hierarchy is not None and len(hierarchy):
        hole_count = sum(1 for item in hierarchy[0] if int(item[3]) >= 0)
    ys, xs = np.nonzero(component)
    if not len(xs):
        return {"hole_count": 0, "fill_ratio": 0.0, "aspect_ratio": 0.0}
    width = int(xs.max() - xs.min() + 1)
    height = int(ys.max() - ys.min() + 1)
    fill_ratio = float(len(xs)) / max(1.0, float(width * height))
    return {
        "hole_count": int(hole_count),
        "fill_ratio": round(fill_ratio, 4),
        "aspect_ratio": round(width / max(1.0, float(height)), 4),
        "contour_count": int(len(contours)),
    }


def _components(
    mask: np.ndarray,
    *,
    hsv: np.ndarray,
    frame_width: int,
    frame_height: int,
    minimum_area: int,
    coloured: bool,
) -> list[dict[str, Any]]:
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask,
        connectivity=8,
    )
    found: list[dict[str, Any]] = []
    for label in range(1, count):
        x, y, width, height, area = [int(value) for value in stats[label]]
        if area < minimum_area or width < 2 or height < 2:
            continue
        local = (labels[y:y + height, x:x + width] == label).astype(np.uint8) * 255
        item: dict[str, Any] = {
            "bounds": _frame_bounds(
                x, y, width, height, frame_width, frame_height,
            ),
            "pixel_area": area,
            "pixel_size": [width, height],
            **_component_topology(local),
        }
        if coloured:
            pixels = hsv[labels == label]
            item.update({
                "colour_family": _colour_family(float(np.median(pixels[:, 0]))),
                "median_saturation": int(round(float(np.median(pixels[:, 1])))),
                "median_value": int(round(float(np.median(pixels[:, 2])))),
            })
        else:
            item["colour_family"] = "dark_achromatic"
        found.append(item)
    found.sort(key=lambda item: int(item["pixel_area"]), reverse=True)
    # Keep the planner payload bounded.  The largest components carry the
    # useful role-colour and layout evidence; dumping every antialiased speck
    # increases latency and makes semantic attribution less reliable.
    for index, item in enumerate(found[:10], 1):
        item["component_id"] = (
            f"colour-component-{index:02d}"
            if coloured else f"dark-component-{index:02d}"
        )
    return found[:10]


def inventory_frame_appearance(
    image_path: str | Path,
    *,
    surface_bounds: list[float] | tuple[float, float, float, float] | None = None,
    client_frame_bounds: list[int] | tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return domain-neutral appearance components from authoritative pixels."""
    path = Path(image_path)
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        return {"ok": False, "reason": "authoritative_frame_unavailable"}
    height, width = image.shape[:2]
    detected: dict[str, Any] | None = None
    if surface_bounds is None:
        try:
            detected = vision_cv.credible_work_surface_frame_bounds(
                base64.b64encode(path.read_bytes()).decode(),
                client_frame_bounds=client_frame_bounds,
            )
        except Exception:
            detected = None
        candidate = (
            detected.get("detected_work_surface_frame_bounds")
            if isinstance(detected, dict) and detected.get("ok") is True
            else None
        )
        surface_bounds = candidate if isinstance(candidate, list) else None
    if not isinstance(surface_bounds, (list, tuple)) or len(surface_bounds) != 4:
        surface_bounds = list(client_frame_bounds or [0, 0, 1000, 1000])
    x1, y1, x2, y2 = _pixel_box(surface_bounds, width, height)
    authority = np.zeros((height, width), dtype=np.uint8)
    authority[y1:y2, x1:x2] = 255
    # Do not let a high-contrast border become an appearance component.
    border = max(2, int(round(min(x2 - x1, y2 - y1) * 0.008)))
    if x2 - x1 > border * 2 and y2 - y1 > border * 2:
        authority[y1:y1 + border, x1:x2] = 0
        authority[y2 - border:y2, x1:x2] = 0
        authority[y1:y2, x1:x1 + border] = 0
        authority[y1:y2, x2 - border:x2] = 0

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    coloured_mask = (
        (saturation >= 72) & (value >= 45) & (authority > 0)
    ).astype(np.uint8) * 255
    coloured_mask = cv2.morphologyEx(
        coloured_mask,
        cv2.MORPH_OPEN,
        np.ones((2, 2), dtype=np.uint8),
    )
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dark_mask = (
        (gray <= 92) & (saturation <= 88) & (authority > 0)
    ).astype(np.uint8) * 255
    dark_mask = cv2.morphologyEx(
        dark_mask,
        cv2.MORPH_OPEN,
        np.ones((2, 2), dtype=np.uint8),
    )
    surface_area = max(1, (x2 - x1) * (y2 - y1))
    minimum_area = max(8, int(round(surface_area * 0.000035)))
    colours = _components(
        coloured_mask,
        hsv=hsv,
        frame_width=width,
        frame_height=height,
        minimum_area=minimum_area,
        coloured=True,
    )
    dark = _components(
        dark_mask,
        hsv=hsv,
        frame_width=width,
        frame_height=height,
        minimum_area=minimum_area,
        coloured=False,
    )
    return {
        "ok": True,
        "provider": "current_frame_pixel_appearance",
        "surface_bounds": [float(value) for value in surface_bounds],
        "surface_source": (
            "current_pixel_work_surface"
            if isinstance(detected, dict) and detected.get("ok") is True
            else "provided_or_client_authority"
        ),
        "semantic_ownership": "unassigned",
        "colour_components": colours,
        "dark_components": dark,
    }


def match_role_colours(
    roles: dict[str, dict[str, str]],
    appearance: dict[str, Any],
) -> dict[str, Any]:
    """Match explicit role colour words to pixel components without ownership guesses."""
    components = list(appearance.get("colour_components") or [])
    dark_components = list(appearance.get("dark_components") or [])
    matches: dict[str, Any] = {}
    for actor, attributes in (roles or {}).items():
        text = json.dumps(attributes, ensure_ascii=False).casefold()
        families = [
            family
            for family, terms in _ROLE_COLOUR_TERMS.items()
            if any(term.casefold() in text for term in terms)
        ]
        if not families:
            continue
        component_ids = [
            str(item.get("component_id"))
            for item in components
            if str(item.get("colour_family")) in families
        ]
        if "black" in families:
            component_ids.extend(
                str(item.get("component_id"))
                for item in dark_components
                if str(item.get("colour_family")) == "dark_achromatic"
            )
        matches[str(actor)] = {
            "declared_colour_families": families,
            "matching_current_component_ids": component_ids,
            "semantic_ownership": "must_be_decided_from_task_and_scene",
        }
    return matches
