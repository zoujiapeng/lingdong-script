"""
端到端测试：菜单操作 + 保存文件 + 验证保存结果
使用键盘模拟跨进程操作
"""
import sys, time, os, ctypes, win32gui, win32con, win32api
sys.path.insert(0, 'python')

from agent.menu_handler import MenuHandler
from agent.dialog_handler import find_all_dialogs, handle_any_dialog

user32 = ctypes.windll.user32
desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")

def log(*args):
    line = ' '.join(str(a) for a in args)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode('gbk', errors='replace').decode('gbk'), flush=True)

def press_key(vk, delay=0.04):
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(delay)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.1)

def type_text(text):
    for ch in text:
        if ch.isalpha():
            shift = ch.isupper()
            vk = ord(ch.upper())
        elif ch in '~!@#$%^&*()_+{}|:"<>?':
            shift = True
            # Map symbol to VK — basic mapping
            smap = {'~':'`', '!':'1', '@':'2', '#':'3', '$':'4', '%':'5',
                    '^':'6', '&':'7', '*':'8', '(':'9', ')':'0',
                    '_':'-', '+':'=', '{':'[', '}':']', '|':'\\',
                    ':':';', '"':"'", '<':',', '>':'.', '?':'/'}
            vk = ord(smap.get(ch, ch))
        else:
            shift = False
            vk = ord(ch)
        if shift:
            win32api.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
            time.sleep(0.02)
        press_key(vk)
        if shift:
            win32api.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.03)

def find_hwnd(class_name):
    found = [0]
    def cb(wnd, _):
        if win32gui.IsWindowVisible(wnd) and win32gui.GetClassName(wnd) == class_name and win32gui.GetParent(wnd) == 0:
            t = win32gui.GetWindowText(wnd)
            if t:
                found[0] = wnd
                return False
        return True
    win32gui.EnumWindows(cb, 0)
    return found[0]

def focus(hwnd):
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.5)

handler = MenuHandler()
start_all = time.time()

# ═══════ 1. Notepad ═══════
log("\n" + "="*60)
log("测试1: 记事本 — 旧式通用对话框")
log("="*60)

hwnd_np = find_hwnd('Notepad')
if not hwnd_np:
    os.startfile("notepad.exe")
    time.sleep(2)
    hwnd_np = find_hwnd('Notepad')
log(f"找到 HWND={hwnd_np}")

# 输入
t0 = time.time()
focus(hwnd_np)
type_text("Lobster automation test - save from Notepad.")
log(f"输入: {time.time()-t0:.1f}s")

# 菜单: Alt+F → A
t0 = time.time()
focus(hwnd_np)
handler._press_key(0x12); time.sleep(0.5)
press_key(ord('F')); time.sleep(1.0)
press_key(ord('A')); time.sleep(2.5)
log(f"Alt+F>A: {time.time()-t0:.1f}s")

# 检测对话框
dlg_save = None
for d in find_all_dialogs():
    if d.class_name == '#32770' and ('另存' in d.title or 'Save' in d.title):
        dlg_save = d
        break
if not dlg_save:
    log("  FAIL: 保存对话框未出现")
    saved1 = False
else:
    log(f"  对话框: hwnd={dlg_save.hwnd} title={dlg_save.title}")
    focus(dlg_save.hwnd)
    time.sleep(0.5)

    # 键盘保存: 只输文件名, 回车
    t0 = time.time()
    # Alt+N 聚焦文件名 (标准快捷键)
    win32api.keybd_event(0x12, 0, 0, 0); time.sleep(0.05)  # Alt down
    press_key(ord('N'))  # N for filename
    win32api.keybd_event(0x12, 0, win32con.KEYEVENTF_KEYUP, 0)  # Alt up
    time.sleep(0.7)

    # Ctrl+A 全选
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    press_key(ord('A'))
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.3)

    # 输文件名
    type_text("lobster_test_notepad.txt")
    time.sleep(0.3)
    # 回车 = 保存
    press_key(win32con.VK_RETURN)
    time.sleep(3)
    log(f"键盘保存: {time.time()-t0:.1f}s")

    # 检查
    path1 = os.path.join(desktop, "lobster_test_notepad.txt")
    saved1 = os.path.exists(path1)
    if saved1:
        with open(path1, 'r', encoding='utf-8') as f:
            log(f"  内容: {f.read()[:100]}")
    else:
        log("  FAIL: 桌面未找到")
        # 如果被覆盖提示拦截
        for d2 in find_all_dialogs():
            log(f"  残留窗口: {d2.class_name} {d2.title}")

# ═══════ 2. Notepad++ ═══════
log("\n" + "="*60)
log("测试2: Notepad++")
log("="*60)

hwnd_npp = find_hwnd('Notepad++')
if not hwnd_npp:
    log("  Notepad++ 未运行，跳过")
    saved2 = False
else:
    log(f"找到 HWND={hwnd_npp}")
    focus(hwnd_npp)

    # 输入
    t0 = time.time()
    type_text("Lobster automation test - save from NPP.")
    log(f"输入文字: {time.time()-t0:.1f}s")

    # 菜单: 文件 > 另存为 (owner-drawn, 用 _accelerator_nav)
    t0 = time.time()
    # 先试 Alt+F → A (Save As accelerator)
    # Notepad++: 文件(F) → 另存为(A)... accelerator 是 A
    handler._press_key(0x12)  # Alt
    time.sleep(0.5)
    press_key(ord('F'))
    time.sleep(1.0)
    press_key(ord('A'))
    time.sleep(2.5)
    log(f"Alt+F > A: {time.time()-t0:.1f}s")

    # 现代 IFileDialog — 全键盘操作
    t0 = time.time()
    # Ctrl+L 定位到地址栏
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    press_key(ord('L'))
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.5)

    # 输路径
    path2 = os.path.join(desktop, "lobster_test_npp.txt")
    type_text(path2)
    time.sleep(0.5)

    # Enter to go to path
    press_key(win32con.VK_RETURN)
    time.sleep(2)

    # Enter to confirm file name / save
    press_key(win32con.VK_RETURN)
    time.sleep(2.5)
    log(f"IFileDialog 键盘操作: {time.time()-t0:.1f}s")

    saved2 = os.path.exists(path2)
    if saved2:
        with open(path2, 'r', encoding='utf-8') as f:
            log(f"  内容: {f.read()[:100]}")
    else:
        log("  FAIL: 桌面未找到 NPP 保存文件")
        for fname in os.listdir(desktop):
            if 'lobster' in fname.lower():
                log(f"  但找到: {fname}")

# ── 最终清理 ──
time.sleep(1)
esc_n = 0
for _ in range(8):
    r = handle_any_dialog()
    if r == "not_found":
        if esc_n >= 4:
            break
        win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.3)
        esc_n += 1

total = time.time() - start_all
log("\n" + "="*60)
log(f"总耗时: {total:.1f}s")
log(f"Notepad 保存: {'OK' if saved1 else 'FAIL'}")
try: log(f"NPP 保存: {'OK' if saved2 else 'FAIL'}")
except: pass
log("="*60)
