# ---
# name: 区域监控流程
# aliases: ["监控", "monitor", "区域监控"]
# tags: ["image", "monitor", "loop"]
# keywords: ["监控", "区域", "变化检测", "轮询", "OCR"]
# category: image
# ---
# 循环监控屏幕区域直到发生变化，OCR识别文字后执行操作

LOOP 10
  REGION_WAIT change x=100 y=200 w=300 h=400 timeout=30 poll=0.5
  IF change_detected:
    SCREENSHOT
    OCR_FIND "确认"
    CLICK {ocr_x} {ocr_y}
    WAIT 2
    BREAK
  ELSE
    WAIT 5
  END
END

CLICK 960 540
WAIT 1
SCREENSHOT
