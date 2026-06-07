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
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
import unicodedata
from datetime import datetime, timezone
from queue import Queue, Empty
from typing import Any, Callable, Optional, Set

logger = logging.getLogger("agent.lingdong")

# ── Self-evolving agent modules ──
from agent.memory import MemoryStore
from agent.reflection import ReflectionEngine

DEFAULT_API_BASE = "https://api.xiaomimimo.com/v1"
DEFAULT_MODEL = "mimo-v2-omni"
MAX_STEPS = 50          # 单次任务最大步数
MAX_CONSECUTIVE_FAILURES = 5  # 连续失败上限 → 自动终止
_MIN_SUCCESS_RATE = 0.1  # 当成功率低于此值时终止（已完成步数 / 总步数）

# ── 已知多模态模型列表（可直接"看"截图，不需要 SoM 标号）──
# ── Imports from extracted modules ──
from agent.prompts import (
    is_multimodal_model,
    SCRIPTED_MODE_INSTRUCTIONS, CHAT_MODE_INSTRUCTIONS,
    TEXT_PROTOCOL_FORMAT_INSTRUCTIONS,
)


# ── Prompt constants imported from agent.prompts ──

_USE_FLOW_CHART_MODE = False  # Set True to use full tool prompt + flow chart
_FLOW_CHART_MODE_LOCK = threading.Lock()  # Thread safety for mode switching

def set_flow_chart_mode(enabled: bool = True):
    """Enable/disable flow chart mode (full tools + generate_flow/execute_flow)."""
    global _USE_FLOW_CHART_MODE
    with _FLOW_CHART_MODE_LOCK:
        _USE_FLOW_CHART_MODE = enabled
    from agent.meta_actions import set_flow_chart_mode as _set_meta_fc
    _set_meta_fc(enabled)

def _build_tools_text() -> str:
    """生成工具描述文本。

    text_protocol 模式: 仅元动作提示，LLM 使用 27 个元动作操作电脑。
    Flow chart mode: 按访问模式分组的完整工具列表。
    """
    if _USE_FLOW_CHART_MODE:
        from agent.tool_path_selector import build_path_prompt
        return build_path_prompt()
    from agent.meta_actions import get_prompt
    return get_prompt(use_flow_chart=_USE_FLOW_CHART_MODE)


def _build_clean_system_prompt(tools_text: str) -> str:
    """Return a compact, encoding-safe system prompt for text-only desktop control."""
    return f"""You are a general desktop automation agent controlling the computer with mouse and keyboard.

Core rules:
- Be honest. Do not report success until you have verified it from screen state, files, process/window state, URL, or other observable evidence.
- Prefer fast structured state when it exists: active window, processes, files, browser URL/tabs, clipboard, and screen size.
- In text-only mode, you cannot see images semantically. Treat OCR/SoM/YOLO as noisy structured hints, not as full visual understanding.
- UIA may be unavailable or misleading in custom-rendered apps. When UIA is disabled or weak, use active-window-limited OCR, SoM labels, YOLO boxes, generic visual input/control candidates, coordinates, clipboard typing, hotkeys, and verification.
- Do not create app-specific rules. Use generic strategies that work across desktop software.
- For text entry, prefer clipboard paste or type_clipboard over slow per-key typing. After clicking a likely input field, verify focus by caret/typed text/screen change when possible.
- For search boxes, a magnifier can be OCR'd as Q/O/0. Labels such as search/find, Chinese search terms, Q-search-like text, or a long rectangular control can all be search/input candidates.
- A blinking caret can be OCR'd as I/l/|. Treat a narrow vertical mark inside a control as possible focused input, but verify after typing.
- If an action fails, adapt using another generic route rather than repeating the same click.
- Use browser tools for browser-only tasks and verify with URL/tab state.
- Finish with done only after observable verification.

Output protocol:
Thought: concise reasoning
Tool: tool_name
Args:
  key: value

When finished:
Done:
Summary: verified result

Available tools:
{tools_text}
"""


# ====================================================================
#  LingdongAgent — 统一 Agent
# ====================================================================

from agent.supervisor import Supervisor
from agent.state_persistence import RunSummary, save_run, load_history
from agent.config import AgentConfig

class LingdongAgent:
    """统一的桌面自动化 AI Agent。

    属性:
        api_key / api_base / model: LLM 配置
        multimodal: 是否启用多模态视觉定位 (VLM 兜底)
        auto_learn: 是否自动学习 (截图→经验库)
        event_callback: IPC 事件回调
        stop_event: 停止信号
    """

    _glyph_templates_cache: list[tuple[str, object]] | None = None

    # ── 初始化 ────────────────────────────────────────────────

    def __init__(self, api_key: str = "", api_base: str = "", model: str = "",
                 event_callback: Optional[Callable] = None,
                 stop_event: Optional[threading.Event] = None,
                 multimodal: bool = False,
                 auto_learn: bool = False,
                 omniparser_enabled: bool = False,
                 temperature: float = 0.1,
                 max_tokens: int = 4096,
                 llm_retries: int = 2,
                 screenshot_quality: int = 60,
                 som_enabled: bool = True,
                 verify_enabled: bool = True,
                 local_vision_enabled: bool = False,
                 guidance_queue: Optional[Queue] = None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
        self.api_base = (api_base or os.environ.get("OPENAI_API_BASE", "")
                         or os.environ.get("LLM_API_BASE", "")
                         or DEFAULT_API_BASE).rstrip("/")
        self.model = model or os.environ.get("VLM_MODEL", "") or os.environ.get("LLM_MODEL", "") or DEFAULT_MODEL
        self.multimodal = multimodal
        self.auto_learn = auto_learn
        self._event_cb = event_callback
        self._stop_event = stop_event or threading.Event()
        self._guidance_queue: Optional[Queue] = guidance_queue
        self._chat_mode: bool = False
        self._tools_text: str = ""
        self._system_prompt: str = ""
        self._registry_loaded = False
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._llm_retries = llm_retries
        self._screenshot_quality = screenshot_quality
        self._som_enabled = som_enabled  # SoM 标号（截图元素编号）
        self._verify_enabled = verify_enabled
        self.sliding_window_limit = 8  # 默认 8, _unified_run 中从 config 覆盖
        self._omniparser = None  # OmniParser client (lazy init)
        self._omniparser_enabled = omniparser_enabled  # 默认关闭，可在设置中开启
        self._local_vision_enabled = local_vision_enabled  # 本地视觉引擎（默认关闭）
        self._local_vision = None  # LocalVisionEngine (lazy init)
        self._last_focus_window: dict[str, Any] = {}
        self._intended_focus_window: dict[str, Any] = {}
        self._trace_dir = ""
        self._llm_call_index = 0

        # 监督层 (Supervisor) — 被所有三种执行模式共享
        self.supervisor = Supervisor(
            emit_event=self._emit,
            stop_event=self._stop_event,
            auto_learn=self.auto_learn,
            multimodal=self.multimodal,
            api_key=self.api_key,
            api_base=self.api_base,
            model=self.model,
            screenshot_quality=self._screenshot_quality,
        )

        # Load run history for context continuity
        self._history: list = []
        try:
            self._history = load_history(limit=10)
        except Exception:
            pass

        # ── Self-evolving agent: memory + reflection ──
        self._memory_store = MemoryStore()
        self._reflection_engine = ReflectionEngine(
            call_llm_fn=self._call_llm,
            emit_fn=self._emit,
        )
        self._step_reflections: list[dict] = []
        self._reflection_enabled: bool = True

        # 导出环境变量供下游模块使用
        self._export_env_vars()

    # ── 工具加载 ──────────────────────────────────────────────


    def _export_env_vars(self):
        """导出 API 配置到环境变量，供下游模块（如 generate_flow_script）使用。"""
        if self.api_key:
            os.environ.setdefault("ANTHROPIC_API_KEY", self.api_key)
            os.environ.setdefault("OPENAI_API_KEY", self.api_key)
        if self.api_base:
            os.environ.setdefault("LLM_API_BASE", self.api_base)
        if self.model:
            os.environ.setdefault("LLM_MODEL", self.model)
    def load_tools(self):
        """加载 @feature 注册表，生成工具描述文本。"""
        from features.registry import registry
        import features._all  # noqa

        self._registry_loaded = True
        all_count = len([s for s in registry.all() if not s.deprecated and not s.hidden])
        self._tools_text = _build_tools_text()
        self._system_prompt = _build_clean_system_prompt(self._tools_text) + TEXT_PROTOCOL_FORMAT_INSTRUCTIONS
        logger.info("Registry: %d tools total, prompt: %d chars", all_count, len(self._system_prompt))

    def _begin_prompt_trace(self, goal: str):
        if self._trace_dir:
            return
        root = os.path.join(tempfile.gettempdir(), "lingdong_agent_prompt_trace")
        safe_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._trace_dir = os.path.join(root, f"run_{safe_ts}_{os.getpid()}")
        try:
            os.makedirs(self._trace_dir, exist_ok=True)
            with open(os.path.join(self._trace_dir, "goal.txt"), "w", encoding="utf-8") as f:
                f.write(goal)
            self._emit("log", {"level": "info", "message": f"Prompt trace: {self._trace_dir}"})
        except Exception:
            self._trace_dir = ""

    def _trace_llm(self, kind: str, data: Any):
        if not self._trace_dir:
            return
        try:
            self._llm_call_index += 1
            path = os.path.join(self._trace_dir, f"{self._llm_call_index:03d}_{kind}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _ensure_registry(self):
        if not self._registry_loaded:
            self.load_tools()

    # ── 事件 ──────────────────────────────────────────────────

    def _emit(self, event: str, data: dict):
        """发送事件到回调 (IPC 协议兼容)。"""
        if self._event_cb:
            self._event_cb({"type": event, **data})

    def _get_screen_context(self, minimal: bool = False) -> str:
        """获取当前屏幕上下文。

        minimal=True: 仅窗口标题 + 鼠标位置（适合多模态模型，它们能从截图直接"看"到界面）
        minimal=False: 完整上下文（UIA 树 + 进程列表 + 窗口列表）
        """
        parts = []
        try:
            from features.registry import registry
            aw = registry.execute("get_active_window")
            if isinstance(aw, dict):
                aw_data = aw.get("result", aw)
                if isinstance(aw_data, dict):
                    title = aw_data.get("title", "") or aw_data.get("name", "")
                    proc = aw_data.get("process", aw_data.get("class", ""))
                    if title:
                        parts.append(f"活动窗口: 「{title}」")
                        if proc:
                            parts[-1] += f" ({proc})"
        except Exception:
            pass

        if not minimal:
            # 所有窗口列表（非 minimal 模式才获取）
            try:
                wins_raw = registry.execute("list_windows")
                wins = wins_raw.get("result", wins_raw) if isinstance(wins_raw, dict) else wins_raw
                all_titles = []
                if isinstance(wins, list):
                    for w in wins:
                        t = w.get("title", "") if isinstance(w, dict) else str(w)
                        if t and t.strip():
                            all_titles.append(t.strip()[:40])
                if all_titles:
                    from collections import Counter
                    title_counts = Counter(all_titles)
                    summary = []
                    for title, count in title_counts.most_common(12):
                        if count > 1:
                            summary.append(f"「{title}」×{count}")
                        else:
                            summary.append(f"「{title}」")
                    parts.append(f"所有窗口: {'; '.join(summary)}")
            except Exception:
                pass

            # 进程摘要（非 minimal 模式）
            try:
                import psutil
                proc_summary = []
                seen_procs = set()
                for p in psutil.process_iter(["name"]):
                    try:
                        pn = (p.info["name"] or "").lower()
                        if pn and pn not in seen_procs and pn.endswith(".exe") and len(pn) < 30:
                            seen_procs.add(pn)
                            proc_summary.append(pn.replace(".exe", ""))
                    except Exception:
                        pass
                if proc_summary:
                    parts.append(f"运行中: {', '.join(sorted(proc_summary)[:16])}")
            except Exception:
                pass

        # 鼠标位置（所有模式都需要）
        try:
            import pyautogui
            w, h = pyautogui.size()
            x, y = pyautogui.position()
            parts.append(f"屏幕: {w}×{h}  鼠标: ({x},{y})")
        except Exception:
            pass

        if not minimal:
            # UIA 控件摘要（非 minimal 模式）
            a11y = self.supervisor.capture_a11y_summary()
            if a11y:
                parts.append("")
                parts.append(a11y)

        return "\n".join(parts)

    # ══════════════════════════════════════════════════════════
    #  文本协议模式 (MIMO V2 Omni 等纯文本模型)
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def _title_matches(actual: str, expected: str) -> bool:
        a = LingdongAgent._clean_visual_label(actual).lower()
        e = LingdongAgent._clean_visual_label(expected).lower()
        if not a or not e:
            return False
        return a == e or e in a or a in e

    def _find_window_by_title(self, title: str) -> dict[str, Any] | None:
        if not title:
            return None
        try:
            import win32gui
            matches: list[dict[str, Any]] = []

            def enum_cb(hwnd, _):
                try:
                    if not win32gui.IsWindowVisible(hwnd):
                        return
                    text = win32gui.GetWindowText(hwnd) or ""
                    if not self._title_matches(text, title):
                        return
                    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                    if right - left < 80 or bottom - top < 80:
                        return
                    matches.append({
                        "hwnd": hwnd,
                        "title": self._clean_visual_label(text),
                        "class_name": win32gui.GetClassName(hwnd) or "",
                        "rect": (int(left), int(top), int(right), int(bottom)),
                    })
                except Exception:
                    return

            win32gui.EnumWindows(enum_cb, None)
            if not matches:
                return None
            matches.sort(key=lambda m: (
                0 if self._title_matches(str(m.get("title", "")), title) else 1,
                -len(str(m.get("title", ""))),
            ))
            return matches[0]
        except Exception:
            return None

    def _remember_intended_focus(self, title: str, ttl: float = 8.0) -> None:
        title = self._clean_visual_label(title)
        if not title:
            return
        found = self._find_window_by_title(title)
        self._intended_focus_window = {
            "title": found.get("title", title) if found else title,
            "class_name": found.get("class_name", "") if found else "",
            "rect": found.get("rect") if found else None,
            "expires_at": time.time() + ttl,
        }

    @staticmethod
    def _som_text_matches(target: str, text: str) -> bool:
        t = LingdongAgent._clean_visual_label(target).lower()
        s = LingdongAgent._clean_visual_label(text).lower()
        if not t or not s:
            return False
        aliases = {
            "*": {"*", "x", "×"},
            "x": {"*", "x", "×"},
            "×": {"*", "x", "×"},
            "/": {"/", "÷"},
            "÷": {"/", "÷"},
        }
        if len(t) == 1:
            return s in aliases.get(t, {t})
        return t in s

    def _active_window_rect(self) -> tuple[int, int, int, int] | None:
        try:
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return None
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if right - left < 80 or bottom - top < 80:
                return None
            fg_title = self._clean_visual_label(win32gui.GetWindowText(hwnd) or "")
            intended = self._intended_focus_window if isinstance(self._intended_focus_window, dict) else {}
            if intended and time.time() <= float(intended.get("expires_at", 0) or 0):
                intended_title = str(intended.get("title", "") or "")
                if intended_title and not self._title_matches(fg_title, intended_title):
                    refreshed = self._find_window_by_title(intended_title)
                    if refreshed:
                        self._intended_focus_window.update({
                            "title": refreshed.get("title", intended_title),
                            "class_name": refreshed.get("class_name", ""),
                            "rect": refreshed.get("rect"),
                        })
                        self._last_focus_window = {
                            "title": refreshed.get("title", intended_title),
                            "class_name": refreshed.get("class_name", ""),
                            "rect": refreshed.get("rect"),
                        }
                        return refreshed.get("rect")
                    rect = intended.get("rect")
                    if isinstance(rect, (tuple, list)) and len(rect) == 4:
                        return int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
            elif intended:
                self._intended_focus_window = {}
            self._last_focus_window = {
                "title": fg_title,
                "class_name": win32gui.GetClassName(hwnd) or "",
                "rect": (int(left), int(top), int(right), int(bottom)),
            }
            return int(left), int(top), int(right), int(bottom)
        except Exception:
            return None

    @staticmethod
    def _clean_visual_label(text: str) -> str:
        s = re.sub(r"\s+", " ", str(text or "").strip())
        s = "".join(ch for ch in s if unicodedata.category(ch) not in ("So", "Co", "Cs"))
        return s.strip(" '\"`|:;,.")

    @staticmethod
    def _looks_corrupt_label(text: str) -> bool:
        s = str(text or "").strip()
        if not s:
            return False
        if "?" in s and any(ord(ch) > 127 for ch in s):
            return True
        if any(unicodedata.category(ch) in ("Co", "Cs") for ch in s):
            return True
        return any(marker in s for marker in ("锟", "�", "閹", "閸", "閻", "閺", "鈧", "閿", "鎷", "鉅"))

    @staticmethod
    def _is_search_like_label(text: str) -> bool:
        s = str(text or "").strip().lower().replace(" ", "")
        return any(token in s for token in (
            "search", "find", "qsearch",
            "\u641c\u7d22", "\u641c\u7d20", "\u641c\u5bfb", "\u67e5\u627e",
            "q\u641c", "0\u641c", "o\u641c",
        ))

    @staticmethod
    def _is_caret_like_label(text: str, w: int, h: int) -> bool:
        s = str(text or "").strip()
        return s in ("I", "l", "|", "丨") and h >= 10 and w <= 12

    def _redraw_som_overlay(self, source_b64: str, label_map: dict) -> str:
        try:
            import cv2
            import numpy as np
            img = cv2.imdecode(np.frombuffer(base64.b64decode(source_b64), np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return source_b64
            h_img, w_img = img.shape[:2]
            overlaid = img.copy()
            for idx, elem in label_map.items():
                cx = int(elem.get("x", 0) or 0)
                cy = int(elem.get("y", 0) or 0)
                ew = int(elem.get("w", 24) or 24)
                eh = int(elem.get("h", 24) or 24)
                x = max(0, min(w_img - 1, cx - ew // 2))
                y = max(0, min(h_img - 1, cy - eh // 2))
                x2 = max(0, min(w_img - 1, x + ew))
                y2 = max(0, min(h_img - 1, y + eh))
                cv2.rectangle(overlaid, (x, y), (x2, y2), (66, 133, 244), 2)
                label = str(idx)
                (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
                cv2.rectangle(overlaid, (x, max(0, y - lh - 8)), (x + lw + 6, max(0, y - 1)), (66, 133, 244), -1)
                cv2.putText(overlaid, label, (x + 2, max(lh, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
            _, buf = cv2.imencode(".jpg", overlaid, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return base64.b64encode(buf).decode()
        except Exception:
            return source_b64

    def _augment_active_window_controls(self, source_b64: str, rect: tuple[int, int, int, int],
                                        elements: list[dict], max_new: int = 24) -> list[dict]:
        try:
            import cv2
            import numpy as np
            img = cv2.imdecode(np.frombuffer(base64.b64decode(source_b64), np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                return elements
            h_img, w_img = img.shape[:2]
            left, top, right, bottom = rect
            left, top = max(0, left), max(0, top)
            right, bottom = min(w_img, right), min(h_img, bottom)
            if right - left < 120 or bottom - top < 120:
                return elements
            roi = img[top:bottom, left:right]
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5))
            small_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3))
            edges = cv2.Canny(gray, 35, 90)
            closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
            contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            bg = float(np.median(gray))
            bright = cv2.inRange(gray, int(min(255, bg + 5)), int(min(255, bg + 85)))
            bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, kernel, iterations=2)
            bright_contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            mid_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            midtone = cv2.inRange(gray, max(0, int(bg - 10)), int(min(255, bg + 70)))
            midtone = cv2.morphologyEx(midtone, cv2.MORPH_OPEN, mid_kernel, iterations=1)
            midtone = cv2.morphologyEx(midtone, cv2.MORPH_CLOSE, mid_kernel, iterations=1)
            midtone_contours, _ = cv2.findContours(midtone, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            # Generic button candidates: many custom-rendered apps expose square-ish
            # controls without UIA names, while the previous pass only kept long fields.
            smooth = cv2.GaussianBlur(gray, (3, 3), 0)
            button_edges = cv2.Canny(smooth, 20, 70)
            button_edges = cv2.morphologyEx(button_edges, cv2.MORPH_CLOSE, small_kernel, iterations=1)
            button_contours, _ = cv2.findContours(button_edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            def _single_glyph_label(crop) -> str:
                try:
                    from PIL import Image, ImageDraw, ImageFont
                    if LingdongAgent._glyph_templates_cache is None:
                        chars = "0123456789+-=.%×÷*/"
                        font_paths = [
                            r"C:\Windows\Fonts\segoeui.ttf",
                            r"C:\Windows\Fonts\seguisym.ttf",
                            r"C:\Windows\Fonts\arial.ttf",
                        ]
                        templates = []
                        for fp in font_paths:
                            if not os.path.exists(fp):
                                continue
                            for size in (22, 24, 26, 28, 30, 32, 36, 40):
                                try:
                                    font = ImageFont.truetype(fp, size)
                                except Exception:
                                    continue
                                for ch in chars:
                                    canvas = Image.new("L", (80, 80), 0)
                                    draw = ImageDraw.Draw(canvas)
                                    bbox = draw.textbbox((0, 0), ch, font=font)
                                    tx = (80 - (bbox[2] - bbox[0])) // 2 - bbox[0]
                                    ty = (80 - (bbox[3] - bbox[1])) // 2 - bbox[1]
                                    draw.text((tx, ty), ch, fill=255, font=font)
                                    arr = np.array(canvas)
                                    cnts, _ = cv2.findContours(arr, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                                    if not cnts:
                                        continue
                                    x0, y0, tw, th = cv2.boundingRect(np.vstack(cnts))
                                    if tw <= 0 or th <= 0:
                                        continue
                                    tmpl = cv2.resize(arr[y0:y0 + th, x0:x0 + tw], (32, 32), interpolation=cv2.INTER_AREA)
                                    templates.append((ch, tmpl))
                        LingdongAgent._glyph_templates_cache = templates
                    templates = LingdongAgent._glyph_templates_cache or []
                    if not templates:
                        return ""
                    crop_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
                    med = float(np.median(crop_gray))
                    if med < 128:
                        mask = cv2.inRange(crop_gray, max(0, int(med + 25)), 255)
                    else:
                        mask = cv2.inRange(crop_gray, 0, min(255, int(med - 25)))
                    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    boxes = []
                    for c2 in cnts:
                        gx, gy, gw, gh = cv2.boundingRect(c2)
                        if 2 <= gw <= 70 and 6 <= gh <= 70:
                            boxes.append((gx, gy, gw, gh))
                    if not boxes:
                        return ""
                    gx = min(bx[0] for bx in boxes)
                    gy = min(bx[1] for bx in boxes)
                    gx2 = max(bx[0] + bx[2] for bx in boxes)
                    gy2 = max(bx[1] + bx[3] for bx in boxes)
                    glyph = cv2.resize(mask[gy:gy2, gx:gx2], (32, 32), interpolation=cv2.INTER_AREA)
                    best_score = 0.0
                    best_char = ""
                    for ch, tmpl in templates:
                        score = float(cv2.matchTemplate(glyph, tmpl, cv2.TM_CCOEFF_NORMED)[0][0])
                        if score > best_score:
                            best_score = score
                            best_char = ch
                    return best_char if best_score >= 0.72 else ""
                except Exception:
                    return ""

            def _near_existing(cx: int, cy: int, bw: int, bh: int) -> bool:
                for elem in elements:
                    ex = int(elem.get("x", 0) or 0)
                    ey = int(elem.get("y", 0) or 0)
                    ew = int(elem.get("w", 0) or 0)
                    eh = int(elem.get("h", 0) or 0)
                    ecx = ex if ex > left else ex + ew // 2
                    ecy = ey if ey > top else ey + eh // 2
                    if abs(ecx - cx) <= max(bw, ew, 24) // 2 and abs(ecy - cy) <= max(bh, eh, 24) // 2:
                        return True
                return False

            candidates = []
            roi_w = right - left
            for c in list(contours) + list(bright_contours):
                x, y, bw, bh = cv2.boundingRect(c)
                if bw < 70 or bh < 16 or bh > 90:
                    continue
                if bw > roi_w * 0.96 or bw / max(bh, 1) < 2.0:
                    continue
                area = bw * bh
                if area < 1400:
                    continue
                cx = left + x + bw // 2
                cy = top + y + bh // 2
                if _near_existing(cx, cy, bw, bh):
                    continue
                rel_y = y / max(bottom - top, 1)
                text = "input/search field" if rel_y < 0.4 and bw >= roi_w * 0.32 else "visual control"
                candidates.append({"text": text, "x": cx, "y": cy, "w": bw, "h": bh, "_area": area})

            for c in list(button_contours) + list(midtone_contours):
                x, y, bw, bh = cv2.boundingRect(c)
                if bw < 28 or bh < 20 or bw > 260 or bh > 130:
                    continue
                ratio = bw / max(bh, 1)
                if ratio < 0.45 or ratio > 8.0:
                    continue
                area = bw * bh
                if area < 650:
                    continue
                if bw > roi_w * 0.97 and bh > (bottom - top) * 0.25:
                    continue
                cx = left + x + bw // 2
                cy = top + y + bh // 2
                if _near_existing(cx, cy, bw, bh):
                    continue
                rel_y = y / max(bottom - top, 1)
                text = "input/search field" if rel_y < 0.4 and bw >= roi_w * 0.32 else "visual control"
                if text == "visual control":
                    crop = roi[max(0, y):min(roi.shape[0], y + bh), max(0, x):min(roi.shape[1], x + bw)]
                    glyph = _single_glyph_label(crop) if crop.size else ""
                    if glyph:
                        text = glyph
                candidates.append({"text": text, "x": cx, "y": cy, "w": bw, "h": bh, "_area": area})

            caret_mask = cv2.inRange(gray, 180, 255)
            caret_contours, _ = cv2.findContours(caret_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in caret_contours:
                x, y, bw, bh = cv2.boundingRect(c)
                if bw <= 6 and 12 <= bh <= 40:
                    cx = left + x
                    cy = top + y + bh // 2
                    if not _near_existing(cx, cy, 160, 32):
                        candidates.append({"text": "focused input field", "x": cx + 80, "y": cy, "w": 180, "h": max(28, bh), "_area": 99999})

            candidates.sort(key=lambda e: (-int(e.get("_area", 0)), int(e.get("y", 0)), int(e.get("x", 0))))
            for cand in candidates[:max_new]:
                cand.pop("_area", None)
                elements.append(cand)
            return elements
        except Exception:
            return elements

    def _filter_som_to_active_window(self, som_result: dict, source_b64: str) -> dict:
        if not self.supervisor.window_aware_ocr:
            return som_result
        rect = self._active_window_rect()
        label_map = som_result.get("label_map") if isinstance(som_result, dict) else None
        if not rect or not isinstance(label_map, dict) or not label_map:
            return som_result
        left, top, right, bottom = rect
        kept = []
        for _, elem in label_map.items():
            try:
                cx = int(elem.get("x", 0) or 0)
                cy = int(elem.get("y", 0) or 0)
            except Exception:
                continue
            if left <= cx <= right and top <= cy <= bottom:
                kept.append(dict(elem))
        if not kept:
            return som_result
        kept = self._augment_active_window_controls(source_b64, rect, kept)
        kept.sort(key=lambda e: (int(e.get("y", 0) or 0), int(e.get("x", 0) or 0)))
        filtered = {i + 1: elem for i, elem in enumerate(kept[:40])}
        before = len(label_map)
        after = len(filtered)
        if after != before:
            self._emit("log", {"level": "info", "message": f"Window-aware vision targets: {before}->{after}"})
        return {**som_result, "image_b64": self._redraw_som_overlay(source_b64, filtered), "label_map": filtered, "count": after}

    @staticmethod
    def _visual_label_rank(text: str, w: int, h: int) -> int:
        if LingdongAgent._is_search_like_label(text) or text in ("focused input field", "input/search field"):
            return 0
        if not text or text == "[icon]" or text.lower().startswith("[icon]"):
            return 1
        if LingdongAgent._looks_corrupt_label(text):
            return 5
        compact = text.replace(" ", "")
        area = max(0, w) * max(0, h)
        if re.fullmatch(r"[0-9:+-]{2,5}", compact) or LingdongAgent._is_caret_like_label(compact, w, h):
            return 4
        if len(compact) > 18 and h <= 22:
            return 3
        if area >= 1600:
            return 1
        return 2

    @staticmethod
    def _compact_som_label(label_map: dict, max_items: int = 30) -> str:
        if not label_map:
            return ""
        def _score(item: tuple) -> tuple:
            idx, elem = item
            text = LingdongAgent._clean_visual_label(elem.get("text", ""))
            w = int(elem.get("w", 0) or 0)
            h = int(elem.get("h", 0) or 0)
            rank = LingdongAgent._visual_label_rank(text, w, h)
            area = w * h
            return (rank, -min(area, 50000), int(elem.get("y", 0) or 0), int(elem.get("x", 0) or 0), int(idx))
        selected = sorted(label_map.items(), key=_score)[:max_items]
        selected.sort(key=lambda kv: int(kv[0]))
        lines = []
        for idx, elem in selected:
            text = LingdongAgent._clean_visual_label(elem.get("text", ""))
            if LingdongAgent._is_search_like_label(text):
                text = f"search field ({text})"
            elif LingdongAgent._is_caret_like_label(text, int(elem.get("w", 0) or 0), int(elem.get("h", 0) or 0)):
                text = "focused input field"
            elif not text or text == "[icon]":
                text = "icon/button"
            elif LingdongAgent._looks_corrupt_label(text):
                text = "visual target"
            text = re.sub(r"\s+", " ", text)[:36]
            x = int(elem.get("x", 0) or 0)
            y = int(elem.get("y", 0) or 0)
            lines.append(f"#{idx} {text} ({x},{y})")
        hidden = max(0, len(label_map) - len(selected))
        suffix = f"\n... {hidden} lower-priority targets omitted" if hidden else ""
        return "\nVisible targets (use #N):\n" + "\n".join(lines) + suffix

    def _apply_som(self, b64_img: str, multimodal: bool = False) -> dict:
        """Set-of-Mark 标号：检测屏幕元素（UIA+OCR），画框编号。

        多模态模式 (multimodal=True): 跳过所有标注，直接返回原始截图。
            模型可以直接从截图中"看"到界面，不需要 #N 编号。
        非多模态模式: 完整 SoM 流程，UIA+OCR+画框编号。
            LLM 看到标号截图后直接用 #N 定位，无需输出文字描述。

        返回 {image_b64, label_map ({N: {text, x, y, w, h}}), count}。
        """
        # ── 多模态快速路径：模型自己"看"截图，不需要标注 ──
        if multimodal:
            return {"image_b64": b64_img, "label_map": {}, "count": 0, "_mode": "direct"}

        # ── 本地视觉引擎（YOLO ONNX DirectML + OCR 合并）──
        # 仅对纯文本模型（如 DeepSeek）生效，多模态模型走上面 direct 路径。
        if self._local_vision_enabled and not multimodal:
            try:
                lv_start = time.time()
                self._emit("log", {"level": "info", "message": "Local vision parse started"})
                if self._local_vision is None:
                    from agent.local_vision_engine import LocalVisionEngine
                    self._local_vision = LocalVisionEngine(enabled=True)
                if self._local_vision.available:
                    # UIA 启用时才收集 UIA 元素，否则纯视觉（YOLO+OCR）
                    prefer_local_vision = False
                    if hasattr(self.supervisor, "should_prefer_local_vision"):
                        try:
                            prefer_local_vision = self.supervisor.should_prefer_local_vision()
                        except Exception:
                            prefer_local_vision = False
                    uia_items = None
                    if self.supervisor.uia_enabled and not prefer_local_vision:
                        uia_items = self._collect_uia_elements()
                    lv_result = self._local_vision.parse(b64_img, uia_elements=uia_items)
                    self._emit("log", {
                        "level": "info",
                        "message": f"Local vision parse finished in {time.time() - lv_start:.1f}s",
                    })
                    if lv_result and self.supervisor.window_aware_ocr:
                        lv_result = self._filter_som_to_active_window(lv_result, b64_img)
                    if lv_result and lv_result["count"] > 0:
                        msg = f"本地视觉引擎: {lv_result['count']} 个元素 (YOLO DirectML)"
                        if lv_result.get("caption"):
                            msg += f" | {lv_result['caption'][:60]}"
                        self._emit("log", {"level": "info", "message": msg})
                        if prefer_local_vision:
                            self._emit("log", {
                                "level": "debug",
                                "message": "UIA-poor window: local vision is running without UIA labels",
                            })
                        return lv_result
                    else:
                        self._emit("log", {
                            "level": "debug",
                            "message": "本地视觉引擎未检出元素，降级到 OmniParser/UIA+OCR",
                        })
            except Exception as exc:
                logger.debug("Local vision engine unavailable, falling back: %s", exc)

        # ── OmniParser（YOLOv8 图标检测 + Florence-2 描述 + OCR）──
        if self._omniparser_enabled and (self._omniparser is not None or self._som_enabled):
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

        nparr = np.frombuffer(base64.b64decode(b64_img), np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        h_img, w_img = img.shape[:2]

        elements: list[dict] = []
        seen_positions: set = set()

        # 1. UIA 控件树（uia_enabled=False 时跳过，纯视觉模式）
        if self.supervisor.uia_enabled:
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

        # 窗口限定：window_aware_ocr 开启时只保留活动窗口内的元素
        if self.supervisor.window_aware_ocr:
            try:
                import win32gui
                hwnd = win32gui.GetForegroundWindow()
                if hwnd:
                    rect = win32gui.GetWindowRect(hwnd)
                    wx, wy, wr, wb = rect[0], rect[1], rect[2], rect[3]
                    before = len(elements)
                    elements = [el for el in elements if wx <= el["x"] + el["w"]//2 <= wr and wy <= el["y"] + el["h"]//2 <= wb]
                    if elements:
                        self._emit("log", {"level": "info", "message": f"窗口限定: {before}→{len(elements)}个元素"})
            except Exception:
                pass
            rect = self._active_window_rect()
            if rect:
                elements = self._augment_active_window_controls(b64_img, rect, elements)

        elements.sort(key=lambda e: (
            self._visual_label_rank(str(e.get("text", "")), int(e.get("w", 0) or 0), int(e.get("h", 0) or 0)),
            int(e.get("y", 0) or 0) // 25,
            int(e.get("x", 0) or 0),
        ))

        label_map: dict = {}
        overlaid = img.copy()
        for i, elem in enumerate(elements[:40]):
            idx = i + 1
            x, y, w, h = elem["x"], elem["y"], elem["w"], elem["h"]
            cx = min(w_img - 1, max(0, x + w // 2))
            cy = min(h_img - 1, max(0, y + h // 2))
            ow = min(w_img - x, w)
            oh = min(h_img - y, h)

            label_map[idx] = {"text": elem["text"], "x": cx, "y": cy, "w": ow, "h": oh}

            cv2.rectangle(overlaid, (x, y), (x + ow, y + oh), (66, 133, 244), 2)
            lbl = str(idx)
            (lbl_w, lbl_h), _ = cv2.getTextSize(lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
            bg_x1, bg_y1 = max(0, x - 1), max(0, y - lbl_h - 8)
            bg_x2, bg_y2 = min(w_img, x + lbl_w + 5), max(0, y - 1)
            cv2.rectangle(overlaid, (bg_x1, bg_y1), (bg_x2, bg_y2), (66, 133, 244), -1)
            cv2.putText(overlaid, lbl, (x, max(lbl_h, y - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

        _, buf = cv2.imencode(".jpg", overlaid, [cv2.IMWRITE_JPEG_QUALITY, 85])
        result_b64 = base64.b64encode(buf).decode()

        return {"image_b64": result_b64, "label_map": label_map, "count": len(label_map)}

    # ── 本地视觉引擎辅助 ──────────────────────────────────────────

    def _collect_uia_elements(self, max_depth: int = 3) -> list[dict]:
        """Collect UIA control tree elements for local vision engine text labels.

        Returns list of {text, x, y, w, h} for merge with YOLO detections.
        Returns empty list on failure (non-fatal, YOLO will run without text labels).
        """
        try:
            from features.registry import registry
            result = registry.execute("a11y_get_tree", depth=max_depth)
            tree = result.get("result", result)
            if isinstance(tree, dict):
                tree = tree.get("result", tree)

            items: list[dict] = []
            seen_positions: set = set()

            def _collect(node, depth=0):
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
                        items.append({"text": f"[{role}] {name}", "x": x, "y": y, "w": w, "h": h})
                for child in (node.get("children", node.get("items", [])) or []):
                    if isinstance(child, dict):
                        _collect(child, depth + 1)

            _collect(tree)
            return items
        except Exception:
            return []

    def _call_llm(self, messages: list[dict], max_retries: int | None = None) -> dict | None:
        """调用 LLM API (兼容文本和视觉消息)。"""
        import requests as _req

        # 使用不信任环境代理的 Session，绕过 Clash 等本地代理干扰
        _session = _req.Session()
        _session.trust_env = False

        retries = max_retries if max_retries is not None else self._llm_retries
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        self._trace_llm("request", payload)
        last_err = ""
        for attempt in range(1 + retries):
            try:
                resp = _session.post(
                    f"{self.api_base}/chat/completions",
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                    timeout=120,
                )
                if resp.status_code == 429:
                    self._emit("log", {"level": "warn", "message": f"API rate limited, retry {attempt+1}/{retries}"})
                    time.sleep(3 * (attempt + 1))
                    continue
                resp.raise_for_status()
                data = resp.json()
                msg = data["choices"][0]["message"]
                content = (msg.get("content", "") or
                          msg.get("reasoning_content", "") or
                          msg.get("reasoning", "") or "")
                if not content:
                    self._emit("log", {"level": "warn", "message": "LLM returned empty content, retry"})
                    continue
                self._trace_llm("response", data)
                return data
            except _req.exceptions.Timeout:
                last_err = "API timeout (120s)"
                self._emit("log", {"level": "warn", "message": f"API timeout, retry {attempt+1}/{retries}"})
            except _req.exceptions.RequestException as e:
                last_err = str(e)
                resp_body = ""
                if hasattr(e, 'response') and e.response is not None:
                    try: resp_body = e.response.text[:200]
                    except Exception: pass
                self._emit("log", {"level": "warn", "message": f"API error: {str(e)[:100]}. Body: {resp_body}"})
                delay = 0.5 if "ConnectionReset" in str(e) or "Connection aborted" in str(e) else 2 * (attempt + 1)
                time.sleep(delay)
        self._emit("log", {"level": "error", "message": f"LLM API failed after {retries} retries: {last_err}"})
        return None

    @staticmethod
    def _parse_response(content: str) -> tuple:
        """解析文本协议回复，返回 (action, name, args)。

        action: 'tool' | 'done' | 'error'
        """
        from agent.text_protocol import parse_response
        return parse_response(content)

    @staticmethod
    def _parse_all_tools(content: str) -> list[tuple[str, dict]]:
        """解析文本协议回复中所有 工具: 块，返回 [(name, args), ...] 列表。"""
        from agent.text_protocol import parse_tool_blocks
        return parse_tool_blocks(content)

    # ══════════════════════════════════════════════════════════
    #  记忆注入统一入口
    # ══════════════════════════════════════════════════════════

    def _inject_memory_prompt(self, goal: str) -> bool:
        """注入历史记忆到 system_prompt。所有入口路径统一使用此方法。"""
        if not self._reflection_enabled:
            return False
        try:
            memory_prompt = self._memory_store.build_context_prompt(goal)
            if memory_prompt:
                self._system_prompt = self._system_prompt + "\n\n" + memory_prompt
                self._emit("log", {"level": "info", "message": "已注入历史记忆到上下文"})
                return True
        except Exception:
            pass
        return False

    # ══════════════════════════════════════════════════════════
    #  对话模式（聊天 + 执行 并行）
    # ══════════════════════════════════════════════════════════

    def _process_chat_messages(self, messages: list) -> bool:
        """检查 guidance_queue，将用户消息注入消息历史。
        同时用 PatternAnalyzer 提取学习信号 (零 LLM 成本)。

        返回 True 表示有新的用户消息被处理。
        """
        if self._guidance_queue is None:
            return False
        processed = False
        try:
            while True:
                msg = self._guidance_queue.get_nowait()
                text = (msg.get("text", msg) if isinstance(msg, dict) else str(msg)).strip()
                if not text:
                    continue

                messages.append({
                    "role": "user",
                    "content": f"[用户消息] {text}\n\n请根据这条消息回应：如果是聊天就自然回复，如果是指引就调整策略，如果是新任务就在当前工作完成后执行。"
                })
                self._emit("log", {"level": "info", "message": f"收到用户消息: {text[:100]}"})
                processed = True

                # ── 实时信号提取 (零 LLM 成本) ──
                if self._reflection_enabled:
                    try:
                        from agent.memory import PatternAnalyzer
                        signal = PatternAnalyzer.analyze(text)
                        if signal["type"] == "correction" and signal["confidence"] >= 0.7:
                            self._memory_store.add_rule(
                                rule_text=f"用户反馈: {text[:200]}",
                                trigger_context="general",
                                confidence=signal["confidence"] * 0.8,
                                category="approach",
                                is_guardrail=False,
                                evidence=text[:300],
                            )
                            self._emit("log", {
                                "level": "info",
                                "message": f"[学习] 从用户反馈中提取纠正信号 (confidence: {signal['confidence']:.0%})"
                            })
                        elif signal["type"] == "guardrail":
                            self._memory_store.add_rule(
                                rule_text=f"约束: {text[:200]}",
                                trigger_context="general",
                                confidence=signal["confidence"] * 0.9,
                                category="guardrail",
                                is_guardrail=True,
                                evidence=text[:300],
                            )
                            self._emit("log", {
                                "level": "info",
                                "message": f"[学习] 从用户反馈中提取约束规则 (confidence: {signal['confidence']:.0%})"
                            })
                    except Exception:
                        pass
        except Empty:
            pass
        return processed

    def _emit_chat_response(self, text: str):
        """发送 AI 聊天响应事件到前端。"""
        self._emit("log", {"level": "info", "message": f"AI回复: {text[:200]}"})
        if self._event_cb:
            try:
                self._event_cb({"type": "agent_chat_response", "text": text})
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════
    #  视觉策略（方案3: OCR → LLM → 点击）
    # ══════════════════════════════════════════════════════════

    def _visual_strategy_ocr_llm(self, tool_name: str, args: dict) -> dict:
        """方案3: OCR截取所有文字 → LLM选择最匹配目标 → 定位坐标 → 点击。

        适用于 click/right_click/hover/mouse_move 等定位操作。
        当 UIA 无法精确匹配时，用 LLM 从 OCR 文字列表中做语义选择。
        """
        if tool_name not in ("click_target", "right_click", "hover", "mouse_move"):
            return args

        target = args.get("target", "") or args.get("from_target", "")
        if not target or not isinstance(target, str):
            return args
        # 跳过已有坐标
        if re.match(r'^-?\d+\s*,\s*-?\d+$', target.strip()):
            return args

        # 0. 优先用 SoM 标号中的 RapidOCR 文字坐标
        if self.supervisor._last_som_map:
            target_clean = target.strip().strip("'\"")
            for idx, m in self.supervisor._last_som_map.items():
                if target_clean in m.get("text", ""):
                    args = dict(args)
                    args["target"] = f"{m['x']},{m['y']}"
                    self._emit("log", {
                        "level": "info",
                        "message": f"SoM文字: 「{target_clean}」→ #{idx}「{m['text']}」→ {m['x']},{m['y']}",
                    })
                    return args

        # 1. OCR 获取屏幕上所有文字（兜底）
        try:
            from features.registry import registry
            ocr_r = registry.execute("ocr_extract_all")
            raw = ocr_r.get("result", ocr_r)
            if isinstance(raw, dict):
                raw = raw.get("result", raw.get("items", raw.get("data", [])))
            items = raw if isinstance(raw, list) else []
        except Exception:
            return args

        if not items:
            return args

        # 构建文字列表
        text_entries = []
        for item in items:
            text = (item.get("text", "") or "").strip()[:60]
            if not text:
                continue
            box = item.get("box", item.get("bbox", item.get("bounding_box", item)))
            x, y = 0, 0
            if isinstance(box, dict):
                x = int(box.get("x", box.get("left", 0)))
                y = int(box.get("y", box.get("top", 0)))
            elif isinstance(box, (list, tuple)) and len(box) >= 2:
                x, y = int(box[0]), int(box[1])
            text_entries.append({"text": text, "x": x, "y": y})

        if not text_entries:
            return args

        # 2. 如果文字数量少（≤5），直接尝试精确匹配
        if len(text_entries) <= 5:
            target_lower = target.lower().strip().strip("'\"")
            for entry in text_entries:
                if target_lower in entry["text"].lower():
                    args = dict(args)
                    args["target"] = f"{entry['x']},{entry['y']}"
                    self._emit("log", {"level": "info", "message": f"OCR精确匹配: 「{target}」→ ({entry['x']},{entry['y']})"})
                    return args
            # 没有精确匹配，走 LLM 选择
            if not self.api_key:
                return args

        # 3. 文字较多 → 调用 LLM 做语义选择
        text_list = "\n".join(f"{i+1}. 「{e['text']}」" for i, e in enumerate(text_entries))
        prompt = (
            f"屏幕上的文字列表:\n{text_list}\n\n"
            f"用户想点击的目标: 「{target}」\n"
            f"请从上述列表中选出语义最匹配的一个，只回复编号（数字），不要解释。"
            f"如果没有匹配的回复 0。"
        )

        try:
            chat_messages = [
                {"role": "system", "content": "你是一个文字匹配助手。从文字列表中选择与目标最匹配的项，只回复数字编号。"},
                {"role": "user", "content": prompt},
            ]
            raw = self._call_llm(chat_messages, max_retries=0)
            if raw:
                reply = raw["choices"][0]["message"].get("content", "").strip()
                match = re.search(r'\d+', reply)
                if match:
                    idx = int(match.group()) - 1
                    if 0 <= idx < len(text_entries):
                        matched = text_entries[idx]
                        args = dict(args)
                        args["target"] = f"{matched['x']},{matched['y']}"
                        self._emit("log", {
                            "level": "info",
                            "message": f"OCR→LLM选择: 「{target}」→ 「{matched['text']}」({matched['x']},{matched['y']})"
                        })
                        return args
        except Exception as exc:
            logger.debug("LLM OCR selection failed: %s", exc)

        return args

    def _apply_visual_strategy(self, tool_name: str, args: dict, strategy: str) -> dict:
        """根据当前视觉策略修改工具参数。

        策略:
        - "ocr_llm": 方案3, OCR→LLM→点击
        - "cli_path": 方案1, 优先尝试CLI等价操作（在supervisor中处理）
        - 其他: 不修改，走原有SoM/UIA路径
        """
        if strategy == "ocr_llm":
            return self._visual_strategy_ocr_llm(tool_name, args)
        # cli_path 策略在 supervisor 执行时处理（检查是否有CLI等价命令）
        # blind_guess 和 som 走原有路径
        return args

    # ══════════════════════════════════════════════════════════
    #  文本协议模式 — 核心循环
    # ══════════════════════════════════════════════════════════

    def _resolve_text_protocol_config(self, agent_config: Optional[AgentConfig]) -> dict:
        """提取或自动检测文本协议的配置参数。"""
        if agent_config is not None:
            resolved = agent_config.resolve()
            return {
                "multimodal": resolved.get("multimodal", False),
                "vision_mode": resolved.get("vision_mode", "som"),
                "context_minimal": resolved.get("screen_context", "full") == "minimal",
                "use_dedup": resolved.get("image_dedup", True),
                "use_fast": resolved.get("fast_mode", False),
                "on_error": resolved.get("on_error", "retry"),
                "max_steps": resolved.get("max_steps", MAX_STEPS),
                "max_consecutive_failures": resolved.get("max_consecutive_failures", MAX_CONSECUTIVE_FAILURES),
                "min_success_rate": resolved.get("min_success_rate", _MIN_SUCCESS_RATE),
                "som_enabled": resolved.get("som_enabled", True),
                "flow_chart_mode": resolved.get("flow_chart_mode", False),
                "screenshot_quality": resolved.get("screenshot_quality", 60),
                "sliding_window_size": resolved.get("sliding_window_size", 0),
                "verify": resolved.get("verify", True),
                "chat_mode": resolved.get("chat_mode", False),
                "visual_strategy": resolved.get("visual_strategy", "auto"),
                "local_vision": resolved.get("local_vision", False),
            }
        multimodal = is_multimodal_model(self.model)
        return {
            "multimodal": multimodal,
            "vision_mode": "direct" if multimodal else "som",
            "context_minimal": multimodal,
            "use_dedup": multimodal,
            "use_fast": False,
            "on_error": "retry",
            "max_steps": MAX_STEPS,
            "max_consecutive_failures": MAX_CONSECUTIVE_FAILURES,
            "min_success_rate": _MIN_SUCCESS_RATE,
            "som_enabled": True,
            "flow_chart_mode": False,
            "screenshot_quality": 60,
            "sliding_window_size": 0,
            "verify": True,
            "local_vision": False,
        }

    def _append_step_feedback(self, messages: list, result: dict, name: str,
                               success: bool, content: str, cfg: dict,
                               last_b64_hash: str) -> tuple:
        """添加执行结果到消息历史，包含截图/SoM/去重/窗口列表。"""
        from agent.meta_actions import NO_SS_AFTER_META

        messages.append({"role": "assistant", "content": content})
        hint = "\n\n(如果任务目标已达成，请直接回复 总结: 结束，不要继续多余操作)" if success else ""
        safe_result = result
        try:
            safe_result = json.loads(json.dumps(result, ensure_ascii=False))
            def _strip_binary(obj):
                if isinstance(obj, dict):
                    for key in list(obj.keys()):
                        if key.lower() in ("base64", "image_b64") and isinstance(obj.get(key), str):
                            obj[key] = f"<omitted {len(obj[key])} base64 chars>"
                        else:
                            _strip_binary(obj[key])
                elif isinstance(obj, list):
                    for item in obj:
                        _strip_binary(item)
            _strip_binary(safe_result)
        except Exception:
            safe_result = result
        messages.append({
            "role": "user",
            "content": json.dumps(safe_result, ensure_ascii=False)[:1500] + hint,
        })

        multimodal = cfg["multimodal"]
        status_txt = "成功" if success else f"失败: {result.get('error', 'unknown')}"
        skip_screenshot = (
            name in Supervisor.NO_SCREENSHOT_TOOLS
            or (not _USE_FLOW_CHART_MODE and name in NO_SS_AFTER_META)
        )
        if skip_screenshot:
            messages.append({
                "role": "user",
                "content": f"执行{status_txt}。{hint}",
            })
        else:
            sq = cfg.get("screenshot_quality", 30 if multimodal else 60)
            raw_b64 = self.supervisor.capture_b64(quality=sq, max_size=1600 if multimodal else 0)
            cur_hash = hashlib.md5(raw_b64.encode()).hexdigest()[:16]
            if cur_hash == last_b64_hash and cfg["use_dedup"]:
                messages.append({
                    "role": "user",
                    "content": f"执行{status_txt} (截图未变化，沿用上一帧)。{hint}",
                })
            else:
                last_b64_hash = cur_hash
                screen_ctx = self._get_screen_context(minimal=cfg["context_minimal"])
                try:
                    from features.system_features import list_windows
                    win_list = list_windows()
                    win_info = f"\n当前已打开的窗口 ({len(win_list)} 个): {win_list}" if win_list else ""
                except Exception:
                    win_info = ""

                som_label = ""
                b64 = raw_b64
                if self._som_enabled:
                    som_result = self._apply_som(raw_b64, multimodal=multimodal)
                    self.supervisor._last_som_map = som_result["label_map"]
                    if cfg["vision_mode"] != "direct" and som_result["count"] > 0:
                        b64 = som_result["image_b64"]
                        # 紧凑版比详细版更适合 LLM 消费，优先使用
                        som_label = self._compact_som_label(som_result["label_map"])

                if multimodal:
                    parts = [
                        {"type": "text", "text": f"执行{status_txt}。{hint}\n\n当前状态:\n{screen_ctx}{win_info}{som_label}"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ]
                    messages.append({"role": "user", "content": parts})
                else:
                    text = f"执行{status_txt}。{hint}\n\n当前状态:\n{screen_ctx}{win_info}{som_label}"
                    messages.append({"role": "user", "content": text})

        if not success:
            time.sleep(1.0)
        return messages, last_b64_hash

    def run_text_protocol(self, goal: str, _agent_config: Optional[AgentConfig] = None,
                           guidance_queue: Optional[Queue] = None) -> dict:
        """文本协议模式：循环 调用 LLM → 解析 → 监督执行 → 截图。

        guidance_queue: 对话模式，从中读取用户消息注入对话。
            当 chat_mode=True 时，Agent 边执行边聊天，接收实时反馈/新任务。
        """
        self._ensure_registry()
        self._begin_prompt_trace(goal)
        self._guidance_queue = guidance_queue
        cfg = self._resolve_text_protocol_config(_agent_config)
        multimodal = cfg["multimodal"]
        vision_mode = cfg["vision_mode"]
        visual_strategy = cfg.get("visual_strategy", "auto")

        if multimodal:
            self._emit("log", {
                "level": "info",
                "message": f"多模态快速路径: {self.model} 可直接理解截图，跳过 SoM 标注",
            })

        self._emit("agent_start", {"goal": goal, "engine": "text_protocol",
                                    "model": self.model, "multimodal": multimodal})
        start = time.time()
        steps = []
        last_b64_hash = ""

        # ── 系统提示词 + 路径建议 ──
        messages = [{"role": "system", "content": self._system_prompt}]
        try:
            from agent.tool_path_selector import select_best_path
            suggested = select_best_path(goal)
            if suggested is not None:
                path_hint = (
                    f"\n\n[路径建议] 根据目标「{goal[:60]}」, 建议使用 **{suggested.label_cn}**(L{suggested.value}) 路径。"
                    f"\n原因: {suggested.guidance}。成本: {suggested.cost}。"
                    f"\n如执行中遇到困难，可以降级到更低优先级的路径。"
                )
                messages[0] = {"role": "system", "content": self._system_prompt + path_hint}
                self._emit("log", {"level": "info", "message": f"路径建议: {suggested.label_cn} (L{suggested.value})"})
        except Exception:
            pass

        # ── 对话模式: 注入聊天指令到系统提示词 ──
        if cfg.get("chat_mode"):
            self._chat_mode = True
            current_content = messages[0]["content"]
            messages[0] = {"role": "system", "content": current_content + CHAT_MODE_INSTRUCTIONS}
            self._emit("log", {"level": "info", "message": "对话模式已开启 — 可边执行边聊天"})

        # ── 本地视觉引擎提示 ──
        if cfg.get("local_vision", False) and not multimodal:
            som_trust = """
[Local vision guidance]
Screen elements are exposed as compact #N targets with text and coordinates.
Prefer click(target="#N") when the target appears in Visible targets. If only a
generic visual control is available, use its #N or coordinates and verify after
the action. OCR/YOLO labels can be noisy; do not assume success from a tool
return alone. After GUI actions, use the refreshed state to check whether the
screen, focused window, text, URL, file, or process state actually changed.

Do not use app-specific operating recipes. For custom-rendered or UIA-poor
software, combine active-window-limited visual targets, OCR text, process/window
state, keyboard shortcuts, clipboard typing, and screenshot-change verification.
"""
            current_content = messages[0]["content"]
            messages[0] = {"role": "system", "content": current_content + som_trust}
            self._emit("log", {"level": "info", "message": "已启用 SoM 信任模式"})

        # ── 首次截图 + 第 1 条用户消息 ──
        raw_b64 = self.supervisor.capture_b64(quality=30 if multimodal else 60, max_size=1600 if multimodal else 0)
        screen_ctx = self._get_screen_context(minimal=cfg["context_minimal"])

        som_label = ""
        b64 = raw_b64
        if self._som_enabled:
            som_result = self._apply_som(raw_b64, multimodal=multimodal)
            self.supervisor._last_som_map = som_result["label_map"]
            if vision_mode != "direct" and som_result["count"] > 0:
                b64 = som_result["image_b64"]
                # 紧凑版过滤出最高优先级的元素，更适合 LLM 消费
                som_label = self._compact_som_label(som_result["label_map"])

        if self._som_enabled and som_result and som_result.get("label_map"):
            screen_ctx = self._get_screen_context(minimal=cfg["context_minimal"])

        try:
            from features.system_features import list_windows
            win_list = list_windows()
            win_info = f"\n当前已打开的窗口 ({len(win_list)} 个): {win_list}" if win_list else ""
        except Exception:
            win_info = ""

        first_prompt = (
            f"任务目标: {goal}\n\n当前状态:\n{screen_ctx}{win_info}"
            + (som_label if vision_mode != "direct" else "")
            + ("\n\n分析屏幕截图，规划3-5步连续操作，用多个「工具:」块一次输出。\n\n"
               "例如:\n工具: click\n参数:\n  target: 7\n\n工具: click\n参数:\n  target: +\n\n"
               "然后等执行结果截图再做下一步规划。" if vision_mode != "direct"
               else "\n\n先判断是否有直接工具（file_ops/manage_app/run_command 等）可用。有就直接用，不需要操作屏幕。没有再用视觉工具点击操作。截图主要用于验证结果。")
        )
        if multimodal:
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": first_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            })
        else:
            messages.append({
                "role": "user",
                "content": first_prompt,
            })

        consecutive_fails = 0
        consecutive_step_fails = 0
        total_success = 0
        last_tool = ""
        repeat_count = 0
        done_no_tool_count = 0

        self._emit("log", {"level": "info", "message": "正在调用 AI 分析屏幕并规划下一步..."})
        _max_steps = cfg.get("max_steps", MAX_STEPS)
        _max_consecutive_failures = cfg.get("max_consecutive_failures", MAX_CONSECUTIVE_FAILURES)
        _min_success_rate = cfg.get("min_success_rate", _MIN_SUCCESS_RATE)
        _som_enabled = cfg.get("som_enabled", True)
        _slide_window = cfg.get("sliding_window_size", 0)
        self._som_enabled = _som_enabled
        if cfg.get("flow_chart_mode"):
            set_flow_chart_mode(True)

        def _runtime_snapshot(include_screen: bool = True) -> dict:
            """Small state snapshot used around GUI actions in text protocol."""
            snap: dict[str, object] = {}
            try:
                from features.system_features import get_active_window
                aw_data = get_active_window()
                if isinstance(aw_data, dict):
                    snap["active_window"] = {
                        "title": aw_data.get("title", ""),
                        "left": aw_data.get("left"),
                        "top": aw_data.get("top"),
                        "width": aw_data.get("width"),
                        "height": aw_data.get("height"),
                    }
            except Exception:
                pass
            if include_screen:
                try:
                    b64 = self.supervisor.capture_b64(quality=35, max_size=900, update_scale=False)
                    snap["screen_hash"] = hashlib.sha256(b64.encode("utf-8")).hexdigest()[:12]
                except Exception:
                    pass
            return snap

        def _strip_trace_binary(obj):
            if isinstance(obj, dict):
                out = {}
                for key, value in obj.items():
                    if key in ("base64", "image_b64", "b64"):
                        out[key] = f"<omitted {len(str(value))} chars>"
                    else:
                        out[key] = _strip_trace_binary(value)
                return out
            if isinstance(obj, list):
                return [_strip_trace_binary(item) for item in obj]
            return obj

        for loop in range(1, _max_steps + 1):
            if self._stop_event and self._stop_event.is_set():
                self._emit("log", {"level": "info", "message": "停止请求，终止循环"})
                break

            # ── 对话模式: 执行前检查用户消息 ──
            if cfg.get("chat_mode"):
                self._process_chat_messages(messages)

            # ── LLM 调用 ──
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
            reasoning = (msg.get("reasoning_content", "") or
                        msg.get("reasoning", "") or "")
            if reasoning:
                self._emit("log", {"level": "info", "message": f"思考: {reasoning[:300]}"})

            # ── 解析回复 ──
            action, name, args = self._parse_response(content)
            tool_blocks = self._parse_all_tools(content)

            if action == "done":
                if not steps:
                    done_no_tool_count += 1
                    if done_no_tool_count >= 3:
                        self._emit("log", {"level": "error",
                                           "message": "LLM 连续 3 次声称完成但未执行任何工具，终止"})
                        break
                    self._emit("log", {"level": "warn",
                                       "message": "LLM 未执行任何工具就声称完成，强制继续"})
                    messages.append({
                        "role": "user",
                        "content": "你没有执行任何工具调用就声称完成了。这不行。必须调用工具操作屏幕。至少执行一个工具后再总结。"
                    })
                    continue
                summary = args.get("summary", content)[:500]
                self._emit("log", {"level": "info", "message": f"完成: {summary}"})
                ok_count = sum(1 for s in steps if s.get("status") == "completed")
                return {
                    "success": ok_count > 0, "steps": steps,
                    "summary": summary, "total_steps": len(steps),
                    "elapsed_ms": round((time.time() - start) * 1000),
                }

            if action == "error":
                # ── 对话模式: LLM 的纯文本回复当作聊天响应 ──
                chat_mode_on = cfg.get("chat_mode", False)
                if chat_mode_on and len(content) > 20:
                    self._emit_chat_response(content[:500])
                    messages.append({"role": "assistant", "content": content})
                    # 检查是否有新的用户消息
                    self._process_chat_messages(messages)
                    continue

                self._emit("log", {"level": "warn",
                                   "message": f"无法解析: {args.get('raw', content)[:200]}"})
                messages.append({
                    "role": "user",
                    "content": "格式错误。请用:\n思考: ...\n工具: 工具名\n参数:\n  参数1: 值1\n或任务完成时用: 总结: ..."
                })
                continue

            # ── 支持多工具: 一次 LLM 回复中执行所有工具块 ──
            if not tool_blocks:
                tool_blocks = [(name, args)]

            last_success = False
            last_result = {"success": False, "error": "no_execution"}
            last_tool_name = name

            hash_same_count = 0  # Fix 5: 截图变化检测

            for ti, (tool_name, tool_args) in enumerate(tool_blocks):
                # ── 视觉策略预处理（在工具执行前修改参数）──
                if visual_strategy and visual_strategy not in ("auto",):
                    try:
                        tool_args = self._apply_visual_strategy(tool_name, tool_args, visual_strategy)
                    except Exception as e:
                        logger.debug("Visual strategy %s error: %s", visual_strategy, e)

                # ── 执行工具 ──
                sn = len(steps) + 1
                self._emit("log", {"level": "info",
                                   "message": f"调用: {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:200]})"})
                step = {"step_num": sn, "tool": tool_name, "args": tool_args,
                        "thought": reasoning[:300] if reasoning else "",
                        "llm_response": content[:800], "status": "running"}
                self._emit("step_complete", dict(step))

                # SoM 文字匹配: 在执行前将文字 target 转为 SoM 坐标
                if tool_name in ("click",) and tool_args.get("target", "") and self.supervisor._last_som_map:
                    som_target = tool_args["target"].strip().strip("'\"")
                    if not re.match(r'^-?\d+\s*,\s*-?\d+$', som_target):
                        matched = False
                        id_match = re.match(r"^#?(\d+)$", som_target)
                        if id_match:
                            idx = int(id_match.group(1))
                            m = self.supervisor._last_som_map.get(idx)
                            if m:
                                tool_args = dict(tool_args)
                                tool_args["target"] = f"{m['x']},{m['y']}"
                                self._emit("log", {
                                    "level": "info",
                                    "message": f"SoM click: #{idx} -> {m['x']},{m['y']}",
                                })
                                matched = True
                        for idx, m in self.supervisor._last_som_map.items():
                            if matched:
                                break
                            if self._som_text_matches(som_target, m.get("text", "")):
                                tool_args = dict(tool_args)
                                tool_args["target"] = f"{m['x']},{m['y']}"
                                self._emit("log", {
                                    "level": "info",
                                    "message": f"SoM点击: 「{som_target}」→ #{idx}「{m['text']}」→ {m['x']},{m['y']}",
                                })
                                matched = True
                                break

                if tool_name == "click":
                    click_target = str(tool_args.get("target", "") or "").strip().strip("'\"")
                    click_button = str(tool_args.get("button", "left") or "left").strip().lower()
                    if re.match(r'^-?\d+\s*,\s*-?\d+$', click_target) and click_button in ("", "left", "double"):
                        normalized = {"target": click_target}
                        if click_button == "double" or int(tool_args.get("clicks", 1) or 1) >= 2:
                            normalized["double"] = True
                        if not self.supervisor.uia_enabled:
                            normalized["allow_uia"] = False
                        normalized["physical"] = True
                        tool_name = "click_target"
                        tool_args = normalized

                verify_before = None
                verify_after = None
                verify_actions = {
                    "click", "click_target", "right_click", "double_click",
                    "middle_click", "type", "type_text", "type_clipboard",
                    "hotkey", "press_keys", "scroll", "drag",
                }
                if tool_name in verify_actions:
                    verify_before = _runtime_snapshot(include_screen=False)

                step_start = time.time()
                try:
                    self._trace_llm("exec_before", {
                        "step": sn,
                        "tool": tool_name,
                        "args": _strip_trace_binary(tool_args),
                    })
                    if (
                        tool_name == "click_target"
                        and tool_args.get("physical")
                        and re.match(r'^-?\d+\s*,\s*-?\d+$', str(tool_args.get("target", "")).strip())
                    ):
                        from features.action_features import click_target as _direct_click_target
                        ok = _direct_click_target(
                            str(tool_args.get("target", "")),
                            double=bool(tool_args.get("double", False)),
                            allow_uia=bool(tool_args.get("allow_uia", False)),
                            physical=True,
                        )
                        step_result = {"success": bool(ok), "result": bool(ok), "fast_path": "physical_click"}
                    elif not _USE_FLOW_CHART_MODE:
                        from agent.meta_actions import execute as meta_execute, META_ACTIONS
                        if tool_name in META_ACTIONS:
                            step_result = meta_execute(tool_name, tool_args)
                        else:
                            step_result = self.supervisor.execute(tool_name, tool_args)
                    else:
                        step_result = self.supervisor.execute(tool_name, tool_args)
                    step_success = step_result.get("success", False)
                except Exception as e:
                    step_result = {"success": False, "error": str(e)}
                    step_success = False
                finally:
                    try:
                        self._trace_llm("exec_after", {
                            "step": sn,
                            "tool": tool_name,
                            "args": _strip_trace_binary(tool_args),
                            "result": _strip_trace_binary(step_result),
                            "elapsed_ms": round((time.time() - step_start) * 1000, 1),
                        })
                    except Exception:
                        pass

                step["status"] = "completed" if step_success else "failed"
                step["result"] = {
                    "success": step_success,
                    "error": step_result.get("error", ""),
                    "step_ms": round((time.time() - step_start) * 1000),
                }
                if verify_before is not None:
                    verify_after = _runtime_snapshot()
                    before_title = ""
                    after_title = ""
                    if isinstance(verify_before.get("active_window"), dict):
                        before_title = str(verify_before["active_window"].get("title", ""))
                    if isinstance(verify_after.get("active_window"), dict):
                        after_title = str(verify_after["active_window"].get("title", ""))
                    step["result"]["verification"] = {
                        "before": verify_before,
                        "after": verify_after,
                        "screen_changed": bool(
                            verify_before.get("screen_hash")
                            and verify_after.get("screen_hash")
                            and verify_before.get("screen_hash") != verify_after.get("screen_hash")
                        ),
                        "active_window_changed": before_title != after_title,
                    }
                if tool_args:
                    step["args_resolved"] = {k: str(v)[:80] for k, v in tool_args.items()}
                steps.append(step)
                self._emit("step_complete", dict(step))

                # ── Per-step 反思 (best-effort) ──
                if self._reflection_enabled:
                    try:
                        reflection = self._reflection_engine.reflect_step(step, goal)
                        if reflection and reflection.get("insight"):
                            self._step_reflections.append(reflection)
                            self._emit("agent_reflection", {
                                "step_num": sn,
                                "insight": reflection.get("insight", ""),
                                "rule": reflection.get("rule"),
                            })
                    except Exception:
                        pass

                # ── 安全计数器 ──
                if step_success:
                    total_success += 1
                    consecutive_step_fails = 0
                else:
                    consecutive_step_fails += 1

                last_success = step_success
                last_result = step_result
                last_tool_name = tool_name

                if step_success and isinstance(tool_args, dict):
                    action_name_for_focus = str(tool_args.get("action", "") or "").lower()
                    focus_hint = ""
                    if tool_name == "manage_window" and action_name_for_focus in ("focus", "restore", "maximize"):
                        focus_hint = str(
                            tool_args.get("title")
                            or tool_args.get("target")
                            or tool_args.get("window")
                            or tool_args.get("name")
                            or ""
                        )
                    elif tool_name == "focus_window":
                        focus_hint = str(tool_args.get("title") or tool_args.get("target") or "")
                    if focus_hint:
                        self._remember_intended_focus(focus_hint)

                if verify_after is not None:
                    before_title = ""
                    after_title = ""
                    if isinstance(verify_before, dict) and isinstance(verify_before.get("active_window"), dict):
                        before_title = str(verify_before["active_window"].get("title", ""))
                    if isinstance(verify_after, dict) and isinstance(verify_after.get("active_window"), dict):
                        after_title = str(verify_after["active_window"].get("title", ""))
                    self._emit("log", {
                        "level": "info",
                        "message": (
                            f"验证: active_window '{before_title}' -> '{after_title}', "
                            f"screen_changed={step['result'].get('verification', {}).get('screen_changed', False)}"
                        ),
                    })

                if step_success and ti < len(tool_blocks) - 1:
                    action_name = str(tool_args.get("action", "")).lower() if isinstance(tool_args, dict) else ""
                    if (
                        (tool_name == "manage_window" and action_name in ("focus", "restore", "maximize"))
                        or (tool_name == "manage_app" and action_name in ("launch", "open"))
                        or tool_name in ("focus_window", "launch_program")
                    ):
                        self._emit("log", {
                            "level": "info",
                            "message": "窗口上下文已变化，刷新感知后再继续执行后续点击",
                        })
                        break

                # ── 连续失败终止（多工具批处理中立即终止）──
                if consecutive_step_fails >= _max_consecutive_failures:
                    self._emit("log", {
                        "level": "error",
                        "message": f"连续 {_max_consecutive_failures} 步执行失败（最后一步: {tool_name}），终止循环"
                    })
                    messages.append({
                        "role": "user",
                        "content": f"连续 {_max_consecutive_failures} 步执行失败，请总结已完成的步骤并结束。"
                    })
                    raw = self._call_llm(messages)
                    if raw:
                        content = raw["choices"][0]["message"].get("content", "") or ""
                        if "总结:" in content:
                            summary = content.split("总结:", 1)[-1].strip()[:500]
                            steps[-1]["early_exit_reason"] = f"consecutive_failures_{_max_consecutive_failures}"
                    break

                # ── 低成功率终止（多工具批处理中立即终止）──
                if len(steps) >= 8 and total_success / len(steps) < _min_success_rate:
                    self._emit("log", {
                        "level": "error",
                        "message": f"成功率过低 ({total_success}/{len(steps)})，终止循环"
                    })
                    break

            # 如果多工具批处理中被中断，跳出外层循环
            if consecutive_step_fails >= _max_consecutive_failures or \
               (len(steps) >= 8 and total_success / len(steps) < _min_success_rate):
                break

            # ── 执行后反馈（消息历史 + 截图/SoM）──
            old_hash = last_b64_hash
            messages, last_b64_hash = self._append_step_feedback(
                messages, last_result, last_tool_name, last_success, content, cfg, last_b64_hash,
            )

            # ── Fix 5: 截图哈希变化检测（防幻觉）──
            if old_hash and last_b64_hash and old_hash == last_b64_hash and last_success:
                hash_same_count += 1
                if hash_same_count >= 3:
                    self._emit("log", {"level": "warn",
                                       "message": "连续 3 步截图未变化，操作可能未生效。强制 LLM 更换策略。"})
                    messages.append({
                        "role": "user",
                        "content": "[系统] 警告：连续 3 步截图未发生变化，之前的操作可能未生效。请更换策略，不要重复相同操作。如果点不中目标，尝试用坐标点击或用 run_python_script 编写 Python 脚本来完成任务。"
                    })
                    hash_same_count = 0
            else:
                hash_same_count = 0

            # ── 重复工具检测（基于第一批次的首个工具）──
            if tool_blocks:
                first_tool = tool_blocks[0][0]
                if first_tool == last_tool:
                    repeat_count += 1
                else:
                    repeat_count = 0
                    last_tool = first_tool
            if repeat_count >= 3:
                total_repeats = repeat_count + 1
                self._emit("log", {"level": "warn",
                                   "message": f"工具 '{name}' 重复 {total_repeats} 次，强制换策略"})
                repeat_count = 0
                messages.append({
                    "role": "user",
                    "content": f"你已连续 {total_repeats} 次使用 {name}，未取得进展。请换完全不同的策略，不要重复同一工具。"
                })

            # ── 滑动窗口 ──
            if _slide_window > 0:
                max_hist = _slide_window
            elif cfg["use_fast"]:
                max_hist = 4
            else:
                max_hist = 8 if multimodal else 12
            if len(messages) > max_hist:
                messages = [messages[0]] + messages[-(max_hist - 1):]

        elapsed = time.time() - start
        ok_count = sum(1 for s in steps if s.get("status") == "completed")
        return {
            "success": ok_count > 0, "steps": steps,
            "summary": f"执行 {len(steps)} 步 ({ok_count} 成功)",
            "total_steps": len(steps), "elapsed_ms": round(elapsed * 1000),
        }

    # ══════════════════════════════════════════════════════════
    #  (已移除: LangGraph / Planned / ComputerUse 旧路由)
    #  新路径统一通过 _unified_run() → execution_mode 分发
    # ══════════════════════════════════════════════════════════

    # ══════════════════════════════════════════════════════════
    #  _unified_run — 统一执行入口
    # ══════════════════════════════════════════════════════════

    def _unified_run(self, goal: str, config: AgentConfig,
                     steps: Optional[list] = None) -> dict:
        """统一执行入口。根据 AgentConfig.execution_mode 直接分发。"""
        execution_mode = config.execution_mode

        # ── mode-based dispatch ──
        resolved = config.resolve()

        # 传播监督层配置
        self.supervisor.on_error = resolved.get("on_error", "retry")
        self.supervisor.window_aware_ocr = resolved.get("window_aware_ocr", False)
        self.supervisor.uia_pre_focus = resolved.get("uia_pre_focus", False)
        self.supervisor.uia_enabled = resolved.get("uia_enabled", True)
        from interaction.actions import set_uia_enabled as _set_uia
        _set_uia(resolved.get("uia_enabled", True))
        self.supervisor.scripted_mode = resolved.get("scripted_mode", False)
        self.supervisor.verify = resolved.get("verify", True)

        # 同步 LLM 参数
        self._temperature = resolved.get("temperature", 0.1)
        self._max_tokens = resolved.get("max_tokens", 4096)
        self._llm_retries = resolved.get("llm_retries", 2)
        self._screenshot_quality = resolved.get("screenshot_quality", 60)
        self._som_enabled = resolved.get("som_enabled", True)
        self._local_vision_enabled = resolved.get("local_vision", False)
        self.sliding_window_limit = resolved.get("sliding_window_size", 0) or 12

        # 记忆系统
        self._reflection_enabled = resolved.get("memory_enabled", False)

        # 脚本模式注入
        self._system_prompt += SCRIPTED_MODE_INSTRUCTIONS

        # ── 历史记忆注入 (统一入口) ──
        self._inject_memory_prompt(goal)

        # Emit memory stats for frontend display (pre-run)
        try:
            self._emit("memory_snapshot", self._memory_store.stats())
        except Exception:
            pass

        self._emit("log", {"level": "info", "message":
            f"Unified agent: mode={execution_mode}, "
            f"memory={resolved.get('memory_enabled')}, "
            f"supervise={resolved.get('supervise', True)}, "
            f"uia={resolved.get('uia_enabled')}, "
            f"som={resolved.get('som_enabled')}, "
            f"win_ocr={resolved.get('window_aware_ocr')}, "
            f"visual={resolved.get('visual_strategy')}, "
            f"max_steps={resolved.get('max_steps')}"})

        # ── Dispatch ──
        if execution_mode == "flow_chart":
            return self._run_direct(goal)

        elif execution_mode == "multimodal":
            return self.run_text_protocol(goal, _agent_config=config,
                                           guidance_queue=self._guidance_queue)

        elif execution_mode == "text":
            return self.run_text_protocol(goal, _agent_config=config,
                                           guidance_queue=self._guidance_queue)

        else:  # offline
            return {"success": True, "steps": [], "summary": "离线模式 (预留)", "total_steps": 0}

    # ══════════════════════════════════════════════════════════
    #  统一入口
    # ══════════════════════════════════════════════════════════

    def run(self, goal: str, **kwargs) -> dict:
        """执行目标。

        参数:
            goal: 自然语言目标描述
            agent_config: AgentConfig 实例

        返回:
            dict{success, steps, summary, total_steps, elapsed_ms}
        """
        if self._stop_event and self._stop_event.is_set():
            return {"success": False, "steps": [], "total_steps": 0,
                    "error": "stopped before start", "elapsed_ms": 0}

        agent_config = kwargs.get("agent_config")
        result = self._unified_run(goal, agent_config, steps=kwargs.get("steps"))
        exec_mode = agent_config.execution_mode

        # ── Post-task reflection + memory update ──
        rules_count = 0
        prefs_count = 0
        if self._reflection_enabled:
            try:
                steps_data = result.get("steps", [])
                if steps_data:
                    task_reflection = self._reflection_engine.reflect_task(
                        goal=goal, steps=steps_data,
                        success=result.get("success", False),
                        elapsed_ms=result.get("elapsed_ms", 0),
                    )
                    if task_reflection and (task_reflection.get("rules") or task_reflection.get("preferences")):
                        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
                        rules_count = len(task_reflection.get("rules", []))
                        prefs_count = len(task_reflection.get("preferences", []))

                        for rule_data in task_reflection.get("rules", []):
                            self._memory_store.add_rule(
                                rule_text=rule_data["rule"],
                                trigger_context=rule_data.get("trigger", "general"),
                                confidence=rule_data.get("confidence", 0.5),
                                category=rule_data.get("category", "approach"),
                                source_run_id=run_id,
                            )

                        for pref in task_reflection.get("preferences", []):
                            self._memory_store.add_preference(
                                key=pref["key"],
                                value=pref["value"],
                                confidence=pref.get("confidence", 0.5),
                                source_run_id=run_id,
                            )

                        from agent.memory import Episode
                        self._memory_store.add_episode(Episode(
                            run_id=run_id,
                            goal=goal[:200],
                            success=result.get("success", False),
                            total_steps=result.get("total_steps", 0),
                            summary=task_reflection.get("overall_assessment", "")[:300],
                            insights=task_reflection.get("insights", [])[:5],
                            mistakes=task_reflection.get("mistakes", [])[:5],
                            tools_used=list(set(
                                s.get("tool", "") for s in steps_data
                            )),
                            elapsed_ms=result.get("elapsed_ms", 0),
                        ))

                        self._emit("agent_learned", {
                            "rules_count": rules_count,
                            "prefs_count": prefs_count,
                            "insights": task_reflection.get("insights", [])[:3],
                            "mistakes": task_reflection.get("mistakes", [])[:3],
                        })
            except Exception as e:
                logger.warning("Post-task reflection failed: %s", e)
            finally:
                self._step_reflections.clear()

        # Persist run summary (after reflection so we capture rules/prefs counts)
        try:
            save_run(RunSummary(
                goal=goal[:200],
                mode=exec_mode,
                model=self.model,
                success=result.get("success", False),
                total_steps=result.get("total_steps", 0),
                elapsed_ms=result.get("elapsed_ms", 0),
                timestamp=datetime.now(timezone.utc).isoformat(),
                summary=(result.get("summary") or "")[:500],
                error=result.get("error", ""),
                rules_extracted=rules_count,
                preferences_updated=prefs_count,
            ))
        except Exception as e:
            logger.warning("Failed to persist run summary: %s", e)

        # Emit memory snapshot for frontend
        try:
            self._emit("memory_snapshot", self._memory_store.stats())
        except Exception:
            pass

        return result

    def _run_direct(self, goal: str) -> dict:
        """直接模式：仅调用 generate_flow_script 生成流程图。"""
        self._ensure_registry()
        self._emit("agent_start", {"goal": goal, "engine": "direct"})

        # 导出环境变量供下游模块使用
        self._export_env_vars()

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
            return {"success": success, "steps": [{
                    "step_num": 1, "tool": "generate_flow_script",
                    "args": {"goal": goal},
                    "status": "completed" if success else "failed",
                    "result": result.get("result", result),
                }], "total_steps": 1,
                    "summary": "Flow script generated" if success else "Generation failed"}
        except Exception as exc:
            self._emit("error", {"message": f"generate_flow_script failed: {exc}"})
            self._emit("agent_done", {"status": "failed", "total_steps": 0, "fail_reason": str(exc)})
            return {"success": False, "steps": [], "total_steps": 0, "error": str(exc)}
