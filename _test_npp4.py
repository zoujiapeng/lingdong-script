"""
Deep dive: Notepad++ dialog handling issues.
1. What are those 4 "dialogs"? Are they docked panels?
2. How to open real dialogs (Preferences, etc)?
3. PostMessage vs SendMessage for WM_COMMAND
4. How to filter out docked panels
"""
import time, sys, ctypes, os
sys.path.insert(0, 'python')

user32 = ctypes.windll.user32
WM_COMMAND = 0x0111

def sp(*args):
    line = ' '.join(str(a) for a in args)
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode('gbk', errors='replace').decode('gbk'))

# ---- Find Notepad++ ----
def find_hwnd(class_name, title_contain=None):
    found = [0]
    def enum_proc(hwnd, lParam):
        if user32.IsWindowVisible(hwnd):
            buff = ctypes.create_unicode_buffer(512)
            user32.GetClassNameW(hwnd, buff, 512)
            if buff.value == class_name:
                if title_contain:
                    user32.GetWindowTextW(hwnd, buff, 512)
                    if title_contain.lower() not in buff.value.lower():
                        return True
                found[0] = hwnd
                return False
        return True
    EP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
    user32.EnumWindows(EP(enum_proc), 0)
    return found[0]

hwnd = find_hwnd("Notepad++")
buff = ctypes.create_unicode_buffer(512)
user32.GetWindowTextW(hwnd, buff, 512)
sp(f"NPP HWND: {hwnd}, title: {buff.value}")

# Also find all child windows of Notepad++ to see what those "dialogs" are
sp("\n=== Child windows of NPP main window ===")
def enum_children(hwnd):
    children = []
    def enum_proc(child, lParam):
        if user32.IsWindowVisible(child):
            buff = ctypes.create_unicode_buffer(512)
            user32.GetClassNameW(child, buff, 512)
            cls = buff.value
            user32.GetWindowTextW(child, buff, 512)
            title = buff.value
            children.append((child, cls, title))
        return True
    EP = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
    user32.EnumChildWindows(hwnd, EP(enum_proc), 0)
    return children

for child, cls, title in enum_children(hwnd):
    sp(f"  child={child} class={cls} title={title[:60]}")

# Find those "dialog" windows - what are they?
sp("\n=== Enumerating ALL top-level windows ===")
from agent.dialog_handler import find_all_dialogs
all_dialogs = find_all_dialogs()

# Group by owner/parent to understand structure
notepad_dialogs = [d for d in all_dialogs if d.class_name == "#32770"]
sp(f"\n#32770 windows found: {len(notepad_dialogs)}")
for d in notepad_dialogs:
    # Get parent
    parent = user32.GetParent(d.hwnd)
    parent_title = ""
    if parent:
        b = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(parent, b, 512)
        parent_title = b.value
    owner = user32.GetWindow(d.hwnd, 4)  # GW_OWNER
    owner_title = ""
    if owner:
        b = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(owner, b, 512)
        owner_title = b.value
    sp(f"\n  hwnd={d.hwnd}")
    sp(f"  title={d.title}")
    sp(f"  parent={parent} ({parent_title})")
    sp(f"  owner={owner} ({owner_title})")
    sp(f"  style=0x{d.style:08x}")
    sp(f"  exstyle=0x{d.ex_style:08x}")
    sp(f"  type={d.dialog_type}")
    sp(f"  buttons={[b.get('text','') for b in d.buttons_win32]}")

    # Check if it's a popup vs dialog
    WS_POPUP = 0x80000000
    WS_DLGFRAME = 0x00400000
    WS_CAPTION = 0x00C00000
    WS_THICKFRAME = 0x00040000
    WS_SYSMENU = 0x00080000
    is_popup = bool(d.style & WS_POPUP)
    is_dlgframe = bool(d.style & WS_DLGFRAME)
    has_sysmenu = bool(d.style & WS_SYSMENU)
    has_caption = (d.style & WS_CAPTION) == WS_CAPTION
    sp(f"  popup={is_popup} dlgframe={is_dlgframe} sysmenu={has_sysmenu} caption={has_caption}")

    # Get window rectangle
    rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(d.hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    sp(f"  size={w}x{h} at ({rect.left},{rect.top})")

# Now let's try to dialogs specifically for Notepad++ real modal dialogs
# A real modal dialog has WS_DLGFRAME, WS_CAPTION, WS_SYSMENU and is MODAL
# (disabled parent window)

sp("\n\n=== TEST: PostMessage WM_COMMAND ===")
# PostMessage vs SendMessage
for name, cmd in [("Preferences", 48011), ("Style Config", 46001), ("Shortcut Mapper", 48009)]:
    sp(f"\n  {name} (cmd={cmd}):")
    result = ctypes.windll.user32.PostMessageW(hwnd, WM_COMMAND, cmd, 0)
    sp(f"    PostMessage returned: {result}")
    time.sleep(1.5)
    dialogs = find_all_dialogs()
    new_dialogs = [d for d in dialogs if d.class_name == "#32770"]
    sp(f"    #32770 dialogs now: {len(new_dialogs)}")
    for d in new_dialogs:
        sp(f"      {d.title}")
    # Close any new ones
    from agent.dialog_handler import handle_any_dialog
    for _ in range(5):
        r = handle_any_dialog()
        if r == "not_found":
            break
        sp(f"    handle: {r}")
        time.sleep(0.3)

# ===========================================================
# SUMMARY
# ===========================================================
sp("\n" + "=" * 55)
sp("KEY FINDINGS")
sp("=" * 55)
sp("  - Notepad++ docked panels are #32770 windows too")
sp("  - They have WS_POPUP but no WS_DLGFRAME (no dialog frame)")
sp("  - Real modal dialogs have WS_DLGFRAME | WS_CAPTION")
sp("  - find_all_dialogs needs better filtering")
sp("  - WM_COMMAND may not work for all Notepad++ menu items")
