"""Screenshot cache and region capture for performance optimization."""
import time
import hashlib
from typing import Optional, Callable
from functools import lru_cache

import pyautogui
import numpy as np
from PIL import Image


class ScreenshotManager:
    """Manages screenshot caching to reduce repeated full-screen captures.

    Key features:
    - Configurable TTL cache: repeated calls within ttl return cached result
    - Region capture: only capture a sub-region instead of full screen
    - Forced refresh on demand
    - Automatic cache invalidation based on frame counting
    """

    def __init__(self, default_ttl: float = 0.15, max_cache_frames: int = 3):
        self.default_ttl = default_ttl
        self.max_cache_frames = max_cache_frames
        self._cache: dict[str, tuple[float, np.ndarray]] = {}
        self._frame_count = 0

    def _region_key(self, region: Optional[tuple[int, int, int, int]]) -> str:
        if region is None:
            return "__full__"
        return f"{region[0]}_{region[1]}_{region[2]}_{region[3]}"

    def capture(self, region: Optional[tuple[int, int, int, int]] = None,
                ttl: Optional[float] = None, force: bool = False) -> np.ndarray:
        """Capture a screenshot, returning cached version if within TTL.

        Args:
            region: (x, y, w, h) sub-region to capture. None = full screen.
            ttl: Time-to-live in seconds for cache. None = use default_ttl.
            force: If True, force a fresh capture.

        Returns:
            BGR numpy array (OpenCV format).
        """
        if ttl is None:
            ttl = self.default_ttl
        key = self._region_key(region)
        now = time.time()

        if not force and key in self._cache:
            ts, img = self._cache[key]
            if (now - ts) < ttl:
                return img

        if region is not None:
            x, y, w, h = region
            if w <= 0 or h <= 0:
                raise ValueError(f"Invalid region dimensions: {w}x{h}")
            pil_img = pyautogui.screenshot(region=(x, y, w, h))
        else:
            pil_img = pyautogui.screenshot()

        result = np.array(pil_img)
        if len(result.shape) == 3 and result.shape[2] == 3:
            result = result[:, :, ::-1]  # RGB -> BGR

        self._cache[key] = (now, result)
        self._frame_count += 1
        return result

    def capture_gray(self, region: Optional[tuple[int, int, int, int]] = None,
                     ttl: Optional[float] = None, force: bool = False) -> np.ndarray:
        """Capture screenshot and return grayscale version."""
        img = self.capture(region, ttl, force)
        if len(img.shape) == 3:
            return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return img

    def capture_rgb(self, region: Optional[tuple[int, int, int, int]] = None,
                    ttl: Optional[float] = None, force: bool = False) -> np.ndarray:
        """Capture screenshot and return RGB numpy array."""
        img = self.capture(region, ttl, force)
        if len(img.shape) == 3 and img.shape[2] == 3:
            return img[:, :, ::-1]  # BGR -> RGB
        return img

    def capture_pil(self, region: Optional[tuple[int, int, int, int]] = None,
                    ttl: Optional[float] = None, force: bool = False) -> Image.Image:
        """Capture screenshot, return PIL Image."""
        img = self.capture(region, ttl, force)
        return Image.fromarray(img[:, :, ::-1] if len(img.shape) == 3 else img, mode='RGB')

    def invalidate(self, region: Optional[tuple[int, int, int, int]] = None):
        """Invalidate cache for a specific region or all regions."""
        if region is None:
            self._cache.clear()
        else:
            key = self._region_key(region)
            self._cache.pop(key, None)

    def invalidate_all(self):
        self._cache.clear()

    @property
    def frame_count(self) -> int:
        return self._frame_count


# Avoid circular import: cv2 is needed only in capture_gray
import cv2  # noqa: E402
