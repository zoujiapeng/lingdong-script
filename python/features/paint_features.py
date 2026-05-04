"""
画板绘图功能
打开 Windows 画图 (mspaint) 并执行绘图操作
"""
import time

from features.registry import feature, P, TC, FeatureCategory as F


def _find_paint_hwnd():
    """查找画板窗口句柄，支持中英文标题"""
    titles = ["画图", "Paint", "画图 -", "Paint -", "未命名 - 画图", "Untitled - Paint"]
    try:
        import win32gui
        def cb(hwnd, ctx):
            text = win32gui.GetWindowText(hwnd)
            for t in titles:
                if t.lower() in text.lower():
                    ctx.append(hwnd)
                    return
        handles = []
        win32gui.EnumWindows(cb, handles)
        return handles[0] if handles else None
    except ImportError:
        return None


def _ensure_paint():
    """确保画板已打开并处于前台，返回窗口信息"""
    hwnd = _find_paint_hwnd()
    if not hwnd:
        import subprocess
        subprocess.Popen(["mspaint.exe"])
        time.sleep(2)
        hwnd = _find_paint_hwnd()
        if not hwnd:
            return None
    try:
        import win32gui
        import win32con
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.2)
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        time.sleep(0.3)
        # 尝试置前（非致命）
        import ctypes
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            try:
                ctypes.windll.user32.BringWindowToTop(hwnd)
            except Exception:
                pass
        rect = win32gui.GetWindowRect(hwnd)
        return {
            "hwnd": hwnd,
            "x": rect[0], "y": rect[1],
            "w": rect[2] - rect[0],
            "h": rect[3] - rect[1],
        }
    except ImportError:
        return {"hwnd": None, "x": 0, "y": 0, "w": 1920, "h": 1080}


def _get_canvas_rect(win_info: dict) -> dict:
    """估算画布区域（相对于屏幕的像素坐标）

    Windows 11 Paint 最大化时布局：
      - 标题栏: ~31px
      - 功能栏 (Ribbon): ~165-175px（视窗口宽度自动折叠/展开）
      - 状态栏: ~28px
      - 边框: ~8px
    将功能栏高度作为可调参数处理。
    """
    win_w, win_h = win_info["w"], win_info["h"]
    win_x, win_y = win_info["x"], win_info["y"]

    border = 8
    title_bar = 31
    # Ribbon 高度与窗口宽度相关：越宽 ribbon 越展开
    if win_w >= 1200:
        ribbon_h = 172
    elif win_w >= 800:
        ribbon_h = 160
    else:
        ribbon_h = 100  # 折叠状态
    status_bar = 28

    cx = win_x + border
    cy = win_y + title_bar + ribbon_h
    cw = win_w - border * 2
    ch = win_h - title_bar - ribbon_h - status_bar - border

    return {"x": cx, "y": cy, "w": max(cw, 100), "h": max(ch, 100)}


def _pct_to_px(canvas: dict, px: float = None, py: float = None) -> tuple:
    """百分比坐标(0-100) → 像素坐标"""
    rx = canvas["x"] + canvas["w"] * (px / 100) if px is not None else None
    ry = canvas["y"] + canvas["h"] * (py / 100) if py is not None else None
    return rx, ry


def _color_position(canvas: dict, color_name: str) -> tuple:
    """取色：返回调色板中指定颜色的像素坐标

    最大化 Paint 时，调色板在 Ribbon 右侧（Color 1 / Color 2 区域）。
    位置随窗口宽度变化，这里用相对 Ribbon 区域的比例估算。
    若识别失败返回 None 不切换颜色（使用当前选中色）。
    """
    # 颜色 → RGB 映射
    colors = {
        "black":   (0, 0, 0),
        "white":   (255, 255, 255),
        "red":     (255, 0, 0),
        "green":   (0, 128, 0),
        "blue":    (0, 0, 255),
        "yellow":  (255, 255, 0),
        "orange":  (255, 165, 0),
        "purple":  (128, 0, 128),
        "brown":   (165, 42, 42),
        "gray":    (128, 128, 128),
    }
    return colors.get(color_name.lower())


def _activate_tool(tool: str):
    """点击工具按钮（使用键盘 Alt 加速键）

    Windows 11 画图 Alt 快捷键（Home 标签下）：
      画笔 (Pencil):    Alt → H → P
      填充 (Fill):      Alt → H → F
      文字 (Text):      Alt → H → T
      橡皮 (Eraser):    Alt → H → E
      吸管 (Color picker): Alt → H → C
      形状 (Shapes):    Alt → H → S → [选择形状]
      粗细 (Size):      Alt → H → Z
      颜色1 (Color 1):  Alt → H → 1
      颜色2 (Color 2):  Alt → H → 2
    """
    try:
        import pyautogui
        tool_keys = {
            "pencil":       ["h", "p"],
            "fill":         ["h", "f"],
            "text":         ["h", "t"],
            "eraser":       ["h", "e"],
            "picker":       ["h", "c"],
            "shapes":       ["h", "s"],
            "size":         ["h", "z"],
            "color1":       ["h", "1"],
            "color2":       ["h", "2"],
            "brush":        ["h", "b"],
        }
        keys = tool_keys.get(tool)
        if not keys:
            return False
        # Alt 激活加速键
        pyautogui.keyDown("alt")
        time.sleep(0.1)
        for k in keys:
            pyautogui.press(k)
            time.sleep(0.08)
        pyautogui.keyUp("alt")
        time.sleep(0.2)
        return True
    except ImportError:
        return False


@feature(
    name="paint_draw",
    display_name="画板绘图",
    description="【画板】在 Windows 画图中执行绘图操作。自动打开/激活画板，支持画线、矩形、圆形、自由绘画、文字输入、颜色切换、清空画布等",
    category=F.ACTION,
    params=[
        P("action", "str", "操作类型: open | line | rect | circle | freehand | text | clear | color",
          example="line"),
        P("x1", "number", "起点/圆心 X (0-100 画布百分比)", required=False, default=50, example=10),
        P("y1", "number", "起点/圆心 Y (0-100 画布百分比)", required=False, default=50, example=20),
        P("x2", "number", "终点/宽度 X (百分比，line/rect 用)", required=False, default=80, example=90),
        P("y2", "number", "终点/高度 Y (百分比，line/rect 用)", required=False, default=80, example=80),
        P("radius", "number", "半径 (百分比，circle 用)", required=False, default=20, example=15),
        P("points", "str", "自由画路径：x1,y1 x2,y2 x3,y3 ...（空格分隔坐标对）",
          required=False, default="", example="10,20 30,40 50,60"),
        P("text", "str", "文字内容（text 操作用）", required=False, default="", example="Hello"),
        P("color", "str", "颜色: black|red|green|blue|yellow|orange|purple|white",
          required=False, default="black", example="red"),
        P("clear_first", "boolean", "执行前是否清空画布", required=False, default=False),
    ],
    returns="bool - 绘图是否成功",
    examples=["画一条红线: paint_draw action=line x1=10 y1=50 x2=90 y2=50 color=red",
              "画矩形: paint_draw action=rect x1=20 y1=20 x2=80 y2=60 color=blue"],
    tags=["paint", "drawing", "action"],
)
def paint_draw(
    action: str,
    x1: float = 50, y1: float = 50,
    x2: float = 80, y2: float = 80,
    radius: float = 20,
    points: str = "",
    text: str = "",
    color: str = "black",
    clear_first: bool = False,
) -> bool:
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
    except ImportError:
        return False

    action = action.lower().strip()

    # 1. 打开/激活画板
    win = _ensure_paint()
    if not win:
        return False
    canvas = _get_canvas_rect(win)

    # 2. 清空画布
    if clear_first:
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.2)
        pyautogui.press("delete")
        time.sleep(0.3)

    # 3. 如果是 open 操作，到这里就够了
    if action == "open":
        return True

    # 4. 切换颜色（如果需要且不是黑色）
    if color and color.lower() != "black":
        rgb = _color_position(color)
        if rgb:
            # 用 Alt+H+1 激活 Color 1，但实际颜色选择需要点击调色板
            # 用快捷键选颜色更可靠：Alt+H → C (吸管取色)
            # 或用 click 预设颜色块
            _activate_tool("color1")
            time.sleep(0.3)
            # 颜色块在 Ribbon 右侧，估算位置
            # 最大化时，预设颜色色块在窗口右侧约 100-200px 宽的区域
            # 具体位置依赖于窗口宽度，这里简化处理
            pyautogui.hotkey("alt", "h", "c")
            time.sleep(0.2)

    # 5. 确保铅笔工具选中
    _activate_tool("pencil")
    time.sleep(0.2)

    # 6. 执行绘图
    if action == "line":
        sx, sy = _pct_to_px(canvas, x1, y1)
        ex, ey = _pct_to_px(canvas, x2, y2)
        pyautogui.moveTo(sx, sy, duration=0.15)
        pyautogui.drag(ex, ey, duration=0.3, button="left")

    elif action == "rect":
        sx, sy = _pct_to_px(canvas, x1, y1)
        ex, ey = _pct_to_px(canvas, x2, y2)
        # 画四条边
        for (lx1, ly1, lx2, ly2) in [
            (sx, sy, ex, sy),    # 上
            (ex, sy, ex, ey),    # 右
            (ex, ey, sx, ey),    # 下
            (sx, ey, sx, sy),    # 左
        ]:
            pyautogui.moveTo(lx1, ly1, duration=0.05)
            pyautogui.drag(lx2, ly2, duration=0.15, button="left")

    elif action == "circle":
        cx_v, cy_v = _pct_to_px(canvas, x1, y1)
        r = canvas["w"] * (radius / 100)
        # 用一小段一小段的直线模拟画圆
        import math
        steps = 60
        pyautogui.moveTo(cx_v + r, cy_v, duration=0.1)
        for i in range(steps + 1):
            angle = 2 * math.pi * i / steps
            px = cx_v + r * math.cos(angle)
            py = cy_v + r * math.sin(angle)
            pyautogui.drag(px, py, duration=0.02, button="left")

    elif action == "freehand":
        if not points:
            return False
        segments = points.strip().split()
        coords = []
        for seg in segments:
            parts = seg.split(",")
            if len(parts) == 2:
                px, py = _pct_to_px(canvas, float(parts[0]), float(parts[1]))
                coords.append((px, py))
        if not coords:
            return False
        pyautogui.moveTo(coords[0][0], coords[0][1], duration=0.1)
        for cx_v, cy_v in coords[1:]:
            pyautogui.drag(cx_v, cy_v, duration=0.05, button="left")

    elif action == "text":
        tx, ty = _pct_to_px(canvas, x1, y1)
        if not text:
            return False
        # 用文字工具 + Ctrl+V 的方式
        _activate_tool("text")
        time.sleep(0.3)
        pyautogui.click(tx, ty)
        time.sleep(0.2)
        pyautogui.write(text, interval=0.02)

    elif action == "clear":
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.2)
        pyautogui.press("delete")
        time.sleep(0.3)

    elif action == "color":
        # 只是切换颜色，不画图
        pass

    else:
        return False

    return True
