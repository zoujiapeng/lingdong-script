# 登录流程: 输入账号密码并登录
# 用途: 自动填写登录表单
# 需要: 先手动将光标定位在账号输入框

CLICK 500 400
TYPE "admin@example.com"
WAIT 0.5
HOTKEY tab
WAIT 0.3
TYPE "MyPassword123"
WAIT 0.5
CLICK 500 520
WAIT 3

# 检测是否登录成功
SCENE_CONDITION "首页"
IF scene_match:
  CLICK 960 540
  WAIT 1
  SCREENSHOT
ELSE
  CLICK 500 520
  WAIT 2
  SCREENSHOT
END
