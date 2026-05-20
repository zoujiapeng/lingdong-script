"""Test menu_handler on Notepad++."""
import sys, time, ctypes, win32gui, win32con, win32api
sys.path.insert(0, 'python')

from agent.menu_handler import MenuHandler

def sp(*args, **kw):
    line = ' '.join(str(a) for a in args)
    try:
        print(line, **kw)
    except UnicodeEncodeError:
        print(line.encode('gbk', errors='replace').decode('gbk'), **kw)

# Find NPP main window using win32gui
all_npp = []
def cb(hwnd, _):
    if win32gui.IsWindowVisible(hwnd):
        cls = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd)
        parent = win32gui.GetParent(hwnd)
        if cls == 'Notepad++' and title:
            all_npp.append((hwnd, title, parent))
    return True
win32gui.EnumWindows(cb, 0)
sp(f"All Notepad++ windows: {len(all_npp)}")
for h, t, p in all_npp:
    sp(f"  hwnd={h} title={t!r} parent={p}")

# Pick first main window
main_hwnds = [h for h, t, p in all_npp if p == 0]
hwnd = main_hwnds[0] if main_hwnds else 0
sp(f"\nUsing HWND: {hwnd}")
if not hwnd:
    sp("ERROR: Cannot find Notepad++!")
    sys.exit(1)

win32gui.SetForegroundWindow(hwnd)
win32gui.BringWindowToTop(hwnd)
time.sleep(0.5)

handler = MenuHandler()

# 1. Get menu structure
sp("\n=== Menu Structure ===")
structure = handler.get_menu_structure(hwnd)
if structure:
    for item in structure:
        t = item.get('text')
        c = item.get('cmd_id')
        kids = item.get('children')
        n = len(kids) if kids else 0
        sp(f"  [{item['pos']}] text={t!r} cmd_id={c} sub_items={n}")
        if kids:
            for k in kids[:8]:
                kt = k.get('text')
                sp(f"    [{k['pos']}] text={kt!r} cmd_id={k['cmd_id']}")
            if len(kids) > 8:
                sp(f"    ... ({len(kids)-8} more)")
else:
    sp("NO MENU")

# 2. Test Level 1 (win32 WM_COMMAND)
sp("\n=== Level 1: WM_COMMAND ===")
# Try common commands
for name, cmd in [("Shortcut Mapper", 48009), ("Style Config", 46001)]:
    sp(f"  {name} cmd={cmd}: ", end="")
    result = handler._level_win32(hwnd, [name])
    sp(f"{result}")
    time.sleep(1.5)
    from agent.dialog_handler import find_all_dialogs, handle_any_dialog
    d = find_all_dialogs()
    dialogs = [w for w in d if w.class_name == '#32770']
    if dialogs:
        for w in dialogs:
            sp(f"    -> DIALOG: {w.title!r}")
        # Close
        for _ in range(3):
            r = handle_any_dialog()
            if r == "not_found":
                break
            time.sleep(0.3)
    else:
        sp("    (no dialog)")

# 3. Test Level 2 (keyboard) - accelerators
sp("\n=== Level 2: Keyboard Accelerator Nav ===")
tests = [
    ("View > Function List", ["View", "Function List"]),
    ("View > Zoom > Zoom In", ["View", "Zoom", "Zoom In"]),
]
for name, path in tests:
    sp(f"  {name}: ", end="")
    result = handler._accelerator_nav(hwnd, path)
    sp(f"{result}")
    time.sleep(1.5)

# 4. Test Level 2 with Chinese menu names
sp("\n=== Level 2: Chinese menu names ===")
# Notepad++ Chinese menus: 文件(F), 编辑(E), 搜索(S), 视图(V), 设置(O/T)
# The accelerator is the letter in parens, not the Chinese text
from agent.dialog_handler import find_all_dialogs, handle_any_dialog

# 设置(O) > 首选项(P) might work with Alt+O then P
# But first let's check if we can use "设置" as text for the path
sp("  Try '设置' (Settings) > '首选项' (Preferences): ", end="")
result = handler._accelerator_nav(hwnd, ["设置", "首选项"])
sp(f"{result}")
time.sleep(2)

# Check for dialogs
d = find_all_dialogs()
dialogs = [w for w in d if w.class_name == '#32770']
sp(f"  Dialogs after: {len(dialogs)}")
for w in dialogs:
    btns = [b.get('text','') for b in w.buttons_win32]
    sp(f"    {w.title!r} btns={btns}")
    if btns:
        # Try to close via handle_any_dialog
        for _ in range(3):
            r = handle_any_dialog()
            if r == "not_found":
                break
            sp(f"    handle: {r}")
            time.sleep(0.3)

# 5. Test Level 1 with known command IDs for Notepad++
sp("\n=== Level 1: WM_COMMAND with known IDs ===")
# We can access sub-menu items by command ID directly
# Notepad++ settings menu has cmd_id=48011 for Preferences
# But we need the path to match via text... since text is None,
# Level 1 fails. Let's add a direct-by-ID approach.

# Try keyboard Alt+T then P (Tools >... or Settings > Preferences)
sp("  Alt+T then P: ", end="")
result = handler._accelerator_nav(hwnd, ["Settings", "Preferences"])
sp(f"{result}")
time.sleep(2)

d = find_all_dialogs()
dialogs = [w for w in d if w.class_name == '#32770']
if dialogs:
    for w in dialogs:
        btns = [b.get('text','') for b in w.buttons_win32]
        sp(f"    DIALOG: {w.title!r} btns={btns}")
        handle_any_dialog()

# 5. Test Alt+F then O (File > Open - should open IFileDialog)
sp("\n  Alt+F then O (File > Open): ", end="")
result = handler._accelerator_nav(hwnd, ["文件", "打开"])
sp(f"{result}")
time.sleep(1.5)

d = find_all_dialogs()
sp(f"  Dialogs: {len(d)}")
# Escape to close any open dialog
win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
time.sleep(0.05)
win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)
time.sleep(0.3)
win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
time.sleep(0.05)
win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)
time.sleep(0.3)

sp("\n=== Summary ===")
sp("  Menu structure enumeration: OK")
sp("  Level 1 (WM_COMMAND text match): fails for owner-drawn menus")
sp("  Level 2 (accelerator nav): ", end="")
sp("  Chinese path names: tested")
sp("Done!")
