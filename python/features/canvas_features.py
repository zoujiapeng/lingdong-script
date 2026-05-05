"""
画布检测功能
通过视觉分析自动识别窗口中的画布/绘图区域（白色矩形区域）
不依赖窗口标题或特定应用，纯视觉检测，通用性强
"""
from features.registry import feature, P, FeatureCategory as F


def _take_screenshot(region_str: str = "") -> tuple:
    """截取屏幕或指定区域，返回 (ndarray, img_width, img_height)"""
    import mss
    import numpy as np
    with mss.mss() as sct:
        if region_str:
            parts = region_str.strip().split(",")
            if len(parts) == 4:
                x, y, w, h = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
                mon = {"left": x, "top": y, "width": w, "height": h}
            else:
                return None
        else:
            mon = sct.monitors[1]  # 主显示器
        img = np.array(sct.grab(mon))
        return img, img.shape[1], img.shape[0]


def _find_white_rect(img, tolerance: float = 30) -> dict:
    """从图像中找到最大的近白色矩形区域

    算法：
    1. 转灰度，二值化（近白为 255，非白为 0）
    2. 形态学闭运算填补小孔
    3. 找轮廓，选面积最大的矩形
    """
    import cv2
    import numpy as np

    gray = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    _, binary = cv2.threshold(gray, 255 - tolerance, 255, cv2.THRESH_BINARY)

    # 形态学闭运算，连接附近白色区域
    kernel = np.ones((15, 15), np.uint8)
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    # 找轮廓
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    # 按面积排序取前 5 大的
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]
    height, width = img.shape[:2]

    best = None
    best_area = 0
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = w * h
        # 过滤太小的区域（<屏幕 5%）
        if area < width * height * 0.05:
            continue
        # 过滤太靠边的区域（可能是桌面背景）
        if x < 5 and y < 5:
            continue
        # 选择面积最大的
        if area > best_area:
            # 检查内部是否大部分是白色（确认是画布不是边框）
            roi = binary[y:y+h, x:x+w]
            white_ratio = float(np.sum(roi > 0)) / (w * h)
            if white_ratio > 0.6:  # 至少 60% 是白色
                best_area = area
                best = {"x": x, "y": y, "w": w, "h": h}

    return best


@feature(
    name="find_canvas",
    display_name="检测画布",
    description="【感知·画布】截取屏幕区域并自动检测其中的空白画布/绘图区域（白色矩形），返回像素坐标。适用于 Paint、白板应用、绘图软件、文档编辑区等任何包含白色矩形区域的场景",
    category=F.PERCEPTION,
    params=[
        P("region", "str", "搜索区域 'x,y,w,h'（可选，默认全屏）", required=False, default=""),
        P("color_tolerance", "number", "颜色容差 0-255（越高检测越宽松），默认 30", required=False, default=30),
    ],
    returns="dict{success, canvas: {x, y, w, h}, center: {x, y}, area_pct}",
    dsl_keyword="FINDCANVAS",
    dsl_template="FINDCANVAS tolerance={color_tolerance}",
    flow_type="condition",
    tags=["canvas", "perception", "region"],
)
def find_canvas(region: str = "", color_tolerance: float = 30) -> dict:
    """截屏并检测白色画布区域，返回画布坐标"""
    try:
        import cv2
    except ImportError:
        return {"success": False, "error": "需要 opencv-python: pip install opencv-python"}

    result = _take_screenshot(region)
    if result is None:
        return {"success": False, "error": "区域格式错误，应为 x,y,w,h"}
    img, img_w, img_h = result

    canvas = _find_white_rect(img, tolerance=color_tolerance)
    if not canvas:
        return {"success": False, "error": "未检测到白色画布区域，请调整 color_tolerance 或确保画布在屏幕可见范围内"}

    area_pct = round((canvas["w"] * canvas["h"]) / (img_w * img_h) * 100, 1)

    return {
        "success": True,
        "canvas": canvas,
        "center": {"x": canvas["x"] + canvas["w"] // 2, "y": canvas["y"] + canvas["h"] // 2},
        "area_pct": area_pct,
    }
