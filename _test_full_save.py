"""
端到端测试：menu_handler 导航 + pywinauto 保存对话框
测试对象：记事本 + Notepad++
"""
import sys, time, os, ctypes, win32gui, win32con, win32api
sys.path.insert(0, 'python')

from agent.menu_handler import MenuHandler
from agent.dialog_handler import fill_save_dialog, find_all_dialogs, handle_any_dialog

user32 = ctypes.windll.user32
desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
handler = MenuHandler()

def log(*args):
    line = ' '.join(str(a) for a in args)
    try: print(line, flush=True)
    except UnicodeEncodeError: print(line.encode('gbk', errors='replace').decode('gbk'), flush=True)

def esc():
    win32api.keybd_event(0x1B, 0, 0, 0); time.sleep(0.03)
    win32api.keybd_event(0x1B, 0, 2, 0); time.sleep(0.3)

def find_hwnd(cls):
    found = [0]
    def cb(w, _):
        if win32gui.IsWindowVisible(w) and win32gui.GetClassName(w) == cls and win32gui.GetParent(w) == 0:
            if win32gui.GetWindowText(w): found[0] = w; return False
        return True
    win32gui.EnumWindows(cb, 0)
    return found[0]

def focus(hwnd):
    user32.SetForegroundWindow(hwnd); user32.BringWindowToTop(hwnd); time.sleep(0.5)

def type_text(text):
    for ch in text:
        vk = ord(ch.upper()) if ch.isalpha() else ord(ch)
        sft = ch.isupper() or ch in '~!@#$%^&*()_+{}|:"<>?'
        if sft: win32api.keybd_event(0x10, 0, 0, 0); time.sleep(0.01)
        win32api.keybd_event(vk, 0, 0, 0); time.sleep(0.02)
        win32api.keybd_event(vk, 0, 2, 0); time.sleep(0.03)
        if sft: win32api.keybd_event(0x10, 0, 2, 0)
        time.sleep(0.02)

# ── Clean start ──
for _ in range(4): esc()
for _ in range(3):
    if handle_any_dialog() == "not_found": break
    time.sleep(0.3)

total_start = time.time()

# ═══════════ 1. 记事本 (#32770 旧式对话框) ═══════════
log("\n" + "="*60)
log("测试1: 记事本 (旧式 #32770 对话框)")
log("="*60)

hwnd_np = find_hwnd('Notepad')
if not hwnd_np:
    os.startfile("notepad.exe"); time.sleep(2)
    hwnd_np = find_hwnd('Notepad')
log(f"HWND={hwnd_np}")

t0 = time.time()
focus(hwnd_np)
type_text("Lobster automation - Notepad save via pywinauto")
log(f"输入: {time.time()-t0:.1f}s")

# 菜单 Alt+F > A
t0 = time.time()
focus(hwnd_np)
handler._press_key(0x12); time.sleep(0.4)
handler._press_key(ord('F')); time.sleep(0.8)
handler._press_key(ord('A')); time.sleep(2)
log(f"Alt+F>A: {time.time()-t0:.1f}s")

# pywinauto 填写保存对话框
t0 = time.time()
path1 = os.path.join(desktop, "lobster_test_notepad.txt")
ok1 = fill_save_dialog(path1, timeout=8)
log(f"fill_save_dialog: {'OK' if ok1 else 'FAIL'} ({time.time()-t0:.1f}s)")

# 验证
saved1 = os.path.exists(path1)
log(f"文件: {'✓' if saved1 else '✗'}")

# ═══════════ 2. Notepad++ (IFileDialog 现代对话框) ═══════════
log("\n" + "="*60)
log("测试2: Notepad++ (IFileDialog 现代对话框)")
log("="*60)

hwnd_npp = find_hwnd('Notepad++')
if not hwnd_npp:
    log("未运行，尝试启动...")
    os.startfile("notepad++.exe"); time.sleep(3)
    hwnd_npp = find_hwnd('Notepad++')
if not hwnd_npp:
    log("找不到 Notepad++，跳过")
    saved2 = False
else:
    log(f"HWND={hwnd_npp}")

    t0 = time.time()
    focus(hwnd_npp)
    type_text("Lobster automation - NPP save via pywinauto")
    log(f"输入: {time.time()-t0:.1f}s")

    # owner-drawn 菜单，用 _accelerator_nav
    t0 = time.time()
    focus(hwnd_npp)
    # Notepad++: 文件(F) → 另存为(A)...
    r = handler._accelerator_nav(hwnd_npp, ["文件", "另存为"])
    log(f"菜单 文件>另存为: {'OK' if r else 'FAIL'} ({time.time()-t0:.1f}s)")
    time.sleep(2)

    # pywinauto 填写保存对话框 (IFileDialog)
    t0 = time.time()
    path2 = os.path.join(desktop, "lobster_test_npp.txt")
    ok2 = fill_save_dialog(path2, timeout=8)
    log(f"fill_save_dialog: {'OK' if ok2 else 'FAIL'} ({time.time()-t0:.1f}s)")

    saved2 = os.path.exists(path2)
    log(f"文件: {'✓' if saved2 else '✗'}")

# ── 清理 ──
time.sleep(1)
esc()
try:
    if saved2 and os.path.exists(path2):
        with open(path2) as f: t = f.read()[:80]
        log(f"NPP 内容: {t}")
except: pass
if saved1 and os.path.exists(path1):
    with open(path1) as f: t = f.read()[:80]
    log(f"记事本内容: {t}")

log("\n" + "="*60)
log(f"总耗时: {time.time()-total_start:.1f}s")
log(f"记事本: {'✓' if saved1 else '✗'}")
try: log(f"NPP:    {'✓' if saved2 else '✗'}")
except: pass
log("="*60)
