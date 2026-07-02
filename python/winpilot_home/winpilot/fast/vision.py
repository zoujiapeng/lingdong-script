"""Cheap screen-delta and ROI helpers for the fast GUI loop."""
from __future__ import annotations

import time

import cv2
import numpy as np

from ..utils.screenshot import Region, capture
from .types import ScreenDelta


def roi_point_to_screen(region: Region, x: int, y: int) -> tuple[int, int]:
    """Convert local ROI coordinates to physical screen coordinates."""
    return region.left + int(x), region.top + int(y)


def screen_point_to_roi(region: Region, x: int, y: int) -> tuple[int, int]:
    """Convert physical screen coordinates into local ROI coordinates."""
    return int(x) - region.left, int(y) - region.top


def inflate_region(region: Region, pad: int) -> Region:
    return Region(
        region.left - int(pad),
        region.top - int(pad),
        region.width + int(pad) * 2,
        region.height + int(pad) * 2,
    ).clamp_to_screen()


def _merge_regions(regions: list[Region], pad: int = 6) -> list[Region]:
    merged: list[Region] = []
    for region in regions:
        candidate = inflate_region(region, pad)
        absorbed = False
        for i, existing in enumerate(merged):
            overlaps = not (
                candidate.right < existing.left
                or candidate.left > existing.right
                or candidate.bottom < existing.top
                or candidate.top > existing.bottom
            )
            if overlaps:
                left = min(existing.left, candidate.left)
                top = min(existing.top, candidate.top)
                right = max(existing.right, candidate.right)
                bottom = max(existing.bottom, candidate.bottom)
                merged[i] = Region(left, top, right - left, bottom - top).clamp_to_screen()
                absorbed = True
                break
        if not absorbed:
            merged.append(candidate)
    return merged


def changed_regions(
    before: np.ndarray,
    after: np.ndarray,
    root_region: Region | None = None,
    pixel_thresh: int = 12,
    min_area: int = 36,
    merge_pad: int = 10,
    max_regions: int = 8,
) -> ScreenDelta:
    """Return changed ROIs between same-sized BGR frames.

    Regions are returned in physical screen coordinates when root_region is
    provided, otherwise in frame-local coordinates.
    """
    started = time.monotonic()
    if before.shape != after.shape:
        region = root_region or Region(0, 0, after.shape[1], after.shape[0])
        return ScreenDelta(True, 1.0, [region], int((time.monotonic() - started) * 1000))

    delta = cv2.absdiff(before, after)
    mask = (delta.max(axis=2) > int(pixel_thresh)).astype("uint8") * 255
    changed = int(mask.sum() // 255)
    total = int(mask.shape[0] * mask.shape[1])
    ratio = changed / total if total else 0.0
    if changed == 0:
        return ScreenDelta(False, 0.0, [], int((time.monotonic() - started) * 1000))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions: list[Region] = []
    offset_left = root_region.left if root_region else 0
    offset_top = root_region.top if root_region else 0
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h < int(min_area):
            continue
        regions.append(Region(offset_left + x, offset_top + y, w, h))
    regions = sorted(_merge_regions(regions, pad=merge_pad), key=lambda r: r.width * r.height, reverse=True)
    elapsed = int((time.monotonic() - started) * 1000)
    return ScreenDelta(bool(regions), ratio, regions[:max_regions], elapsed)


def capture_delta(
    region: Region | None = None,
    wait_s: float = 0.12,
    pixel_thresh: int = 12,
) -> ScreenDelta:
    """Capture two frames and return a cheap change summary."""
    root = region.clamp_to_screen() if region else None
    before = capture(root)
    time.sleep(max(0.01, float(wait_s)))
    after = capture(root)
    return changed_regions(before, after, root_region=root, pixel_thresh=pixel_thresh)
