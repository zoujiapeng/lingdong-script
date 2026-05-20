"""
菜单操作功能注册
menu_click / menu_structure — 三层降级策略：win32 → keyboard → uia
"""
import logging

from features.registry import feature, P, TC, FeatureCategory as F

logger = logging.getLogger("features.menu")


@feature(
    name="menu_click",
    display_name="点击菜单项",
    description="【菜单】按路径点击 Windows 窗口菜单。自动降级：Win32 WM_COMMAND → 键盘加速键 → UIA。支持 owner-drawn 菜单（Notepad++ 等）",
    category=F.ACTION,
    params=[
        P("target", "any", "窗口标题（模糊匹配）或 HWND 数字"),
        P("path", "list", "菜单路径列表，如 [\"View\", \"Function List\"] 或 [\"文件\", \"打开\"]"),
        P("timeout", "number", "超时秒数", required=False, default=5),
    ],
    returns="bool - 是否成功",
    dsl_keyword=None,
    examples=[
        'lobster call menu-click target="记事本" path=["View","Function List"]',
        'lobster call menu-click target="Notepad++" path=["文件","打开"]',
    ],
    tags=["menu", "windows", "automation"],
)
def menu_click(target: str | int, path: list, timeout: float = 5) -> bool:
    try:
        from agent.menu_handler import menu_click as _click
        result = _click(target, path, timeout)
        # 菜单操作后可能弹出对话框，自动清扫一轮
        if result:
            try:
                from agent.dialog_handler import find_all_dialogs
                dialogs = find_all_dialogs()
                if dialogs:
                    logger.info("menu_click: %d dialog(s) detected after menu, sweeping...", len(dialogs))
                    from agent.dialog_handler import handle_any_dialog
                    for _ in range(min(len(dialogs) * 2, 10)):
                        r = handle_any_dialog()
                        if r == "not_found":
                            break
            except Exception:
                pass
        return result
    except ImportError as e:
        return False
    except Exception as e:
        logger.warning("menu_click error: %s", e)
        return False


@feature(
    name="menu_structure",
    display_name="查看菜单结构",
    description="【菜单·诊断】获取窗口的完整菜单结构，用于调试和查找菜单项路径",
    category=F.DEBUG,
    params=[
        P("target", "any", "窗口标题（模糊匹配）或 HWND 数字"),
    ],
    returns="list - 菜单结构（含子菜单、命令 ID、位置）",
    tags=["menu", "diagnostic"],
)
def menu_structure(target: str | int) -> list:
    try:
        from agent.menu_handler import get_menu_structure as _struct
        result = _struct(target)
        return result or []
    except ImportError as e:
        return []
    except Exception as e:
        logger.warning("menu_structure error: %s", e)
        return []
