# 监控流程: 循环监控页面变化
# 用途: 监控某个区域直到发生变化, 然后执行操作
# 注意: 需要先配置 REGION_WAIT 的区域参数

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
