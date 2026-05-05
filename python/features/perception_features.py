"""
感知类功能注册
OCR / 图像匹配 / 颜色检测 / 进度条 / 截图
"""
from features.registry import feature, P, TC, FeatureCategory as F
from features._utils import parse_region


@feature(
    name="region_select",
    display_name="区域框选",
    description="【交互·框选】弹出全屏选框界面，让用户用鼠标拖拽选择屏幕区域，返回坐标{x,y,w,h}。这是AI引导用户视觉选择区域的唯一方式。其他项目做不到——LLM不能看屏幕，更不可能让用户框选",
    category=F.PERCEPTION,
    params=[
        P("message", "str", "提示用户框选区域的文字说明", required=False, default="请拖拽选择屏幕区域"),
    ],
    returns="dict{x, y, w, h} - 选中区域的坐标和尺寸",
    dsl_keyword="REGION_SELECT",
    dsl_template="REGION_SELECT var {message}",
    flow_type="screenshot",
)
def region_select(message: str = "请拖拽选择屏幕区域") -> dict:
    try:
        from interaction.region_picker import RegionPicker
        region = RegionPicker.pick(message)
        if region:
            x, y, w, h = region
            return {"x": x, "y": y, "w": w, "h": h}
        return {"x": 0, "y": 0, "w": 0, "h": 0, "cancelled": True}
    except ImportError:
        return {"x": 0, "y": 0, "w": 0, "h": 0, "error": "region_picker 模块不可用"}


# ── capture_multi: 连续框选截取多张模板 → 逐个点击 ───────────────────

def _do_capture_multi(prefix: str = "", count: int = 0,
                      similarity: float = 0.9, method: str = "single",
                      offset_x: int = 0, offset_y: int = 0,
                      click_count: int = 1, timeout: float = 5) -> dict:
    """
    连续框选 → 自动截图存为模板 → 逐个点击每张模板。
    每张模板按顺序尝试匹配，找到后点 click_count 次，
    找不到则超时后自动跳过到下一张。
    - 左键框选 → click: 截图+点击
    - 右键框选 → monitor: 只记录坐标+用途，不点击
    """
    from interaction.region_picker import RegionPicker
    from interaction.actions import ActionHandler, HumanMouse
    from perception.vision import ScreenCapture, TemplateMatcher
    import os, cv2, time

    template_dir = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", "templates"))
    os.makedirs(template_dir, exist_ok=True)

    # 第一步：连续框选（左键=点击目标，右键=监控区域）
    regions = RegionPicker.multi_pick("请框选目标：左键=点击  右键=监控(滚轮选用途)  空格结束")
    if not regions:
        return {"success": True, "captured": [], "cancelled": True}

    click_regions = [r for r in regions if r["type"] == "click"]
    monitor_regions = [r for r in regions if r["type"] == "monitor"]

    # 第二步：左键区域 → 截图存为模板
    captured = []
    template_names = []
    for i, item in enumerate(click_regions):
        idx = count + i + 1
        name = f"{prefix}{idx}" if prefix else f"capture_{idx}"
        x, y, w, h = item["x"], item["y"], item["w"], item["h"]
        img = ScreenCapture.capture((x, y, w, h))
        if img is None or img.size == 0:
            continue
        path = os.path.join(template_dir, f"{name}.png")
        cv2.imwrite(path, img)
        captured.append({"name": name, "path": path,
                         "region": {"x": x, "y": y, "w": w, "h": h}})
        template_names.append(name)

    # 第三步：逐个点击每张模板
    click_results = []
    if template_names:
        matcher = TemplateMatcher(template_dir=template_dir)
        for name in template_names:
            end_time = time.time() + timeout
            found = False
            while time.time() < end_time:
                match = matcher.find(name, threshold=similarity)
                if match:
                    (cx, cy), confidence = match
                    click_x = cx + offset_x
                    click_y = cy + offset_y
                    for _ in range(click_count):
                        if method == "right":
                            HumanMouse.right_click(click_x, click_y)
                        elif method == "double":
                            HumanMouse.double_click(click_x, click_y)
                        elif method == "middle":
                            HumanMouse.click(click_x, click_y, button="middle")
                        else:
                            HumanMouse.click(click_x, click_y)
                    click_results.append({
                        "target": name, "success": True,
                        "x": click_x, "y": click_y,
                        "confidence": float(confidence), "clicks": click_count,
                    })
                    found = True
                    break
                time.sleep(0.3)
            if not found:
                click_results.append({
                    "target": name, "success": False,
                    "error": f"超时未找到 ({timeout}s)",
                })

    # 第四步：监控区域 → 返回坐标和用途
    monitors = []
    for i, m in enumerate(monitor_regions):
        monitors.append({
            "name": f"monitor_{i+1}",
            "region": {"x": m["x"], "y": m["y"], "w": m["w"], "h": m["h"]},
            "purpose": m.get("purpose", "search_bound"),
            "purpose_label": m.get("purpose_label", "搜索边界"),
        })

    return {
        "success": True,
        "captured": captured,
        "total": len(captured),
        "click_results": click_results,
        "clicked": sum(1 for r in click_results if r["success"]),
        "failed": sum(1 for r in click_results if not r["success"]),
        "monitors": monitors,
        "monitor_count": len(monitors),
        "cancelled": False,
    }


@feature(
    name="capture_multi",
    display_name="连续框选→逐个点击 + 监控区域（空格结束）",
    description="""【框选·点击·监控】一体式框选操作：左键框选截图+点击，右键框选监控区域（滚轮选用途）。

    **两种框选：**
    1. 🟢 **左键拖拽** → 截图存模板 → 逐个点击（找不到超时跳过）
    2. 🔵 **右键拖拽** → 标记监控区域 → 滚轮选用途 → 左键确认
       - 用途: 搜索边界 / 进度监控 / 颜色变化

    **监控区域用途说明：**
    - search_bound: 限定后续 IMAGE_CLICK/OCR 的搜索范围，提高匹配精度
    - progress: 进度条监控，配合 PROGRESS_WAIT / IF PROGRESS 使用
    - color_change: 颜色变化检测，配合 IF COLOR / WAIT 变化 使用

    **不需要再生成 DSL，一次调用完成全部操作。**

    适用于: 点击桌面图标+监控进度条、游戏操作+全屏搜索边界、UI 测试
    """,
    category=F.ACTION,
    params=[
        P("prefix", "str", "模板名前缀（可选，默认 capture_）", required=False, default="capture_"),
        P("similarity", "number", "匹配相似度 0-1", required=False, default=0.9),
        P("method", "str", "点击方式: single|double|right|middle", required=False, default="single"),
        P("count", "int", "每张模板点击次数", required=False, default=1),
        P("timeout", "number", "每张模板搜索超时秒数", required=False, default=5),
        P("offset_x", "number", "点击 X 偏移", required=False, default=0),
        P("offset_y", "number", "点击 Y 偏移", required=False, default=0),
    ],
    returns="dict{success, captured, click_results, clicked, failed, monitors, monitor_count}",
    examples=[
        "capture_multi",
        "capture_multi method=right",
        "capture_multi count=3 method=double",
        "capture_multi similarity=0.85 offset_x=5 offset_y=5",
        "capture_multi method=middle count=2 timeout=10",
    ],
)
def capture_multi(prefix: str = "capture_", similarity: float = 0.9,
                  method: str = "single", count: int = 1, timeout: float = 5,
                  offset_x: int = 0, offset_y: int = 0) -> dict:
    return _do_capture_multi(prefix=prefix, similarity=similarity,
                             method=method, click_count=count, timeout=timeout,
                             offset_x=offset_x, offset_y=offset_y)


@feature(
    name="ocr_find_text",
    display_name="OCR查找文字",
    description="【感知·定位】在屏幕或指定区域内用OCR查找文字，返回位置坐标(center_x/center_y)和置信度。用于定位按钮/文本后执行点击",
    category=F.PERCEPTION,
    params=[
        P("query", "str", "要查找的文字（支持模糊匹配）", example="开始游戏"),
        P("region", "str", "搜索区域 'x,y,w,h'（可选）", required=False, default=""),
        P("fuzzy_threshold", "number", "模糊匹配阈值 0-1", required=False, default=0.7),
    ],
    returns="dict{found, text, x, y, w, h, confidence}",
    dsl_keyword="OCR_FIND",
    dsl_template="OCR_FIND {query}",
    flow_type="click_text",
    test_cases=[
        TC("empty_query", {"query": ""}, "success",
           validator=lambda r: r.get("success") is True),
    ],
)
def ocr_find_text(query: str, region: str = "", fuzzy_threshold: float = 0.7):
    try:
        from perception.ocr import OCREngine
        engine = OCREngine()
        reg = parse_region(region)
        result = engine.find_text(query, region=reg, threshold=fuzzy_threshold)
        if result:
            x, y, w, h = result.bbox
            return {"found": True, "text": result.text, "x": x, "y": y, "w": w, "h": h,
                    "confidence": result.confidence,
                    "center_x": x + w // 2, "center_y": y + h // 2}
        return {"found": False}
    except ImportError:
        return {"found": False, "error": "OCR不可用"}


@feature(
    name="ocr_extract_all",
    display_name="OCR全文提取",
    description="【感知·全文】提取屏幕或区域内所有可见文字，返回带位置和置信度的列表。适用于数据采集、获取页面全部文本内容",
    category=F.PERCEPTION,
    params=[
        P("region", "str", "区域 'x,y,w,h'（可选）", required=False, default=""),
    ],
    returns="list[dict{text, x, y, confidence}]",
    dsl_keyword="OCR_EXTRACT",
    dsl_template="OCR_EXTRACT",
    flow_type="screenshot",
)
def ocr_extract_all(region: str = ""):
    try:
        from perception.ocr import OCREngine
        engine = OCREngine()
        reg = parse_region(region)
        results = engine.extract_all(reg)
        return [{"text": r.text, "x": r.bbox[0], "y": r.bbox[1], "confidence": r.confidence}
                for r in results]
    except ImportError:
        return []


@feature(
    name="find_image",
    display_name="图像模板匹配",
    description="【感知·模板】在屏幕中查找指定图像模板，支持多尺度匹配。用于查找图标/图片按钮等 OCR 无法识别的非文字元素",
    category=F.PERCEPTION,
    params=[
        P("template_name", "str", "模板图像名称（不含扩展名）", example="attack_button"),
        P("threshold", "number", "匹配阈值 0-1", required=False, default=0.8),
        P("region", "str", "搜索区域 'x,y,w,h'（可选）", required=False, default=""),
        P("find_all", "boolean", "是否查找所有匹配项", required=False, default=False),
    ],
    returns="dict{found, x, y, confidence} or list",
    dsl_keyword="IMAGE_FIND",
    dsl_template="IMAGE_FIND {template_name}",
    flow_type="condition",
    test_cases=[
        TC("nonexistent", {"template_name": "__nonexistent__"}, "success",
           validator=lambda r: r.get("success") and not r["result"]["found"]),
    ],
)
def find_image(template_name: str, threshold: float = 0.8, region: str = "", find_all: bool = False):
    try:
        from perception.vision import TemplateMatcher
        matcher = TemplateMatcher()
        reg = parse_region(region)
        if find_all:
            results = matcher.find_all(template_name, threshold=threshold, region=reg)
            return [{"found": True, "x": pt[0], "y": pt[1], "confidence": conf}
                    for pt, conf in results]
        result = matcher.find(template_name, threshold=threshold, region=reg)
        if result:
            (x, y), conf = result
            return {"found": True, "x": x, "y": y, "confidence": conf}
        return {"found": False}
    except ImportError:
        return {"found": False, "error": "视觉模块不可用"}


@feature(
    name="detect_color",
    display_name="颜色检测",
    description="【感知·颜色】检测区域内指定 HSV 颜色的占比(0.0-1.0)。用于判断画面状态、检测指示灯颜色",
    category=F.PERCEPTION,
    params=[
        P("h", "number", "色相 Hue 0-179", example=120),
        P("s", "number", "饱和度 Saturation 0-255", example=200),
        P("v", "number", "亮度 Value 0-255", example=200),
        P("region", "str", "检测区域 'x,y,w,h'", required=False, default=""),
        P("tolerance", "number", "HSV容差", required=False, default=15),
    ],
    returns="float - 颜色占比 0.0~1.0",
)
def detect_color(h: int, s: int, v: int, region: str = "", tolerance: int = 15) -> float:
    try:
        from perception.vision import ColorDetector
        reg = parse_region(region)
        return ColorDetector.detect_color((h, s, v), tolerance=(tolerance,) * 3, region=reg)
    except ImportError:
        return 0.0


@feature(
    name="detect_progress",
    display_name="进度条检测",
    description="【感知·进度】自动识别区域内的进度条并返回填充比例(0.0-1.0)。用于等待安装/下载/加载完成的场景，替代固定秒数等待",
    category=F.PERCEPTION,
    params=[
        P("region", "str", "进度条区域 'x,y,w,h'（必填）"),
        P("direction", "str", "方向: horizontal|vertical|auto", required=False, default="auto"),
    ],
    returns="float - 进度 0.0~1.0",
)
def detect_progress(region: str, direction: str = "auto") -> float:
    try:
        from perception.vision import ColorDetector
        reg = parse_region(region)
        if reg is None:
            return 0.0
        return ColorDetector.detect_progress_bar(reg, direction=direction)
    except ImportError:
        return 0.0


@feature(
    name="screenshot",
    display_name="截图",
    description="【系统·截图】截取屏幕或区域截图，返回 base64 编码图像。分析场景用 jpeg（更小），文字识别用 png（无损）",
    category=F.SYSTEM,
    params=[
        P("region", "str", "截图区域 'x,y,w,h'（可选，默认全屏）", required=False, default=""),
        P("format", "str", "图像格式 jpeg|png", required=False, default="jpeg"),
        P("quality", "number", "JPEG质量 1-100", required=False, default=70),
    ],
    returns="dict{base64, width, height, format}",
    dsl_keyword="SCREENSHOT",
    dsl_template="SCREENSHOT",
    flow_type="screenshot",
    test_cases=[
        TC("fullscreen", {}, "non_null", skip_in_ci=True),
    ],
)
def screenshot(region: str = "", format: str = "jpeg", quality: int = 70):
    try:
        import base64, cv2
        from perception.vision import ScreenCapture
        reg = parse_region(region)
        img = ScreenCapture.capture(reg)
        h, w = img.shape[:2]
        if format == "jpeg":
            _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        else:
            _, buf = cv2.imencode(".png", img)
        b64 = base64.b64encode(buf).decode()
        return {"base64": b64, "width": w, "height": h, "format": format}
    except ImportError:
        return {"base64": "", "width": 0, "height": 0, "format": format, "error": "模拟模式"}


@feature(
    name="popup_close",
    display_name="关闭弹窗",
    description="【感知·弹窗】自动检测并关闭屏幕上的弹窗，支持 OCR 文字匹配关闭按钮。用于清理系统弹窗、广告、确认对话框",
    category=F.PERCEPTION,
    params=[
        P("text", "str", "要点击的按钮文字（可选，留空则自动检测）", required=False, default=""),
        P("timeout", "number", "超时秒数", required=False, default=5),
    ],
    returns="bool - 是否关闭了弹窗",
    dsl_keyword="POPUP_CLOSE",
    dsl_template="POPUP_CLOSE {text}",
    flow_type="popup_close",
    test_cases=[
        TC("auto_close", {"text": "", "timeout": 2}, "success",
           validator=lambda r: r.get("success") is True, skip_in_ci=True),
    ],
)
def popup_close(text: str = "", timeout: float = 5) -> bool:
    try:
        from interaction.actions import ActionHandler, AutoPopup, ClickTarget
        from perception.ocr import OCREngine
        from perception.vision import TemplateMatcher
        ocr = OCREngine()
        matcher = TemplateMatcher()
        clicker = ClickTarget(ocr, matcher)
        popup = AutoPopup(clicker)
        if text:
            return popup.close_by_text(text, timeout=timeout)
        return popup.check_and_handle(ocr)
    except ImportError:
        return False


@feature(
    name="progress_wait",
    display_name="进度条等待（DSL关键字）",
    description="""【DSL·进度】阻塞等待进度条达到指定阈值后继续执行。

    这是 DSL 关键字，应通过 PROGRESS_WAIT 指令在 DSL 脚本中使用。
    不会直接在 MCP 中调用。

    DSL 语法: PROGRESS_WAIT x,y,w,h threshold [timeout]
    示例:
      PROGRESS_WAIT 100,200,300,20 0.8 30
      PROGRESS_WAIT {progress_area} 0.9 60

    参数说明:
      region: 进度条区域 x,y,w,h（必填）
      threshold: 目标百分比 0.0-1.0（必填）
      timeout: 超时秒数（可选，默认 30s）

    返回: 进度达标或超时后继续后续指令""",
    category=F.CONTROL,
    params=[],
    returns="None（DSL 内嵌指令）",
    dsl_keyword="PROGRESS_WAIT",
    dsl_template="PROGRESS_WAIT {region} {threshold} [timeout]",
    flow_type="progress_wait",
    hidden=True,
    examples=[
        "PROGRESS_WAIT 100,200,300,20 0.8",
        "PROGRESS_WAIT {progress_area} 0.9 60",
    ],
)
def progress_wait():
    raise RuntimeError("PROGRESS_WAIT 是 DSL 内嵌指令，通过 `PROGRESS_WAIT region threshold [timeout]` 使用")


@feature(
    name="progress_init",
    display_name="进度条初始化（DSL关键字）",
    description="""【DSL·进度】交互式框选进度条区域 + 保存模板 + 建立饱和度基线。
    替代手动 REGION_SELECT + PROGRESS_WAIT 两步操作。

    DSL 语法: PROGRESS_INIT var_name
    示例:
      PROGRESS_INIT progress_area

    后续配合 PROGRESS_WAIT {progress_area} 0.8 使用。
    会自动初始化遮挡检测基线和重定位模板。""",
    category=F.CONTROL,
    params=[],
    returns="None（DSL 内嵌指令）",
    dsl_keyword="PROGRESS_INIT",
    dsl_template="PROGRESS_INIT {var_name}",
    flow_type="progress_wait",
    hidden=True,
    examples=[
        "PROGRESS_INIT progress_area",
    ],
)
def progress_init():
    raise RuntimeError("PROGRESS_INIT 是 DSL 内嵌指令，通过 `PROGRESS_INIT var_name` 使用")


@feature(
    name="progress_relocate",
    display_name="进度条重定位（DSL关键字）",
    description="""【DSL·进度】用已保存的进度条模板重新定位其位置。
    进度条被移动后自动找回新坐标。

    DSL 语法: PROGRESS_RELOCATE [var_name]
    示例:
      PROGRESS_RELOCATE progress_area

    需要先执行过 PROGRESS_INIT 保存了模板。""",
    category=F.CONTROL,
    params=[],
    returns="None（DSL 内嵌指令）",
    dsl_keyword="PROGRESS_RELOCATE",
    dsl_template="PROGRESS_RELOCATE {var_name}",
    flow_type="progress_wait",
    hidden=True,
    examples=[
        "PROGRESS_RELOCATE progress_area",
    ],
)
def progress_relocate():
    raise RuntimeError("PROGRESS_RELOCATE 是 DSL 内嵌指令，通过 `PROGRESS_RELOCATE [var_name]` 使用")


@feature(
    name="color_check",
    display_name="颜色检查",
    description="【感知·颜色判定】检测指定区域内是否包含目标颜色，返回 match/ratio 判断结果。效率高于 OCR，适合高频轮询状态变化",
    category=F.PERCEPTION,
    params=[
        P("h", "number", "色相 Hue 0-179", example=120),
        P("s", "number", "饱和度 Saturation 0-255", example=200),
        P("v", "number", "亮度 Value 0-255", example=200),
        P("region", "str", "检测区域 'x,y,w,h'"),
        P("tolerance", "number", "HSV容差", required=False, default=15),
        P("min_ratio", "number", "最小占比 0-1", required=False, default=0.1),
    ],
    returns="dict{match, ratio} - 是否匹配及颜色占比",
    dsl_keyword="COLOR_CHECK",
    dsl_template="COLOR_CHECK {region} {h} {s} {v}",
    flow_type="color_check",
    test_cases=[
        TC("basic", {"h": 0, "s": 0, "v": 0, "region": "0,0,100,100"}, "success"),
    ],
)
def color_check(h: int, s: int, v: int, region: str, tolerance: int = 15, min_ratio: float = 0.1) -> dict:
    try:
        from perception.vision import ColorDetector
        from features._utils import parse_region
        reg = parse_region(region)
        if reg is None:
            return {"match": False, "ratio": 0.0, "error": "无效区域"}
        ratio = ColorDetector.detect_color((h, s, v), tolerance=(tolerance,) * 3, region=reg)
        return {"match": ratio >= min_ratio, "ratio": round(ratio, 3)}
    except ImportError:
        return {"match": False, "ratio": 0.0}


@feature(
    name="wheel_select",
    display_name="滚轮选择器",
    description="""【交互·选择】弹出小窗口让用户用鼠标滚轮选择选项，左键/回车确认。

    替代文字问答，用于所有"让用户选择"的场景：
    - 选择图片获取方式（截取/文件/口头说明）
    - 选择操作类型
    - 选择参数配置

    **所有需要用户做选择的场景都应该用这个工具**，而不是文字提问。

    使用方式：
      1. 调用 wheel_select，传入 options 和 title
      2. 用户看到弹窗，滚轮切换选项
      3. 左键或回车确认，返回选中值
      4. 用户取消则返回 None
    """,
    category=F.PERCEPTION,
    params=[
        P("options", "str", "选项列表，用 | 分隔（如 '截取图片 | 文件选择 | 口头说明'）"),
        P("values", "str", "对应返回值列表，用 | 分隔（可选，默认返回选项文字）", required=False, default=""),
        P("title", "str", "窗口标题", required=False, default="请选择"),
        P("message", "str", "顶部提示文字", required=False, default=""),
        P("default", "int", "默认选中索引", required=False, default=0),
    ],
    returns="str - 选中的 value（取消返回 null）",
    examples=[
        "wheel_select options='截取图片 | 文件选择 | 口头说明' values='capture | file | description' title='选择图片获取方式' message='你要怎样添加图片？'",
        "wheel_select options='左键点击 | 右键点击 | 双击' values='single | right | double' title='选择点击方式'",
    ],
)
def wheel_select(options: str, values: str = "", title: str = "请选择",
                  message: str = "", default: int = 0):
    try:
        from interaction.wheel_selector import WheelSelector
        opts = [o.strip() for o in options.split("|")]
        vals = [v.strip() for v in values.split("|")] if values else None
        result = WheelSelector.select(opts, values=vals, title=title,
                                       message=message, default=default)
        return {"selected": result, "success": result is not None}
    except ImportError:
        return {"selected": None, "success": False, "error": "wheel_selector 模块不可用"}


@feature(
    name="wait_region",
    display_name="等待区域变化/稳定",
    description="""【DSL·感知】阻塞等待屏幕指定区域发生稳定或变化。

    **stable**: 等待区域画面不再变化（适用于等待加载完成、动画结束）
    **change**: 等待区域画面发生变化（适用于等待弹窗出现、页面切换）

    通过定时截图比较像素差异来判断状态。
    用于替代固定 WAIT 秒数，让流程继续得更快。""",
    category=F.PERCEPTION,
    params=[
        P("region", "str", "监控区域 'x,y,w,h'", example="100,200,300,400"),
        P("region_type", "str", "类型: stable|change", required=False, default="stable"),
        P("timeout", "number", "超时秒数", required=False, default=30),
        P("poll_interval", "number", "轮询间隔秒数", required=False, default=0.5),
    ],
    returns="dict{matched, elapsed} - 检测结果",
    dsl_keyword="REGION_WAIT",
    dsl_template="REGION_WAIT {region} {region_type} {timeout}",
    flow_type="region_wait",
    examples=[
        "REGION_WAIT 100,200,300,400 stable",
        "REGION_WAIT 200,100,400,30 change 15",
    ],
    tags=["perception", "dsl", "wait"],
)
def wait_region(region: str, region_type: str = "stable",
                timeout: float = 30, poll_interval: float = 0.5) -> dict:
    try:
        from features._utils import parse_region
        from perception.vision import ScreenCapture
        import time, cv2, numpy as np

        reg = parse_region(region)
        if reg is None:
            return {"matched": False, "elapsed": 0, "error": "无效区域"}

        deadline = time.time() + timeout
        prev = ScreenCapture.capture(reg)

        while time.time() < deadline:
            time.sleep(poll_interval)
            current = ScreenCapture.capture(reg)
            if current is None or prev is None:
                continue
            diff = cv2.absdiff(prev, current)
            change_ratio = np.count_nonzero(diff > 30) / diff.size

            if region_type == "stable" and change_ratio < 0.01:
                return {"matched": True, "elapsed": round(time.time() - (deadline - timeout), 2)}
            elif region_type == "change" and change_ratio > 0.02:
                return {"matched": True, "elapsed": round(time.time() - (deadline - timeout), 2)}

            prev = current

        return {"matched": False, "elapsed": round(timeout, 2), "error": "超时"}
    except ImportError:
        return {"matched": False, "elapsed": 0, "error": "视觉模块不可用"}


@feature(
    name="wait_screen_change",
    display_name="等待屏幕变化",
    description="""【DSL·感知】阻塞等待屏幕画面发生变化（全屏）。
    通过定时截图比对像素差异，检测到变化时立即返回。
    适用于等待弹窗出现、页面切换等场景。替代固定 WAIT，让流程继续得更快。""",
    category=F.PERCEPTION,
    params=[
        P("timeout", "number", "超时秒数", required=False, default=30),
        P("poll_interval", "number", "轮询间隔秒数", required=False, default=0.5),
        P("threshold", "number", "变化判定阈值（像素差异比例 0-1）", required=False, default=0.02),
    ],
    returns="dict{matched, elapsed} - 是否检测到变化",
    dsl_keyword="WAITSCREEN",
    dsl_template="WAITSCREEN {timeout}",
    flow_type="region_wait",
    examples=["WAITSCREEN 30", "WAITSCREEN"],
    tags=["perception", "dsl", "wait"],
)
def wait_screen_change(timeout: float = 30, poll_interval: float = 0.5,
                        threshold: float = 0.02) -> dict:
    """等待全屏画面发生变化"""
    import time
    try:
        import mss
        import numpy as np
        with mss.mss() as sct:
            mon = sct.monitors[1]
            prev = np.array(sct.grab(mon))
            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(poll_interval)
                current = np.array(sct.grab(mon))
                diff = np.abs(current.astype(np.int16) - prev.astype(np.int16))
                change_ratio = np.count_nonzero(diff > 30) / diff.size
                if change_ratio > threshold:
                    return {"matched": True, "elapsed": round(time.time() - (deadline - timeout), 2)}
                prev = current
            return {"matched": False, "elapsed": round(timeout, 2), "error": "超时"}
    except ImportError:
        # 降级：直接 sleep timeout
        time.sleep(min(timeout, 10))
        return {"matched": True, "elapsed": timeout, "note": "mss不可用，降级sleep"}


@feature(
    name="wait_screen_stable",
    display_name="等待屏幕静止",
    description="""【DSL·感知】阻塞等待屏幕画面停止变化（全屏）。
    通过定时截图比对像素差异，直到画面连续稳定才返回。
    适用于等待加载完成、动画结束等场景。替代固定 WAIT，让流程继续得更快。""",
    category=F.PERCEPTION,
    params=[
        P("timeout", "number", "超时秒数", required=False, default=30),
        P("poll_interval", "number", "轮询间隔秒数", required=False, default=0.5),
        P("stability_threshold", "number", "稳定判定阈值（像素差异比例 0-1）", required=False, default=0.01),
        P("stable_rounds", "number", "连续稳定次数", required=False, default=2),
    ],
    returns="dict{matched, elapsed} - 是否已稳定",
    dsl_keyword="SCREENSTABLE",
    dsl_template="SCREENSTABLE {timeout}",
    flow_type="region_wait",
    examples=["SCREENSTABLE 30", "SCREENSTABLE"],
    tags=["perception", "dsl", "wait"],
)
def wait_screen_stable(timeout: float = 30, poll_interval: float = 0.5,
                        stability_threshold: float = 0.01, stable_rounds: int = 2) -> dict:
    """等待全屏画面静止"""
    import time
    try:
        import mss
        import numpy as np
        with mss.mss() as sct:
            mon = sct.monitors[1]
            prev = np.array(sct.grab(mon))
            stable_count = 0
            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(poll_interval)
                current = np.array(sct.grab(mon))
                diff = np.abs(current.astype(np.int16) - prev.astype(np.int16))
                change_ratio = np.count_nonzero(diff > 30) / diff.size
                if change_ratio < stability_threshold:
                    stable_count += 1
                    if stable_count >= stable_rounds:
                        return {"matched": True, "elapsed": round(time.time() - (deadline - timeout), 2)}
                else:
                    stable_count = 0
                prev = current
            return {"matched": False, "elapsed": round(timeout, 2), "error": "超时"}
    except ImportError:
        time.sleep(min(timeout, 10))
        return {"matched": True, "elapsed": timeout, "note": "mss不可用，降级sleep"}

@feature(
    name="scene_match",
    display_name="场景识别",
    description="""【DSL·感知】识别当前屏幕是否匹配指定场景。

    通过 AI 或图像特征匹配判断当前画面是否为目标场景。
    适用于条件分支中的场景判断：如在游戏中判断是否在战斗/菜单/设置界面。

    **注意**: 需要预训练场景模型或配置场景模板。无模型时默认返回模拟匹配。""",
    category=F.PERCEPTION,
    params=[
        P("scene_name", "str", "场景名称", example="battle_scene"),
        P("similarity", "number", "相似度阈值 0-100", required=False, default=70),
    ],
    returns="dict{match, confidence} - 匹配结果",
    dsl_keyword="SCENE_CONDITION",
    dsl_template="SCENE_CONDITION {scene_name} {similarity}",
    flow_type="scene_condition",
    examples=[
        "SCENE_CONDITION battle_scene 80",
        "SCENE_CONDITION main_menu",
    ],
    tags=["perception", "dsl", "condition"],
)
def scene_match(scene_name: str, similarity: int = 70) -> dict:
    """场景识别，默认返回模拟结果（未匹配）。
    实际使用时需集成场景分类模型或模板匹配。"""
    try:
        # 尝试用图像模板匹配场景
        from perception.vision import TemplateMatcher
        matcher = TemplateMatcher()
        result = matcher.find(scene_name, threshold=similarity / 100.0)
        if result:
            (x, y), conf = result
            return {"match": True, "confidence": round(conf * 100, 1)}
        return {"match": False, "confidence": 0}
    except ImportError:
        return {"match": False, "confidence": 0, "error": "视觉模块不可用"}
