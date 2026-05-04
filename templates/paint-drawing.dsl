# 画板绘画工作流 v3
# 用途: 打开 Windows 画图 → 最大化 → 检测画布 → 画横线 → 截图验证
# 全部使用 Lobster 内置 DSL 能力
#
# 改进点:
#   - WAITSCREEN/SCREENSTABLE 替代硬编码 WAIT
#   - FOCUS 确保画板在前台
#   - DRAG 坐标内联算术 ({canvas_y2 - 50})
#   - SCREENSHOT 执行后截图验证

LAUNCH mspaint.exe
WAIT 1
WAITSCREEN 10
HOTKEY win+up
SCREENSTABLE 5
FOCUS 画图
FINDCANVAS tolerance=30

# 画一条横线，从左侧1/4到右侧3/4，垂直居中
DRAG {canvas_x1} {canvas_y2} TO {canvas_x3} {canvas_y2}

WAIT 1
SCREENSHOT
