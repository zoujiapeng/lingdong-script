"""Agent tool definitions (OpenAI function-calling schemas) + dispatcher.

The dispatcher holds per-run session state: the latest Snapshot (element-id
registry) and the current target hwnd. Every action tool accepts an optional
``expect`` condition (see action/verify.py DSL) and runs through the
Action→Verify→Retry engine.
"""
from __future__ import annotations

import json
import time
from typing import Any

import win32con
import win32gui

from ..action import executor, shell, verify
from ..config import CONFIG
from ..memory import experience
from ..perception import apps, fusion, ocr, search, tray, vision_cv, vlm, win32, world
from ..utils.log import BUS, logger
from ..utils.screenshot import Region, capture, window_region
from . import planner

_EXPECT_SCHEMA = {
    "type": "object",
    "description": (
        '验证条件,可组合: {"text_appears":"另存为"} {"text_gone":"..."} '
        '{"window_appears":"标题子串"} {"window_gone":"..."} {"screen_stable":true} '
        '{"color_match":{"x":1,"y":2,"rgb":[0,120,215],"tolerance":40}} '
        '【确定性ground-truth(最可靠,涉及文件/进程时优先用)】: '
        '{"file_exists":"路径"} {"file_contains":{"path":"..","text":".."}} '
        '{"process_running":"exe名"} {"process_gone":"exe名"} '
        '{"shell_true":"PowerShell表达式(真值即通过)"}'
    ),
}


def tool_schemas(planning: bool = False) -> list[dict]:
    """OpenAI tools array. ``planning`` adds the todo-management tools;
    vlm_describe only when a VLM is configured."""
    tools = [
        _tool("list_windows", "列出当前所有可见的顶层窗口（标题、hwnd、位置）", {}),
        _tool(
            "observe",
            "感知一个窗口：返回其全部 UI 元素列表（带元素id、文字、坐标、状态、来源维度）。"
            "不传 hwnd 则观察当前前台窗口。操作前必须先 observe。",
            {
                "hwnd": {"type": "integer", "description": "窗口句柄，省略=前台窗口"},
                "include_icons": {"type": "boolean", "description": "是否包含CV图标候选，默认true"},
            },
        ),
        _tool(
            "scan_windows",
            "Scan several matching windows by briefly focusing and observing each one. "
            "Use when an app has multiple windows with the same title, or when the "
            "current window appears to be a login/blocked page but another instance "
            "may already be usable.",
            {
                "query": {"type": "string", "description": "Title/app substring, e.g. QQ, WeChat, Chrome"},
                "max_windows": {"type": "integer", "description": "Maximum matching windows to scan"},
            },
            required=["query"],
        ),
        _tool(
            "focus_window",
            "把窗口切到前台并设为当前操作目标",
            {"hwnd": {"type": "integer"}},
            required=["hwnd"],
        ),
        _tool(
            "find_app",
            "检查程序是否已在运行（即使窗口隐藏/在系统托盘）。返回进程数与所有窗口（含隐藏）。"
            "launch 任何可能已在后台的程序（QQ/微信等）前必须先调用此工具",
            {"name": {"type": "string", "description": "进程名子串，如 QQ、wechat、notepad"}},
            required=["name"],
        ),
        _tool(
            "find_program",
            "按显示名查找本机【已安装】的程序（支持中文名，如 网易云音乐、微信），"
            "返回可启动目标列表。找不到程序的 exe 名时必须用它，不要瞎猜",
            {"name": {"type": "string", "description": "程序显示名或其一部分"}},
            required=["name"],
        ),
        _tool(
            "tray_click",
            "点击系统托盘图标（自动展开隐藏图标溢出区）。用于唤起最小化到托盘的程序。"
            "找不到时会返回托盘现有图标名列表",
            {
                "name": {"type": "string", "description": "图标名子串，如 QQ"},
                "double": {"type": "boolean", "description": "双击，默认false"},
                "button": {"type": "string", "enum": ["left", "right"], "description": "默认left；right弹出托盘菜单"},
            },
            required=["name"],
        ),
        _tool(
            "launch",
            "启动程序（exe名/完整路径/shell命令/应用显示名如\"网易云音乐\"——"
            "非可执行命令会自动按已安装应用解析启动）",
            {"command": {"type": "string"}, "expect": _EXPECT_SCHEMA},
            required=["command"],
        ),
        _tool(
            "click",
            "点击。优先传 element_id（走UIA快路径）；或传裸坐标 x,y",
            {
                "element_id": {"type": "integer", "description": "最近一次observe中的元素id"},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "button": {"type": "string", "enum": ["left", "right", "middle"]},
                "double": {"type": "boolean"},
                "expect": _EXPECT_SCHEMA,
            },
        ),
        _tool(
            "type_text",
            "输入文字（支持中文，自动处理输入法）。可先传 element_id 点击聚焦该输入框",
            {
                "text": {"type": "string"},
                "element_id": {"type": "integer", "description": "可选，先聚焦的输入框元素id"},
                "expect": _EXPECT_SCHEMA,
            },
            required=["text"],
        ),
        _tool(
            "hotkey",
            '按快捷键组合，如 ["ctrl","s"]、["enter"]、["alt","f4"]',
            {
                "keys": {"type": "array", "items": {"type": "string"}},
                "expect": _EXPECT_SCHEMA,
            },
            required=["keys"],
        ),
        _tool(
            "scroll",
            "在坐标处滚动，amount 正=向上 负=向下（格数）",
            {
                "x": {"type": "integer"}, "y": {"type": "integer"},
                "amount": {"type": "integer"},
            },
            required=["x", "y", "amount"],
        ),
        _tool(
            "drag",
            "拖拽/绘画：从(x1,y1)拖到(x2,y2)。绘制曲线/多段线时用 points 传"
            "中间路径点 [[x,y],...]（按顺序经过，再到终点）。duration 为总时长(秒)。",
            {
                "x1": {"type": "integer"}, "y1": {"type": "integer"},
                "x2": {"type": "integer"}, "y2": {"type": "integer"},
                "points": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "integer"}},
                    "description": "可选中间路径点 [[x,y],...]，用于自由绘画/多段拖拽",
                },
                "duration": {"type": "number", "description": "总拖拽时长(秒)，默认0.4"},
            },
            required=["x1", "y1", "x2", "y2"],
        ),
        _tool(
            "wait",
            "等待条件满足（不执行任何动作），如等窗口出现/画面稳定",
            {
                "condition": _EXPECT_SCHEMA,
                "timeout_s": {"type": "number", "description": "默认10秒"},
            },
            required=["condition"],
        ),
        _tool(
            "read_text",
            "读取窗口内全部可见文字（融合感知+OCR），用于读结果/确认内容",
            {"hwnd": {"type": "integer", "description": "省略=当前目标窗口"}},
        ),
        _tool(
            "find_image",
            "在屏幕上模板匹配查找图片（templates_img/下的模板名），返回坐标",
            {"template": {"type": "string"}},
            required=["template"],
        ),
        _tool(
            "capture_template",
            "把屏幕某区域截存为图像模板（之后可用 find_image 在屏幕上找它）。"
            "用于记住某个图标/按钮的样子，逐步积累图标库",
            {
                "left": {"type": "integer"}, "top": {"type": "integer"},
                "width": {"type": "integer"}, "height": {"type": "integer"},
                "name": {"type": "string", "description": "模板名，如 chrome图标"},
            },
            required=["left", "top", "width", "height", "name"],
        ),
        _tool(
            "color_probe",
            "读取屏幕某点的颜色（平均色+主色，hex），判断按钮状态/主题/进度条",
            {"x": {"type": "integer"}, "y": {"type": "integer"}},
            required=["x", "y"],
        ),
        _tool(
            "undo_last",
            "撤销/回收本次最近的可逆动作:取消刚启动的后台/定时任务、关掉刚打开的程序窗口、"
            "或对当前窗口执行 Ctrl+Z。用户说'撤销/取消刚才/关掉它'时用。GUI 可逆性有限。",
            {},
        ),
        _tool(
            "search_files",
            "用 Everything 秒级搜索全盘文件名（找文件/歌曲/文档首选，远快于遍历）。"
            "返回匹配的完整路径列表。可选 path 限定目录",
            {
                "query": {"type": "string", "description": "文件名关键词或通配符，如 *.mp3、报告"},
                "max_results": {"type": "integer", "description": "默认50"},
                "path": {"type": "string", "description": "限定搜索目录，省略=全盘"},
            },
            required=["query"],
        ),
        _tool(
            "done",
            "任务成功完成时调用，结束运行",
            {"summary": {"type": "string", "description": "完成情况总结"}},
            required=["summary"],
        ),
        _tool(
            "fail",
            "确认任务无法完成时调用，结束运行",
            {"reason": {"type": "string"}},
            required=["reason"],
        ),
    ]
    if shell.shell_enabled():
        tools.insert(0, _tool(
            "run_detached",
            "启动一个【独立存活】的后台 PowerShell 脚本（不随本程序退出而死）。"
            "延时/定时/长时后台任务的唯一可靠方式——run_shell 的进程命令返回就死，"
            "Start-Job 也会随之死。脚本里用 Start-Sleep 做延时、用 msg.exe 或托盘气泡做非阻塞提醒"
            "（不要用会阻塞的 MessageBox）。返回 PID，安排后务必验证该进程存活。",
            {
                "script": {"type": "string",
                           "description": "完整 PowerShell 脚本（可含 Start-Sleep、Start-Process 等）"},
                "name": {"type": "string", "description": "任务名（用于脚本文件名），如 提醒吃饭"},
            },
            required=["script"],
        ))
        tools.insert(0, _tool(
            "run_shell",
            "执行 PowerShell/cmd 命令并返回输出（stdout/stderr/退出码）。"
            "文件增删改查移动、启动程序、播放媒体、系统/进程/服务查询等优先用它，比 GUI 快且稳。"
            "默认 powershell；命令可直接读到结果",
            {
                "command": {"type": "string", "description": "要执行的命令"},
                "shell": {"type": "string", "enum": ["powershell", "cmd"], "description": "默认powershell"},
                "timeout_s": {"type": "number", "description": "默认30秒"},
            },
            required=["command"],
        ))
    if planning:
        tools.append(_tool(
            "revise_plan",
            "把当前任务（重新）拆解为有序子目标 todo 列表。多步任务开始时或发现原计划不对时调用。",
            {"steps": {"type": "array", "items": {"type": "string"},
                       "description": "有序子目标。每项是一句简短动宾短语；"
                       "若某步有确定性完成信号,可写成对象 {\"goal\":\"..\",\"check\":{\"file_exists\":\"..\"}} 便于自动核验"}},
            required=["steps"],
        ))
        tools.append(_tool(
            "complete_subgoal",
            "标记某个子目标已完成（按计划里的序号）。完成一步就调用，便于跟踪进度。",
            {"index": {"type": "integer", "description": "计划中的子目标序号（从1开始）"}},
            required=["index"],
        ))
    if experience.enabled():
        tools.append(_tool(
            "remember",
            "把一条用户要求记住的信息/偏好/做法存入长期经验记忆（跨任务生效）。"
            "仅在用户明确要求记住某事时调用。",
            {
                "note": {"type": "string", "description": "要记住的内容，一句话"},
                "app": {"type": "string", "description": "关联的应用名，可选"},
            },
            required=["note"],
        ))
        tools.append(_tool(
            "forget",
            "按关键词删除匹配的经验记忆条目（用户要求忘掉某事时调用）。会返回删除了哪些。",
            {"query": {"type": "string", "description": "匹配任务/应用/步骤文本的关键词"}},
            required=["query"],
        ))
        tools.append(_tool(
            "reuse_skill",
            "复用上次某个成功任务的完整流程（0-token 离线重放，文字→坐标自动重定位）。"
            "当前任务与历史成功任务基本相同时优先用它，比一步步重做快得多。"
            "重放到某步失败会自动停下并告知失败位置，你再从那步起改用实时操作。",
            {
                "task": {"type": "string", "description": "要复用的历史任务描述（或其关键词）"},
                "recipe_id": {"type": "string", "description": "可选，直接指定经验条目 id"},
            },
        ))
    if vlm.is_configured():
        tools.append(
            _tool(
                "vlm_describe",
                "让视觉模型描述屏幕区域（看图片内容/图标含义）。不传区域=当前窗口",
                {
                    "question": {"type": "string"},
                    "left": {"type": "integer"}, "top": {"type": "integer"},
                    "width": {"type": "integer"}, "height": {"type": "integer"},
                },
                required=["question"],
            )
        )
    _normalize_tool_schema_text(tools)
    return tools


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


_CLEAN_TOOL_DESCRIPTIONS = {
    "list_windows": "List visible top-level windows with title, hwnd, and geometry.",
    "observe": (
        "Observe a window and return structured UI elements with element ids, "
        "text, role, coordinates, bbox, state, and perception source. If hwnd "
        "is omitted, observes the current foreground/target window."
    ),
    "scan_windows": (
        "Briefly focus and observe several matching windows, returning text "
        "summaries and generic signals such as auth/login-like or main/search-like. "
        "If no visible window matches, it also scans matching hidden/running "
        "top-level windows. Use this when an app has multiple windows, hidden "
        "windows, or the focused window is a login page."
    ),
    "focus_window": "Bring the given hwnd to the foreground and set it as the current target.",
    "find_app": (
        "Check whether an app/process is running, including hidden/background/tray "
        "state. Use before launching apps that may already be running."
    ),
    "find_program": (
        "Find locally installed programs by display name or partial name. Use when "
        "the executable name is unknown."
    ),
    "tray_click": "Click a system tray icon by name; useful for apps minimized to tray.",
    "launch": "Launch a program by exe name, full path, shell command, or installed app display name.",
    "click": "Click an observed element_id, or absolute screen coordinates x/y.",
    "type_text": "Type literal text. Optionally pass element_id to focus an input before typing.",
    "hotkey": "Press a keyboard shortcut, e.g. ['ctrl','s'], ['enter'], ['alt','f4'].",
    "scroll": "Scroll at screen coordinates x/y by amount; positive/negative direction depends on system wheel semantics.",
    "drag": "Drag from x1/y1 to x2/y2, optionally through intermediate points.",
    "wait": "Wait until an expected condition is true, without performing an action.",
    "read_text": "Read visible text from the target window using fused perception/OCR.",
    "find_image": "Find an image template on screen and return matching coordinates.",
    "capture_template": "Capture a screen region as a reusable image template.",
    "color_probe": "Read color information at a screen point.",
    "undo_last": "Undo or clean up the most recent reversible action from this run.",
    "search_files": (
        "Search user files by name, optionally under a path. Do not use this "
        "to launch installed applications; use find_app/find_program/launch."
    ),
    "run_shell": "Run a PowerShell/cmd command and return stdout/stderr/exit code.",
    "run_detached": "Start an independent background PowerShell script and return its PID.",
    "revise_plan": "Create or replace a short ordered todo plan for the task.",
    "complete_subgoal": "Mark a todo item complete by index.",
    "remember": "Store a user-requested long-term memory note.",
    "forget": "Delete matching long-term memory notes by query.",
    "reuse_skill": "Replay a matching previously successful workflow when appropriate.",
    "vlm_describe": "Ask the visual model to describe a screen/window region when available.",
    "done": "Finish the run only after verified evidence shows the task is complete.",
    "fail": "Finish with an honest failure reason when the task cannot be completed.",
}


_CLEAN_EXPECT_DESCRIPTION = (
    "Verification condition. Examples: "
    '{"text_appears":"Save"}, {"text_gone":"Loading"}, '
    '{"window_appears":"Notepad"}, {"window_gone":"Calculator"}, '
    '{"screen_stable":true}, '
    '{"color_match":{"x":1,"y":2,"rgb":[0,120,215],"tolerance":40}}, '
    '{"file_exists":"C:\\\\path\\\\file.txt"}, '
    '{"file_contains":{"path":"C:\\\\path\\\\file.txt","text":"hello"}}, '
    '{"process_running":"notepad"}, {"process_gone":"notepad"}, '
    '{"shell_true":"Test-Path C:\\\\x"}'
)


_CLEAN_PROPERTY_DESCRIPTIONS = {
    "hwnd": "Window handle. Omit for current target/foreground window when allowed.",
    "include_icons": "Whether to include CV icon candidates.",
    "name": "Program, process, tray icon, or task name.",
    "command": "Command, exe name, path, or installed app display name.",
    "element_id": "Element id from the most recent observe result.",
    "x": "Absolute screen x coordinate.",
    "y": "Absolute screen y coordinate.",
    "button": "Mouse button.",
    "double": "Whether to double-click.",
    "text": "Literal text to type or verify.",
    "keys": "Shortcut keys as an array.",
    "amount": "Scroll amount.",
    "condition": _CLEAN_EXPECT_DESCRIPTION,
    "timeout_s": "Timeout in seconds.",
    "query": "Search query.",
    "max_windows": "Maximum number of matching windows to scan.",
    "max_results": "Maximum number of results.",
    "path": "Optional path scope.",
    "summary": "Short completion summary.",
    "reason": "Failure reason.",
    "question": "Focused visual question.",
}


def _normalize_tool_schema_text(tools: list[dict]) -> None:
    """Replace mojibake descriptions before sending schemas to the LLM."""
    for tool in tools:
        fn = tool.get("function", {})
        name = fn.get("name", "")
        if name in _CLEAN_TOOL_DESCRIPTIONS:
            fn["description"] = _CLEAN_TOOL_DESCRIPTIONS[name]
        params = fn.get("parameters", {})
        props = params.get("properties", {})
        for prop_name, spec in props.items():
            if prop_name == "expect":
                spec["description"] = _CLEAN_EXPECT_DESCRIPTION
            elif isinstance(spec, dict) and prop_name in _CLEAN_PROPERTY_DESCRIPTIONS:
                spec["description"] = _CLEAN_PROPERTY_DESCRIPTIONS[prop_name]


class ToolResult:
    """Dispatcher output: text for the LLM + metadata for the loop."""

    def __init__(self, text: str, is_observe: bool = False,
                 terminal: str | None = None, summary: str = "",
                 replay: dict | None = None) -> None:
        self.text = text
        self.is_observe = is_observe
        self.terminal = terminal          # None | "done" | "fail"
        self.summary = summary            # one-line replacement when folded
        self.replay = replay              # selector info for offline replay export


class ToolSession:
    """Per-run state: latest snapshot + current target window."""

    def __init__(self, stop_event=None, task: str = "") -> None:
        self.snapshot: fusion.Snapshot | None = None
        self.hwnd: int | None = None
        self.task = task or ""
        self._pending_visual_advisory = ""
        self._last_visual_advisory = ""
        self.target_hint: str = ""
        # Shared with the runner so long ops (reuse_skill replay) honor Stop.
        import threading
        self.stop_event = stop_event or threading.Event()
        # World model is fed from dispatch(); read once per run (runs are
        # short-lived, created per task) so a runtime toggle change applies
        # to the next task rather than mid-run.
        self.world_enabled: bool = bool(CONFIG.get("agent", "world_model", default=True))
        self.world = world.WorldModel()
        # Macro recovery: give the model one forced reconsideration before a
        # fail() actually terminates the run.
        self.replan_on_fail: bool = bool(CONFIG.get("agent", "replan_on_fail", default=True))
        self._fail_softened = False
        self.plan: planner.Plan | None = None
        # Lightweight undo stack: things this run created that can be rolled back.
        self._undo_stack: list[tuple[str, object]] = []

    # ----------------------------------------------------------- dispatch
    def dispatch(self, name: str, args: dict[str, Any]) -> ToolResult:
        handler = getattr(self, f"_t_{name}", None)
        if handler is None:
            return ToolResult(f"未知工具: {name}")
        try:
            result = handler(args)
        except Exception as exc:
            logger.exception("工具 %s 执行异常", name)
            return ToolResult(f"工具 {name} 执行异常: {exc}")
        if self.world_enabled:
            try:
                self.world.record_action(name, args)
                if result.is_observe and self.snapshot is not None:
                    self.world.observe_snapshot(self.snapshot)
            except Exception:
                logger.exception("世界模型更新失败")  # never block tool flow
        return result

    @staticmethod
    def _norm_window_text(value: object) -> str:
        return str(value or "").casefold()

    @staticmethod
    def _window_alive(hwnd: int | None) -> bool:
        return bool(hwnd and win32gui.IsWindow(int(hwnd)) and win32gui.IsWindowVisible(int(hwnd)))

    def _remember_target(self, hwnd: int | None = None, hint: str = "") -> None:
        if hwnd and self._window_alive(hwnd):
            self.hwnd = int(hwnd)
        hint = (hint or "").strip()
        if hint:
            self.target_hint = hint

    def _find_target_window(self) -> int | None:
        needle = self._norm_window_text(self.target_hint)
        if not needle:
            return None
        for window in win32.list_windows():
            title = self._norm_window_text(window.get("title"))
            class_name = self._norm_window_text(window.get("class_name"))
            if needle in title or title in needle or needle in class_name:
                hwnd = int(window.get("hwnd") or 0)
                if self._window_alive(hwnd):
                    return hwnd
        return None

    def _resolve_hwnd(self, args: dict) -> int | None:
        if args.get("hwnd"):
            hwnd = int(args["hwnd"])
            if self._window_alive(hwnd):
                self._remember_target(hwnd)
            return hwnd
        if self._window_alive(self.hwnd):
            return self.hwnd
        target = self._find_target_window()
        if target:
            self._remember_target(target)
            return target
        fg = win32gui.GetForegroundWindow()
        return fg if fg else None

    def _resolve_element(self, elem_id: int):
        if self.snapshot is None:
            return None, "尚未 observe，无元素可用"
        element = self.snapshot.by_id(elem_id)
        if element is None:
            return None, f"元素 id={elem_id} 不存在于最近一次 observe（请重新 observe）"
        return element, ""

    @staticmethod
    def _fmt(data: dict) -> str:
        return json.dumps(data, ensure_ascii=False, default=str)

    def _needs_visual_state_summary(self) -> bool:
        text = f"{self.task} {self.snapshot.title if self.snapshot else ''}".casefold()
        needles = (
            "draw", "drawing", "paint", "sketch", "canvas", "whiteboard",
            "\u753b", "\u753b\u56fe", "\u7ed8\u56fe", "\u6d82\u9e26",
            "\u753b\u677f", "\u767d\u677f", "\u989c\u8272", "color",
        )
        return any(needle in text for needle in needles)

    def _visual_state_summary(self, hwnd: int) -> str:
        if not self._needs_visual_state_summary():
            return ""
        try:
            region = window_region(hwnd)
            image = capture(region)
            canvas_bbox = vision_cv.likely_canvas_bbox(image)
            offset_x = 0
            offset_y = 0
            scope = "current window"
            if canvas_bbox:
                x1, y1, x2, y2 = canvas_bbox
                image = image[y1:y2, x1:x2]
                offset_x = x1
                offset_y = y1
                scope = f"likely_canvas@({region.left + x1},{region.top + y1},{region.left + x2},{region.top + y2})"
            stats = vision_cv.color_presence(image)
        except Exception as exc:
            logger.debug("visual state summary failed: %s", exc)
            return ""
        colors = stats.get("colors", {}) if isinstance(stats, dict) else {}
        visible_parts: list[str] = []
        for name in ("red", "orange", "yellow", "green", "blue", "purple", "black"):
            item = colors.get(name, {})
            if not item.get("visible"):
                continue
            bbox = item.get("largest_bbox") or item.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                sx1, sy1, sx2, sy2 = (
                    int(bbox[0]) + region.left + offset_x,
                    int(bbox[1]) + region.top + offset_y,
                    int(bbox[2]) + region.left + offset_x,
                    int(bbox[3]) + region.top + offset_y,
                )
                visible_parts.append(f"{name}@({sx1},{sy1},{sx2},{sy2})")
            else:
                visible_parts.append(name)
        popup_terms = (
            "PopupHost", "Line", "Fill", "Color 1:", "No fill", "Brush",
            "Edit colors", "OK", "Cancel", "RGB",
        )
        popup_open = False
        if self.snapshot is not None:
            popup_open = any(
                any(term in (element.text or "") for term in popup_terms)
                or "popup" in (element.role or "").casefold()
                for element in self.snapshot.elements[:80]
            )
        colors_text = ", ".join(visible_parts) if visible_parts else "none"
        popup_text = ""
        advisory = ""
        if popup_open and scope == "current window":
            popup_text = (
                "; popup/tool panel appears open; these colors may belong to "
                "the tool panel, not the canvas"
            )
            advisory = (
                "A tool/color modal is foreground. Its visible colors are controls, "
                "not proof of canvas content. Finish with OK/Cancel/Escape or close "
                "the modal, then observe the canvas before drawing or claiming color evidence."
            )
        elif popup_open:
            popup_text = "; popup/tool panel appears open"
            advisory = (
                "A popup/tool panel is open over the canvas. If tool/color selection is done, "
                "close it with OK/Escape or click back on the canvas before drawing. Do not keep "
                "probing the same panel."
            )
        if advisory and advisory != self._last_visual_advisory:
            self._pending_visual_advisory = advisory
            self._last_visual_advisory = advisory
        return (
            f"Visual state summary ({scope}): visible coarse colors: "
            f"{colors_text}{popup_text}. If this contradicts the plan, correct "
            "the next action before continuing."
        )

    def take_visual_advisory(self) -> str:
        note = self._pending_visual_advisory
        self._pending_visual_advisory = ""
        return note

    # -------------------------------------------------------------- tools
    def _t_list_windows(self, _args: dict) -> ToolResult:
        windows = win32.list_windows()
        lines = [
            f'hwnd={w["hwnd"]} "{w["title"]}" class={w["class_name"]} rect={w["rect"]}'
            for w in windows[:40]
        ]
        return ToolResult("\n".join(lines) or "无可见窗口",
                          summary=f"[已折叠] list_windows {len(windows)}个窗口")

    def _t_list_windows(self, _args: dict) -> ToolResult:
        windows = win32.list_windows()
        lines = [
            f'hwnd={w["hwnd"]} "{w["title"]}" class={w["class_name"]} rect={w["rect"]}'
            for w in windows[:40]
        ]
        groups: dict[tuple[str, str], list[dict]] = {}
        for window in windows:
            key = (str(window.get("title", "")).lower(),
                   str(window.get("class_name", "")).lower())
            groups.setdefault(key, []).append(window)
        duplicates = [
            wins for (title, _class), wins in groups.items()
            if title and len(wins) > 1
        ]
        if duplicates:
            lines.append("")
            lines.append(
                "NOTE: multiple windows share the same title/class. Do not assume "
                "the foreground one is the right one; use scan_windows(query=...) "
                "or observe each hwnd before login/account actions."
            )
            for wins in duplicates[:5]:
                label = wins[0].get("title", "")
                hwnds = ", ".join(str(w.get("hwnd")) for w in wins[:8])
                lines.append(f'duplicate "{label}": hwnds={hwnds}')
        return ToolResult("\n".join(lines) or "No visible windows.",
                          summary=f"[folded] list_windows {len(windows)} windows")

    @staticmethod
    def _window_signal(text: str) -> str:
        low = text.lower()
        auth = (
            "登录", "扫码", "密码", "账号", "注册", "服务协议", "隐私政策",
            "login", "sign in", "password", "account", "privacy",
        )
        search_like = (
            "搜索", "查找", "联系人", "消息", "会话", "好友", "群聊", "聊天",
            "search", "contacts", "messages", "chats",
        )
        if any(word in low for word in auth):
            return "auth/login-like"
        if any(word in low for word in search_like):
            return "main/search-like"
        return "unknown"

    def _t_scan_windows(self, args: dict) -> ToolResult:
        query = str(args.get("query") or "").strip().lower()
        max_windows = max(1, min(int(args.get("max_windows", 6)), 10))
        windows = win32.list_windows()
        matches = [
            w for w in windows
            if not query
            or query in str(w.get("title", "")).lower()
            or query in str(w.get("class_name", "")).lower()
        ]
        used_hidden_candidates = False
        if not matches:
            app_state = win32.find_app(query) if query else {"windows": []}
            for item in app_state.get("windows", []):
                hwnd = int(item.get("hwnd") or 0)
                if not hwnd or not win32gui.IsWindow(hwnd):
                    continue
                try:
                    title = win32gui.GetWindowText(hwnd) or str(item.get("title", ""))
                    class_name = win32gui.GetClassName(hwnd)
                    if query and query not in title.lower() and query not in class_name.lower():
                        continue
                    rect = list(win32gui.GetWindowRect(hwnd))
                    matches.append({
                        "hwnd": hwnd,
                        "title": title,
                        "class_name": class_name,
                        "rect": rect,
                        "pid": item.get("pid", 0),
                        "visible": bool(win32gui.IsWindowVisible(hwnd)),
                    })
                except Exception:
                    continue
            used_hidden_candidates = bool(matches)
        if not matches:
            return ToolResult(f'No visible or running windows match query "{query}".')

        original = win32gui.GetForegroundWindow()
        rows: list[str] = []
        best_hwnd: int | None = None
        best_score = -999.0
        for window in matches[:max_windows]:
            hwnd = int(window["hwnd"])
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNORMAL)
                    time.sleep(0.25)
                executor.focus_window(hwnd)
                time.sleep(0.2)
                snapshot = fusion.observe(hwnd, include_icons=False)
                texts = [e.text.strip() for e in snapshot.elements if e.text.strip()]
                unique = list(dict.fromkeys(texts))
                joined = " | ".join(unique[:18])
                signal = self._window_signal(joined)
                score = (3 if signal.startswith("main") else 0) - (
                    2 if signal.startswith("auth") else 0
                ) + min(len(unique), 12) / 20
                if score > best_score:
                    best_score = score
                    best_hwnd = hwnd
                rows.append(
                    f'hwnd={hwnd} title="{window["title"]}" rect={window["rect"]} '
                    f'signal={signal} text={joined or "(no readable text)"}'
                )
            except Exception as exc:
                rows.append(
                    f'hwnd={hwnd} title="{window.get("title", "")}" '
                    f'error={type(exc).__name__}: {exc}'
                )
        if original and win32gui.IsWindow(original):
            try:
                executor.focus_window(original)
            except Exception:
                pass
        if best_hwnd is not None:
            rows.insert(0, f"recommended_hwnd={best_hwnd}")
        prefix = "Scanned hidden/running candidates because no visible window matched. " if used_hidden_candidates else ""
        rows.insert(
            0,
            prefix
            + "Prefer main/search-like windows over auth/login-like windows. "
            + "signal=unknown means insufficient evidence; do not classify it as a login page without login/auth text."
        )
        return ToolResult("\n".join(rows))

    def _t_observe(self, args: dict) -> ToolResult:
        hwnd = self._resolve_hwnd(args)
        if not hwnd:
            return ToolResult("无法确定目标窗口，请先 list_windows + focus_window")
        include_icons = bool(args.get("include_icons", True))
        self.snapshot = fusion.observe(hwnd, include_icons=include_icons)
        self._remember_target(hwnd, self.snapshot.title)
        text = self.snapshot.to_text()
        visual_summary = self._visual_state_summary(hwnd)
        if visual_summary:
            text = text + "\n" + visual_summary
        summary = (
            f'[已折叠] observe "{self.snapshot.title}" '
            f"{len(self.snapshot.elements)}个元素"
        )
        return ToolResult(text, is_observe=True, summary=summary)

    def _t_focus_window(self, args: dict) -> ToolResult:
        hwnd = int(args["hwnd"])
        title = win32._window_text(hwnd)
        result = executor.focus_window(hwnd)
        if result.get("ok"):
            self._remember_target(hwnd, title)
        return ToolResult(
            self._fmt(result),
            replay={"tool": "focus_window", "title": title},
        )

    def _t_find_app(self, args: dict) -> ToolResult:
        info = win32.find_app(str(args["name"]))
        return ToolResult(self._fmt(info))

    def _t_find_program(self, args: dict) -> ToolResult:
        candidates = apps.resolve(str(args["name"]))
        if not candidates:
            return ToolResult(
                f'未找到名称含 "{args["name"]}" 的已安装程序。'
                "可换更短的关键词重试，或用 search_files 搜可执行文件")
        lines = [
            f'{i}. "{c["name"]}" [{c["kind"]}] {c["target"]}'
            for i, c in enumerate(candidates, 1)
        ]
        self.target_hint = str(candidates[0].get("name") or args["name"])
        return ToolResult(
            "已安装程序候选（直接 launch 其名称或完整 target 即可启动）:\n"
            + "\n".join(lines))

    def _t_run_shell(self, args: dict) -> ToolResult:
        command = str(args["command"])
        shell_kind = str(args.get("shell", "powershell"))
        timeout_s = float(args.get("timeout_s", 30))
        result = shell.run(command, shell=shell_kind, timeout_s=timeout_s)
        return ToolResult(
            self._fmt(result),
            replay={"tool": "run_shell", "command": command, "shell": shell_kind,
                    "timeout_s": timeout_s},
        )

    def _t_run_detached(self, args: dict) -> ToolResult:
        script = str(args["script"])
        name = str(args.get("name", "task"))
        result = shell.run_detached(script, name=name)
        if result.get("ok") and result.get("pid"):
            self._undo_stack.append(("detached", result["pid"]))  # cancelable
        return ToolResult(
            self._fmt(result),
            replay={"tool": "run_detached", "script": script, "name": name},
        )

    def _t_undo_last(self, _args: dict) -> ToolResult:
        """Best-effort rollback of the most recent reversible action this run:
        cancel a scheduled/background task, close the last launched app, or
        Ctrl+Z the foreground window. GUI is only partly reversible."""
        while self._undo_stack:
            kind, payload = self._undo_stack.pop()
            if kind == "detached":
                import subprocess
                subprocess.run(["taskkill", "/f", "/pid", str(payload)],
                               capture_output=True, timeout=10)
                BUS.publish("action", action="undo", what=f"取消后台任务 pid={payload}")
                return ToolResult(f"已取消最近的后台/定时任务 (pid={payload})。")
            if kind == "app":
                # Close the specific launched window (not the current foreground,
                # which the user may have switched). Skip if it's already gone.
                hwnd = payload
                if not hwnd or not win32gui.IsWindow(hwnd):
                    continue
                title = win32._window_text(hwnd)
                res = executor.close_window(hwnd)
                if res.get("ok"):
                    BUS.publish("action", action="undo", what=f"关闭窗口 {title}")
                    return ToolResult(f'已关闭最近打开的程序窗口 "{title}"。')
                # couldn't close → fall through to next undo candidate
        # nothing on the stack → editor-style undo on the foreground
        executor.press_keys(["ctrl", "z"])
        return ToolResult("无可回收的启动记录；已对当前窗口执行撤销 (Ctrl+Z)。")

    def _t_search_files(self, args: dict) -> ToolResult:
        result = search.search(
            str(args["query"]),
            max_results=int(args.get("max_results", 50)),
            path=args.get("path"),
        )
        return ToolResult(self._fmt(result))

    def _t_tray_click(self, args: dict) -> ToolResult:
        result = tray.click_tray_icon(
            str(args["name"]),
            double=bool(args.get("double", False)),
            button=str(args.get("button", "left")),
        )
        return ToolResult(
            self._fmt(result),
            replay={"tool": "tray_click", "name": str(args["name"]),
                    "double": bool(args.get("double", False)),
                    "button": str(args.get("button", "left"))},
        )

    def _t_launch(self, args: dict) -> ToolResult:
        command = str(args["command"]).strip()
        resolved = self._resolve_launch_target(command)

        def do_launch() -> dict:
            if resolved is not None:
                return apps.launch_target(resolved)
            return executor.launch(command)

        result = verify.run_with_verify(
            "launch",
            do_launch,
            expect=args.get("expect"),
            hwnd=self.hwnd,
            timeout_s=10.0,
            stop_event=self.stop_event,
        )
        if result.get("ok"):
            # Record the launched app's window (post-launch foreground) so
            # undo_last closes THAT window, not whatever is foreground later.
            launched_hwnd = (result.get("post_state") or {}).get("foreground_hwnd")
            self._undo_stack.append(("app", launched_hwnd))
            if resolved is not None:
                self._remember_target(launched_hwnd, str(resolved.get("name") or command))
                target = self._find_target_window()
                if target:
                    self._remember_target(target)
            elif launched_hwnd:
                self._remember_target(launched_hwnd)
        if resolved is not None:
            result["resolved_app"] = f'{resolved["name"]} [{resolved["kind"]}]'
            if self.hwnd:
                result["target_hwnd"] = self.hwnd
        return ToolResult(
            self._fmt(result),
            replay={"tool": "launch", "command": command,
                    "expect": args.get("expect")},
        )

    @staticmethod
    def _resolve_launch_target(command: str) -> dict | None:
        """Map an app display name (e.g. 网易云音乐) to an installed-app target.

        Only kicks in when the command is clearly NOT directly executable:
        not an existing path, not on PATH, no shell syntax. Keeps notepad/calc
        and full commands on the legacy Popen path untouched.
        """
        import shutil
        from pathlib import Path as _P

        if not command or any(ch in command for ch in "\\/|&><\""):
            return None
        if _P(command).exists() or shutil.which(command):
            return None
        base = command.split()[0] if command.isascii() else command
        if command.isascii() and (shutil.which(base) or len(command.split()) > 1):
            return None  # looks like a real command line — don't second-guess it
        candidates = apps.resolve(command)
        return candidates[0] if candidates else None

    def _t_click(self, args: dict) -> ToolResult:
        element_id = args.get("element_id")
        button = args.get("button", "left")
        double = bool(args.get("double", False))
        replay: dict[str, Any] = {
            "tool": "click", "button": button, "double": double,
            "expect": args.get("expect"),
        }

        if element_id is not None:
            element, err = self._resolve_element(int(element_id))
            if element is None:
                return ToolResult(err)
            x, y = element.cx, element.cy
            replay.update({
                "text": element.text, "role": element.role,
                "coords": [x, y], "source": element.source,
            })

            def action() -> dict:
                # UIA fast path for plain left single clicks
                if button == "left" and not double and executor.uia_invoke(element):
                    BUS.publish("action", action="uia_invoke", elem_id=element.elem_id,
                                text=element.text)
                    return {"ok": True, "via": "uia_invoke"}
                return executor.click(x, y, button=button, double=double, hwnd=self.hwnd)
        elif "x" in args and "y" in args:
            x, y = int(args["x"]), int(args["y"])
            replay["coords"] = [x, y]

            def action() -> dict:
                return executor.click(x, y, button=button, double=double, hwnd=self.hwnd)
        else:
            return ToolResult("click 需要 element_id 或 x,y 坐标")

        result = verify.run_with_verify(
            "click", action, expect=args.get("expect"), hwnd=self.hwnd,
            stop_event=self.stop_event)
        if not args.get("expect"):
            result.setdefault("verified", False)
            result.setdefault("verify_detail", "未提供 expect；ok 只表示点击已执行，必须 observe 确认界面变化")
        return ToolResult(self._fmt(result), replay=replay)

    def _t_type_text(self, args: dict) -> ToolResult:
        text = str(args["text"])
        element_id = args.get("element_id")
        replay: dict[str, Any] = {
            "tool": "type_text", "text_input": text, "expect": args.get("expect"),
        }
        if element_id is not None:
            element, err = self._resolve_element(int(element_id))
            if element is None:
                return ToolResult(err)
            replay.update({
                "element_text": element.text, "coords": [element.cx, element.cy],
            })
        else:
            element = None

        def action() -> dict:
            if element is not None:
                if executor.uia_set_value(element, text):
                    return {"ok": True, "via": "uia_set_value"}
                executor.click(element.cx, element.cy, hwnd=self.hwnd)
                import time
                time.sleep(0.15)
            return executor.type_text(text)

        result = verify.run_with_verify(
            "type_text", action, expect=args.get("expect"),
            hwnd=self.hwnd, retryable=False, stop_event=self.stop_event)
        if not args.get("expect"):
            result.setdefault("verified", False)
            result.setdefault("verify_detail", "未提供 expect；ok 只表示输入动作已执行，必须 observe/read_text 确认文本位置")
        return ToolResult(self._fmt(result), replay=replay)

    def _t_hotkey(self, args: dict) -> ToolResult:
        keys = [str(k) for k in args["keys"]]
        result = verify.run_with_verify(
            "hotkey", lambda: executor.press_keys(keys),
            expect=args.get("expect"), hwnd=self.hwnd, retryable=False,
            stop_event=self.stop_event)
        if not args.get("expect"):
            result.setdefault("verified", False)
            result.setdefault("verify_detail", "未提供 expect；ok 只表示快捷键已发送，必须 observe/read_text 确认结果")
        return ToolResult(
            self._fmt(result),
            replay={"tool": "hotkey", "keys": keys, "expect": args.get("expect")},
        )

    def _t_scroll(self, args: dict) -> ToolResult:
        x, y, amount = int(args["x"]), int(args["y"]), int(args["amount"])
        result = executor.scroll(x, y, amount)
        return ToolResult(
            self._fmt(result),
            replay={"tool": "scroll", "coords": [x, y], "amount": amount},
        )

    def _t_drag(self, args: dict) -> ToolResult:
        x1, y1 = int(args["x1"]), int(args["y1"])
        x2, y2 = int(args["x2"]), int(args["y2"])
        points = args.get("points") if isinstance(args.get("points"), list) else None
        duration = float(args.get("duration", 0.4))
        region = self._drag_feedback_region(x1, y1, x2, y2, points)
        before = None
        try:
            before = capture(region)
        except Exception as exc:
            logger.debug("drag pre-capture failed: %s", exc)
        result = executor.drag(x1, y1, x2, y2, duration_s=duration,
                               points=points, hwnd=self.hwnd)
        if before is not None:
            try:
                time.sleep(0.12)
                after = capture(region)
                ratio = vision_cv.diff_ratio(before, after)
                result["visual_change_ratio"] = round(float(ratio), 5)
                result["visual_change_detected"] = bool(ratio >= 0.0008)
                if ratio < 0.0008:
                    result["visual_feedback"] = (
                        "drag completed but the local screen region barely changed; "
                        "for drawing/canvas tasks, re-observe and try a visible stroke "
                        "inside the canvas rather than toolbar or popup chrome."
                    )
                else:
                    result["visual_feedback"] = (
                        "local screen pixels changed after drag; this is useful evidence "
                        "that the action affected the visible surface."
                    )
            except Exception as exc:
                logger.debug("drag post-capture/diff failed: %s", exc)
        return ToolResult(
            self._fmt(result),
            replay={"tool": "drag", "from": [x1, y1], "to": [x2, y2],
                    "points": points, "duration": duration},
        )

    @staticmethod
    def _drag_feedback_region(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        points: list | None,
    ) -> Region:
        coords: list[tuple[int, int]] = [(x1, y1), (x2, y2)]
        for point in points or []:
            try:
                coords.append((int(point[0]), int(point[1])))
            except (TypeError, ValueError, IndexError, KeyError):
                continue
        xs = [x for x, _ in coords]
        ys = [y for _, y in coords]
        pad = 28
        left = min(xs) - pad
        top = min(ys) - pad
        right = max(xs) + pad
        bottom = max(ys) + pad
        return Region(left, top, max(1, right - left), max(1, bottom - top)).clamp_to_screen()

    def _t_wait(self, args: dict) -> ToolResult:
        condition = args.get("condition") or {}
        if not isinstance(condition, dict) or not condition:
            return ToolResult("wait 需要 condition 验证条件对象")
        timeout_s = float(args.get("timeout_s", 10.0))
        outcome = verify.check(condition, hwnd=self.hwnd, timeout_s=timeout_s,
                               stop_event=self.stop_event)
        return ToolResult(
            self._fmt(outcome.to_dict()),
            replay={"tool": "wait", "condition": condition, "timeout_s": timeout_s},
        )

    def _t_read_text(self, args: dict) -> ToolResult:
        hwnd = self._resolve_hwnd(args)
        if not hwnd:
            return ToolResult("无目标窗口")
        snapshot = fusion.observe(hwnd, include_icons=False)
        texts = [e.text for e in snapshot.elements if e.text.strip()]
        ocr_elements = ocr.collect(snapshot.region, min_conf=0.45)
        texts.extend(e.text for e in ocr_elements if e.text.strip())
        body = "\n".join(dict.fromkeys(texts))  # de-dupe, keep order
        return ToolResult(body or "（未识别到文字）",
                          summary=f"[已折叠] read_text {len(texts)}条文字")

    def _t_find_image(self, args: dict) -> ToolResult:
        matches = vision_cv.match_template(str(args["template"]))
        if not matches:
            available = vision_cv.list_templates()
            listing = ("可用模板: " + ", ".join(available)) if available else (
                "模板库为空——可用 capture_template 先截存一个图标区域作为模板")
            return ToolResult(f"未找到匹配（检查模板名/阈值）。{listing}")
        lines = [
            f'匹配 "{m.text}" 中心({m.cx},{m.cy}) 置信度{m.confidence:.2f}'
            for m in matches
        ]
        return ToolResult("\n".join(lines))

    def _t_capture_template(self, args: dict) -> ToolResult:
        region = Region(int(args["left"]), int(args["top"]),
                        int(args["width"]), int(args["height"]))
        if region.width < 8 or region.height < 8 or region.width > 600 or region.height > 600:
            return ToolResult("模板区域不合理（宽高需在 8~600 像素之间，应只框住目标图标/按钮）")
        path = vision_cv.save_template(region, str(args["name"]))
        BUS.publish("action", action="capture_template", name=path.stem,
                    size=f"{region.width}x{region.height}")
        return ToolResult(f"已保存模板 \"{path.stem}\"，之后可用 find_image(\"{path.stem}\") 查找")

    def _t_color_probe(self, args: dict) -> ToolResult:
        probe = vision_cv.color_probe(int(args["x"]), int(args["y"]))
        return ToolResult(self._fmt(probe))

    def _t_vlm_describe(self, args: dict) -> ToolResult:
        if not vlm.is_configured():
            return ToolResult("VLM 未配置")
        if all(k in args for k in ("left", "top", "width", "height")):
            region = Region(int(args["left"]), int(args["top"]),
                            int(args["width"]), int(args["height"]))
        else:
            hwnd = self._resolve_hwnd(args)
            if not hwnd:
                return ToolResult("无目标窗口且未指定区域")
            region = window_region(hwnd)
        answer = vlm.describe(region, str(args["question"]))
        return ToolResult(answer)

    def _t_revise_plan(self, args: dict) -> ToolResult:
        steps = args.get("steps") or []
        if not isinstance(steps, list) or not steps:
            return ToolResult("revise_plan 需要非空的 steps 数组")
        self.plan = planner.Plan(steps)  # items may be str or {goal, check}
        # A new approach earns a fresh soft-fail bounce (per-approach, not per-run).
        self._fail_softened = False
        BUS.publish("agent_plan", action="revise", plan=self.plan.snapshot())
        return ToolResult(f"已更新计划：\n{self.plan.render()}")

    def _t_complete_subgoal(self, args: dict) -> ToolResult:
        if self.plan is None or not self.plan.active():
            return ToolResult("当前没有计划，可先调用 revise_plan 制定")
        index = int(args.get("index", -1))
        if not (1 <= index <= len(self.plan.steps)):
            return ToolResult(f"子目标序号 {index} 无效。\n{self.plan.render()}")
        # Subgoal verification gate: if the planner attached a deterministic
        # check, the subgoal can't be marked done until it actually passes.
        check_expect = self.plan.check_for(index)
        if check_expect:
            outcome = verify.check(check_expect, hwnd=self.hwnd, timeout_s=4.0)
            BUS.publish("agent_plan", action="subgoal_check", index=index,
                        verified=outcome.verified, detail=outcome.detail)
            if not outcome.verified:
                return ToolResult(
                    f"子目标 {index} 核验未通过（{outcome.detail}）——尚未真正完成，"
                    "请先达成该子目标再标完成，不要跳步。")
        self.plan.complete(index)
        BUS.publish("agent_plan", action="complete", index=index,
                    plan=self.plan.snapshot())
        if self.plan.all_done():
            return ToolResult(
                f"已完成子目标 {index}（已核验）。所有子目标均完成——若任务确已达成请调用 done。\n"
                f"{self.plan.render()}")
        return ToolResult(f"已完成子目标 {index}{'（已核验）' if check_expect else ''}。\n"
                          f"{self.plan.render()}")

    def _t_remember(self, args: dict) -> ToolResult:
        note = str(args.get("note", "")).strip()
        if not note:
            return ToolResult("remember 需要 note 内容")
        recipe = experience.add(note, app=str(args.get("app", "")).strip(),
                                steps=[note])
        BUS.publish("memory", action="remember", id=recipe["id"], note=note[:120])
        return ToolResult(f"已记住（id={recipe['id']}）: {note}")

    def _t_forget(self, args: dict) -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            return ToolResult("forget 需要 query 关键词")
        removed = experience.delete_matching(query)
        if not removed:
            return ToolResult(f'没有匹配 "{query}" 的记忆条目')
        names = "；".join(r.get("task", "")[:60] for r in removed)
        BUS.publish("memory", action="forget", count=len(removed), query=query)
        return ToolResult(f"已删除 {len(removed)} 条记忆: {names}")

    def _t_reuse_skill(self, args: dict) -> ToolResult:
        from ..recorder import trace as trace_mod

        task = str(args.get("task", "")).strip()
        recipe_id = str(args.get("recipe_id", "")).strip()
        script, recipe = experience.reusable_script_for(task or self.task, recipe_id)
        if not script:
            return ToolResult(
                "没有可复用的成功流程脚本（该任务无匹配的已验证历史）。请改用实时操作。")
        from ..config import SCRIPTS_DIR
        script_path = SCRIPTS_DIR / script
        if not script_path.exists():
            return ToolResult(f"复用脚本 {script} 已丢失，请实时操作。")
        BUS.publish("memory", action="reuse", task=(recipe or {}).get("task", task))
        try:
            result = trace_mod.replay(script_path, stop_event=self.stop_event)
        except Exception as exc:
            return ToolResult(f"复用重放异常: {exc}。请改用实时操作。")
        steps = result.get("results", [])
        if result.get("ok"):
            return ToolResult(
                f"✅ 已 0-token 复用上次成功流程（{len(steps)}步全部重放成功）。"
                "请 observe 确认最终结果是否符合预期，符合即可 done。")
        # partial failure → tell the agent where to take over live
        failed = next((s for s in steps if not s.get("ok")), None)
        at = failed.get("index") if failed else len(steps)
        return ToolResult(
            f"⚠️ 复用重放进行到第 {at} 步（{failed.get('tool') if failed else '?'}）失败，"
            f"前 {at-1} 步已执行。请先 observe 看当前界面，从第 {at} 步起改用实时操作完成剩余部分。")

    def _t_done(self, args: dict) -> ToolResult:
        summary = str(args.get("summary", ""))
        return ToolResult(f"任务完成: {summary}", terminal="done")

    def _t_fail(self, args: dict) -> ToolResult:
        reason = str(args.get("reason", ""))
        # Macro recovery: don't give up on the first fail — force one change of
        # approach. Only the second fail (or when disabled) truly terminates.
        if self.replan_on_fail and not self._fail_softened:
            self._fail_softened = True
            BUS.publish("agent_recovery", reason=reason[:200])
            return ToolResult(
                f"在放弃前请再换一种思路尝试一次（你给的失败原因：{reason}）。"
                "建议：重新 observe 确认当前真实界面；检查是否有未处理的弹窗/遮挡；"
                "能否改用 run_shell 直接命令行完成；是否漏看了某个元素或其它窗口。"
                "若确认所有可行办法都已尝试且无解，再次调用 fail 即真正结束。",
                terminal=None,
            )
        return ToolResult(f"任务失败: {reason}", terminal="fail")
