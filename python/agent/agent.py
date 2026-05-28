"""
LingdongAgent — 统一的桌面自动化 AI Agent

整合：
  - MimoAgent: 文本协议 LLM 循环 (工具描述系统提示词, 解析回复, 截图验证)
  - LangGraphAgent: 监督执行层 (窗口焦点, 重试, 对话框处理, 屏幕稳定, 视觉定位)
  - AgentIPC: 事件协议兼容

双模式执行：
  - text_protocol: 适用于 MIMO V2 Omni 等纯文本模型 (不支持 function calling)
  - langgraph: 适用于 DeepSeek/GPT/Claude 等支持函数调用的模型

用法：
    agent = LingdongAgent(api_key="...", event_callback=my_cb)
    result = agent.run("打开计算器", mode="auto")
"""

import base64
import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("agent.lingdong")

DEFAULT_API_BASE = "https://api.xiaomimimo.com/v1"
DEFAULT_MODEL = "mimo-v2-omni"
MAX_STEPS = 50

# ── 文本协议模式系统提示词 ─────────────────────────────────────

SYSTEM_PROMPT_TEXT = """你是灵动脚本 (Lingdong Script) 的 AI Agent，可以通过 {tool_count} 个工具操控 Windows 电脑完成任何桌面自动化任务。

## 工作方式

每次我给你：
1. 当前屏幕截图
2. 之前操作的结果

你需要：
1. 观察截图，理解当前桌面状态
2. 思考：当前处于什么状态？下一步该做什么？
3. 从下方场景分组中选择合适的工具

## 回复格式

你必须严格按照以下格式回复：

```
思考: 你的分析过程...
工具: 工具名称
参数:
  参数名1: 值1
  参数名2: 值2
```

如果你认为任务已完成，回复：
```
总结: 完成了什么, 结果如何
```

## 可用工具（按使用场景分组）

{tools_text}

---

## 典型工作流

### 示例1: 桌面应用操作（通用模式）
适用于任何桌面应用：QQ/微信发消息、记事本、设置、安装程序等。

```
1. launch_program(target="应用名")      # 启动(已运行则自动聚焦)
2. wait_process_start(name="进程名")     # 等进程起来
3. focus_window(title="窗口标题")        # 确保前台
4. screenshot()                         # 截图看状态
5. popup_close()                        # 清理弹窗
6. ocr_find_text(query="目标文字")       # OCR找目标
7. click_target(target="目标文字")       # 点击
8. wait_screen_change(timeout=3)        # 等界面变化
9. screenshot()                         # 截图验证
10. 重复 定位→点击→验证 直到任务完成
```

关键模式：任何点击/输入后必须截图验证效果，不要假设操作成功。

### 示例2: 浏览器自动化
适用于网页操作、数据提取、表单填写。

```
1. browser_navigate(url="https://...")   # 打开网页
2. browser_wait_page_load(timeout=10)    # 等加载
3. browser_find_elements(selector="css") # 看页面结构
4. browser_click(text="链接文字")         # 点击
5. browser_wait_navigation(timeout=5)    # 等跳转
6. browser_type(selector="#input", text="xxx") # 输入
7. browser_get_text(selector=".result")  # 提取内容
8. browser_screenshot()                  # 截图留存
```

### 示例3: 错误恢复
工具返回 success=true 但屏幕没变化时：

```
1. screenshot()                          # 确认当前状态
2. 如果确实没变化:
   a. click_target 无效 → 先用 ocr_find_text 取坐标再点
   b. type_text 无效 → 改用 type_clipboard（中文）
   c. 找不到窗口 → list_windows() 查实际标题
3. 连续3次同工具失败 → 换完全不同的方法
```

## 重要规则

1. **启动 vs 命令**: launch_program 启动GUI(非阻塞)，run_command 执行命令(阻塞有返回值)
2. **中文输入**: 必须用 type_clipboard 而非 type_text（绕过输入法）
3. **验证**: 每次操作后必须截图验证，不可假设成功
4. **弹窗**: 操作前先 popup_close() 清理干扰
5. **浏览器优先**: 网页操作优先用 browser_*，不用桌面通用点击
6. **文件对话框**: 用 filedialog_* 工具，不要用 click_target 点对话框
7. **失败换策略**: 连续2次失败必须换完全不同方法，禁止重复同一工具
8. **编号定位优先**: 如果截图上有蓝色框和 #N 编号，用编号定位（target: #3）代替文字描述，
   精度更高。编号在屏幕元素列表中可以查到对应元素。
9. **目标达成后**回复总结（不要继续多余操作）
"""

# ── 工具描述生成 (from MimoAgent) ─────────────────────────────

def _build_tools_text() -> str:
    """生成按场景分组的工具描述文本。"""
    from features.registry import registry
    import features._all  # noqa

    spec_map = {}
    for spec in registry.all():
        if spec.deprecated or spec.hidden:
            continue
        spec_map[spec.name] = spec

    def _fmt(spec, max_desc=80):
        if not spec:
            return ""
        key_params = []
        for p in spec.params:
            if p.required:
                key_params.append(p.name)
            elif p.name in ("timeout", "region", "threshold", "similarity",
                            "delay", "confirm", "target", "path", "keys",
                            "checked", "query", "value", "direction",
                            "name", "amount", "text", "action"):
                key_params.append(p.name)
        param_str = f"({', '.join(key_params[:4])})" if key_params else "()"
        desc = spec.description[:max_desc].split("\n")[0].strip()
        desc = re.sub(r"^【[^】]*】\s*", "", desc)
        return f"  {spec.name}{param_str} → {desc}"

    browser_tools = {s.name for s in spec_map.values()
                     if s.name.startswith("browser_") or s.name == "web_browser_execute"}
    vision_tools = {
        "screenshot", "ocr_find_text", "ocr_extract_all", "find_image",
        "find_element", "find_canvas", "color_check", "detect_color",
        "detect_progress", "scene_match", "wait_element", "popup_close",
        "wait_region", "wait_screen_change", "wait_screen_stable",
    }
    click_tools = {
        "click_target", "image_click", "capture_multi", "type_text",
        "type_clipboard", "hotkey", "drag", "scroll", "wheel_select",
        "region_select", "menu_click",
        "filedialog_open", "filedialog_save", "filedialog_folder",
    }
    window_tools = {
        "launch_program", "focus_window", "list_windows", "get_active_window",
        "kill_process", "wait_process_start", "wait_process_exit",
        "detect_process", "find_processes", "show_desktop",
    }
    system_tools = {
        "run_command", "network_status", "volume_set", "volume_mute",
        "display_brightness", "display_off", "shutdown", "reboot",
        "logoff", "hibernate", "system_sleep", "set_wallpaper",
        "battery_status", "empty_recycle_bin",
    }
    file_tools = {
        "file_read", "file_write", "file_edit", "file_append",
        "file_delete", "file_copy", "file_move", "file_list", "file_exists",
    }
    ai_tools = {
        "vlm_analyze", "dsl_to_flow", "flow_to_dsl", "create_flow_from_dsl",
        "run_dsl", "run_dsl_sync", "run_dsl_robot", "ai_generate_dsl",
        "ai_plan_and_execute", "wait_action", "wait_condition",
        "set_variable", "get_variable", "generate_flow_script",
        "search_flow_templates",
    }
    macro_tools = {
        "macro_start_recording", "macro_stop_recording", "macro_playback",
        "macro_list", "macro_delete", "macro_get_events",
        "macro_convert_to_flow",
        "macro_start_visual_recording", "macro_stop_visual_recording",
        "macro_extract_visual_templates",
    }
    camera_tools = {
        "camera_list", "camera_capture", "camera_detect_motion",
        "camera_record_start", "camera_record_stop",
        "screen_record_start", "screen_record_stop",
    }

    groups = [
        ("1. 浏览器操作 — Browser",
         "操作网页/Web应用时优先使用此组工具。自动管理浏览器生命周期。",
         browser_tools),
        ("2. 截图与视觉识别 — Screenshot & OCR",
         "获取屏幕信息、定位文字/图标、判断画面状态。每步操作后建议截图验证。",
         vision_tools),
        ("3. 目标点击与输入 — Click & Input",
         "点击屏幕元素、输入文字、快捷键、文件对话框。中文务必用type_clipboard。",
         click_tools),
        ("4. 窗口与进程管理 — Window & Process",
         "启动/关闭程序、切换窗口。GUI用launch_program(非阻塞)，命令用run_command(阻塞有返回值)。",
         window_tools),
        ("5. 系统控制与命令 — System",
         "Shell命令执行、电源管理、音量、网络等系统操作。",
         system_tools),
        ("6. 文件操作 — File",
         "读写编辑文件、目录浏览、文件复制移动删除。使用绝对路径。",
         file_tools),
        ("7. AI规划与DSL执行 — AI & DSL",
         "自然语言生成自动化流程、执行DSL脚本、VLM分析画面。",
         ai_tools),
        ("8. 宏录制与回放 — Macro",
         "录制操作序列并回放。适合需要精确重现的操作流程。",
         macro_tools),
        ("9. 摄像头与录屏 — Camera & Screen",
         "摄像头拍照/录像/运动检测、屏幕录制。",
         camera_tools),
    ]

    all_grouped = set()
    lines = []
    for title, scenario, tool_set in groups:
        all_grouped.update(tool_set)
        tools_in_group = [s for s in spec_map.values() if s.name in tool_set]
        if not tools_in_group:
            continue
        lines.append(f"\n### {title}")
        lines.append(f"   {scenario}")
        for t in sorted(tools_in_group, key=lambda x: x.name):
            lines.append(_fmt(t))

    ungrouped = {n: s for n, s in spec_map.items() if n not in all_grouped}
    if ungrouped:
        from collections import defaultdict
        by_cat = defaultdict(list)
        for name, spec in sorted(ungrouped.items()):
            cat_name = spec.category.value.upper() if spec.category else "其他"
            by_cat[cat_name].append(name)
        lines.append("\n### 10. 其他辅助工具（按类别汇总，需要时查看详情）")
        for cat in sorted(by_cat.keys()):
            names = by_cat[cat]
            lines.append(f"   {cat} ({len(names)}): {' / '.join(sorted(names))}")

    return "\n".join(lines)


# ====================================================================
#  LingdongAgent — 统一 Agent
# ====================================================================

class LingdongAgent:
    """统一的桌面自动化 AI Agent。

    属性:
        api_key / api_base / model: LLM 配置
        multimodal: 是否启用多模态视觉定位 (VLM 兜底)
        auto_learn: 是否自动学习 (截图→经验库)
        event_callback: IPC 事件回调
        stop_event: 停止信号
    """

    # GUI 动作集合 (受益于窗口焦点 + 重试 + 屏幕稳定)
    GUI_ACTIONS = frozenset({
        "click_target", "right_click", "hover", "mouse_move",
        "type_text", "type_clipboard",
        "hotkey", "scroll", "drag", "swipe",
        "triple_click", "middle_click", "select_text",
    })

    # 不需要截图的工具 (读取类操作)
    NO_SCREENSHOT_TOOLS = frozenset({
        "list_windows", "get_active_window", "find_element", "ocr_find_text",
        "ocr_extract_all", "get_variable", "file_read", "file_list",
        "battery_status", "network_status", "detect_process",
        "find_processes",
    })

    # ── 初始化 ────────────────────────────────────────────────

    def __init__(self, api_key: str = "", api_base: str = "", model: str = "",
                 event_callback: Optional[Callable] = None,
                 stop_event: Optional[threading.Event] = None,
                 multimodal: bool = False,
                 auto_learn: bool = False):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
        self.api_base = (api_base or os.environ.get("OPENAI_API_BASE", "")
                         or os.environ.get("LLM_API_BASE", "")
                         or DEFAULT_API_BASE).rstrip("/")
        self.model = model or os.environ.get("VLM_MODEL", "") or os.environ.get("LLM_MODEL", "") or DEFAULT_MODEL
        self.multimodal = multimodal
        self.auto_learn = auto_learn
        self._event_cb = event_callback
        self._stop_event = stop_event or threading.Event()
        self._last_launched: str = ""
        self._tools_text: str = ""
        self._system_prompt: str = ""
        self._registry_loaded = False
        self._som_enabled = True  # SoM 标号（截图元素编号）
        self._last_som_map: dict[int, dict] = {}  # 编号→{text,x,y,w,h}
        self._last_som_ts: float = 0  # 标号截图的像素时间戳
        self._omniparser = None  # OmniParser client (lazy init)

        # Export env vars for downstream features
        if self.api_key:
            os.environ.setdefault("ANTHROPIC_API_KEY", self.api_key)
            os.environ.setdefault("OPENAI_API_KEY", self.api_key)
        if self.api_base:
            os.environ.setdefault("LLM_API_BASE", self.api_base)
        if self.model:
            os.environ.setdefault("LLM_MODEL", self.model)

    # ── 工具加载 ──────────────────────────────────────────────

    def load_tools(self):
        """加载 @feature 注册表，生成工具描述文本。"""
        from features.registry import registry
        import features._all  # noqa

        self._registry_loaded = True
        count = len([s for s in registry.all() if not s.deprecated and not s.hidden])
        self._tools_text = _build_tools_text()
        self._system_prompt = SYSTEM_PROMPT_TEXT.format(
            tool_count=count, tools_text=self._tools_text
        )
        logger.info("Registry loaded: %d tools, prompt: %d chars", count, len(self._system_prompt))

    def _ensure_registry(self):
        if not self._registry_loaded:
            self.load_tools()

    # ── 事件 ──────────────────────────────────────────────────

    def _emit(self, event: str, data: dict):
        """发送事件到回调 (IPC 协议兼容)。"""
        if self._event_cb:
            self._event_cb({"type": event, **data})

    # ══════════════════════════════════════════════════════════
    #  监督层 (共享于双模式)
    # ══════════════════════════════════════════════════════════

    def _ensure_safe_window(self, tool_name: str, args: dict) -> dict:
        """窗口焦点安全：GUI 操作前确认目标窗口已激活。"""
        try:
            target_hint = (
                args.get("target_window") or
                args.get("target") or
                args.get("title") or
                self._last_launched or ""
            )
            if not target_hint:
                return {"success": True, "focused": True}

            from features.registry import registry as r
            result = r.execute("get_active_window")
            active_data = result if isinstance(result, dict) else {}
            active_data = active_data.get("result", active_data)
            active_title = ""
            if isinstance(active_data, dict):
                active_title = active_data.get("title", "")

            if target_hint.lower() in active_title.lower():
                return {"success": True, "focused": True}

            r.execute("focus_window", title=target_hint)
            time.sleep(0.3)
            return {"success": True, "focused": True}
        except Exception as e:
            logger.warning("Window focus check failed: %s", e)
            return {"success": False, "focused": False, "error": str(e)}

    @staticmethod
    def _wait_screen_stable(timeout: float = 1.5):
        """等待屏幕稳定。"""
        time.sleep(min(timeout, 1.5))

    def _auto_handle_dialogs(self) -> bool:
        """自动关闭可见对话框。"""
        try:
            time.sleep(0.3)
            from agent.dialog_handler import handle_any_dialog
            result = handle_any_dialog()
            if result != "not_found":
                logger.info("Dialog handled: %s", result)
                return True
        except Exception:
            pass
        return False

    def _apply_visual_locator(self, name: str, args: dict) -> dict:
        """视觉定位：经验库 → VLM 兜底，注入坐标到 args。

        仅当 auto_learn 或 multimodal 开启时生效。
        (目前默认关闭，经验库自动学习暂不启用)
        """
        # 防御性检查：两标志都关时不执行任何定位/学习逻辑
        if not self.auto_learn and not self.multimodal:
            return args
        _MAP = {
            "click_target":   ("target", "click"),
            "right_click":    ("target", "click"),
            "hover":          ("target", "click"),
            "mouse_move":     ("target", "click"),
            "type_text":      ("text",   "type"),
            "type_clipboard": ("text",   "type"),
            "drag":           ("from_target", "drag"),
            "scroll":         ("target", "scroll"),
        }

        if name not in _MAP:
            return args

        target_param, step_type = _MAP[name]
        target_text = args.get(target_param, "")

        if not target_text or not isinstance(target_text, str):
            return args
        if re.match(r'^-?\d+$', target_text.strip()):
            return args
        if re.match(r'^-?\d+\s*,\s*-?\d+$', target_text.strip()):
            return args
        x_val, y_val = args.get("x", -1), args.get("y", -1)
        if isinstance(x_val, (int, float)) and isinstance(y_val, (int, float)):
            if x_val >= 0 and y_val >= 0:
                return args

        # Screenshot
        import pyautogui as _pg
        import tempfile
        ss_path = os.path.join(
            tempfile.gettempdir(),
            f"lg_vl_{int(time.time())}_{hash(target_text) & 0xFFFF}.png",
        )
        try:
            _pg.screenshot(ss_path)
        except Exception as exc:
            logger.warning("Visual locator screenshot failed: %s", exc)
            return args

        try:
            from agent import visual_locator as _vl
            vlm_config = None
            if self.multimodal and self.api_key:
                vlm_config = {
                    "api_key": self.api_key,
                    "api_base": self.api_base or "https://api.deepseek.com/v1",
                    "model": self.model or "deepseek-chat",
                }

            result = _vl.locate(
                target_text=target_text,
                step_type=step_type,
                screenshot_path=ss_path,
                use_vlm=self.multimodal,
                vlm_config=vlm_config,
                window_title="",
                threshold=0.75,
            )

            if result.get("success"):
                coords = result["result"]
                args = dict(args)
                cx = coords.get("center_x", coords.get("x", 0))
                cy = coords.get("center_y", coords.get("y", 0))
                args["x"] = int(cx)
                args["y"] = int(cy)
                if name == "click_target":
                    args["target"] = f"{cx},{cy}"
                self._emit("log", {
                    "level": "info",
                    "message": f"视觉定位: 「{target_text}」→ ({cx}, {cy}) [{coords.get('method', '?')}]",
                })
            else:
                self._emit("log", {
                    "level": "debug",
                    "message": f"视觉定位未命中: 「{target_text}」({result.get('error', '无匹配')})",
                })
        except Exception as exc:
            logger.warning("Visual locator error (non-fatal): %s", exc)
        finally:
            try:
                if os.path.exists(ss_path):
                    os.remove(ss_path)
            except Exception:
                pass

        return args

    @staticmethod
    def _execute_with_type_conv(name: str, args: dict) -> dict:
        """参数类型转换 + 注册表执行。"""
        from features.registry import registry
        spec = registry.get(name)
        if spec:
            converted = {}
            for p in spec.params:
                val = args.get(p.name)
                if val is None:
                    continue
                if p.type in ("number", "int"):
                    try:
                        converted[p.name] = int(val) if p.type == "int" else float(val)
                    except (ValueError, TypeError):
                        converted[p.name] = val
                elif p.type == "boolean":
                    if isinstance(val, str):
                        converted[p.name] = val.lower() in ("true", "1", "yes")
                    else:
                        converted[p.name] = bool(val)
                elif p.type == "array":
                    if isinstance(val, str):
                        try:
                            converted[p.name] = json.loads(val)
                        except json.JSONDecodeError:
                            converted[p.name] = [v.strip() for v in val.split(",") if v.strip()]
                    else:
                        converted[p.name] = val
                else:
                    converted[p.name] = str(val).strip("\"'") if not isinstance(val, str) else val.strip("\"'")
        else:
            converted = args
        return registry.execute(name, **converted)

    def _supervised_execute(self, name: str, args: dict) -> dict:
        """带完整监督层的工具执行。

        监督流水线:
          1. 视觉定位 (auto_learn / multimodal) → 注入坐标
          2. GUI 操作 → 窗口焦点安全
          3. 执行 + 重试 (GUI: 2 次退避重试, 其他: 1 次)
          4. GUI 操作后 → 对话框自动处理
          5. GUI 操作后 → 等待屏幕稳定
        """
        is_gui = name in self.GUI_ACTIONS

        # ── 0. SoM 编号解引用（#N → 坐标） ──
        if is_gui and self._last_som_map:
            som_target = args.get("target", "") or args.get("text", "") or args.get("from_target", "")
            if isinstance(som_target, str) and som_target.startswith("#"):
                try:
                    idx = int(som_target.lstrip("#").strip())
                    if idx in self._last_som_map:
                        m = self._last_som_map[idx]
                        args = dict(args)
                        args["target"] = f"{m['x']},{m['y']}"
                        self._emit("log", {
                            "level": "info",
                            "message": f"SoM#{idx}: 「{m['text']}」→ {m['x']},{m['y']}",
                        })
                except (ValueError, TypeError):
                    pass

        # ── 1a. UIA 语义定位（优先：免OCR免ML，Windows 标准控件全覆盖）──
        if is_gui and name in ("click_target", "right_click", "hover",
                                "mouse_move", "drag"):
            target = args.get("target", "") or args.get("from_target", "")
            if target and not re.match(r'^-?\d+\s*,\s*-?\d+$', str(target).strip()):
                try:
                    uia_match = self._resolve_via_a11y(target)
                    if uia_match:
                        args = dict(args)
                        args["target"] = f"{uia_match['x']},{uia_match['y']}"
                        self._emit("log", {
                            "level": "info",
                            "message": f"UIA定位: 「{target}」→ {uia_match['x']},{uia_match['y']} ({uia_match['role']})",
                        })
                except Exception as e:
                    logger.warning("UIA resolution error: %s", e)

        # ── 1b. 视觉定位（VLM / 经验库兜底，默认关闭）──
        if is_gui and (self.auto_learn or self.multimodal):
            try:
                args = self._apply_visual_locator(name, args)
            except Exception as e:
                logger.warning("Visual locator error: %s", e)

        # ── 2. 窗口焦点 ──
        if is_gui:
            self._ensure_safe_window(name, args)

        # ── 3. 执行 + 重试 ──
        max_retries = 2 if is_gui else 1
        result = {"success": False, "error": "no_attempt"}
        for attempt in range(max_retries + 1):
            if self._stop_event and self._stop_event.is_set():
                self._emit("log", {"level": "info", "message": f"停止请求，跳过 {name} 重试"})
                return {"success": False, "error": "stopped"}

            if attempt > 0:
                delay = 2 ** (attempt - 1)
                self._emit("log", {"level": "info", "message": f"重试 {name} ({attempt}/{max_retries}, {delay}s)"})
                time.sleep(delay)

            try:
                result = self._execute_with_type_conv(name, args)
                if result.get("success", False):
                    break
            except Exception as e:
                result = {"success": False, "error": str(e)}
                if attempt >= max_retries:
                    break

        # ── 4. 对话框处理 ──
        if is_gui or name in ("screenshot", "hotkey", "launch_program"):
            self._auto_handle_dialogs()

        # ── 5. 屏幕稳定 ──
        if is_gui:
            self._wait_screen_stable()

        # ── 6. 失败诊断：截图记录当前屏幕状态 ──
        if not result.get("success") and is_gui:
            try:
                b64 = self._capture_b64()
                a11y = self._capture_a11y_summary()
                self._emit("log", {
                    "level": "info",
                    "message": f"故障诊断: {name} 失败 — 当前画面(base64): data:image/jpeg;base64,{b64[:50]}..."
                })
                if a11y:
                    self._emit("log", {"level": "info", "message": a11y})
            except Exception:
                pass

        # ── 跟踪启动的程序 ──
        if name == "launch_program":
            target = args.get("target") or args.get("name") or ""
            if target:
                self._last_launched = target

        return result

    # ══════════════════════════════════════════════════════════
    #  文本协议模式 (MIMO V2 Omni 等纯文本模型)
    # ══════════════════════════════════════════════════════════

    def _capture_b64(self, max_size: int = 0) -> str:
        """全屏截图 → base64 JPEG。"""
        import cv2
        from perception.vision import ScreenCapture
        img = ScreenCapture.capture()
        if max_size:
            h, w = img.shape[:2]
            if max(h, w) > max_size:
                scale = max_size / max(h, w)
                new_w, new_h = int(w * scale), int(h * scale)
                img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])
        return base64.b64encode(buf).decode()

    @staticmethod
    def _capture_a11y_summary(max_items: int = 30) -> str:
        """捕获 UIA 无障碍控件树摘要，格式化为结构化文本。

        返回类似:
          [UI Controls]
          Button "确定" (520,410 80x30)
          Edit "搜索" (300,200 200x30)
          Text "欢迎" (100,100 50x20)

        用于作为 LLM 的结构化观察输入，与截图互补。
        """
        try:
            from features.registry import registry
            result = registry.execute("a11y_get_tree", depth=2)
            if not result.get("success"):
                return ""
            tree = result.get("result", result)
            if isinstance(tree, dict):
                tree = tree.get("result", tree)

            def _walk(node, depth=0):
                items = []
                if depth > 3:
                    return items
                name = node.get("name", "") or node.get("text", "") or ""
                role = node.get("control_type", node.get("role", ""))
                rect = node.get("bounding_rectangle", node.get("rect", {}))
                if isinstance(rect, dict):
                    x, y, w, h = rect.get("x", 0), rect.get("y", 0), rect.get("w", 0), rect.get("h", 0)
                else:
                    x = y = w = h = 0
                if name and role and w > 0 and h > 0 and w < 1500 and h < 500:
                    items.append(f"  {role} \"{name}\" ({x},{y} {w}x{h})")
                for child in (node.get("children", node.get("items", [])) or []):
                    if isinstance(child, dict):
                        items.extend(_walk(child, depth + 1))
                return items

            visible = _walk(tree)[:max_items]
            if not visible:
                return ""
            return "[UI Controls]\n" + "\n".join(visible)
        except Exception:
            return ""

    def _get_screen_context(self) -> str:
        """获取当前屏幕上下文：窗口信息、分辨率、鼠标位置、UIA 控件摘要。"""
        parts = []
        try:
            from features.registry import registry
            # 活动窗口
            aw = registry.execute("get_active_window")
            if isinstance(aw, dict):
                aw_data = aw.get("result", aw)
                if isinstance(aw_data, dict):
                    title = aw_data.get("title", "") or aw_data.get("name", "")
                    proc = aw_data.get("process", aw_data.get("class", ""))
                    if title:
                        parts.append(f"窗口: 「{title}」")
                        if proc:
                            parts[-1] += f" ({proc})"
        except Exception:
            pass
        try:
            import pyautogui
            w, h = pyautogui.size()
            x, y = pyautogui.position()
            parts.append(f"屏幕: {w}×{h}  鼠标: ({x},{y})")
        except Exception:
            pass
        # UIA 控件摘要
        a11y = self._capture_a11y_summary()
        if a11y:
            parts.append("")
            parts.append(a11y)
        return "\n".join(parts)

    def _resolve_via_a11y(self, target_text: str) -> dict | None:
        """用 UIA 无障碍树解析语义目标→屏幕坐标。

        对 Windows 标准控件（按钮/输入框/菜单等）可实现像素级精准定位，
        不需要 OCR 或视觉模型。返回 {x, y, w, h, name, role} 或 None。
        """
        if not target_text or not isinstance(target_text, str):
            return None
        target_text = target_text.strip().strip("\"'")
        if not target_text:
            return None

        try:
            from features.registry import registry
            result = registry.execute("a11y_get_tree", depth=4)
            if not result.get("success"):
                return None
            tree = result.get("result", result)
            if isinstance(tree, dict):
                tree = tree.get("result", tree)

            best: dict | None = None
            best_name_len = 9999

            def _search(node, depth=0):
                nonlocal best, best_name_len
                if depth > 6:
                    return
                name = node.get("name", "") or node.get("text", "") or ""
                role = node.get("control_type", node.get("role", ""))
                rect = node.get("bounding_rectangle", node.get("rect", {}))
                if isinstance(rect, dict):
                    x, y, w_, h_ = rect.get("x", 0), rect.get("y", 0), rect.get("w", 0), rect.get("h", 0)
                else:
                    x = y = w_ = h_ = 0

                if name and role and w_ > 0 and h_ > 0 and w_ < 2000 and h_ < 1000:
                    nl = name.lower()
                    tl = target_text.lower()
                    # 精确匹配优先，包含匹配兜底
                    if nl == tl or (tl in nl and len(name) < best_name_len):
                        best = {
                            "x": int(x + w_ // 2),
                            "y": int(y + h_ // 2),
                            "w": int(w_),
                            "h": int(h_),
                            "name": name,
                            "role": role,
                        }
                        best_name_len = len(name)
                for child in (node.get("children", node.get("items", [])) or []):
                    if isinstance(child, dict):
                        _search(child, depth + 1)

            _search(tree)
            return best
        except Exception:
            return None

    def _apply_som(self, b64_img: str) -> dict:
        """Set-of-Mark 标号：检测屏幕元素（UIA+OCR），画框编号。

        MIMO 看到标号截图后直接用 #N 定位，无需输出文字描述。
        返回 {image_b64 (标号图), label_map ({N: {text, x, y, w, h}}), count}。
        """
        # ── OmniParser 优先（YOLOv8 图标检测 + Florence-2 描述 + OCR）──
        if self._omniparser is not None or self._som_enabled:
            try:
                if self._omniparser is None:
                    from agent.omniparser_client import OmniParserClient
                    self._omniparser = OmniParserClient()
                op_result = self._omniparser.parse(b64_img)
                if op_result and op_result["count"] > 0:
                    self._emit("log", {
                        "level": "info",
                        "message": f"OmniParser: {op_result['count']} 个元素 (延迟 {op_result.get('latency',0):.2f}s)",
                    })
                    return op_result
            except Exception as exc:
                logger.debug("OmniParser unavailable, falling back to UIA+OCR: %s", exc)

        import base64
        import cv2
        import numpy as np

        # 解码截图
        nparr = np.frombuffer(base64.b64decode(b64_img), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        h_img, w_img = img.shape[:2]

        # ── 收集元素（UIA + OCR 兜底）──
        elements: list[dict] = []
        seen_positions: set = set()

        # 1. UIA 控件树
        try:
            from features.registry import registry
            result = registry.execute("a11y_get_tree", depth=3)
            tree = result.get("result", result)
            if isinstance(tree, dict):
                tree = tree.get("result", tree)

            def _collect_uia(node, depth=0):
                if depth > 4:
                    return
                name = (node.get("name", "") or node.get("text", "") or "").strip()
                role = node.get("control_type", node.get("role", ""))
                rect = node.get("bounding_rectangle", node.get("rect", {}))
                x = int(rect.get("x", 0)) if isinstance(rect, dict) else 0
                y = int(rect.get("y", 0)) if isinstance(rect, dict) else 0
                w = int(rect.get("w", 0)) if isinstance(rect, dict) else 0
                h = int(rect.get("h", 0)) if isinstance(rect, dict) else 0
                if name and role and 10 < w < 1500 and 8 < h < 500:
                    key = (x // 10, y // 10)
                    if key not in seen_positions:
                        seen_positions.add(key)
                        elements.append({"text": f"[{role}] {name}", "x": x, "y": y, "w": w, "h": h})
                for child in (node.get("children", node.get("items", [])) or []):
                    if isinstance(child, dict):
                        _collect_uia(child, depth + 1)

            _collect_uia(tree)
        except Exception:
            pass

        # 2. OCR 文字区域（补充 UIA 未覆盖的控件）
        try:
            from features.registry import registry
            ocr_r = registry.execute("ocr_extract_all")
            raw = ocr_r.get("result", ocr_r)
            if isinstance(raw, dict):
                raw = raw.get("result", raw.get("items", raw.get("data", [])))
            items = raw if isinstance(raw, list) else []
            for item in items:
                text = (item.get("text", "") or "").strip()[:40]
                if not text:
                    continue
                box = item.get("box", item.get("bbox", item.get("bounding_box", item)))
                if isinstance(box, (list, tuple)):
                    if len(box) == 4:
                        x, y, w, h = map(int, box[:4])
                    elif len(box) == 8:
                        xs = [box[i] for i in range(0, 8, 2)]
                        ys = [box[i] for i in range(1, 8, 2)]
                        x, y, w, h = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
                    else:
                        continue
                elif isinstance(box, dict):
                    x = int(box.get("x", box.get("left", 0)))
                    y = int(box.get("y", box.get("top", 0)))
                    w = int(box.get("w", box.get("width", box.get("right", 0))) - x if "right" in box else box.get("w", 0))
                    h = int(box.get("h", box.get("height", box.get("bottom", 0))) - y if "bottom" in box else box.get("h", 0))
                else:
                    continue
                if w < 15 or h < 8 or w > 1000 or h > 300:
                    continue
                key = (x // 15, y // 15)
                if key not in seen_positions:
                    seen_positions.add(key)
                    elements.append({"text": text, "x": x, "y": y, "w": w, "h": h})
        except Exception:
            pass

        if not elements:
            return {"image_b64": b64_img, "label_map": {}, "count": 0}

        # 排序：从上到下，从左到右
        elements.sort(key=lambda e: (e["y"] // 25, e["x"]))

        # ── 画框 + 编号 ──
        label_map: dict = {}
        overlaid = img.copy()
        for i, elem in enumerate(elements[:40]):  # 最多 40 个
            idx = i + 1
            x, y, w, h = elem["x"], elem["y"], elem["w"], elem["h"]
            cx = min(w_img - 1, max(0, x + w // 2))
            cy = min(h_img - 1, max(0, y + h // 2))
            ow = min(w_img - x, w)
            oh = min(h_img - y, h)

            label_map[idx] = {"text": elem["text"], "x": cx, "y": cy, "w": ow, "h": oh}

            # 蓝色半透明框（增强可见性而不遮盖文字）
            cv2.rectangle(overlaid, (x, y), (x + ow, y + oh), (66, 133, 244), 2)

            # 编号标签（白色背景 + 蓝色文字，放在框左上角）
            lbl = str(idx)
            (lbl_w, lbl_h), _ = cv2.getTextSize(lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
            bg_x1, bg_y1 = max(0, x - 1), max(0, y - lbl_h - 8)
            bg_x2, bg_y2 = min(w_img, x + lbl_w + 5), max(0, y - 1)
            cv2.rectangle(overlaid, (bg_x1, bg_y1), (bg_x2, bg_y2), (66, 133, 244), -1)
            cv2.putText(overlaid, lbl, (x, max(lbl_h, y - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

        _, buf = cv2.imencode(".jpg", overlaid, [cv2.IMWRITE_JPEG_QUALITY, 85])
        result_b64 = base64.b64encode(buf).decode()

        return {"image_b64": result_b64, "label_map": label_map, "count": len(label_map), "elements": elements}

    def _call_llm(self, messages: list[dict], max_retries: int = 2) -> dict | None:
        """调用 LLM API (兼容文本和视觉消息)。"""
        import requests as _req
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 4096,
        }
        last_err = ""
        for attempt in range(1 + max_retries):
            try:
                resp = _req.post(
                    f"{self.api_base}/chat/completions",
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                    timeout=120,
                )
                if resp.status_code == 429:
                    self._emit("log", {"level": "warn", "message": f"API rate limited, retry {attempt+1}/{max_retries}"})
                    time.sleep(3 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                msg = data["choices"][0]["message"]
                content = msg.get("content", "") or msg.get("reasoning_content", "") or ""
                if not content:
                    self._emit("log", {"level": "warn", "message": "LLM returned empty content, retry"})
                    continue
                return data
            except _req.exceptions.Timeout:
                last_err = "API timeout (120s)"
                self._emit("log", {"level": "warn", "message": f"API timeout, retry {attempt+1}/{max_retries}"})
            except _req.exceptions.RequestException as e:
                last_err = str(e)
                self._emit("log", {"level": "warn", "message": f"API error: {str(e)[:100]}, retry {attempt+1}/{max_retries}"})
                time.sleep(2 * (attempt + 1))
        self._emit("log", {"level": "error", "message": f"LLM API failed after {max_retries} retries: {last_err}"})
        return None

    @staticmethod
    def _parse_response(content: str) -> tuple:
        """解析文本协议回复，返回 (action, name, args)。

        action: 'tool' | 'done' | 'error'
        """
        if not content:
            return "error", "", {}

        if "总结:" in content:
            return "done", "", {"summary": content}

        tool_match = re.search(r"工具:\s*(\S+)", content)
        if not tool_match:
            return "error", "", {"raw": content[:200]}

        tool_name = tool_match.group(1).strip()
        args = {}
        param_section = re.search(r"参数:\s*(.*?)(?:\n\s*\n|$)", content, re.DOTALL)
        if param_section:
            for line in param_section.group(1).strip().split("\n"):
                line = line.strip()
                m = re.match(r"(\w+)\s*[:：]\s*(.+)", line)
                if m:
                    args[m.group(1)] = m.group(2).strip()

        return "tool", tool_name, args

    def run_text_protocol(self, goal: str) -> dict:
        """文本协议模式：循环 调用 LLM → 解析 → 监督执行 → 截图。

        适用于 MIMO V2 Omni 等不支持 function calling 的纯文本模型。
        """
        self._ensure_registry()
        self._emit("agent_start", {"goal": goal, "engine": "text_protocol", "model": self.model})

        start = time.time()
        steps = []

        messages = [{"role": "system", "content": self._system_prompt}]
        raw_b64 = self._capture_b64()
        screen_ctx = self._get_screen_context()

        # SoM 标号处理
        som_label = ""
        b64 = raw_b64
        if self._som_enabled:
            som_result = self._apply_som(raw_b64)
            self._last_som_map = som_result["label_map"]
            if som_result["count"] > 0:
                b64 = som_result["image_b64"]
                lines = "\n".join(f"  #{i}: 「{e['text']}」" for i, e in som_result["label_map"].items())
                som_label = (
                    f"\n屏幕元素编号（元素上有蓝色框和编号）:\n{lines}\n"
                    f"\n使用 #N 指定目标，如: target: #3  或  target: #5"
                )

        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": f"任务目标: {goal}\n\n当前状态:\n{screen_ctx}{som_label}\n\n分析屏幕截图，执行第一步。"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        })

        consecutive_fails = 0
        last_tool = ""
        repeat_count = 0

        for loop in range(1, MAX_STEPS + 1):
            if self._stop_event and self._stop_event.is_set():
                self._emit("log", {"level": "info", "message": "停止请求，终止循环"})
                break

            raw = self._call_llm(messages)
            if raw is None:
                consecutive_fails += 1
                if consecutive_fails >= 2:
                    self._emit("log", {"level": "error", "message": "LLM 连续失败，终止"})
                    break
                continue

            consecutive_fails = 0
            msg = raw["choices"][0]["message"]
            content = msg.get("content", "") or msg.get("reasoning_content", "") or ""
            reasoning = msg.get("reasoning_content", "") or ""

            if reasoning:
                self._emit("log", {"level": "info", "message": f"思考: {reasoning[:300]}"})

            action, name, args = self._parse_response(content)

            if action == "done":
                summary = args.get("summary", content)[:500]
                self._emit("log", {"level": "info", "message": f"完成: {summary}"})
                ok_count = sum(1 for s in steps if s.get("status") == "completed")
                return {
                    "success": ok_count > 0, "steps": steps,
                    "summary": summary, "total_steps": len(steps),
                    "elapsed_ms": round((time.time() - start) * 1000),
                }

            if action == "error":
                self._emit("log", {"level": "warn", "message": f"无法解析: {args.get('raw', content)[:200]}"})
                messages.append({
                    "role": "user",
                    "content": "格式错误。请用:\n思考: ...\n工具: 工具名\n参数:\n  参数1: 值1\n或任务完成时用: 总结: ..."
                })
                continue

            # ── 执行工具 (带监督层) ──
            sn = len(steps) + 1
            self._emit("log", {"level": "info", "message": f"调用: {name}({json.dumps(args, ensure_ascii=False)[:200]})"})

            step = {"step_num": sn, "tool": name, "args": args,
                    "thought": reasoning[:300] if reasoning else "", "status": "running"}
            self._emit("step_complete", dict(step))

            step_start = time.time()
            try:
                result = self._supervised_execute(name, args)
                success = result.get("success", False)
            except Exception as e:
                result = {"success": False, "error": str(e)}
                success = False
            step_elapsed = round((time.time() - step_start) * 1000)

            step["status"] = "completed" if success else "failed"
            step["result"] = {
                "success": result.get("success", False),
                "error": result.get("error", ""),
                "step_ms": step_elapsed,
            }
            if args:
                step["args_resolved"] = {k: str(v)[:80] for k, v in args.items()}
            steps.append(step)
            self._emit("step_complete", dict(step))

            # ── 更新消息历史 ──
            messages.append({"role": "assistant", "content": content})
            messages.append({
                "role": "user",
                "content": json.dumps(result, ensure_ascii=False)[:1500],
            })

            status_txt = "成功" if success else f"失败: {result.get('error', 'unknown')}"
            if name in self.NO_SCREENSHOT_TOOLS:
                messages.append({
                    "role": "user",
                    "content": f"执行{status_txt}。",
                })
            else:
                raw_b64 = self._capture_b64()
                screen_ctx = self._get_screen_context()

                # SoM 标号
                som_label = ""
                b64 = raw_b64
                if self._som_enabled:
                    som_result = self._apply_som(raw_b64)
                    self._last_som_map = som_result["label_map"]
                    if som_result["count"] > 0:
                        b64 = som_result["image_b64"]
                        lines = "\n".join(f"  #{i}: 「{e['text']}」" for i, e in som_result["label_map"].items())
                        som_label = f"\n屏幕元素编号:\n{lines}"

                parts = [
                    {"type": "text", "text": f"执行{status_txt}。\n\n当前状态:\n{screen_ctx}{som_label}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]
                messages.append({"role": "user", "content": parts})

            if not success:
                time.sleep(1.5)

            # ── 重复工具检测 ──
            if name == last_tool:
                repeat_count += 1
            else:
                repeat_count = 0
                last_tool = name
            if repeat_count >= 3:
                total_repeats = repeat_count + 1
                self._emit("log", {"level": "warn", "message": f"工具 '{name}' 重复 {total_repeats} 次，强制换策略"})
                repeat_count = 0
                messages.append({
                    "role": "user",
                    "content": f"你已连续 {total_repeats} 次使用 {name}，未取得进展。请换完全不同的策略，不要重复同一工具。"
                })

        elapsed = time.time() - start
        ok_count = sum(1 for s in steps if s.get("status") == "completed")
        return {
            "success": ok_count > 0, "steps": steps,
            "summary": f"执行 {len(steps)} 步 ({ok_count} 成功)",
            "total_steps": len(steps), "elapsed_ms": round(elapsed * 1000),
        }

    # ══════════════════════════════════════════════════════════
    #  LangGraph 模式 (函数调用模型)
    # ══════════════════════════════════════════════════════════

    _LANGGRAPH_AVAILABLE = None

    @classmethod
    def _check_langgraph(cls) -> bool:
        if cls._LANGGRAPH_AVAILABLE is None:
            try:
                import langgraph  # noqa
                import langchain_openai  # noqa
                cls._LANGGRAPH_AVAILABLE = True
            except ImportError:
                cls._LANGGRAPH_AVAILABLE = False
        return cls._LANGGRAPH_AVAILABLE

    def _create_llm(self):
        """创建 LangChain LLM 实例。"""
        api_base = (self.api_base or "https://api.deepseek.com/v1").rstrip("/")
        try:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=self.model or "deepseek-chat",
                api_key=self.api_key or "",
                base_url=f"{api_base}",
                temperature=0.1,
                max_tokens=4096,
                timeout=30,
                max_retries=2,
            )
        except ImportError:
            pass

        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.outputs import ChatResult, ChatGeneration
        from langchain_core.messages import AIMessage

        class _MockLLM(BaseChatModel):
            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="I will use a tool."))])
            @property
            def _llm_type(self) -> str:
                return "mock"

        logger.warning("langchain-openai not installed, using mock LLM")
        return _MockLLM()

    AGENT_TOOLS = frozenset({
        "click_target", "right_click", "hover", "mouse_move",
        "type_text", "type_clipboard",
        "hotkey", "scroll", "drag", "swipe",
        "triple_click", "middle_click", "select_text",
        "browser_connect", "browser_navigate", "browser_click", "browser_type",
        "browser_list_tabs", "browser_close_tab", "browser_screenshot",
        "browser_get_url", "browser_get_text", "browser_find_elements",
        "browser_wait_element", "browser_wait_page_load", "browser_execute_js",
        "browser_refresh", "browser_go_back", "browser_new_tab", "browser_switch_tab",
        "screenshot", "find_image", "ocr_find_text", "ocr_extract_all",
        "region_select", "color_check", "popup_close",
        "a11y_get_tree", "find_element", "wait_element",
        "launch_program", "run_command", "program_resolve", "program_list",
        "focus_window", "get_active_window", "list_windows",
        "window_minimize", "window_maximize", "window_close",
        "window_resize", "window_restore", "show_desktop",
        "detect_process", "find_processes", "kill_process",
        "volume_set", "volume_mute", "display_brightness",
        "battery_status", "lock_workstation",
        "wifi_list", "wifi_connect", "wifi_disconnect",
        "wait_action", "wait_screen_stable", "wait_screen_change",
        "clipboard_set_text", "clipboard_get_text",
        "clipboard_clear", "clipboard_get_files", "clipboard_get_image",
        "handle_dialog", "fill_save_dialog",
        "set_variable", "get_variable",
        "run_dsl", "run_dsl_sync",
        "a11y_find_element", "a11y_click_element",
        "network_status",
        "scheduler_create", "scheduler_delete", "scheduler_list",
        "scheduler_update", "scheduler_get", "quick_schedule",
        "generate_flow_script",
        "file_read", "file_write", "file_list",
        "file_exists", "file_edit", "file_append", "file_copy", "file_move", "file_delete",
        "screen_record_start", "screen_record_stop",
        "menu_click",
    })

    def _load_langgraph_tools(self) -> list:
        """从 @feature 注册表加载 LangGraph 工具集。"""
        from features.registry import registry
        import features._all  # noqa

        tools = []
        for spec in registry.all():
            if spec.deprecated or spec.hidden:
                continue
            if spec.name not in self.AGENT_TOOLS:
                continue
            tools.append(self._make_tool_func(spec))
        logger.info("Loaded %d LangGraph tools from registry", len(tools))
        return tools

    def _make_tool_func(self, spec):
        """包装 @feature 为 LangGraph 可调用函数。"""
        import inspect
        param_names = [p.name for p in spec.params]
        _rename = {"args": "v_args", "kwargs": "v_kwargs"}
        schema_names = [_rename.get(n, n) for n in param_names]

        def tool_fn(**kwargs) -> str:
            try:
                from features.registry import registry as r
                rev = {"v_args": "args", "v_kwargs": "kwargs"}
                mapped = {rev.get(k, k): v for k, v in kwargs.items()}
                cleaned = {k: v for k, v in mapped.items() if v is not None}
                result = r.execute(spec.name, **cleaned)
                if isinstance(result, dict):
                    return json.dumps(result, ensure_ascii=False, default=str)
                return str(result)
            except Exception as e:
                return json.dumps({"success": False, "error": str(e)})

        sig = inspect.Signature(
            [inspect.Parameter(sn, inspect.Parameter.KEYWORD_ONLY, default=None)
             for sn in schema_names],
            return_annotation=str,
        )
        tool_fn.__signature__ = sig
        return tool_fn

    def _make_supervision_wrapper(self):
        """创建 LangGraph ToolNode 监督包装器。

        与 _supervised_execute 共享监督逻辑，但适配 LangGraph 的
        wrap_tool_call 接口 (call_req, execute) → result_str。
        """
        def _extract_content(result) -> Optional[str]:
            if result is None:
                return None
            if isinstance(result, str):
                return result
            if hasattr(result, "content"):
                c = result.content
                return str(c) if c is not None else None
            return str(result)

        def wrapper(call_req, execute):
            tool_call = call_req.tool_call
            name = tool_call.get("name", "")
            args = tool_call.get("args", {})
            is_gui = name in self.GUI_ACTIONS

            # ── Pre: 视觉定位 ──
            if is_gui and (self.auto_learn or self.multimodal):
                try:
                    args = self._apply_visual_locator(name, args)
                except Exception as e:
                    logger.warning("Visual locator error: %s", e)

            # ── Pre: 窗口焦点 ──
            if is_gui:
                self._ensure_safe_window(name, args)

            # ── 执行 + 重试 ──
            last_result = None
            max_retries = 2 if is_gui else 1
            for attempt in range(max_retries + 1):
                if attempt > 0 and self._stop_event and self._stop_event.is_set():
                    self._emit("log", {"level": "info", "message": f"停止请求，跳过 {name} 重试"})
                    break
                if attempt > 0:
                    delay = 2 ** (attempt - 1)
                    self._emit("log", {"level": "info", "message": f"重试 {name} ({attempt}/{max_retries}, {delay}s)"})
                    time.sleep(delay)
                try:
                    last_result = execute(call_req)
                    content = _extract_content(last_result)
                    if content and isinstance(content, str):
                        try:
                            parsed = json.loads(content)
                            if isinstance(parsed, dict) and not parsed.get("success", True):
                                if attempt < max_retries:
                                    continue
                        except (json.JSONDecodeError, TypeError):
                            pass
                    break
                except Exception as e:
                    if attempt >= max_retries:
                        raise
                    last_result = None

            # ── Post: 对话框 ──
            if is_gui or name in ("screenshot", "hotkey", "launch_program"):
                self._auto_handle_dialogs()

            # ── Post: 屏幕稳定 ──
            if is_gui:
                self._wait_screen_stable()

            # ── 跟踪启动程序 ──
            if name == "launch_program":
                target = args.get("target") or args.get("name") or ""
                if target:
                    self._last_launched = target

            return last_result

        return wrapper

    def run_langgraph(self, goal: str, config: Optional[dict] = None) -> dict:
        """LangGraph 模式：create_react_agent 驱动执行。

        适用于 DeepSeek/GPT/Claude 等支持函数调用的 LLM。
        """
        from langgraph.prebuilt import create_react_agent, ToolNode
        from langchain_core.messages import HumanMessage, ToolMessage, AIMessage

        self._ensure_registry()
        tools = self._load_langgraph_tools()

        llm = self._create_llm()

        try:
            from agent.dialog_handler import start_dialog_monitor
            desktop = os.path.expanduser("~/Desktop")
            start_dialog_monitor(save_dir=desktop)
        except Exception:
            pass

        tool_node = ToolNode(tools, wrap_tool_call=self._make_supervision_wrapper())

        system_prompt = """你是一个桌面自动化助手。可用工具通过 @feature 注册表提供。

执行规则：
1. 对于简单操作（点击、输入、截图等），直接调用对应工具
2. 对于多步骤工作流，先用 generate_flow_script 生成流程，然后按步骤执行
3. 遇到失败时尝试 recovery: 重试→替代方案→跳过→失败
4. 工具调用完成后无需额外标记，直接结束即可
5. 使用 wait_action 代替 time.sleep
6. 所有坐标默认为屏幕绝对坐标
7. 浏览器通过 CDP 自动连接，首次调用 browser_navigate 等工具时会自动启动 Chrome
8. 登录流程：点击登录按钮 → 等待表单出现 → 填写 → 提交 → 等待跳转"""

        graph = create_react_agent(model=llm, tools=tool_node, prompt=system_prompt)

        self._emit("agent_start", {"goal": goal, "engine": "langgraph", "model": self.model})

        thread_config = {"recursion_limit": MAX_STEPS}
        if config:
            thread_config.update(config)

        start = time.time()
        steps = []
        pending_by_id: dict = {}

        try:
            events = graph.stream(
                {"messages": [HumanMessage(content=goal)]},
                thread_config,
                stream_mode="values",
            )

            step_num = 0
            seen_ids: set = set()
            for event in events:
                messages = event.get("messages", [])
                if not messages:
                    continue

                last = messages[-1]

                if isinstance(last, AIMessage) and hasattr(last, "tool_calls") and last.tool_calls:
                    for tc in last.tool_calls:
                        tid = tc.get("id", "")
                        if tid in seen_ids:
                            continue
                        seen_ids.add(tid)
                        step_num += 1
                        step_data = {
                            "step_num": step_num,
                            "tool": tc.get("name", "unknown"),
                            "args": tc.get("args", {}),
                            "status": "running",
                        }
                        if tid:
                            pending_by_id[tid] = step_data
                        steps.append(step_data)
                        self._emit("step_complete", dict(step_data))
                        logger.info("Step %d: %s(%s)", step_num,
                                    tc["name"], json.dumps(tc.get("args", {}), ensure_ascii=False)[:120])

                elif isinstance(last, ToolMessage):
                    tid = last.tool_call_id
                    pd = pending_by_id.pop(tid, None)
                    if pd is not None:
                        try:
                            result_data = json.loads(last.content)
                        except Exception:
                            result_data = {"output": str(last.content)[:500]}
                        pd["status"] = "completed" if result_data.get("success") else "failed"
                        pd["result"] = result_data
                        self._emit("step_complete", dict(pd))

            for pd in pending_by_id.values():
                pd["status"] = "error"
                pd["result"] = {"success": False, "error": "execution timeout"}

            elapsed = time.time() - start
            success_count = sum(1 for s in steps if s.get("status") == "completed")
            result = {
                "success": success_count > 0,
                "steps": steps,
                "summary": f"Executed {len(steps)} steps ({success_count} ok) in {elapsed:.1f}s",
                "total_steps": len(steps),
                "elapsed_ms": round(elapsed * 1000, 1),
            }
            self._emit("agent_done", {"status": "completed", **result})
            return result

        except Exception as e:
            elapsed = time.time() - start
            error_result = {
                "success": False, "steps": steps, "error": str(e),
                "total_steps": len(steps), "elapsed_ms": round(elapsed * 1000, 1),
            }
            self._emit("agent_done", {"status": "failed", **error_result})
            logger.exception("LangGraph execution failed")
            return error_result

    # ══════════════════════════════════════════════════════════
    #  统一入口
    # ══════════════════════════════════════════════════════════

    def run(self, goal: str, mode: str = "auto") -> dict:
        """执行目标。

        参数:
            goal: 自然语言目标描述
            mode: 执行模式
                "auto"           — 自动选择 (mimo-model → text_protocol, 否则 → langgraph)
                "text_protocol"  — MIMO 风格文本协议循环
                "langgraph"      — LangGraph create_react_agent
                "direct"         — 仅生成流程图 (generate_flow_script)

        返回:
            dict{success, steps, summary, total_steps, elapsed_ms}
        """
        if self._stop_event and self._stop_event.is_set():
            return {"success": False, "steps": [], "total_steps": 0,
                    "error": "stopped before start", "elapsed_ms": 0}

        # 自动检测模式
        if mode == "auto":
            model_lower = self.model.strip().lower()
            if "mimo" in model_lower:
                mode = "text_protocol"
            elif self._check_langgraph():
                mode = "langgraph"
            else:
                # 无 langgraph → 回退到文本协议 (即使不是 MIMO)
                self._emit("log", {"level": "info", "message": "LangGraph not available, falling back to text_protocol"})
                mode = "text_protocol"

        if mode == "text_protocol":
            return self.run_text_protocol(goal)
        elif mode == "langgraph":
            return self.run_langgraph(goal)
        elif mode == "direct":
            return self._run_direct(goal)
        else:
            raise ValueError(f"Unknown mode: {mode}")

    def _run_direct(self, goal: str) -> dict:
        """直接模式：仅调用 generate_flow_script 生成流程图。"""
        self._ensure_registry()
        self._emit("agent_start", {"goal": goal, "engine": "direct"})

        try:
            from features.flow_features import generate_flow_script
            result = generate_flow_script(goal)
            success = result.get("success", False)
            self._emit("step_complete", {
                "step_num": 1, "tool": "generate_flow_script",
                "args": {"goal": goal},
                "status": "completed" if success else "failed",
                "result": {"success": success, "data": result.get("result", result)},
            })
            self._emit("agent_done", {
                "status": "completed" if success else "failed",
                "total_steps": 1, "summary": "Flow script generated" if success else "Generation failed",
            })
            return {"success": success, "steps": [], "total_steps": 1,
                    "summary": "Flow script generated" if success else "Generation failed"}
        except Exception as exc:
            self._emit("error", {"message": f"generate_flow_script failed: {exc}"})
            self._emit("agent_done", {"status": "failed", "total_steps": 0, "fail_reason": str(exc)})
            return {"success": False, "steps": [], "total_steps": 0, "error": str(exc)}
