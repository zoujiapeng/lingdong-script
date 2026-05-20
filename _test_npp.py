"""Minimal Notepad++ menu interaction test - no features._all import."""
import time, sys, ctypes, os
sys.path.insert(0, 'python')

import psutil
import win32api, win32con

user32 = ctypes.windll.user32

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
                title = buff.value
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value in target_pids and 'Notepad++' in title:
                    found[0] = hwnd
                    return False
        return True
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
    user32.EnumWindows(EnumWindowsProc(enum_proc), 0)
    return found[0]

hwnd = find_npp_hwnd()
print(f"Target HWND: {hwnd}")

user32.SetForegroundWindow(hwnd)
user32.BringWindowToTop(hwnd)
time.sleep(0.5)

def press_key(vk):
    """Press and release a key."""
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.03)

def press_alt_key(char):
    """Press Alt+Char."""
    vk = ord(char.upper())
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    time.sleep(0.05)
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.08)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.5)

def type_text(text):
    """Type text using VkKeyScan."""
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

def take_screenshot(path):
    """Take screenshot using win32."""
    try:
        import PIL.ImageGrab
        img = PIL.ImageGrab.grab()
        img.save(path)
        return True
    except Exception as e:
        print(f"  Screenshot failed: {e}")
        return False

# First, type some text into Notepad++
print("1. Type text into Notepad++...")
type_text("def hello():\n    print('Hello Lobster!')\n    return 42\n\nclass Test:\n    pass\n")
time.sleep(0.5)

# Test: Take screenshot after typing
sc_path = "C:\\Users\\zou\\Desktop\\npp_test_after_type.png"
take_screenshot(sc_path)
print(f"   Screenshot saved to {sc_path}")

# Test keyboard menu navigation: Alt+V → F (Function List)
print("\n2. Open View > Function List via Alt+V then F...")
press_alt_key('V')
time.sleep(1.5)
press_key(ord('F'))
time.sleep(2.0)

sc_path2 = "C:\\Users\\zou\\Desktop\\npp_test_func_list.png"
take_screenshot(sc_path2)
print(f"   Screenshot saved to {sc_path2}")

# Check for any dialogs
print("\n3. Checking for dialogs...")
from agent.dialog_handler import find_all_dialogs
dialogs = find_all_dialogs()
real_dialogs = [d for d in dialogs if d.class_name not in ("Notepad++", "SciTEWindow", "Scintilla")]
print(f"   Total windows: {len(dialogs)}, non-Notepad++: {len(real_dialogs)}")
for d in real_dialogs:
    print(f"   DIALOG: {d.title!r} class={d.class_name} type={d.dialog_type}")

# Try opening Settings > Preferences
print("\n4. Open Settings (Alt+O)...")
press_alt_key('O')
time.sleep(1.5)
press_key(ord('P'))  # Try P for Preferences
time.sleep(2.0)

sc_path3 = "C:\\Users\\zou\\Desktop\\npp_test_settings.png"
take_screenshot(sc_path3)
print(f"   Screenshot saved to {sc_path3}")

# Check dialogs
dialogs = find_all_dialogs()
real_dialogs = [d for d in dialogs if d.class_name not in ("Notepad++", "SciTEWindow", "Scintilla")]
print(f"\n5. Dialogs after Settings: {len(real_dialogs)}")
for d in real_dialogs:
    print(f"   DIALOG: title={d.title!r} class={d.class_name} type={d.dialog_type}")
    print(f"       buttons_win32={d.buttons_win32}")

# Handle any dialog
from agent.dialog_handler import handle_any_dialog
result = handle_any_dialog()
print(f"\n6. handle_any_dialog result: {result!r}")

# Try Alt+T for Tools menu
print("\n7. Try Alt+T (Tools)...")
press_alt_key('T')
time.sleep(1.0)
press_key(win32con.VK_ESCAPE)  # Close menu
time.sleep(0.5)

# Try standard Windows Alt key for Notepad++ menus
print("\n8. Menu key tests with screenshots...")
for menu_key, menu_name in [('F', 'File'), ('E', 'Edit'), ('V', 'View'), ('S', 'Search'),
                             ('L', 'Language'), ('O', 'Format/Settings'), ('T', 'Tools')]:
    press_alt_key(menu_key)
    time.sleep(0.5)
    press_key(win32con.VK_ESCAPE)  # Close menu
    time.sleep(0.3)
    print(f"   Alt+{menu_key} ({menu_name}): OK")

print("\n=== All tests completed ===")
