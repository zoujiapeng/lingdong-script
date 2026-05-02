"""Reward collector — scan for and collect rewards/gifts/loot."""
import time
from typing import Optional
import pyautogui
from core.screenshot import ScreenshotManager
from interaction.click_all import BatchClicker
from recognition.ocr import OCRRecognizer


class RewardCollector:
    """Automated reward collection.

    Scans screen for reward text ("领取", "collect", "reward", "claim")
    and clicks all matching elements.
    """

    # Reward-related keywords
    REWARD_KEYWORDS = [
        '领取', 'collect', 'claim', 'reward', 'receive', '获取',
        '领取奖励', '签到', 'check-in', 'daily', '每日',
        'open', '打开', 'chest', '宝箱', 'loot',
        'gift', '礼物', 'present', 'bonus', '红利',
    ]

    def __init__(self, screenshot_mgr: ScreenshotManager,
                 batch_clicker: Optional[BatchClicker] = None,
                 ocr: Optional[OCRRecognizer] = None):
        self.screenshot = screenshot_mgr
        self.batch_clicker = batch_clicker or BatchClicker(
            screenshot_mgr,
            ocr=ocr or OCRRecognizer(screenshot_mgr)
        )
        self.ocr = ocr or OCRRecognizer(screenshot_mgr)

    def collect_all(self, region: Optional[tuple[int, int, int, int]] = None,
                    max_rounds: int = 15,
                    round_delay: float = 0.5) -> int:
        """Scan and collect all rewards on screen.

        Repeatedly scans for reward-related text and clicks everything.
        Stops when no more reward elements are found.

        Args:
            region: Screen region. None = full screen.
            max_rounds: Max scanning rounds.
            round_delay: Delay between rounds.

        Returns:
            Total rewards collected.
        """
        total = 0

        for _ in range(max_rounds):
            found_any = False

            for keyword in self.REWARD_KEYWORDS:
                count = self.batch_clicker.click_all_text(
                    keyword, region=region, fuzzy=True,
                    max_clicks=10, delay=0.3
                )
                if count > 0:
                    total += count
                    found_any = True
                    time.sleep(round_delay)

            if not found_any:
                break

        return total

    def collect_by_template(self, template_paths: list[str],
                            region: Optional[tuple[int, int, int, int]] = None,
                            max_rounds: int = 10,
                            round_delay: float = 0.5) -> int:
        """Collect rewards using template images.

        Args:
            template_paths: List of template images for reward buttons.
            region: Screen region.
            max_rounds: Max rounds.
            round_delay: Delay between rounds.

        Returns:
            Total collected.
        """
        total = 0

        for _ in range(max_rounds):
            found_any = False

            for tpl_path in template_paths:
                count = self.batch_clicker.click_all_template(
                    tpl_path, region=region, similarity=0.8, delay=0.3
                )
                if count > 0:
                    total += count
                    found_any = True
                    time.sleep(round_delay)

            if not found_any:
                break

        return total

    def wait_for_rewards(self, region: Optional[tuple[int, int, int, int]] = None,
                         timeout: float = 30.0,
                         collect_rounds: int = 5) -> int:
        """Wait for rewards to appear and then collect them.

        Args:
            region: Screen region.
            timeout: Max wait time.
            collect_rounds: Collection rounds once rewards appear.

        Returns:
            Total collected.
        """
        start = time.time()
        while time.time() - start < timeout:
            # Check if any reward text is visible
            texts = self.ocr.recognize(region=region)
            has_reward = any(
                any(kw in text.lower() for kw in self.REWARD_KEYWORDS)
                for text, conf, box in texts
            )
            if has_reward:
                return self.collect_all(region=region, max_rounds=collect_rounds)
            time.sleep(0.5)

        return 0
