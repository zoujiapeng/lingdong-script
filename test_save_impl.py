"""Test: WM_SETTEXT filename + BM_CLICK Save on Notepad save dialog."""
import win32gui, win32con, time, os, subprocess

# 1. Start Notepad
proc = subprocess.Popen(['notepad.exe'])
time.sleep(1.5)

# Find Notepad window
notepad = None
def enum_win(hwnd, _):
    global notepad
    if win32gui.GetClassName(hwnd) == 'Notepad' and win32gui.IsWindowVisible(hwnd):
        notepad = hwnd
        return False
    return True
win32gui.EnumWindows(enum_win, 0)
print(f'Notepad: {notepad}')

# Type text
for ch in 'HelloSaveDialog':
    win32gui.PostMessage(notepad, win32con.WM_CHAR, ord(ch), 0)
time.sleep(0.3)

# Trigger Save (Ctrl+S)
win32gui.SendMessage(notepad, win32con.WM_COMMAND, 3, 0)
time.sleep(2)

# Find save dialog
save_hwnd = None
def enum_dlg(hwnd, _):
    global save_hwnd
    if win32gui.GetClassName(hwnd) == '#32770' and '另存为' in win32gui.GetWindowText(hwnd):
        save_hwnd = hwnd
        return False
    return True
win32gui.EnumWindows(enum_dlg, 0)
print(f'Save dialog: {save_hwnd}')

# Enumerate children
children = []
def enum_child(hwnd, _):
    try:
        children.append((hwnd, win32gui.GetClassName(hwnd), win32gui.GetWindowText(hwnd)))
    except:
        pass
    return True
win32gui.EnumChildWindows(save_hwnd, enum_child, 0)
print(f'Children ({len(children)}):')
for h, cls, txt in children:
    print(f'  {h} {cls} [{txt[:40]}]')

# Find ComboBox (filename control) and its Edit child
combos = [h for h, cls, _ in children if cls == 'ComboBox']
print(f'\nComboBoxes: {combos}')

if combos:
    cb = combos[0]
    # Get Edit children of ComboBox
    edit_kids = []
    def enum_edit(h, _):
        edit_kids.append(h)
        return True
    win32gui.EnumChildWindows(cb, enum_edit, 0)
    print(f'ComboBox Edit children: {edit_kids}')
    for eh in edit_kids:
        print(f'  Edit {eh} cls={win32gui.GetClassName(eh)}')

    # Try clipboard + WM_PASTE (most reliable cross-process)
    import win32clipboard
    win32clipboard.OpenClipboard(0)
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardText('lingdong_now.txt')
    win32clipboard.CloseClipboard()

    target = edit_kids[0] if edit_kids else cb
    print(f'Pasting to hwnd={target}')
    win32gui.SendMessage(target, win32con.WM_PASTE, 0, 0)
    time.sleep(0.5)

    # Also try WM_SETTEXT
    result_st = win32gui.SendMessage(cb, win32con.WM_SETTEXT, 0, 'lingdong_set.txt')
    print(f'WM_SETTEXT result: {result_st}')

# Find Save button (类别: Button, 文本含 "保存")
btns = [h for h, cls, txt in children if cls == 'Button' and '保存' in txt]
print(f'\nSave buttons: {btns}')
for btn in btns:
    print(f'  hwnd={btn} text=[{win32gui.GetWindowText(btn)}]')

# Click Save
clicked = False
for btn in btns:
    win32gui.SendMessage(btn, win32con.BM_CLICK, 0, 0)
    clicked = True
    print(f'Clicked {btn}')
    break

if not clicked:
    print('No Save btn, sending WM_COMMAND IDOK')
    win32gui.PostMessage(save_hwnd, win32con.WM_COMMAND, 1, 0)

time.sleep(2)

# Check result
path1 = os.path.expanduser('~/Desktop/lingdong_now.txt')
path2 = os.path.expanduser('~/Desktop/lingdong_set.txt')
print(f'\nResult:')
print(f'  lingdong_now.txt exists: {os.path.exists(path1)}')
print(f'  lingdong_set.txt exists: {os.path.exists(path2)}')
print(f'  Dialog still visible: {win32gui.IsWindowVisible(save_hwnd)}')

# List .txt on desktop
desktop = os.path.expanduser('~/Desktop')
txts = [f for f in os.listdir(desktop) if f.endswith('.txt')]
print(f'  Desktop .txt files: {txts}')

# Cleanup
time.sleep(1)
win32gui.PostMessage(notepad, win32con.WM_CLOSE, 0, 0)
time.sleep(0.5)
# Answer "Don't Save" if close prompt appears
dlg2 = win32gui.FindWindow('#32770', '记事本')
if dlg2:
    win32gui.PostMessage(dlg2, win32con.WM_COMMAND, 7, 0)  # 7 = "不保存"
time.sleep(1)
for p in [proc]:
    try: p.kill()
    except: pass
