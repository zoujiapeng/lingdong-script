"""通过 QQNT 给手机发消息。
使用键盘 Tab+Enter 触发登录（不依赖像素坐标），发消息。
"""
import os, sys, time, subprocess, logging, ctypes, win32con

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

import win32gui, win32process, psutil, pyautogui
import win32clipboard

pyautogui.FAILSAFE = False

QQ_EXE = r"C:\Program Files\Tencent\QQNT\QQ.exe"
t0 = time.time()

def log(msg):
    print(f"[{time.time()-t0:6.2f}s] {msg}", flush=True)

def find_qq():
    return win32gui.FindWindow("Chrome_WidgetWin_1", "QQ")

def get_qq_rect(hwnd):
    r = win32gui.GetWindowRect(hwnd)
    return r, r[2]-r[0], r[3]-r[1]

def bring_to_front(hwnd):
    fore_hwnd = win32gui.GetForegroundWindow()
    fore_tid = win32process.GetWindowThreadProcessId(fore_hwnd)[0]
    target_tid = win32process.GetWindowThreadProcessId(hwnd)[0]
    ctypes.windll.user32.AttachThreadInput(target_tid, fore_tid, True)
    win32gui.SetForegroundWindow(hwnd)
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.3)
    ctypes.windll.user32.AttachThreadInput(target_tid, fore_tid, False)

def click_rel(hwnd, rx, ry):
    r, w, h = get_qq_rect(hwnd)
    x = r[0] + int(rx)
    y = r[1] + int(ry)
    pyautogui.click(x, y)
    time.sleep(2)

def type_via_clipboard(text):
    win32clipboard.OpenClipboard(0)
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardText(text)
    win32clipboard.CloseClipboard()
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.5)

# ══════════════════════════════════
log("=== QQ 给手机发消息 ===")

# Step 1: Kill QQ
for p in psutil.process_iter(["pid", "name"]):
    try:
        if p.info["name"] in ("QQ.exe", "QQEX.exe"):
            p.kill()
    except:
        pass
time.sleep(2)
log("QQ killed")

# Step 2: Launch QQ
subprocess.Popen([QQ_EXE])
log("QQ launched")

# Step 3: Wait for login window
hwnd = None
deadline = time.time() + 30
while time.time() < deadline:
    hwnd = find_qq()
    if hwnd:
        r, w, h = get_qq_rect(hwnd)
        if w > 800:
            log(f"Login window: {w}x{h} at ({r[0]},{r[1]})")
            break
    time.sleep(1)
if not hwnd:
    log("FAIL: QQ window not found")
    sys.exit(1)

# Step 4: Login via keyboard (Tab -> Enter)
# This avoids DPI/coordinate issues
bring_to_front(hwnd)
time.sleep(1)
log("Logging in via Tab+Enter...")
for _ in range(10):
    pyautogui.press("tab")
    time.sleep(0.2)
pyautogui.press("enter")
time.sleep(1)
pyautogui.press("enter")  # extra Enter in case
log("Login keypresses sent")

# Step 5: Wait for login
time.sleep(5)

hwnd = find_qq()
if not hwnd:
    log("FAIL: QQ window lost after login")
    sys.exit(1)

r, w, h = get_qq_rect(hwnd)
log(f"After login: {w}x{h} at ({r[0]},{r[1]})")

# Force compact mode for reliable 我的手机 position
COMPACT_W, COMPACT_H = 320, 589
win32gui.SetWindowPos(hwnd, 0, max(0, r[0]), max(0, r[1]),
                      COMPACT_W, COMPACT_H, win32con.SWP_SHOWWINDOW)
time.sleep(1)
log(f"Compact: {w}x{h}" if (w, h) == (COMPACT_W, COMPACT_H) else f"Resized to {w}x{h}")

# Step 6: Click 我的手机
MY_PHONE_REL = (13, 506)
bring_to_front(hwnd)
time.sleep(1)
log("Clicking 我的手机...")
click_rel(hwnd, *MY_PHONE_REL)
time.sleep(3)

# Step 7: Click input area
sidebar_w = 40
input_rel_x = sidebar_w + (COMPACT_W - sidebar_w) // 2
input_rel_y = COMPACT_H - 45
click_rel(hwnd, input_rel_x, input_rel_y)
time.sleep(1)

# Step 8: Type and send message
msg = "你好，这是通过电脑QQ自动发送的测试消息！"
log(f"Sending: {msg}")
type_via_clipboard(msg)
pyautogui.hotkey("ctrl", "enter")
time.sleep(2)

log("=== 消息已发送！请检查手机QQ ===")
