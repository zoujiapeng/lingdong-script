"""Cheap semantic analysis for screen-delta ROI crops.

This layer deliberately excludes UIA and VLM. It is safe to run from the
per-hwnd perception daemon whenever Level-0 differencing reports a useful
local change.
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np

from ..config import CONFIG
from ..utils.log import logger
from ..utils.screenshot import Region
from . import ocr, win32
from .fusion import Snapshot
from .model import UIElement
from .ocr_lines import merge_line_elements

EvidenceCrop = tuple[np.ndarray, Region]


def regions_intersect(left: Region, right: Region) -> bool:
    return (
        left.left < right.right
        and left.right > right.left
        and left.top < right.bottom
        and left.bottom > right.top
    )


def element_region(element: UIElement) -> Region:
    return Region(
        int(element.left),
        int(element.top),
        max(1, int(element.width)),
        max(1, int(element.height)),
    )


def union_regions(regions: list[Region]) -> Region:
    left = min(region.left for region in regions)
    top = min(region.top for region in regions)
    right = max(region.right for region in regions)
    bottom = max(region.bottom for region in regions)
    return Region(left, top, right - left, bottom - top)


def dedupe_elements(elements: list[UIElement]) -> list[UIElement]:
    kept: list[UIElement] = []
    for candidate in elements:
        duplicate = False
        for existing in kept:
            if candidate.iou(existing) < 0.5:
                continue
            if (
                not candidate.text
                or candidate.text == existing.text
                or candidate.text in existing.text
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(candidate)
    return kept


def analyze_changed_regions(
    hwnd: int,
    crops: list[EvidenceCrop],
    metadata: dict[str, Any],
) -> Snapshot:
    """Produce a compact OCR/Win32 snapshot from already captured ROI images."""
    regions = [region for _image, region in crops]
    if not regions:
        raise ValueError("incremental perception requires changed ROI crops")
    try:
        title = win32._window_text(hwnd)
    except Exception:
        title = ""
    snapshot = Snapshot(hwnd=hwnd, title=title, region=union_regions(regions))
    enabled = set(CONFIG.enabled_dimensions())
    elements: list[UIElement] = []

    started = time.monotonic()
    win32_elements: list[UIElement] = []
    if "win32" in enabled:
        try:
            win32_elements = [
                element for element in win32.collect(hwnd)
                if any(regions_intersect(element_region(element), region) for region in regions)
            ]
            for element in win32_elements:
                if "incremental_perception" not in element.states:
                    element.states.append("incremental_perception")
        except Exception:
            logger.debug("incremental Win32 collection failed", exc_info=True)
    snapshot.dim_stats["win32"] = {
        "count": len(win32_elements),
        "ms": int((time.monotonic() - started) * 1000),
        "scope": "changed_roi_prefetch",
    }
    elements.extend(win32_elements)

    started = time.monotonic()
    ocr_elements: list[UIElement] = []
    if (
        "ocr" in enabled
        and bool(CONFIG.get("fusion", "predictive_ocr_enabled", default=True))
    ):
        for image, region in crops:
            recognized = ocr.recognize(
                image,
                origin=(region.left, region.top),
            )
            reconstructed = [*recognized, *merge_line_elements(recognized)]
            for element in reconstructed:
                for state in ("predictive_ocr", "incremental_perception"):
                    if state not in element.states:
                        element.states.append(state)
                if metadata.get("transient") and "transient_action_evidence" not in element.states:
                    element.states.append("transient_action_evidence")
            ocr_elements.extend(reconstructed)
    snapshot.dim_stats["ocr"] = {
        "count": len(ocr_elements),
        "ms": int((time.monotonic() - started) * 1000),
        "scope": "changed_roi_prefetch",
        "transient": bool(metadata.get("transient")),
    }
    elements.extend(ocr_elements)

    snapshot.elements = dedupe_elements(elements)
    for index, element in enumerate(snapshot.elements, start=1):
        element.elem_id = index
    return snapshot
