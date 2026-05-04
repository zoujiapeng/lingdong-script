"""
图像点击：多目标优先级图像匹配 + 点击
媲美源项目 image_click 节点：多目标按优先级尝试，相似度/点击方式/偏移量/超时全支持
"""
from features.registry import feature, P, TC, FeatureCategory as F
from features._utils import parse_region


@feature(
    name="image_click",
    display_name="图像点击（多目标优先级）",
    description="""【视觉·点击】在屏幕上查找指定图像模板并点击。支持多目标优先级：传入多个模板名，按顺序查找第一个匹配的并点击。

## 使用流程
在调用此工具前，先用 **wheel_select** 让用户选择图片获取方式，**不要文字提问**：

  wheel_select options='截取图片 | 从资源管理器选择 | 口头说明'
              values='capture | file | description'
              title='选择图片获取方式'
              message='你要怎样添加图片？'

根据用户选择，用 DSL 批处理执行（不是一步步调 MCP）：

  1. 【截取图片】→ 先调 capture_multi 连续框选（框选→自动存→空格结束），再生成 DSL:
     IMAGE_CLICK capture_1, capture_2

  2. 【文件选择】→ 先调 select_images 获取模板名，再生成 DSL:
     IMAGE_CLICK 模板名1, 模板名2

  3. 【口头说明】→ 先调 search_images 搜索本地图片，再用模板名:
     IMAGE_CLICK 模板名

关键原则：用 `lobster exec` 执行完整 DSL，不要分步调 MCP 工具。

核心特性:
- 多目标优先级: targets="icon_a.png, icon_b.png" → 先找 icon_a，找不到再找 icon_b
- 点击方式: method=single/double/right
- 偏移量: offset_x/y 微调点击位置
- 相似度阈值: similarity=0.9 控制匹配精度
- 全部匹配: match_all=true 点击所有匹配项

适用于: 桌面图标点击、游戏界面操作、OCR无法识别的按钮""",
    category=F.ACTION,
    params=[
        P("targets", "str", "模板名称（多个用逗号分隔，按优先级排序）", example="attack_btn, defend_btn"),
        P("similarity", "number", "匹配相似度 0-1", required=False, default=0.9),
        P("method", "str", "点击方式: single|double|right", required=False, default="single"),
        P("timeout", "number", "每张图搜索超时秒数", required=False, default=5),
        P("region", "str", "限定搜索区域 'x,y,w,h'（可选）", required=False, default=""),
        P("offset_x", "number", "点击 X 偏移", required=False, default=0),
        P("offset_y", "number", "点击 Y 偏移", required=False, default=0),
        P("match_all", "boolean", "是否点击所有匹配项", required=False, default=False),
    ],
    returns="dict{success, target, x, y, confidence} - 点击结果",
    dsl_keyword="IMAGE_CLICK",
    dsl_template="IMAGE_CLICK {targets}",
    flow_type="image_click",
    examples=[
        "IMAGE_CLICK attack_btn",
        "IMAGE_CLICK icon_a.png, icon_b.png",
        "IMAGE_CLICK target_icon sim=0.85 method=right",
        "# 截图方式完整 DSL: lobster exec 执行",
        "# REGION_SELECT _target 请框选你要点击的目标",
        "# TEMPLATE_CAPTURE _target 模板名",
        "# IMAGE_CLICK 模板名",
        "# 文件选择方式 DSL: 先 select_images 选图，再:",
        "# IMAGE_CLICK 模板名1, 模板名2",
    ],
    test_cases=[
        TC("empty", {"targets": ""}, "success",
           validator=lambda r: r.get("success") is True),
    ],
)
def image_click(
    targets: str,
    similarity: float = 0.9,
    method: str = "single",
    timeout: float = 5,
    region: str = "",
    offset_x: int = 0,
    offset_y: int = 0,
    match_all: bool = False,
) -> dict:
    try:
        from interaction.actions import ActionHandler
        handler = ActionHandler()
        return handler._handle_image_click(
            targets=targets,
            similarity=similarity,
            method=method,
            timeout=timeout,
            region=region,
            offset_x=offset_x,
            offset_y=offset_y,
            match_all=match_all,
        )
    except ImportError:
        return {"success": False, "error": "ActionHandler 不可用"}
