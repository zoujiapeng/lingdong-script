# 截图 + OCR 识别 + 点击工作流
# 用途: 截取屏幕区域, OCR 识别文字后点击目标

SCREENSHOT region=(0,0,1920,1080) save_path=page.png
OCR_FIND "登录"
IF found:
  CLICK {ocr_x} {ocr_y}
  WAIT 2
ELSE
  SCREENSHOT region=(0,0,1920,1080) save_path=page_retry.png
  OCR_FIND "登录"
  CLICK {ocr_x} {ocr_y}
END
WAIT 3
CLICK 960 540 double
