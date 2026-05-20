"""
诊断: 记事本另存为对话框的子控件结构
"""
import sys, time, os, ctypes, win32gui, win32con, win32api
sys.path.insert(0, 'python')

user32 = ctypes.windll.user32

def log(*args):
    line = ' '.join(str(a) for a in args)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode('gbk', errors='replace').decode('gbk'), flush=True)

def find_hwnd(cls):
    found = [0]
    def cb(w, _):
        if win32gui.IsWindowVisible(w) and win32gui.GetClassName(w) == cls and win32gui.GetParent(w) == 0:
            if win32gui.GetWindowText(w):
                found[0] = w
                return False
        return True
    win32gui.EnumWindows(cb, 0)
    return found[0]

def enum_all_children(hwnd, depth=0):
    """递归枚举所有子窗口"""
    results = []
    def cb(child, _):
        cls = win32gui.GetClassName(child)
        text = win32gui.GetWindowText(child)
        cid = win32gui.GetDlgCtrlID(child)
        style = win32gui.GetWindowLong(child, win32con.GWL_STYLE)
        results.append((depth, cls, cid, text[:40], hex(style)))
        # Recursive
        sub = enum_all_children(child, depth + 1)
        results.extend(sub)
        return True
    win32gui.EnumChildWindows(hwnd, cb, 0)
    return results

# Clean state
for _ in range(3):
    win32api.keybd_event(0x1B, 0, 0, 0); time.sleep(0.05)
    win32api.keybd_event(0x1B, 0, 2, 0); time.sleep(0.3)

# Launch Notepad
hwnd = find_hwnd('Notepad')
if not hwnd:
    os.startfile("notepad.exe")
    time.sleep(2)
    hwnd = find_hwnd('Notepad')
log(f"Notepad HWND={hwnd}")

# Type something
user32.SetForegroundWindow(hwnd); time.sleep(0.5)
for ch in "Hello diagnostic test":
    vk = ord(ch.upper()) if ch.isalpha() else ord(ch)
    sft = ch.isupper()
    if sft: win32api.keybd_event(0x10, 0, 0, 0); time.sleep(0.02)
    win32api.keybd_event(vk, 0, 0, 0); time.sleep(0.03)
    win32api.keybd_event(vk, 0, 2, 0); time.sleep(0.05)
    if sft: win32api.keybd_event(0x10, 0, 2, 0)
    time.sleep(0.03)
time.sleep(0.5)
log("Typed text")

# Alt+F → A to open Save As
user32.SetForegroundWindow(hwnd); time.sleep(0.3)
from agent.menu_handler import MenuHandler
h = MenuHandler()
h._press_key(0x12); time.sleep(0.5)
win32api.keybd_event(ord('F'), 0, 0, 0); time.sleep(0.05)
win32api.keybd_event(ord('F'), 0, 2, 0); time.sleep(1.0)
win32api.keybd_event(ord('A'), 0, 0, 0); time.sleep(0.05)
win32api.keybd_event(ord('A'), 0, 2, 0); time.sleep(2.5)

# Find save dialog
from agent.dialog_handler import find_all_dialogs
dlgs = [d for d in find_all_dialogs() if d.class_name == '#32770']
log(f"\n#32770 dialogs: {len(dlgs)}")

for d in dlgs:
    log(f"\n=== Dialog hwnd={d.hwnd} title={d.title} ===")

    # Method 1: EnumChildWindows
    log("--- EnumChildWindows ---")
    kids = enum_all_children(d.hwnd)
    if kids:
        for dep, cls, cid, txt, sty in kids:
            log(f"  {'  '*dep}cls={cls} id={cid} text={txt!r} style={sty}")
    else:
        log("  (no children via EnumChildWindows)")

    # Method 2: FindWindowEx direct
    log("--- FindWindowEx ---")
    for target_cls in ['Edit', 'ComboBox', 'ComboBoxEx32', 'Button', 'Static', 'DUIView', 'WorkerW', 'DirectUIHWND']:
        ctrl = win32gui.FindWindowEx(d.hwnd, 0, target_cls, None)
        if ctrl:
            cid = win32gui.GetDlgCtrlID(ctrl)
            txt = win32gui.GetWindowText(ctrl)
            log(f"  Found {target_cls}: hwnd={ctrl} id={cid} text={txt!r}")
        else:
            log(f"  No {target_cls}")

    # Method 3: GetDlgItem for common control IDs
    log("--- GetDlgItem ---")
    for cid in [1, 2, 0x47C, 0x480, 0x48C, 1152, 1148]:
        ctrl = win32gui.GetDlgItem(d.hwnd, cid)
        if ctrl:
            cls = win32gui.GetClassName(ctrl)
            txt = win32gui.GetWindowText(ctrl)
            log(f"  id=0x{cid:04X}({cid}): hwnd={ctrl} cls={cls} text={txt!r}")

    # Method 4: GetTopWindow (first child)
    log("--- GetTopWindow ---")
    top = win32gui.GetTopWindow(d.hwnd)
    if top:
        cls = win32gui.GetClassName(top)
        txt = win32gui.GetWindowText(top)
        log(f"  top child: hwnd={top} cls={cls} text={txt!r}")

# Final cleanup
for _ in range(5):
    win32api.keybd_event(0x1B, 0, 0, 0); time.sleep(0.05)
    win32api.keybd_event(0x1B, 0, 2, 0); time.sleep(0.3)

log("\n=== Done ===")
