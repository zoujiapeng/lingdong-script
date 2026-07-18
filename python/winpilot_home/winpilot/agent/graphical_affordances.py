"""Pixel-native hypotheses for small graphical GUI affordances.

UIA and OCR can name visible text, but they cannot explain an icon-only
control.  This module works only from the authoritative JPEG already shown to
the vision model.  It finds compact glyph-like regions and ranks repeated
layout/appearance families without assigning an application-specific meaning.

The output is deliberately advisory: a candidate box is a localization hint,
never proof that the pixels are clickable or that an action succeeded.
"""
from __future__ import annotations

import base64
from typing import Any

import cv2
import numpy as np


def _decode(image_b64: str) -> np.ndarray | None:
    try:
        raw = np.frombuffer(base64.b64decode(image_b64), dtype=np.uint8)
        image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    except Exception:
        return None
    if image is None or image.ndim != 3 or image.size == 0:
        return None
    return image


def _normalized(value: float, extent: int) -> int:
    return max(0, min(1000, int(round(
        float(value) * 1000.0 / max(1, int(extent) - 1)
    ))))


def _descriptor(gray: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    left, top, right, bottom = box
    patch = gray[top:bottom, left:right]
    resized = cv2.resize(patch, (20, 20), interpolation=cv2.INTER_AREA)
    values = resized.astype(np.float32)
    values -= float(values.mean())
    scale = float(values.std())
    if scale >= 1e-4:
        values /= scale
    return values.reshape(-1)


def _appearance_similarity(first: np.ndarray, second: np.ndarray) -> float:
    if first.size != second.size or first.size == 0:
        return 0.0
    return float(np.clip(np.mean(first * second), -1.0, 1.0))


def detect_graphical_affordances(
    image_b64: str,
    *,
    max_candidates: int = 14,
) -> list[dict[str, Any]]:
    """Return bounded, normalized icon/control hypotheses from real pixels."""
    image = _decode(image_b64)
    if image is None:
        return []
    height, width = image.shape[:2]
    shortest = max(1, min(height, width))
    min_side = max(7, min(16, int(round(shortest * 0.014))))
    max_side = max(48, min(112, int(round(shortest * 0.14))))

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # A fixed high threshold misses pale disabled/toggle glyphs.  Combining a
    # low and medium Canny pass keeps those while repeated-layout scoring below
    # suppresses most individual text strokes.
    low_edges = cv2.Canny(gray, 28, 92)
    medium_edges = cv2.Canny(gray, 62, 168)
    edges = cv2.bitwise_or(low_edges, medium_edges)
    edges = cv2.dilate(edges, np.ones((2, 2), np.uint8), iterations=1)
    contours, _ = cv2.findContours(
        edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
    )

    raw: list[dict[str, Any]] = []
    for contour in contours:
        left, top, box_width, box_height = cv2.boundingRect(contour)
        short_side = min(box_width, box_height)
        long_side = max(box_width, box_height)
        if short_side < min_side or long_side > max_side:
            continue
        aspect = box_width / max(1.0, float(box_height))
        if not 0.40 <= aspect <= 2.50:
            continue
        if (
            left <= int(width * 0.012)
            or top <= int(height * 0.012)
            or left + box_width >= int(width * 0.988)
            or top + box_height >= int(height * 0.958)
        ):
            continue
        roi = edges[top:top + box_height, left:left + box_width]
        ink_ratio = float(cv2.countNonZero(roi)) / max(1, roi.size)
        if not 0.055 <= ink_ratio <= 0.96:
            continue
        rect_area = max(1, box_width * box_height)
        contour_fill = float(cv2.contourArea(contour)) / rect_area
        if contour_fill < 0.015:
            continue
        right = left + box_width
        bottom = top + box_height
        raw.append({
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": box_width,
            "height": box_height,
            "cx": (left + right) / 2.0,
            "cy": (top + bottom) / 2.0,
            "ink_ratio": ink_ratio,
            "descriptor": _descriptor(gray, (left, top, right, bottom)),
        })

    # Keep the comparison budget bounded on text-heavy pages.  Large glyphs
    # and compact non-edge regions are more likely to be whole controls than a
    # single character fragment.
    raw.sort(key=lambda item: (
        -(item["width"] * item["height"]),
        abs(item["cx"] - width / 2.0),
    ))
    raw = raw[:180]

    for index, item in enumerate(raw):
        vertical_peers = 0
        appearance_peers = 0
        close_text_peers = 0
        spaced_row_peers = 0
        for other_index, other in enumerate(raw):
            if index == other_index:
                continue
            size_ratio = max(
                item["width"] / max(1.0, other["width"]),
                other["width"] / max(1.0, item["width"]),
                item["height"] / max(1.0, other["height"]),
                other["height"] / max(1.0, item["height"]),
            )
            if size_ratio > 2.1:
                continue
            dx = abs(item["cx"] - other["cx"])
            dy = abs(item["cy"] - other["cy"])
            column_tolerance = max(9.0, width * 0.014)
            row_tolerance = max(7.0, height * 0.012)
            if dx <= column_tolerance and dy >= max(
                14.0, min(item["height"], other["height"]) * 1.35,
            ):
                vertical_peers += 1
            if dy <= row_tolerance:
                mean_width = (item["width"] + other["width"]) / 2.0
                if dx <= mean_width * 1.65:
                    close_text_peers += 1
                elif dx >= mean_width * 2.2:
                    spaced_row_peers += 1
            if (
                max(dx, dy) >= 12.0
                and _appearance_similarity(
                    item["descriptor"], other["descriptor"],
                ) >= 0.64
            ):
                appearance_peers += 1

        score = 0.34
        score += min(0.30, vertical_peers * 0.11)
        score += min(0.24, appearance_peers * 0.09)
        score += min(0.12, spaced_row_peers * 0.035)
        if close_text_peers >= 2:
            # Characters form dense same-baseline runs even when the same
            # glyph repeats elsewhere.  Real action-bar icons are usually
            # separated by substantially more than one glyph width.
            score -= min(0.32, close_text_peers * 0.055)
        if item["width"] * item["height"] >= min_side * min_side * 2.0:
            score += 0.05
        item["score"] = score
        item["vertical_peers"] = vertical_peers
        item["appearance_peers"] = appearance_peers
        item["spaced_row_peers"] = spaced_row_peers

    ranked = [
        item for item in raw
        if item["score"] >= 0.47
        and (
            item["vertical_peers"]
            or item["appearance_peers"]
            or item["spaced_row_peers"] >= 2
        )
    ]
    ranked.sort(key=lambda item: (
        -item["score"],
        -item["vertical_peers"],
        item["cy"],
        item["cx"],
    ))

    kept: list[dict[str, Any]] = []
    for item in ranked:
        duplicate = False
        for current in kept:
            ix1 = max(item["left"], current["left"])
            iy1 = max(item["top"], current["top"])
            ix2 = min(item["right"], current["right"])
            iy2 = min(item["bottom"], current["bottom"])
            intersection = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            union = (
                item["width"] * item["height"]
                + current["width"] * current["height"]
                - intersection
            )
            if union > 0 and intersection / union >= 0.32:
                duplicate = True
                break
        if duplicate:
            continue
        kept.append(item)
        if len(kept) >= max(1, int(max_candidates)):
            break

    results: list[dict[str, Any]] = []
    for candidate_index, item in enumerate(kept, start=1):
        evidence: list[str] = []
        if item["vertical_peers"]:
            evidence.append("repeated_column")
        if item["appearance_peers"]:
            evidence.append("repeated_appearance")
        if item["spaced_row_peers"] >= 2:
            evidence.append("spaced_action_row")
        results.append({
            "candidate_id": f"g{candidate_index}",
            "role": "unlabeled_graphical_candidate",
            "center_frame": [
                _normalized(item["cx"], width),
                _normalized(item["cy"], height),
            ],
            "bbox_frame": [
                _normalized(item["left"], width),
                _normalized(item["top"], height),
                _normalized(item["right"], width),
                _normalized(item["bottom"], height),
            ],
            "pixel_evidence": evidence,
            "layout_family": (
                f"column_{int(round(_normalized(item['cx'], width) / 20.0) * 20)}"
                if item["vertical_peers"] else ""
            ),
            "localization_confidence": round(
                max(0.0, min(0.96, float(item["score"]))), 3,
            ),
        })
    return results
