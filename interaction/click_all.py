"""Batch click — find and click ALL matching elements on screen."""
import time
from typing import Optional
import pyautogui
from core.screenshot import ScreenshotManager
from recognition.ui_element import UIElementLocator
from recognition.matcher import MultiScaleMatcher


class BatchClicker:
    """Find and click all matching elements on screen.

    Useful for:
    - Collecting multiple rewards/loot
    - Closing all popups of the same type
    - Clicking all "Accept" buttons
    - Clearing notification badges
    """

    def __init__(self, screenshot_mgr: ScreenshotManager,
                 locator: Optional[UIElementLocator] = None,
                 matcher: Optional[MultiScaleMatcher] = None):
        self.screenshot = screenshot_mgr
        self.locator = locator
        self.matcher = matcher or MultiScaleMatcher(screenshot_mgr)

    def click_all_text(self, text: str,
                       region: Optional[tuple[int, int, int, int]] = None,
                       fuzzy: bool = True,
                       max_clicks: int = 50,
                       delay: float = 0.3,
                       button: str = 'left') -> int:
        """Click all occurrences of a text label on screen.

        Args:
            text: Text to search for.
            region: Screen region.
            fuzzy: Fuzzy text matching.
            max_clicks: Maximum number of clicks.
            delay: Delay between clicks.
            button: Mouse button.

        Returns:
            Number of clicks performed.
        """
        if self.locator:
            elements = self.locator.locate_all_combined(text=text, region=region)
        else:
            from recognition.ocr import OCRRecognizer
            ocr = OCRRecognizer(self.screenshot)
            elements = []
            for match_text, conf, box in ocr.find_text(text, region, fuzzy=fuzzy):
                elements.append({
                    'center_x': box[0] + box[2] // 2,
                    'center_y': box[1] + box[3] // 2,
                    'confidence': conf,
                    'method': 'ocr',
                })

        count = 0
        for elem in elements:
            if count >= max_clicks:
                break
            cx, cy = elem['center_x'], elem['center_y']
            try:
                pyautogui.click(cx, cy, button=button)
                count += 1
                time.sleep(delay)
            except Exception:
                continue

        return count

    def click_all_template(self, template_path: str,
                           region: Optional[tuple[int, int, int, int]] = None,
                           similarity: float = 0.85,
                           max_clicks: int = 50,
                           delay: float = 0.3) -> int:
        """Click all instances of a template image on screen.

        Args:
            template_path: Path to template image.
            region: Screen region.
            similarity: Minimum similarity threshold.
            max_clicks: Maximum number of clicks.
            delay: Delay between clicks.

        Returns:
            Number of clicks performed.
        """
        if self.matcher:
            matches = self.matcher.find_all(template_path, region=region,
                                            similarity=similarity)
        else:
            matches = []

        count = 0
        for (cx, cy), score, scale in matches:
            if count >= max_clicks:
                break
            try:
                pyautogui.click(cx, cy)
                count += 1
                time.sleep(delay)
            except Exception:
                continue

        return count

    def click_all_until_gone(self, text: Optional[str] = None,
                              template_path: Optional[str] = None,
                              region: Optional[tuple[int, int, int, int]] = None,
                              max_rounds: int = 10,
                              delay: float = 0.3) -> int:
        """Repeatedly click all matching elements until none remain.

        Args:
            text: Text to search for.
            template_path: Template image.
            region: Screen region.
            max_rounds: Max scanning rounds.
            delay: Delay between rounds.

        Returns:
            Total clicks performed.
        """
        total = 0
        for _ in range(max_rounds):
            if text:
                count = self.click_all_text(text, region, max_clicks=100, delay=delay)
            elif template_path:
                count = self.click_all_template(template_path, region, delay=delay)
            else:
                break

            total += count
            if count == 0:
                break
            time.sleep(delay)

        return total
