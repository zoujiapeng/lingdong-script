"""
Meta-Actions — 桌面自动化的高层动作层

每个元动作 = 一件明确的事，最多 3 个参数，无 action 分发器。
LLM 直接选择动作名和参数，内部路由到 @feature 工具。

架构:
  LLM 选择 → click_text/type_text/launch_app...
  系统执行 → 路由到 @feature 工具 + 参数类型强制 + 重试
"""

from __future__ import annotations
import time
import logging
import re
from typing import Any, Callable, Optional

logger = logging.getLogger("agent.meta_actions")

# ── 辅助 ──────────────────────────────────────────────────────

def _registry():
    if hasattr(_registry, '_cache'):
        return _registry._cache
    from features.registry import registry
    import features._all  # noqa
    _registry._cache = registry
    return registry


# ── 屏幕点击 ──────────────────────────────────────────────────

def click_text(target: str, region: str = "") -> dict:
    """Click the text/element on screen.
    target: button text, element name, SoM label '#3', or coordinates '100,200'.
    region: limit search area 'x,y,w,h' when multiple targets exist.
    Falls back: UIA tree → OCR → image match.
    """
    r = _registry()
    kw = {"target": target}
    if region:
        kw["region"] = region
    return r.execute("click_target", **kw)


def click_coord(x: int, y: int, button: str = "left") -> dict:
    """Click at exact screen pixel coordinates.
    Use when target has no text (blank canvas, desktop background).
    button: 'left'(default) | 'right' | 'middle' | 'double'
    """
    r = _registry()
    coord = f"{x},{y}"
    btn_map = {"left": "click_target", "right": "right_click",
               "middle": "middle_click", "double": "click_target"}
    tool = btn_map.get(button, "click_target")
    kw: dict[str, Any] = {"target": coord}
    if button in ("double", "right"):
        kw["button"] = button
    return r.execute(tool, **kw)


def double_click(target: str = "") -> dict:
    """Double-click to open desktop icons, list items, files.
    Single-click only selects; double-click opens.
    """
    r = _registry()
    kw: dict[str, Any] = {"double": True}
    if target:
        kw["target"] = target
    return r.execute("click_target", **kw)


def right_click(target: str = "") -> dict:
    """Right-click to open context menu."""
    r = _registry()
    kw: dict[str, Any] = {}
    if target:
        kw["target"] = target
    return r.execute("right_click", **kw)


# ── 键盘与输入 ────────────────────────────────────────────────

def type_text(text: str, target: str = "", clear_first: bool = True) -> dict:
    """Type text into the focused input field.
    target: optional — click here first before typing.
    Chinese text automatically uses clipboard paste (bypasses IME).
    clear_first: clear existing text before typing (default True).
    """
    r = _registry()
    if target:
        click_result = click_text(target=target, timeout=3)
        if not click_result.get("success"):
            return click_result
        time.sleep(0.3)

    has_chinese = any('一' <= c <= '鿿' for c in text)
    if has_chinese:
        result = r.execute("type_clipboard", text=text)
        if result.get("success"):
            return result
    return r.execute("type_text", text=text, clear_first=clear_first)


def press_keys(keys: str) -> dict:
    """Press a keyboard shortcut.
    Examples: 'ctrl+s', 'alt+tab', 'esc', 'ctrl+c', 'ctrl+v', 'enter', 'win+d'
    """
    return _registry().execute("hotkey", keys=keys)


def scroll(direction: str = "down", amount: int = 3,
           target: str = "", x: int = 0, y: int = 0) -> dict:
    """Scroll at a target location.
    direction: 'down'(default) | 'up' | 'left' | 'right'
    amount: scroll steps (default 3)
    target: text description or SoM label for scroll position.
    """
    r = _registry()
    kw: dict[str, Any] = {"direction": direction, "amount": amount}
    if target:
        kw["target"] = target
    if x or y:
        kw["x"], kw["y"] = x, y
    return r.execute("scroll", **kw)


def hover(target: str, duration: float = 0.5) -> dict:
    """Hover cursor over an element to see tooltip or trigger hover effect."""
    return _registry().execute("hover", target=target, duration=duration)


def drag(from_target: str, to_target: str, duration: float = 0.3) -> dict:
    """Drag from one position to another."""
    return _registry().execute("drag", from_target=from_target,
                                to_target=to_target, duration=duration)


# ── 屏幕读取 ──────────────────────────────────────────────────

def ocr_read(target: str = "", region: str = "") -> dict:
    """Read text from screen using OCR.
    target: if provided, search for this text and return its location.
    region: optional 'x,y,w,h' to limit scan area.
    """
    r = _registry()
    if target:
        return r.execute("ocr_find_text", query=target, region=region)
    return r.execute("ocr_extract_all", region=region)


def read_ui(max_depth: int = 5) -> dict:
    """Read the UI accessibility tree of the active window.
    Returns all buttons, menus, inputs, and their labels.
    """
    return _registry().execute("a11y_get_tree", max_depth=max_depth)


def screenshot(region: str = "", quality: int = 50) -> dict:
    """Take a screenshot. Empty region = full screen.
    quality: JPEG quality 1-100 (default 50).
    """
    kw: dict[str, Any] = {"format": "jpeg", "quality": quality}
    if region:
        kw["region"] = region
    return _registry().execute("screenshot", **kw)


def get_cursor() -> dict:
    """Get current mouse cursor position and the window under cursor."""
    return _registry().execute("get_cursor_pos")


# ── 窗口管理 ──────────────────────────────────────────────────

def list_windows() -> dict:
    """List all visible windows with their titles, positions, and states."""
    return _registry().execute("list_windows")


def focus_window(title: str) -> dict:
    """Bring a window to the foreground by title (partial match)."""
    return _registry().execute("focus_window", title=title)


def close_window(title: str = "") -> dict:
    """Close a window. Empty title = active window."""
    return _registry().execute("window_close", title=title)


def minimize_window(title: str = "") -> dict:
    """Minimize a window to taskbar. Empty title = active window."""
    return _registry().execute("window_minimize", title=title)


def maximize_window(title: str = "") -> dict:
    """Maximize a window. Empty title = active window."""
    return _registry().execute("window_maximize", title=title)


def resize_window(width: int, height: int, title: str = "") -> dict:
    """Resize a window to specific dimensions. Empty title = active window."""
    return _registry().execute("window_resize", width=width, height=height, title=title)


# ── 应用管理 ──────────────────────────────────────────────────

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
    """Resolve Chinese alias / short name to exe filename."""
    if not target:
        return ""
    if target.lower().endswith(".exe"):
        return target
    t = target.lower().strip()
    if t in _COMMON_APPS:
        return _COMMON_APPS[t]
    return f"{t}.exe" if "." not in t else t


def launch_app(target: str) -> dict:
    """Launch an application. Supports Chinese names like '计算器', '记事本'.
    Auto-detects if already running to avoid duplicate launches.
    After launch, use focus_window(title) to bring it front.
    """
    r = _registry()
    exe = _resolve_app(target)
    if not exe:
        return {"success": False, "error": "launch_app needs target, e.g. target='notepad.exe'"}

    try:
        detect_r = r.execute("detect_process", name=exe)
        det_ok = detect_r.get("result", detect_r.get("success", False))
        if isinstance(det_ok, bool) and det_ok:
            return {"success": True, "skipped": "already_running",
                    "message": f"'{target}' already running. Use focus_window(title='...') to bring it front."}
    except Exception:
        pass
    return r.execute("launch_program", target=exe)


def kill_app(target: str, force: bool = False) -> dict:
    """Terminate an application by name. force=True for forced kill."""
    exe = _resolve_app(target)
    if not exe:
        return {"success": False, "error": "kill_app needs target, e.g. target='notepad.exe'"}
    return _registry().execute("kill_process", name=exe, force=force)


def detect_app(target: str) -> dict:
    """Check if an application process is running."""
    exe = _resolve_app(target)
    if not exe:
        return {"success": False, "error": "detect_app needs target, e.g. target='notepad.exe'"}
    return _registry().execute("detect_process", name=exe)


# ── 系统控制 ──────────────────────────────────────────────────

def set_volume(level: float) -> dict:
    """Set system volume (0-100)."""
    return _registry().execute("volume_set", level=level)


def toggle_mute() -> dict:
    """Toggle system audio mute on/off."""
    return _registry().execute("volume_mute")


def get_battery() -> dict:
    """Get battery status (percentage, charging)."""
    return _registry().execute("battery_status")


def set_brightness(level: float) -> dict:
    """Set display brightness (0-100). Laptop displays only."""
    return _registry().execute("display_brightness", level=level)


def lock_workstation() -> dict:
    """Lock the screen (Win+L)."""
    return _registry().execute("lock_workstation")


def notify(title: str = "Task Complete", message: str = "") -> dict:
    """Send a Windows taskbar notification."""
    return _registry().execute("send_notification", title=title, message=message)


# ── 对话框 ────────────────────────────────────────────────────

def close_dialog(timeout: float = 5) -> dict:
    """Auto-close any open dialog/popup (save confirm, error, overwrite prompt)."""
    return _registry().execute("handle_dialog", timeout=timeout)


def save_dialog(path: str, timeout: float = 5) -> dict:
    """Fill the file-save dialog with a path.
    Note: modern Windows IFileDialog cannot be automated — use write_file() instead.
    """
    return _registry().execute("fill_save_dialog", filepath=path, timeout=timeout)


# ── 网络 ──────────────────────────────────────────────────────

def get_network_status() -> dict:
    """Get current network connectivity status."""
    return _registry().execute("network_status")


def scan_wifi() -> dict:
    """List available WiFi networks."""
    return _registry().execute("wifi_list")


def connect_wifi(ssid: str, password: str = "") -> dict:
    """Connect to a WiFi network."""
    return _registry().execute("wifi_connect", ssid=ssid, password=password)


def disconnect_wifi() -> dict:
    """Disconnect from current WiFi network."""
    return _registry().execute("wifi_disconnect")


# ── 浏览器 ────────────────────────────────────────────────────

def browser_open(url: str) -> dict:
    """Open a URL in the browser. Auto-connects to existing browser session."""
    r = _registry()
    r.execute("browser_connect")
    return r.execute("browser_navigate", url=url)


def browser_click(selector: str) -> dict:
    """Click an element in the browser by CSS selector."""
    r = _registry()
    r.execute("browser_connect")
    return r.execute("browser_click", selector=selector)


def browser_type(selector: str, text: str) -> dict:
    """Type text into a browser input field by CSS selector."""
    r = _registry()
    r.execute("browser_connect")
    return r.execute("browser_type", selector=selector, text=text)


def browser_read(selector: str = "") -> dict:
    """Read text from the browser page. With selector, reads that element."""
    r = _registry()
    r.execute("browser_connect")
    if selector:
        return r.execute("browser_get_text", selector=selector)
    return r.execute("browser_get_text")


def browser_screenshot() -> dict:
    """Take a screenshot of the current browser page."""
    r = _registry()
    r.execute("browser_connect")
    return r.execute("browser_screenshot")


def browser_back() -> dict:
    """Navigate back in browser history."""
    r = _registry()
    r.execute("browser_connect")
    return r.execute("browser_go_back")


def browser_refresh() -> dict:
    """Refresh the current browser page."""
    r = _registry()
    r.execute("browser_connect")
    return r.execute("browser_refresh")


def browser_get_url() -> dict:
    """Get the current browser URL."""
    r = _registry()
    r.execute("browser_connect")
    return r.execute("browser_get_url")


# ── 文件与命令 ────────────────────────────────────────────────

def run_command(command: str, timeout: float = 30) -> dict:
    """Execute a shell command and return its output."""
    return _registry().execute("run_command", command=command, timeout=timeout)


def run_python_script(script: str, timeout: int = 120) -> dict:
    """Execute Python code in a subprocess.
    Pre-installed: pywinauto, pyautogui, psutil, os, subprocess, json.
    """
    import subprocess as _sp, tempfile as _tf, os as _os, sys as _sys
    try:
        fd, tmp = _tf.mkstemp(suffix=".py", prefix="lg_")
        with _os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        start = time.time()
        proc = _sp.run([_sys.executable, tmp], capture_output=True, text=True,
                       timeout=timeout, env={**_os.environ, "PYTHONIOENCODING": "utf-8"})
        return {
            "success": proc.returncode == 0,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "exit_code": proc.returncode,
            "elapsed": round(time.time() - start, 1),
        }
    except _sp.TimeoutExpired:
        return {"success": False, "error": f"Script timed out after {timeout}s"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        try:
            _os.unlink(tmp)
        except Exception:
            pass


def read_file(path: str) -> dict:
    """Read file contents as text."""
    return _registry().execute("file_read", path=path)


def write_file(path: str, content: str) -> dict:
    """Write text content to a file. Creates directories if needed."""
    return _registry().execute("file_write", path=path, content=content)


def list_files(path: str, pattern: str = "", recursive: bool = False) -> dict:
    """List files in a directory. Optional glob pattern and recursive search."""
    kw: dict[str, Any] = {"path": path}
    if pattern:
        kw["pattern"] = pattern
    if recursive:
        kw["recursive"] = True
    return _registry().execute("file_list", **kw)


def delete_file(path: str) -> dict:
    """Delete a file."""
    return _registry().execute("file_delete", path=path)


def copy_file(src: str, dst: str) -> dict:
    """Copy a file from source to destination."""
    return _registry().execute("file_copy", src=src, dst=dst)


def move_file(src: str, dst: str) -> dict:
    """Move/rename a file."""
    return _registry().execute("file_move", src=src, dst=dst)


def wait(seconds: float = 1.0) -> dict:
    """Wait for N seconds. Use between actions to let the UI settle."""
    time.sleep(seconds)
    return {"success": True, "result": {"waited": seconds}}


# ── 流程图模式（默认隐藏）─────────────────────────────────────

_FLOW_CHART_ENABLED = False


def set_flow_chart_mode(enabled: bool):
    global _FLOW_CHART_ENABLED
    _FLOW_CHART_ENABLED = enabled


def generate_flow(goal: str) -> dict:
    """[Flow Chart] Generate a reusable visual workflow from a task description."""
    return _registry().execute("generate_flow_script", goal=goal)


def execute_flow(script: str) -> dict:
    """[Flow Chart] Execute a flow chart script (DSL text or FlowScript JSON)."""
    r = _registry()
    if script.strip().startswith("{"):
        return r.execute("software_load_script", script=script)
    return r.execute("run_dsl", dsl=script)


# ── 元动作注册表 ──────────────────────────────────────────────

# 不需要执行后截图的元动作（屏幕不会变化）
NO_SS_AFTER_META = frozenset({
    "ocr_read", "read_ui", "screenshot", "get_cursor",
    "list_windows", "get_network_status", "scan_wifi",
    "get_battery", "set_volume", "toggle_mute",
    "set_brightness", "lock_workstation", "notify",
    "read_file", "list_files", "wait",
    "generate_flow", "execute_flow",
})

META_ACTIONS: dict[str, tuple] = {}
_w = META_ACTIONS  # shorthand

_w["click_text"] = (
    "点击屏幕上的文字/元素。桌面图标用 double_click，右键菜单用 right_click。",
    {"target": "str - 按钮文字/元素名/SoM编号#N/坐标'x,y'",
     "region": "str? - 限定'x,y,w,h'，多目标时使用"},
    click_text,
)
_w["click_coord"] = (
    "在精确像素坐标点击。用于没有文字的区域（桌面背景、画布空白）。",
    {"x": "int - 屏幕x坐标", "y": "int - 屏幕y坐标",
     "button": "str? - left(默认)/right/middle/double"},
    click_coord,
)
_w["double_click"] = (
    "双击打开桌面图标/列表项/文件。单击只选中，必须双击才能打开。",
    {"target": "str? - 目标文字或SoM编号"},
    double_click,
)
_w["right_click"] = (
    "右键点击弹出上下文菜单。",
    {"target": "str? - 目标文字或SoM编号"},
    right_click,
)
_w["type_text"] = (
    "向输入框输入文字。中文自动用剪贴板粘贴(绕过输入法)。",
    {"text": "str - 要输入的文字",
     "target": "str? - 先点击此目标再输入",
     "clear_first": "bool? - 输入前清空(默认true)"},
    type_text,
)
_w["press_keys"] = (
    "按快捷键: 'ctrl+s', 'alt+tab', 'ctrl+c', 'ctrl+v', 'esc', 'enter', 'win+d'…",
    {"keys": "str - 快捷键，多个键用+连接"},
    press_keys,
)
_w["scroll"] = (
    "在指定位置滚动鼠标滚轮。",
    {"direction": "str? - down(默认)/up/left/right",
     "amount": "int? - 滚动步数(默认3)",
     "target": "str? - 滚动位置描述"},
    scroll,
)
_w["hover"] = (
    "悬停鼠标在元素上查看工具提示。",
    {"target": "str - 目标文字/SoM编号",
     "duration": "float? - 悬停秒数(默认0.5)"},
    hover,
)
_w["drag"] = (
    "从一处拖动到另一处。",
    {"from_target": "str - 起始位置",
     "to_target": "str - 目标位置",
     "duration": "float? - 拖动秒数(默认0.3)"},
    drag,
)
_w["ocr_read"] = (
    "用OCR读取屏幕文字。指定target则搜索该文字并返回坐标。",
    {"target": "str? - 要搜索的文字",
     "region": "str? - 限定区域'x,y,w,h'"},
    ocr_read,
)
_w["read_ui"] = (
    "读取活动窗口的无障碍控件树(所有按钮/菜单/输入框)。",
    {"max_depth": "int? - 树深度(默认5)"},
    read_ui,
)
_w["screenshot"] = (
    "截取屏幕截图。用于查看当前界面状态。",
    {"region": "str? - 截取区域'x,y,w,h'(空=全屏)",
     "quality": "int? - JPEG质量1-100(默认50)"},
    screenshot,
)
_w["get_cursor"] = (
    "获取当前鼠标位置和鼠标下的窗口信息。",
    {},
    get_cursor,
)
_w["list_windows"] = (
    "列出所有可见窗口(标题、位置、状态)。",
    {},
    list_windows,
)
_w["focus_window"] = (
    "将窗口前置激活（支持部分标题匹配）。",
    {"title": "str - 窗口标题关键词"},
    focus_window,
)
_w["close_window"] = (
    "关闭窗口。不传title=关闭当前活动窗口。",
    {"title": "str? - 窗口标题关键词"},
    close_window,
)
_w["minimize_window"] = (
    "最小化窗口到任务栏。不传title=最小化当前窗口。",
    {"title": "str? - 窗口标题关键词"},
    minimize_window,
)
_w["maximize_window"] = (
    "最大化窗口。不传title=最大化当前窗口。",
    {"title": "str? - 窗口标题关键词"},
    maximize_window,
)
_w["resize_window"] = (
    "调整窗口大小到指定像素尺寸。",
    {"width": "int - 宽(像素)", "height": "int - 高(像素)",
     "title": "str? - 窗口标题关键词"},
    resize_window,
)
_w["launch_app"] = (
    "启动应用程序。支持中文名'计算器''记事本'。自动检测是否已在运行避免重复启动。",
    {"target": "str - 程序名(notepad.exe/计算器/chrome)"},
    launch_app,
)
_w["kill_app"] = (
    "终止应用程序进程。",
    {"target": "str - 程序名(notepad.exe)", "force": "bool? - 强制终止"},
    kill_app,
)
_w["detect_app"] = (
    "检查应用程序是否在运行。",
    {"target": "str - 程序名(notepad.exe)"},
    detect_app,
)
_w["set_volume"] = (
    "设置系统音量(0-100)。",
    {"level": "float - 音量0-100"},
    set_volume,
)
_w["toggle_mute"] = (
    "切换静音开关。",
    {},
    toggle_mute,
)
_w["get_battery"] = (
    "获取电池状态(百分比/是否充电)。",
    {},
    get_battery,
)
_w["set_brightness"] = (
    "设置屏幕亮度(0-100，仅笔记本)。",
    {"level": "float - 亮度0-100"},
    set_brightness,
)
_w["lock_workstation"] = (
    "锁定工作站(Win+L)。",
    {},
    lock_workstation,
)
_w["notify"] = (
    "发送Windows任务栏通知。",
    {"title": "str? - 标题(默认Task Complete)", "message": "str? - 消息内容"},
    notify,
)
_w["close_dialog"] = (
    "自动关闭弹窗(保存确认/错误提示/覆盖询问)。",
    {"timeout": "float? - 等待秒数(默认5)"},
    close_dialog,
)
_w["save_dialog"] = (
    "填写文件保存对话框路径。(旧版对话框可用，现代IFileDialog无法自动化，用write_file替代)",
    {"path": "str - 保存路径"},
    save_dialog,
)
_w["get_network_status"] = (
    "获取网络连接状态。",
    {},
    get_network_status,
)
_w["scan_wifi"] = (
    "扫描可用WiFi网络列表。",
    {},
    scan_wifi,
)
_w["connect_wifi"] = (
    "连接WiFi网络。",
    {"ssid": "str - WiFi名称", "password": "str? - 密码"},
    connect_wifi,
)
_w["disconnect_wifi"] = (
    "断开当前WiFi连接。",
    {},
    disconnect_wifi,
)
_w["browser_open"] = (
    "在浏览器中打开URL。",
    {"url": "str - 完整URL(含https://)"},
    browser_open,
)
_w["browser_click"] = (
    "点击浏览器页面的元素(CSS选择器)。",
    {"selector": "str - CSS选择器"},
    browser_click,
)
_w["browser_type"] = (
    "在浏览器输入框中输入文字。",
    {"selector": "str - CSS选择器", "text": "str - 输入文字"},
    browser_type,
)
_w["browser_read"] = (
    "读取浏览器页面文字。",
    {"selector": "str? - CSS选择器(空=读取全文)"},
    browser_read,
)
_w["browser_screenshot"] = (
    "截取浏览器页面截图。",
    {},
    browser_screenshot,
)
_w["browser_back"] = (
    "浏览器后退。",
    {},
    browser_back,
)
_w["browser_refresh"] = (
    "刷新浏览器页面。",
    {},
    browser_refresh,
)
_w["browser_get_url"] = (
    "获取当前浏览器URL。",
    {},
    browser_get_url,
)
_w["run_command"] = (
    "执行Shell命令并返回输出结果。",
    {"command": "str - shell命令", "timeout": "float? - 超时秒数(默认30)"},
    run_command,
)
_w["run_python_script"] = (
    "执行Python代码(子进程隔离)。预装: pywinauto, pyautogui, psutil。",
    {"script": "str - Python代码", "timeout": "int? - 超时秒数(默认120)"},
    run_python_script,
)
_w["read_file"] = (
    "读取文件内容。",
    {"path": "str - 文件路径"},
    read_file,
)
_w["write_file"] = (
    "写入文件内容(自动创建目录)。",
    {"path": "str - 文件路径", "content": "str - 内容"},
    write_file,
)
_w["list_files"] = (
    "列出目录中的文件。",
    {"path": "str - 目录路径", "pattern": "str? - glob模式(*.txt)",
     "recursive": "bool? - 递归子目录"},
    list_files,
)
_w["delete_file"] = (
    "删除文件。",
    {"path": "str - 文件路径"},
    delete_file,
)
_w["copy_file"] = (
    "复制文件。",
    {"src": "str - 源路径", "dst": "str - 目标路径"},
    copy_file,
)
_w["move_file"] = (
    "移动/重命名文件。",
    {"src": "str - 源路径", "dst": "str - 目标路径"},
    move_file,
)
_w["wait"] = (
    "等待N秒让界面稳定(输入后0.3s，操作后0.5s，新窗口打开后1-2s)。",
    {"seconds": "float? - 等待秒数(默认1.0)"},
    wait,
)
_w["generate_flow"] = (
    "[流程图] 从任务描述生成可视化工作流。",
    {"goal": "str - 任务描述"},
    generate_flow,
    True,  # expert=True
)
_w["execute_flow"] = (
    "[流程图] 执行一个流程图脚本。",
    {"script": "str - DSL文本或FlowScript JSON"},
    execute_flow,
    True,
)
del _w


# ── 主入口 ────────────────────────────────────────────────────

def execute(name: str, args: dict) -> dict:
    """Execute a meta-action by name. Auto-coerces parameter types."""
    entry = META_ACTIONS.get(name)
    if not entry:
        return {"success": False, "error": f"Unknown meta-action: {name}"}

    handler = entry[2]
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
    """Build the meta-actions prompt for LLM consumption."""
    lines = [
        "## 可用动作（按分类）",
        "",
        "你是桌面自动化 AI。每个动作做一件事，最多传3个参数。",
        "内部自动选择：UIA控件树 → OCR文字识别 → 图像匹配 → 直接坐标。",
        "",
        "【参数规则】",
        "- 字符串值加引号: click_text(target='确认')",
        "- 数字和布尔值不加引号: wait(seconds=0.5), resize_window(width=800, height=600)",
        "- ? 表示可选参数，可省略",
        "",
    ]

    categories = [
        ("点击", ["click_text", "click_coord", "double_click", "right_click"]),
        ("输入与键盘", ["type_text", "press_keys", "scroll", "hover", "drag"]),
        ("读取屏幕", ["ocr_read", "read_ui", "screenshot", "get_cursor"]),
        ("窗口管理", ["list_windows", "focus_window", "close_window", "minimize_window",
                       "maximize_window", "resize_window"]),
        ("应用管理", ["launch_app", "kill_app", "detect_app"]),
        ("系统控制", ["set_volume", "toggle_mute", "get_battery",
                       "set_brightness", "lock_workstation", "notify"]),
        ("对话框", ["close_dialog", "save_dialog"]),
        ("浏览器", ["browser_open", "browser_click", "browser_type",
                     "browser_read", "browser_screenshot", "browser_back",
                     "browser_refresh", "browser_get_url"]),
        ("网络", ["get_network_status", "scan_wifi", "connect_wifi", "disconnect_wifi"]),
        ("文件与命令", ["run_command", "run_python_script",
                        "read_file", "write_file", "list_files",
                        "delete_file", "copy_file", "move_file"]),
        ("等待", ["wait"]),
    ]

    if use_flow_chart:
        categories.append(("流程图", ["generate_flow", "execute_flow"]))

    for cat_name, action_names in categories:
        lines.append(f"\n### {cat_name}\n")
        for name in action_names:
            entry = META_ACTIONS.get(name)
            if not entry:
                continue
            desc, params, handler, *_ = entry
            short = desc.split("。")[0]
            lines.append(f"**{name}** — {short}。")
            if params:
                lines.append("参数:")
                for pname, pdesc in params.items():
                    optional = "?" in pname
                    clean_name = pname.replace("?", "")
                    pd = pdesc.split(" - ", 1)[-1] if " - " in pdesc else pdesc
                    lines.append(f"  - {clean_name} {'(可选)' if optional else ''}: {pd}")

    lines.append(r"""
## 常用工作流

1. 启动应用 → 操作
   launch_app(target='notepad.exe')
   wait(seconds=0.5)
   type_text(text='Hello')

2. 聚焦窗口 → 点击
   focus_window(title='计算器')
   click_text(target='1')
   click_text(target='+')
   click_text(target='2')

3. 保存文件(写入而非对话框)
   press_keys(keys='ctrl+s')
   wait(seconds=0.3)
   close_dialog()

4. 处理弹窗
   close_dialog()  — 自动关闭保存确认/错误提示

5. 读取屏幕 → 点击菜单
   read_ui()                  — 获取所有按钮/菜单
   click_text(target='文件')
   click_text(target='另存为')

6. 打开桌面图标/列表项(必须双击)
   double_click(target='微信')   — 或双击桌面图标

7. 浏览器操作
   browser_open(url='https://www.baidu.com')
   browser_type(selector='#kw', text='天气')
   browser_click(selector='#su')

8. 右键菜单
   right_click(target='桌面')

## 使用原则
- 用 focus_window 直接聚焦窗口而不是手动点击标题栏
- 用 press_keys 快捷键代替菜单点击(ctrl+s保存, ctrl+c复制)
- 描述目标用按钮文字，不要猜坐标
- 操作后加 wait(0.2-1.0) 等待界面稳定
- 输入文字前先用 click_text 点击输入框
- 桌面图标和列表项必须用 double_click 才能打开
- 桌面空白区域没有控件名，用 click_coord(x, y) 点击坐标
""")

    visible = len(META_ACTIONS) - (0 if use_flow_chart else 2)
    lines.append(f"\n共 {len(META_ACTIONS)} 个动作注册，默认展示 {visible} 个。")
    return "\n".join(lines)
