"""
通知功能
send_notification — Windows 托盘通知/弹窗提醒
Uses win32api Shell_NotifyIcon for taskbar balloon notifications.
"""

from features.registry import feature, P, TC, FeatureCategory as F

_NOTIFICATION_ID = 1


@feature(
    name="send_notification",
    display_name="发送通知",
    description="【系统·通知】在 Windows 任务栏托盘区域显示气泡通知。用于任务完成提醒、错误告警或状态更新",
    category=F.SYSTEM,
    params=[
        P("title", "str", "通知标题", example="任务完成"),
        P("message", "str", "通知正文内容", example="自动化脚本已执行完毕"),
        P("duration", "number", "显示秒数（0=使用系统默认）", required=False, default=0),
    ],
    returns="bool - 是否成功",
    tags=["notification", "system", "tray"],
)
def send_notification(title: str, message: str, duration: float = 0) -> dict:
    try:
        import win32gui
        import win32api
        import win32con

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            hwnd = win32api.GetConsoleWindow()

        flags = win32con.NIF_ICON | win32con.NIF_INFO | win32con.NIF_TIP
        tip = title[:128] if title else ""

        # Use a standard Windows icon
        try:
            import win32ui
            icon = win32ui.LoadIcon(0, win32con.IDI_INFORMATION)
            hicon = icon.GetHandle()
        except Exception:
            hicon = 0

        # Shell_NotifyIcon data structure via RegisterWindowMessage
        import ctypes
        from ctypes import wintypes

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HANDLE),
                ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", wintypes.BYTE * 16),
                ("hBalloonIcon", wintypes.HANDLE),
            ]

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = _NOTIFICATION_ID
        nid.uFlags = win32con.NIF_INFO | win32con.NIF_ICON | win32con.NIF_TIP
        nid.hIcon = hicon
        nid.szTip = tip[:127]
        nid.szInfo = message[:255]
        nid.szInfoTitle = title[:63]
        nid.dwInfoFlags = 1  # NIIF_INFO

        ctypes.windll.shell32.Shell_NotifyIconW(0x00000001, ctypes.byref(nid))  # NIM_ADD

        if duration > 0:
            import time
            time.sleep(duration)
            ctypes.windll.shell32.Shell_NotifyIconW(0x00000002, ctypes.byref(nid))  # NIM_DELETE

        return {"success": True, "result": True}
    except ImportError:
        # Fallback: try print to console with a bell
        print(f"\n[NOTIFICATION] {title}: {message}")
        return {"success": True, "result": True, "fallback": "console"}
    except Exception as e:
        return {"success": False, "error": f"Notification failed: {e}"}
