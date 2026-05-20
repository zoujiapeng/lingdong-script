"""
Systematic test of Notepad++ menu operations and dialog handling.
"""
import time, sys, ctypes, win32api, win32con
sys.path.insert(0, 'python')

user32 = ctypes.windll.user32
WM_COMMAND = 0x0111

def sp(*args):
    try:
        print(' '.join(str(a) for a in args))
    except UnicodeEncodeError:
        print(' '.join(str(a) for a in args).encode('gbk', errors='replace').decode('gbk'))

def find_hwnd():
    found = [0]
    def enum_proc(hwnd, lParam):
        if user32.IsWindowVisible(hwnd):
            b = ctypes.create_unicode_buffer(512)
            user32.GetClassNameW(hwnd, b, 512)
            if b.value == 'Notepad++' and user32.GetParent(hwnd) == 0:
                user32.GetWindowTextW(hwnd, b, 512)
                if b.value:
                    found[0] = hwnd
                    return False
        return True
    EP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
    user32.EnumWindows(EP(enum_proc), 0)
    return found[0]

hwnd = find_hwnd()
sp(f"NPP HWND: {hwnd}")
user32.SetForegroundWindow(hwnd)
user32.BringWindowToTop(hwnd)
time.sleep(0.5)

from agent.dialog_handler import find_all_dialogs, handle_any_dialog

def check_dialogs(label):
    d = find_all_dialogs()
    dialogs = [w for w in d if w.class_name == '#32770']
    if dialogs:
        for w in dialogs:
            btns = [b.get('text', '') for b in w.buttons_win32]
            sp(f"  [{label}] DIALOG: {w.title!r} type={w.dialog_type} btns={btns}")
        return dialogs
    return []

def press_alt(char):
    vk = ord(char.upper())
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    time.sleep(0.07)
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.07)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.07)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(1.0)

def press_key(char):
    vk = ord(char.upper()) if isinstance(char, str) else char
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.3)

def press_down(n=1):
    for _ in range(n):
        win32api.keybd_event(win32con.VK_DOWN, 0, 0, 0)
        time.sleep(0.1)
        win32api.keybd_event(win32con.VK_DOWN, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.3)

def press_enter():
    win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(1.0)

def esc():
    win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.5)

# Empty initial dialogs
sp("Clean up initial dialogs...")
for _ in range(5):
    r = handle_any_dialog()
    if r == "not_found":
        break
    time.sleep(0.3)

sp("\n" + "=" * 60)
sp("TEST: Notepad++ Menu Operations")
sp("=" * 60)

# TEST: File > New (Alt+F, N)
sp("\n[1] File > New (Alt+F, N)")
press_alt('F')
press_key('N')
time.sleep(1)
check_dialogs("new file")

# TEST: File > Open (Alt+F, O)
sp("\n[2] File > Open (Alt+F, O)")
press_alt('F')
press_key('O')
time.sleep(1.5)
dialogs = check_dialogs("file open")
# File Open is an IFileDialog (modern) - should NOT be detected as #32770
if dialogs:
    # Close it
    esc()
    esc()

# TEST: View > Function List (Alt+V, F)
sp("\n[3] View > Function List (Alt+V, F)")
press_alt('V')
time.sleep(0.3)
press_key('F')
time.sleep(2)
check_dialogs("func list")

# TEST: View > Show Symbol (Alt+V, S)
sp("\n[4] View > Show Symbol (Alt+V, S)")
press_alt('V')
time.sleep(0.3)
press_key('S')
time.sleep(1.5)
check_dialogs("show symbol")

# TEST: Try Settings menu (Alt+O in some versions)
sp("\n[5] Settings > Preferences attempt (Alt+O)")
press_alt('O')
time.sleep(0.5)
press_key('P')
time.sleep(2)
check_dialogs("settings prefs")

# TEST: Try Tools menu
sp("\n[6] Tools menu (Alt+T)")
press_alt('T')
time.sleep(0.5)
press_key('P')
time.sleep(1.5)
check_dialogs("tools menu")

# TEST: Try the Language menu
sp("\n[7] Language menu (Alt+L)")
press_alt('L')
time.sleep(0.5)
press_key('P')  # P for Python
time.sleep(1)
check_dialogs("language")

# TEST: Try Encoding menu
sp("\n[8] Encoding menu (Alt+G or other)")
for key in ['G', 'M', 'N', 'R', 'D', 'A']:
    press_alt(key)
    time.sleep(0.5)
    # Check if menu opened by looking for child menu windows
    d = find_all_dialogs()
    # Sometimes menu popups create #32768 windows (menus)
    non_32770 = [w for w in d if w.class_name not in ('#32770', 'Notepad++', 'SciTEWindow', 'Scintilla')]
    if [w for w in d if w.class_name != 'Notepad++']:
        sp(f"  Alt+{key} triggered something")
        esc()
        break
    esc()

# Final test: handle any remaining dialogs
sp("\n[9] Cleanup remaining dialogs...")
for _ in range(5):
    r = handle_any_dialog()
    if r == "not_found":
        break
    sp(f"  handle: {r}")
    time.sleep(0.3)

# Try handle_any_dialog on main window
sp("\n[10] Verify handle_any_dialog on NPP main window...")
d = find_all_dialogs()
sp(f"  All windows: {len(d)}")
for w in d:
    sp(f"  {w.class_name} {w.title!r} type={w.dialog_type}")

sp("\n=== Test Complete ===")
