"""
Meta-Actions — Computer Use 风格的高层动作层

默认只暴露 ~22 个元动作，每个内部路由到最合适的 @feature 工具。
流程图模式 (generate_flow) 默认隐藏，通过 use_flow_chart=True 开启。

架构：
  LLM 看到 → 元动作 (click, type_text, manage_window...)
  系统执行 → 路由到 @feature 工具 (click_target, a11y_click_element, hotkey...)
"""

from __future__ import annotations
import time
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger("agent.meta_actions")

# ── 元动作注册表 ──────────────────────────────────────────────

MetaHandler = Callable[..., dict]


def _registry():
    """延迟获取 registry（模块级缓存，首次 import 后复用）。"""
    if hasattr(_registry, '_cache'):
        return _registry._cache
    from features.registry import registry
    import features._all  # noqa
    # 缓存到函数属性，避免全局变量污染
    _registry._cache = registry
    return registry


# ── 核心桌面动作 ──────────────────────────────────────────────

def click(target: str = "", x: int = 0, y: int = 0,
          button: str = "left", region: str = "", clicks: int = 1,
          timeout: float = 5) -> dict:
    """Click at a target on screen.

    target: text description like "OK", "Save", or element name.
            If empty, uses x,y coordinates.
    button: 'left', 'right', 'middle', 'double'
    region: optional 'x,y,w,h' to limit search area
    """
    r = _registry()

    # Resolve target to coordinates
    tx, ty = x, y
    if target:
        # Try given as coordinates first
        if re.match(r'^-?\d+\s*,\s*-?\d+$', target.strip()):
            parts = target.strip().split(",")
            tx, ty = int(parts[0]), int(parts[1])
            target = ""

    if not target and tx == 0 and ty == 0:
        return {"success": False, "error": "Need target description or coordinates"}
    if not target and tx != 0 and ty != 0:
        # Direct coordinate click
        coord_str = f"{tx},{ty}"
        btn_map = {"left": "click_target", "right": "right_click",
                    "middle": "middle_click", "double": "click_target"}
        tool = btn_map.get(button, "click_target")
        kw = {"target": coord_str}
        if region:
            kw["region"] = region
        if button == "double" or clicks >= 2:
            kw["double"] = True
        return r.execute(tool, **kw)

    # Text target: try UIA first, then OCR-based click
    if button == "right":
        return r.execute("right_click", target=target, region=region)
    if button == "middle":
        return r.execute("middle_click", target=target)
    kw = {"target": target}
    if region:
        kw["region"] = region
    if button == "double" or clicks >= 2:
        kw["double"] = True
    if timeout:
        kw["timeout"] = timeout
    return r.execute("click_target", **kw)


def type_text(text: str, target: str = "",
              clear_first: bool = True, interval: float = 0) -> dict:
    """Type text onto screen.

    text: text to type
    target: optional — click here first before typing
    clear_first: clear existing text before typing
    """
    r = _registry()

    if target:
        click_result = click(target=target, timeout=3)
        if not click_result.get("success"):
            return click_result
        time.sleep(0.3)

    # Prefer clipboard paste for Chinese/special characters
    has_chinese = any('一' <= c <= '鿿' for c in text)
    if has_chinese:
        result = r.execute("type_clipboard", text=text)
        if result.get("success"):
            return result

    # Fallback to char-by-char typing
    kw = {"text": text, "clear_first": clear_first}
    if interval > 0:
        kw["interval"] = interval
    return r.execute("type_text", **kw)


def press_keys(keys: str) -> dict:
    """Press keyboard shortcut. Examples: 'ctrl+s', 'alt+tab', 'esc', 'ctrl+c'."""
    r = _registry()
    return r.execute("hotkey", keys=keys)


def scroll(direction: str = "down", amount: int = 3,
           target: str = "", x: int = 0, y: int = 0) -> dict:
    """Scroll at target location.

    direction: 'down', 'up', 'left', 'right'
    amount: scroll clicks or pixels
    """
    r = _registry()
    kw = {"direction": direction, "amount": amount}
    if target:
        kw["target"] = target
    if x or y:
        kw["x"], kw["y"] = x, y
    return r.execute("scroll", **kw)


def hover(target: str, duration: float = 0.5) -> dict:
    """Hover cursor over an element."""
    r = _registry()
    return r.execute("hover", target=target, duration=duration)


def drag(from_target: str, to_target: str, duration: float = 0.3) -> dict:
    """Drag from one position to another."""
    r = _registry()
    return r.execute("drag", from_target=from_target,
                     to_target=to_target, duration=duration)


# ── 屏幕读取 ──────────────────────────────────────────────────

def read_text(target: str = "", region: str = "") -> dict:
    """Read text from screen using OCR.

    target: if provided, search for specific text and return its location
    region: optional 'x,y,w,h' to limit search area
    """
    r = _registry()
    if target:
        return r.execute("ocr_find_text", query=target, region=region)
    return r.execute("ocr_extract_all", region=region)


def read_ui(max_depth: int = 5) -> dict:
    """Read UI accessibility tree of active window."""
    r = _registry()
    return r.execute("a11y_get_tree", max_depth=max_depth)


def screenshot(region: str = "", format: str = "jpeg", quality: int = 50) -> dict:
    """Take a screenshot.

    region: optional 'x,y,w,h'. Empty = full screen
    """
    r = _registry()
    kw = {"format": format, "quality": quality}
    if region:
        kw["region"] = region
    return r.execute("screenshot", **kw)


def get_cursor() -> dict:
    """Get current mouse cursor position and window under cursor."""
    r = _registry()
    return r.execute("get_cursor_pos")


# ── 窗口管理 ──────────────────────────────────────────────────

def manage_window(action: str, title: str = "",
                  width: int = 0, height: int = 0,
                  position: str = "") -> dict:
    """Manage windows.

    action: 'focus' | 'minimize' | 'maximize' | 'restore' |
            'close' | 'resize' | 'snap'
    title: window title (empty = active window)
    """
    r = _registry()
    action = action.lower()

    if action == "focus":
        if not title:
            return {"success": False, "error": "manage_window(action='focus') 需要 title 参数，如 title='计算器'"}
        return r.execute("focus_window", title=title)
    if action == "minimize":
        return r.execute("window_minimize", title=title)
    if action == "maximize":
        return r.execute("window_maximize", title=title)
    if action == "restore":
        return r.execute("window_restore", title=title)
    if action == "close":
        return r.execute("window_close", title=title)
    if action == "resize":
        return r.execute("window_resize", width=width,
                         height=height, title=title)
    if action == "snap":
        return r.execute("window_snap", position=position, title=title)

    return {"success": False, "error": f"Unknown action: {action}"}


def list_windows() -> dict:
    """List all visible windows."""
    r = _registry()
    return r.execute("list_windows")


# ── 应用管理 ──────────────────────────────────────────────────

# 常用程序名对照表（中文名 → exe名，容忍 LLM 不传准确 exe 名）
_COMMON_APPS = {
    "calc": "calc.exe", "calculator": "calc.exe", "计算器": "calc.exe",
    "notepad": "notepad.exe", "记事本": "notepad.exe",
    "mspaint": "mspaint.exe", "paint": "mspaint.exe", "画图": "mspaint.exe",
    "cmd": "cmd.exe", "命令提示符": "cmd.exe",
    "powershell": "powershell.exe",
    "chrome": "chrome.exe", "google chrome": "chrome.exe", "浏览器": "chrome.exe",
    "msedge": "msedge.exe", "edge": "msedge.exe",
    "explorer": "explorer.exe", "文件资源管理器": "explorer.exe",
    "taskmgr": "taskmgr.exe", "任务管理器": "taskmgr.exe",
    "control": "control.exe", "控制面板": "control.exe",
    "regedit": "regedit.exe", "注册表": "regedit.exe",
    "winver": "winver.exe", "关于": "winver.exe",
    "snippingtool": "SnippingTool.exe", "截图": "SnippingTool.exe",
    "magnifier": "Magnify.exe", "放大镜": "Magnify.exe",
}


def _resolve_app(target: str) -> str:
    """将中文名/简写解析为 exe 文件名"""
    if not target:
        return ""
    # 已经是 exe 格式
    if target.lower().endswith(".exe"):
        return target
    # 查别名表
    t = target.lower().strip()
    if t in _COMMON_APPS:
        return _COMMON_APPS[t]
    # 尝试直接加 .exe
    exe_name = f"{t}.exe" if "." not in t else t
    return exe_name


def manage_app(action: str, target: str = "", force: bool = False) -> dict:
    """Manage applications.

    action: 'launch' | 'kill' | 'detect'
    target: program name like 'notepad.exe', 'calc.exe'
    """
    r = _registry()

    if action == "launch":
        exe = _resolve_app(target)
        if not exe:
            return {"success": False, "error": "manage_app(action='launch') 需要 target 参数，如 target='notepad.exe'"}

        # ── 检测进程是否已在运行 → 不重复启动 ──
        try:
            detect_r = r.execute("detect_process", name=exe)
            det_ok = detect_r.get("result", detect_r.get("success", False))
            if isinstance(det_ok, bool) and det_ok:
                return {
                    "success": True, "skipped": "already_running",
                    "message": f"「{target}」已在运行。请用 manage_window(action='focus', title='...') 聚焦窗口后继续操作。"
                }
        except Exception:
            pass

        # ── 启动 ──
        return r.execute("launch_program", target=exe)
    if action == "kill":
        exe = _resolve_app(target)
        if not exe:
            return {"success": False, "error": "manage_app(action='kill') 需要 target 参数，如 target='notepad.exe'"}
        return r.execute("kill_process", name=exe, force=force)
    if action == "detect":
        exe = _resolve_app(target)
        if not exe:
            return {"success": False, "error": "manage_app(action='detect') 需要 target 参数"}
        return r.execute("detect_process", name=exe)

    return {"success": False, "error": f"Unknown action: {action}"}


# ── 系统控制 ──────────────────────────────────────────────────

def control_audio(action: str = "set", level: float = 50) -> dict:
    """Control system audio.

    action: 'set' (volume 0-100) or 'mute' (toggle)
    level: volume level 0-100 (for 'set' action)
    """
    r = _registry()
    if action == "set":
        return r.execute("volume_set", level=level)
    if action == "mute":
        return r.execute("volume_mute")
    return {"success": False, "error": f"Unknown action: {action}"}


def system_info(query: str = "all") -> dict:
    """Get system information.

    query: 'battery' | 'network' | 'all'
    """
    r = _registry()
    results = {}

    if query in ("battery", "all"):
        try:
            results["battery"] = r.execute("battery_status")
        except Exception as e:
            results["battery"] = {"error": str(e)}

    if query in ("network", "all"):
        try:
            results["network"] = r.execute("network_status")
        except Exception as e:
            results["network"] = {"error": str(e)}

    return {"success": True, "result": results} if results else \
           {"success": False, "error": f"Unknown query: {query}"}


def set_brightness(level: float) -> dict:
    """Set display brightness (0-100). For laptop displays."""
    r = _registry()
    return r.execute("display_brightness", level=level)


def lock_screen() -> dict:
    """Lock the workstation (equivalent to Win+L)."""
    r = _registry()
    return r.execute("lock_workstation")


def notify(title: str = "Task Complete", message: str = "") -> dict:
    """Send Windows taskbar notification."""
    r = _registry()
    return r.execute("send_notification", title=title, message=message)


# ── 对话框 ────────────────────────────────────────────────────

def dialog(action: str = "close", path: str = "", timeout: float = 5) -> dict:
    """Handle dialogs.

    action: 'close' to auto-close popups, 'save' to fill save dialog
    path: file path for 'save' action
    """
    r = _registry()
    if action == "close":
        return r.execute("handle_dialog", timeout=timeout)
    if action == "save":
        return r.execute("fill_save_dialog", filepath=path, timeout=timeout)
    return {"success": False, "error": f"Unknown dialog action: {action}"}


# ── 网络 ──────────────────────────────────────────────────────

def network(action: str = "status", ssid: str = "", password: str = "") -> dict:
    """Manage network.

    action: 'status' | 'scan_wifi' | 'connect_wifi' | 'disconnect_wifi'
    """
    r = _registry()
    if action == "status":
        return r.execute("network_status")
    if action == "scan_wifi":
        return r.execute("wifi_list")
    if action == "connect_wifi":
        return r.execute("wifi_connect", ssid=ssid, password=password)
    if action == "disconnect_wifi":
        return r.execute("wifi_disconnect")
    return {"success": False, "error": f"Unknown network action: {action}"}


# ── 工具 ──────────────────────────────────────────────────────

def run_command(command: str, timeout: float = 30) -> dict:
    """Execute a shell command and return output."""
    r = _registry()
    return r.execute("run_command", command=command, timeout=timeout)


def run_python_script(script: str, timeout: int = 120) -> dict:
    """Execute Python code directly. Uses subprocess for isolation.

    Pre-installed: pywinauto, pyautogui, psutil, os, subprocess, time, json, math, random.
    """
    import subprocess, tempfile, os, time, sys
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix="lg_script_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(script)
        except Exception:
            os.unlink(tmp_path)
            return {"success": False, "error": "failed to write script"}

        start = time.time()
        proc = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        elapsed = time.time() - start
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        success = proc.returncode == 0

        return {
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
            "elapsed": round(elapsed, 1),
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Script timed out after {timeout}s"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def file_ops(action: str, path: str, content: str = "",
             pattern: str = "", recursive: bool = False) -> dict:
    """File operations.

    action: 'read' | 'write' | 'list' | 'exists' | 'delete' | 'copy' | 'move'
    path: file path
    content: file content (for 'write')
    pattern: glob pattern (for 'list')
    """
    r = _registry()
    if action == "read":
        return r.execute("file_read", path=path)
    if action == "write":
        return r.execute("file_write", path=path, content=content)
    if action == "list":
        kw = {"path": path}
        if pattern:
            kw["pattern"] = pattern
        kw["recursive"] = recursive
        return r.execute("file_list", **kw)
    if action == "exists":
        return r.execute("file_exists", path=path)
    if action == "delete":
        return r.execute("file_delete", path=path)
    if action == "copy":
        return r.execute("file_copy", src=path, dst=content)
    if action == "move":
        return r.execute("file_move", src=path, dst=content)
    return {"success": False, "error": f"Unknown file action: {action}"}


def wait(seconds: float = 1.0) -> dict:
    """Wait for specified seconds. Use between actions to let UI settle."""
    import time
    time.sleep(seconds)
    return {"success": True, "result": {"waited": seconds}}


# ── 浏览器操作 ───────────────────────────────────────────────

def browser_do(action: str, url: str = "", text: str = "",
               selector: str = "", timeout: float = 10) -> dict:
    """Browser automation — interact with web pages via CDP.

    action: 'navigate' | 'click' | 'type' | 'read' | 'screenshot' |
            'back' | 'close' | 'refresh' | 'get_url'
    url: target URL (for 'navigate')
    text: text to type (for 'type')
    selector: CSS selector (for 'click'/'type'/'read')
    """
    r = _registry()

    if action == "navigate":
        r.execute("browser_connect")
        return r.execute("browser_navigate", url=url)
    if action == "click":
        r.execute("browser_connect")
        return r.execute("browser_click", selector=selector)
    if action == "type":
        r.execute("browser_connect")
        return r.execute("browser_type", selector=selector, text=text)
    if action == "read":
        r.execute("browser_connect")
        return r.execute("browser_get_text", selector=selector)
    if action == "screenshot":
        r.execute("browser_connect")
        return r.execute("browser_screenshot")
    if action == "back":
        r.execute("browser_connect")
        return r.execute("browser_go_back")
    if action == "close":
        r.execute("browser_connect")
        return r.execute("browser_close")
    if action == "refresh":
        r.execute("browser_connect")
        return r.execute("browser_refresh")
    if action == "get_url":
        r.execute("browser_connect")
        return r.execute("browser_get_url")

    return {"success": False, "error": f"Unknown browser action: {action}"}


# ── 流程图模式（默认隐藏）────────────────────────────────────────

_FLOW_CHART_ENABLED = False


def set_flow_chart_mode(enabled: bool):
    """Enable/disable flow chart meta-actions."""
    global _FLOW_CHART_ENABLED
    _FLOW_CHART_ENABLED = enabled


def generate_flow(goal: str) -> dict:
    """[Flow Chart] Generate a visual flow chart from a task description.
    This creates a reusable automated workflow in the frontend.
    """
    r = _registry()
    return r.execute("generate_flow_script", goal=goal)


def execute_flow(script: str) -> dict:
    """[Flow Chart] Execute a flow chart script (DSL text or FlowScript JSON)."""
    r = _registry()
    # Try as DSL text first
    if script.strip().startswith("{"):
        return r.execute("software_load_script", script=script)
    return r.execute("run_dsl", dsl=script)


# ── 元动作注册表 ──────────────────────────────────────────────

# 不需要执行后截图的元动作（读取类/纯工具类操作，屏幕不会变化）
NO_SS_AFTER_META = frozenset({
    "read_text", "read_ui", "screenshot", "get_cursor",
    "list_windows", "control_audio", "system_info",
    "set_brightness", "lock_screen", "notify",
    "network", "file_ops", "wait",
    "generate_flow", "execute_flow",
})

# Each meta-action: (description, params_schema, handler_fn, expert)
# expert=True means hidden in default mode (only shown when flow_chart enabled)
META_ACTIONS: dict[str, tuple] = {}

# Core desktop actions
META_ACTIONS["click"] = (
    "点击屏幕上的目标。普通按钮用 click(target='按钮名') 即可。"
    "对于列表中的项目（联系人、文件、聊天记录等），单击通常只选中不打开，需要用 button='double' 双击才能进入。"
    "target 可以是按钮文字('确认'、'保存')、元素名称、截图编号('#3')或坐标'100,200'。"
    "桌面背景、画布空白等没有控件名的区域，只能用 x,y 坐标点击，根据截图估算位置。"
    "自动用 UIA 无障碍树 → OCR 文字识别 → 图像匹配定位目标。如果目标唯一可以直接点，"
    "如果多个同名目标用 region 参数限定搜索区域。"
    "常用: click(target='确认'), click(target='#3'), click(x=500,y=400), click(target='联系人名', button='double')",
    {
        "target": "str - 目标文字/元素名/截图编号#N/坐标'x,y'",
        "button": "str? - 鼠标键: 'left'(默认), 'right'右键, 'middle'中键, 'double'双击",
        "region": "str? - 限定搜索区域 'x,y,w,h'，多目标时使用",
    },
    click,
)

META_ACTIONS["type_text"] = (
    "向当前焦点输入框输入文字。先点击目标输入框再输入。中文会自动用剪贴板粘贴(绕过输入法)。"
    "clear_first=True 会先清空已有内容。"
    "常用: type_text(text='Hello World'), type_text(text='搜索关键词', target='搜索框', clear_first=True)",
    {
        "text": "str - 要输入的文字内容",
        "target": "str? - 先点击此目标再输入（输入框的描述）",
        "clear_first": "bool? - 输入前先清空已有内容（默认true）",
    },
    type_text,
)

META_ACTIONS["press_keys"] = (
    "按下键盘快捷键或组合键。常用: 'ctrl+s'保存, 'alt+tab'切换窗口, 'ctrl+c'复制, 'ctrl+v'粘贴, "
    "'esc'取消/关闭, 'alt+f4'关闭窗口, 'ctrl+a'全选, 'win+d'显示桌面, "
    "'enter'确认, 'tab'切换焦点, 'ctrl+z'撤销, 'ctrl+shift+z'重做。"
    "多个键用 + 连接。",
    {"keys": "str - 快捷键组合，如 'ctrl+s', 'alt+tab', 'esc'"},
    press_keys,
)

META_ACTIONS["scroll"] = (
    "在屏幕指定位置滚动鼠标滚轮。direction方向: 'down'向下(默认), 'up'向上, 'left'向左, 'right'向右。"
    "amount滚动格数(默认3格)。target指定滚动位置(文字描述或坐标)，不填则在当前鼠标位置滚动。"
    "常用: scroll(direction='down'), scroll(direction='down', target='设置页面', amount=5)",
    {
        "direction": "str? - 滚动方向: 'down'(默认), 'up', 'left', 'right'",
        "amount": "int? - 滚动格数（默认3）",
        "target": "str? - 滚动位置（文字描述或坐标，不填则当前鼠标位置）",
    },
    scroll,
)

META_ACTIONS["hover"] = (
    "将鼠标悬停在目标元素上（不点击）。用于触发 tooltip 提示、hover 样式效果或预览链接。"
    "常用: hover(target='菜单项')",
    {
        "target": "str - 悬停目标（文字描述）",
        "duration": "num? - 悬停秒数（默认0.5）",
    },
    hover,
)

META_ACTIONS["drag"] = (
    "从起始位置拖拽到目标位置。from_target 和 to_target 可以是文字描述或坐标 'x,y'。"
    "用于拖拽文件、移动窗口、调整滑块。"
    "常用: drag(from_target='文件图标', to_target='目标文件夹')",
    {
        "from_target": "str - 起始位置（文字描述或坐标）",
        "to_target": "str - 目标位置（文字描述或坐标）",
        "duration": "num? - 拖动耗时秒数（默认0.3）",
    },
    drag,
)

# Screen reading
META_ACTIONS["read_text"] = (
    "用 OCR 读取屏幕上的文字。如果提供 target 参数，会查找特定文字并返回坐标；如果 target 为空，返回屏幕上所有文字。"
    "用 region 参数限定识别区域。先 read_ui 看控件树，再 read_text 找具体文字。"
    "常用: read_text() 读取全部文字, read_text(target='错误提示') 找特定文字位置, read_text(region='100,200,500,300') 限区域",
    {
        "target": "str? - 要查找的特定文字（找到返回坐标），空则返回全部文字",
        "region": "str? - 限定识别区域 'x,y,w,h'",
    },
    read_text,
)

META_ACTIONS["read_ui"] = (
    "读取当前活动窗口的无障碍控件树(UIA)，返回按钮、输入框、列表等元素的层级结构、名称和位置。"
    "比 OCR 更精确，能获取标准 Windows 控件的准确名称(按钮、输入框、列表等)。"
    "点击前先用 read_ui 查看目标控件的准确名称。"
    "常用: read_ui(), read_ui(max_depth=3)",
    {"max_depth": "int? - 控件树深度（默认5，越大信息越多）"},
    read_ui,
)

META_ACTIONS["screenshot"] = (
    "截取当前屏幕。region 为空截取全屏，指定 region 截取部分区域。"
    "用于视觉验证界面状态或提供给多模态模型分析。"
    "常用: screenshot(), screenshot(region='0,0,500,800')",
    {"region": "str? - 截取区域 'x,y,w,h'，空=全屏"},
    screenshot,
)

META_ACTIONS["get_cursor"] = (
    "获取鼠标当前屏幕坐标(x, y)和所在窗口的标题。点击前先获取鼠标位置确认位置正确。"
    "常用: get_cursor()",
    {},
    get_cursor,
)

# Window management
META_ACTIONS["manage_window"] = (
    "窗口操作：聚焦、最小化、最大化、还原、关闭、调整大小、贴边。"
    "title 为空时操作当前活动窗口。"
    "【重要】action='focus' 时必须提供 title。"
    "常用: manage_window(action='focus', title='计算器') 聚焦窗口, "
    "manage_window(action='close') 关闭当前窗口, "
    "manage_window(action='minimize', title='记事本') 最小化, "
    "manage_window(action='snap', position='left') 左半屏贴边",
    {
        "action": "str - 操作: 'focus'聚焦, 'minimize'最小化, 'maximize'最大化, 'restore'还原, 'close'关闭, 'resize'调整大小, 'snap'贴边",
        "title": "str? - 窗口标题（action='focus' 时此参数必需）",
        "width": "int? - 调整宽度（用于 'resize'）",
        "height": "int? - 调整高度（用于 'resize'）",
        "position": "str? - 贴边位置（用于 'snap'）: 'left', 'right', 'top', 'bottom'",
    },
    manage_window,
)

META_ACTIONS["list_windows"] = (
    "列出所有可见窗口的标题。启动应用前检查是否已在运行，或查找窗口标题用于 manage_window。"
    "常用: list_windows()",
    {},
    list_windows,
)

# App management
META_ACTIONS["manage_app"] = (
    "启动、终止或检测应用程序。'launch'启动程序(未运行时)或聚焦已运行的窗口。"
    "'kill'终止进程,'force=true'强制终止。'detect'检查进程是否在运行。"
    "【重要】target 是必需的参数，必须指定程序名。"
    "常用: manage_app(action='launch', target='notepad.exe') 启动记事本, "
    "manage_app(action='detect', target='chrome.exe') 检查Chrome是否在运行",
    {
        "action": "str - 操作: 'launch'启动, 'kill'终止, 'detect'检测",
        "target": "str - 必需。程序名如 'notepad.exe', 'calc.exe', 'chrome.exe'",
        "force": "bool? - 强制终止（用于 'kill'）",
    },
    manage_app,
)

# System
META_ACTIONS["control_audio"] = (
    "控制系统音量。'set'设置音量(0-100), 'mute'静音切换。"
    "常用: control_audio(action='set', level=50) 设音量50%, control_audio(action='mute') 静音切换",
    {
        "action": "str? - 'set'设置音量(默认) 或 'mute'静音切换",
        "level": "num? - 音量 0-100（用于 'set'）",
    },
    control_audio,
)

META_ACTIONS["system_info"] = (
    "获取系统信息。'battery'电池电量百分比和充电状态, 'network'网络连接类型和IP, 'all'全部信息。"
    "常用: system_info(query='battery') 查电量, system_info(query='all') 查全部",
    {"query": "str? - 'battery'(默认)电池, 'network'网络, 'all'全部"},
    system_info,
)

META_ACTIONS["set_brightness"] = (
    "设置笔记本屏幕亮度百分比(0-100)。仅在笔记本上有效。"
    "常用: set_brightness(level=70)",
    {"level": "num - 亮度百分比 0-100"},
    set_brightness,
)

META_ACTIONS["lock_screen"] = (
    "立即锁定工作站(等同于Win+L)。离开时使用，无需参数。"
    "常用: lock_screen()",
    {},
    lock_screen,
)

META_ACTIONS["browser_do"] = (
    "浏览器自动化(Chrome CDP)。自动连接已打开的Chrome。'navigate'打开URL, "
    "'click'用CSS选择器点击元素, 'type'输入文字, 'read'读取元素文本, "
    "'screenshot'截取页面, 'back'后退, 'close'关闭, 'refresh'刷新, 'get_url'获取当前URL。"
    "常用: browser_do(action='navigate', url='https://www.baidu.com'), "
    "browser_do(action='click', selector='#su'), "
    "browser_do(action='type', selector='#kw', text='搜索词')",
    {
        "action": "str - 操作: 'navigate'导航, 'click'点击, 'type'输入, 'read'读取, 'screenshot'截图, 'back'后退, 'close'关闭, 'refresh'刷新, 'get_url'获取URL",
        "url": "str? - 目标URL（用于 'navigate'）",
        "selector": "str? - CSS选择器（用于 'click'/'type'/'read'）",
        "text": "str? - 要输入的文字（用于 'type'）",
    },
    browser_do,
)

META_ACTIONS["notify"] = (
    "在Windows任务栏托盘区域发送通知。用于任务完成提醒或告警。"
    "常用: notify(title='完成', message='脚本已执行完毕')",
    {"title": "str - 通知标题", "message": "str - 通知正文"},
    notify,
)

META_ACTIONS["dialog"] = (
    "处理对话框和弹窗。'close'自动检测并关闭任意可见对话框(保存确认、错误提示、覆盖询问等)。"
    "'save'在已有保存对话框中填入路径。每次操作后如果出现弹窗应先调用dialog('close')。"
    "常用: dialog(action='close'), dialog(action='save', path='C:\\\\test.txt')",
    {
        "action": "str - 'close'关闭弹窗(默认) 或 'save'填写保存路径",
        "path": "str? - 文件路径（用于 'save'）",
    },
    dialog,
)

META_ACTIONS["network"] = (
    "网络连接管理。'status'查看连接状态, 'scan_wifi'扫描WiFi列表, 'connect_wifi'连接WiFi, 'disconnect_wifi'断开WiFi。"
    "常用: network(action='status'), network(action='scan_wifi'), network(action='connect_wifi', ssid='MyWiFi', password='123456')",
    {
        "action": "str - 'status'(默认)状态, 'scan_wifi'扫描WiFi, 'connect_wifi'连接WiFi, 'disconnect_wifi'断开WiFi",
        "ssid": "str? - WiFi名称（用于 'connect_wifi'）",
        "password": "str? - WiFi密码（用于 'connect_wifi'）",
    },
    network,
)

# Utility
META_ACTIONS["run_command"] = (
    "执行命令行(cmd.exe)命令并返回输出结果。用于系统配置、查询状态、文件操作。"
    "常用: run_command(command='ipconfig'), run_command(command='dir C:\\\\')",
    {
        "command": "str - 要执行的命令",
        "timeout": "num? - 超时秒数（默认30）",
    },
    run_command,
)

META_ACTIONS["run_python_script"] = (
    "直接执行 Python 代码。用于复杂桌面操作、条件逻辑、批量处理。"
    "已预装 pywinauto, pyautogui, psutil, os, subprocess, time, json, math, random 等库。"
    "代码中适当加 print() 输出结果和时间。"
    "常用: run_python_script(script='import pyautogui\\npyautogui.click(100, 200)')",
    {
        "script": "str - 要执行的 Python 代码",
        "timeout": "num? - 超时秒数（默认120）",
    },
    run_python_script,
)

META_ACTIONS["file_ops"] = (
    "文件操作：读取、写入、列出、删除、复制、移动。'write'写入内容到文件, "
    "'read'读取文件内容, 'list'列出目录文件(pattern支持*.txt通配), "
    "'exists'检查是否存在, 'delete'删除, 'copy'复制, 'move'移动。"
    "常用: file_ops(action='read', path='C:\\\\test.txt'), "
    "file_ops(action='write', path='C:\\\\test.txt', content='Hello'), "
    "file_ops(action='list', path='C:\\\\', pattern='*.txt')",
    {
        "action": "str - 操作: 'read'读取, 'write'写入, 'list'列出, 'exists'检查, 'delete'删除, 'copy'复制, 'move'移动",
        "path": "str - 文件路径",
        "content": "str? - 文件内容(用于'write') 或 目标路径(用于'copy'/'move')",
        "pattern": "str? - 文件通配(用于'list')，如 '*.txt'",
    },
    file_ops,
)

META_ACTIONS["wait"] = (
    "等待指定秒数。在操作之间让界面稳定、等待窗口打开或应用响应。"
    "常用: wait(seconds=0.5) 等半秒, wait(seconds=2) 等2秒",
    {"seconds": "num - 等待秒数（0.5-30）"},
    wait,
)

# Flow chart mode (expert=True = hidden by default)
META_ACTIONS["generate_flow"] = (
    "[高级] 从任务描述生成可视化流程图。在前端画布创建可复用的自动化工作流。"
    "流程图可以保存、分享和反复执行。用于创建通用自动化脚本。"
    "常用: generate_flow(goal='打开计算器并计算 1+2')",
    {"goal": "str - 任务描述，用于生成流程图"},
    generate_flow,
    True,  # expert=True
)

META_ACTIONS["execute_flow"] = (
    "[高级] 执行之前生成的流程图脚本(DSL文本或FlowScript JSON)。"
    "常用: execute_flow(script='CLICK 100 200\\nWAIT 2\\nSCREENSHOT')",
    {"script": "str - 流程图脚本（DSL文本或FlowScript JSON）"},
    execute_flow,
    True,  # expert=True
)


# ── 主入口 ────────────────────────────────────────────────────

def execute(name: str, args: dict) -> dict:
    """Execute a meta-action by name.

    Auto-coerces parameter types based on handler's type annotations
    (handles LLMs passing numbers as strings).

    Returns:
        dict with 'success' key, matching @feature tool return format.
    """
    entry = META_ACTIONS.get(name)
    if not entry:
        return {"success": False, "error": f"Unknown meta-action: {name}"}

    handler = entry[2]

    # Type coercion: convert string values to annotated types
    import inspect
    sig = inspect.signature(handler)
    coerced = dict(args)
    for pname, param in sig.parameters.items():
        if pname not in coerced:
            continue
        val = coerced[pname]
        if not isinstance(val, str):
            continue
        ann = param.annotation
        # Handle both string annotations (from __future__) and direct type annotations
        if ann is int or ann == 'int':
            try:
                coerced[pname] = int(val)
            except (ValueError, TypeError):
                pass
        elif ann is float or ann == 'float':
            try:
                coerced[pname] = float(val)
            except (ValueError, TypeError):
                pass

    try:
        result = handler(**coerced)
        return result if isinstance(result, dict) else {"success": True, "result": result}
    except Exception as e:
        logger.error("Meta-action %s failed: %s", name, e)
        return {"success": False, "error": f"{name} failed: {e}"}


def get_prompt(use_flow_chart: bool = False) -> str:
    """Build the meta-actions prompt for LLM consumption.

    Returns a ~8K char prompt with Chinese descriptions and usage examples.
    """
    lines = [
        "## 可用动作（按分类）",
        "",
        "你是桌面自动化 AI，有约 24 个上层动作。每个动作内部自动选择最佳实现方式：",
        "UIA 无障碍树定位 → OCR 文字识别定位 → 图像模板匹配 → 直接屏幕坐标。",
        "你不需要关心底层技术细节，只需要描述你要做什么和操作目标。",
        "",
        "【重要】参数中的 ? 表示可选参数。字符串值需要加引号，数字和布尔值不加。",
        "例: click(target='确认', region='0,0,500,800')  ✓  target 加了引号",
        "    click(target=确认)                            ✗ 没加引号",
        "    manage_app(action='launch', target='notepad.exe')",
        "    press_keys(keys='ctrl+s')",
        "    wait(seconds=0.5)",
        "",
    ]

    categories = [
        ("屏幕点击与输入", [
            "click", "type_text", "press_keys", "scroll", "hover", "drag",
        ]),
        ("屏幕读取（先读后点）", [
            "read_text", "read_ui", "screenshot", "get_cursor",
        ]),
        ("窗口管理", [
            "manage_window", "list_windows",
        ]),
        ("应用管理", [
            "manage_app",
        ]),
        ("系统控制", [
            "control_audio", "system_info", "set_brightness", "lock_screen", "notify",
        ]),
        ("对话框处理", [
            "dialog",
        ]),
        ("浏览器自动化", [
            "browser_do",
        ]),
        ("网络管理", [
            "network",
        ]),
        ("工具命令", [
            "run_command", "file_ops", "wait",
        ]),
    ]

    if use_flow_chart:
        categories.append(("流程图（高级）", [
            "generate_flow", "execute_flow",
        ]))

    for cat_name, action_names in categories:
        lines.append(f"\n### {cat_name}\n")
        for name in action_names:
            entry = META_ACTIONS.get(name)
            if not entry:
                continue
            desc, params, handler, *_ = entry
            lines.append(f"**{name}** — {desc.split('.')[0]}。")
            if params:
                lines.append("参数:")
                for pname, pdesc in params.items():
                    optional = "?" in pname
                    clean_name = pname.replace("?", "")
                    lines.append(f"  - {clean_name} {'(可选)' if optional else ''}: {pdesc.split(' - ', 1)[-1] if ' - ' in pdesc else pdesc}")

    lines.append(r"""
## 常用工作流

1. 启动程序 → 操作
   manage_app(action='launch', target='notepad.exe')
   wait(seconds=0.5)
   type_text(text='Hello World')

2. 聚焦窗口 → 点击
   manage_window(action='focus', title='计算器')
   click(target='1')
   click(target='+')
   click(target='2')

3. 保存文件
   press_keys(keys='ctrl+s')
   wait(seconds=0.3)
   dialog(action='save', path='C:\Users\test\file.txt')

4. 处理弹窗
   dialog(action='close')  — 自动关闭保存确认/错误提示/覆盖询问

5. 读取 → 点击菜单
   read_ui()                    — 获取窗口所有按钮/菜单名
   click(target='文件')
   click(target='另存为')

6. 打开列表项（联系人/文件/聊天）
   click(target='目标名', button='double')  — 双击打开，单击只选中

7. 浏览器操作
   browser_do(action='navigate', url='https://www.baidu.com')
   browser_do(action='type', selector='#kw', text='天气')
   browser_do(action='click', selector='#su')

## 核心原则
- 能用 manage_window 直接聚焦的，不要手动点击标题栏
- 能用 press_keys 快捷键的，不要点菜单（如 ctrl+s 保存，ctrl+c 复制）
- 描述目标用文字（按钮名称），不要猜坐标
- 操作后加 wait(0.2-0.5) 让界面稳定
- 输入文字前必须先 click 点击输入框
- 列表项（联系人/聊天/文件列表）需要双击才能打开，用 button='double'
- 桌面背景/空白区域没有控件名，用坐标点击 click(x=..., y=..., button='right')
""")

    lines.append(f"\n共 {len(META_ACTIONS)} 个动作注册，默认展示 {len(META_ACTIONS) - (0 if use_flow_chart else 2)} 个。")
    lines.append("需要创建可视化流程图请开启流程图模式。")

    return "\n".join(lines)


import re  # noqa: needed at module level for click()
