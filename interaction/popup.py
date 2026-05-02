"""Auto popup closer — scan for popup close buttons and dismiss them."""
import time
from typing import Optional
import pyautogui
from core.screenshot import ScreenshotManager
from recognition.ocr import OCRRecognizer
from recognition.matcher import MultiScaleMatcher


class PopupCloser:
    """Detect and close popup dialogs automatically.

    Detection strategies:
    1. OCR: search for close-related keywords ("X", "关闭", "确认", "取消", "Close")
    2. Template matching: match close button templates
    3. Positional: scan common popup X locations (top-right corner of screen regions)
    """

    # Common close/cancel text patterns across languages
    CLOSE_KEYWORDS = ['x', '×', 'close', '关闭', '取消', 'dismiss', '忽略',
                      'confirm', '确定', 'yes', '是', 'no', '否', 'ok',
                      'got it', '知道了', 'i understand', '我明白了']

    def __init__(self, screenshot_mgr: ScreenshotManager,
                 ocr: Optional[OCRRecognizer] = None,
                 matcher: Optional[MultiScaleMatcher] = None):
        self.screenshot = screenshot_mgr
        self.ocr = ocr or OCRRecognizer(screenshot_mgr)
        self.matcher = matcher or MultiScaleMatcher(screenshot_mgr)

    def find_close_buttons(self, region: Optional[tuple[int, int, int, int]] = None,
                           templates: Optional[list[str]] = None) -> list[dict]:
        """Find all potential close/cancel buttons on screen.

        Args:
            region: Screen region to search. None = full screen.
            templates: Optional template paths for close buttons.

        Returns:
            List of {center_x, center_y, text, confidence, method} dicts.
        """
        candidates = []

        # Strategy 1: OCR text matching
        all_texts = self.ocr.recognize(region=region)
        for text, confidence, box in all_texts:
            if any(kw in text.lower() for kw in self.CLOSE_KEYWORDS):
                candidates.append({
                    'center_x': box[0] + box[2] // 2,
                    'center_y': box[1] + box[3] // 2,
                    'text': text,
                    'confidence': confidence,
                    'method': 'ocr',
                    'region': box,
                })

        # Strategy 2: Template matching for close button images
        if templates:
            for tpl_path in templates:
                found, positions, scores = self.matcher.find_best(
                    tpl_path, region=region, similarity=0.8
                )
                if found:
                    for (cx, cy), score in zip(positions, scores):
                        candidates.append({
                            'center_x': cx,
                            'center_y': cy,
                            'text': '[template]',
                            'confidence': score,
                            'method': 'template',
                        })

        # Strategy 3: Scan top-right areas for small buttons
        screen_w, screen_h = pyautogui.size()
        scan_regions = []

        if region:
            rx, ry, rw, rh = region
            # Top-right corner of the specified region
            scan_regions.append((rx + rw - 60, ry, 60, 40))
        else:
            # Common popup locations (center of screen)
            cw, ch = 400, 300
            cx, cy = screen_w // 2 - cw // 2, screen_h // 2 - ch // 2
            scan_regions.append((cx + cw - 50, cy, 50, 40))

        # (Strategy 3 would scan these regions - simplified for now)

        # Deduplicate by position
        unique = []
        seen = set()
        for c in sorted(candidates, key=lambda x: x.get('confidence', 0), reverse=True):
            pos = (c['center_x'] // 10, c['center_y'] // 10)
            if pos not in seen:
                seen.add(pos)
                unique.append(c)

        return unique

    def close_all(self, templates: Optional[list[str]] = None,
                  region: Optional[tuple[int, int, int, int]] = None,
                  max_attempts: int = 20,
                  delay: float = 0.3) -> int:
        """Find and close all visible popups.

        Args:
            templates: Template paths for close buttons.
            region: Screen region.
            max_attempts: Max clicks.
            delay: Delay between close attempts.

        Returns:
            Number of popups closed.
        """
        closed = 0

        for _ in range(max_attempts):
            buttons = self.find_close_buttons(region, templates)

            if not buttons:
                break

            clicked = False
            for btn in buttons:
                try:
                    pyautogui.click(btn['center_x'], btn['center_y'])
                    closed += 1
                    clicked = True
                    time.sleep(delay)
                    break  # Re-scan after each click
                except Exception:
                    continue

            if not clicked:
                break

        return closed

    def close_by_text(self, text: str,
                      region: Optional[tuple[int, int, int, int]] = None,
                      fuzzy: bool = True) -> bool:
        """Close a specific popup by button text.

        Args:
            text: Button text to click (e.g., "关闭", "确定", "Close").
            region: Screen region.
            fuzzy: Fuzzy text matching.

        Returns:
            True if clicked successfully.
        """
        matches = self.ocr.find_text(text, region, fuzzy=fuzzy)
        if matches:
            _, conf, box = matches[0]
            cx = box[0] + box[2] // 2
            cy = box[1] + box[3] // 2
            pyautogui.click(cx, cy)
            return True
        return False
