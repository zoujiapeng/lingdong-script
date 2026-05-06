# ---
# name: 画板绘画
# aliases: ["画图", "绘画", "paint", "mspaint"]
# tags: ["system", "drawing", "canvas"]
# keywords: ["画图", "画板", "绘画", "画布", "横线", "mspaint"]
# category: system
# ---
# 打开 Windows 画图 → 最大化 → 检测画布 → 画横线 → 截图验证

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
