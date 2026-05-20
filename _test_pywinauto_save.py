"""
验证 pywinauto 能否直接操作记事本另存为对话框
"""
import sys, time, os, win32gui, win32con, win32api
sys.path.insert(0, 'python')
from agent.menu_handler import MenuHandler

desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")

def log(*args):
    line = ' '.join(str(a) for a in args)
    try: print(line, flush=True)
    except UnicodeEncodeError: print(line.encode('gbk', errors='replace').decode('gbk'), flush=True)

def find_hwnd(cls):
    found = [0]
    def cb(w, _):
        if win32gui.IsWindowVisible(w) and win32gui.GetClassName(w) == cls and win32gui.GetParent(w) == 0:
            if win32gui.GetWindowText(w): found[0] = w; return False
        return True
    win32gui.EnumWindows(cb, 0)
    return found[0]

# Clean up
for _ in range(4):
    win32api.keybd_event(0x1B, 0, 0, 0); time.sleep(0.04)
    win32api.keybd_event(0x1B, 0, 2, 0); time.sleep(0.3)

# Launch Notepad
hwnd = find_hwnd('Notepad')
if not hwnd:
    os.startfile("notepad.exe"); time.sleep(2)
    hwnd = find_hwnd('Notepad')
log(f"Notepad HWND={hwnd}")

# Type text
import ctypes
user32 = ctypes.windll.user32
user32.SetForegroundWindow(hwnd); time.sleep(0.5)
for ch in "Hello pywinauto save test":
    vk = ord(ch.upper()) if ch.isalpha() else ord(ch)
    sft = ch.isupper()
    if sft: win32api.keybd_event(0x10, 0, 0, 0); time.sleep(0.01)
    win32api.keybd_event(vk, 0, 0, 0); time.sleep(0.02)
    win32api.keybd_event(vk, 0, 2, 0); time.sleep(0.03)
    if sft: win32api.keybd_event(0x10, 0, 2, 0)
    time.sleep(0.02)

# Menu: Alt+F > A
h = MenuHandler()
h._press_key(0x12); time.sleep(0.5)
h._press_key(ord('F')); time.sleep(1.0)
h._press_key(ord('A')); time.sleep(2.5)
log("Alt+F > A  done")

# === 用 pywinauto 操作对话框 ===
from pywinauto import Desktop, Application

# 方法1：通过 Desktop 找对话框
try:
    dlg = Desktop(backend="win32").window(title="另存为")
    log(f"Desktop.find: title={dlg.window_text()}")
    # 找文件名输入框
    edit = dlg.Edit
    log(f"  Edit: {edit.window_text()}")
    edit.set_edit_text("lobster_pywinauto.txt")
    log("  set text OK")
    time.sleep(0.3)
    # 点保存
    save_btn = dlg.Button
    log(f"  Button: {save_btn.window_text()}")
    save_btn.click()
    time.sleep(2)
    log("  save clicked!")
except Exception as e:
    log(f"Desktop(win32) failed: {e}")

# 验证
path = os.path.join(desktop, "lobster_pywinauto.txt")
if os.path.exists(path):
    with open(path, 'r') as f:
        log(f"  FILE SAVED! content: {f.read()[:80]}")
else:
    log("  File NOT found via Desktop(win32)")

    # 方法2：UIA backend
    try:
        log("\n  Trying UIA backend...")
        dlg2 = Desktop(backend="uia").window(title="另存为")
        log(f"  UIA dlg: {dlg2.window_text()}")
        # There might be multiple Edit controls
        edits = dlg2.descendants(control_type="Edit")
        log(f"  UIA Edit controls: {len(edits)}")
        for i, e in enumerate(edits):
            log(f"    [{i}] name={e.element_info.name} rect={e.rectangle()}")

        # Try to set filename
        if edits:
            path2 = os.path.join(desktop, "lobster_pywinauto2.txt")
            edits[0].set_edit_text(path2)
            log("  set text via UIA OK")
            time.sleep(0.3)
            # Find Save button
            btns = dlg2.descendants(control_type="Button")
            for b in btns:
                if '保存' in b.window_text() or 'Save' in b.window_text():
                    b.click()
                    log(f"  clicked {b.window_text()}")
                    break
            time.sleep(2)
    except Exception as e2:
        log(f"  UIA also failed: {e2}")

# Final check
for fname in ['lobster_pywinauto.txt', 'lobster_pywinauto2.txt']:
    p = os.path.join(desktop, fname)
    if os.path.exists(p):
        with open(p, 'r') as f:
            log(f"\n  {fname} SAVED: {f.read()[:80]}")

# Cleanup
for _ in range(3):
    win32api.keybd_event(0x1B, 0, 0, 0); time.sleep(0.04)
    win32api.keybd_event(0x1B, 0, 2, 0); time.sleep(0.3)
