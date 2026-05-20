"""
AppAdapter 基类 — 桌面应用适配器框架
子类只需覆盖少量应用特有逻辑即可完成适配器：
  - app_name / display_name
  - _match_window(hwnd, title, cls) → bool
  - _launch_command() → list[str] | None
"""
from __future__ import annotations
import os
import subprocess
import time
import logging
from typing import Optional, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AdapterWindowInfo:
    hwnd: int = 0
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0
    title: str = ""


class AppAdapter:
    """桌面应用适配器基类"""

    # ── 子类覆盖 ──────────────────────────────────────────────
    app_name = "app"
    display_name = "应用"

    def _match_window(self, hwnd: int, title: str, cls: str) -> bool:
        """判断 hwnd 是否为目标应用的主窗口。子类必须覆盖。"""
        raise NotImplementedError

    def _launch_command(self) -> Optional[list[str]]:
        """返回启动命令列表，None 表示不支持自动启动。子类可选覆盖。"""
        return None

    # ── 窗口查找 ───────────────────────────────────────────────

    def find_window(self) -> Optional[int]:
        """查找目标应用的主窗口 HWND。"""
        try:
            import win32gui
            candidates = []

            def enum_callback(hwnd, results):
                try:
                    if not win32gui.IsWindowVisible(hwnd):
                        return
                    title = win32gui.GetWindowText(hwnd) or ""
                    cls = win32gui.GetClassName(hwnd) or ""
                    if not self._match_window(hwnd, title, cls):
                        return
                    r = win32gui.GetWindowRect(hwnd)
                    w, h = int(r[2] - r[0]), int(r[3] - r[1])
                    if w <= 100 or h <= 100:
                        return
                    has_content = self._capture_has_content((int(r[0]), int(r[1]), w, h))
                    score = (1 if has_content else 0, w * h)
                    results.append((score, hwnd))
                except Exception:
                    pass

            win32gui.EnumWindows(enum_callback, candidates)
            if candidates:
                candidates.sort(reverse=True)
                return candidates[0][1]
            return None
        except Exception:
            return None

    def get_window_info(self, hwnd: Optional[int] = None) -> Optional[AdapterWindowInfo]:
        """获取窗口信息。"""
        hwnd = hwnd or self.find_window()
        if not hwnd:
            return None
        try:
            import win32gui
            title = win32gui.GetWindowText(hwnd) or ""
            rect = win32gui.GetWindowRect(hwnd)
            return AdapterWindowInfo(
                hwnd=hwnd,
                left=int(rect[0]), top=int(rect[1]),
                width=int(rect[2] - rect[0]),
                height=int(rect[3] - rect[1]),
                title=title,
            )
        except Exception:
            return None

    def wait_window(self, timeout: float = 20) -> Optional[int]:
        """等待窗口出现。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            hwnd = self.find_window()
            if hwnd:
                return hwnd
            time.sleep(1)
        return None

    # ── 窗口聚焦 ───────────────────────────────────────────────

    def bring_to_front(self, hwnd: Optional[int] = None):
        """将窗口带到前台。"""
        hwnd = hwnd or self.find_window()
        if not hwnd:
            return
        try:
            import win32gui
            import win32con
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.15)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.15)
        except Exception:
            pass

    def is_foreground(self, hwnd: Optional[int] = None) -> bool:
        """判断窗口是否在前台。"""
        try:
            import win32gui
            fg = win32gui.GetForegroundWindow()
            if hwnd and fg == hwnd:
                return True
            return self._same_process(fg, hwnd or self.find_window())
        except Exception:
            return False

    def ensure_foreground(self, hwnd: Optional[int] = None, timeout: float = 2.0) -> bool:
        """确保窗口在前台。"""
        hwnd = hwnd or self.find_window()
        if not hwnd:
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_foreground(hwnd):
                return True
            self.bring_to_front(hwnd)
            time.sleep(0.2)
        return self.is_foreground(hwnd)

    # ── 启动 ───────────────────────────────────────────────────

    def _is_process_running(self) -> bool:
        """检查应用进程是否已在运行。"""
        cmd = self._launch_command()
        if not cmd:
            return False
        exe_name = os.path.basename(cmd[0]).lower()
        try:
            import psutil
            for p in psutil.process_iter(["name"]):
                try:
                    if p.info["name"] and p.info["name"].lower() == exe_name:
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def launch(self) -> bool:
        """启动应用。进程已在运行时不重复启动，仅聚焦窗口。"""
        hwnd = self.find_window()
        if hwnd:
            self.bring_to_front(hwnd)
            return True

        # 进程已运行但窗口未找到 → 等待窗口出现
        if self._is_process_running():
            logger.info("%s 进程已在运行，等待窗口出现", self.app_name)
            hwnd = self.wait_window(timeout=10)
            if hwnd:
                self.bring_to_front(hwnd)
                return True

        cmd = self._launch_command()
        if not cmd:
            return False
        try:
            subprocess.Popen(cmd, shell=False)
            time.sleep(2)
            return True
        except Exception as e:
            logger.error("启动 %s 失败: %s", self.app_name, e)
            return False

    # ── 主窗口区域 ─────────────────────────────────────────────

    def get_region(self, hwnd: Optional[int] = None) -> Optional[tuple[int, int, int, int]]:
        """获取窗口区域 (x, y, w, h)。"""
        info = self.get_window_info(hwnd)
        if not info:
            return None
        return (info.left, info.top, info.width, info.height)

    # ── 感知辅助 ───────────────────────────────────────────────

    def find_element(self, query: str, timeout: float = 5, threshold: float = 0.6) -> "FusionResult":
        """在窗口区域内融合定位元素。"""
        from perception.fusion import get_fusion_engine
        engine = get_fusion_engine()
        region = self.get_region()
        return engine.wait_element(query, region=region, timeout=timeout, threshold=threshold)

    def find_ocr(self, query: str, timeout: float = 2, threshold: float = 0.45) -> list:
        """在窗口区域内 OCR 查找文字。"""
        end = time.time() + timeout
        while time.time() <= end:
            region = self.get_region()
            if not region:
                time.sleep(0.25)
                continue
            self._wait_screen_ready(timeout=1.0)
            try:
                from perception.ocr import OCREngine
                ocr = OCREngine()
                matches = ocr.find_all_text(query, region=region, threshold=threshold)
                if matches:
                    return matches
            except Exception:
                pass
            time.sleep(0.25)
        return []

    def _wait_screen_ready(self, hwnd: Optional[int] = None, timeout: float = 3.0) -> bool:
        """等待屏幕内容就绪（非黑屏）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            region = self.get_region(hwnd)
            if self._capture_has_content(region):
                return True
            try:
                from perception.vision import ScreenCapture
                ScreenCapture.clear_cache()
            except Exception:
                pass
            time.sleep(0.25)
        return False

    # ── 交互辅助 ───────────────────────────────────────────────

    def click(self, x: int, y: int, delay: float = 0.3):
        """点击窗口内绝对坐标。"""
        try:
            import pyautogui
            pyautogui.click(int(x), int(y))
            time.sleep(delay)
        except Exception:
            pass

    def hotkey(self, *keys):
        """在窗口前台发送快捷键。"""
        if not self.ensure_foreground():
            raise RuntimeError(f"{self.display_name} 不在前台")
        import pyautogui
        pyautogui.hotkey(*keys)

    def press(self, key: str):
        """在窗口前台按键。"""
        if not self.ensure_foreground():
            raise RuntimeError(f"{self.display_name} 不在前台")
        import pyautogui
        pyautogui.press(key)

    def type_via_clipboard(self, text: str):
        """通过剪贴板输入文字（绕过 IME）。"""
        try:
            import pyperclip
            old = pyperclip.paste()
            pyperclip.copy(text)
            time.sleep(0.05)
            self.hotkey("ctrl", "v")
            time.sleep(0.1)
            pyperclip.copy(old)
        except Exception:
            pass

    # ── 内部工具 ───────────────────────────────────────────────

    def _capture_has_content(self, region=None) -> bool:
        """判断屏幕区域是否有内容（非黑屏）。"""
        try:
            import numpy as np
            from perception.vision import ScreenCapture
            img = ScreenCapture.capture(region)
            if img is None or img.size == 0:
                return False
            gray = img.mean(axis=2) if img.ndim == 3 else img
            return float(np.mean(gray)) > 8.0 and float(np.std(gray)) > 3.0
        except Exception:
            return True

    def _same_process(self, hwnd_a: Optional[int], hwnd_b: Optional[int]) -> bool:
        """判断两个窗口是否同进程。"""
        if not hwnd_a or not hwnd_b:
            return False
        try:
            import win32gui
            import win32process
            return (
                win32process.GetWindowThreadProcessId(hwnd_a)[1]
                == win32process.GetWindowThreadProcessId(hwnd_b)[1]
            )
        except Exception:
            return hwnd_a == hwnd_b

    def _is_valid_click_point(self, x: int, y: int) -> bool:
        """判断点击坐标是否在屏幕范围内。"""
        try:
            import pyautogui
            sw, sh = pyautogui.size()
            if sw <= 0 or sh <= 0:
                return True
            return 0 <= int(x) < sw and 0 <= int(y) < sh
        except Exception:
            return True
