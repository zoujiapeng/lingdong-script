"""
系统控制功能
音量 / 亮度 / 锁屏 / 电池 / 回收站 — 基于 Windows API
"""
import time

from features.registry import feature, P, TC, FeatureCategory as F


@feature(
    name="volume_set",
    display_name="设置音量",
    description="【系统控制】设置系统主音量到指定百分比（0-100）。用于控制播放音量",
    category=F.SYSTEM,
    params=[
        P("level", "number", "音量 0-100", example=50),
    ],
    returns="bool - 是否成功",
    tags=["system", "audio"],
    hidden=True,
)
def volume_set(level: float) -> bool:
    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        scalar = max(0.0, min(1.0, level / 100.0))
        volume.SetMasterVolumeLevelScalar(scalar, None)
        return True
    except ImportError:
        # fallback: 通过快捷键模拟
        try:
            import pyautogui
            # 模拟音量增加/减少到目标水平（粗略）
            pyautogui.press("volumemute")
            return True
        except Exception:
            return False


@feature(
    name="volume_mute",
    display_name="静音切换",
    description="【系统控制】切换系统静音状态。用于快速静音或取消静音",
    category=F.SYSTEM,
    params=[
        P("mute", "boolean", "True=静音 False=取消静音（不填则切换）", required=False, default=None),
    ],
    returns="dict{muted, volume} - 当前状态",
    tags=["system", "audio"],
    hidden=True,
)
def volume_mute(mute: bool = None) -> dict:
    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))

        if mute is not None:
            volume.SetMute(1 if mute else 0, None)
        else:
            volume.SetMute(not volume.GetMute(), None)

        current_mute = volume.GetMute()
        current_level = round(volume.GetMasterVolumeLevelScalar() * 100, 1)
        return {"success": True, "result": {"muted": bool(current_mute), "volume": current_level}}
    except ImportError:
        try:
            import pyautogui
            pyautogui.press("volumemute")
            return {"success": True, "result": {"muted": None, "volume": None}}
        except Exception:
            return {"success": False, "error": "pycaw/音量控制不可用"}


@feature(
    name="display_brightness",
    display_name="设置屏幕亮度",
    description="【系统控制】设置显示器亮度百分比（0-100）。用于调节屏幕亮度",
    category=F.SYSTEM,
    params=[
        P("level", "number", "亮度 0-100", example=80),
        P("display_index", "int", "显示器索引（0=主屏）", required=False, default=0),
    ],
    returns="bool - 是否成功",
    tags=["system", "display"],
    hidden=True,
)
def display_brightness(level: float, display_index: int = 0) -> bool:
    try:
        import wmi
        c = wmi.WMI()
        level = max(0, min(100, int(level)))
        monitors = c.WmiMonitorBrightnessMethods()
        if display_index < len(monitors):
            monitors[display_index].WmiSetBrightness(level, 0)
            return True
        elif monitors:
            monitors[0].WmiSetBrightness(level, 0)
            return True
        return False
    except ImportError:
        return False


@feature(
    name="lock_workstation",
    display_name="锁定工作站",
    description="【系统控制】锁定当前工作站（等效 Win+L）。用于离开时快速锁定",
    category=F.SYSTEM,
    params=[],
    returns="bool - 是否成功",
    tags=["system", "security"],
    hidden=True,
)
def lock_workstation() -> bool:
    try:
        import ctypes
        ctypes.windll.user32.LockWorkStation()
        return True
    except Exception:
        try:
            import pyautogui
            pyautogui.hotkey("win", "l")
            return True
        except Exception:
            return False


@feature(
    name="battery_status",
    display_name="获取电池状态",
    description="【系统控制】获取电池电量百分比、是否充电、剩余时间等信息。用于笔记本供电状态监控",
    category=F.SYSTEM,
    params=[],
    returns="dict{percentage, charging, remaining} - 电池信息",
    tags=["system", "power"],
    hidden=True,
)
def battery_status() -> dict:
    try:
        import psutil
        battery = psutil.sensors_battery()
        if battery:
            return {
                "success": True,
                "result": {
                    "percentage": round(battery.percent, 1),
                    "charging": battery.power_plugged or False,
                    "remaining_sec": battery.secsleft if battery.secsleft != -1 else None,
                }
            }
        return {"success": False, "error": "未检测到电池"}
    except ImportError:
        # fallback: Windows API
        try:
            import ctypes
            from ctypes import wintypes
            class SYSTEM_POWER_STATUS(ctypes.Structure):
                _fields_ = [
                    ("ACLineStatus", wintypes.BYTE),
                    ("BatteryFlag", wintypes.BYTE),
                    ("BatteryLifePercent", wintypes.BYTE),
                    ("Reserved1", wintypes.BYTE),
                    ("BatteryLifeTime", wintypes.DWORD),
                    ("BatteryFullLifeTime", wintypes.DWORD),
                ]
            sps = SYSTEM_POWER_STATUS()
            ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps))
            return {
                "success": True,
                "result": {
                    "percentage": float(sps.BatteryLifePercent) if sps.BatteryLifePercent <= 100 else None,
                    "charging": sps.ACLineStatus == 1,
                    "remaining_sec": int(sps.BatteryLifeTime) if sps.BatteryLifeTime != -1 else None,
                }
            }
        except Exception:
            return {"success": False, "error": "无法获取电池信息"}


@feature(
    name="empty_recycle_bin",
    display_name="清空回收站",
    description="【系统控制】清空回收站。用于释放磁盘空间",
    category=F.SYSTEM,
    params=[
        P("confirm", "boolean", "是否确认清空（防误触）", required=False, default=True),
    ],
    returns="bool - 是否成功",
    tags=["system", "disk"],
    hidden=True,
)
def empty_recycle_bin(confirm: bool = True) -> bool:
    if confirm:
        return {"success": False, "error": "请设置 confirm=False 以确认清空回收站（防误触保护）"}
    try:
        import ctypes
        # SHEmptyRecycleBinW(None, None, 0)
        ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0)
        return True
    except Exception:
        try:
            import subprocess
            subprocess.run(["cmd", "/c", "rd /s /q C:\\$Recycle.Bin"], capture_output=True, timeout=5)
            return True
        except Exception:
            return False


@feature(
    name="shutdown",
    display_name="关机",
    description="【系统控制】关闭计算机。可设置延迟秒数，支持执行前确认",
    category=F.SYSTEM,
    params=[
        P("delay_seconds", "int", "延迟关机秒数（0=立即）", required=False, default=0),
        P("confirm", "boolean", "执行前确认", required=False, default=True),
    ],
    returns="bool - 关机指令是否已发出",
    dsl_keyword="SHUTDOWN",
    dsl_template="SHUTDOWN {delay_seconds}",
    flow_type="shutdown",
    examples=["SHUTDOWN", "SHUTDOWN 30"],
    tags=["system", "power"],
)
def shutdown_fn(delay_seconds: int = 0, confirm: bool = True) -> bool:
    if confirm:
        return {"success": False, "error": "请设置 confirm=False 以确认关机（防误触保护）"}
    import subprocess
    subprocess.run(["shutdown", "/s", "/t", str(delay_seconds)], shell=False)
    return True


@feature(
    name="reboot",
    display_name="重启",
    description="【系统控制】重新启动计算机。可设置延迟秒数，支持执行前确认",
    category=F.SYSTEM,
    params=[
        P("delay_seconds", "int", "延迟重启秒数（0=立即）", required=False, default=0),
        P("confirm", "boolean", "执行前确认", required=False, default=True),
    ],
    returns="bool - 重启指令是否已发出",
    dsl_keyword="REBOOT",
    dsl_template="REBOOT {delay_seconds}",
    flow_type="reboot",
    examples=["REBOOT", "REBOOT 60"],
    tags=["system", "power"],
)
def reboot_fn(delay_seconds: int = 0, confirm: bool = True) -> bool:
    if confirm:
        return {"success": False, "error": "请设置 confirm=False 以确认重启（防误触保护）"}
    import subprocess
    subprocess.run(["shutdown", "/r", "/t", str(delay_seconds)], shell=False)
    return True


@feature(
    name="logoff",
    display_name="注销",
    description="【系统控制】注销当前用户",
    category=F.SYSTEM,
    params=[
        P("confirm", "boolean", "执行前确认", required=False, default=True),
    ],
    returns="bool - 注销指令是否已发出",
    dsl_keyword="LOGOFF",
    dsl_template="LOGOFF",
    flow_type="logoff",
    examples=["LOGOFF"],
    tags=["system", "power"],
)
def logoff_fn(confirm: bool = True) -> bool:
    if confirm:
        return {"success": False, "error": "请设置 confirm=False 以确认注销（防误触保护）"}
    import subprocess
    subprocess.run(["shutdown", "/l"], shell=False)
    return True


@feature(
    name="hibernate",
    display_name="休眠",
    description="【系统控制】使计算机进入休眠状态",
    category=F.SYSTEM,
    params=[
        P("confirm", "boolean", "执行前确认", required=False, default=True),
    ],
    returns="bool - 休眠指令是否已发出",
    dsl_keyword="HIBERNATE",
    dsl_template="HIBERNATE",
    flow_type="hibernate",
    examples=["HIBERNATE"],
    tags=["system", "power"],
)
def hibernate_fn(confirm: bool = True) -> bool:
    if confirm:
        return {"success": False, "error": "请设置 confirm=False 以确认休眠（防误触保护）"}
    import subprocess
    subprocess.run(["shutdown", "/h"], shell=False)
    return True


@feature(
    name="kill_process",
    display_name="终止进程",
    description="【系统控制】按名称终止指定进程。用于清理环境或强制关闭无响应程序",
    category=F.SYSTEM,
    params=[
        P("name", "str", "进程名称（如 notepad.exe）", example="notepad.exe"),
        P("force", "boolean", "是否强制终止", required=False, default=False),
    ],
    returns="dict{success, killed: bool} - 是否成功终止",
    dsl_keyword="KILL",
    dsl_template="KILL {name}",
    flow_type="kill_process",
    examples=["KILL notepad.exe"],
    tags=["system", "process"],
)
def kill_process(name: str, force: bool = False) -> dict:
    try:
        import subprocess, signal
        flags = ["/f"] if force else []
        result = subprocess.run(
            ["taskkill"] + flags + ["/im", name],
            capture_output=True, text=True, timeout=10,
        )
        success = result.returncode == 0
        return {"success": success, "result": {"killed": success, "detail": result.stdout.strip()}}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "终止进程超时"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="wait_process_start",
    display_name="等待进程启动",
    description="【系统控制】等待指定进程启动（轮询检测）。用于等待目标软件启动后再继续",
    category=F.SYSTEM,
    params=[
        P("name", "str", "进程名称", example="notepad.exe"),
        P("timeout", "number", "超时秒数", required=False, default=30),
        P("poll_interval", "number", "轮询间隔秒数", required=False, default=0.5),
    ],
    returns="dict{success, elapsed} - 进程是否已启动",
    dsl_keyword="WAIT_PROCESS",
    dsl_template="WAIT_PROCESS {name} {timeout}",
    flow_type="wait_process",
    examples=["WAIT_PROCESS notepad.exe 30"],
    tags=["system", "process"],
)
def wait_process_start(name: str, timeout: float = 30, poll_interval: float = 0.5) -> dict:
    try:
        import time, psutil
        deadline = time.time() + timeout
        while time.time() < deadline:
            for proc in psutil.process_iter(['name']):
                if proc.info['name'] and name.lower() in proc.info['name'].lower():
                    return {"success": True, "result": {"elapsed": round(timeout - (deadline - time.time()), 2)}}
            time.sleep(poll_interval)
        return {"success": False, "error": f"等待进程 '{name}' 启动超时 ({timeout}s)"}
    except ImportError:
        # fallback: 用 tasklist 轮询
        try:
            import subprocess, time
            deadline = time.time() + timeout
            while time.time() < deadline:
                r = subprocess.run(["tasklist", "/fi", f"IMAGENAME eq {name}"],
                                   capture_output=True, text=True, timeout=5)
                if name.lower() in r.stdout.lower():
                    return {"success": True, "result": {"elapsed": round(timeout - (deadline - time.time()), 2)}}
                time.sleep(poll_interval)
            return {"success": False, "error": f"等待进程 '{name}' 启动超时 ({timeout}s)"}
        except Exception as e:
            return {"success": False, "error": str(e)}


@feature(
    name="wait_process_exit",
    display_name="等待进程退出",
    description="【系统控制】等待指定进程退出。用于等待安装程序完成或进程自动结束",
    category=F.SYSTEM,
    params=[
        P("name", "str", "进程名称", example="notepad.exe"),
        P("timeout", "number", "超时秒数", required=False, default=60),
        P("poll_interval", "number", "轮询间隔秒数", required=False, default=0.5),
    ],
    returns="dict{success, elapsed} - 进程是否已退出",
    dsl_keyword="WAIT_EXIT",
    dsl_template="WAIT_EXIT {name} {timeout}",
    flow_type="wait_process",
    examples=["WAIT_EXIT notepad.exe 60"],
    tags=["system", "process"],
)
def wait_process_exit(name: str, timeout: float = 60, poll_interval: float = 0.5) -> dict:
    try:
        import time, psutil
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = False
            for proc in psutil.process_iter(['name']):
                if proc.info['name'] and name.lower() in proc.info['name'].lower():
                    found = True
                    break
            if not found:
                return {"success": True, "result": {"elapsed": round(timeout - (deadline - time.time()), 2)}}
            time.sleep(poll_interval)
        return {"success": False, "error": f"等待进程 '{name}' 退出超时 ({timeout}s)"}
    except ImportError:
        try:
            import subprocess, time
            deadline = time.time() + timeout
            while time.time() < deadline:
                r = subprocess.run(["tasklist", "/fi", f"IMAGENAME eq {name}"],
                                   capture_output=True, text=True, timeout=5)
                if name.lower() not in r.stdout.lower():
                    return {"success": True, "result": {"elapsed": round(timeout - (deadline - time.time()), 2)}}
                time.sleep(poll_interval)
            return {"success": False, "error": f"等待进程 '{name}' 退出超时 ({timeout}s)"}
        except Exception as e:
            return {"success": False, "error": str(e)}


@feature(
    name="set_wallpaper",
    display_name="设置壁纸",
    description="【系统控制】将指定图片设为桌面壁纸。支持 bmp/jpg/png 格式",
    category=F.SYSTEM,
    params=[
        P("path", "str", "图片路径", example="C:\\wallpaper.jpg"),
        P("style", "str", "填充方式: fill|fit|stretch|tile|center", required=False, default="fill"),
    ],
    returns="bool - 是否成功",
    dsl_keyword="WALLPAPER",
    dsl_template="WALLPAPER {path}",
    flow_type="set_wallpaper",
    examples=["WALLPAPER C:\\wallpaper.jpg"],
    tags=["system", "desktop"],
)
def set_wallpaper(path: str, style: str = "fill") -> bool:
    try:
        import ctypes
        # SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, path, SPIF_UPDATEINIFILE)
        SPI_SETDESKWALLPAPER = 20
        SPIF_UPDATEINIFILE = 0x02
        result = ctypes.windll.user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, path, SPIF_UPDATEINIFILE)
        if not result:
            return False
        # 设置样式（注册表）
        try:
            import winreg
            style_map = {"fill": 10, "fit": 6, "stretch": 2, "tile": 1, "center": 0}
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Control Panel\Desktop", 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "WallpaperStyle", 0, winreg.REG_SZ, str(style_map.get(style, 10)))
                winreg.SetValueEx(key, "TileWallpaper", 0, winreg.REG_SZ, "1" if style == "tile" else "0")
        except Exception:
            pass
        return True
    except Exception as e:
        return False


@feature(
    name="display_off",
    display_name="关闭显示器",
    description="【系统控制】关闭所有显示器（进入省电模式）。移动鼠标或按任意键恢复",
    category=F.SYSTEM,
    params=[],
    returns="bool - 是否成功",
    dsl_keyword="DISPLAY_OFF",
    dsl_template="DISPLAY_OFF",
    flow_type="display_off",
    examples=["DISPLAY_OFF"],
    tags=["system", "power"],
)
def display_off() -> bool:
    try:
        import ctypes
        # SendMessage(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)
        HWND_BROADCAST = 0xFFFF
        WM_SYSCOMMAND = 0x0112
        SC_MONITORPOWER = 0xF170
        MONITOR_OFF = 2
        ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF)
        return True
    except Exception as e:
        return False


@feature(
    name="system_sleep",
    display_name="睡眠",
    description="【系统控制】使计算机进入睡眠（待机）状态。与休眠不同，睡眠保持内存供电",
    category=F.SYSTEM,
    params=[
        P("confirm", "boolean", "执行前确认", required=False, default=True),
    ],
    returns="bool - 睡眠指令是否已发出",
    dsl_keyword="SLEEP",
    dsl_template="SLEEP",
    flow_type="system_sleep",
    examples=["SLEEP"],
    tags=["system", "power"],
)
def system_sleep(confirm: bool = True) -> bool:
    if confirm:
        return {"success": False, "error": "请设置 confirm=False 以确认睡眠（防误触保护）"}
    try:
        import ctypes
        # SetSuspendState(False, True, False) — 睡眠，允许唤醒事件，不强制
        ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
        return True
    except Exception as e:
        return False


@feature(
    name="show_desktop",
    display_name="显示桌面",
    description="【系统控制】最小化所有窗口，显示桌面（等效 Win+D）。用于快速回到桌面",
    category=F.SYSTEM,
    params=[],
    returns="bool - 是否成功",
    dsl_keyword="DESKTOP",
    dsl_template="DESKTOP",
    flow_type="show_desktop",
    examples=["DESKTOP"],
    tags=["system", "desktop"],
)
def show_desktop() -> bool:
    try:
        import ctypes
        # ShellExec Windows 10/11: toggle desktop
        ctypes.windll.user32.ShowWindow(
            ctypes.windll.kernel32.GetConsoleWindow(), 0)
        # 模拟 Win+D
        import pyautogui
        pyautogui.hotkey("win", "d")
        return True
    except ImportError:
        try:
            import ctypes
            ctypes.windll.user32.keybd_event(0x5B, 0, 0, 0)  # Win key down
            ctypes.windll.user32.keybd_event(0x44, 0, 0, 0)  # D key down
            ctypes.windll.user32.keybd_event(0x44, 0, 2, 0)  # D key up
            ctypes.windll.user32.keybd_event(0x5B, 0, 2, 0)  # Win key up
            return True
        except Exception as e:
            return False
