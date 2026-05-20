"""
menu_handler.py — 通用 Windows 菜单操作模块。

不依赖视觉模型。多层降级策略：
  Level 1 (win32): GetMenu + GetMenuStringW → SendMessage(WM_COMMAND)
  Level 2 (keyboard): Alt + 键盘加速键导航（通用，所有 Win32 菜单有效）
  Level 3 (uia): UIA MenuBar/MenuItem 导航

用法:
    handler = MenuHandler()
    handler.click("记事本", ["文件", "打开"])
    handler.click(hwnd, ["View", "Function List"])
"""
from __future__ import annotations

import logging
import time
import ctypes
from typing import Optional, Union

import win32gui
import win32con
import win32api

logger = logging.getLogger("agent.menu_handler")

# ── Windows 常量 ──────────────────────────────────────────────
WM_COMMAND = 0x0111
WM_SYSCOMMAND = 0x0112
VK_MENU = 0x12
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
KEYEVENTF_KEYUP = 0x0002

user32 = ctypes.windll.user32

# ── 标准加速键映射（按位置推测） ────────────────────────────────
# Windows 应用程序中，顶层菜单的加速键几乎永远是第一个字母：
#   File=F, Edit=E, View=V, Search=S, 格式=O, 工具=T 等
# 对于具有标准 Windows 菜单栏的应用，这些是固定约定。
_STANDARD_ACCELERATORS: list[str] = [
    "F", "E", "V", "S", "L", "O", "T", "M", "R", "P", "W", "H", "A", "N", "G", "B", "D"
]


class MenuHandler:
    """通用 Windows 菜单操作器。

    Args:
        strategy_order: 尝试策略顺序，默认 ["win32", "keyboard", "uia"]
        key_delay: 键盘操作间隔（秒），默认 0.05
        menu_open_delay: 菜单展开等待时间（秒），默认 0.3
    """

    def __init__(
        self,
        strategy_order: Optional[list[str]] = None,
        key_delay: float = 0.05,
        menu_open_delay: float = 0.3,
    ):
        self._strategy_order = strategy_order or ["win32", "keyboard", "uia"]
        self._key_delay = key_delay
        self._menu_open_delay = menu_open_delay

    # ── 公开入口 ──────────────────────────────────────────────────

    def click(
        self,
        target: Union[str, int],
        path: list[str],
        timeout: float = 5,
    ) -> bool:
        """按菜单路径点击菜单项。

        Args:
            target: 窗口标题（模糊匹配）或 HWND
            path: 菜单路径，如 ["文件", "打开"] 或 ["View", "Function List"]
            timeout: 超时秒数

        Returns:
            bool — 是否成功
        """
        hwnd = self._resolve_target(target)
        if not hwnd:
            logger.warning("menu_click: target not found: %s", target)
            return False

        logger.info("menu_click: target=0x%x path=%s", hwnd, path)
        deadline = time.time() + timeout

        for level_name in self._strategy_order:
            if time.time() > deadline:
                break
            method_name = f"_level_{level_name}"
            method = getattr(self, method_name, None)
            if not method:
                continue
            try:
                if method(hwnd, path):
                    logger.info("menu_click: OK via %s", level_name)
                    return True
            except Exception as e:
                logger.debug("menu_click: %s failed: %s", level_name, e)
                continue

        logger.warning("menu_click: all levels failed for %s %s", target, path)
        return False

    def get_menu_structure(self, hwnd: int) -> Optional[list]:
        """获取窗口的完整菜单结构（用于调试/诊断）。"""
        menu = user32.GetMenu(hwnd)
        if not menu:
            return None
        return self._enumerate_menu(menu)

    # ── 目标解析 ──────────────────────────────────────────────────

    def _resolve_target(self, target: Union[str, int]) -> Optional[int]:
        """将窗口标题或 HWND 解析为窗口句柄。"""
        if isinstance(target, int):
            return target if user32.IsWindow(target) else None

        # 字符串 → 按标题模糊查找
        found: list[int] = []

        def enum_proc(hwnd: int, _unused) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = (win32gui.GetWindowText(hwnd) or "").strip()
            if not title:
                return True
            if target.lower() in title.lower():
                # 优先选有 WS_CAPTION 的主窗口（排除子窗口）
                style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
                CAPTION = 0x00C00000
                parent = win32gui.GetParent(hwnd)
                if parent == 0 and (style & CAPTION) == CAPTION:
                    found.insert(0, hwnd)  # 主窗口优先
                else:
                    found.append(hwnd)
            return True

        win32gui.EnumWindows(enum_proc, 0)
        return found[0] if found else None

    # ── Level 1: Win32 GetMenu + WM_COMMAND ─────────────────────

    def _level_win32(self, hwnd: int, path: list[str]) -> bool:
        """GetMenu → 遍历子菜单 → SendMessage(WM_COMMAND)。"""
        menu = user32.GetMenu(hwnd)
        if not menu:
            return False

        cmd_id = self._find_command_id(menu, path, 0)
        if cmd_id and cmd_id != -1:
            # 找到命令 ID → 发送 WM_COMMAND
            user32.PostMessageW(hwnd, WM_COMMAND, cmd_id, 0)
            time.sleep(self._menu_open_delay)
            return True
        return False

    def _find_command_id(self, menu: int, path: list[str], depth: int) -> Optional[int]:
        """递归遍历菜单树，匹配文本，返回命令 ID。"""
        count = user32.GetMenuItemCount(menu)
        if count <= 0:
            return None

        target = path[depth]
        target_lower = target.lower().replace("&", "").replace("...", "").strip()

        for i in range(count):
            text = self._get_menu_text(menu, i)
            if text is None:
                continue  # owner-drawn → 无法通过 Level 1 处理

            clean = self._clean_text(text)
            if target_lower not in clean.lower():
                continue

            sub = user32.GetSubMenu(menu, i)
            is_last = depth == len(path) - 1

            if is_last:
                if sub:
                    return None  # 路径指向一个子菜单而非菜单项
                cmd_id = user32.GetMenuItemID(menu, i)
                return cmd_id
            else:
                if sub:
                    return self._find_command_id(sub, path, depth + 1)
                return None

        return None

    @staticmethod
    def _get_menu_text(menu: int, pos: int) -> Optional[str]:
        """安全地获取菜单项文本，owner-drawn 返回 None。"""
        length = user32.GetMenuStringW(menu, pos, None, 0, 1)
        if length == 0:
            # 可能是 owner-drawn 或分隔符
            if user32.GetMenuItemID(menu, pos) == 0 and user32.GetSubMenu(menu, pos) == 0:
                return ""  # 分隔符
            return None  # owner-drawn，无法读取文本
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetMenuStringW(menu, pos, buff, length + 1, 1)
        return buff.value

    @staticmethod
    def _clean_text(text: str) -> str:
        """清理菜单文本中的 & 和 ... 后缀。"""
        return text.replace("&", "").replace("...", "").strip()

    # ── Level 2: Alt + 键盘加速键 ──────────────────────────────
    #
    # 核心原理：Windows 菜单加速键是系统层内置的，不需要读取菜单文本。
    # Alt 激活菜单栏后，直接按加速键字母即可选中对应菜单项。
    # 即使菜单是 owner-drawn（Notepad++、VS Code 等），加速键仍然有效。
    #
    # 策略：
    #   1. Alt 激活菜单栏
    #   2. 对每个 path segment，按目标文本的首字母
    #   3. 如果目标文本是中文（首字母不在 A-Z），尝试标准加速键列表
    #   4. 最后一项按 Enter 确认

    def _level_keyboard(self, hwnd: int, path: list[str]) -> bool:
        """Alt + 加速键导航菜单。"""
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        time.sleep(0.3)

        # 有菜单结构 → 先尝试用文本匹配找位置
        menu = user32.GetMenu(hwnd)
        positions = self._resolve_positions(menu, path) if menu else []

        if positions and all(p is not None for p in positions):
            # 文本匹配成功（标准菜单）→ 方向键导航
            return self._arrow_key_nav(hwnd, positions)
        else:
            # 文本匹配失败（owner-drawn）→ Alt + 加速键
            return self._accelerator_nav(hwnd, path)

    def _resolve_positions(self, menu: int, path: list[str]) -> list[Optional[int]]:
        """遍历菜单树，返回每个 path segment 的位置索引。"""
        positions: list[Optional[int]] = []
        current = menu
        for segment in path:
            pos = self._find_menu_position(current, segment)
            positions.append(pos)
            if pos is not None and pos >= 0:
                sub = user32.GetSubMenu(current, pos)
                if sub and segment != path[-1]:
                    current = sub
        return positions

    def _find_menu_position(self, menu: int, target_text: str) -> Optional[int]:
        """在菜单中按文本匹配查找位置。owner-drawn 项跳过。"""
        count = user32.GetMenuItemCount(menu)
        if count <= 0:
            return None

        target_lower = target_text.lower().replace("&", "").replace("...", "").strip()

        for i in range(count):
            text = self._get_menu_text(menu, i)
            if text is None:
                continue
            clean = self._clean_text(text)
            if target_lower in clean.lower():
                return i

        # 宽松再匹配一次
        for i in range(count):
            text = self._get_menu_text(menu, i)
            if text is None:
                continue
            clean = self._clean_text(text)
            for word in target_lower.split():
                if len(word) >= 2 and word in clean.lower():
                    return i
        return None

    def _arrow_key_nav(self, hwnd: int, positions: list[int]) -> bool:
        """已知位置时，用方向键 + Enter 导航。"""
        # Alt 激活菜单栏
        self._press_key(VK_MENU)
        time.sleep(0.3)

        # 移动到第一个顶层菜单位置
        for _ in range(positions[0]):
            self._press_key(VK_RIGHT)

        # 展开子菜单
        self._press_key(VK_DOWN)
        time.sleep(self._menu_open_delay)

        # 后续层级
        for depth in range(1, len(positions)):
            for _ in range(positions[depth]):
                self._press_key(VK_DOWN)

            if depth < len(positions) - 1:
                self._press_key(VK_RIGHT)
                time.sleep(self._menu_open_delay)
            else:
                self._press_key(VK_RETURN)

        return True

    def _accelerator_nav(self, hwnd: int, path: list[str]) -> bool:
        """Alt + 加速键导航（不依赖文本读取，适用于 owner-drawn 菜单）。

        原理：Windows 菜单栏激活后，按键直接传递给菜单系统作加速键匹配。
        """
        # Alt 激活菜单栏
        self._press_key(VK_MENU)
        time.sleep(0.3)

        for i, segment in enumerate(path):
            first = segment[0].upper()
            if 'A' <= first <= 'Z':
                # 首字母是英文字母 → 直接作为加速键发送
                self._press_key(ord(first))
                time.sleep(self._menu_open_delay)
            else:
                # 中文文本 → 尝试标准 Windows 加速键
                # 先试菜单位置对应的标准加速键
                matched = False
                # 从标准加速键列表中找：按顺序比较相似的菜单名称
                for accel in _STANDARD_ACCELERATORS:
                    self._press_key(ord(accel))
                    time.sleep(0.2)
                    # 检测菜单是否成功展开：检查是否出现了菜单弹窗（#32768）
                    if self._menu_popup_detected(hwnd):
                        matched = True
                        break
                    # 没反应->Escape取消再试下一个
                    self._press_key(VK_ESCAPE)
                    time.sleep(0.15)

                if not matched:
                    return False

            # 最后一级直接按 Enter
            if i == len(path) - 1:
                time.sleep(0.2)
                # 有些人可能会再按一次字母（如果没展开的话）
                if 'A' <= first <= 'Z':
                    self._press_key(ord(first))
                    time.sleep(0.2)
                self._press_key(VK_RETURN)

        return True

    @staticmethod
    def _menu_popup_detected(hwnd: int) -> bool:
        """检测菜单是否已弹出（通过检查 #32768 类窗口是否存在）。"""
        found = [False]
        def enum_proc(wnd: int, _unused) -> bool:
            if not win32gui.IsWindowVisible(wnd):
                return True
            cls = win32gui.GetClassName(wnd)
            if cls == "#32768":
                found[0] = True
                return False
            return True
        win32gui.EnumWindows(enum_proc, 0)
        # 也检测前景窗口是否为菜单弹窗
        fore = win32gui.GetForegroundWindow()
        if fore == hwnd:
            return True
        cls = win32gui.GetClassName(fore)
        return cls == "#32768"

    # ── Level 3: UIA 菜单导航 ──────────────────────────────────

    def _level_uia(self, hwnd: int, path: list[str]) -> bool:
        """UIA 枚举 MenuBar → MenuItem → Invoke。"""
        try:
            import uiautomation as uia
        except ImportError:
            return False

        try:
            # 通过 HWND 获取 UIA 窗口
            window = uia.ControlFromHandle(hwnd)
            if not window.Exists(0, 0):
                return False

            # 找 MenuBar
            menu_bar = None
            for ctrl_type in ["MenuItem", "MenuBar"]:
                try:
                    menu_bar = window.MenuBarControl(searchDepth=2)
                    if menu_bar.Exists(0, 0):
                        break
                except Exception:
                    continue

            if not menu_bar:
                return False

            current = menu_bar
            for depth, segment in enumerate(path):
                found = False
                children = current.GetChildren() if hasattr(current, "GetChildren") else []
                if not children:
                    return False

                for child in children:
                    name = child.Name or ""
                    if segment.lower() in name.lower():
                        if depth == len(path) - 1:
                            try:
                                child.Click()
                                return True
                            except Exception:
                                try:
                                    child.Invoke()
                                    return True
                                except Exception:
                                    return False
                        else:
                            # 展开子菜单
                            try:
                                child.Click()
                                time.sleep(self._menu_open_delay)
                                current = child
                                found = True
                                break
                            except Exception:
                                return False
                if not found:
                    return False

            return True
        except Exception:
            return False

    # ── 键盘辅助 ──────────────────────────────────────────────────

    def _press_key(self, vk: int):
        """按下再释放一个键。"""
        self._key_down(vk)
        time.sleep(self._key_delay)
        self._key_up(vk)
        time.sleep(self._key_delay)

    def _press_alt_key(self, vk: int):
        """按下 Alt+Key 组合。"""
        self._key_down(VK_MENU)
        time.sleep(self._key_delay)
        self._key_down(vk)
        time.sleep(self._key_delay)
        self._key_up(vk)
        time.sleep(self._key_delay)
        self._key_up(VK_MENU)
        time.sleep(self._menu_open_delay)

    @staticmethod
    def _key_down(vk: int):
        win32api.keybd_event(vk, 0, 0, 0)

    @staticmethod
    def _key_up(vk: int):
        win32api.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

    # ── 菜单诊断 ──────────────────────────────────────────────────

    def _enumerate_menu(self, menu: int, depth: int = 0) -> list:
        """递归枚举菜单结构（调试用）。"""
        count = user32.GetMenuItemCount(menu)
        items = []
        for i in range(count):
            text = self._get_menu_text(menu, i)
            sub = user32.GetSubMenu(menu, i)
            cmd_id = user32.GetMenuItemID(menu, i)

            entry = {"pos": i, "cmd_id": cmd_id}

            if text is None:
                entry["text"] = None  # owner-drawn
            elif text == "" and cmd_id == 0 and sub == 0:
                entry["text"] = "---separator---"
            else:
                entry["text"] = text

            if sub:
                entry["children"] = self._enumerate_menu(sub, depth + 1)

            items.append(entry)
        return items


# ── 便捷函数 ──────────────────────────────────────────────────

_handler = MenuHandler()


def menu_click(target: Union[str, int], path: list[str], timeout: float = 5) -> bool:
    """按菜单路径点击菜单项。

    Args:
        target: 窗口标题或 HWND
        path: 菜单路径，如 ["文件", "打开"]
        timeout: 超时秒数

    Returns:
        bool — 是否成功
    """
    return _handler.click(target, path, timeout)


def get_menu_structure(target: Union[str, int]) -> Optional[list]:
    """获取窗口的完整菜单结构。"""
    if isinstance(target, int):
        hwnd = target
    else:
        hwnd = _handler._resolve_target(target)
    if not hwnd:
        return None
    return _handler.get_menu_structure(hwnd)
