"""
系统功能注册
进程检测 / 窗口管理 / 剪贴板输入
"""
import logging
# import time

from features.registry import feature, P, TC, FeatureCategory as F

logger = logging.getLogger(__name__)

_DPI_AWARENESS_SET = False


def _ensure_dpi_awareness():
    """Align Win32 coordinates with screenshot pixels on scaled displays."""
    global _DPI_AWARENESS_SET
    if _DPI_AWARENESS_SET:
        return
    _DPI_AWARENESS_SET = True
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    except Exception:
        pass


_ensure_dpi_awareness()


@feature(
    name="detect_process",
    display_name="检测进程",
    description="【系统·进程】检查指定进程是否在运行中。用于确认目标软件已启动或等待进程退出",
    category=F.SYSTEM,
    params=[
        P("name", "str", "进程名称，如 notepad.exe、chrome.exe", example="notepad.exe"),
        P("exact", "boolean", "是否精确匹配", required=False, default=False),
    ],
    returns="bool - 进程是否存在",
    hidden=False,
    test_cases=[
        TC("python", {"name": "python"}, "success",
           validator=lambda r: r.get("success") is True, skip_in_ci=False),
    ],
)
def detect_process(name: str, exact: bool = False) -> bool:
    try:
        from system.process import ProcessDetector
        return ProcessDetector().is_running(name, exact=exact)
    except ImportError:
        return False


@feature(
    name="find_processes",
    display_name="查找进程",
    description="【系统·进程详情】查找所有匹配的进程，返回 PID、CPU、内存等详细信息。比 detect_process 更详细，用于监控和诊断",
    category=F.SYSTEM,
    params=[
        P("name", "str", "进程名称", example="python"),
    ],
    returns="list[dict{pid, name, cpu, memory}]",
    hidden=False,
)
def find_processes(name: str) -> list:
    try:
        from system.process import ProcessDetector
        return ProcessDetector().find_processes(name)
    except ImportError:
        return []


@feature(
    name="focus_window",
    display_name="激活窗口",
    description="【系统·窗口激活】将指定标题的窗口带到前台。操作前确保目标窗口在前台。注意 Windows 限制跨进程置前，可能需重试",
    category=F.SYSTEM,
    params=[
        P("title", "str", "窗口标题（支持模糊匹配）", example="记事本"),
        P("exact", "boolean", "是否精确匹配", required=False, default=False),
    ],
    returns="bool - 是否成功",
    dsl_keyword="FOCUS",
    dsl_template="FOCUS {title}",
    flow_type="focus_window",
    examples=["focus_window title=记事本"],
)
def focus_window(title: str, exact: bool = False) -> bool:
    try:
        import time
        import win32api
        import win32con
        import win32gui
        import win32process

        needle = (title or "").strip().lower()
        if not needle:
            return False

        def matches(text: str) -> bool:
            value = (text or "").strip().lower()
            if not value:
                return False
            return value == needle if exact else (needle in value or value in needle)

        candidates = []

        def enum_cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            text = win32gui.GetWindowText(hwnd).strip()
            if matches(text):
                rect = win32gui.GetWindowRect(hwnd)
                width = rect[2] - rect[0]
                height = rect[3] - rect[1]
                if width > 0 and height > 0:
                    candidates.append(hwnd)

        win32gui.EnumWindows(enum_cb, None)
        if not candidates:
            return False

        hwnd = candidates[0]
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        try:
            rect = win32gui.GetWindowRect(hwnd)
            width = rect[2] - rect[0]
            height = rect[3] - rect[1]
            screen_w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
            screen_h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
            new_left = min(max(rect[0], 0), max(0, screen_w - min(width, screen_w)))
            new_top = min(max(rect[1], 0), max(0, screen_h - min(height, screen_h)))
            if new_left != rect[0] or new_top != rect[1]:
                win32gui.SetWindowPos(
                    hwnd, None, new_left, new_top, width, height,
                    win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
                )
        except Exception:
            pass

        foreground = win32gui.GetForegroundWindow()
        current_thread = win32api.GetCurrentThreadId()
        target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
        foreground_thread = 0
        if foreground:
            foreground_thread, _ = win32process.GetWindowThreadProcessId(foreground)

        attached = []
        try:
            for thread_id in {target_thread, foreground_thread}:
                if thread_id and thread_id != current_thread:
                    try:
                        win32process.AttachThreadInput(current_thread, thread_id, True)
                        attached.append(thread_id)
                    except Exception:
                        pass
            for op in (
                lambda: win32gui.BringWindowToTop(hwnd),
                lambda: win32gui.SetForegroundWindow(hwnd),
                lambda: win32gui.SetActiveWindow(hwnd),
            ):
                try:
                    op()
                except Exception:
                    pass
        finally:
            for thread_id in attached:
                try:
                    win32process.AttachThreadInput(current_thread, thread_id, False)
                except Exception:
                    pass

        for _ in range(10):
            active = win32gui.GetForegroundWindow()
            if active and matches(win32gui.GetWindowText(active)):
                return True
            try:
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                      win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
                win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                      win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
            time.sleep(0.1)
        return False
    except Exception:
        pass
    try:
        return WindowManager().focus_window(title, exact=exact)
    except Exception:
        return False


@feature(
    name="list_windows",
    display_name="列出窗口",
    description="【系统·窗口列表】列出当前所有可见窗口的标题。用于查找目标窗口标题或监控窗口变化",
    category=F.SYSTEM,
    params=[],
    returns="list[str] - 窗口标题列表",
)
def list_windows() -> list:
    return _get_visible_window_titles()


def _get_visible_window_titles() -> list[str]:
    """Return visible window titles with a Win32 fallback."""
    try:
        titles = WindowManager().get_all_window_titles()
        if titles:
            return titles
    except Exception:
        pass
    try:
        import win32gui
        titles = []
        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).strip()
            if title:
                titles.append(title)
        win32gui.EnumWindows(cb, None)
        return titles
    except Exception:
        return []


@feature(
    name="get_active_window",
    display_name="当前窗口信息",
    description="【系统·当前窗口】获取当前活动窗口的信息（标题、位置、大小）。用于确认当前操作上下文或监控窗口变化",
    category=F.SYSTEM,
    params=[],
    returns="dict{title, left, top, width, height}",
)
def get_active_window() -> dict:
    try:
        import win32gui
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            title = win32gui.GetWindowText(hwnd)
            rect = win32gui.GetWindowRect(hwnd)
            return {
                "title": title,
                "left": rect[0],
                "top": rect[1],
                "width": rect[2] - rect[0],
                "height": rect[3] - rect[1],
            }
    except Exception:
        pass
    try:
        result = WindowManager().get_active_window()
        return result or {"error": "无法获取窗口信息"}
    except ImportError:
        return {"error": "窗口管理模块不可用"}


def _find_matching_pids(exe_name: str, target_name: str = "") -> set:
    """查找匹配的进程 PID，使用多重策略适配 Electron 多进程应用。"""
    import psutil
    exe_lower = exe_name.lower()
    target_lower = target_name.lower().strip()
    base = exe_lower.replace(".exe", "")

    pids: set = set()
    for p in psutil.process_iter(["pid", "name"]):
        try:
            pname = (p.info["name"] or "").lower()
            # 1. Exact match
            if pname == exe_lower:
                pids.add(p.info["pid"])
                continue
            pname_stripped = pname.replace(".exe", "")
            # 2. Base is substring of process name (QQ.exe → QQNT.exe)
            if len(base) >= 2 and base in pname:
                pids.add(p.info["pid"])
                continue
            # 3. Process name is substring of base (QQNT.exe → QQ.exe)
            if len(pname_stripped) >= 2 and pname_stripped in base:
                pids.add(p.info["pid"])
                continue
            # 4. Target name matches process name (e.g. "QQ" in "QQ.exe")
            if len(target_lower) > 1 and target_lower in pname:
                pids.add(p.info["pid"])
        except Exception:
            continue
    return pids


def _try_focus_existing(exe_name: str, target_name: str = "") -> bool:
    """尝试查找已运行程序的窗口并聚焦。多重匹配策略。

    对 Electron 多进程应用（QQ、微信等）可匹配不同子进程名。
    """
    try:
        import win32gui, win32con, win32process
        pids = _find_matching_pids(exe_name, target_name)
        if not pids:
            return False

        found = False
        def enum_cb(hwnd, ctx):
            nonlocal found
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid not in ctx["pids"]:
                    return
                r = win32gui.GetWindowRect(hwnd)
                w, h = int(r[2]-r[0]), int(r[3]-r[1])
                if w > 200 and h > 100 and w < 5000 and h < 5000:
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    win32gui.SetForegroundWindow(hwnd)
                    found = True
            except Exception:
                pass
        win32gui.EnumWindows(enum_cb, {"pids": pids})
        return found
    except Exception:
        return False


@feature(
    name="launch_program",
    display_name="启动程序",
    description="【系统·启动】启动可执行程序或打开文件/URL。如果程序已在运行，会自动聚焦其窗口避免重复启动",
    category=F.SYSTEM,
    params=[
        P("target", "str", "程序路径、文件名（如 notepad.exe）、文档或 URL", example="notepad.exe"),
        P("args", "str", "命令行参数（可选）", required=False, default=""),
        P("wait", "boolean", "是否等待程序退出", required=False, default=False),
        P("force_new", "boolean", "即使程序已运行也强制启动新实例", required=False, default=False),
    ],
    returns="dict{success, pid} - 启动结果",
    tags=["system", "process", "launch"],
    hidden=False,
)
def launch_program(target: str, args: str = "", wait: bool = False, force_new: bool = False) -> dict:
    try:
        import subprocess, os, re, time

        # Resolve via program registry first (PATH + App Paths + cached aliases)
        from features.program_registry import get_program_registry
        reg = get_program_registry()  # 确保注册表已加载
        resolved = reg.resolve(target) or target
        before_titles = set(_get_visible_window_titles())

        # If the name wasn't resolved and looks like Chinese text, give a helpful hint
        if resolved == target and re.search(r'[一-鿿]', target):
            all_items = reg.get_name_list()
            close = [i for i in all_items if target.lower() in i["name"] or i["name"] in target.lower()][:5]
            hint = ""
            if close:
                    hint = f"，你是不是想启动: {' / '.join(f'「{c["name"]}」' for c in close)}"
            return {"success": False, "error": f"未找到程序「{target}」{hint}"}

        # If resolved target is a URL, open it with the default browser
        if "://" in resolved:
            os.startfile(resolved)
            return {"success": True, "pid": None, "resolved_path": resolved}

        # Non-executable path - return error instead of calling os.startfile
        # (os.startfile on an unresolvable program name opens Microsoft Store on Win11)
        if not any(resolved.lower().endswith(ext) for ext in (".exe", ".com", ".bat", ".cmd", ".msi")):
            return {"success": False, "error": f"程序「{target}」解析为非可执行文件: {resolved}，请使用完整 .exe 路径"}

        # 防重复启动：程序已在运行且非强制新实例 → 聚焦现有窗口
        if not force_new and not args:
            exe_name = os.path.basename(resolved)
            if _try_focus_existing(exe_name, target):
                logger.info("程序 %s 已在运行，已聚焦窗口", exe_name)
                return {"success": True, "focused_existing": True, "resolved_path": resolved}
            title_candidates = []
            for cand in [target, resolved, exe_name, os.path.splitext(exe_name)[0]]:
                cand = (cand or "").strip()
                if cand and cand not in title_candidates:
                    title_candidates.append(cand)
            try:
                titles = _get_visible_window_titles()
                for title in titles:
                    low = title.lower()
                    if any((c.lower() in low or low in c.lower()) for c in title_candidates if c):
                        try:
                            import win32gui, win32con
                            def enum_cb(hwnd, _):
                                if not win32gui.IsWindowVisible(hwnd):
                                    return
                                if win32gui.GetWindowText(hwnd).strip() == title:
                                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                                    try:
                                        win32gui.SetForegroundWindow(hwnd)
                                    except Exception:
                                        pass
                            win32gui.EnumWindows(enum_cb, None)
                        except Exception:
                            pass
                        return {"success": True, "focused_existing": True, "resolved_path": resolved, "window_title": title}
            except Exception:
                pass

        full_args = [resolved] + (args.split() if args else [])
        proc = subprocess.Popen(full_args, shell=False)
        if wait:
            proc.wait()
        result = {"success": True, "pid": proc.pid, "resolved_path": resolved}

        window_title = ""
        try:
            import win32gui, win32con, win32process
            start_time = time.time()
            while time.time() - start_time < 5.0:
                found_hwnd = None
                def enum_cb(hwnd, _):
                    nonlocal found_hwnd
                    if found_hwnd is not None:
                        return
                    if not win32gui.IsWindowVisible(hwnd):
                        return
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    except Exception:
                        return
                    if pid != proc.pid:
                        return
                    title = win32gui.GetWindowText(hwnd).strip()
                    if title:
                        found_hwnd = hwnd
                try:
                    win32gui.EnumWindows(enum_cb, None)
                except Exception:
                    pass
                if found_hwnd is not None:
                    try:
                        win32gui.ShowWindow(found_hwnd, win32con.SW_RESTORE)
                        try:
                            win32gui.SetForegroundWindow(found_hwnd)
                        except Exception:
                            pass
                        window_title = win32gui.GetWindowText(found_hwnd).strip()
                    except Exception:
                        pass
                    break
                time.sleep(0.25)
        except Exception:
            pass
        if window_title:
            result["window_title"] = window_title
            result["visible_window"] = True
        else:
            result["visible_window"] = False
            result["verify_pending"] = True
        return result
    except FileNotFoundError:
        try:
            import subprocess
            proc = subprocess.Popen([target] + (args.split() if args else []), shell=True)
            if wait:
                proc.wait()
            return {"success": True, "pid": proc.pid, "fallback": "shell", "resolved_path": target}
        except Exception as e2:
            return {"success": False, "error": f"无法启动 {target}，尝试 shell fallback 失败: {e2}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@feature(
    name="run_command",
    display_name="运行命令",
    description="【系统·命令】运行 shell 命令并捕获输出（通过 cmd.exe，支持管道和重定向）。用于执行系统命令、脚本和自动化操作。注意：命令通过 shell 执行，请确保输入可信",
    category=F.SYSTEM,
    params=[
        P("command", "str", "要执行的命令", example="echo hello"),
        P("timeout", "number", "超时秒数", required=False, default=30),
    ],
    returns="dict{success, stdout, stderr, returncode}",
    tags=["system", "shell", "command"],
)
def run_command(command: str, timeout: float = 30) -> dict:
    try:
        import subprocess
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"命令超时 ({timeout}s)", "stdout": "", "stderr": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}
