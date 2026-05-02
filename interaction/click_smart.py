"""Smart click — hybrid click strategy: OCR -> Template -> History -> Position fallback."""
import time
from typing import Optional
import pyautogui
from core.screenshot import ScreenshotManager
from recognition.ui_element import UIElementLocator
from recognition.ocr import OCRRecognizer
from recognition.matcher import MultiScaleMatcher


class SmartClicker:
    """Click elements using the best available strategy.

    Strategy pipeline (in order):
    1. OCR: find element by text label
    2. Template: find by image matching
    3. History: use previous known position
    4. Position: calculate from positional rules

    Configurable to skip or prioritize strategies per-call.
    """

    def __init__(self, screenshot_mgr: ScreenshotManager,
                 locator: Optional[UIElementLocator] = None):
        self.screenshot = screenshot_mgr
        self.locator = locator or UIElementLocator(
            screenshot_mgr,
            ocr=OCRRecognizer(screenshot_mgr),
            matcher=MultiScaleMatcher(screenshot_mgr)
        )

    def click(self, text: Optional[str] = None,
              template_path: Optional[str] = None,
              template_b64: Optional[str] = None,
              region: Optional[tuple[int, int, int, int]] = None,
              strategy: str = 'ocr_first',
              button: str = 'left',
              clicks: int = 1,
              offset_x: int = 0, offset_y: int = 0,
              human_like: bool = False,
              fallback_coords: Optional[tuple[int, int]] = None) -> bool:
        """Click on an element using smart detection.

        Args:
            text: Text label of the element (for OCR).
            template_path/b64: Template image (for template matching).
            region: Screen region to search in.
            strategy: 'ocr_first', 'template_first', 'fusion'.
            button: 'left', 'right', 'middle'.
            clicks: Number of clicks.
            offset_x, offset_y: Click offset from center.
            human_like: Use Bezier curve human-like movement.
            fallback_coords: (x, y) to click if no other strategy works.

        Returns:
            True if successfully clicked.
        """
        element = self.locator.locate_combined(
            text=text, template_path=template_path,
            template_b64=template_b64, region=region,
            strategy=strategy
        )

        if element:
            cx = element['center_x'] + offset_x
            cy = element['center_y'] + offset_y
        elif fallback_coords:
            cx, cy = fallback_coords
        else:
            return False

        self._do_click(cx, cy, button=button, clicks=clicks,
                       human_like=human_like)
        return True

    def click_position(self, x: int, y: int,
                       button: str = 'left',
                       clicks: int = 1,
                       human_like: bool = False):
        """Click at a specific position."""
        self._do_click(x, y, button=button, clicks=clicks,
                       human_like=human_like)

    def _do_click(self, x: int, y: int,
                  button: str = 'left',
                  clicks: int = 1,
                  human_like: bool = False):
        """Execute a click at the given coordinates."""
        if human_like:
            from .human_behavior import HumanBehaviorSimulator
            hbs = HumanBehaviorSimulator()
            hbs.move_and_click(x, y, button=button, clicks=clicks)
        else:
            pyautogui.moveTo(x, y, duration=0.1)
            click_fn = {
                'left': pyautogui.click,
                'right': pyautogui.rightClick,
                'middle': pyautogui.middleClick,
                'double': pyautogui.doubleClick,
            }.get(button, pyautogui.click)

            if button == 'double':
                pyautogui.doubleClick(x, y)
            else:
                for _ in range(clicks):
                    click_fn(x, y)
                    if clicks > 1:
                        time.sleep(0.05)
