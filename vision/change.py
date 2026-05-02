"""Region change detection — detect when screen regions change or stabilize."""
import time
from collections import deque
from typing import Optional
import cv2
import numpy as np
from core.screenshot import ScreenshotManager
from utils.image import ImageUtils


class ChangeDetector:
    """Detect changes, stability, and animation end in screen regions.

    Features:
    - Region change detection (SSIM or pixel diff)
    - Region stability detection (N consecutive frames below threshold)
    - Animation end detection (frame diff approaching zero)
    """

    def __init__(self, screenshot_mgr: ScreenshotManager,
                 diff_method: str = 'pixel',
                 change_threshold: float = 0.05,
                 stable_window: int = 5,
                 stable_threshold: float = 0.02):
        """
        Args:
            screenshot_mgr: Shared screenshot manager.
            diff_method: 'pixel' for pixel diff, 'ssim' for SSIM.
            change_threshold: Fraction of pixels changed to trigger "changed".
            stable_window: Number of consecutive frames for stability check.
            stable_threshold: Max diff to consider "stable".
        """
        self.screenshot = screenshot_mgr
        self.diff_method = diff_method
        self.change_threshold = change_threshold
        self.stable_window = stable_window
        self.stable_threshold = stable_threshold
        self._history: dict[str, deque] = {}

    def _region_key(self, x: int, y: int, w: int, h: int) -> str:
        return f"{x}_{y}_{w}_{h}"

    def _get_diff(self, img1: np.ndarray, img2: np.ndarray) -> float:
        """Compute difference between two images using configured method."""
        if self.diff_method == 'ssim':
            return 1.0 - ImageUtils.compute_ssim(img1, img2)
        else:
            return ImageUtils.pixel_diff(img1, img2)

    def has_changed(self, x: int, y: int, w: int, h: int,
                    threshold: Optional[float] = None) -> bool:
        """Check if a region has changed compared to previous capture.

        Stores last capture internally. Two-phase: first call captures baseline,
        second call compares and updates.

        Args:
            x, y, w, h: Region.
            threshold: Pixel diff threshold. Uses instance default if None.

        Returns:
            True if the region has changed beyond threshold.
        """
        key = self._region_key(x, y, w, h)
        current = self.screenshot.capture_gray(region=(x, y, w, h))

        if key not in self._history:
            self._history[key] = deque(maxlen=self.stable_window)
            self._history[key].append(current)
            return False

        prev = self._history[key][-1]
        diff = self._get_diff(prev, current)
        self._history[key].append(current)

        effective_threshold = threshold if threshold is not None else self.change_threshold
        return diff > effective_threshold

    def wait_for_change(self, x: int, y: int, w: int, h: int,
                        timeout: float = 10.0,
                        threshold: Optional[float] = None,
                        poll_interval: float = 0.2) -> bool:
        """Wait until a region changes, or timeout.

        Returns:
            True if change detected within timeout, False if timed out.
        """
        key = self._region_key(x, y, w, h)
        baseline = self.screenshot.capture_gray(region=(x, y, w, h), force=True)
        self._history[key] = deque(maxlen=self.stable_window)
        self._history[key].append(baseline)

        start = time.time()
        while time.time() - start < timeout:
            current = self.screenshot.capture_gray(region=(x, y, w, h), force=True)
            diff = self._get_diff(baseline, current)
            self._history[key].append(current)

            effective_threshold = threshold if threshold is not None else self.change_threshold
            if diff > effective_threshold:
                return True

            time.sleep(poll_interval)

        return False

    def wait_for_stable(self, x: int, y: int, w: int, h: int,
                        timeout: float = 10.0,
                        stable_frames: Optional[int] = None,
                        threshold: Optional[float] = None,
                        poll_interval: float = 0.15) -> bool:
        """Wait until a region becomes stable (N consecutive frames similar).

        Args:
            x, y, w, h: Region.
            timeout: Max wait time.
            stable_frames: Number of stable frames required. Uses instance default if None.
            threshold: Max diff for "stable". Uses instance default if None.
            poll_interval: Seconds between captures.

        Returns:
            True if stable within timeout, False if timed out.
        """
        n_frames = stable_frames if stable_frames is not None else self.stable_window
        eff_threshold = threshold if threshold is not None else self.stable_threshold

        key = f"stable_{self._region_key(x, y, w, h)}"
        frame_buffer = deque(maxlen=n_frames)
        start = time.time()

        while time.time() - start < timeout:
            current = self.screenshot.capture_gray(region=(x, y, w, h), force=True)
            frame_buffer.append(current)

            if len(frame_buffer) == n_frames:
                # Check all consecutive differences
                all_stable = True
                for i in range(1, len(frame_buffer)):
                    diff = self._get_diff(frame_buffer[i - 1], frame_buffer[i])
                    if diff > eff_threshold:
                        all_stable = False
                        break
                if all_stable:
                    return True

            time.sleep(poll_interval)

        return False

    def wait_for_animation_end(self, x: int, y: int, w: int, h: int,
                               timeout: float = 15.0,
                               stable_duration: float = 0.5,
                               threshold: Optional[float] = None,
                               poll_interval: float = 0.1) -> bool:
        """Wait for animation/transition to finish in a region.

        Uses a sliding window: checks if all frames within stable_duration
        are below the diff threshold.

        Args:
            x, y, w, h: Region.
            timeout: Max wait time.
            stable_duration: How long the region must remain stable (seconds).
            threshold: Max diff threshold.
            poll_interval: Seconds between captures.

        Returns:
            True if animation ended within timeout, False if timed out.
        """
        eff_threshold = threshold if threshold is not None else self.stable_threshold
        window_size = max(2, int(stable_duration / poll_interval))

        key = f"anim_{self._region_key(x, y, w, h)}"
        frame_buffer = deque(maxlen=window_size)
        start = time.time()

        while time.time() - start < timeout:
            current = self.screenshot.capture_gray(region=(x, y, w, h), force=True)
            frame_buffer.append(current)

            if len(frame_buffer) == window_size:
                # Compute all pairwise diffs within window
                diffs = []
                for i in range(1, len(frame_buffer)):
                    d = self._get_diff(frame_buffer[i - 1], frame_buffer[i])
                    diffs.append(d)

                # Animation end = all recent diffs are near zero
                if all(d <= eff_threshold for d in diffs):
                    return True

            # Also emit current diff level for debugging
            time.sleep(poll_interval)

        return False

    def get_change_ratio(self, x: int, y: int, w: int, h: int) -> float:
        """Get the instantaneous change ratio for a region.

        Compares current screenshot with the last captured frame.
        If no prior frame, captures baseline and returns 0.

        Returns:
            Change ratio (0-1).
        """
        key = self._region_key(x, y, w, h)
        current = self.screenshot.capture_gray(region=(x, y, w, h), force=True)

        if key not in self._history or len(self._history[key]) == 0:
            self._history[key] = deque(maxlen=self.stable_window)
            self._history[key].append(current)
            return 0.0

        prev = self._history[key][-1]
        diff = self._get_diff(prev, current)
        self._history[key].append(current)
        return diff

    def clear_history(self, region: Optional[tuple[int, int, int, int]] = None):
        """Clear stored frame history."""
        if region:
            key = self._region_key(*region)
            self._history.pop(key, None)
            self._history.pop(f"stable_{key}", None)
            self._history.pop(f"anim_{key}", None)
        else:
            self._history.clear()
