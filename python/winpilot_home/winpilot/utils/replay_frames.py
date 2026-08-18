"""Tiny on-disk cache for exact model-view click frames."""
from __future__ import annotations

import base64
import re

from ..config import CONFIG, TRACES_DIR


def _save_pending_frame(
    *,
    run_id: str,
    decision_id: str,
    image_b64: str,
    prefix: str,
) -> str | None:
    """Save a JPEG only while the run's trace file is actually open on disk."""

    run_dir = TRACES_DIR / run_id
    if not run_dir.is_dir() or not (TRACES_DIR / f"{run_id}.jsonl").is_file():
        return None
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", decision_id).strip("_")
    if not safe_id:
        return None
    try:
        payload = base64.b64decode(image_b64, validate=True)
        if not payload.startswith(b"\xff\xd8"):
            return None
        name = f"{prefix}{safe_id[:160]}.jpg"
        relative = f"{run_id}/{name}"
        (TRACES_DIR / relative).write_bytes(payload)
        return relative
    except (OSError, ValueError):
        return None


def save_pending_click_frame(
    *,
    run_id: str,
    decision_id: str,
    image_b64: str,
) -> str | None:
    """Save the exact model view associated with one pointer click."""

    if CONFIG.get(
        "vnext", "save_click_reference_frames", default=False,
    ) is not True:
        return None

    return _save_pending_frame(
        run_id=run_id,
        decision_id=decision_id,
        image_b64=image_b64,
        prefix="click_frame_",
    )


def save_pending_view_frame(
    *,
    run_id: str,
    decision_id: str,
    image_b64: str,
) -> str | None:
    """Save a copied model view temporarily for possible flow extraction."""

    return _save_pending_frame(
        run_id=run_id,
        decision_id=decision_id,
        image_b64=image_b64,
        prefix="view_frame_",
    )
