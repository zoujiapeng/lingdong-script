"""Flow recovery — detect abnormal UI states and recover to a known good state."""
import time
from typing import Optional, Callable
import pyautogui
from core.screenshot import ScreenshotManager
from recognition.ocr import OCRRecognizer
from interaction.popup import PopupCloser


class FlowRecovery:
    """Detect and recover from abnormal UI states.

    Recovery strategies:
    1. Close popups
    2. Press Escape (back/close)
    3. Click known "return to main" buttons
    4. Navigate to specific coordinates
    """

    def __init__(self, screenshot_mgr: ScreenshotManager,
                 popup_closer: Optional[PopupCloser] = None,
                 ocr: Optional[OCRRecognizer] = None):
        self.screenshot = screenshot_mgr
        self.popup_closer = popup_closer or PopupCloser(screenshot_mgr)
        self.ocr = ocr or OCRRecognizer(screenshot_mgr)

    def recover(self, main_menu_indicators: Optional[list[str]] = None,
                max_steps: int = 10,
                step_delay: float = 0.5) -> bool:
        """Attempt to recover to a normal state.

        Strategy pipeline:
        1. Close any visible popups
        2. Press Escape (back/close current dialog)
        3. Try common "return" buttons

        Args:
            main_menu_indicators: Text that indicates recovery success.
            max_steps: Max recovery attempts.
            step_delay: Delay between recovery actions.

        Returns:
            True if recovery succeeded.
        """
        indicators = main_menu_indicators or ["main", "menu", "主页", "首页"]

        for step in range(max_steps):
            # Check if already recovered
            texts = self.ocr.recognize()
            for text, conf, box in texts:
                if any(indicator.lower() in text.lower()
                       for indicator in indicators):
                    return True

            # Strategy 1: Close popups
            if step % 3 == 0:
                self.popup_closer.close_all(max_attempts=3)

            # Strategy 2: Press Escape
            if step % 2 == 0:
                pyautogui.press('esc')

            # Strategy 3: Click common return positions
            if step == 4:
                # Try clicking "back" at top-left area
                from interaction.click_smart import SmartClicker
                clicker = SmartClicker(self.screenshot)
                clicker.click(
                    text="back", template_path=None,
                    fallback_coords=(50, 50)
                )

            # Strategy 4: Click known safe coordinates
            if step == 7:
                # Center of screen (often a "confirm" area)
                screen_w, screen_h = pyautogui.size()
                pyautogui.click(screen_w // 2, screen_h * 0.6)

            time.sleep(step_delay)

        return False

    def detect_abnormal(self, known_scene_func: Callable[[], bool],
                        poll_interval: float = 1.0) -> bool:
        """Continuously monitor and detect abnormal states.

        Args:
            known_scene_func: Function that returns True if in a known good state.
            poll_interval: Check interval.

        Returns:
            True if abnormal state detected.
        """
        return not known_scene_func()

    def safe_operation(self, fn: Callable,
                       recovery_func: Optional[Callable] = None,
                       max_retries: int = 3) -> bool:
        """Execute an operation with automatic recovery on failure.

        Args:
            fn: The operation to execute.
            recovery_func: Custom recovery function. Uses default if None.
            max_retries: Max recovery+retry attempts.

        Returns:
            True if operation succeeded.
        """
        for attempt in range(max_retries):
            try:
                result = fn()
                if result:
                    return True
            except Exception:
                pass

            if recovery_func:
                recovery_func()
            else:
                self.recover()

        return False
