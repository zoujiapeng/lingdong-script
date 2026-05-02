"""Navigation — follow predefined menu trees and click through paths."""
import time
from typing import Optional
import pyautogui
from core.screenshot import ScreenshotManager
from recognition.ocr import OCRRecognizer
from interaction.click_smart import SmartClicker


class Navigator:
    """Navigate through predefined menu paths.

    A "navigation path" is a sequence of steps:
    [Step 1: click "Menu"], [Step 2: click "Settings"], [Step 3: click "Option"]

    Each step can be resolved by text (OCR), template image, or position.
    """

    def __init__(self, screenshot_mgr: ScreenshotManager,
                 smart_clicker: Optional[SmartClicker] = None):
        self.screenshot = screenshot_mgr
        self.smart_clicker = smart_clicker or SmartClicker(screenshot_mgr)

    def navigate(self, path: list[dict],
                 timeout_per_step: float = 5.0,
                 delay: float = 0.5) -> bool:
        """Execute a navigation path sequentially.

        Args:
            path: List of step dicts. Each step can have:
                  - text: Text to find and click
                  - template: Template image path
                  - position: (x, y) fallback position
                  - region: (x, y, w, h) search region for this step
                  - delay: Override default delay after this step
            timeout_per_step: Max time to find the element per step.
            delay: Default delay between steps.

        Returns:
            True if all steps completed successfully.
        """
        for i, step in enumerate(path):
            text = step.get('text')
            template = step.get('template')
            position = step.get('position')
            region = step.get('region')
            step_delay = step.get('delay', delay)

            start = time.time()
            clicked = False

            while time.time() - start < timeout_per_step:
                if text or template:
                    success = self.smart_clicker.click(
                        text=text, template_path=template,
                        region=region,
                        fallback_coords=position
                    )
                elif position:
                    pyautogui.click(position[0], position[1])
                    success = True
                else:
                    success = False

                if success:
                    clicked = True
                    break

                time.sleep(0.3)

            if not clicked:
                return False

            time.sleep(step_delay)

        return True

    def register_path(self, name: str, path: list[dict]):
        """Register a named navigation path for later use."""
        if not hasattr(self, '_paths'):
            self._paths = {}
        self._paths[name] = path

    def go(self, name: str) -> bool:
        """Execute a registered navigation path by name."""
        if not hasattr(self, '_paths') or name not in self._paths:
            return False
        return self.navigate(self._paths[name])

    def back_to_main(self, main_menu_indicators: list[str],
                     max_attempts: int = 5,
                     delay: float = 0.5) -> bool:
        """Try to navigate back to the main menu.

        Uses a strategy of pressing Escape, then looking for
        main menu indicators. Repeats until found or max attempts.

        Args:
            main_menu_indicators: Text strings that indicate main menu.
            max_attempts: Max Escape presses.
            delay: Delay between actions.

        Returns:
            True if main menu was found.
        """
        ocr = OCRRecognizer(self.screenshot)

        for _ in range(max_attempts):
            # Check if already on main menu
            texts = ocr.recognize()
            for text, conf, box in texts:
                if any(indicator.lower() in text.lower()
                       for indicator in main_menu_indicators):
                    return True

            # Press Escape to go back
            pyautogui.press('esc')
            time.sleep(delay)

        return False
