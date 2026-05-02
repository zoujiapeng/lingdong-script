"""Auto combat / grind task — template flows for repetitive game tasks."""
import time
from typing import Optional
import pyautogui
from core.screenshot import ScreenshotManager
from recognition.ocr import OCRRecognizer
from recognition.matcher import MultiScaleMatcher
from vision.change import ChangeDetector


class AutoCombat:
    """Automated battle/grind workflow.

    Provides a configurable template loop:
    detect battle -> execute skills -> check result -> repeat

    Designed to be subclassed for specific game implementations.
    """

    def __init__(self, screenshot_mgr: ScreenshotManager,
                 battle_indicators: Optional[list[str]] = None,
                 victory_indicators: Optional[list[str]] = None,
                 defeat_indicators: Optional[list[str]] = None,
                 skill_positions: Optional[list[tuple[int, int]]] = None):
        self.screenshot = screenshot_mgr
        self.ocr = OCRRecognizer(screenshot_mgr)
        self.matcher = MultiScaleMatcher(screenshot_mgr)
        self.change = ChangeDetector(screenshot_mgr)

        self.battle_indicators = battle_indicators or [
            "战斗", "battle", "fight", "attack", "回合",
            "auto", "skip", "speed", "加速"
        ]
        self.victory_indicators = victory_indicators or [
            "胜利", "victory", "win", "通关", "完成",
            "reward", "奖励"
        ]
        self.defeat_indicators = defeat_indicators or [
            "失败", "defeat", "lose", "败", "game over"
        ]

        self.skill_positions = skill_positions or []
        self._running = False

    def detect_battle(self, region: Optional[tuple[int, int, int, int]] = None) -> bool:
        """Detect if a battle scene is active."""
        texts = self.ocr.recognize(region=region)
        detected = False
        for text, conf, box in texts:
            if any(indicator.lower() in text.lower()
                   for indicator in self.battle_indicators):
                if conf > 0.3:
                    detected = True
                    break
        return detected

    def detect_victory(self, region: Optional[tuple[int, int, int, int]] = None) -> bool:
        """Detect victory/result screen."""
        texts = self.ocr.recognize(region=region)
        for text, conf, box in texts:
            if any(indicator.lower() in text.lower()
                   for indicator in self.victory_indicators):
                if conf > 0.3:
                    return True
        return False

    def detect_defeat(self, region: Optional[tuple[int, int, int, int]] = None) -> bool:
        """Detect defeat/loss screen."""
        texts = self.ocr.recognize(region=region)
        for text, conf, box in texts:
            if any(indicator.lower() in text.lower()
                   for indicator in self.defeat_indicators):
                if conf > 0.3:
                    return True
        return False

    def execute_skills(self, skill_positions: Optional[list[tuple[int, int]]] = None,
                       delays: Optional[list[float]] = None):
        """Click skill buttons in sequence."""
        positions = skill_positions or self.skill_positions
        for i, (sx, sy) in enumerate(positions):
            delay = delays[i] if delays else 0.5
            pyautogui.click(sx, sy)
            time.sleep(delay)

    def grind(self, skill_positions: Optional[list[tuple[int, int]]] = None,
              battle_region: Optional[tuple[int, int, int, int]] = None,
              max_battles: int = 100,
              post_battle_delay: float = 2.0,
              battle_timeout: float = 60.0,
              on_battle_start: Optional[callable] = None,
              on_battle_end: Optional[callable] = None) -> int:
        """Run the grind loop for a specified number of battles.

        Args:
            skill_positions: List of (x, y) skill button positions.
            battle_region: Screen region to monitor for battle state.
            max_battles: Maximum battles to run.
            post_battle_delay: Delay after battle ends.
            battle_timeout: Max time per battle.
            on_battle_start: Callback when battle starts.
            on_battle_end: Callback when battle ends.

        Returns:
            Number of battles completed.
        """
        self._running = True
        battles = 0

        while self._running and battles < max_battles:
            # Wait for battle to start
            start = time.time()
            while time.time() - start < battle_timeout:
                if not self._running:
                    return battles
                if self.detect_battle(battle_region):
                    break
                time.sleep(0.5)
            else:
                # No battle detected, might be in overworld
                time.sleep(2)
                continue

            battles += 1
            if on_battle_start:
                on_battle_start(battles)

            # Execute skills during battle
            if skill_positions or self.skill_positions:
                time.sleep(1)
                self.execute_skills(skill_positions)

            # Wait for battle to end
            start = time.time()
            while time.time() - start < battle_timeout:
                if not self._running:
                    return battles
                if self.detect_victory(battle_region):
                    break
                if self.detect_defeat(battle_region):
                    break
                time.sleep(0.5)

            if on_battle_end:
                on_battle_end(battles)

            time.sleep(post_battle_delay)

        return battles

    def stop(self):
        """Stop the grind loop."""
        self._running = False
