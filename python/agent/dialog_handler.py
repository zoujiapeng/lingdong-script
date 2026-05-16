"""
dialog_handler.py — Windows 对话框处理模块。

纯 win32 API 实现（不依赖 UIA/pywinauto），可靠检测和关闭 Windows 对话框。

局限性：
- 现代 IFileDialog（Windows 8+ 保存/打开对话框）无法通过 win32 API 设置文件名
  或触发保存动作，只能关闭（WM_CLOSE / BM_CLICK）
- 如需保存文件，应直接在 Python 中用文件 I/O 保存，而非触发系统保存对话框
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import win32gui
import win32con

logger = logging.getLogger("agent.dialog_handler")

# ── 对话框分类 ──────────────────────────────────────────────────

# Dialog type constants
SAVE_DIALOG = "save_dialog"
OVERWRITE_CONFIRM = "overwrite_confirm"
NOTEPAD_SAVE_PROMPT = "notepad_save_prompt"
ERROR_DIALOG = "error_dialog"
CONFIRMATION = "confirmation"
MESSAGE = "message"
ELECTRON_DIALOG = "electron_dialog"
UNKNOWN = "unknown"


class DialogInfo:
    """Windows 对话框信息 — 使用 win32gui 检测。"""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.title = (win32gui.GetWindowText(hwnd) or "").strip()
        self.class_name = (win32gui.GetClassName(hwnd) or "").strip()
        self.buttons: list[dict] = []
        self.dialog_type = UNKNOWN
        self._scan_buttons()

    def _scan_buttons(self):
        """扫描对话框内的所有 Button 子窗口。"""
        def _enum(hwnd, _unused):
            try:
                cls = win32gui.GetClassName(hwnd)
                if cls == "Button":
                    text = win32gui.GetWindowText(hwnd)
                    rect = win32gui.GetWindowRect(hwnd)
                    enabled = win32gui.IsWindowEnabled(hwnd)
                    visible = win32gui.IsWindowVisible(hwnd)
                    if visible:
                        self.buttons.append({
                            "hwnd": hwnd,
                            "text": text,
                            "name": text.lower().replace("&", ""),
                            "rect": rect,
                            "enabled": enabled,
                            "visible": visible,
                        })
            except Exception:
                pass
            return True

        win32gui.EnumChildWindows(self.hwnd, _enum, None)

    def classify(self):
        """分类对话框类型。"""
        btn_names = {b["name"] for b in self.buttons}
        title_lower = self.title.lower()

        # Overwrite confirmation
        if "确认另存为" in self.title:
            self.dialog_type = OVERWRITE_CONFIRM
            return

        # Notepad save prompt: "你想将更改保存到..."
        if any(kw in title_lower for kw in ("记事本", "notepad")) and \
           any("保存" in n for n in btn_names) and \
           any("不保存" in n for n in btn_names):
            self.dialog_type = NOTEPAD_SAVE_PROMPT
            return

        # Electron blob dialogs (from the frontend)
        if "blob:http" in title_lower:
            self.dialog_type = ELECTRON_DIALOG
            return

        # Error dialog
        if any(kw in title_lower for kw in ("错误", "出错", "error", "失败")):
            self.dialog_type = ERROR_DIALOG
            return

        # Confirmation
        if btn_names & {"确定", "是", "ok", "yes", "confirm", "是(y)"}:
            self.dialog_type = CONFIRMATION
            return

        # Message
        if any(kw in title_lower for kw in ("提示", "info", "message", "警告", "注意")):
            self.dialog_type = MESSAGE
            return

        # Save dialog — has an Edit/ComboBox child for filename
        if self.class_name == "#32770" and \
           any(kw in title_lower for kw in ("另存为", "保存", "save", "记事本", "notepad")):
            self.dialog_type = SAVE_DIALOG
            return

        self.dialog_type = UNKNOWN

    # ── 按钮查找 ──

    def find_button(self, *keywords: str) -> Optional[dict]:
        """Find a button matching any keyword (case-insensitive, &-stripped)."""
        for btn in self.buttons:
            bn = btn["name"]
            for kw in keywords:
                if kw.lower() in bn:
                    return btn
        return None

    def find_buttons(self, *keywords: str) -> list[dict]:
        return [b for b in self.buttons
                if any(kw.lower() in b["name"] for kw in keywords)]

    def save_button(self) -> Optional[dict]:
        """Find Save/保存 button (NOT 不保存)."""
        for btn in self.buttons:
            if "保存" in btn["name"] and "不" not in btn["name"]:
                return btn
            if btn["name"] in ("save",):
                return btn
        return None

    def dont_save_button(self) -> Optional[dict]:
        """Find 'Don't Save' button."""
        return self.find_button("不保存", "don't save", "dont save", "don")

    def cancel_button(self) -> Optional[dict]:
        return self.find_button("取消", "cancel", "关闭", "close")

    def primary_button(self) -> Optional[dict]:
        return self.find_button("确定", "是", "ok", "yes", "confirm") or self.save_button()

    def has_edit_field(self) -> bool:
        """Check if dialog has an Edit or ComboBox child."""
        found = [False]
        def _enum(hwnd, _unused):
            try:
                cls = win32gui.GetClassName(hwnd)
                if cls in ("Edit", "ComboBox"):
                    found[0] = True
                    return False  # Stop enumeration
            except Exception:
                pass
            return True
        win32gui.EnumChildWindows(self.hwnd, _enum, None)
        return found[0]


# ── 对话框查找 ──────────────────────────────────────────────────


def find_all_dialogs() -> list[DialogInfo]:
    """枚举所有可见的对话框窗口。"""
    dialogs = []

    def _enum(hwnd, _unused):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        try:
            cls = win32gui.GetClassName(hwnd) or ""
            title = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            return True

        is_dialog = False
        if cls in ("#32770", "DirectUIHWND", "TaskDialog"):
            is_dialog = True
        elif any(kw in title.lower() for kw in (
            "另存为", "保存", "save as", "确认", "overwrite",
            "错误", "error", "警告", "提示", "灵动", "记事本",
            "notepad", "确认另存为", "blob:http"
        )):
            is_dialog = True

        if is_dialog:
            info = DialogInfo(hwnd)
            info.classify()
            dialogs.append(info)

        return True

    win32gui.EnumWindows(_enum, None)
    return dialogs


def find_dialog_by_title(keyword: str) -> Optional[DialogInfo]:
    for dlg in find_all_dialogs():
        if keyword.lower() in dlg.title.lower():
            return dlg
    return None


def find_dialog_of_type(dlg_type: str) -> Optional[DialogInfo]:
    for dlg in find_all_dialogs():
        if dlg.dialog_type == dlg_type:
            return dlg
    return None


# ── 对话框操作 ──────────────────────────────────────────────────


def _bm_click(btn_info: dict) -> bool:
    """Send BM_CLICK to a button. Returns True if message was sent."""
    try:
        win32gui.SendMessage(btn_info["hwnd"], win32con.BM_CLICK, 0, 0)
        return True
    except Exception:
        return False


def _send_alt_key(hwnd: int, vk: int):
    """Send Alt+key shortcut to a window via PostMessage."""
    import ctypes
    _PM = ctypes.windll.user32.PostMessageW
    _PM(hwnd, 0x0104, 0x12, 0)   # WM_SYSKEYDOWN Alt
    _PM(hwnd, 0x0104, vk, 0)     # WM_SYSKEYDOWN key
    _PM(hwnd, 0x0105, vk, 0)     # WM_SYSKEYUP key
    _PM(hwnd, 0x0105, 0x12, 0)   # WM_SYSKEYUP Alt


def _is_open(hwnd: int) -> bool:
    try:
        return win32gui.IsWindowVisible(hwnd)
    except Exception:
        return False


def _close_hwnd(hwnd: int):
    """Close a window via WM_CLOSE."""
    try:
        win32gui.SendMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    except Exception:
        pass


# ── 各类型对话框处理 ──


def handle_notepad_save_prompt(dlg: DialogInfo) -> bool:
    """Handle Notepad's 'Do you want to save changes?' dialog.

    Action: Click '不保存' (Don't Save) to discard changes.
    """
    # Method 1: BM_CLICK on "不保存" button
    btn = dlg.dont_save_button()
    if btn and _bm_click(btn):
        time.sleep(0.3)
        return True

    # Method 2: Alt+N (不保存(N) accelerator)
    _send_alt_key(dlg.hwnd, 0x4E)  # VK_N
    time.sleep(0.3)
    if not _is_open(dlg.hwnd):
        return True

    # Method 3: Alt+D (不保存(D) — English)
    _send_alt_key(dlg.hwnd, 0x44)  # VK_D
    time.sleep(0.3)
    if not _is_open(dlg.hwnd):
        return True

    # Method 4: WM_CLOSE to dismiss
    _close_hwnd(dlg.hwnd)
    time.sleep(0.3)
    return True


def handle_overwrite_confirm(dlg: DialogInfo) -> bool:
    """Handle overwrite confirmation — click '是' (Yes) / '替换' (Replace)."""
    for kw in ("是", "替换", "覆盖", "yes", "replace", "overwrite"):
        for btn in dlg.buttons:
            if kw.lower() in btn["name"]:
                if _bm_click(btn):
                    time.sleep(0.3)
                    return True

    # Fallback: click any primary button
    btn = dlg.primary_button()
    if btn and _bm_click(btn):
        time.sleep(0.3)
        return True

    # Fallback: Enter key
    _send_alt_key(dlg.hwnd, 0x0D)  # Enter
    time.sleep(0.3)
    return True


def handle_confirmation(dlg: DialogInfo) -> bool:
    """Handle confirmation dialog — click OK/Yes."""
    btn = dlg.primary_button()
    if btn and _bm_click(btn):
        time.sleep(0.3)
        return True
    _close_hwnd(dlg.hwnd)
    return True


def handle_electron_dialog(dlg: DialogInfo) -> bool:
    """Handle Electron blob dialog — dismiss via WM_CLOSE or Escape."""
    _close_hwnd(dlg.hwnd)
    time.sleep(0.3)
    if _is_open(dlg.hwnd):
        import ctypes
        _PM = ctypes.windll.user32.PostMessageW
        _PM(dlg.hwnd, 0x0100, 0x1B, 0)  # Escape
        _PM(dlg.hwnd, 0x0101, 0x1B, 0)
    return True


def handle_save_dialog(dlg: DialogInfo) -> bool:
    """Handle save dialog.

    NOTE: Modern IFileDialog (Windows 8+) filename field is DirectUI-rendered
    and NOT accessible via win32 API. We cannot set the filename or trigger
    the Save action reliably.

    What we CAN do:
    - Close the dialog with WM_CLOSE (discards unsaved state)
    - The caller should save files programmatically instead of via dialog

    Returns:
        True if dialog was dismissed, False if still open
    """
    _close_hwnd(dlg.hwnd)
    time.sleep(0.3)

    if _is_open(dlg.hwnd):
        import ctypes
        _PM = ctypes.windll.user32.PostMessageW
        _PM(dlg.hwnd, 0x0100, 0x1B, 0)  # Escape
        _PM(dlg.hwnd, 0x0101, 0x1B, 0)
        time.sleep(0.3)

    return not _is_open(dlg.hwnd)


# ── 通用入口 ────────────────────────────────────────────────────


def handle_any_dialog() -> str:
    """查找并处理任何可见的对话框。

    Returns:
        "handled" — 成功处理
        "not_found" — 没有找到对话框
        "unknown" — 找到但无法识别类型
        str(dlg_type) — 特定类型但处理失败
    """
    dialogs = find_all_dialogs()
    if not dialogs:
        return "not_found"

    for dlg in dialogs:
        logger.info("Found: type=%s title=%s btns=%s",
                     dlg.dialog_type, dlg.title,
                     [b["text"] for b in dlg.buttons])

        try:
            if dlg.dialog_type == NOTEPAD_SAVE_PROMPT:
                if handle_notepad_save_prompt(dlg):
                    return "handled"
            elif dlg.dialog_type == SAVE_DIALOG:
                if handle_save_dialog(dlg):
                    return "handled_save_dialog"
                return "save_dialog_unhandled"
            elif dlg.dialog_type == OVERWRITE_CONFIRM:
                if handle_overwrite_confirm(dlg):
                    return "handled"
            elif dlg.dialog_type == ELECTRON_DIALOG:
                if handle_electron_dialog(dlg):
                    return "handled"
            elif dlg.dialog_type in (CONFIRMATION, ERROR_DIALOG, MESSAGE):
                if handle_confirmation(dlg):
                    return "handled"
            else:
                # Unknown dialog — try close
                _close_hwnd(dlg.hwnd)
                time.sleep(0.3)
                if not _is_open(dlg.hwnd):
                    return "handled"
        except Exception as e:
            logger.warning("Error handling %s: %s", dlg.dialog_type, e)

    return UNKNOWN


# ── 诊断 ────────────────────────────────────────────────────────


def dump_all_dialogs() -> list[dict]:
    results = []
    for dlg in find_all_dialogs():
        results.append({
            "hwnd": dlg.hwnd,
            "title": dlg.title,
            "class_name": dlg.class_name,
            "type": dlg.dialog_type,
            "buttons": [b["text"] for b in dlg.buttons],
        })
    return results


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.DEBUG)
    print("=== Current dialogs ===")
    for info in dump_all_dialogs():
        print(json.dumps(info, ensure_ascii=False, indent=2))
