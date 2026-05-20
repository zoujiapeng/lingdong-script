"""
文件对话框功能
处理 Windows 文件打开/保存对话框 — 优先使用 pywinauto 控件绑定，降级到键盘模拟
"""
import time

from features.registry import feature, P, TC, FeatureCategory as F


def _find_filename_combo(ctrl, depth=0):
    """Recursively find the filename ComboBox inside the dialog. (module level)"""
    if depth > 6:
        return None
    try:
        tn = ctrl.ControlTypeName or ""
        cn = ctrl.Name or ""
        if tn == "ComboBoxControl" and cn and ("文件名" in cn or "File name" in cn):
            return ctrl
        for child in ctrl.GetChildren():
            result = _find_filename_combo(child, depth + 1)
            if result:
                return result
    except Exception:
        pass
    return None


def _find_filename_edit(ctrl, depth=0):
    """Recursively find the filename Edit field inside the dialog. (module level)"""
    if depth > 8:
        return None
    try:
        if ctrl.ControlType == 4:  # EditControl
            cn = (ctrl.Name or "")
            if "文件名" in cn or "File name" in cn:
                return ctrl
            try:
                aid = ctrl.AutomationId or ""
                if aid == "1001":
                    return ctrl
            except Exception:
                pass
        for child in ctrl.GetChildren():
            r = _find_filename_edit(child, depth + 1)
            if r:
                return r
    except Exception:
        pass
    return None


def _type_path_and_confirm(path: str, timeout: float) -> bool:
    """通用：在文件对话框输入路径并确认。优先 uiautomation 控件绑定。"""
    import uiautomation as uia
    deadline = time.time() + timeout

    # Wait for the dialog to appear
    while time.time() < deadline:
        dlg = _find_file_dialog()
        if dlg:
            break
        time.sleep(0.3)
    else:
        return False

    try:
        dlg.SetFocus()
        time.sleep(0.3)

        # Find the filename field: recursive search (nested deep inside DUIView)
        filename_box = _find_filename_combo(dlg)
        if not filename_box:
            filename_box = _find_filename_edit(dlg)

        if not filename_box:
            return False

        # Select all and set the path
        filename_box.Click()
        time.sleep(0.15)
        filename_box.SendKeys("{Ctrl}a")
        time.sleep(0.1)
        filename_box.SendKeys(path)
        time.sleep(0.3)

        # Find and click Save/Open button (direct children of #32770)
        save_button = None
        for btn in dlg.GetChildren():
            try:
                if btn.ControlType == 5:  # ButtonControl
                    bn = (btn.Name or "")
                    if any(kw in bn for kw in ("保存", "打开", "Save", "Open")):
                        save_button = btn
                        break
            except Exception:
                continue

        if save_button:
            save_button.Click()
            time.sleep(0.5)
            _try_handle_overwrite(timeout)
            return True

        # Fallback: press Enter on the filename field
        filename_box.SendKeys("{Enter}")
        time.sleep(0.5)
        _try_handle_overwrite(timeout)
        return True
    except Exception:
        import traceback
        traceback.print_exc()

    # 降级：win32api 键盘模拟 — 先激活对话框再输入
    try:
        import pyperclip
        from interaction.actions import _win32_hotkey
        _bring_dialog_to_front(dlg)
        time.sleep(0.5)
        old = pyperclip.paste()
        pyperclip.copy(path)
        time.sleep(0.1)
        _win32_hotkey("ctrl", "a")
        time.sleep(0.1)
        _win32_hotkey("ctrl", "v")
        time.sleep(0.1)
        _win32_hotkey("enter")
        time.sleep(1)
        _try_handle_overwrite(timeout)
        try:
            pyperclip.copy(old)
        except Exception:
            pass
        return True
    except ImportError:
        return False


def _find_file_dialog():
    """使用 uiautomation 查找文件对话框（搜索深度 4）。"""
    import uiautomation as uia
    root = uia.GetRootControl()

    def _has_save_button(ctrl):
        """Check that a #32770 window has a Save/Open button (direct child)."""
        try:
            for child in ctrl.GetChildren():
                tn = child.ControlTypeName or ""
                cn = child.Name or ""
                if tn == "ButtonControl" and cn and any(kw in cn for kw in ("保存", "打开", "Save", "Open", "取消", "Cancel")):
                    return True
        except Exception:
            pass
        return False

    def _search(ctrl, depth=0):
        if depth > 4:
            return None
        try:
            if ctrl.ClassName == "#32770":
                cn = ctrl.Name or ""
                if any(kw in cn for kw in ("另存", "打开", "Save", "Open", "选择")):
                    if _has_save_button(ctrl):
                        return ctrl
            for child in ctrl.GetChildren():
                result = _search(child, depth + 1)
                if result:
                    return result
        except Exception:
            pass
        return None

    return _search(root)


def _bring_dialog_to_front(dlg):
    """Bring a uiautomation dialog to the foreground."""
    try:
        import win32gui, win32con
        rect = dlg.BoundingRectangle
        if not rect:
            return
        # Find by click on the title bar area
        x = rect.left + (rect.right - rect.left) // 2
        y = rect.top + 5
        import ctypes
        ctypes.windll.user32.SetCursorPos(x, y)
        time.sleep(0.05)
        ctypes.windll.user32.mouse_event(0x0001, 0, 0, 0, 0)  # move
    except Exception:
        pass


def _try_handle_overwrite(timeout: float = 3):
    """Handle 'confirm overwrite' dialog that may appear after saving."""
    try:
        from agent.dialog_handler import find_all_dialogs, handle_overwrite_confirm
        deadline = time.time() + timeout
        while time.time() < deadline:
            dialogs = find_all_dialogs()
            for d in dialogs:
                if d.dialog_type == "overwrite_confirm":
                    if handle_overwrite_confirm(d):
                        return True
            time.sleep(0.3)
    except Exception:
        pass
    return False


@feature(
    name="filedialog_open",
    display_name="文件打开对话框",
    description="【对话框】在文件打开对话框中输入路径并确认。用于绕过手动选择文件，自动化文件打开操作",
    category=F.ACTION,
    params=[
        P("path", "str", "要打开的完整文件路径", example="C:\\文档\\report.docx"),
        P("timeout", "number", "等待对话框超时秒数", required=False, default=10),
    ],
    returns="bool - 是否成功",
    dsl_keyword="FILEDIALOG_OPEN",
    flow_type="filedialog_open",
    tags=["dialog", "file"],
)
def filedialog_open(path: str, timeout: float = 10) -> dict:
    return {"success": True, "result": _type_path_and_confirm(path, timeout)}


@feature(
    name="filedialog_save",
    display_name="文件保存对话框",
    description="【对话框】在文件保存对话框中输入路径并确认。用于自动化保存操作",
    category=F.ACTION,
    params=[
        P("path", "str", "保存的完整文件路径", example="C:\\输出\\result.pdf"),
        P("overwrite", "boolean", "是否覆盖已存在文件", required=False, default=True),
        P("timeout", "number", "等待对话框超时秒数", required=False, default=10),
    ],
    returns="bool - 是否成功",
    dsl_keyword="FILEDIALOG_SAVE",
    flow_type="filedialog_save",
    tags=["dialog", "file"],
)
def filedialog_save(path: str, overwrite: bool = True, timeout: float = 10) -> dict:
    return {"success": True, "result": _type_path_and_confirm(path, timeout)}


@feature(
    name="filedialog_folder",
    display_name="选择文件夹对话框",
    description="【对话框】在文件夹选择对话框中输入路径并确认。用于自动化选择目录操作",
    category=F.ACTION,
    params=[
        P("path", "str", "要选择的文件夹路径", example="C:\\目标文件夹"),
        P("timeout", "number", "等待对话框超时秒数", required=False, default=10),
    ],
    returns="bool - 是否成功",
    dsl_keyword="FILEDIALOG_FOLDER",
    flow_type="filedialog_folder",
    tags=["dialog", "file"],
)
def filedialog_folder(path: str, timeout: float = 10) -> dict:
    return {"success": True, "result": _type_path_and_confirm(path, timeout)}


@feature(
    name="select_images",
    display_name="选择图片文件",
    description="""【交互·文件】弹出原生 Windows 文件选择对话框让用户选择图片文件（png/jpg/bmp）。

使用场景:
  - 用户说"选一张图"时调用此工具
  - 配合 IMAGE_CLICK 使用：选择图片 → 复制到模板目录 → 按名点击

选择后自动复制到 templates/ 目录并返回模板名。""",
    category=F.ACTION,
    params=[
        P("title", "str", "对话框标题", required=False, default="请选择图片文件"),
        P("multi_select", "boolean", "是否允许多选", required=False, default=False),
    ],
    returns="dict{success, files: [{path, name, template_name}], cancelled}",
)
def select_images(title: str = "请选择图片文件", multi_select: bool = False) -> dict:
    try:
        import tkinter as tk
        from tkinter import filedialog
        import shutil, os

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)

        filetypes = [("图片文件", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")]
        if multi_select:
            files = filedialog.askopenfilenames(title=title, filetypes=filetypes)
        else:
            files = filedialog.askopenfilename(title=title, filetypes=filetypes)

        root.destroy()

        if not files:
            return {"success": True, "files": [], "cancelled": True}

        # 复制到 templates/ 目录
        template_dir = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..", "templates"))
        os.makedirs(template_dir, exist_ok=True)

        result_files = []
        for fp in files if isinstance(files, tuple) else [files]:
            name = os.path.splitext(os.path.basename(fp))[0]
            ext = os.path.splitext(fp)[1]
            dest = os.path.join(template_dir, f"{name}{ext}")
            shutil.copy2(fp, dest)
            result_files.append({
                "path": fp,
                "name": name,
                "template_name": name,
            })

        return {"success": True, "files": result_files, "cancelled": False}
    except ImportError:
        return {"success": False, "error": "tkinter 不可用"}
