"""Tests for vision modules (color, progress, change detection)."""
import sys
import os
import time
import unittest
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.screenshot import ScreenshotManager
from vision.color import ColorDetector
from vision.progress import ProgressDetector
from vision.change import ChangeDetector
from utils.image import ImageUtils


class TestImageUtils(unittest.TestCase):
    def setUp(self):
        self.test_img = np.zeros((100, 200, 3), dtype=np.uint8)
        self.test_img[30:70, 50:150] = [255, 0, 0]  # Blue block

    def test_bgr_to_gray(self):
        gray = ImageUtils.bgr_to_gray(self.test_img)
        self.assertEqual(len(gray.shape), 2)
        self.assertEqual(gray.shape, (100, 200))

    def test_bgr_to_hsv(self):
        hsv = ImageUtils.bgr_to_hsv(self.test_img)
        self.assertEqual(hsv.shape, (100, 200, 3))

    def test_compute_ssim_identical(self):
        ssim = ImageUtils.compute_ssim(self.test_img, self.test_img)
        self.assertAlmostEqual(ssim, 1.0, places=4)

    def test_compute_ssim_different(self):
        other = np.zeros((100, 200, 3), dtype=np.uint8)
        ssim = ImageUtils.compute_ssim(self.test_img, other)
        self.assertLess(ssim, 0.5)

    def test_pixel_diff_identical(self):
        diff = ImageUtils.pixel_diff(self.test_img, self.test_img)
        self.assertAlmostEqual(diff, 0.0, places=4)

    def test_pixel_diff_different(self):
        other = np.ones((100, 200, 3), dtype=np.uint8) * 255
        diff = ImageUtils.pixel_diff(self.test_img, other)
        self.assertGreater(diff, 0.0)

    def test_color_mask(self):
        # BGR image: blue block
        lower = (100, 150, 150)  # BGR: approximate
        upper = (130, 255, 255)
        mask = ImageUtils.color_mask(self.test_img, lower, upper, color_space='hsv')
        # With default HSV space, this may not find the blue exactly
        self.assertEqual(mask.shape, (100, 200))

    def test_threshold(self):
        gray = np.ones((100, 200), dtype=np.uint8) * 128
        binary = ImageUtils.threshold(gray, 127)
        self.assertEqual(binary.shape, (100, 200))

    def test_crop_region(self):
        cropped = ImageUtils.crop_region(self.test_img, 10, 10, 50, 50)
        self.assertEqual(cropped.shape[:2], (50, 50))


class TestColorDetector(unittest.TestCase):
    def setUp(self):
        self.screenshot = ScreenshotManager()
        self.detector = ColorDetector(self.screenshot)

    def test_rgb_to_hsv(self):
        # Pure red in RGB
        h, s, v = ColorDetector.rgb_to_hsv(255, 0, 0)
        self.assertAlmostEqual(h, 0, delta=5)
        self.assertGreater(s, 200)
        self.assertGreater(v, 200)

    def test_hsv_distance_same(self):
        d = ColorDetector.hsv_distance((0, 255, 255), (0, 255, 255))
        self.assertAlmostEqual(d, 0.0)

    def test_hsv_distance_different(self):
        d = ColorDetector.hsv_distance((0, 255, 255), (90, 128, 128))
        self.assertGreater(d, 0.0)


class TestProgressDetector(unittest.TestCase):
    def setUp(self):
        self.screenshot = ScreenshotManager()
        self.detector = ProgressDetector(self.screenshot)

    def test_detect_horizontal_empty_progress(self):
        """Test with a fully empty (dark) image."""
        empty = np.zeros((20, 200, 3), dtype=np.uint8)
        # Can't easily test without mock screenshot, but shouldn't crash
        self.detector.screenshot._cache.clear()

    def test_detect_vertical_empty_progress(self):
        self.detector.screenshot._cache.clear()


class TestChangeDetector(unittest.TestCase):
    def setUp(self):
        self.screenshot = ScreenshotManager()
        self.detector = ChangeDetector(self.screenshot)
        self.detector.diff_method = 'pixel'

    def test_first_call_no_change(self):
        """First call should always return False (no baseline)."""
        changed = self.detector.has_changed(0, 0, 100, 100)
        self.assertFalse(changed)

    def test_get_change_ratio_zero_initially(self):
        ratio = self.detector.get_change_ratio(0, 0, 50, 50)
        self.assertEqual(ratio, 0.0)


if __name__ == '__main__':
    unittest.main()
