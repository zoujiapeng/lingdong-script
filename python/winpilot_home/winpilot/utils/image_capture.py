"""Small image capture helpers shared by agent tools and the loop."""
from __future__ import annotations

import base64

import cv2
import numpy as np

from . import screenshot


def capture_window_jpeg_b64(hwnd: int, max_side: int = 1120,
                            quality: int = 75,
                            region: screenshot.Region | None = None) -> str:
    """Return the target window as a resized JPEG data payload.

    The helper never raises; callers can treat an empty string as "no image".
    """
    try:
        region = (region or screenshot.window_region(hwnd)).clamp_to_screen()
        img = screenshot.capture(region)
        h, w = img.shape[:2]
        if max(h, w) > max_side:
            scale = max_side / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)))
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            return ""
        return base64.b64encode(buf.tobytes()).decode()
    except Exception:
        return ""


def jpeg_diff_ratio(image_a_b64: str, image_b_b64: str,
                    pixel_thresh: int = 12) -> float | None:
    """Return the changed-pixel fraction for two encoded JPEG frames."""
    try:
        raw_a = np.frombuffer(base64.b64decode(image_a_b64), dtype=np.uint8)
        raw_b = np.frombuffer(base64.b64decode(image_b_b64), dtype=np.uint8)
        image_a = cv2.imdecode(raw_a, cv2.IMREAD_COLOR)
        image_b = cv2.imdecode(raw_b, cv2.IMREAD_COLOR)
        if image_a is None or image_b is None:
            return None
        if image_a.shape != image_b.shape:
            return 1.0
        delta = cv2.absdiff(image_a, image_b)
        changed = int((delta.max(axis=2) > int(pixel_thresh)).sum())
        total = int(image_a.shape[0] * image_a.shape[1])
        return changed / total if total else None
    except Exception:
        return None


def jpeg_diff_ratio_in_normalized_roi(
    image_a_b64: str,
    image_b_b64: str,
    roi: tuple[float, float, float, float],
    pixel_thresh: int = 12,
) -> float | None:
    """Return the changed-pixel fraction inside one 0..1000 frame ROI.

    The two JPEGs are the exact submitted target-window frames, so normalized
    coordinates remain valid even when capture resized a large window.  A
    shape mismatch is treated as a total change, matching ``jpeg_diff_ratio``.
    """
    try:
        raw_a = np.frombuffer(base64.b64decode(image_a_b64), dtype=np.uint8)
        raw_b = np.frombuffer(base64.b64decode(image_b_b64), dtype=np.uint8)
        image_a = cv2.imdecode(raw_a, cv2.IMREAD_COLOR)
        image_b = cv2.imdecode(raw_b, cv2.IMREAD_COLOR)
        if image_a is None or image_b is None:
            return None
        if image_a.shape != image_b.shape:
            return 1.0
        height, width = image_a.shape[:2]
        left, top, right, bottom = (
            max(0.0, min(1000.0, float(value))) for value in roi
        )
        if right <= left or bottom <= top or width <= 0 or height <= 0:
            return None
        x1 = max(0, min(width - 1, int(np.floor(left / 1000.0 * width))))
        y1 = max(0, min(height - 1, int(np.floor(top / 1000.0 * height))))
        x2 = max(x1 + 1, min(width, int(np.ceil(right / 1000.0 * width))))
        y2 = max(y1 + 1, min(height, int(np.ceil(bottom / 1000.0 * height))))
        delta = cv2.absdiff(image_a[y1:y2, x1:x2], image_b[y1:y2, x1:x2])
        changed = int((delta.max(axis=2) > int(pixel_thresh)).sum())
        total = int(delta.shape[0] * delta.shape[1])
        return changed / total if total else None
    except Exception:
        return None
