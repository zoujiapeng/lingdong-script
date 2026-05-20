"""
桌面自动化关键字库 — 封装 RPA.Windows + pywinauto 操作。
注意：类名必须与文件名匹配 (desktop_actions.py → desktop_actions)，这是 RF 7.x 的要求。
"""

import time

from robot.api.deco import keyword


class desktop_actions:
    """桌面操作关键字：点击、输入、快捷键、截图、窗口管理、启动程序。"""

    ROBOT_LIBRARY_SCOPE = "GLOBAL"

    def __init__(self):
        self._rpa_windows = None

    def _get_windows(self):
        if self._rpa_windows is None:
            from RPA.Windows import Windows
            self._rpa_windows = Windows()
        return self._rpa_windows

    # ── 鼠标操作 ─────────────────────────────────────────────────

    @keyword("Click Coordinates")
    def click_coordinates(self, x: int, y: int, button: str = "left"):
        """在指定坐标点击。button: left | right | middle | double"""
        win = self._get_windows()
        btn_map = {"left": win.click, "right": win.right_click,
                   "middle": win.middle_click, "double": win.double_click}
        click_fn = btn_map.get(button, win.click)
        click_fn(coordinates=(int(x), int(y)))

    @keyword("Click Element")
    def click_element(self, locator: str):
        """通过定位器点击UI元素（支持name/class/automation_id等）。

        locator 示例:
            name:确定
            id:button1
            class:Button
            automation_id:1001
        """
        win = self._get_windows()
        win.click(locator)

    @keyword("Type Text")
    def type_text(self, text: str, clear_first: bool = False):
        """输入文字。clear_first=True 先清空再输入。"""
        win = self._get_windows()
        if clear_first:
            win.send_keys("^a")
            time.sleep(0.05)
        win.send_keys(text)

    @keyword("Send Keys")
    def send_keys(self, keys: str):
        """发送快捷键。支持 ^c (Ctrl+C), %f (Alt+F), +a (Shift+A) 等。

        常见:
            ^{key} — Ctrl
            %{key} — Alt
            +{key} — Shift
            {ENTER} — Enter
            {TAB} — Tab
            {ESC} — Escape
        """
        win = self._get_windows()
        win.send_keys(keys)

    @keyword("Hotkey")
    def hotkey(self, *keys):
        """按下组合键。自动转换为 RPA 格式。

        示例:
            Hotkey    ctrl    c
            Hotkey    ctrl    shift    esc
        """
        win = self._get_windows()
        combo = "+".join(keys)
        win.send_keys(combo)

    @keyword("Mouse Move")
    def mouse_move(self, x: int, y: int):
        """移动鼠标到指定坐标。"""
        win = self._get_windows()
        win.move_mouse(str(x), str(y))

    @keyword("Right Click")
    def right_click(self, locator: str = "", x: int = None, y: int = None):
        """右键点击元素或坐标。"""
        win = self._get_windows()
        if locator:
            win.right_click(locator)
        elif x is not None and y is not None:
            win.right_click(str(x), str(y))
        else:
            win.right_click()

    @keyword("Double Click")
    def double_click(self, locator: str = "", x: int = None, y: int = None):
        """双击元素或坐标。"""
        win = self._get_windows()
        if locator:
            win.double_click(locator)
        elif x is not None and y is not None:
            win.double_click(str(x), str(y))
        else:
            win.double_click()

    @keyword("Press Key")
    def press_key(self, key: str):
        """按下一个键（非组合键）。"""
        win = self._get_windows()
        win.send_keys("{" + key.upper() + "}")

    @keyword("Handle Dialog")
    def handle_dialog(self):
        """自动检测并关闭系统对话框。"""
        try:
            from agent.dialog_handler import handle_any_dialog
            result = handle_any_dialog()
            return result != "not_found"
        except Exception:
            return False

    @keyword("Scroll")
    def scroll(self, direction: str = "down", clicks: int = 3):
        """滚动滚轮。direction: up | down"""
        try:
            from pywinauto import mouse
            amount = clicks if direction == "down" else -clicks
            mouse.scroll(coords=(0, 0), wheel_dist=amount)
        except ImportError:
            import pyautogui
            amount = clicks if direction == "down" else -clicks
            pyautogui.scroll(amount)

    @keyword("Drag And Drop")
    def drag_and_drop(self, source: str, target: str):
        """从 source 元素拖拽到 target 元素（支持定位器）。"""
        win = self._get_windows()
        win.drag_and_drop(source, target)

    # ── 窗口管理 ─────────────────────────────────────────────────

    @keyword("Focus Window")
    def focus_window(self, title: str):
        """激活指定标题的窗口。"""
        win = self._get_windows()
        win.foreground_window(title)

    @keyword("Close Window")
    def close_window(self, title: str = ""):
        """关闭指定窗口（空标题关闭当前窗口）。"""
        win = self._get_windows()
        if title:
            win.close_window(title)
        else:
            win.close_current_window()

    @keyword("List Windows")
    def list_windows(self) -> list:
        """列出所有可见窗口。"""
        win = self._get_windows()
        return [w.get("name", "") for w in win.list_windows() if w.get("visible")]

    # ── 等待 ─────────────────────────────────────────────────────

    @keyword("Wait For Seconds")
    def wait_for_seconds(self, seconds: float = 1.0):
        """等待指定秒数。"""
        time.sleep(float(seconds))

    @keyword("Wait Until Element Exists")
    def wait_until_element_exists(self, locator: str, timeout: float = 10.0):
        """等待元素出现。"""
        win = self._get_windows()
        end = time.time() + timeout
        while time.time() < end:
            try:
                el = win.get_element(locator)
                if el:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    # ── 程序管理 ─────────────────────────────────────────────────

    @keyword("Launch Program")
    def launch_program(self, target: str, args: str = ""):
        """启动程序或打开文件/URL。"""
        from RPA.Desktop import Desktop
        Desktop().open_app(target, args)

    @keyword("Capture Screen")
    def screenshot(self, path: str = ""):
        """截取全屏截图。使用 win32api 避免 RPA.Windows 的控件级截图冲突。"""
        import tempfile, os as _os
        dest = path or _os.path.join(tempfile.gettempdir(), "lobster_screenshot_rf.png")
        try:
            from core.screenshot import capture_screen
            capture_screen(dest)
        except Exception:
            try:
                from interaction.actions import ActionHandler
                ActionHandler()._exec_screenshot(dest=dest)
            except Exception:
                import pyautogui
                pyautogui.screenshot(dest)
        return dest

    # ── 文件对话框 ───────────────────────────────────────────────

    @keyword("File Dialog Type Path")
    def file_dialog_type_path(self, path: str):
        """在文件对话框中输入路径（使用窗口控件绑定）。"""
        try:
            from pywinauto import Application
            app = Application(backend="uia").connect(class_name="#32770", visible_only=True, timeout=5)
            dlg = app.top_window()
            dlg.set_focus()
            time.sleep(0.3)
            edit = dlg.child_window(class_name="Edit", found_index=0)
            if edit.exists():
                edit.set_text(path)
                time.sleep(0.2)
                for btn_text in ("打开(&O)", "保存(&S)", "Open", "Save", "选择文件夹"):
                    try:
                        btn = dlg.child_window(text=btn_text, control_type="Button")
                        if btn.exists() and btn.is_visible():
                            btn.click()
                            return True
                    except Exception:
                        continue
                edit.type_keys("{ENTER}")
                return True
        except Exception:
            pass
        # Fallback: clipboard method
        import pyautogui, pyperclip
        old = pyperclip.paste()
        pyperclip.copy(path)
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.hotkey("ctrl", "v")
        pyautogui.press("enter")
        time.sleep(0.5)
        pyautogui.press("enter")
        pyperclip.copy(old)
        return True
