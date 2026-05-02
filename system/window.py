"""Window detection and focus management."""
from typing import Optional
import time


class WindowManager:
    """Detect, enumerate, and focus windows.

    Uses pygetwindow as primary backend, with win32gui fallback on Windows.
    """

    def __init__(self):
        self._gw_available = False
        self._win32_available = False

        try:
            import pygetwindow as gw
            self._gw = gw
            self._gw_available = True
        except ImportError:
            pass

        try:
            import win32gui
            import win32con
            import win32process
            self._win32gui = win32gui
            self._win32con = win32con
            self._win32process = win32process
            self._win32_available = True
        except ImportError:
            pass

        import pyautogui
        self._pyautogui = pyautogui

    def find_windows(self, title: str, exact: bool = False) -> list[dict]:
        """Find windows matching a title pattern.

        Returns:
            List of {title, left, top, width, height, hwnd (if win32)}.
        """
        windows = []

        if self._gw_available:
            try:
                all_windows = self._gw.getAllWindows()
                for w in all_windows:
                    if not w.title:
                        continue
                    match = (w.title.lower() == title.lower()) if exact \
                        else (title.lower() in w.title.lower())
                    if match:
                        windows.append({
                            'title': w.title,
                            'left': w.left, 'top': w.top,
                            'width': w.width, 'height': w.height,
                            'hwnd': None,
                        })
            except Exception:
                pass

        if self._win32_available and not windows:
            def enum_callback(hwnd, results):
                if not self._win32gui.IsWindowVisible(hwnd):
                    return
                wtext = self._win32gui.GetWindowText(hwnd)
                if not wtext:
                    return
                match = (wtext.lower() == title.lower()) if exact \
                    else (title.lower() in wtext.lower())
                if match:
                    rect = self._win32gui.GetWindowRect(hwnd)
                    results.append({
                        'title': wtext,
                        'left': rect[0], 'top': rect[1],
                        'width': rect[2] - rect[0],
                        'height': rect[3] - rect[1],
                        'hwnd': hwnd,
                    })

            results = []
            self._win32gui.EnumWindows(enum_callback, results)
            windows = results

        return windows

    def focus_window(self, title: str, exact: bool = False) -> bool:
        """Bring a window matching the title to the foreground.

        Args:
            title: Window title to search for.
            exact: Exact title match.

        Returns:
            True if successful.
        """
        if self._win32_available:
            def enum_callback(hwnd, target):
                if not self._win32gui.IsWindowVisible(hwnd):
                    return
                wtext = self._win32gui.GetWindowText(hwnd)
                match = (wtext.lower() == target.lower()) if exact \
                    else (target.lower() in wtext.lower())
                if match:
                    self._win32gui.ShowWindow(hwnd, self._win32con.SW_RESTORE)
                    self._win32gui.SetForegroundWindow(hwnd)
                    target.append(hwnd)

            found = []
            self._win32gui.EnumWindows(enum_callback, found)
            if found:
                time.sleep(0.2)
                return True

        if self._gw_available:
            try:
                windows = self._gw.getWindowsWithText(title)
                if windows:
                    windows[0].activate()
                    time.sleep(0.2)
                    return True
            except Exception:
                pass

        return False

    def get_active_window(self) -> Optional[dict]:
        """Get information about the currently active window.

        Returns:
            {title, left, top, width, height} or None.
        """
        if self._win32_available:
            try:
                hwnd = self._win32gui.GetForegroundWindow()
                title = self._win32gui.GetWindowText(hwnd)
                rect = self._win32gui.GetWindowRect(hwnd)
                return {
                    'title': title,
                    'left': rect[0], 'top': rect[1],
                    'width': rect[2] - rect[0],
                    'height': rect[3] - rect[1],
                }
            except Exception:
                pass

        if self._gw_available:
            try:
                w = self._gw.getActiveWindow()
                if w:
                    return {
                        'title': w.title,
                        'left': w.left, 'top': w.top,
                        'width': w.width, 'height': w.height,
                    }
            except Exception:
                pass

        return None

    def get_window_rect(self, title: str, exact: bool = False) -> Optional[dict]:
        """Get the bounding rectangle of a window.

        Returns:
            {left, top, width, height} or None.
        """
        windows = self.find_windows(title, exact)
        if windows:
            w = windows[0]
            return {'left': w['left'], 'top': w['top'],
                    'width': w['width'], 'height': w['height']}
        return None

    def window_exists(self, title: str, exact: bool = False) -> bool:
        """Check if a window with the given title exists."""
        return len(self.find_windows(title, exact)) > 0

    def wait_for_window(self, title: str, timeout: float = 10.0,
                        poll_interval: float = 0.3) -> bool:
        """Wait until a window with the given title appears.

        Returns:
            True if window appeared within timeout.
        """
        start = time.time()
        while time.time() - start < timeout:
            if self.window_exists(title):
                return True
            time.sleep(poll_interval)
        return False

    def wait_for_window_close(self, title: str, timeout: float = 10.0,
                              poll_interval: float = 0.3) -> bool:
        """Wait until a window closes.

        Returns:
            True if window closed within timeout.
        """
        start = time.time()
        while time.time() - start < timeout:
            if not self.window_exists(title):
                return True
            time.sleep(poll_interval)
        return False

    def get_all_window_titles(self) -> list[str]:
        """Get titles of all visible windows."""
        titles = []
        if self._gw_available:
            try:
                for w in self._gw.getAllWindows():
                    if w.title:
                        titles.append(w.title)
            except Exception:
                pass
        return titles
