"""
高级输入功能
右键 / 悬停 / 鼠标移动 / 连击 / 中键
All operations route through ActionHandler (uiautomation/win32api).
"""
import time
import random

from features.registry import feature, P, TC, FeatureCategory as F
from features._utils import resolve_coord, parse_region


def _do(action, **kw):
    from interaction.actions import ActionHandler
    return ActionHandler()(action, **kw)


def _find_target_coords(target: str, region: str = "") -> tuple:
    """将目标文字解析为屏幕坐标 (x, y)"""
    try:
        parts = target.strip().split(",")
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    try:
        from features.perception_features import ocr_find_text
        reg = parse_region(region)
        ocr_result = ocr_find_text(query=target, region=region, fuzzy_threshold=0.7)
        if isinstance(ocr_result, dict) and ocr_result.get("success"):
            boxes = ocr_result.get("result", [])
            if boxes:
                box = boxes[0]
                x = box.get("center_x") or (box["x"] + box["w"] // 2)
                y = box.get("center_y") or (box["y"] + box["h"] // 2)
                return x, y
    except Exception:
        pass
    return 0, 0


@feature(
    name="right_click",
    display_name="右键点击",
    description="【鼠标】在指定位置或目标上执行右键点击。用于打开右键菜单、触发上下文操作",
    category=F.ACTION,
    params=[
        P("target", "str", "目标：文字 / 坐标 'x,y' / 图像模板名", example="桌面"),
        P("region", "str", "搜索区域 'x,y,w,h'（可选）", required=False, default=""),
    ],
    returns="bool - 是否成功",
    tags=["mouse", "click"],
    hidden=True,
)
def right_click(target: str, region: str = "") -> bool:
    x, y = _find_target_coords(target, region)
    if x == 0 and y == 0:
        return False
    return _do("click", x=x, y=y, button="right")


@feature(
    name="hover",
    display_name="悬停",
    description="【鼠标】将鼠标悬停在目标上不点击。用于触发 tooltip、hover 效果或预览链接",
    category=F.ACTION,
    params=[
        P("target", "str", "悬停目标：文字 / 坐标 / 图像模板名", example="菜单项"),
        P("duration", "number", "悬停秒数", required=False, default=0.5),
    ],
    returns="bool - 是否成功",
    tags=["mouse", "hover"],
    hidden=True,
)
def hover(target: str, duration: float = 0.5) -> bool:
    x, y = _find_target_coords(target)
    if x == 0 and y == 0:
        return False
    from interaction.actions import _ensure_win32
    w32 = _ensure_win32()
    w32['api'].SetCursorPos((int(x), int(y)))
    time.sleep(duration)
    return True


@feature(
    name="mouse_move",
    display_name="鼠标移动",
    description="【鼠标】将鼠标移动到语义目标或绝对坐标。支持文字/坐标定位，推荐用 target 参数代替手动输入坐标",
    category=F.ACTION,
    params=[
        P("target", "str", "目标：文字 / 坐标 / 图像模板名（优先）", required=False, default="", example="确认按钮"),
        P("x", "int", "目标 X 坐标（target 为空时使用）", required=False, default=0),
        P("y", "int", "目标 Y 坐标（target 为空时使用）", required=False, default=0),
        P("human_like", "boolean", "是否模拟人类移动轨迹", required=False, default=True),
    ],
    returns="bool - 是否成功",
    tags=["mouse"],
    hidden=True,
)
def mouse_move(x: int = 0, y: int = 0, human_like: bool = True, target: str = "") -> bool:
    if target:
        x, y = _find_target_coords(target)
    from interaction.actions import _ensure_win32
    w32 = _ensure_win32()
    w32['api'].SetCursorPos((int(x), int(y)))
    if human_like:
        time.sleep(random.uniform(0.05, 0.15))
    return True


@feature(
    name="triple_click",
    display_name="三击",
    description="【鼠标】在目标上执行三次点击。用于选中整段文本或触发特定应用功能",
    category=F.ACTION,
    params=[
        P("target", "str", "目标：文字 / 坐标 / 图像模板名", example="段落文本"),
    ],
    returns="bool - 是否成功",
    tags=["mouse", "click"],
    hidden=True,
)
def triple_click(target: str) -> bool:
    x, y = _find_target_coords(target)
    if x == 0 and y == 0:
        return _do("triple_click")
    return _do("triple_click", x=x, y=y)


@feature(
    name="select_text",
    display_name="选中文本",
    description="【鼠标】在目标位置执行双击选中单词或拖动选中范围。用于后续复制或操作",
    category=F.ACTION,
    params=[
        P("target", "str", "要选中的文字或其附近坐标", example="关键词"),
        P("extend_to_line", "boolean", "是否扩展到整行", required=False, default=False),
    ],
    returns="bool - 是否成功",
    tags=["mouse", "text"],
    hidden=True,
)
def select_text(target: str, extend_to_line: bool = False) -> bool:
    x, y = _find_target_coords(target)
    if x == 0 and y == 0:
        return False
    _do("click", x=x, y=y)
    time.sleep(0.05)
    _do("click", x=x, y=y)
    if extend_to_line:
        time.sleep(0.05)
        _do("click", x=x, y=y)
    return True


@feature(
    name="middle_click",
    display_name="中键点击",
    description="【鼠标】在目标上执行鼠标中键点击。用于浏览器新标签页打开、画布平移等场景",
    category=F.ACTION,
    params=[
        P("target", "str", "目标：文字 / 坐标 / 图像模板名", example="链接文字"),
    ],
    returns="bool - 是否成功",
    tags=["mouse", "click"],
    hidden=True,
)
def middle_click(target: str) -> bool:
    x, y = _find_target_coords(target)
    if x == 0 and y == 0:
        return _do("click", button="middle")
    return _do("click", x=x, y=y, button="middle")
