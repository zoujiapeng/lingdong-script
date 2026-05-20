"""
对话框处理功能注册
handle_dialog / dialog_list / dialog_take_action — UIA + win32 双层实现
"""
import logging

from features.registry import feature, P, TC, FeatureCategory as F

logger = logging.getLogger("features.dialog")


@feature(
    name="handle_dialog",
    display_name="处理对话框",
    description="【弹窗】自动检测并关闭系统对话框。支持：记事本保存提示、错误弹窗、确认对话框、覆盖确认、保存对话框等。UIA + win32 双层检测",
    category=F.ACTION,
    params=[
        P("timeout", "number", "等待对话框出现的秒数", required=False, default=0),
    ],
    returns="str - handled / not_found / unhandlable / 错误信息",
    dsl_keyword=None,
    examples=[
        "lobster call handle-dialog",
    ],
    tags=["dialog", "windows", "popup"],
)
def handle_dialog(timeout: float = 0) -> str:
    import time, importlib
    try:
        mod = importlib.import_module("agent.dialog_handler")
        if timeout > 0:
            deadline = time.time() + timeout
            while time.time() < deadline:
                result = mod.handle_any_dialog()
                if result != "not_found":
                    return result
                time.sleep(0.3)
            return "not_found"
        return mod.handle_any_dialog()
    except ImportError as e:
        return f"import_error: {e}"
    except Exception as e:
        return f"error: {e}"


@feature(
    name="dialog_list",
    display_name="列出对话框",
    description="【弹窗·诊断】列出当前桌面上所有可见对话框。用于诊断弹窗情况和调试",
    category=F.DEBUG,
    params=[],
    returns="list[dict] - 对话框列表（hwnd, title, class_name, type, buttons, buttons_win32, buttons_uia）",
    tags=["dialog", "diagnostic"],
)
def dialog_list() -> list:
    try:
        from agent.dialog_handler import dump_all_dialogs
        return dump_all_dialogs()
    except ImportError as e:
        return [{"error": str(e)}]
    except Exception as e:
        return [{"error": str(e)}]


@feature(
    name="dialog_take_action",
    display_name="对话框操作",
    description="【弹窗·操作】对指定的对话框执行操作：点击按钮、关闭等。需要先用 dialog_list 获取对话框信息。支持 UIA Click → BM_CLICK 降级",
    category=F.ACTION,
    params=[
        P("action", "str", "操作：click:<按钮文本> / close / save / dont_save / cancel / overwrite_yes", example="click:保存"),
        P("timeout", "number", "等待对话框出现的秒数", required=False, default=3),
    ],
    returns="bool - 是否成功",
    tags=["dialog", "windows"],
)
def dialog_take_action(action: str, timeout: float = 3) -> bool:
    import time
    try:
        from agent.dialog_handler import (
            find_all_dialogs, handle_any_dialog, DialogInfo,
            _get_hwnd, _click_button, _close_hwnd,
            handle_notepad_save_prompt, handle_overwrite_confirm,
        )

        deadline = time.time() + timeout
        while time.time() < deadline:
            dialogs = find_all_dialogs()
            if dialogs:
                break
            time.sleep(0.3)
        else:
            logger.info("dialog_take_action: no dialogs found within timeout")
            return False

        dlg = dialogs[0]

        if action == "close":
            _close_hwnd(_get_hwnd(dlg))
            return True

        if action.startswith("click:"):
            btn_text = action[6:].strip()
            btn = dlg.find_button(btn_text)
            if btn:
                return _click_button(btn)
            return False

        if action == "save":
            btn = dlg.save_button()
            if btn:
                return _click_button(btn)
            return False

        if action == "dont_save":
            return handle_notepad_save_prompt(dlg)

        if action == "cancel":
            btn = dlg.cancel_button()
            if btn:
                return _click_button(btn)
            return False

        if action == "overwrite_yes":
            return handle_overwrite_confirm(dlg)

        if action.startswith("save_as:"):
            filepath = action[8:].strip()
            if filepath:
                from agent.dialog_handler import fill_save_dialog as _fill
                return _fill(filepath, timeout)
            return False

        logger.warning("dialog_take_action: unknown action=%s", action)
        return False

    except ImportError as e:
        logger.warning("dialog_take_action: import error=%s", e)
        return False
    except Exception as e:
        logger.warning("dialog_take_action: error=%s", e)
        return False


@feature(
    name="fill_save_dialog",
    display_name="填写保存对话框",
    description="【弹窗·保存】通过 pywinauto 填写另存为对话框的文件名路径并保存。跨进程可靠，支持旧式 #32770 和现代 IFileDialog",
    category=F.ACTION,
    params=[
        P("filepath", "str", "完整保存路径，如 C:\\Users\\xxx\\Desktop\\test.txt"),
        P("timeout", "number", "等待对话框出现的秒数", required=False, default=8),
    ],
    returns="bool - 是否保存成功",
    tags=["dialog", "save", "windows"],
)
def fill_save_dialog(filepath: str, timeout: float = 8) -> bool:
    try:
        from agent.dialog_handler import fill_save_dialog as _fill
        return _fill(filepath, timeout)
    except ImportError as e:
        logger.warning("fill_save_dialog import error: %s", e)
        return False
    except Exception as e:
        logger.warning("fill_save_dialog error: %s", e)
        return False
