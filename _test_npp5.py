"""
Quick step-by-step: test dialog handling on Notepad++ Preferences dialog.
"""
import time, sys, ctypes
sys.path.insert(0, 'python')

user32 = ctypes.windll.user32
WM_COMMAND = 0x0111

def sp(*args):
    line = ' '.join(str(a) for a in args)
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode('gbk', errors='replace').decode('gbk'))

# Find NPP
found = [0]
def enum_proc(hwnd, lParam):
    if user32.IsWindowVisible(hwnd):
        b = ctypes.create_unicode_buffer(512)
        user32.GetClassNameW(hwnd, b, 512)
        if b.value == 'Notepad++':
            found[0] = hwnd
            return False
    return True
EP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
user32.EnumWindows(EP(enum_proc), 0)
hwnd = found[0]
sp(f"NPP HWND: {hwnd}")

# Focus
user32.SetForegroundWindow(hwnd)
user32.BringWindowToTop(hwnd)
time.sleep(0.5)

from agent.dialog_handler import find_all_dialogs, handle_any_dialog

# 1. Handle any leftover dialog
sp("\n1. Handle leftover dialogs...")
for _ in range(3):
    r = handle_any_dialog()
    if r == "not_found":
        break
    sp(f"   {r}")
    time.sleep(0.3)

# 2. Try to open Preferences via PostMessage
sp("\n2. PostMessage WM_COMMAND 48011 (Preferences)...")
user32.PostMessageW(hwnd, WM_COMMAND, 48011, 0)
time.sleep(2.5)

d = find_all_dialogs()
pref_dialogs = [w for w in d if w.class_name == "#32770"]
sp(f"   #32770 dialogs: {len(pref_dialogs)}")
for w in pref_dialogs:
    sp(f"   hwnd={w.hwnd} title={w.title!r} type={w.dialog_type}")
    sp(f"   buttons: {[b.get('text','') for b in w.buttons_win32]}")

# 3. Try to open via SendMessage instead
sp("\n3. SendMessage WM_COMMAND 48011...")
user32.SendMessageW(hwnd, WM_COMMAND, 48011, 0)
time.sleep(2.5)

d = find_all_dialogs()
pref_dialogs = [w for w in d if w.class_name == "#32770"]
sp(f"   #32770 dialogs: {len(pref_dialogs)}")
for w in pref_dialogs:
    sp(f"   hwnd={w.hwnd} title={w.title!r} type={w.dialog_type}")
    sp(f"   buttons: {[b.get('text','') for b in w.buttons_win32]}")

# 4. Try keyboard navigation: Alt+O then P for Preferences
sp("\n4. Keyboard Alt+O then P (Settings > Preferences)...")
import win32api, win32con
win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
time.sleep(0.05)
win32api.keybd_event(ord('O'), 0, 0, 0)
time.sleep(0.05)
win32api.keybd_event(ord('O'), 0, win32con.KEYEVENTF_KEYUP, 0)
win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
time.sleep(1.5)
# Press P for Preferences
win32api.keybd_event(ord('P'), 0, 0, 0)
time.sleep(0.05)
win32api.keybd_event(ord('P'), 0, win32con.KEYEVENTF_KEYUP, 0)
time.sleep(2.5)

d = find_all_dialogs()
pref_dialogs = [w for w in d if w.class_name == "#32770"]
sp(f"   #32770 dialogs after keyboard: {len(pref_dialogs)}")
for w in pref_dialogs:
    sp(f"   hwnd={w.hwnd} title={w.title!r} type={w.dialog_type}")
    sp(f"   buttons: {[b.get('text','') for b in w.buttons_win32]}")

# 5. Try handle_any_dialog
sp("\n5. Handle dialog...")
if pref_dialogs:
    r = handle_any_dialog()
    sp(f"   handle result: {r}")
    time.sleep(1)
    d = find_all_dialogs()
    remaining = [w for w in d if w.class_name == "#32770"]
    sp(f"   Remaining: {len(remaining)}")

# 6. Test what Alt+O actually opens - enumerate visible menu
sp("\n6. Investigate Notepad++ menu structure...")
from agent.dialog_handler import DialogInfo

# List all top-level visible windows with Notepad++ PID
buff = ctypes.create_unicode_buffer(512)
user32.GetWindowTextW(hwnd, buff, 512)
title = buff.value
sp(f"   Main window: {title}")

# Check if there are any owned (modal) windows
def find_owned_windows(owner_hwnd):
    result = []
    def enum_proc(hwnd, lParam):
        if user32.IsWindowVisible(hwnd):
            if user32.GetWindow(hwnd, 4) == owner_hwnd:  # GW_OWNER
                result.append(hwnd)
        return True
    EP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
    user32.EnumWindows(EP(enum_proc), 0)
    return result

owned = find_owned_windows(hwnd)
sp(f"   Owned windows: {len(owned)}")
for ow in owned:
    user32.GetWindowTextW(ow, buff, 512)
    user32.GetClassNameW(ow, buff, 512)
    sp(f"   class={buff.value} title={buff.value}")

# 7. Check if the menu command IDs are correct
sp("\n7. Verify menu command IDs...")
menu = user32.GetMenu(hwnd)
if menu:
    count = user32.GetMenuItemCount(menu)
    sp(f"   Top-level menu items: {count}")
    for i in range(count):
        sub = user32.GetSubMenu(menu, i)
        if sub:
            sc = user32.GetMenuItemCount(sub)
            sp(f"   [{i}] submenu with {sc} items (IDs: ", end="")
            ids = []
            for j in range(min(sc, 5)):
                id_ = user32.GetMenuItemID(sub, j)
                ids.append(str(id_))
            sp(f"      {', '.join(ids)}{',...' if sc > 5 else ''}")


sp("\n=== Done ===")
