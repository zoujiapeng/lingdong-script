"""
LangGraph 驱动的 AI Agent — 替代自研 ReAct 循环。

工具集通过 @feature 注册表动态生成，LangGraph 负责状态管理/循环控制。
包含完整的监督执行层：窗口焦点安全、指数退避重试、屏幕稳定等待、
OCR 验证、失败恢复策略。

IPC 兼容: 保持与 __main__.py 相同的 JSON stdout 事件协议。
"""

import json
import logging
import os
import re
import threading
import time
from queue import Queue
from typing import Any, Callable, Dict, List, Optional, Set
from datetime import datetime, timezone

from langgraph.prebuilt import create_react_agent, ToolNode

logger = logging.getLogger("agent.langgraph_agent")

# GUI actions that benefit from window focus + screen-stability wait
_GUI_ACTIONS: Set[str] = {
    "click_target", "right_click", "hover", "mouse_move",
    "type_text", "type_clipboard",
    "hotkey", "scroll", "drag", "swipe",
}

# Step types in controller.py that trigger focus + settle
_STEP_TYPES_GUI = {"click", "text_click", "type", "hotkey", "image_click",
                   "scroll", "drag", "launch", "focus"}

# System prompt to guide LLM behavior (reduces unnecessary reasoning loops)
_SYSTEM_PROMPT = """你是一个桌面自动化助手。可用工具通过 @feature 注册表提供。

执行规则：
1. 对于简单操作（点击、输入、截图等），直接调用对应工具
2. 对于多步骤工作流，先用 generate_flow_script 生成流程，然后按步骤执行
3. 对于定时/重复任务，使用 scheduler_create 创建调度（需要 template_id 参数）
4. 遇到失败时尝试 recovery: 重试→替代方案→跳过→失败
5. 工具调用完成后无需额外标记，直接结束即可
6. 使用 wait_action 代替 time.sleep
7. 所有坐标默认为屏幕绝对坐标
8. 浏览器操作规则（重要）：
   a. 浏览器通过 CDP (Chrome DevTools Protocol) 自动连接 — 首次调用 browser_navigate 等工具时会自动启动 Chrome 调试实例并连接，无需手动调用 browser_connect 或 launch_program
   b. 如果需要显式连接已有 Chrome（已开启 --remote-debugging-port=9222），可以调用 browser_connect(port=9222)
   c. 导航到 URL 使用 browser_navigate(url="https://example.com") — 不要用键盘在地址栏输入
   d. 点击页面元素使用 browser_click(selector="CSS选择器") — selector 从页面上下文推断（id, class, role, 文本等）
   e. 输入文本到页面元素使用 browser_type(selector="CSS选择器", text="内容")
   f. 等待页面元素出现使用 browser_wait_element(selector="CSS选择器", timeout=10)
   g. 获取页面文本使用 browser_get_text(selector="CSS选择器")
   h. 浏览器截图使用 browser_screenshot()
   i. 在新标签页中打开使用 browser_new_tab(url="URL")
   j. 切换标签页使用 browser_switch_tab(index=N)
9. 绝对不要编造用户名、密码或凭据。如果用户目标提到"登录"但没有提供凭据，请暂停并输出一条消息要求用户提供，而不是使用占位符值。
10. 对于所有需要浏览网页的任务（搜索、登录、填写表单、查看网页等），必须使用 browser_* 系列工具（browser_navigate, browser_click, browser_type 等），这些工具通过 Playwright CDP 协议直接控制浏览器，比 OCR/鼠标模拟更精确可靠。不要在浏览器上使用 click_target/type_text 等通用桌面自动化工具。
11. 登录流程规则：当用户让"登录"某个网站时，不能只点击一下登录按钮就认为完成。正确的流程：
    a. 点击登录按钮 → browser_wait_element 等待登录表单出现
    b. 如果用户没有提供凭据，暂停并输出消息要求提供，不要编造
    c. 获取凭据后使用 browser_type 填写表单
    d. browser_click 点击提交按钮
    e. browser_wait_element 等待登录成功后页面跳转
12. 流程完整性：每个操作之间必须有适当等待。导航后等加载，点击后等新内容出现。"""


class LangGraphAgent:
    """LangGraph 驱动的 Agent，含完整监督执行层。

    - 工具自动从 @feature 注册表加载
    - 状态由 LangGraph 管理 (断点续跑/回滚)
    - ToolNode 内嵌监督层 (窗口焦点/重试/对话框自动处理/屏幕稳定)
    - 生成流程图脚本时可前端可视化展示
    """

    MAX_STEPS = 50

    def __init__(self, model: str = "deepseek-chat", api_key: str = "",
                 api_base: str = "", event_callback: Optional[Callable] = None,
                 stop_event: Optional[threading.Event] = None,
                 guidance_queue: Optional[Queue] = None,
                 multimodal: bool = False,
                 auto_learn: bool = False,
                 verify_enabled: bool = True):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.multimodal = multimodal
        self.auto_learn = auto_learn
        self.verify_enabled = verify_enabled
        self._event_cb = event_callback
        self._graph = None
        self._tools: list = []
        self._last_launched: str = ""
        self._stop_event = stop_event
        self._guidance_queue = guidance_queue
        # Export to env so downstream features (e.g. generate_flow_script) can read them
        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key
        if api_base:
            os.environ["LLM_API_BASE"] = api_base
        if model:
            os.environ["LLM_MODEL"] = model

    def set_stop_event(self, stop_event: threading.Event):
        self._stop_event = stop_event

    AGENT_TOOLS: Set[str] = {
        # ---- 交互动作 ----
        "click_target", "right_click", "hover", "mouse_move",
        "type_text", "type_clipboard",
        "hotkey", "scroll", "drag", "swipe",
        "triple_click", "middle_click", "select_text",
        # ---- 浏览器 ----
        "browser_connect", "browser_navigate", "browser_click", "browser_type",
        "browser_list_tabs", "browser_close_tab", "browser_screenshot",
        "browser_get_url", "browser_get_text", "browser_find_elements",
        "browser_wait_element", "browser_wait_page_load", "browser_execute_js",
        "browser_refresh", "browser_go_back", "browser_new_tab", "browser_switch_tab",
        # ---- 感知识别 ----
        "screenshot", "find_image", "ocr_find_text", "ocr_extract_all",
        "region_select", "color_check", "popup_close",
        "a11y_get_tree", "find_element", "wait_element",
        # ---- 系统控制 ----
        "launch_program", "run_command", "program_resolve", "program_list",
        "focus_window", "get_active_window", "list_windows",
        # ---- 窗口管理 ----
        "window_minimize", "window_maximize", "window_close",
        "window_resize", "window_restore", "show_desktop",
        # ---- 进程 ----
        "detect_process", "find_processes", "kill_process",
        # ---- 系统功能 ----
        "volume_set", "volume_mute", "display_brightness",
        "battery_status", "lock_workstation",
        "wifi_list", "wifi_connect", "wifi_disconnect",
        # ---- 等待 ----
        "wait_action", "wait_screen_stable", "wait_screen_change",
        # ---- 剪贴板 ----
        "clipboard_set_text", "clipboard_get_text",
        "clipboard_clear", "clipboard_get_files", "clipboard_get_image",
        # ---- 对话框 ----
        "handle_dialog", "fill_save_dialog",
        # ---- 变量 ----
        "set_variable", "get_variable",
        # ---- DSL ----
        "run_dsl", "run_dsl_sync",
        # ---- 辅助功能 ----
        "a11y_find_element", "a11y_click_element",
        # ---- 网络 ----
        "network_status",
        # ---- 调度器 ----
        "scheduler_create", "scheduler_delete", "scheduler_list",
        "scheduler_update", "scheduler_get",
        "quick_schedule",
        # ---- 流程生成 ----
        "generate_flow_script",
        # ---- 文件 ----
        "file_read", "file_write", "file_list",
        "file_exists", "file_edit",
        "file_append", "file_copy", "file_move", "file_delete",
        # ---- 媒体 ----
        "screen_record_start", "screen_record_stop",
        # ---- 菜单 ----
        "menu_click",
    }

    # ── 工具加载 ──────────────────────────────────────────────

    def load_tools(self) -> list:
        """从 @feature 注册表加载 Agent 可用工具集。"""
        try:
            from features.registry import registry
            import features._all  # noqa

            tools = []
            for spec in registry.all():
                if spec.deprecated or spec.hidden:
                    continue
                if spec.name not in self.AGENT_TOOLS:
                    continue
                fn = self._make_tool_func(spec)
                fn.__name__ = spec.name
                fn.__doc__ = (
                    f"{spec.display_name}: {spec.description}\n\n"
                    f"参数: {[p.name for p in spec.params]}"
                )
                tools.append(fn)
            self._tools = tools
            logger.info("Loaded %d agent tools from registry", len(tools))
            return tools
        except ImportError:
            logger.warning("Feature registry not available, using fallback tools")
            return self._fallback_tools()

    def _make_tool_func(self, spec):
        """Wrap a FeatureSpec into a callable tool function for LangGraph.

        Sets __signature__ so LangChain generates the correct tool schema from
        the @feature's declared params rather than seeing only **kwargs.
        Handles LangChain's v__ prefix for reserved names like args.
        """
        import inspect
        param_names = [p.name for p in spec.params]
        # LangChain prefixes reserved names (args, kwargs) with v__
        _rename = {"args": "v_args", "kwargs": "v_kwargs"}
        schema_names = [_rename.get(n, n) for n in param_names]

        def tool_fn(**kwargs) -> str:
            try:
                from features.registry import registry as r
                # Map v_args/v_kwargs back to original names
                rev = {"v_args": "args", "v_kwargs": "kwargs"}
                mapped = {rev.get(k, k): v for k, v in kwargs.items()}
                # Clean None values (LangGraph sends None for unspecified params)
                cleaned = {k: v for k, v in mapped.items() if v is not None}
                result = r.execute(spec.name, **cleaned)
                if isinstance(result, dict):
                    return json.dumps(result, ensure_ascii=False, default=str)
                return str(result)
            except Exception as e:
                return json.dumps({"success": False, "error": str(e)})

        # Expose the real param names so LangChain builds the correct schema
        sig = inspect.Signature(
            [inspect.Parameter(sn, inspect.Parameter.KEYWORD_ONLY, default=None)
             for sn in schema_names],
            return_annotation=str,
        )
        tool_fn.__signature__ = sig
        return tool_fn

    def _fallback_tools(self) -> list:
        """Fallback tools when registry is not available."""
        import pyautogui

        def click(x: int = 0, y: int = 0, button: str = "left") -> str:
            pyautogui.click(x, y, button=button)
            return json.dumps({"success": True})

        def type_text(text: str = "") -> str:
            pyautogui.typewrite(text)
            return json.dumps({"success": True})

        def wait(duration: float = 1.0) -> str:
            time.sleep(duration)
            return json.dumps({"success": True})

        def screenshot(path: str = "") -> str:
            import tempfile, os as _os
            dest = path or _os.path.join(tempfile.gettempdir(), "lg_screenshot.png")
            pyautogui.screenshot(dest)
            return json.dumps({"success": True, "path": dest})

        for fn in [click, type_text, wait, screenshot]:
            fn.__doc__ = fn.__name__.replace("_", " ")
        return [click, type_text, wait, screenshot]

    # ── 监督层 ────────────────────────────────────────────────

    def _ensure_safe_window(self, tool_name: str, args: dict) -> dict:
        """Window focus safety: verify target window is focused before GUI actions."""
        try:
            # Extract target hint from args
            target_hint = (
                args.get("target_window") or
                args.get("target") or
                args.get("title") or
                self._last_launched or ""
            )
            from features.registry import registry as r
            result = r.execute("get_active_window")
            active_data = result
            if isinstance(result, dict):
                active_data = result.get("result", result)
            active_title = ""
            if isinstance(active_data, dict):
                nested = active_data
                if isinstance(active_data.get("result"), dict):
                    nested = active_data["result"]
                active_title = nested.get("title", "")

            if not target_hint:
                return {"success": True, "focused": True}
            # If target is in active window title, we're good
            if target_hint.lower() in active_title.lower():
                return {"success": True, "focused": True}
            # Try to focus the target window
            r.execute("focus_window", title=target_hint)
            time.sleep(0.3)
            return {"success": True, "focused": True}
        except Exception as e:
            logger.warning("Window focus check failed: %s", e)
            return {"success": False, "focused": False, "error": str(e)}

    def _wait_screen_stable(self, timeout: float = 1.5):
        """Wait for screen to settle after an action."""
        time.sleep(min(timeout, 1.5))

    def _decide_recovery(self, tool_name: str, args: dict,
                         error: str, retry_count: int = 0) -> dict:
        """Decide recovery strategy for a failed step (rules-based, no LLM)."""
        error_lower = error.lower()

        # Window/access errors — can't recover
        if any(kw in error_lower for kw in
               ["access denied", "permission denied", "forbidden"]):
            return {"action": "fail"}

        # Focus errors — retry
        if any(kw in error_lower for kw in
               ["cannot focus", "window not found", "not active", "focus failed"]):
            return {"action": "retry"}

        # Element visibility — wait and retry
        if any(kw in error_lower for kw in
               ["not visible", "not found", "element not", "no such"]):
            return {"action": "retry"}

        # Timeout — retry with backoff
        if "timeout" in error_lower:
            return {"action": "retry"}

        # Connection/network errors — retry
        if any(kw in error_lower for kw in ["connection", "network", "timed out"]):
            return {"action": "retry"}

        # Max retries exceeded
        if retry_count >= 2:
            if tool_name in _GUI_ACTIONS:
                return {"action": "skip"}
            return {"action": "fail"}

        # Default: retry once
        if retry_count < 1:
            return {"action": "retry"}
        return {"action": "skip"}

    def _auto_handle_dialogs(self):
        """Auto-dismiss any visible dialogs after a tool execution.

        Uses dialog_handler (pure win32) to find and dismiss dialogs.
        Modern IFileDialog cannot be saved via automation — only dismissed.

        For file operations, use file_write @feature (Python I/O) instead of
        triggering UI save dialogs.
        """
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

    # (unused SendInput methods removed — dialog_handler handles dialogs)

    # ── 视觉定位 (Visual Locator) ──────────────────────────────

    def _apply_visual_locator(self, name: str, args: dict) -> dict:
        """Use visual locator to find GUI element before click/type.

        Strategy:
        1. Extract target text from args based on tool name
        2. Take a screenshot
        3. Call visual_locator.locate() — tries ExperienceDB first, VLM fallback
        4. If located, update args with center_x / center_y coordinates

        Returns updated args dict (coordinates injected if found).
        """
        # Map tool names → (target_param, step_type)
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

        # Skip if: no target text, already has explicit coords, or target is a raw coordinate
        if not target_text or not isinstance(target_text, str):
            return args
        if re.match(r'^-?\d+$', target_text.strip()):
            return args
        if re.match(r'^-?\d+\s*,\s*-?\d+$', target_text.strip()):
            return args
        x_val = args.get("x", -1)
        y_val = args.get("y", -1)
        if isinstance(x_val, (int, float)) and isinstance(y_val, (int, float)):
            if x_val >= 0 and y_val >= 0:
                return args

        # ── Take screenshot to temp file ──
        import pyautogui as _pg
        import tempfile
        screenshot_path = os.path.join(
            tempfile.gettempdir(),
            f"lg_vl_{int(time.time())}_{hash(target_text) & 0xFFFF}.png",
        )
        try:
            _pg.screenshot(screenshot_path)
        except Exception as exc:
            logger.warning("Visual locator screenshot failed: %s", exc)
            return args

        # ── Run locator ──
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
                screenshot_path=screenshot_path,
                use_vlm=self.multimodal,
                vlm_config=vlm_config,
                window_title="",
                threshold=0.75,
            )

            if result.get("success"):
                coords = result["result"]
                args = dict(args)  # shallow copy — don't mutate caller
                cx = coords.get("center_x", coords.get("x", 0))
                cy = coords.get("center_y", coords.get("y", 0))
                args["x"] = int(cx)
                args["y"] = int(cy)
                # For click_target, update target to coordinates so @feature skips OCR
                if name == "click_target":
                    args["target"] = f"{cx},{cy}"
                self._emit("log", {
                    "level": "info",
                    "message": (
                        f"视觉定位: 「{target_text}」→ ({cx}, {cy}) "
                        f"[{coords.get('method', '?')}]"
                    ),
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
                if os.path.exists(screenshot_path):
                    os.remove(screenshot_path)
            except Exception:
                pass

        return args

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _hotkey(keys: str):
        """Send keyboard shortcut via pyautogui."""
        import pyautogui as _pg
        try:
            if "+" in keys:
                mods = keys.split("+")
                _pg.hotkey(*mods)
            else:
                _pg.press(keys)
        except Exception:
            try:
                from features.registry import registry as r
                r.execute("hotkey", keys=keys)
            except Exception:
                pass

    def _make_supervision_wrapper(self):
        """Create a wrap_tool_call function with full supervision pipeline.

        Layers:
        1. Window focus safety — verify target window is focused
        2. Execute with retry — exponential backoff (1s, 2s, 4s)
        3. Auto-handle dialogs — dismiss any popups using dialog_handler
        4. Screen stability wait — let UI settle
        5. Track launched programs

        Note: Modern IFileDialog save dialogs cannot be interacted with
        via automation (DirectUI rendering). The agent should save files
        programmatically using file_write rather than triggering UI dialogs.
        """
        def wrapper(call_req, execute):
            tool_call = call_req.tool_call
            name = tool_call.get("name", "")
            args = tool_call.get("args", {})

            # ── 0. Visual locator: experience → VLM → coordinate ──
            if name in _GUI_ACTIONS and (self.auto_learn or self.multimodal):
                try:
                    args = self._apply_visual_locator(name, args)
                except Exception as e:
                    logger.warning("Visual locator error (non-fatal): %s", e)

            # ── 1. Window focus safety (GUI actions only) ──
            if name in _GUI_ACTIONS:
                self._ensure_safe_window(name, args)

            # ── 2. Execute with retry ──
            last_result = None
            max_retries = 2 if name in _GUI_ACTIONS else 1
            for attempt in range(max_retries + 1):
                # Check stop between retries
                if attempt > 0 and self._stop_event and self._stop_event.is_set():
                    self._emit("log", {"level": "info",
                               "message": f"停止请求，跳过 {name} 重试"})
                    break
                if attempt > 0:
                    delay = 2 ** (attempt - 1)
                    self._emit("log", {"level": "info",
                               "message": f"重试 {name} ({attempt}/{max_retries}, {delay}s)"})
                    time.sleep(delay)
                try:
                    last_result = execute(call_req)
                    # Check if result indicates failure
                    content = self._extract_content(last_result)
                    if content and isinstance(content, str):
                        try:
                            parsed = json.loads(content)
                            if isinstance(parsed, dict) and not parsed.get("success", True):
                                if attempt < max_retries:
                                    continue  # Retry
                        except (json.JSONDecodeError, TypeError):
                            pass
                    break  # Success
                except Exception as e:
                    if attempt >= max_retries:
                        raise
                    last_result = None

            # ── 3. Auto-handle any dialogs ──
            if name in _GUI_ACTIONS or name in ("screenshot", "hotkey", "file_write",
                                                  "run_dsl", "run_dsl_sync",
                                                  "generate_flow_script", "launch_program"):
                self._auto_handle_dialogs()

            # ── 4. Screen stability wait after GUI actions ──
            if name in _GUI_ACTIONS:
                self._wait_screen_stable()

            # ── 5. Track launched programs ──
            if name == "launch_program":
                target = args.get("target") or args.get("name") or ""
                if target:
                    self._last_launched = target

            return last_result

        return wrapper

    @staticmethod
    def _extract_content(result) -> Optional[str]:
        """Extract string content from various result types."""
        if result is None:
            return None
        if isinstance(result, str):
            return result
        if hasattr(result, "content"):
            c = result.content
            return str(c) if c is not None else None
        return str(result)

    # ── LLM ───────────────────────────────────────────────────

    def _create_llm(self):
        """创建 LLM 实例（OpenAI 兼容 API / DeepSeek）。"""
        api_base = (self.api_base or "https://api.deepseek.com/v1").rstrip("/")
        try:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=self.model or "deepseek-chat",
                api_key=self.api_key or "",
                base_url=f"{api_base}",
                temperature=0.1,
                max_tokens=4096,
                # Longer timeout for DeepSeek's slower responses
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
                return ChatResult(
                    generations=[ChatGeneration(
                        message=AIMessage(content="I will use a tool."))]
                )
            @property
            def _llm_type(self) -> str:
                return "mock"

        logger.warning("langchain-openai not installed, using mock LLM")
        return _MockLLM()

    # ── 图初始化 ──────────────────────────────────────────────

    def init(self):
        """初始化 LangGraph 图（含监督 ToolNode + 系统提示词 + 后台对话框监控）。"""
        if not self._tools:
            self.load_tools()

        # 启动后台对话框监控（自动处理命名错误/保存提示/确认弹窗等）
        try:
            from agent.dialog_handler import start_dialog_monitor
            desktop = os.path.expanduser("~/Desktop")
            start_dialog_monitor(save_dir=desktop)
        except Exception:
            pass

        llm = self._create_llm()

        # Attach system prompt to guide LLM behavior
        # Create ToolNode with supervision wrapper
        tool_node = ToolNode(
            self._tools,
            wrap_tool_call=self._make_supervision_wrapper(),
        )

        self._graph = create_react_agent(
            model=llm,
            tools=tool_node,
            prompt=_SYSTEM_PROMPT,
        )

    # ── 执行入口 ──────────────────────────────────────────────

    def run(self, goal: str, config: Optional[Dict] = None) -> Dict:
        """执行目标。

        Args:
            goal: 自然语言目标描述
            config: LangGraph 配置 (recursion_limit, thread_id 等)

        Returns:
            dict{success, steps, summary, error, total_steps, elapsed_ms}
        """
        if self._graph is None:
            self.init()

        # Check stop before starting
        if self._stop_event and self._stop_event.is_set():
            return {
                "success": False, "steps": [], "total_steps": 0,
                "error": "stopped before start", "elapsed_ms": 0,
            }

        self._emit("agent_start", {"goal": goal, "model": self.model})

        thread_config = {"recursion_limit": self.MAX_STEPS}
        if config:
            thread_config.update(config)

        from langchain_core.messages import HumanMessage, ToolMessage, AIMessage
        start = time.time()
        steps = []
        pending_by_id: dict = {}

        try:
            events = self._graph.stream(
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

                # LLM thinking step → tool call
                if (isinstance(last, AIMessage)
                        and hasattr(last, "tool_calls") and last.tool_calls):
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
                        logger.info(
                            "Step %d: %s(%s)", step_num,
                            tc["name"], json.dumps(tc.get("args", {}), ensure_ascii=False)[:120]
                        )

                # Tool result step
                elif isinstance(last, ToolMessage):
                    tid = last.tool_call_id
                    pd = pending_by_id.pop(tid, None)
                    if pd is not None:
                        try:
                            result_data = json.loads(last.content)
                        except Exception:
                            result_data = {"output": str(last.content)[:500]}
                        pd["status"] = ("completed" if result_data.get("success")
                                        else "failed")
                        pd["result"] = result_data
                        self._emit("step_complete", dict(pd))

                # LLM final response (no more tool calls → done)
                elif (isinstance(last, AIMessage)
                      and not getattr(last, "tool_calls", None)
                      and last.content):
                    # No pending tools → agent considers the task done
                    if not pending_by_id:
                        pass  # Will exit loop naturally

            # Mark any pending steps as timed out
            for pd in pending_by_id.values():
                pd["status"] = "error"
                pd["result"] = {"success": False, "error": "execution timeout"}

            elapsed = time.time() - start
            success_count = sum(
                1 for s in steps if s.get("status") == "completed"
            )
            result = {
                "success": success_count > 0,
                "steps": steps,
                "summary": (
                    f"Executed {len(steps)} steps ({success_count} ok)"
                    f" in {elapsed:.1f}s"
                ),
                "total_steps": len(steps),
                "elapsed_ms": round(elapsed * 1000, 1),
            }
            self._emit("agent_done", {"status": "completed", **result})
            return result

        except Exception as e:
            elapsed = time.time() - start
            error_result = {
                "success": False,
                "steps": steps,
                "error": str(e),
                "total_steps": len(steps),
                "elapsed_ms": round(elapsed * 1000, 1),
            }
            self._emit("agent_done", {"status": "failed", **error_result})
            logger.exception("Agent execution failed")
            return error_result

    # ── 事件 ──────────────────────────────────────────────────

    def _emit(self, event: str, data: dict):
        """发送事件到回调（与 IPC 协议兼容）。"""
        if self._event_cb:
            self._event_cb({"type": event, **data})
