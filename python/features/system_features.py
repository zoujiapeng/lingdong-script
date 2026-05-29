"""
系统功能注册
进程检测 / 窗口管理 / 剪贴板输入
"""
import logging
import time

from features.registry import feature, P, TC, FeatureCategory as F

logger = logging.getLogger(__name__)


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
    hidden=True,
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
    hidden=True,
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
        from system.window import WindowManager
        return WindowManager().focus_window(title, exact=exact)
    except ImportError:
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
    try:
        from system.window import WindowManager
        return WindowManager().get_all_window_titles()
    except ImportError:
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
        from system.window import WindowManager
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
            if len(base) >= 3 and base in pname:
                pids.add(p.info["pid"])
                continue
            # 3. Process name is substring of base (QQNT.exe → QQ.exe)
            if len(pname_stripped) >= 3 and pname_stripped in base:
                pids.add(p.info["pid"])
                continue
            # 4. Target name matches process name (e.g. "QQ" in "QQ.exe")
            if len(target_lower) > 2 and target_lower in pname:
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
        import subprocess, os, re

        # Resolve via program registry first (PATH + App Paths + cached aliases)
        from features.program_registry import get_program_registry
        reg = get_program_registry()  # 确保注册表已加载
        resolved = reg.resolve(target) or target

        # If the name wasn't resolved and looks like Chinese text, give a helpful hint
        if resolved == target and re.search(r'[一-鿿]', target):
            all_items = reg.get_name_list()
            close = [i for i in all_items if target.lower() in i["name"] or i["name"] in target.lower()][:5]
            hint = ""
            if close:
                hint = f"，你是不是想启动: {' / '.join(f'『{c["name"]}』' for c in close)}"
            return {"success": False, "error": f"未找到程序「{target}」{hint}"}

        # If resolved target is a URL, open it with the default browser
        if "://" in resolved:
            os.startfile(resolved)
            return {"success": True, "pid": None, "resolved_path": resolved}

        # Non-executable path — return error instead of calling os.startfile
        # (os.startfile on an unresolvable program name opens Microsoft Store on Win11)
        if not any(resolved.lower().endswith(ext) for ext in (".exe", ".com", ".bat", ".cmd", ".msi")):
            return {"success": False, "error": f"程序「{target}」解析为非可执行文件: {resolved}，请使用完整 .exe 路径"}

        # 防重复启动：程序已在运行且非强制新实例 → 聚焦现有窗口
        if not force_new and not args:
            exe_name = os.path.basename(resolved)
            if _try_focus_existing(exe_name, target):
                logger.info("程序 %s 已在运行，已聚焦窗口", exe_name)
                return {"success": True, "focused_existing": True, "resolved_path": resolved}

        full_args = [resolved] + (args.split() if args else [])
        proc = subprocess.Popen(full_args, shell=False)
        if wait:
            proc.wait()
        return {"success": True, "pid": proc.pid, "resolved_path": resolved}
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
