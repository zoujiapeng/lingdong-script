"""
Tool Path Selector — 工具访问模式分类 + 路径选择器

Agent 不再平铺 196 个工具，而是先选路径再选工具。
路径优先级: DIRECT_API(0) < BROWSER_CDP(1) < WINDOWS_UIA(2) < VISUAL_OCR(3) < VISUAL_SOM(4) < MACRO(5) < DSL_GEN(6)

每条路径有:
  - priority: 越高越慢, 越低越可靠
  - tools: 该路径下可用的工具集合
  - condition: 什么时候应该用这条路径
"""

from __future__ import annotations
import re
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Dict, List, Optional


class AccessPattern(IntEnum):
    """访问模式 — 值越低优先级越高（越快/越可靠）。"""
    DIRECT_API = 0   # 直接 API 调用, 不需要 UI 交互
    BROWSER_CDP = 1  # 浏览器 CDP 协议
    WINDOWS_UIA = 2  # Windows UIA 无障碍树
    VISUAL_OCR = 3   # 截图 + OCR 文字定位
    VISUAL_SOM = 4   # 截图 + 元素检测 + 编号标号
    MACRO = 5        # 宏录制/回放
    DSL_GEN = 6      # DSL 脚本生成 + 执行

    @property
    def label_cn(self) -> str:
        return _LABELS_CN[self]

    @property
    def cost(self) -> str:
        return _COST[self]

    @property
    def guidance(self) -> str:
        return _GUIDANCE[self]


_LABELS_CN = {
    AccessPattern.DIRECT_API: "直接 API",
    AccessPattern.BROWSER_CDP: "浏览器 CDP",
    AccessPattern.WINDOWS_UIA: "Windows UIA",
    AccessPattern.VISUAL_OCR: "OCR 文字定位",
    AccessPattern.VISUAL_SOM: "SoM 视觉标号",
    AccessPattern.MACRO: "宏回放",
    AccessPattern.DSL_GEN: "DSL 脚本生成",
}

_COST = {
    AccessPattern.DIRECT_API: "~0.1s, 无界面依赖",
    AccessPattern.BROWSER_CDP: "~0.5s, 仅浏览器标签页",
    AccessPattern.WINDOWS_UIA: "~0.5s, 仅 Windows 标准控件",
    AccessPattern.VISUAL_OCR: "~1-3s, 需截图 + OCR",
    AccessPattern.VISUAL_SOM: "~3-8s, 需截图 + 元素检测",
    AccessPattern.MACRO: "~0.5-5s, 需预录制",
    AccessPattern.DSL_GEN: "~3-10s + 1次LLM调用, 适合多步骤流程",
}

_GUIDANCE = {
    AccessPattern.DIRECT_API: "能用直接 API 的绝不用其他方式",
    AccessPattern.BROWSER_CDP: "操作网页优先用浏览器 CDP",
    AccessPattern.WINDOWS_UIA: "Windows 标准控件用 UIA 定位",
    AccessPattern.VISUAL_OCR: "纯文字目标用 OCR 坐标",
    AccessPattern.VISUAL_SOM: "复杂界面用 SoM 编号定位",
    AccessPattern.MACRO: "重复精确操作用宏回放",
    AccessPattern.DSL_GEN: "多步骤复杂流程先生成 DSL 再执行",
}


# ── 工具分类规则 ────────────────────────────────────────────
# 规则比手工列表更易维护且不会与注册表冲突
# 对于无法被规则覆盖的例外工具使用显式 override

@dataclass
class ToolClass:
    """工具分类结果。"""
    tool_name: str
    pattern: AccessPattern
    category_note: str = ""  # 简短说明如 "系统工具/文件操作"


def classify_tool(tool_name: str, category: str = "", description: str = "") -> AccessPattern:
    """根据工具名和分类自动判断访问模式。

    参数:
        tool_name: @feature 的工具名
        category: FeatureCategory.value (action/perception/system/etc)
        description: 工具描述文本（用于兜底判断）

    返回:
        AccessPattern 枚举
    """
    name = tool_name.lower()

    # ── 浏览器 CDP (所有 browser_* 前缀) ──
    if name.startswith("browser_") or name == "web_browser_execute":
        return AccessPattern.BROWSER_CDP

    # ── Windows UIA (所有 a11y_* 前缀) ──
    if name.startswith("a11y_"):
        return AccessPattern.WINDOWS_UIA

    # ── 宏 (所有 macro_* 前缀) ──
    if name.startswith("macro_"):
        return AccessPattern.MACRO

    # ── DSL / AI 规划 ──
    if category == "ai":
        return AccessPattern.DSL_GEN
    if name in ("generate_flow_script",):
        return AccessPattern.DSL_GEN

    # ── 视觉 OCR ──
    if name in ("ocr_find_text", "ocr_extract_all", "find_element"):
        return AccessPattern.VISUAL_OCR
    if name == "popup_close":  # 通常需要 OCR 找关闭按钮
        return AccessPattern.VISUAL_OCR

    # ── 视觉 SoM / 图像匹配 ──
    if name in ("screenshot", "screenshot_region_live", "region_select", "wheel_select"):
        return AccessPattern.VISUAL_SOM
    if name in ("find_image", "image_click", "search_images", "select_images",
                "color_check", "detect_color", "detect_progress",
                "scene_match", "find_canvas",
                "wait_element", "wait_region", "wait_screen_change", "wait_screen_stable"):
        return AccessPattern.VISUAL_SOM
    if name.startswith("camera_") or name.startswith("screen_record_"):
        return AccessPattern.VISUAL_SOM

    # ── 混合型 (可根据上下文走 UIA / OCR / SoM / 直接坐标) ──
    # 这些工具不绑定到单一模式
    hybrid_tools = frozenset({
        "click_target", "type_text", "type_clipboard", "hotkey", "scroll", "drag",
        "capture_multi", "menu_click",
        "filedialog_open", "filedialog_save", "filedialog_folder",
    })
    if name in hybrid_tools:
        return AccessPattern.WINDOWS_UIA  # 默认 UIA, 执行时根据上下文调整

    # ── 直接 API (系统工具、文件操作、进程管理、剪贴板等) ──
    # 规则: system 分类 + 非 visual/screen/camera
    if category == "system":
        # 排除视觉相关的 system 工具
        if name in ("screenshot", "screen_record_start", "screen_record_stop",
                     "camera_record_start", "camera_record_stop"):
            return AccessPattern.VISUAL_SOM
        return AccessPattern.DIRECT_API

    if category == "control":
        return AccessPattern.DIRECT_API

    if category == "perception":
        # perception 中未匹配的归入 VISUAL_SOM
        return AccessPattern.VISUAL_SOM

    # ── 兜底 ──
    if category in ("action", "debug"):
        return AccessPattern.DIRECT_API

    return AccessPattern.DIRECT_API


# ── 从 agent 提示词中隐藏的工具（调试/内部/开发用）──
_AGENT_HIDDEN = frozenset({
    "debug_generate_test_flow", "debug_get_logs", "debug_list_features",
    "debug_run_tests", "debug_system_info", "debug_validate_dsl",
    "exec_delete_checkpoint", "exec_get_checkpoint", "exec_list_checkpoints",
    "exec_telemetry_stats", "exec_vault_get", "exec_vault_list", "exec_vault_set",
    "crystallized_list", "export_template_to_file", "save_test_flow",
    "ai_plan_history", "ai_plan_list",
    "dsl_optimize_analyze",
})

# ── 关键工具的"适用场景"提示 ──
_AGENT_HINTS = {
    "hotkey": (
        "Use for keyboard shortcuts: Ctrl+S save, Alt+Tab switch windows, "
        "Ctrl+C copy, Ctrl+V paste, Esc close menu, Alt+F4 close window, "
        "Win+D show desktop. Format: single key 'esc'/'tab', combo 'ctrl+s'/'alt+tab'."
    ),
    "handle_dialog": (
        "Call AFTER click/type/hotkey when a popup/dialog appears "
        "(save confirm, overwrite confirm, error alert, permission request). "
        "Auto-detects and closes all visible dialogs. No params needed."
    ),
    "fill_save_dialog": (
        "Use AFTER hotkey(ctrl+s) shows a save-file dialog. "
        "Path param = full path like C:\\Users\\name\\Desktop\\file.txt. "
        "timeout = seconds to wait for dialog to appear."
    ),
    "click_target": (
        "Click screen text/icon/button by description. target = button text "
        "like 'OK', 'Save', 'Search'. region optional: 'x,y,w,h'. "
        "Uses OCR + image matching internally."
    ),
    "type_clipboard": (
        "Paste text via clipboard (bypasses input method issues). "
        "More reliable than type_text for Chinese/special chars. Prefer this."
    ),
    "type_text": (
        "Type text character by character. May have IME issues with Chinese. "
        "If Chinese input fails, switch to type_clipboard."
    ),
    "focus_window": (
        "Bring window to front by title (partial match supported). "
        "If this fails repeatedly, try hotkey(alt+tab) or click_target on taskbar icon."
    ),
    "launch_program": (
        "Start program or open file/URL. target = 'notepad.exe' or full path. "
        "wait=true waits for window. If already running, activates existing window."
    ),
    "menu_click": (
        "Click Windows app menu bar. path format: 'File>Save As', 'Edit>Replace'. "
        "Works via Win32/UIA. Use menu_structure first to discover available menus."
    ),
    "menu_structure": (
        "Read menu structure of target window. Returns full menu tree. "
        "Call before menu_click to discover available menu items."
    ),
    "list_windows": (
        "List all visible windows. Call before launch_program to avoid "
        "re-launching already running apps."
    ),
    "get_active_window": (
        "Get current foreground window title, position, and process name."
    ),
    "screenshot": (
        "Save screen image for verification or later OCR. Full screen or region."
    ),
    "ocr_extract_all": (
        "Read all text from screen (dialog messages, error text, search results). "
        "Returns text blocks with positions."
    ),
    "ocr_find_text": (
        "Find specific text on screen and return its coordinates. "
        "Use before click_target to locate elements by text."
    ),
    # ── Mouse operations ──
    "right_click": (
        "Open context menu at target location. target = button text like "
        "'Refresh', 'Properties'. Uses OCR + image matching to find target."
    ),
    "hover": (
        "Hover cursor over element without clicking. Use to trigger tooltips, "
        "hover effects, or preview links. duration = seconds to hover."
    ),
    "mouse_move": (
        "Move cursor to target position or coordinates. Use for precise cursor "
        "positioning without clicking. target param recommended over raw coordinates."
    ),
    "select_text": (
        "Select text at target location for copy/delete. Double-clicks to select word. "
        "extend_to_line=true selects full line. Use before hotkey(ctrl+c) to copy."
    ),
    "middle_click": (
        "Middle-click target. In browsers this opens links in new tabs. "
        "In other apps may enable canvas panning/auto-scroll."
    ),
    "triple_click": (
        "Triple-click to select full paragraph or line of text. "
        "Use before hotkey(ctrl+c) to copy entire paragraph."
    ),
    # ── Window management ──
    "window_minimize": (
        "Minimize window to taskbar. title optional (empty = current active window). "
        "Use before switching to another app to keep desktop clean."
    ),
    "window_maximize": (
        "Maximize window to full screen. title optional (empty = current active window). "
        "Use for full-screen work or before taking screenshots."
    ),
    "window_restore": (
        "Restore minimized/maximized window to normal size. "
        "title optional (empty = current active window)."
    ),
    "window_close": (
        "Close window by sending close signal. title optional (empty = current window). "
        "force=true terminates the process. Safer than hotkey(alt+f4) for specific windows."
    ),
    "window_resize": (
        "Resize window to exact width x height pixels. "
        "title optional (empty = current active window). Use for precise layout control."
    ),
    "window_snap": (
        "Snap window to screen region: left/right/top/bottom or quadrant. "
        "Use for side-by-side multitasking. title optional (empty = current window)."
    ),
    # ── System control ──
    "volume_set": (
        "Set system master volume to percentage (0-100). "
        "Use for audio control without opening any settings panel."
    ),
    "volume_mute": (
        "Toggle system mute. mute=true to mute, mute=false to unmute, "
        "omit to toggle. Returns current muted state and volume level."
    ),
    "display_brightness": (
        "Set display brightness percentage (0-100) for laptop screens. "
        "display_index=0 for main monitor. Uses WMI API."
    ),
    "lock_workstation": (
        "Lock the workstation immediately (equivalent to Win+L). "
        "Use when stepping away from computer. No parameters needed."
    ),
    "battery_status": (
        "Get battery percentage, charging status, and remaining time. "
        "Returns dict with percentage/charging/remaining_sec. No parameters needed."
    ),
    # ── Accessibility (UIA) ──
    "a11y_get_tree": (
        "Read full accessibility tree of active window. Returns element hierarchy "
        "with names, roles, and bounds. More precise than OCR for standard controls."
    ),
    "a11y_find_element": (
        "Find UI element by name and/or role in accessibility tree. "
        "Use before a11y_click_element to verify the target exists."
    ),
    "a11y_click_element": (
        "Click UI element by name via accessibility API. Most reliable method for "
        "standard Windows controls (buttons, checkboxes, menus). No OCR needed."
    ),
    # ── Network ──
    "wifi_list": (
        "Scan and list available WiFi networks with SSID and signal strength. "
        "Use before wifi_connect to discover available networks."
    ),
    "wifi_connect": (
        "Connect to WiFi network by SSID. password required for secured networks. "
        "Automatically creates WLAN profile if password given."
    ),
    "wifi_disconnect": (
        "Disconnect from current WiFi network. "
        "Use before connecting to a different network."
    ),
    # ── Process ──
    "detect_process": (
        "Check if a process is running. Returns true/false. "
        "Use before launch_program to avoid re-launching, or before kill_process."
    ),
    "find_processes": (
        "Find all matching processes with PID, CPU, memory details. "
        "More detailed than detect_process. Use for monitoring and diagnostics."
    ),
    "get_cursor_pos": (
        "Get current mouse cursor screen coordinates (x, y) and the window "
        "under the cursor. Use before mouse_move or click to verify position."
    ),
    "send_notification": (
        "Show a Windows taskbar balloon notification with title and message. "
        "Use to alert user when a long task completes or an error occurs."
    ),
}


def get_tools_by_pattern() -> Dict[AccessPattern, List[dict]]:
    """从注册表加载所有工具并按访问模式分组。

    返回:
        {AccessPattern: [{name, desc, params, pattern_note}, ...], ...}
    """
    from features.registry import registry

    result: Dict[AccessPattern, List[dict]] = {p: [] for p in AccessPattern}

    for spec in registry.all():
        if spec.deprecated or spec.hidden:
            continue
        if spec.name in _AGENT_HIDDEN:
            continue
        pattern = classify_tool(
            spec.name, spec.category.value if spec.category else "",
            spec.description
        )
        desc = spec.description or ""
        hint = _AGENT_HINTS.get(spec.name)
        if hint:
            desc = desc.rstrip(".。") + ". " + hint
        result[pattern].append({
            "name": spec.name,
            "desc": desc,
            "params": [{"name": p.name, "type": p.type, "required": p.required, "desc": p.description}
                       for p in spec.params],
        })

    return result


def build_path_prompt() -> str:
    """构建按访问模式组织的系统提示词文本。

    替代 agent.py 中 _build_tools_text() 的平面工具列表。
    按路径优先级从高到低排列，每个路径下显示可用工具。
    """
    from features.registry import registry

    # 确保注册表加载
    import features._all  # noqa: F401

    grouped = get_tools_by_pattern()

    lines = [
        "## 工具选择策略（按优先级从高到低）",
        "",
        "核心原则: 能用直接 API 的绝不用视觉定位，能走 CDP 的不用截图点击。",
        "每次选择工具前，先判断当前任务属于哪个访问模式，再从对应分组选工具。",
        "",
    ]

    path_order = [
        AccessPattern.DIRECT_API,
        AccessPattern.BROWSER_CDP,
        AccessPattern.WINDOWS_UIA,
        AccessPattern.VISUAL_OCR,
        AccessPattern.VISUAL_SOM,
        AccessPattern.MACRO,
        AccessPattern.DSL_GEN,
    ]

    skipped_counts = {p: len(grouped.get(p, [])) for p in path_order}
    total_tools = sum(skipped_counts.values())

    # ── 简短表格概览 ──
    lines.append("### 路径概览")
    lines.append("")
    lines.append(f"| 优先级 | 模式 | 成本 | 工具数 | 适用场景 |")
    lines.append(f"|--------|------|------|--------|----------|")
    for p in path_order:
        count = skipped_counts.get(p, 0)
        if count == 0:
            continue
        lines.append(f"| **L{p.value}** | {p.label_cn} | {p.cost} | {count} | {p.guidance} |")
    lines.append("")

    # ── 每路径详细工具列表 ──
    for p in path_order:
        tools = grouped.get(p, [])
        if not tools:
            continue
        lines.append(f"---")
        lines.append(f"### L{p.value}: {p.label_cn} ({p.guidance})")
        lines.append(f"成本: {p.cost}  |  按照此项选择工具时，你应已确认当前上下文适合此模式")
        lines.append("")
        lines.append("可用工具:")
        for t in sorted(tools, key=lambda x: x["name"]):
            params_str = ", ".join(
                f"{pp['name']}:{pp['type']}{'?' if not pp['required'] else ''}"
                for pp in t["params"][:4]
            )
            if t["params"]:
                lines.append(f"  - **{t['name']}**({params_str}) — {t['desc'][:300]}")
            else:
                lines.append(f"  - **{t['name']}** — {t['desc'][:300]}")

    lines.append("")
    lines.append(f"---")
    lines.append(f"共 {total_tools} 个可用工具，分布在 {sum(1 for p in path_order if skipped_counts.get(p,0)>0)} 个访问模式中。")
    lines.append("选工具路径的优先级: 数字越小越优先。")

    return "\n".join(lines)


def select_best_path(goal: str) -> Optional[AccessPattern]:
    """为目标选择最合适的工具访问路径。

    通过规则匹配目标文本中的关键词，返回最高优先级的路径。
    如果无法确定，返回 None（让 LLM 自行判断）。
    """
    goal_lower = goal.lower()

    # DSL 生成 — 需要多步骤复杂流程
    dsl_keywords = [
        "多步骤", "工作流", "流程", "自动化", "序列", "依次",
        "先...然后", "第一步", "第二步",
    ]
    if any(kw in goal_lower for kw in dsl_keywords):
        return AccessPattern.DSL_GEN

    # 浏览器任务
    browser_keywords = [
        "打开网页", "搜索", "浏览器", "网页", "网站", "baidu", "google",
        "百度", "谷歌", "bing", "网址", "上网", "浏览",
        "下载", "上传", "登录", "注册", "表单", "提交",
    ]
    # 排除桌面/文件搜索场景
    desktop_search_hints = ["文件搜索", "搜索文件", "搜索桌面", "本地搜索", "硬盘搜索"]
    if any(kw in goal_lower for kw in desktop_search_hints):
        pass  # 不走浏览器
    elif any(kw in goal_lower for kw in browser_keywords):
        return AccessPattern.BROWSER_CDP

    # 系统设置 — 直接 API
    system_keywords = [
        "音量", "声音", "静音", "亮度", "关机", "重启", "注销",
        "休眠", "睡眠", "壁纸", "桌面", "回收站",
        "wifi", "网络", "电池", "电源",
        "锁屏", "锁定", "显示器", "关闭显示器",
    ]
    if any(kw in goal_lower for kw in system_keywords):
        return AccessPattern.DIRECT_API

    # 文件操作 — 直接 API
    file_keywords = [
        "文件", "目录", "文件夹", "读取", "写入", "保存", "复制", "移动",
        "删除", "创建", "重命名", "路径",
    ]
    if any(kw in goal_lower for kw in file_keywords):
        return AccessPattern.DIRECT_API

    # 进程/窗口管理 — direct API
    process_keywords = [
        "打开", "启动", "运行", "关闭", "结束", "进程", "窗口", "切换到",
    ]
    if any(kw in goal_lower for kw in process_keywords):
        return AccessPattern.DIRECT_API

    # 剪贴板 — direct API
    clipboard_keywords = [
        "剪贴板", "复制", "粘贴",
    ]
    if any(kw in goal_lower for kw in clipboard_keywords):
        return AccessPattern.DIRECT_API

    # 无明确匹配，返回 None
    return None


def build_system_prompt() -> str:
    path_prompt = build_path_prompt()
    prompt = f"""你是灵动脚本 (Lingdong Script) 的 AI Agent，可以通过大量工具操控 Windows 电脑完成任何桌面自动化任务。

## 工作方式

1. 接到目标后，先判断任务属于哪个访问模式
2. 按优先级选择路径：先用最快的 DIRECT API，不行再降级到视觉方法
3. 在选定的路径分组中选择合适的工具
4. 执行工具，验证结果，必要时调整路径

## 核心原则

- 能直接 API 的绝不视觉定位: 系统设置、文件操作、进程管理等有直接 API
- 能 CDP 的绝不截图点击: 浏览器操作必须用 browser_* 工具
- 能 UIA 的绝不 OCR: Windows 标准控件用无障碍树定位
- 先试最快路径，失败再降级

{path_prompt}

## 决策规则

1. 分类优先: 接到目标先用关键词判断属于哪类任务，再选路径
2. 路径降级: 如果当前路径执行失败，降级到下一优先级路径重试
3. 浏览器必须用 CDP: 涉及网页操作，必须用 browser_* 工具
4. 工具调用后必须验证: 每次检查返回的 success 字段
5. 连续失败换路径: 同一工具连续 2 次失败则换路径

6. 回复格式:


7. 任务完成:


## 常见桌面操作工作流

### 启动程序并操作
launch_program -> wait -> focus_window -> type_clipboard/click_target
示例: 打开记事本输入文字 -> launch_program("notepad.exe") -> wait -> type_clipboard("Hello")

### 窗口切换
如果 focus_window 失败，按顺序尝试:
1. hotkey("alt+tab") - 切换到上一个窗口
2. click_target(target="窗口标题") - 点击任务栏图标
3. 再次 focus_window

### 保存文件
1. hotkey("ctrl+s") - 触发保存快捷键
2. fill_save_dialog(path="完整保存路径", timeout=10) - 填写保存对话框
如果 fill_save_dialog 不可用，用 filedialog_save 或 handle_dialog

### 处理弹窗
每次 click/type/hotkey 操作后，如果可能出现了弹窗:
- 调用 handle_dialog() 自动关闭确认/错误/警告弹窗
- 然后继续下一步

### 点菜单
需要点击应用菜单时:
1. menu_structure(title="窗口标题") - 查看可用菜单项
2. menu_click(title="窗口标题", path="文件>另存为") - 执行菜单命令

### 浏览器操作
涉及网页时，必须用 browser_* 工具，不要截图点击:
1. browser_connect() - 连接/启动 Chrome
2. browser_navigate(url) - 导航到目标网页
3. browser_type(selector="#search", text="关键词") - 输入搜索
4. browser_click(selector="#search-btn") - 点击搜索按钮
"""
    return prompt

