"""Color detection module — detect colors in screen regions using HSV distance."""
from typing import Optional, Union
import cv2
import numpy as np
from core.screenshot import ScreenshotManager


class ColorDetector:
    """Detect colors in screen regions using HSV color space.

    Features:
    - Average HSV of a region
    - Color distance measurement (HSV-weighted)
    - Color presence detection with tolerance
    - Multiple color matching
    """

    # Default HSV weights: hue matters most, then saturation, then value
    HSV_WEIGHTS = (1.0, 0.5, 0.3)

    def __init__(self, screenshot_mgr: ScreenshotManager):
        self.screenshot = screenshot_mgr

    @staticmethod
    def rgb_to_hsv(r: int, g: int, b: int) -> tuple[int, int, int]:
        """Convert single RGB value to HSV."""
        color = np.uint8([[ [r, g, b] ]])
        hsv = cv2.cvtColor(color, cv2.COLOR_RGB2HSV)
        return tuple(map(int, hsv[0][0]))

    @staticmethod
    def hsv_distance(hsv1: tuple[int, int, int], hsv2: tuple[int, int, int],
                     weights: Optional[tuple[float, float, float]] = None) -> float:
        """Compute weighted HSV distance.

        H is circular (0-180), so we take the minimum angular distance.
        """
        if weights is None:
            weights = ColorDetector.HSV_WEIGHTS

        h_dist = min(abs(hsv1[0] - hsv2[0]), 180 - abs(hsv1[0] - hsv2[0]))
        s_dist = abs(hsv1[1] - hsv2[1])
        v_dist = abs(hsv1[2] - hsv2[2])

        return math.sqrt(
            (weights[0] * h_dist) ** 2 +
            (weights[1] * s_dist) ** 2 +
            (weights[2] * v_dist) ** 2
        )

    def get_region_hsv(self, x: int, y: int, w: int, h: int,
                       force: bool = False) -> tuple[float, float, float]:
        """Get average HSV value of a screen region.

        Returns:
            (H_avg, S_avg, V_avg) as floats.
        """
        img = self.screenshot.capture(region=(x, y, w, h), force=force)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        avg_h = float(np.mean(hsv[:, :, 0]))
        avg_s = float(np.mean(hsv[:, :, 1]))
        avg_v = float(np.mean(hsv[:, :, 2]))
        return (avg_h, avg_s, avg_v)

    def get_pixel_hsv(self, x: int, y: int, force: bool = False) -> tuple[int, int, int]:
        """Get HSV of a single pixel."""
        img = self.screenshot.capture(region=(x, y, 1, 1), force=force)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        return tuple(map(int, hsv[0][0]))

    def check_color(self, x: int, y: int, w: int, h: int,
                    target_hsv: tuple[int, int, int],
                    tolerance: float = 30.0,
                    threshold_fraction: float = 0.5) -> bool:
        """Check if a region contains a target color within tolerance.

        Args:
            x, y, w, h: Screen region.
            target_hsv: Target color in HSV.
            tolerance: Max allowed HSV distance.
            threshold_fraction: Fraction of pixels that must match.

        Returns:
            True if enough pixels match the target color.
        """
        img = self.screenshot.capture(region=(x, y, w, h))
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        dh = np.minimum(np.abs(hsv[:, :, 0].astype(np.int16) - target_hsv[0]),
                        180 - np.abs(hsv[:, :, 0].astype(np.int16) - target_hsv[0]))
        ds = np.abs(hsv[:, :, 1].astype(np.int16) - target_hsv[1])
        dv = np.abs(hsv[:, :, 2].astype(np.int16) - target_hsv[2])

        distances = np.sqrt(
            (self.HSV_WEIGHTS[0] * dh) ** 2 +
            (self.HSV_WEIGHTS[1] * ds) ** 2 +
            (self.HSV_WEIGHTS[2] * dv) ** 2
        )

        matching = np.sum(distances <= tolerance)
        total = distances.size
        return (matching / total) >= threshold_fraction

    def find_color_regions(self, x: int, y: int, w: int, h: int,
                           target_hsv: tuple[int, int, int],
                           tolerance: float = 30.0,
                           min_area: int = 50) -> list[dict]:
        """Find connected regions of a specific color.

        Returns:
            List of {x, y, w, h, center_x, center_y, area} dicts.
        """
        img = self.screenshot.capture(region=(x, y, w, h))
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        lower = np.array([
            max(0, target_hsv[0] - tolerance),
            max(0, target_hsv[1] - tolerance),
            max(0, target_hsv[2] - tolerance),
        ], dtype=np.uint8)
        upper = np.array([
            min(180, target_hsv[0] + tolerance),
            min(255, target_hsv[1] + tolerance),
            min(255, target_hsv[2] + tolerance),
        ], dtype=np.uint8)

        mask = cv2.inRange(hsv, lower, upper)
        from utils.image import ImageUtils
        components = ImageUtils.extract_connected_components(mask, min_area=min_area)

        # Offset coordinates back to screen space
        for comp in components:
            comp['x'] += x
            comp['y'] += y
            comp['center_x'] += x
            comp['center_y'] += y

        return components


import math  # noqa: E402
