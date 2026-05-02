"""Human behavior simulation — natural mouse movements and typing."""
import random
import time
from typing import Optional
import pyautogui
from utils.bezier import BezierCurve


class HumanBehaviorSimulator:
    """Simulate human-like mouse and keyboard behavior.

    Features:
    - Bezier curve mouse movement with natural acceleration/deceleration
    - Random delay variation between actions
    - Overshoot correction for short movements
    - Consistent movement speed profile regardless of distance
    """

    def __init__(self, move_duration_base: float = 0.3,
                 click_delay_range: tuple[float, float] = (0.05, 0.15),
                 type_delay_range: tuple[float, float] = (0.05, 0.15)):
        self.move_duration_base = move_duration_base
        self.click_delay_range = click_delay_range
        self.type_delay_range = type_delay_range

    def move_mouse(self, x: float, y: float,
                   duration: Optional[float] = None,
                   overshoot: bool = False,
                   smoothness: float = 0.4):
        """Move mouse to target position with human-like motion.

        Args:
            x, y: Target coordinates.
            duration: Movement duration. Auto-calculated if None.
            overshoot: Enable overshoot behavior for more natural feel.
            smoothness: Path curvature (0=straight line, 1=very curved).
        """
        start_x, start_y = pyautogui.position()

        if duration is None:
            dist = ((x - start_x) ** 2 + (y - start_y) ** 2) ** 0.5
            # Humans move faster over longer distances
            duration = max(0.05, self.move_duration_base * (dist / 500) ** 0.5)

        num_points = max(10, min(50, int(duration / 0.01)))

        if overshoot and random.random() < 0.3:
            path = BezierCurve.generate_with_overshoot(
                start_x, start_y, x, y,
                overshoot_px=random.uniform(5, 20),
                num_points=num_points
            )
        else:
            path = BezierCurve.generate_path(
                start_x, start_y, x, y,
                num_points=num_points,
                smoothness=smoothness * random.uniform(0.7, 1.3),
                randomness=0.2 * random.uniform(0.5, 1.5)
            )

        delays = BezierCurve.compute_delays(
            len(path), base_duration=duration, jitter=0.15
        )

        for (px, py), wait in zip(path, delays):
            pyautogui.moveTo(px, py, duration=0.0)
            time.sleep(wait)

    def move_and_click(self, x: float, y: float,
                       button: str = 'left',
                       clicks: int = 1,
                       duration: Optional[float] = None):
        """Move to position and click with human-like behavior.

        Includes slight movement after click (human tendency).
        """
        self.move_mouse(x, y, duration=duration)

        # Slight hesitation before clicking
        time.sleep(random.uniform(*self.click_delay_range))

        if button == 'left':
            if clicks > 1:
                for _ in range(clicks):
                    pyautogui.click()
                    time.sleep(random.uniform(0.03, 0.08))
            else:
                pyautogui.click()
        elif button == 'right':
            pyautogui.rightClick()
        elif button == 'middle':
            pyautogui.middleClick()
        elif button == 'double':
            pyautogui.doubleClick()

        # Humans often move slightly right after clicking
        if random.random() < 0.2:
            rx, ry = pyautogui.position()
            pyautogui.moveTo(rx + random.randint(-3, 3),
                             ry + random.randint(-3, 3),
                             duration=random.uniform(0.02, 0.08))

    def type_text(self, text: str, per_char_delay: Optional[tuple[float, float]] = None):
        """Type text with human-like variable delays between keystrokes."""
        delays = per_char_delay or self.type_delay_range
        for char in text:
            pyautogui.write(char, interval=0)
            time.sleep(random.uniform(*delays))

    def drag(self, start_x: float, start_y: float,
             end_x: float, end_y: float,
             duration: Optional[float] = None):
        """Perform a human-like drag operation."""
        # Move to start
        self.move_mouse(start_x, start_y)

        time.sleep(random.uniform(0.1, 0.2))

        # Generate drag path with slight curve
        path = BezierCurve.generate_path(
            start_x, start_y, end_x, end_y,
            num_points=20, smoothness=0.1, randomness=0.05
        )

        if duration is None:
            dist = ((end_x - start_x) ** 2 + (end_y - start_y) ** 2) ** 0.5
            duration = max(0.1, dist / 800)

        pyautogui.mouseDown()
        delays = BezierCurve.compute_delays(len(path), base_duration=duration)
        for (px, py), wait in zip(path, delays):
            pyautogui.moveTo(px, py, duration=0.0)
            time.sleep(wait)
        pyautogui.mouseUp()

    def random_scroll(self, clicks: int = -3):
        """Perform a random scroll (negative = down, positive = up)."""
        pyautogui.scroll(clicks)
        time.sleep(random.uniform(0.1, 0.3))

    def random_delay(self, min_d: float = 0.5, max_d: float = 2.0):
        """Wait a random amount of time (human-like hesitation)."""
        time.sleep(random.uniform(min_d, max_d))
