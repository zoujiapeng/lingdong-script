"""Independent oracle for the menu probe.

Reads a window's own menu and state through the Win32 API, from a process that
is neither the plugin nor the runtime, so a menu command's effect is confirmed
by something that did not perform it.

Usage:
    python menu-oracle.py --hwnd 1234 --dump
    python menu-oracle.py --hwnd 1234 --uia
    python menu-oracle.py --hwnd 1234 --state
    python menu-oracle.py --foreground
    python menu-oracle.py --hwnd 1234 --deactivate
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import io
import json
import sys
import time

import win32con
import win32gui

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

user32 = ctypes.WinDLL("user32", use_last_error=True)

MIIM = (win32con.MIIM_STATE | win32con.MIIM_ID | win32con.MIIM_SUBMENU
        | win32con.MIIM_STRING | win32con.MIIM_FTYPE)


class MENUITEMINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("fMask", wintypes.UINT), ("fType", wintypes.UINT),
        ("fState", wintypes.UINT), ("wID", wintypes.UINT), ("hSubMenu", wintypes.HMENU),
        ("hbmpChecked", wintypes.HBITMAP), ("hbmpUnchecked", wintypes.HBITMAP),
        ("dwItemData", wintypes.LPVOID), ("dwTypeData", wintypes.LPVOID),
        ("cch", wintypes.UINT), ("hbmpItem", wintypes.HBITMAP),
    ]


def read_item(menu: int, position: int) -> dict | None:
    info = MENUITEMINFO()
    info.cbSize = ctypes.sizeof(MENUITEMINFO)
    info.fMask = MIIM
    buf = ctypes.create_unicode_buffer(256)
    info.dwTypeData = ctypes.cast(buf, wintypes.LPVOID)
    info.cch = 256
    if not user32.GetMenuItemInfoW(wintypes.HMENU(menu), int(position), True, ctypes.byref(info)):
        return None
    return {
        "text": buf.value,
        "id": int(info.wID),
        "submenu": int(info.hSubMenu or 0),
        "enabled": (int(info.fState) & win32con.MFS_GRAYED) == 0,
        "checked": bool(int(info.fState) & win32con.MFS_CHECKED),
        "separator": bool(int(info.fType) & win32con.MF_SEPARATOR),
    }


def all_items(menu: int) -> list[dict]:
    if not menu:
        return []
    count = int(user32.GetMenuItemCount(wintypes.HMENU(menu)))
    return [entry for entry in (read_item(menu, index) for index in range(count)) if entry]


def dump(hwnd: int, depth: int = 3) -> dict:
    """The window's real menu, nested to ``depth`` levels."""
    menu = int(user32.GetMenu(wintypes.HWND(hwnd)) or 0)

    def walk(handle: int, level: int) -> list[dict]:
        entries = all_items(handle)
        for entry in entries:
            entry["children"] = walk(entry["submenu"], level + 1) if entry.get("submenu") and level < depth else []
        return entries

    return {"hwnd": hwnd, "menu": menu, "items": walk(menu, 1)}


def uia_dump(hwnd: int, depth: int = 3) -> dict:
    """What the accessibility tree exposes for the same menu.

    This is the other half of the comparison: the Win32 reading above and this
    one describe the same menu, and where they disagree is the whole point.
    """
    try:
        import uiautomation as auto
    except Exception as exc:
        return {"available": False, "error": f"uiautomation unavailable: {exc}"}

    try:
        window = auto.ControlFromHandle(hwnd)
        if window is None:
            return {"available": True, "menu_bar": None, "note": "no accessibility element"}
        bar = None
        for child in window.GetChildren():
            if child.ControlTypeName in ("MenuBarControl", "MenuControl"):
                bar = child
                break
        if bar is None:
            return {"available": True, "menu_bar": None, "note": "no menu bar in the tree"}

        levels: list[dict] = []

        def walk(control, level: int) -> None:
            if level > depth:
                return
            try:
                children = control.GetChildren()
            except Exception:
                children = []
            for child in children:
                try:
                    levels.append({
                        "level": level,
                        "name": str(getattr(child, "Name", "")),
                        "type": child.ControlTypeName,
                        "children": len(child.GetChildren()),
                    })
                except Exception:
                    continue
                walk(child, level + 1)

        walk(bar, 1)
        return {"available": True, "menu_bar": {"name": str(getattr(bar, "Name", "")), "type": bar.ControlTypeName}, "levels": levels}
    except Exception as exc:
        return {"available": True, "error": f"{type(exc).__name__}: {exc}"}


def text_length(hwnd: int) -> int:
    out = ctypes.c_size_t()
    ok = user32.SendMessageTimeoutW(
        wintypes.HWND(hwnd), win32con.WM_GETTEXTLENGTH, 0, 0,
        win32con.SMTO_ABORTIFHUNG, 1000, ctypes.byref(out),
    )
    return int(out.value) if ok else -1


def state(hwnd: int) -> dict:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    classes = []

    def collect(child, _param):
        if win32gui.IsWindowVisible(child):
            classes.append(str(win32gui.GetClassName(child) or ""))
        return True

    win32gui.EnumChildWindows(hwnd, collect, None)
    return {
        "hwnd": hwnd,
        "title": str(win32gui.GetWindowText(hwnd)),
        "text_length": text_length(hwnd),
        "rect": [int(left), int(top), int(right - left), int(bottom - top)],
        "visible_children": sorted(classes),
        "foreground": int(win32gui.GetForegroundWindow()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hwnd", type=int, default=0)
    parser.add_argument("--dump", action="store_true")
    parser.add_argument("--uia", action="store_true")
    parser.add_argument("--close", action="store_true")
    parser.add_argument("--state", action="store_true")
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--deactivate", action="store_true")
    args = parser.parse_args()

    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass

    if args.foreground:
        print(json.dumps({"foreground": int(win32gui.GetForegroundWindow())}))
        return 0
    if args.deactivate:
        shell = int(user32.GetShellWindow() or 0)
        if shell:
            try:
                user32.SetForegroundWindow(wintypes.HWND(shell))
            except Exception:
                pass
        time.sleep(0.8)
        print(json.dumps({"foreground": int(win32gui.GetForegroundWindow()), "shell": shell}))
        return 0
    if args.dump:
        print(json.dumps(dump(args.hwnd), ensure_ascii=False))
        return 0
    if args.uia:
        print(json.dumps(uia_dump(args.hwnd), ensure_ascii=False))
        return 0
    if args.close:
        # Closing the window through its own message queue is the polite path;
        # the caller verifies that it actually went.
        win32gui.PostMessage(args.hwnd, win32con.WM_CLOSE, 0, 0)
        print(json.dumps({"closed": int(args.hwnd)}))
        return 0
    print(json.dumps(state(args.hwnd), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
