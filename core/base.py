"""Base automation engine — integrates all modules into a unified interface."""
import time
import json
import threading
from datetime import datetime
from typing import Any, Optional, Callable

from .screenshot import ScreenshotManager
from .template_cache import TemplateCache
from .parallel import ThreadPoolManager
from utils.image import ImageUtils


class AutomationEngine:
    """Unified automation engine integrating all subsystems.

    This is the main entry point for programmatic use.
    All modules are accessible as properties for fine-grained control.
    """

    def __init__(self, debug: bool = False):
        self.debug = debug
        self._stopped = False
        self._paused = False
        self._listeners: list[Callable] = []

        # ---- Core ----
        self.screenshot = ScreenshotManager()
        self.template_cache = TemplateCache()
        self.thread_pool = ThreadPoolManager.get_instance()

        # ---- Vision (lazy imports to avoid circular deps) ----
        from vision.color import ColorDetector
        from vision.progress import ProgressDetector
        from vision.change import ChangeDetector
        from vision.scene import SceneClassifier
        self.color = ColorDetector(self.screenshot)
        self.progress = ProgressDetector(self.screenshot)
        self.change = ChangeDetector(self.screenshot)
        self.scene = SceneClassifier(self.screenshot)

        # ---- Recognition ----
        from recognition.ocr import OCRRecognizer
        from recognition.matcher import MultiScaleMatcher
        from recognition.ui_element import UIElementLocator
        self.ocr = OCRRecognizer(self.screenshot)
        self.matcher = MultiScaleMatcher(self.screenshot, self.template_cache)
        self.ui_element = UIElementLocator(self.screenshot, self.ocr, self.matcher)

        # ---- System ----
        from system.process import ProcessDetector
        from system.window import WindowManager
        from system.resolution import ResolutionAdapter
        self.process = ProcessDetector()
        self.window = WindowManager()
        self.resolution = ResolutionAdapter()

        # ---- Interaction ----
        from interaction.click_smart import SmartClicker
        from interaction.click_all import BatchClicker
        from interaction.human_behavior import HumanBehaviorSimulator
        from interaction.popup import PopupCloser
        self.clicker = SmartClicker(self.screenshot, self.ui_element)
        self.batch_clicker = BatchClicker(self.screenshot, self.ui_element, self.matcher)
        self.human = HumanBehaviorSimulator()
        self.popup_closer = PopupCloser(self.screenshot, self.ocr, self.matcher)

        # ---- Logic ----
        from logic.retry import RetryMechanism
        from logic.timeout import TimeoutControl
        from logic.variables import VariableSystem
        self.variables = VariableSystem()
        self.retry = RetryMechanism()
        self.timeout = TimeoutControl()

        # ---- Modules ----
        from modules.combat import AutoCombat
        from modules.rewards import RewardCollector
        from modules.navigate import Navigator
        from modules.recovery import FlowRecovery
        self.combat = AutoCombat(self.screenshot)
        self.rewards = RewardCollector(self.screenshot, self.batch_clicker, self.ocr)
        self.navigator = Navigator(self.screenshot, self.clicker)
        self.recovery = FlowRecovery(self.screenshot, self.popup_closer, self.ocr)

        # ---- DSL ----
        from dsl.parser import DSLParser
        from dsl.executor import DSLExecutor
        from dsl.resolver import ConditionResolver
        self.dsl_parser = DSLParser()
        self.dsl_executor = DSLExecutor(self.screenshot, self.variables)
        self.dsl_resolver = ConditionResolver(
            self.screenshot, self.ocr, self.matcher,
            self.color, self.variables
        )

        # ---- Utils ----
        self.image = ImageUtils()

    # ================================================================
    # Lifecycle
    # ================================================================

    def stop(self):
        """Stop all running operations."""
        self._stopped = True
        self.combat.stop()
        self.dsl_executor.stop()

    def pause(self):
        """Pause execution."""
        self._paused = True
        self.dsl_executor.pause()

    def resume(self):
        """Resume execution."""
        self._paused = False
        self.dsl_executor.resume()

    def add_listener(self, callback: Callable[[str, Any], None]):
        """Add an event listener. Callback receives (event_type, data)."""
        self._listeners.append(callback)

    def _emit(self, event: str, data: Any = None):
        for cb in self._listeners:
            try:
                cb(event, data)
            except Exception:
                pass

    # ================================================================
    # High-Level Operations
    # ================================================================

    def run_dsl(self, code: str) -> bool:
        """Run a DSL script."""
        self._stopped = False
        self.dsl_executor._stopped = False
        return self.dsl_executor.execute(code)

    def run_dsl_file(self, path: str) -> bool:
        """Run a DSL script from a file."""
        with open(path, 'r', encoding='utf-8') as f:
            code = f.read()
        return self.run_dsl(code)

    def get_engine_state(self) -> dict:
        """Get the current engine state for status reporting."""
        return {
            'status': 'running' if not self._stopped else 'stopped',
            'paused': self._paused,
            'variables': self.variables.export_dict(),
            'screen_size': self.resolution.current_size,
            'timestamp': datetime.now().isoformat(),
        }

    # ================================================================
    # Wait Helpers
    # ================================================================

    def wait(self, seconds: float):
        time.sleep(seconds)

    def wait_until(self, condition_expr: str, timeout: float = 10.0,
                   poll_interval: float = 0.3) -> bool:
        """Wait until a condition expression is true.

        Args:
            condition_expr: Condition string (text:, img:, etc.)
            timeout: Max wait time.
            poll_interval: Check interval.

        Returns:
            True if condition was met within timeout.
        """
        start = time.time()
        while time.time() - start < timeout:
            if self._stopped:
                return False
            if self.dsl_resolver.resolve(condition_expr):
                return True
            time.sleep(poll_interval)
        return False

    def wait_for_stable(self, x: int, y: int, w: int, h: int,
                        timeout: float = 5.0) -> bool:
        """Wait for a region to become stable."""
        return self.change.wait_for_stable(x, y, w, h, timeout=timeout)

    def wait_for_change(self, x: int, y: int, w: int, h: int,
                        timeout: float = 10.0) -> bool:
        """Wait for a region to change."""
        return self.change.wait_for_change(x, y, w, h, timeout=timeout)

    # ================================================================
    # Shortcut Click Methods
    # ================================================================

    def click(self, x: int, y: int, human: bool = False):
        """Click at coordinates."""
        if human:
            self.human.move_and_click(x, y)
        else:
            import pyautogui
            pyautogui.click(x, y)

    def click_text(self, text: str, human: bool = False) -> bool:
        """Click on text found on screen using OCR."""
        return self.clicker.click(text=text, human_like=human)

    def click_image(self, template_path: str, human: bool = False) -> bool:
        """Click on a template image found on screen."""
        return self.clicker.click(template_path=template_path, human_like=human)

    def click_all_text(self, text: str) -> int:
        """Click all instances of a text label."""
        return self.batch_clicker.click_all_text(text)

    def type_text(self, text: str, human: bool = False):
        """Type text on the keyboard."""
        if human:
            self.human.type_text(text)
        else:
            import pyautogui
            pyautogui.write(text)
