"""
dialog_handler.py — Windows 对话框处理模块（UIA + win32 双层）。

策略（从强到弱）:
  Level 1: uiautomation 枚举 ButtonControl → 文字匹配 → Click()
  Level 2: win32 EnumChildWindows + BM_CLICK (Button class)
  Level 3: 键盘模拟 (Escape → Enter → Alt+F4)
  Level 4: WM_CLOSE

自绘界面检测：如果窗口全是 Pane 无 Button → 标记为 UNHANDLABLE → 暂停等待人工。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

import pythoncom
import win32gui
import win32con

logger = logging.getLogger("agent.dialog_handler")

# ── 对话框分类 ──────────────────────────────────────────────────

SAVE_DIALOG = "save_dialog"
OVERWRITE_CONFIRM = "overwrite_confirm"
NOTEPAD_SAVE_PROMPT = "notepad_save_prompt"
ERROR_DIALOG = "error_dialog"
CONFIRMATION = "confirmation"
MESSAGE = "message"
ELECTRON_DIALOG = "electron_dialog"
UNKNOWN = "unknown"
UNHANDLABLE = "unhandlable"  # 自绘界面，无法自动处理

# ── 多语言按钮文字库 ────────────────────────────────────────────

# (keyword, priority) — priority 越高越优先匹配
_BUTTON_KEYWORDS: list[tuple[str, int]] = [
    # 保存类
    ("保存", 50), ("save", 50), ("save as", 50),
    # 不保存
    ("不保存", 60), ("don't save", 60), ("dont save", 60), ("don", 30),
    # 确认/肯定
    ("确定", 50), ("是", 50), ("yes", 50), ("ok", 40), ("confirm", 40),
    # 否定/取消
    ("取消", 50), ("cancel", 50), ("no", 50), ("否", 50), ("关闭", 40), ("close", 40),
    # 重试
    ("重试", 50), ("retry", 50), ("再试一次", 50),
    # 忽略
    ("忽略", 50), ("ignore", 50),
    # 放弃
    ("放弃", 50), ("discard", 50), ("abort", 50),
    # 应用
    ("应用", 40), ("apply", 40),
    # 替换/覆盖
    ("替换", 50), ("覆盖", 50), ("replace", 50), ("overwrite", 50),
    # 继续
    ("继续", 40), ("continue", 40),
    # 允许/授权
    ("允许", 40), ("allow", 40), ("授权", 40),
    # 拒绝
    ("拒绝", 40), ("deny", 40), ("block", 40),
    # 下载
    ("下载", 40), ("download", 40),
    # 运行
    ("运行", 40), ("run", 40), ("启动", 40),
]

# 需要避免误匹配的否定前缀
_NEGATIVE_PREFIXES = ("不", "don", "skip", "忽略")


def _match_button_keyword(text: str) -> tuple[Optional[str], int]:
    """对按钮文字匹配关键字库，返回 (keyword, priority) 或 (None, 0)。"""
    t = text.lower().replace("&", "").strip()
    for kw, pri in _BUTTON_KEYWORDS:
        if kw.lower() in t:
            # 如果匹配到的是否定词（不保存/不替换），确保 text 确实含有"不"前缀
            return kw, pri
    return None, 0


def _sort_buttons_by_priority(buttons: list[dict]) -> list[dict]:
    """按关键字优先级排序按钮（高优先在前）。"""
    def _key(b):
        _, pri = _match_button_keyword(b.get("text", ""))
        return -pri
    return sorted(buttons, key=_key)


def _get_buttons_safe(dlg) -> list[dict]:
    """安全获取对话框按钮列表，兼容新旧 DialogInfo 和 Mock 对象。"""
    # 真实 DialogInfo: 有 buttons_win32 属性，用 all_buttons() 获取完整列表
    if hasattr(dlg, "buttons_win32"):
        try:
            if hasattr(dlg, "all_buttons"):
                result = dlg.all_buttons()
                if isinstance(result, list):
                    return result
        except Exception:
            pass
        return getattr(dlg, "buttons_win32", [])
    # Mock 对象: buttons 直接存储为实例属性
    btns = getattr(dlg, "buttons", None)
    return btns if isinstance(btns, list) else []


# ── UIA 工具函数 ────────────────────────────────────────────────


def _uia_available() -> bool:
    try:
        import uiautomation as uia
        return True
    except ImportError:
        return False


def _ensure_com():
    """确保当前线程 COM 已初始化（uiautomation 需要）。"""
    try:
        import pythoncom
        pythoncom.CoInitializeEx(0)
    except Exception:
        pass


def _uia_get_buttons(hwnd: int) -> list[dict]:
    """使用 uiautomation 获取窗口内的 ButtonControl 列表。

    能检测到 DirectUI 渲染的按钮（win32 Button class 找不到的）。
    """
    import uiautomation as uia

    # 确保 COM 已初始化（子线程中调用此函数需要）
    _ensure_com()

    buttons = []

    def _search(ctrl, depth=0):
        if depth > 8:
            return
        try:
            if ctrl.ControlType == 5:  # ButtonControl
                name = (ctrl.Name or "").strip()
                if name:  # 只收集有文字的按钮
                    buttons.append({
                        "uia": ctrl,
                        "text": name,
                        "enabled": ctrl.IsEnabled,
                        "visible": ctrl.IsVisible,
                    })
            # 递归查找所有子控件（不限于 Button，因为可能嵌套）
            for child in ctrl.GetChildren():
                _search(child, depth + 1)
        except Exception:
            pass

    try:
        root = uia.GetRootControl()
        # 查找目标窗口
        target = _uia_find_window_by_hwnd(root, hwnd)
        if target:
            _search(target)
    except Exception:
        pass

    return buttons


def _uia_find_window_by_hwnd(root, hwnd: int, depth=0):
    """在 UIA 树中按窗口句柄查找 Control。"""
    if depth > 6:
        return None
    try:
        if hasattr(root, 'NativeWindowHandle') and root.NativeWindowHandle == hwnd:
            return root
        for child in root.GetChildren():
            r = _uia_find_window_by_hwnd(child, hwnd, depth + 1)
            if r:
                return r
    except Exception:
        pass
    return None


def _uia_click_button(btn_info: dict) -> bool:
    """通过 UIA Click() 点击按钮。"""
    try:
        uia = btn_info.get("uia")
        if uia:
            uia.Click()
            return True
    except Exception:
        pass
    return False


def _uia_get_window_title(hwnd: int) -> str:
    """获取 UIA 窗口标题（对某些 DirectUI 窗口比 win32 更准确）。"""
    import uiautomation as uia
    _ensure_com()
    try:
        root = uia.GetRootControl()
        target = _uia_find_window_by_hwnd(root, hwnd)
        if target:
            return (target.Name or "").strip()
    except Exception:
        pass
    return ""


def _uia_has_only_panes(hwnd: int) -> Optional[bool]:
    """检测窗口是否全是 Pane 控件（自绘界面特征）。

    Returns:
        True — 找到窗口且只有 Pane
        False — 找到窗口且有交互控件
        None — 无法在 UIA 树中找到该窗口
    """
    import uiautomation as uia
    _ensure_com()
    found_button = False
    found_edit = False
    found_combo = False
    window_found = False

    def _scan(ctrl, depth=0):
        nonlocal found_button, found_edit, found_combo, window_found
        window_found = True
        if depth > 6:
            return
        try:
            ct = ctrl.ControlType
            if ct == 5:
                found_button = True
            elif ct == 4:
                found_edit = True
            elif ct == 6:
                found_combo = True
            if found_button or found_edit or found_combo:
                return
            for child in ctrl.GetChildren():
                _scan(child, depth + 1)
        except Exception:
            pass

    try:
        root = uia.GetRootControl()
        target = _uia_find_window_by_hwnd(root, hwnd)
        if target:
            _scan(target)
    except Exception:
        pass

    if not window_found:
        return None
    return not (found_button or found_edit or found_combo)


# ── DialogInfo ──────────────────────────────────────────────────


class DialogInfo:
    """Windows 对话框信息 — 使用 win32 + UIA 双重检测。"""

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.title = (win32gui.GetWindowText(hwnd) or "").strip()
        self.class_name = (win32gui.GetClassName(hwnd) or "").strip()
        # win32 按钮（Button class）
        self.buttons_win32: list[dict] = []
        # UIA 按钮（ButtonControl，能检测 DirectUI）
        self.buttons_uia: list[dict] = []
        self._uia_scanned = False
        self.dialog_type = UNKNOWN
        self._scan_buttons_win32()
        # UIA scanning is lazy (called on first all_buttons() call)

    @property
    def buttons(self) -> list[dict]:
        """向后兼容: all_buttons() 的 property 别名。"""
        return self.all_buttons()

    @buttons.setter
    def buttons(self, value: list[dict]):
        """向后兼容: 允许 test 直接设置 self.buttons = [...]。"""
        self.buttons_win32 = value

    def all_buttons(self) -> list[dict]:
        """合并 win32 + UIA 按钮，UIA 优先（去重）。"""
        if not self._uia_scanned and self.class_name not in ("#32770",):
            # 对非标准对话框类（DirectUI/自绘）才扫描 UIA 按钮
            try:
                self._scan_buttons_uia()
            except Exception:
                pass
            self._uia_scanned = True
        seen_texts = set()
        result = []
        for btn in self.buttons_uia:
            key = btn["text"].lower().replace("&", "")
            if key not in seen_texts:
                seen_texts.add(key)
                result.append(btn)
        for btn in self.buttons_win32:
            key = btn["text"].lower().replace("&", "")
            if key not in seen_texts:
                seen_texts.add(key)
                result.append(btn)
        return result

    def _scan_buttons_win32(self):
        """扫描对话框内的所有 Button 子窗口（win32）。"""
        def _enum(hwnd, _unused):
            try:
                cls = win32gui.GetClassName(hwnd)
                if cls == "Button":
                    text = win32gui.GetWindowText(hwnd)
                    rect = win32gui.GetWindowRect(hwnd)
                    enabled = win32gui.IsWindowEnabled(hwnd)
                    visible = win32gui.IsWindowVisible(hwnd)
                    if visible:
                        self.buttons_win32.append({
                            "hwnd": hwnd,
                            "text": text,
                            "name": text.lower().replace("&", ""),
                            "rect": rect,
                            "enabled": enabled,
                            "visible": visible,
                            "source": "win32",
                        })
            except Exception:
                pass
            return True
        win32gui.EnumChildWindows(self.hwnd, _enum, None)

    def _scan_buttons_uia(self):
        """使用 UIA 扫描按钮（能检测 DirectUI 渲染的按钮）。"""
        if not _uia_available():
            return
        try:
            raw = _uia_get_buttons(self.hwnd)
            for b in raw:
                self.buttons_uia.append({
                    **b,
                    "name": b["text"].lower().replace("&", ""),
                    "source": "uia",
                })
        except Exception:
            pass

    # ── 对话框类型分类 ──

    def classify(self):
        """分类对话框类型（综合 title + 按钮信息）。"""
        title_lower = self.title.lower()
        all_btns = self.all_buttons()
        btn_names = {b["name"] for b in all_btns}

        # Overwrite confirmation (title-based, high priority)
        if "确认另存为" in self.title:
            self.dialog_type = OVERWRITE_CONFIRM
            return

        # Notepad save prompt
        if any(kw in title_lower for kw in ("记事本", "notepad")) and \
           any("保存" in n for n in btn_names) and \
           any("不保存" in n for n in btn_names):
            self.dialog_type = NOTEPAD_SAVE_PROMPT
            return

        # Electron blob dialogs
        if "blob:http" in title_lower:
            self.dialog_type = ELECTRON_DIALOG
            return

        # 错误对话框 (title-based)
        if any(kw in title_lower for kw in ("错误", "出错", "error", "失败", "fatal",
                                            "crash", "异常", "exception")):
            self.dialog_type = ERROR_DIALOG
            return

        # 确认对话框（有确定/是/OK 按钮）
        if btn_names & {"确定", "是", "ok", "yes", "confirm", "是(y)", "确定(y)"}:
            self.dialog_type = CONFIRMATION
            return

        # 信息/提示对话框
        if any(kw in title_lower for kw in ("提示", "info", "message", "警告", "注意",
                                            "warning", "information", "notice")):
            self.dialog_type = MESSAGE
            return

        # 保存对话框
        if self.class_name == "#32770" and \
           any(kw in title_lower for kw in ("另存为", "保存", "save", "记事本", "notepad")):
            self.dialog_type = SAVE_DIALOG
            return

        # 通用：有按钮的未知对话框
        if all_btns:
            self.dialog_type = UNKNOWN
            return

        # 自绘界面检测：无任何按钮且 UIA 确认找到窗口但只有 Pane
        if _uia_available() and self.class_name not in ("#32770",):
            # 只对非标准对话框类做自绘检测（#32770 是标准 win32 对话框类）
            try:
                result = _uia_has_only_panes(self.hwnd)
                if result is True:  # 找到窗口且只有 Pane → 自绘界面
                    self.dialog_type = UNHANDLABLE
                    return
            except Exception:
                pass

        # 最终：无法分类的窗口标记为 UNKNOWN（仍可尝试键盘降级/WM_CLOSE）
        self.dialog_type = UNKNOWN

    # ── 按钮查找（综合 UIA + win32）─

    def find_button(self, *keywords: str) -> Optional[dict]:
        """在所有按钮中按文字匹配查找（UIA 优先）。"""
        for btn in self.all_buttons():
            bn = btn["name"]
            for kw in keywords:
                if kw.lower() in bn:
                    return btn
        return None

    def find_buttons(self, *keywords: str) -> list[dict]:
        return [b for b in self.all_buttons()
                if any(kw.lower() in b["name"] for kw in keywords)]

    def save_button(self) -> Optional[dict]:
        for btn in self.all_buttons():
            if "保存" in btn["name"] and "不" not in btn["name"]:
                return btn
            if btn["name"] in ("save", "save as"):
                return btn
        return None

    def dont_save_button(self) -> Optional[dict]:
        return self.find_button("不保存", "don't save", "dont save", "don")

    def cancel_button(self) -> Optional[dict]:
        return self.find_button("取消", "cancel", "关闭", "close")

    def primary_button(self) -> Optional[dict]:
        """找"确定"/"是"/OK 类按钮。"""
        return (self.find_button("确定", "是", "ok", "yes", "confirm")
                or self.save_button())

    def has_edit_field(self) -> bool:
        """Check if dialog has an Edit or ComboBox child."""
        found = [False]
        def _enum(hwnd, _unused):
            try:
                cls = win32gui.GetClassName(hwnd)
                if cls in ("Edit", "ComboBox"):
                    found[0] = True
                    return False
            except Exception:
                pass
            return True
        win32gui.EnumChildWindows(self.hwnd, _enum, None)
        return found[0]


# ── 已知非对话框的系统窗口类名排除列表 ────────────────────────

_SYSTEM_WINDOW_CLASSES = {
    "Shell_TrayWnd", "Desktop", "Progman", "WorkerW",
    "SysListView32", "ToolbarWindow32", "Button", "Static",
    "msctls_statusbar32", "msctls_progress32", "msctls_trackbar32",
    "msctls_updown32", "SysHeader32", "SysTabControl32",
    "SysTreeView32", "SysAnimate32", "SysIPAddress32",
    "DummyDWMListenerWindow", "EdgeUiInputTopWndClass",
    "ThumbnailDeviceHelperWnd", "Windows.UI.Core.CoreWindow",
    "CiceroUIWndFrame", "TF_FloatingLangBar_WndTitle",
    "NotifyIconOverflowWindow", "PseudoConsoleWindow",
    "ApplicationManager_DesktopShellWindow",
    "ImmersiveLauncher", "Xaml_WindowedPopupClass",
    "Windows.Shell.ServiceWindow", "CoreWindowInputPane",
    "SystemSettings", "SearchPane", "MultitaskingViewFrame",
    "SnappedDesktop", "FlyoutNavPane",
    "Qt51515QWindowToolTipSaveBits", "Qt51515QWindowIcon",
    # 终端/控制台窗口（常被误检为对话框）
    "ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS",
    "WindowsTerminal", "VirtualConsoleClass",
    "XConsoleWindowClass", "WinConsoleWindowClass",
    "ConEmu", "mintty", "PuTTY",
    "WslWindowClass", "VcXsrv",
}

# 广泛匹配的弹窗标题关键词（多语言）
_DIALOG_TITLE_KEYWORDS = [
    "另存为", "保存", "save as", "save", "确认", "overwrite", "确认另存为",
    "错误", "error", "警告", "warning", "提示", "info", "message", "灵动",
    "记事本", "notepad", "blob:http",
    "打开", "open", "选择文件夹", "浏览文件夹", "browse",
    "确认", "confirm", "询问", "question",
    "安全", "security", "警告", "alert",
    "安装", "setup", "install", "update", "更新",
    "下载", "download", "上传", "upload",
    "打印", "print",
    "属性", "properties", "设置", "settings", "preferences",
    "登录", "login", "sign in", "注册", "register",
    "发现", "discovery", "新版本", "new version",
    "连接", "connect", "disconnect", "断开",
    "删除", "delete", "remove", "移除",
    "导入", "import", "export", "导出",
]


def _is_popup_window(hwnd: int) -> bool:
    """检测窗口是否为弹出式对话框（排除已知系统窗口）。"""
    try:
        cls = win32gui.GetClassName(hwnd) or ""

        # 立即排除已知系统窗口类
        if cls in _SYSTEM_WINDOW_CLASSES:
            return False

        # 无类名窗口排除
        if not cls:
            return False

        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)

        # 对话框类
        if cls in ("#32770", "DirectUIHWND", "TaskDialog", "ToastWindowClass"):
            # #32770 也可能是子面板（如 Notepad++ 停靠面板），排除 WS_CHILD
            if cls == "#32770":
                style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
                if style & win32con.WS_CHILD:
                    return False
            return True

        # 标准弹窗检测（WS_POPUP）
        if style & win32con.WS_POPUP:
            # 排除没有标题的不可交互后台窗口
            title = win32gui.GetWindowText(hwnd) or ""
            if not title:
                return False
            return True

        # WS_CAPTION 的小窗口也可能是对话框
        if style & win32con.WS_CAPTION:
            rect = win32gui.GetWindowRect(hwnd)
            w, h = rect[2] - rect[0], rect[3] - rect[1]
            title = win32gui.GetWindowText(hwnd) or ""
            if w < 800 and h < 600 and title:
                return True

        # 工具窗口样式 + 有标题
        if ex_style & win32con.WS_EX_TOOLWINDOW:
            title = win32gui.GetWindowText(hwnd) or ""
            if title:
                return True

    except Exception:
        pass
    return False


def find_all_dialogs() -> list[DialogInfo]:
    """枚举所有可见的对话框窗口（广泛扫描 + UIA 辅助）。"""
    dialogs = []

    def _enum(hwnd, _unused):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        try:
            cls = win32gui.GetClassName(hwnd) or ""
            title = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            return True

        # 排除已知系统窗口类（快速路径）
        if cls in _SYSTEM_WINDOW_CLASSES:
            return True

        is_dialog = False

        # 条件1：已知对话框类
        if cls in ("#32770", "DirectUIHWND", "TaskDialog", "ToastWindowClass"):
            # #32770 也可能是子面板（如 Notepad++ 停靠面板），排除 WS_CHILD
            if cls == "#32770":
                style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
                if style & win32con.WS_CHILD:
                    return True  # not a dialog, skip
            is_dialog = True

        # 条件2：标题匹配弹窗关键词（排除已知主应用窗口类）
        if not is_dialog and any(kw in title.lower() for kw in _DIALOG_TITLE_KEYWORDS):
            # Notepad, 资源管理器, 浏览器等主窗口即使标题含关键词也不是对话框
            if cls in ("Notepad", "CabinetWClass", "Chrome_WidgetWin_0",
                        "Chrome_WidgetWin_1", "MozillaWindowClass",
                        "ApplicationFrameWindow", "Windows.UI.Core.CoreWindow",
                        "HwndWrapper", "SWT_Window0"):
                pass  # 不是对话框，跳过
            else:
                is_dialog = True

        # 条件3：弹出式窗口特征（有标题的小窗口）
        if not is_dialog and len(title) >= 2 and len(title) < 80:
            if _is_popup_window(hwnd):
                is_dialog = True

        # 条件4：有 owner 的无标题弹窗（如某些错误提示）
        if not is_dialog and not title:
            if win32gui.GetWindow(hwnd, win32con.GW_OWNER):
                # 只有 #32770 类的无 owner 弹窗才算
                if cls == "#32770":
                    is_dialog = True

        if is_dialog:
            try:
                info = DialogInfo(hwnd)
                info.classify()
                dialogs.append(info)
            except Exception:
                pass

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


# ── 点击执行层 ──────────────────────────────────────────────────


def _click_button(btn_info: dict) -> bool:
    """点击按钮：UIA Click → win32 BM_CLICK 降级。

    Returns True if button was clicked (success assumed).
    """
    # Level 1: UIA Click（能处理 DirectUI 按钮）
    if btn_info.get("source") == "uia":
        if _uia_click_button(btn_info):
            return True
        # UIA 失败，如果有 hwnd 则降级 BM_CLICK
        if "hwnd" in btn_info:
            return _bm_click(btn_info)
        return False

    # Level 2: win32 BM_CLICK
    if "hwnd" in btn_info:
        return _bm_click(btn_info)
    return False


def _bm_click(btn_info: dict) -> bool:
    """Send BM_CLICK to a button."""
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


def _send_key(hwnd: int, vk: int):
    """Send key press to window."""
    import ctypes
    _PM = ctypes.windll.user32.PostMessageW
    _PM(hwnd, 0x0100, vk, 0)  # WM_KEYDOWN
    _PM(hwnd, 0x0101, vk, 0)  # WM_KEYUP


def _get_hwnd(dlg) -> int:
    """安全获取对话框的窗口句柄，兼容 Mock 对象。"""
    try:
        return int(getattr(dlg, 'hwnd', 0))
    except (TypeError, ValueError):
        return 0


def _is_open(hwnd) -> bool:
    try:
        h = _get_hwnd(hwnd) if not isinstance(hwnd, int) else hwnd
        if not h:
            return False  # No valid handle → assume closed
        return win32gui.IsWindowVisible(h)
    except Exception:
        return True  # Assume still open on error


def _close_hwnd(hwnd):
    try:
        h = _get_hwnd(hwnd) if not isinstance(hwnd, int) else hwnd
        if h:
            win32gui.SendMessage(h, win32con.WM_CLOSE, 0, 0)
    except Exception:
        pass


# ── 键盘降级策略 ──────────────────────────────────────────────


def _keyboard_fallback(hwnd) -> bool:
    """键盘模拟降级：Escape → Enter → Alt+F4。"""
    try:
        hwnd = int(hwnd)
    except (TypeError, ValueError):
        return False
    if not hwnd:
        return False
    # Escape first（大多数对话框取消/关闭）
    _send_key(hwnd, 0x1B)  # VK_ESCAPE
    time.sleep(0.3)
    if not _is_open(hwnd):
        return True

    # Enter（确认默认按钮）
    _send_key(hwnd, 0x0D)  # VK_RETURN
    time.sleep(0.3)
    if not _is_open(hwnd):
        return True

    # Alt+F4
    _send_alt_key(hwnd, 0x73)  # VK_F4
    time.sleep(0.3)
    if not _is_open(hwnd):
        return True

    return False


# ── 各类型对话框处理 ──


def handle_notepad_save_prompt(dlg) -> bool:
    """Handle Notepad's 'Do you want to save changes?' → 点击"不保存"。"""
    hwnd = _get_hwnd(dlg)
    # Level 1: 找"不保存"按钮（UIA 优先）
    btn = dlg.dont_save_button()
    if btn and _click_button(btn):
        time.sleep(0.3)
        if not _is_open(hwnd):
            return True

    # Level 2: Alt+N（不保存(N) accelerator）
    _send_alt_key(hwnd, 0x4E)  # VK_N
    time.sleep(0.3)
    if not _is_open(hwnd):
        return True

    # Level 3: Alt+D（Don't Save）
    _send_alt_key(hwnd, 0x44)  # VK_D
    time.sleep(0.3)
    if not _is_open(hwnd):
        return True

    # Level 4: WM_CLOSE
    _close_hwnd(hwnd)
    time.sleep(0.3)
    return True


def handle_overwrite_confirm(dlg) -> bool:
    """Handle overwrite confirmation — click 是/替换/覆盖/Yes。"""
    hwnd = _get_hwnd(dlg)
    all_btns = _sort_buttons_by_priority(_get_buttons_safe(dlg))

    for btn in all_btns:
        bn = btn.get("name", "")
        if any(kw in bn for kw in ("是", "替换", "覆盖", "yes", "replace", "overwrite")):
            if _click_button(btn):
                time.sleep(0.3)
                if not _is_open(hwnd):
                    return True

    # Fallback: primary button
    btn = dlg.primary_button() if hasattr(dlg, "primary_button") else None
    if btn and _click_button(btn):
        time.sleep(0.3)
        if not _is_open(hwnd):
            return True

    # Fallback: Enter
    return _keyboard_fallback(hwnd)


def handle_confirmation(dlg) -> bool:
    """Handle confirmation dialog — click OK/Yes/确定。"""
    hwnd = _get_hwnd(dlg)
    btn = dlg.primary_button() if hasattr(dlg, "primary_button") else None
    if btn and _click_button(btn):
        time.sleep(0.3)
        if not _is_open(hwnd):
            return True
    # Fallback
    _close_hwnd(hwnd)
    time.sleep(0.3)
    if not _is_open(hwnd):
        return True
    return _keyboard_fallback(hwnd)


def handle_error_dialog(dlg) -> bool:
    """Handle error dialog — click OK/关闭/确定 first, fallback close."""
    hwnd = _get_hwnd(dlg)
    for btn in _get_buttons_safe(dlg):
        bn = btn.get("name", "")
        if any(kw in bn for kw in ("确定", "ok", "关闭", "close", "取消", "cancel",
                                    "yes", "是")):
            if _click_button(btn):
                time.sleep(0.3)
                if not _is_open(hwnd):
                    return True
    _close_hwnd(hwnd)
    time.sleep(0.3)
    if not _is_open(hwnd):
        return True
    return _keyboard_fallback(hwnd)


def handle_electron_dialog(dlg) -> bool:
    """Handle Electron blob dialog."""
    hwnd = _get_hwnd(dlg)
    for btn in _get_buttons_safe(dlg):
        if _click_button(btn):
            time.sleep(0.3)
            if not _is_open(hwnd):
                return True
    _close_hwnd(hwnd)
    time.sleep(0.3)
    if _is_open(hwnd):
        _send_key(hwnd, 0x1B)
        _send_key(hwnd, 0x1B)
        time.sleep(0.3)
    return True


def fill_save_dialog(filepath: str, timeout: float = 8) -> bool:
    """通过 pywinauto win32 后端填写保存对话框并保存。

    跨进程可靠方案（不受 UIPI 限制）：
      Level 1: pywinauto Desktop(win32) → find Edit → set_edit_text → Save.click
      Level 2: pywinauto Desktop(uia) → find Edit → SetValue → Button.Invoke
      Level 3: 键盘 Alt+N 聚焦文件名 → 输路径 → Enter

    Args:
        filepath: 完整保存路径
        timeout: 等待对话框出现的秒数

    Returns:
        bool — 是否保存成功
    """
    deadline = time.time() + timeout
    dlg = None

    while time.time() < deadline:
        try:
            from pywinauto import Desktop
            # Level 1: win32 backend
            dlg = Desktop(backend="win32").window(title="另存为")
            if dlg.exists() or 'Save' in dlg.window_text():
                break
        except Exception:
            pass
        try:
            from pywinauto import Desktop
            dlg = Desktop(backend="win32").window(title="Save As")
            if dlg.exists():
                break
        except Exception:
            pass
        time.sleep(0.3)

    if not dlg or not (hasattr(dlg, 'exists') and dlg.exists(timeout=0)):
        return False

    try:
        # Level 1: win32 backend
        dlg.set_focus()
        time.sleep(0.3)

        # Find the filename edit control
        try:
            edit_ctrl = dlg.Edit
            edit_ctrl.set_edit_text(filepath)
            time.sleep(0.3)
        except Exception:
            # ComboBox variant (some dialogs use ComboBox for filename)
            try:
                combo = dlg.ComboBox
                combo.set_edit_text(filepath)
                time.sleep(0.3)
            except Exception:
                return False

        # Click Save button
        try:
            save_btn = dlg.Save
            save_btn.click()
        except Exception:
            try:
                save_btn = dlg.Button
                save_btn.click()
            except Exception:
                # Send Enter as last resort
                dlg.type_keys("{ENTER}")
        time.sleep(2)
        return True

    except Exception:
        pass

    # Level 2: UIA backend fallback
    try:
        from pywinauto import Desktop
        dlg_uia = Desktop(backend="uia").window(title="另存为")
        if not dlg_uia.exists(timeout=1):
            dlg_uia = Desktop(backend="uia").window(title="Save As")
        if dlg_uia.exists(timeout=1):
            dlg_uia.set_focus()
            time.sleep(0.3)
            edits = dlg_uia.descendants(control_type="Edit")
            if edits:
                edits[0].set_edit_text(filepath)
                time.sleep(0.3)
                btns = dlg_uia.descendants(control_type="Button")
                for b in btns:
                    txt = b.window_text()
                    if '保存' in txt or 'Save' in txt or 'save' in txt.lower():
                        b.click()
                        time.sleep(2)
                        return True
                # Fallback: first button (usually Save)
                if btns:
                    btns[0].click()
                    time.sleep(2)
                    return True
    except Exception:
        pass

    return False


def _find_child_window(parent_hwnd, class_name=None, title=None, control_type=None):
    """递归查找子窗口。"""
    results = []

    def _enum(hwnd, _unused):
        try:
            cls = win32gui.GetClassName(hwnd)
            txt = win32gui.GetWindowText(hwnd)
            if (class_name is None or cls == class_name) and \
               (title is None or title in txt):
                results.append(hwnd)
        except Exception:
            pass
        return True

    win32gui.EnumChildWindows(parent_hwnd, _enum, 0)
    return results


def handle_save_dialog(dlg: DialogInfo, save_path: str = "") -> bool:
    """填写保存路径→点击保存→降级关闭。

    Windows 保存对话框的"文件名"输入框接受完整路径（如
    ``C:\\Users\\name\\Desktop\\out.txt``），粘贴完整路径后点保存即可。

    Args:
        dlg: DialogInfo object
        save_path: 完整保存路径。为空时只关闭不保存。

    Returns:
        bool — 是否处理成功
    """
    hwnd = _get_hwnd(dlg)
    logger.info("save_dialog: 检测到保存对话框 title='%s'", getattr(dlg, "title", ""))

    if not save_path:
        _close_hwnd(hwnd)
        time.sleep(0.3)
        if not _is_open(hwnd):
            return True
        _keyboard_fallback(hwnd)
        return not _is_open(hwnd)

    try:
        # 1. 枚举对话框所有子控件
        all_kids = []
        def _enum_all(h, _):
            try:
                all_kids.append((h, win32gui.GetClassName(h), win32gui.GetWindowText(h)))
            except Exception:
                pass
            return True
        win32gui.EnumChildWindows(hwnd, _enum_all, 0)
        logger.info("save_dialog: 子控件(%d个): %s", len(all_kids),
                     [(cls, txt[:20]) for _, cls, txt in all_kids])

        # 2. 剪贴板 + WM_PASTE 填写完整保存路径
        import win32clipboard
        combos = [h for h, cls, _ in all_kids if cls == "ComboBox"]
        logger.info("save_dialog: ComboBox=%s", combos)
        if combos:
            edit_kids = _find_child_window(combos[0], class_name="Edit")
            logger.info("save_dialog: ComboBox.Edit=%s", edit_kids)
            if edit_kids:
                win32clipboard.OpenClipboard(0)
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(save_path)
                win32clipboard.CloseClipboard()
                win32gui.SendMessage(edit_kids[0], win32con.WM_PASTE, 0, 0)
                logger.info("save_dialog: WM_PASTE sent")
                time.sleep(0.5)
            # 降级：WM_SETTEXT
            fname = os.path.basename(save_path) if "\\" in save_path or "/" in save_path else save_path
            result_st = win32gui.SendMessage(combos[0], win32con.WM_SETTEXT, 0, fname)
            logger.info("save_dialog: WM_SETTEXT(%s) result=%s", fname, result_st)
            time.sleep(0.3)

        # 3. 找保存按钮
        btns = [(h, txt) for h, cls, txt in all_kids if cls == "Button" and txt.strip()]
        logger.info("save_dialog: Button列表=%s", [(txt, h) for h, txt in btns])
        for btn_hwnd, txt in btns:
            if "保存" in txt and "取消" not in txt:
                logger.info("save_dialog: 点击保存按钮 hwnd=%s txt=%s", btn_hwnd, txt)
                win32gui.SendMessage(btn_hwnd, win32con.BM_CLICK, 0, 0)
                time.sleep(2)
                if _is_open(hwnd):
                    # 可能是覆写确认
                    for btn2_hwnd, txt2 in [(h, win32gui.GetWindowText(h))
                                             for h, cls, _ in all_kids if cls == "Button"]:
                        if txt2 in ("是(&Y)", "是", "替换"):
                            win32gui.SendMessage(btn2_hwnd, win32con.BM_CLICK, 0, 0)
                            time.sleep(1)
                            break
                    else:
                        win32gui.PostMessage(hwnd, win32con.WM_COMMAND, 6, 0)
                        time.sleep(1)
                if not _is_open(hwnd):
                    logger.info("save_dialog: 保存成功 (%s)", save_path)
                    return True
                break

        # 4. IDOK 降级
        if _is_open(hwnd):
            logger.info("save_dialog: IDOK 降级")
            win32gui.PostMessage(hwnd, win32con.WM_COMMAND, 1, 0)
            time.sleep(1)
            if not _is_open(hwnd):
                return True
    except Exception as e:
        logger.warning("save_dialog填写失败: %s", e)

    # 最终降级：关闭
    logger.info("save_dialog: 降级关闭")
    _close_hwnd(hwnd)
    time.sleep(0.3)
    if not _is_open(hwnd):
        return True
    _keyboard_fallback(hwnd)
    return not _is_open(hwnd)


def handle_unhandlable(dlg) -> str:
    """自绘界面无法自动处理，返回提示信息。"""
    hwnd = _get_hwnd(dlg)
    logger.warning("自绘对话框无法自动处理: hwnd=%s title='%s' class='%s'",
                   hwnd, getattr(dlg, "title", ""), getattr(dlg, "class_name", ""))
    return "unhandlable"


# ── 通用入口 ──


def handle_any_dialog(save_filename: str = "") -> str:
    """查找并处理任何可见的对话框。

    Args:
        save_filename: 保存文件的完整路径（如 ``C:\\Users\\name\\Desktop\\out.txt``）。
                       空字符串则只关闭不保存。

    Returns:
        "handled" — 成功处理
        "not_found" — 没有找到对话框
        "unhandlable" — 找到但为自绘界面，无法自动处理
        str(dlg_type) — 特定类型但处理失败
    """
    global _DIALOG_SKIP_CACHE

    # 定期清理过期缓存
    now = time.time()
    if _DIALOG_SKIP_CACHE:
        expired = {h for h, ts in _DIALOG_SKIP_CACHE.items() if now - ts > _DIALOG_SKIP_COOLDOWN}
        if expired:
            for h in expired:
                _DIALOG_SKIP_CACHE.pop(h, None)

    dialogs = find_all_dialogs()
    if not dialogs:
        return "not_found"

    for dlg in dialogs:
        try:
            dlg_type = dlg.dialog_type
            dlg_title = getattr(dlg, "title", "")
            dlg_class = getattr(dlg, "class_name", "")
            dlg_btns = [b.get("text", "") for b in getattr(dlg, "buttons", [])]

            # 冷却缓存内的 UNKNOWN / UNHANDLABLE 窗口直接跳过
            hwnd = _get_hwnd(dlg)
            if dlg_type in (UNKNOWN, UNHANDLABLE) and hwnd and hwnd in _DIALOG_SKIP_CACHE:
                continue

            logger.info("Found: type=%s title=%s class=%s btns=%s",
                         dlg_type, dlg_title, dlg_class, dlg_btns)
        except Exception:
            pass

        try:
            if dlg.dialog_type == UNHANDLABLE:
                hwnd = _get_hwnd(dlg)
                if hwnd:
                    _DIALOG_SKIP_CACHE[hwnd] = time.time()
                handle_unhandlable(dlg)
                continue  # 不自绘对话框继续处理其他对话框

            elif dlg.dialog_type == NOTEPAD_SAVE_PROMPT:
                if handle_notepad_save_prompt(dlg):
                    return "handled"

            elif dlg.dialog_type == SAVE_DIALOG:
                if handle_save_dialog(dlg, save_filename):
                    return "handled_save_dialog"
                return "save_dialog_unhandled"

            elif dlg.dialog_type == OVERWRITE_CONFIRM:
                if handle_overwrite_confirm(dlg):
                    return "handled"

            elif dlg.dialog_type == ERROR_DIALOG:
                if handle_error_dialog(dlg):
                    return "handled"

            elif dlg.dialog_type == ELECTRON_DIALOG:
                if handle_electron_dialog(dlg):
                    return "handled"

            elif dlg.dialog_type in (CONFIRMATION, MESSAGE):
                if handle_confirmation(dlg):
                    return "handled"

            else:
                # Unknown dialog — try all strategies
                if _handle_unknown(dlg):
                    return "handled"
                # 无法处理的 UNKNOWN 窗口加入冷却缓存避免刷日志
                hwnd = _get_hwnd(dlg)
                if hwnd:
                    _DIALOG_SKIP_CACHE[hwnd] = time.time()

        except Exception as e:
            logger.warning("Error handling %s: %s", dlg.dialog_type, e)

    return UNKNOWN


def _handle_unknown(dlg) -> bool:
    """处理未知类型对话框：智能按钮匹配 → 键盘降级 → WM_CLOSE。"""
    hwnd = _get_hwnd(dlg)
    all_btns = _get_buttons_safe(dlg)

    if all_btns:
        for btn in all_btns:
            if _click_button(btn):
                time.sleep(0.3)
                if not _is_open(hwnd):
                    return True
                break

    if _keyboard_fallback(hwnd):
        return True

    _close_hwnd(hwnd)
    time.sleep(0.3)
    return not _is_open(hwnd)


# ── 诊断 ──


def dump_all_dialogs() -> list[dict]:
    results = []
    for dlg in find_all_dialogs():
        all_btns = _get_buttons_safe(dlg)
        results.append({
            "hwnd": dlg.hwnd,
            "title": dlg.title,
            "class_name": dlg.class_name,
            "type": dlg.dialog_type,
            "buttons": [b["text"] for b in all_btns],
            "buttons_win32": [b["text"] for b in getattr(dlg, "buttons_win32", [])],
            "buttons_uia": [b["text"] for b in getattr(dlg, "buttons_uia", [])],
        })
    return results


# ── 后台对话框监控 ──────────────────────────────────────────────

_monitor_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_seen_hwnds: set[int] = set()
_monitor_save_dir: Optional[str] = None

# 非对话框窗口冷却缓存：hwnd → timestamp，防止重复扫描同一窗口刷日志
# 被标记为 UNKNOWN 或 UNHANDLABLE 的窗口在 COOLDOWN 秒内不再处理/记录
_DIALOG_SKIP_CACHE: dict[int, float] = {}
_DIALOG_SKIP_COOLDOWN = 30.0  # 跳过同一非对话框窗口的秒数


def start_dialog_monitor(save_dir: Optional[str] = None) -> None:
    """启动后台线程持续监控对话框，发现后自动处理。

    Args:
        save_dir: 保存对话框的默认保存目录。None 表示只关闭不保存。
    """
    global _monitor_thread, _monitor_save_dir
    if _monitor_thread and _monitor_thread.is_alive():
        return

    _stop_event.clear()
    _seen_hwnds.clear()
    _monitor_save_dir = save_dir

    # 预热 UIA/comtypes（避免后台线程首次初始化卡顿）
    try:
        find_all_dialogs()
    except Exception:
        pass

    _monitor_thread = threading.Thread(
        target=_monitor_loop, daemon=True, name="dialog-monitor"
    )
    _monitor_thread.start()
    logger.info("后台对话框监控已启动 (save_dir=%s)", save_dir)


def stop_dialog_monitor() -> None:
    """停止后台对话框监控。"""
    global _monitor_thread
    if _monitor_thread and _monitor_thread.is_alive():
        _stop_event.set()
        _monitor_thread.join(timeout=3)
        _monitor_thread = None
        logger.info("后台对话框监控已停止")


def _monitor_loop() -> None:
    """后台监控主循环：每 0.5s 轮询，发现新对话框立即分类处理。"""
    try:
        pythoncom.CoInitializeEx(0)  # 线程初始化 COM（uiautomation 依赖）
    except Exception:
        pass  # 可能已初始化

    while not _stop_event.is_set():
        try:
            # 清理已关闭窗口的 HWND，避免复用导致遗漏
            closed = {h for h in _seen_hwnds if not _is_open(h)}
            if closed:
                _seen_hwnds.difference_update(closed)

            dialogs = find_all_dialogs()
            for dlg in dialogs:
                hwnd = _get_hwnd(dlg)
                if hwnd in _seen_hwnds:
                    continue
                _seen_hwnds.add(hwnd)

                dlg_type = dlg.dialog_type
                dlg_title = getattr(dlg, "title", "")

                if dlg_type == SAVE_DIALOG:
                    if _monitor_save_dir:
                        ts = int(time.time())
                        fname = f"lingdong_{ts}.txt"
                        save_path = os.path.join(_monitor_save_dir, fname)
                        ok = handle_save_dialog(dlg, save_path)
                        logger.info("monitor: 保存对话框已处理 hwnd=%s path=%s ok=%s",
                                     hwnd, save_path, ok)
                    else:
                        handle_save_dialog(dlg, "")
                        logger.info("monitor: 保存对话框已关闭 hwnd=%s", hwnd)

                elif dlg_type == NOTEPAD_SAVE_PROMPT:
                    handle_notepad_save_prompt(dlg)
                    logger.info("monitor: Notepad保存提示已处理 hwnd=%s", hwnd)

                elif dlg_type == OVERWRITE_CONFIRM:
                    handle_overwrite_confirm(dlg)
                    logger.info("monitor: 覆写确认已处理 hwnd=%s", hwnd)

                elif dlg_type == ERROR_DIALOG:
                    handle_error_dialog(dlg)
                    logger.info("monitor: 错误对话框已处理 hwnd=%s title=%s",
                                 hwnd, dlg_title)

                elif dlg_type in (CONFIRMATION, MESSAGE):
                    handle_confirmation(dlg)
                    logger.info("monitor: 确认/提示对话框已处理 hwnd=%s", hwnd)

                elif dlg_type == UNKNOWN:
                    # UNKNOWN 可能是主应用窗口（如 Notepad 主窗口因标题含"记事本"被误判）
                    # 不处理，避免点击主窗口按钮引发意外操作
                    logger.info("monitor: 跳过未知窗口 hwnd=%s cls=%s title=%s",
                                 hwnd, dlg.class_name, dlg_title[:30])

                # UNHANDLABLE → 跳过

        except Exception:
            logger.exception("monitor: 循环异常")

        _stop_event.wait(0.5)

    try:
        pythoncom.CoUninitialize()
    except Exception:
        pass


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.DEBUG)
    dialogs = dump_all_dialogs()
    for info in dialogs:
        pass  # debug use: print(json.dumps(info, ensure_ascii=False, indent=2))
