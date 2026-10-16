"""Prove nested menu invocation on a window whose submenu levels UIA cannot reach.

Notepad offered a two-level menu. The case that decides whether path-addressed
menu invocation is a real capability is a menu nested three deep, because that is
where a click-based driver runs out of road: its target must be addressable in
the accessibility tree, and a submenu item has no node there.

This builds a small WinForms window with a three-level menu whose leaf commands
report what they ran through the window title -- an effect observable from
outside the application, by a process that did not cause it. It first records
what the accessibility tree exposes for that same menu, so "a click driver could
not do this" is measured rather than asserted.

Run:  python packages/dsh-plugin-lingdong/e2e/probe-menu-nested.py            # compare the two views, then exit
      python packages/dsh-plugin-lingdong/e2e/probe-menu-nested.py --serve    # keep the window up for a driver
"""
from __future__ import annotations

import ctypes
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import win32gui
import win32process

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = pathlib.Path(__file__).resolve().parent
ORACLE = HERE / "menu-oracle.py"

SOURCE = r"""
using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public class NativeDeepMenu {
    [DllImport("user32.dll")] static extern IntPtr CreateMenu();
    [DllImport("user32.dll")] static extern IntPtr CreatePopupMenu();
    [DllImport("user32.dll")] static extern bool AppendMenu(IntPtr menu, uint flags, IntPtr id, string text);
    [DllImport("user32.dll")] static extern bool SetMenu(IntPtr hwnd, IntPtr menu);
    [DllImport("user32.dll")] static extern bool DrawMenuBar(IntPtr hwnd);

    const uint MF_STRING = 0x0000;
    const uint MF_POPUP = 0x0010;
    const int WM_COMMAND = 0x0111;

    // Leaf commands are identified by these ids, and the window answers the
    // WM_COMMAND Windows itself sends for a chosen item by naming it in its own
    // title -- so the effect is readable from outside the application.
    const int ID_FILE_NEW = 101;
    const int ID_EXPORT_TEXT = 102;
    const int ID_EXPORT_CSV = 103;
    const int ID_EDIT_UNDO = 104;

    /// <summary>React to a menu command the way an application does.</summary>
    class CommandSink : NativeWindow {
        readonly Form _form;
        public CommandSink(Form form) { _form = form; }

        protected override void WndProc(ref Message m) {
            if (m.Msg == WM_COMMAND && (int)m.LParam == 0) {
                int id = (int)m.WParam & 0xFFFF;
                if (id == ID_FILE_NEW) _form.Text = "RAN:file-new";
                else if (id == ID_EXPORT_TEXT) _form.Text = "RAN:file-export-text";
                else if (id == ID_EXPORT_CSV) _form.Text = "RAN:file-export-csv";
                else if (id == ID_EDIT_UNDO) _form.Text = "RAN:edit-undo";
            }
            base.WndProc(ref m);
        }
    }

    static IntPtr Popup(params object[] pairs) {
        IntPtr menu = CreatePopupMenu();
        for (int i = 0; i < pairs.Length; i += 2) {
            AppendMenu(menu, MF_STRING, new IntPtr((int)pairs[i]), (string)pairs[i + 1]);
        }
        return menu;
    }

    [STAThread]
    public static void Main() {
        var form = new Form();
        form.Text = "Lingdong native menu probe";
        form.Width = 560; form.Height = 320;
        form.Show();                       // realize the handle first
        IntPtr hwnd = form.Handle;

        // A real Win32 menu: CreateMenu + AppendMenu + SetMenu, so GetMenu()
        // returns it and GetMenuItemInfo reads it. A WinForms MenuStrip would
        // not qualify: it is drawn by the toolkit, and GetMenu() answers zero.
        IntPtr bar = CreateMenu();
        IntPtr export = Popup(ID_EXPORT_TEXT, "As &text", ID_EXPORT_CSV, "As &csv");
        IntPtr file = Popup(ID_FILE_NEW, "&New");
        AppendMenu(file, MF_POPUP, export, "&Export");
        AppendMenu(bar, MF_POPUP, file, "&File");
        AppendMenu(bar, MF_POPUP, Popup(ID_EDIT_UNDO, "&Undo"), "&Edit");

        SetMenu(hwnd, bar);
        DrawMenuBar(hwnd);

        var sink = new CommandSink(form);
        sink.AssignHandle(hwnd);
        Application.Run(form);
    }
}
"""

COMPILER = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"


def bind_lifetime(handle) -> bool:
    """Tie a child process's life to this one through a Windows job object.

    A probe must not be able to leave a window on someone's desktop. Killing the
    builder alone was not enough: the window it starts is a separate process and
    simply outlives it. With KILL_ON_JOB_CLOSE, Windows terminates everything in
    the job when this process ends, however it ends.
    """
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            return False
        return bool(kernel32.AssignProcessToJobObject(job, int(handle)))
    except Exception:
        return False


def oracle(*args: str) -> dict:
    """Ask the out-of-process oracle, which neither drives nor is driven."""
    result = subprocess.run(
        [sys.executable, str(ORACLE), *args],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"oracle failed: {result.stderr or result.stdout}")
    return json.loads((result.stdout or "").strip().splitlines()[-1])


def find_window(pid: int) -> int:
    found = []

    def collect(handle, _param):
        _, owner = win32process.GetWindowThreadProcessId(handle)
        if owner == pid and win32gui.IsWindowVisible(handle) and win32gui.GetWindowText(handle):
            found.append(handle)

    win32gui.EnumWindows(collect, None)
    return found[0] if found else 0


def main() -> int:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass

    # The caller may own the working directory, so that a probe leaving a
    # temporary build behind is impossible rather than merely unlikely.
    requested = ""
    if "--workdir" in sys.argv:
        requested = sys.argv[sys.argv.index("--workdir") + 1]
    if requested:
        workdir = pathlib.Path(requested)
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        workdir = pathlib.Path(tempfile.gettempdir()) / f"lingdong-nested-{uuid.uuid4().hex[:8]}"
        workdir.mkdir(parents=True, exist_ok=True)
    source = workdir / "DeepMenu.cs"
    source.write_text(SOURCE, encoding="utf-8")
    exe = workdir / "DeepMenu.exe"
    build = subprocess.run(
        [COMPILER, "/nologo", "/target:winexe", f"/out:{exe}",
         "/reference:System.Windows.Forms.dll,System.Drawing.dll", str(source)],
        capture_output=True, text=True,
    )
    if build.returncode != 0 or not exe.exists():
        print(json.dumps({"built": False, "compiler": (build.stderr or build.stdout)[:600]}, ensure_ascii=False))
        return 1

    proc = subprocess.Popen([str(exe)])
    tied = bind_lifetime(proc._handle)
    deadline = time.monotonic() + 20
    hwnd = 0
    while time.monotonic() < deadline and not hwnd:
        hwnd = find_window(proc.pid)
        time.sleep(0.2)

    if not hwnd:
        proc.terminate()
        print(json.dumps({"built": True, "error": "window did not appear"}, ensure_ascii=False))
        return 1

    try:
        # The two readings of the same menu, from the same process, side by side.
        report = {
            "built": True,
            "hwnd": int(hwnd),
            "pid": int(proc.pid),
            "lifetime_tied_to_builder": tied,
            "win32_menu": oracle("--hwnd", str(hwnd), "--dump"),
            "uia_menu": oracle("--hwnd", str(hwnd), "--uia"),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"PROBE_WINDOW hwnd={hwnd} pid={proc.pid}", flush=True)

        if "--serve" in sys.argv:
            # Keep the window up so another process can drive its menu. The
            # caller owns it from here: it is removed when this process is.
            while proc.poll() is None:
                time.sleep(0.5)
    finally:
        # The window is this script's; it is removed with the process it started.
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
