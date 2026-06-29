"""Small image capture helpers shared by agent tools and the loop."""
from __future__ import annotations

import base64

import cv2

from . import screenshot


def capture_window_jpeg_b64(hwnd: int, max_side: int = 1120,
                            quality: int = 75) -> str:
    """Return the target window as a resized JPEG data payload.

    The helper never raises; callers can treat an empty string as "no image".
    """
    try:
        region = screenshot.window_region(hwnd)
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
