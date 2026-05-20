"""Human behavior simulation — natural mouse movements and typing."""
import random
import time
from typing import Optional

from interaction.actions import _ensure_win32, _win32_click


class HumanBehaviorSimulator:
    """Simulate human-like mouse and keyboard behavior.

    Uses win32api for all operations (no pyautogui dependency).
    Falls back to pyautogui only if win32api fails.
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
        """Move mouse to target position with human-like motion."""
        w32 = _ensure_win32()
        start_x, start_y = w32['api'].GetCursorPos()

        if duration is None:
            dist = ((x - start_x) ** 2 + (y - start_y) ** 2) ** 0.5
            duration = max(0.05, self.move_duration_base * (dist / 500) ** 0.5)

        num_points = max(10, min(50, int(duration / 0.01)))
        steps = num_points
        for i in range(1, steps + 1):
            t = i / steps
            cx = int(start_x + (x - start_x) * t)
            cy = int(start_y + (y - start_y) * t)
            w32['api'].SetCursorPos((cx, cy))
            time.sleep(duration / steps)

    def move_and_click(self, x: float, y: float,
                       button: str = 'left',
                       clicks: int = 1,
                       duration: Optional[float] = None):
        """Move to position and click with human-like behavior."""
        self.move_mouse(x, y, duration=duration)
        time.sleep(random.uniform(*self.click_delay_range))

        btn = button
        if button == 'double':
            btn = 'left'
            clicks = 2
        for _ in range(clicks):
            _win32_click(int(x), int(y), btn)
            time.sleep(random.uniform(0.03, 0.08))

        # Humans often move slightly right after clicking
        if random.random() < 0.2:
            w32 = _ensure_win32()
            rx, ry = w32['api'].GetCursorPos()
            w32['api'].SetCursorPos((rx + random.randint(-3, 3),
                                      ry + random.randint(-3, 3)))

    def type_text(self, text: str, per_char_delay: Optional[tuple[float, float]] = None):
        """Type text with human-like variable delays between keystrokes."""
        delays = per_char_delay or self.type_delay_range
        w32 = _ensure_win32()
        for char in text:
            vk = ord(char.upper())
            w32['api'].keybd_event(vk, 0, 0, 0)
            time.sleep(0.01)
            w32['api'].keybd_event(vk, 0, w32['con'].KEYEVENTF_KEYUP, 0)
            time.sleep(random.uniform(*delays))

    def drag(self, start_x: float, start_y: float,
             end_x: float, end_y: float,
             duration: Optional[float] = None):
        """Perform a human-like drag operation."""
        w32 = _ensure_win32()
        self.move_mouse(start_x, start_y)
        time.sleep(random.uniform(0.1, 0.2))

        if duration is None:
            dist = ((end_x - start_x) ** 2 + (end_y - start_y) ** 2) ** 0.5
            duration = max(0.1, dist / 800)

        w32['api'].mouse_event(w32['con'].MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        steps = max(int(duration / 0.01), 10)
        for i in range(1, steps + 1):
            t = i / steps
            cx = int(start_x + (end_x - start_x) * t)
            cy = int(start_y + (end_y - start_y) * t)
            w32['api'].SetCursorPos((cx, cy))
            time.sleep(duration / steps)
        w32['api'].mouse_event(w32['con'].MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def random_scroll(self, clicks: int = -3):
        """Perform a random scroll."""
        from interaction.actions import _win32_scroll
        _win32_scroll(clicks)

    def random_delay(self, min_d: float = 0.5, max_d: float = 2.0):
        """Wait a random amount of time (human-like hesitation)."""
        time.sleep(random.uniform(min_d, max_d))
