"""Progress bar detection module — detect fill percentage of progress bars."""
from typing import Optional
import cv2
import numpy as np
from core.screenshot import ScreenshotManager
from utils.image import ImageUtils


class ProgressDetector:
    """Detect progress bar fill percentage.

    Methods:
    - Horizontal scan: scan rows for filled pixel ratio
    - Vertical scan: scan columns for filled pixel ratio
    - Color segmentation: use color ranges to distinguish filled vs empty
    """

    def __init__(self, screenshot_mgr: ScreenshotManager):
        self.screenshot = screenshot_mgr

    def detect_horizontal(self, x: int, y: int, w: int, h: int,
                          filled_color_range: Optional[tuple[tuple[int, int, int],
                                                           tuple[int, int, int]]] = None,
                          scan_rows: Optional[int] = None) -> float:
        """Detect progress by horizontal (left-to-right) scanning.

        Args:
            x, y, w, h: Progress bar region.
            filled_color_range: ((L_H, L_S, L_V), (U_H, U_S, U_V)) in HSV.
                                If None, uses brightness threshold.
            scan_rows: Number of rows to sample. None = all rows.

        Returns:
            Fill percentage (0.0 - 1.0).
        """
        img = self.screenshot.capture(region=(x, y, w, h))
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        if scan_rows and scan_rows < h:
            step = max(1, h // scan_rows)
            indices = np.arange(0, h, step)
            hsv = hsv[indices]

        if filled_color_range:
            lower = np.array(filled_color_range[0], dtype=np.uint8)
            upper = np.array(filled_color_range[1], dtype=np.uint8)
            mask = cv2.inRange(hsv, lower, upper)
        else:
            # Use brightness threshold (V channel)
            _, mask = cv2.threshold(hsv[:, :, 2], 100, 255, cv2.THRESH_BINARY)

        # For each row, find the rightmost filled pixel
        row_fills = []
        for row_idx in range(mask.shape[0]):
            row = mask[row_idx, :]
            filled_indices = np.where(row > 0)[0]
            if len(filled_indices) > 0:
                # Rightmost filled pixel
                rightmost = filled_indices[-1]
                fill_ratio = (rightmost + 1) / mask.shape[1]
                row_fills.append(fill_ratio)

        if not row_fills:
            return 0.0

        return float(np.mean(row_fills))

    def detect_vertical(self, x: int, y: int, w: int, h: int,
                        filled_color_range: Optional[tuple] = None,
                        scan_cols: Optional[int] = None) -> float:
        """Detect progress by vertical (bottom-to-top) scanning.

        Args:
            x, y, w, h: Progress bar region.
            filled_color_range: ((L_H, L_S, L_V), (U_H, U_S, U_V)) in HSV.
            scan_cols: Number of columns to sample.

        Returns:
            Fill percentage (0.0 - 1.0).
        """
        img = self.screenshot.capture(region=(x, y, w, h))
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        if scan_cols and scan_cols < w:
            step = max(1, w // scan_cols)
            indices = np.arange(0, w, step)
            hsv = hsv[:, indices]

        if filled_color_range:
            lower = np.array(filled_color_range[0], dtype=np.uint8)
            upper = np.array(filled_color_range[1], dtype=np.uint8)
            mask = cv2.inRange(hsv, lower, upper)
        else:
            _, mask = cv2.threshold(hsv[:, :, 2], 100, 255, cv2.THRESH_BINARY)

        col_fills = []
        for col_idx in range(mask.shape[1]):
            col = mask[:, col_idx]
            filled_indices = np.where(col > 0)[0]
            if len(filled_indices) > 0:
                # Bottommost filled pixel (for bottom-up bars)
                bottom = filled_indices[-1]
                fill_ratio = (bottom + 1) / mask.shape[0]
                col_fills.append(fill_ratio)

        if not col_fills:
            return 0.0

        return float(np.mean(col_fills))

    def detect_color_ratio(self, x: int, y: int, w: int, h: int,
                           filled_color: tuple[int, int, int],
                           empty_color: tuple[int, int, int],
                           tolerance: int = 30) -> float:
        """Detect progress by ratio of filled-colored pixels vs empty-colored.

        Args:
            x, y, w, h: Region.
            filled_color: HSV of filled portion.
            empty_color: HSV of empty portion.
            tolerance: HSV distance tolerance.

        Returns:
            Estimated fill percentage (0.0 - 1.0).
        """
        img = self.screenshot.capture(region=(x, y, w, h))
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        # Build masks for filled and empty colors
        from .color import ColorDetector
        cd = ColorDetector(self.screenshot)

        filled_mask = np.zeros((h, w), dtype=bool)
        empty_mask = np.zeros((h, w), dtype=bool)

        for row in range(h):
            for col in range(w):
                pixel_hsv = tuple(hsv[row, col])
                if cd.hsv_distance(pixel_hsv, filled_color) < tolerance:
                    filled_mask[row, col] = True
                if cd.hsv_distance(pixel_hsv, empty_color) < tolerance:
                    empty_mask[row, col] = True

        filled_count = np.sum(filled_mask)
        empty_count = np.sum(empty_mask)
        total = filled_count + empty_count

        if total == 0:
            return 0.0

        return filled_count / total

    def detect_text_percentage(self, x: int, y: int, w: int, h: int) -> Optional[float]:
        """Try to extract percentage from text within the region using OCR.

        This is a placeholder that would use OCR to read percentage text.
        Falls back to color-based detection if OCR is unavailable.

        Returns:
            Percentage (0-100) or None if no percentage text found.
        """
        try:
            from recognition.ocr import OCRRecognizer
            ocr = OCRRecognizer(self.screenshot)
            img = self.screenshot.capture(region=(x, y, w, h))
            results = ocr.recognize(img)

            import re
            for text, conf, box in results:
                matches = re.findall(r'(\d+)\s*%', text)
                if matches:
                    return float(matches[-1])
            return None
        except Exception:
            return None
