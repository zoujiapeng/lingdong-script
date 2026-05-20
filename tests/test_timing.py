"""Measure end-to-end time: Notepad + Notepad++ save files to desktop via background monitor."""
import os, sys, time, subprocess, logging, glob

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
logging.getLogger("comtypes").setLevel(logging.WARNING)
logging.getLogger("agent.dialog_handler").setLevel(logging.DEBUG)  # suppress noisy dialog logs

from agent.dialog_handler import start_dialog_monitor, stop_dialog_monitor
import win32gui, win32con, win32process
import ctypes

desktop = os.path.expanduser("~/Desktop")
t0 = time.time()

def log(msg):
    print(f"[{time.time()-t0:6.2f}s] {msg}")

# Clean start — kill any lingering Notepad/Notepad++ processes
for exe in ("notepad", "notepad++"):
    try:
        subprocess.run(["taskkill", "/f", "/im", f"{exe}.exe"], capture_output=True, timeout=5)
    except: pass
for f in glob.glob(os.path.join(desktop, "lingdong_*.txt")):
    try: os.unlink(f)
    except: pass
log("Cleaned up")

# Start monitor
start_dialog_monitor(save_dir=desktop)
log("Monitor started")

# ── Notepad ──
t_np_start = time.time()
proc1 = subprocess.Popen(["notepad.exe"])
time.sleep(1.5)

def find_window(kw):
    r = []
    def e(h, _):
        if win32gui.IsWindowVisible(h) and kw in win32gui.GetWindowText(h):
            r.append(h)
        return True
    win32gui.EnumWindows(e, 0)
    return r

np = find_window("记事本")
if np:
    edits = []
    def enum_edit(h, _):
        if win32gui.GetClassName(h) in ('Edit', 'RichEdit20W', 'RichEdit50W'):
            edits.append(h); return False
        return True
    win32gui.EnumChildWindows(np[0], enum_edit, 0)
    target = edits[0] if edits else np[0]
    for ch in "Hello Notepad!":
        win32gui.SendMessage(target, win32con.WM_CHAR, ord(ch), 0)
        time.sleep(0.02)
    win32gui.PostMessage(np[0], win32con.WM_COMMAND, 3, 0)
    log("Notepad: WM_COMMAND 3 sent")
else:
    log("ERROR: Notepad not found")

# ── Notepad++ ──
t_npp_start = time.time()
npp_exe = r"C:\Program Files\Notepad++\notepad++.exe"

if os.path.exists(npp_exe):
    proc2 = subprocess.Popen([npp_exe, "-nosession"])
    time.sleep(3)
    npp = find_window("Notepad++")
    if npp:
        hwnd_npp = npp[0]
        # Find Scintilla
        scis = []
        def enum_sci(h, _):
            if win32gui.GetClassName(h) == "Scintilla":
                scis.append(h); return False
            return True
        win32gui.EnumChildWindows(hwnd_npp, enum_sci, 0)
        if scis:
            import win32clipboard
            win32clipboard.OpenClipboard(0)
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText("Hello Notepad++!")
            win32clipboard.CloseClipboard()
            win32gui.SendMessage(scis[0], win32con.WM_PASTE, 0, 0)
            log("Notepad++: pasted text")
        # Bring to foreground + SendKeys
        fore_hwnd = win32gui.GetForegroundWindow()
        fore_tid = win32process.GetWindowThreadProcessId(fore_hwnd)[0]
        target_tid = win32process.GetWindowThreadProcessId(hwnd_npp)[0]
        ctypes.windll.user32.AttachThreadInput(target_tid, fore_tid, True)
        ctypes.windll.user32.SetForegroundWindow(hwnd_npp)
        ctypes.windll.user32.ShowWindow(hwnd_npp, 9)
        time.sleep(0.3)
        ctypes.windll.user32.AttachThreadInput(target_tid, fore_tid, False)
        subprocess.Popen(
            ["powershell", "-Command",
             "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait('^s')"],
            creationflags=subprocess.CREATE_NO_WINDOW).wait(timeout=5)
        log("Notepad++: Ctrl+S sent")
    else:
        log("WARN: Notepad++ not found")
else:
    log("WARN: Notepad++ not installed, skipping")
    proc2 = None

# ── Wait and check ──
log("Waiting for monitor...")
time.sleep(20)

# Check files — search broadly for any lingdong file anywhere
saved = sorted(glob.glob(os.path.join(desktop, "lingdong_*")))
# also check for double-extension and other variations
saved += sorted(glob.glob(os.path.join(desktop, "lingdong_*.*")))
saved += sorted(glob.glob(os.path.join(desktop, "lingdong_*" + ".txt.txt")))
# check cwd too
saved += sorted(glob.glob(os.path.join(os.getcwd(), "lingdong_*")))
saved = sorted(set(saved))
t_end = time.time()

for f in saved:
    sz = os.path.getsize(f) if os.path.isfile(f) else 0
    log(f"FILE OK: {os.path.basename(f)} ({sz} bytes)")

total = t_end - t0
log(f"=== TOTAL: {total:.1f}s ===")
log(f"Files saved: {len(saved)}/2")

# Debug: check Notepad++ default save location
npp_dir = os.path.expandvars(r"%APPDATA%\Notepad++")
if os.path.isdir(npp_dir):
    npp_files = [f for f in os.listdir(npp_dir) if f.startswith("lingdong_")]
    if npp_files:
        log(f"FOUND in Notepad++ appdata: {npp_files}")
# Also check user temp
temp_dir = os.environ.get("TEMP", "")
if temp_dir:
    temp_files = [f for f in os.listdir(temp_dir) if f.startswith("lingdong_")]
    if temp_files:
        log(f"FOUND in TEMP: {temp_files}")

# Cleanup
stop_dialog_monitor()
for p in ([proc1] + ([proc2] if proc2 else [])):
    try: p.kill(); p.wait(timeout=2)
    except: pass
for kw in ("记事本", "Notepad++"):
    for h in find_window(kw):
        try: win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
        except: pass
for f in glob.glob(os.path.join(desktop, "lingdong_*.txt")):
    os.unlink(f)
log("Cleanup done")
