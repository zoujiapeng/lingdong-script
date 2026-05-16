"""
诊断工具: 枚举所有可见窗口的类名和标题。用于识别保存对话框的窗口类。

用法: 在触发保存对话框后运行: python tools/diag_windows.py

输出安全: 所有非 ASCII 字符使用 repr() 转义，避免 GBK 编码错误。
"""

import sys

# Force UTF-8 for stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import win32gui
import win32process
import psutil
import ctypes

_output = []

def safe(s: str, maxlen: int = 50) -> str:
    """Return repr(s) if it contains non-ASCII, else as-is, truncated."""
    try:
        s.encode("gbk")
    except UnicodeEncodeError:
        return repr(s)[:maxlen]
    return s[:maxlen]

def say(line: str = ""):
    _output.append(line)

def flush():
    for line in _output:
        try:
            sys.stdout.write(line + "\n")
        except UnicodeEncodeError:
            sys.stdout.write(repr(line) + "\n")
    sys.stdout.flush()
    _output.clear()

def enum_all():
    say("=== All visible windows ===")
    say(f"{'HWND':>8} {'Class':30s} {'Title':50s} {'PID':>6} {'Process':20s}")
    say("-" * 130)

    def _enum(hwnd, _unused):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            cls = win32gui.GetClassName(hwnd)
        except Exception:
            cls = "?"
        try:
            title = win32gui.GetWindowText(hwnd)
        except Exception:
            title = "?"
        try:
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            proc = psutil.Process(pid)
            pname = proc.name()
        except Exception:
            pid = 0
            pname = "?"

        line = f"{hwnd:>8} {safe(cls,30):30s} \"{safe(title,48)}\" {pid:>6} {safe(pname,20)}"
        say(line)
        # Flush periodically to show progress
        if len(_output) > 50:
            flush()

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception as e:
        say(f"[EnumWindows error] {e}")
    flush()

def check_alt_methods():
    """Alternative window detection checks."""
    say("\n=== Direct FindWindow checks ===")
    for cls, title in [
        ("#32770", "另存为"),
        ("#32770", "保存为"),
        ("#32770", "保存"),
        ("#32770", "Save As"),
        ("#32770", "Save"),
        ("#32770", None),  # any #32770 dialog
        ("Chrome_WidgetWin_1", None),
        ("DirectUIHWND", None),
        ("Windows.UI.Core.CoreWindow", None),
        ("WorkerW", None),
    ]:
        try:
            hwnd = win32gui.FindWindow(cls, title)
            visible = win32gui.IsWindowVisible(hwnd) if hwnd else False
            actual_title = win32gui.GetWindowText(hwnd) if hwnd else ""
            say(f"  FindWindow({cls}, {title!r}) -> hwnd={hwnd}, visible={visible}, title={safe(actual_title)}")
        except Exception as e:
            say(f"  FindWindow({cls}, {title!r}) -> error: {e}")

    say("\n=== All #32770 dialogs ===")
    def enum_dialogs(hwnd, _unused):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            cls = win32gui.GetClassName(hwnd)
            if cls == "#32770":
                title = win32gui.GetWindowText(hwnd)
                pid = win32process.GetWindowThreadProcessId(hwnd)[1]
                try:
                    proc = psutil.Process(pid).name()
                except Exception:
                    proc = "?"
                say(f"  hwnd={hwnd:>8} title={safe(title,40)} pid={pid} proc={safe(proc)}")
        except Exception:
            pass
    try:
        win32gui.EnumWindows(enum_dialogs, None)
    except Exception as e:
        say(f"[EnumWindows error] {e}")
    flush()

def check_focus():
    """Check currently focused window."""
    say("\n=== Focus info ===")
    try:
        foreground = ctypes.windll.user32.GetForegroundWindow()
        cls = win32gui.GetClassName(foreground)
        title = win32gui.GetWindowText(foreground)
        say(f"  ForegroundWindow: hwnd={foreground}, class={safe(cls)}, title={safe(title)}")
    except Exception as e:
        say(f"  Focus check error: {e}")
    flush()

if __name__ == "__main__":
    import time
    say("Window diagnostic tool")
    say("=" * 60)
    say(f"Running at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    say("")
    check_focus()
    enum_all()
    check_alt_methods()
    say("=" * 60)
    say("Done.")
    flush()
