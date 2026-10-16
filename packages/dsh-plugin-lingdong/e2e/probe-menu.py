"""Probe how this desktop exposes application menus.

Answers two questions with real evidence instead of memory:

1. Can the accessibility tree reach a second-level menu item?  The handover note
   claims the menu bar and its first-level items are readable while their
   children come back empty.  If that holds, UIA cannot be the path.
2. Does the plain Win32 menu API (GetMenu / GetMenuItemInfo / WM_COMMAND) reach
   the same items, including nested submenus?

Run:  python packages/dsh-plugin-lingdong/e2e/probe-menu.py
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import subprocess
import sys
import time

import win32con
import win32gui
import win32process

user32 = ctypes.WinDLL("user32", use_last_error=True)


class MENUITEMINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("fMask", wintypes.UINT),
        ("fType", wintypes.UINT),
        ("fState", wintypes.UINT),
        ("wID", wintypes.UINT),
        ("hSubMenu", wintypes.HMENU),
        ("hbmpChecked", wintypes.HBITMAP),
        ("hbmpUnchecked", wintypes.HBITMAP),
        ("dwItemData", ctypes.POINTER(ctypes.c_ulong)),
        ("dwTypeData", wintypes.LPWSTR),
        ("cch", wintypes.UINT),
        ("hbmpItem", wintypes.HBITMAP),
    ]


MIIM_STATE = 0x00000001
MIIM_ID = 0x00000002
MIIM_SUBMENU = 0x00000004
MIIM_STRING = 0x00000040
MIIM_FTYPE = 0x00000100

MFS_GRAYED = 0x00000003


def menu_items(hmenu: int) -> list[dict]:
    """Read every item of one menu handle through the Win32 menu API."""
    if not hmenu:
        return []
    count = int(user32.GetMenuItemCount(wintypes.HMENU(hmenu)))
    items: list[dict] = []
    for index in range(count):
        info = MENUITEMINFO()
        info.cbSize = ctypes.sizeof(MENUITEMINFO)
        info.fMask = MIIM_STATE | MIIM_ID | MIIM_SUBMENU | MIIM_STRING | MIIM_FTYPE
        buffer = ctypes.create_unicode_buffer(256)
        info.dwTypeData = ctypes.cast(buffer, wintypes.LPWSTR)
        info.cch = 256
        ok = user32.GetMenuItemInfoW(
            wintypes.HMENU(hmenu), index, True, ctypes.byref(info)
        )
        if not ok:
            items.append({"index": index, "error": int(ctypes.get_last_error())})
            continue
        items.append({
            "index": index,
            "text": buffer.value.replace("&", ""),
            "raw_text": buffer.value,
            "id": int(info.wID),
            "submenu": int(info.hSubMenu or 0),
            "state": int(info.fState),
            "enabled": (int(info.fState) & MFS_GRAYED) == 0,
            "separator": bool(int(info.fType) & 0x800),
        })
    return items


def walk(hmenu: int, depth: int = 1, limit: int = 2) -> list[dict]:
    if depth > limit:
        return []
    out = []
    for item in menu_items(hmenu):
        node = dict(item)
        if node.get("submenu"):
            node["children"] = walk(node["submenu"], depth + 1, limit)
        out.append(node)
    return out


def uia_probe(hwnd: int) -> dict:
    """Walk the accessibility tree the way the perception collector does."""
    try:
        import uiautomation as auto
    except Exception as exc:
        return {"available": False, "error": f"uiautomation is not importable: {exc}"}

    result: dict = {"available": True}
    try:
        window = auto.ControlFromHandle(hwnd)
        if window is None:
            return {"available": True, "error": "no accessibility element for that handle"}

        def find_menu_bar(control, depth=0):
            if depth > 4:
                return None
            try:
                children = control.GetChildren()
            except Exception:
                return None
            for child in children:
                try:
                    if child.ControlTypeName in ("MenuBarControl", "MenuControl"):
                        return child
                except Exception:
                    continue
            for child in children:
                found = find_menu_bar(child, depth + 1)
                if found is not None:
                    return found
            return None

        bar = find_menu_bar(window)
        if bar is None:
            return {"available": True, "menu_bar": None, "note": "no menu bar in the tree"}

        level1 = []
        for child in bar.GetChildren():
            entry = {
                "name": getattr(child, "Name", ""),
                "type": child.ControlTypeName,
                "children": len(child.GetChildren()),
                "patterns": [],
            }
            try:
                entry["patterns"] = [name for name in ("ExpandCollapse", "Invoke", "LegacyIAccessible") if child.GetPattern(getattr(auto, f"{name}Pattern")) is not None]
            except Exception:
                entry["patterns"] = ["<pattern query failed>"]
            level1.append(entry)
        result["menu_bar"] = {"type": bar.ControlTypeName, "name": getattr(bar, "Name", ""), "children": len(level1)}
        result["level1"] = level1
        return result
    except Exception as exc:
        return {"available": True, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    # DPI awareness first: a virtualized rectangle would be a wrong rectangle.
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception as exc:
        print(f"dpi awareness not set: {exc}")

    creation = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(["notepad.exe"], creationflags=creation)
    deadline = time.monotonic() + 15
    hwnd = 0
    while time.monotonic() < deadline:
        found = []

        def collect(handle, _):
            _, pid = win32process.GetWindowThreadProcessId(handle)
            if pid == proc.pid and win32gui.IsWindowVisible(handle) and win32gui.GetWindowText(handle):
                found.append(handle)

        win32gui.EnumWindows(collect, None)
        if found:
            hwnd = found[0]
            break
        time.sleep(0.2)

    if not hwnd:
        print("no Notepad window appeared")
        proc.terminate()
        return 1

    report = {
        "hwnd": int(hwnd),
        "title": win32gui.GetWindowText(hwnd),
        "class": win32gui.GetClassName(hwnd),
        "pid": int(proc.pid),
    }
    hmenu = int(user32.GetMenu(wintypes.HWND(hwnd)) or 0)
    report["win32_getmenu"] = hmenu
    report["win32_items"] = walk(hmenu, 1, 2) if hmenu else []
    report["uia"] = uia_probe(hwnd)

    print(json.dumps(report, ensure_ascii=False, indent=2))

    try:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    except Exception:
        pass
    time.sleep(0.5)
    if proc.poll() is None:
        proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
