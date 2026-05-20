"""
Test Notepad++ dialog handling + menu interaction.
No features._all import (avoid hangs).
"""
import time, sys, ctypes, os
sys.path.insert(0, 'python')

import psutil
import win32api, win32con

user32 = ctypes.windll.user32

def safe_print(*args, **kwargs):
    """Print with GBK-safe encoding."""
    line = ' '.join(str(a) for a in args)
    try:
        line.encode('gbk')
        print(line, **kwargs)
    except UnicodeEncodeError:
        print(line.encode('gbk', errors='replace').decode('gbk'), **kwargs)

target_pids = [p.info['pid'] for p in psutil.process_iter(['pid','name'])
               if 'notepad++' in p.info['name'].lower()]

def find_npp_hwnd():
    found = [0]
    def enum_proc(hwnd, lParam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value in target_pids:
                    found[0] = hwnd
                    return False
        return True
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
    user32.EnumWindows(EnumWindowsProc(enum_proc), 0)
    return found[0]

hwnd = find_npp_hwnd()
safe_print(f"Target HWND: {hwnd}")

user32.SetForegroundWindow(hwnd)
user32.BringWindowToTop(hwnd)
time.sleep(0.5)

WM_COMMAND = 0x0111

def press_key(vk):
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.03)

def press_alt_key(char):
    vk = ord(char.upper())
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.08)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.5)

def press_ctrl_key(char):
    """Press Ctrl+Char combo."""
    vk = ord(char.lower())
    win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)
    win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.3)

def type_text(text):
    for ch in text:
        if ch == '\n':
            press_key(win32con.VK_RETURN)
            continue
        vk = win32api.VkKeyScanEx(ch, 0)
        vk_code = vk & 0xFF
        shift = (vk >> 8) & 0xFF
        if shift:
            win32api.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
        win32api.keybd_event(vk_code, 0, 0, 0)
        time.sleep(0.01)
        win32api.keybd_event(vk_code, 0, win32con.KEYEVENTF_KEYUP, 0)
        if shift:
            win32api.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.01)

def find_dialogs():
    """Find real dialog windows (not main windows)."""
    from agent.dialog_handler import find_all_dialogs
    dialogs = find_all_dialogs()
    # Filter out main application windows
    real = [d for d in dialogs if d.class_name not in (
        "Notepad++", "SciTEWindow", "Scintilla", "msctls_statusbar32",
        "tooltips_class32", "SysTabControl32"
    )]
    return real

# === TEST 1: Hotkey via win32 (Ctrl+Shift+N = new file) ===
safe_print("\n=== TEST 1: Ctrl+Shift+N (new file) ===")
win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
win32api.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
win32api.keybd_event(ord('N'), 0, 0, 0)
time.sleep(0.05)
win32api.keybd_event(ord('N'), 0, win32con.KEYEVENTF_KEYUP, 0)
win32api.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)
win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
time.sleep(1)
safe_print("OK")

# === TEST 2: Type code ===
safe_print("\n=== TEST 2: Type Python code ===")
type_text("# Notepad++ test\nimport sys\n\ndef test_func():\n    pass\n\nclass App:\n    def run(self):\n        self.test()\n")
time.sleep(0.5)
safe_print("OK")

# === TEST 3: Open Function List (Alt+V then F) ===
safe_print("\n=== TEST 3: Open Function List (Alt+V then F) ===")
press_alt_key('V')
time.sleep(0.5)
press_key(ord('F'))
time.sleep(1.5)

dialogs = find_dialogs()
safe_print(f"  Dialogs found: {len(dialogs)}")
for d in dialogs:
    safe_print(f"    class={d.class_name} type={d.dialog_type} buttons={len(d.buttons_win32)}")

if dialogs:
    safe_print("  Closing dialogs with handle_any_dialog...")
    from agent.dialog_handler import handle_any_dialog
    for _ in range(len(dialogs)):
        r = handle_any_dialog()
        safe_print(f"    result: {r}")
        time.sleep(0.3)

# === TEST 4: Open Preferences (WM_COMMAND 48011) ===
safe_print("\n=== TEST 4: Open Preferences (WM_COMMAND 48011) ===")
user32.SendMessageW(hwnd, WM_COMMAND, 48011, 0)
time.sleep(2)

dialogs = find_dialogs()
safe_print(f"  Dialogs: {len(dialogs)}")
for d in dialogs:
    safe_print(f"    title={d.title} class={d.class_name} buttons={len(d.buttons_win32)}")
    safe_print(f"    button_names: {[b.get('text','') for b in d.buttons_win32]}")
    # Check UIA buttons
    try:
        uia_btns = d.all_buttons()
        safe_print(f"    UIA buttons: {[b.get('text','') for b in uia_btns]}")
    except Exception as e:
        safe_print(f"    UIA scan: {e}")

# Try to handle the Preferences dialog
if dialogs:
    safe_print("\n  Attempting to handle Preferences dialog...")
    from agent.dialog_handler import handle_any_dialog
    r = handle_any_dialog()
    safe_print(f"  handle_any_dialog: {r}")
    time.sleep(1)
    remaining = find_dialogs()
    safe_print(f"  Remaining: {len(remaining)}")

# === TEST 5: Test shortcut mapper (WM_COMMAND 48009) ===
safe_print("\n=== TEST 5: Open Shortcut Mapper (WM_COMMAND 48009) ===")
user32.SendMessageW(hwnd, WM_COMMAND, 48009, 0)
time.sleep(1.5)

dialogs = find_dialogs()
safe_print(f"  Dialogs: {len(dialogs)}")
for d in dialogs:
    safe_print(f"    title={d.title} buttons={[b.get('text','') for b in d.buttons_win32]}")

if dialogs:
    from agent.dialog_handler import handle_any_dialog
    r = handle_any_dialog()
    safe_print(f"  handle result: {r}")

# === TEST 6: Test style config (WM_COMMAND 46001) ===
safe_print("\n=== TEST 6: Open Style Config (WM_COMMAND 46001) ===")
user32.SendMessageW(hwnd, WM_COMMAND, 46001, 0)
time.sleep(1.5)

dialogs = find_dialogs()
safe_print(f"  Dialogs: {len(dialogs)}")
for d in dialogs:
    safe_print(f"    title={d.title} buttons={[b.get('text','') for b in d.buttons_win32]}")

if dialogs:
    from agent.dialog_handler import handle_any_dialog
    r = handle_any_dialog()
    safe_print(f"  handle result: {r}")

# === TEST 7: Function List docked panel ===
safe_print("\n=== TEST 7: Toggle Function List (Alt+V then F) ===")
press_alt_key('V')
time.sleep(0.5)
press_key(ord('F'))
time.sleep(2)
dialogs = find_dialogs()
safe_print(f"  Dialogs after Function List toggle: {len(dialogs)}")
# Function list is a dockable panel, not a dialog - should be 0 real dialogs

# === TEST 8: Summary of dialog_handler capabilities for Notepad++ ===
safe_print("\n=== TEST RESULTS ===")
safe_print(f"  All {len(dialogs)} tests completed")
safe_print(f"  Last dialog check: {len(dialogs)} dialogs remaining")

# Take final screenshot
safe_print("\n  Taking final screenshot...")
try:
    import PIL.ImageGrab
    PIL.ImageGrab.grab().save("C:\\Users\\zou\\Desktop\\npp_final.png")
    safe_print("  Saved to Desktop\\npp_final.png")
except Exception as e:
    safe_print(f"  Screenshot error: {e}")

safe_print("\nDone!")
