"""
Find correct main Notepad++ HWND and test Preferences dialog.
"""
import time, sys, ctypes
sys.path.insert(0, 'python')

user32 = ctypes.windll.user32
WM_COMMAND = 0x0111

def sp(*args):
    try:
        print(' '.join(str(a) for a in args))
    except UnicodeEncodeError:
        print(' '.join(str(a) for a in args).encode('gbk', errors='replace').decode('gbk'))

# Find ALL Notepad++ windows with details
found_windows = []
def enum_proc(hwnd, lParam):
    if user32.IsWindowVisible(hwnd):
        b = ctypes.create_unicode_buffer(512)
        user32.GetClassNameW(hwnd, b, 512)
        if b.value == 'Notepad++':
            user32.GetWindowTextW(hwnd, b, 512)
            title = b.value
            parent = user32.GetParent(hwnd)
            style = user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE
            exstyle = user32.GetWindowLongW(hwnd, -20)
            found_windows.append((hwnd, title, parent, style, exstyle))
    return True

EP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
user32.EnumWindows(EP(enum_proc), 0)

sp(f"NPP windows: {len(found_windows)}")
for hwnd, title, parent, style, exstyle in found_windows:
    WS_CAPTION = 0x00C00000
    WS_THICKFRAME = 0x00040000
    WS_SYSMENU = 0x00080000
    has_caption = (style & WS_CAPTION) == WS_CAPTION
    has_thickframe = bool(style & WS_THICKFRAME)
    has_sysmenu = bool(style & WS_SYSMENU)
    sp(f"  hwnd={hwnd} parent={parent} title={title!r}")
    sp(f"    style=0x{style:08x} caption={has_caption} thick={has_thickframe} sysmenu={has_sysmenu}")

main = [(h,t,p,s,es) for h,t,p,s,es in found_windows if p == 0 and t != '']
sp(f"\nMain windows: {len(main)}")
if not main:
    sp("ERROR: No main window found!")
    sys.exit(1)

hwnd = main[0][0]
title = main[0][1]
sp(f"Using HWND: {hwnd} title={title!r}")

# Focus
user32.SetForegroundWindow(hwnd)
user32.BringWindowToTop(hwnd)
time.sleep(0.5)

# Verify menu exists
menu = user32.GetMenu(hwnd)
sp(f"Menu handle: {menu}")

# Test PostMessage
sp("\nPostMessage WM_COMMAND 48011 (Preferences)...")
user32.PostMessageW(hwnd, WM_COMMAND, 48011, 0)
time.sleep(2.5)

from agent.dialog_handler import find_all_dialogs, handle_any_dialog
d = find_all_dialogs()
dialogs = [w for w in d if w.class_name == '#32770']
sp(f"#32770 dialogs: {len(dialogs)}")
for w in dialogs:
    sp(f"  hwnd={w.hwnd} title={w.title!r} type={w.dialog_type}")
    btn_texts = [b.get('text', '') for b in w.buttons_win32]
    sp(f"  buttons: {btn_texts}")

# Test SendMessage
sp("\nSendMessage WM_COMMAND 48011...")
user32.SendMessageW(hwnd, WM_COMMAND, 48011, 0)
time.sleep(2.5)

d = find_all_dialogs()
dialogs = [w for w in d if w.class_name == '#32770']
sp(f"#32770 dialogs: {len(dialogs)}")
for w in dialogs:
    sp(f"  hwnd={w.hwnd} title={w.title!r} type={w.dialog_type}")
    btn_texts = [b.get('text', '') for b in w.buttons_win32]
    sp(f"  buttons: {btn_texts}")

# Try all possible Preferences command IDs
sp("\nTry multiple command IDs for Preferences...")
for cmd_id in [48011, 48001, 47011, 46001]:
    sp(f"  Trying cmd_id={cmd_id}...")
    user32.SendMessageW(hwnd, WM_COMMAND, cmd_id, 0)
    time.sleep(1.5)
    d = find_all_dialogs()
    dialogs = [w for w in d if w.class_name == '#32770']
    if dialogs:
        sp(f"    GOT DIALOG! With cmd={cmd_id}")
        for w in dialogs:
            btn_texts = [b.get('text', '') for b in w.buttons_win32]
            sp(f"    {w.title!r} type={w.dialog_type} buttons={btn_texts}")
            sp(f"    class={w.class_name} hwnd={w.hwnd}")
        handle_any_dialog()
        time.sleep(0.5)
        break

# Try keyboard menu navigation properly
sp("\nKeyboard Alt+F to open File menu...")
import win32api, win32con
win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
time.sleep(0.1)
win32api.keybd_event(ord('F'), 0, 0, 0)
time.sleep(0.1)
win32api.keybd_event(ord('F'), 0, win32con.KEYEVENTF_KEYUP, 0)
win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
time.sleep(2)
# Check if File menu opened (it's a popup menu, not a dialog)
sp("Alt+F done")

# Now try Alt+T (Tools/Settings in some NPP versions)
sp("\nAlt+T (Tools/Settings)...")
win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
time.sleep(0.1)
win32api.keybd_event(ord('T'), 0, 0, 0)
time.sleep(0.1)
win32api.keybd_event(ord('T'), 0, win32con.KEYEVENTF_KEYUP, 0)
win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
time.sleep(1.5)
sp("Alt+T done - checking for dialogs...")
d = find_all_dialogs()
dialogs = [w for w in d if w.class_name == '#32770']
sp(f"#32770 dialogs: {len(dialogs)}")
for w in dialogs:
    btn_texts = [b.get('text', '') for b in w.buttons_win32]
    sp(f"  {w.title!r} type={w.dialog_type} buttons={btn_texts}")

# Escape to close any menu
win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
time.sleep(0.1)
win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)
time.sleep(0.5)

sp("\nDone!")
