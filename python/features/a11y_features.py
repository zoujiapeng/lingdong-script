"""
无障碍树功能
Windows Accessibility Tree 获取和操作 — 基于 uiautomation 库
"""
from features.registry import feature, P, TC, FeatureCategory as F


def _get_fg_control():
    """获取前台窗口对应的 uiautomation Control"""
    import win32gui
    import uiautomation as auto
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None
    root = auto.GetRootControl()
    for child in root.GetChildren():
        try:
            if child.NativeWindowHandle == hwnd:
                return child
        except Exception:
            pass
    return root


def _get_name(ctrl) -> str:
    try:
        return ctrl.Name or ""
    except Exception:
        return ""


def _get_role(ctrl) -> str:
    try:
        return ctrl.ControlTypeName or ""
    except Exception:
        return ""


def _get_bounds(ctrl):
    try:
        r = ctrl.BoundingRectangle
        if r and hasattr(r, 'left'):
            return {"left": r.left, "top": r.top,
                    "width": r.right - r.left, "height": r.bottom - r.top}
    except Exception:
        pass
    return None


def _is_visible(ctrl) -> bool:
    try:
        return not ctrl.IsOffscreen
    except Exception:
        return True


def _walk_tree(ctrl, depth: int, max_depth: int, filter_visible: bool):
    """递归遍历无障碍树"""
    if depth > max_depth:
        return None
    try:
        if filter_visible and not _is_visible(ctrl):
            return None

        node = {
            "name": _get_name(ctrl),
            "role": _get_role(ctrl),
            "children": [],
        }
        try:
            node["enabled"] = ctrl.IsEnabled
        except Exception:
            pass
        bounds = _get_bounds(ctrl)
        if bounds:
            node["bounds"] = bounds

        if depth < max_depth:
            for child in ctrl.GetChildren():
                child_node = _walk_tree(child, depth + 1, max_depth, filter_visible)
                if child_node:
                    node["children"].append(child_node)

        return node
    except Exception:
        return None


@feature(
    name="a11y_get_tree",
    display_name="获取无障碍树",
    description="【无障碍】获取当前活动窗口的无障碍访问树结构。用于了解界面元素层次结构，比 OCR 更精确",
    category=F.PERCEPTION,
    params=[
        P("max_depth", "int", "最大深度", required=False, default=5),
        P("filter_visible", "boolean", "是否只返回可见元素", required=False, default=True),
    ],
    returns="dict{role, name, children} - 无障碍树",
    tags=["a11y", "uia"],
)
def a11y_get_tree(max_depth: int = 5, filter_visible: bool = True) -> dict:
    try:
        import uiautomation as auto
        fg = _get_fg_control()
        if not fg:
            return {"success": False, "error": "无前台窗口"}
        tree = _walk_tree(fg, 1, max_depth, filter_visible) or {}
        return {"success": True, "result": tree}
    except ImportError:
        return {"success": False, "error": "需要 uiautomation: pip install uiautomation"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _find_controls(ctrl, name: str, role: str, control_type: str, results: list, max_results: int = 20):
    """递归查找匹配的 uiautomation Control 对象"""
    if len(results) >= max_results:
        return
    try:
        el_name = _get_name(ctrl)
        el_role = _get_role(ctrl)
        el_type = ctrl.ControlTypeName or ""
        name_match = not name or name.lower() in el_name.lower()
        role_match = not role or role.lower() in el_role.lower()
        type_match = not control_type or control_type.lower() in el_type.lower()
        if name_match and role_match and type_match:
            results.append(ctrl)
        for child in ctrl.GetChildren():
            _find_controls(child, name, role, control_type, results, max_results)
    except Exception:
        pass


def _control_to_dict(ctrl) -> dict:
    """将 uiautomation Control 转为可序列化的 dict"""
    entry = {"name": _get_name(ctrl), "role": _get_role(ctrl)}
    bounds = _get_bounds(ctrl)
    if bounds:
        entry["bounds"] = bounds
    try:
        entry["enabled"] = ctrl.IsEnabled
    except Exception:
        pass
    return entry


@feature(
    name="a11y_find_element",
    display_name="查找无障碍元素",
    description="【无障碍】在无障碍树中查找匹配指定条件的 UI 元素。用于精确定位按钮、输入框等控件",
    category=F.PERCEPTION,
    params=[
        P("name", "str", "元素名称（模糊匹配）", example="发送"),
        P("role", "str", "元素角色（如 button, edit, list）", required=False, default=""),
        P("control_type", "str", "控件类型（如 Edit, Button, ComboBox）", required=False, default=""),
    ],
    returns="list[dict{name, role, bounds, enabled}] - 匹配元素列表",
    tags=["a11y", "uia"],
)
def a11y_find_element(name: str, role: str = "", control_type: str = "") -> dict:
    try:
        import uiautomation as auto
        root = auto.GetRootControl()
        controls = []
        _find_controls(root, name, role, control_type, controls)
        results = [_control_to_dict(c) for c in controls]
        return {"success": True, "result": results}
    except ImportError:
        return {"success": False, "error": "需要 uiautomation: pip install uiautomation"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="a11y_click_element",
    display_name="点击无障碍元素",
    description="【无障碍】通过无障碍接口点击指定 UI 元素，绕过坐标/图像匹配。适用于标准 Windows 控件",
    category=F.ACTION,
    params=[
        P("name", "str", "元素名称", example="确定"),
        P("role", "str", "元素角色（可选）", required=False, default=""),
    ],
    returns="bool - 是否点击成功",
    tags=["a11y", "uia"],
)
def a11y_click_element(name: str, role: str = "") -> dict:
    try:
        import uiautomation as auto
        root = auto.GetRootControl()
        controls = []
        _find_controls(root, name, role, "", controls, max_results=5)
        for ctrl in controls:
            try:
                ctrl.Click()
                return {"success": True, "result": True}
            except Exception:
                continue
        return {"success": False, "error": f"未找到元素 '{name}'"}
    except ImportError:
        return {"success": False, "error": "需要 uiautomation: pip install uiautomation"}
    except Exception as e:
        return {"success": False, "error": str(e)}
