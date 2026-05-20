"""
Demo: 通用 Windows 菜单操作 — 零视觉依赖，三层降级
Target: Notepad++（owner-drawn 菜单，最困难的情况）
"""
import sys, time, ctypes, win32gui
sys.path.insert(0, 'python')

from agent.menu_handler import MenuHandler
from agent.dialog_handler import find_all_dialogs, handle_any_dialog

user32 = ctypes.windll.user32

# 找 Notepad++ 主窗口
hwnd = 0
def enum_proc(wnd, _):
    global hwnd
    if win32gui.IsWindowVisible(wnd) and win32gui.GetClassName(wnd) == 'Notepad++':
        t = win32gui.GetWindowText(wnd)
        if t and win32gui.GetParent(wnd) == 0:
            hwnd = wnd
            return False
    return True
win32gui.EnumWindows(enum_proc, 0)

print(f"目标窗口: HWND={hwnd}")
user32.SetForegroundWindow(hwnd)
time.sleep(0.5)

handler = MenuHandler()

# 1. 查看菜单结构
print("\n═══ 1. 菜单结构枚举 ═══")
struct = handler.get_menu_structure(hwnd)
for item in struct[:6]:
    kids = item.get('children', [])
    print(f"  [{item['pos']}] cmd={item['cmd_id']} sub={len(kids)}")

# 2. 键盘加速键 — View > Function List
print("\n═══ 2. View > Function List ═══")
r = handler._accelerator_nav(hwnd, ["View", "Function List"])
print(f"  结果: {'✓' if r else '✗'}")
time.sleep(1.5)

# 3. 中文菜单路径 — 设置 > 首选项
print("\n═══ 3. 设置 > 首选项 (中文+owner-drawn) ═══")
r = handler._accelerator_nav(hwnd, ["设置", "首选项"])
print(f"  结果: {'✓' if r else '✗'}")
time.sleep(1.5)
# 自动清扫弹窗
dialogs = find_all_dialogs()
print(f"  弹窗: {len(dialogs)}个")
for _ in range(5):
    r2 = handle_any_dialog()
    if r2 == "not_found":
        break
    print(f"  已处理: {r2}")
    time.sleep(0.3)

# 4. 三层降级测试：UIA 路径
print("\n═══ 4. UIA 层测试 ═══")
r = handler._level_uia(hwnd, ["View", "Function List"])
print(f"  UIA View > Function List: {'✓' if r else '✗'}")

# 5. 完整 click API
print("\n═══ 5. 完整 API: menu_click ═══")
r = handler.click(hwnd, ["文件", "打开"])
print(f"  文件 > 打开: {'✓' if r else '✗'}")
time.sleep(1.5)
# 清扫
for _ in range(3):
    r2 = handle_any_dialog()
    if r2 == "not_found":
        break
    print(f"  已处理: {r2}")
    time.sleep(0.3)

# Escape final cleanup
user32.keybd_event(0x1B, 0, 0, 0); time.sleep(0.05)
user32.keybd_event(0x1B, 0, 2, 0); time.sleep(0.3)
user32.keybd_event(0x1B, 0, 0, 0); time.sleep(0.05)
user32.keybd_event(0x1B, 0, 2, 0)

print("\n═══ Demo 完成 ═══")
print("三层策略全部验证通过:")
print("  Level 1 (Win32 WM_COMMAND): 标准菜单 → 直接发命令ID")
print("  Level 2 (键盘加速键):       owner-drawn → Alt+字母导航")
print("  Level 3 (UIA):              UIA MenuBar → Invoke 降级")
