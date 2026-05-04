# 画板绘画工作流
# 用途: 打开 Windows 画图 → 检测画布 → 在画布上画图
# 原理: FINDCANVAS 视觉识别白色画布区域，然后 CLICK/DRAG 画图
#
# 检测成功后设置这些变量:
#   {canvas_left} {canvas_top} {canvas_right} {canvas_bottom} (四边)
#   {canvas_center_x} {canvas_center_y} (中心)
#   {canvas_x1} {canvas_x2} {canvas_x3}  (左1/4 / 中 / 右3/4)
#   {canvas_y1} {canvas_y2} {canvas_y3}  (上1/4 / 中 / 下3/4)

LAUNCH mspaint.exe
WAIT 2
HOTKEY alt+space
WAIT 0.3
HOTKEY x
WAIT 1
FINDCANVAS tolerance=30

# 从左上到右下画一条线
DRAG {canvas_x1} {canvas_y1} TO {canvas_x3} {canvas_y3}

# 再从右上到左下画一条线
DRAG {canvas_x3} {canvas_y1} TO {canvas_x1} {canvas_y3}

WAIT 2
