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
import threading
import time
from datetime import datetime, timezone
from queue import Queue, Empty
from typing import Any, Callable, Optional

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
_MULTIMODAL_MODELS = frozenset({
    "mimo-v2-omni", "mimo-omni", "mimo-v2", "mimo-pro",
    "gpt-4o", "gpt-4o-mini", "gpt-4v", "gpt-4-vision",
    "claude-3-opus", "claude-3-sonnet", "claude-3-haiku",
    "claude-3.5-sonnet", "claude-3.5-haiku",
    "claude-4", "claude-opus-4", "claude-sonnet-4", "claude-haiku-4",
    "gemini-pro-vision", "gemini-1.5-pro", "gemini-1.5-flash",
    "qwen-vl", "qwen2-vl", "glm-4v", "glm-4v-plus",
    "deepseek-vl2", "deepseek-vl",
})

_MULTIMODAL_PREFIXES = ("mimo-", "gpt-4", "claude-3", "claude-4", "claude-opus", "claude-sonnet", "claude-haiku", "gemini-", "qwen-vl", "qwen2-vl", "glm-4v", "deepseek-vl")


def _is_multimodal_model(model_name: str) -> bool:
    """检测模型是否支持图像理解（多模态视觉能力）。

    多模态模型可以直接从截图中读取按钮文字、识别界面元素，
    不需要 SoM 标号或 OCR 文本注入。
    """
    name = model_name.strip().lower()
    if name in _MULTIMODAL_MODELS:
        return True
    for prefix in _MULTIMODAL_PREFIXES:
        if name.startswith(prefix):
            return True
    # DeepSeek 纯文本模型排除
    if "deepseek" in name and not any(vl in name for vl in ("vl", "vision")):
        return False
    # 包含 vision/multimodal 关键词
    if "vision" in name or "multimodal" in name or "omni" in name:
        return True
    return False


# ── 文本协议模式系统提示词 ─────────────────────────────────────

SYSTEM_PROMPT_TEXT = """你是桌面自动化 AI。你有 {tool_count} 个动作可用，分直接路径和视觉路径。

选择规则：看用户意图——要的是「结果」还是「操作程序」？

### 直接路径（要结果时使用 — 不操作屏幕，直接返回结果）
- file_ops — 文件读写（创建/写入/读取/删除）
- manage_app — 启动/关闭/检测应用程序
- run_command — 执行命令/脚本
- system_info — 获取系统信息
- notify — 发送通知
- network — 管理网络连接
- control_audio — 音量控制

**何时用：** 用户只关心结果。"保存文件"→ file_ops，"启动计算器"→ manage_app，"查电量"→ system_info。

### 视觉路径（要操作程序时使用 — 启动程序→界面操作→验证）
- screenshot — 截图验证结果/检查进度（每轮自动附截图，不需手动调用）
- click — 点击按钮或坐标
- type_text — 在已聚焦输入框输入文字（先用 click 聚焦）
- press_keys — 键盘快捷键
- read_ui — 读取窗口控件树
- read_text — OCR 识别文字
- manage_window — 窗口管理（聚焦/移动/缩放）
- browser_do — 浏览器自动化

**何时用：** 用户说要操作某个程序。"打开记事本输入 Hello"→ launch → click → type_text。
不要用 file_ops 替代打开记事本操作——用户说的是操作程序，不是要文件结果。

### 判断方法

| 用户任务 | 意图 | 该怎么做 |
|---------|------|---------|
| "创建 test.txt 内容 Hello" | 要结果 | file_ops 直接写文件 |
| "打开记事本输入 Hello" | 操作程序 | manage_app 启动 → click 聚焦 → type_text |
| "计算 7+3" | 要结果 | 打开计算器 → click 按钮 |
| "保存文件到桌面" | 要结果 | file_ops 直接写 |
| "在记事本里保存文件" | 操作程序 | 启动记事本 → type_text → click 菜单保存 |
| "查一下电池电量" | 要结果 | system_info |

**重要：当用户说「打开/启动某个程序」时，必须用 manage_app 来启动该程序，不能用 file_ops 替代。**
**"打开记事本"=启动应用（操作程序），不是创建文件（要结果）。**

### 执行流程

1. **判断意图**：用户要结果还是操作程序？
2. **要结果** → 用直接路径工具一步完成
3. **操作程序** → 启动程序 → 界面操作（click/type_text）→ 截图验证
4. **每步验证**：截图确认操作结果正确

### 关键规则

**工具执行结果必须如实反映。** 如果工具返回错误（拒绝访问、路径不存在等），必须在总结中如实报告失败原因，绝不能声称成功。错误信息示例：「file_ops 返回错误：[WinError 5] 拒绝访问」。如果遇到错误，尝试更换路径或方法后重试。

**文件路径必须使用正确的用户目录。** 不要硬编码用户名。桌面路径固定为 `C:\\Users\\zou\\Desktop\\`。

**点击失败时换用坐标。** 如果 `click(target="按钮名")` 第一次失败，换用 `click(x=数字, y=数字)` 坐标点击。坐标可以从截图中的按钮位置估算。

**完成任务后必须验证。** 创建文件后检查文件是否存在，计算后检查结果是否正确。

必须严格按照以下格式输出，不要省略：

思考: <推理过程>
工具: <动作名>
参数:
  参数1: 值1

任务完全完成后，输出:

总结: <结果描述>

动作列表（完整详情）:
{tools_text}
"""

SCRIPTED_MODE_INSTRUCTIONS = """
[脚本执行模式已开启]

你也可以编写 Python 脚本来完成桌面操作，使用 `run_python_script(script="...")` 工具。

什么时候用脚本：
- 需要启动程序 → 等待 → 操作 → 验证 的完整流程
- 需要条件判断或循环的重复操作
- 简单的点击/输入不适合的场景

脚本注意事项：
- 系统已预装 pywinauto, pyautogui, psutil, time, os, subprocess 等常见库，直接 import 即可
- 不需要 pip install，系统会自动处理依赖
- 每个脚本建议控制在 50 行以内
- 脚本中适当加 time.sleep() 等待界面响应
- 每个脚本执行完成后用 print() 输出结果报告

示例: 获取活动窗口信息并截图保存
```
工具: run_python_script
参数:
  script: |
    import pywinauto, time, os
    desktop = pywinauto.Desktop(backend="uia")
    active_title = desktop.window_text()
    wins = desktop.windows()
    logs_dir = os.path.join(os.environ["USERPROFILE"], "Desktop")
    with open(os.path.join(logs_dir, "window_report.txt"), "w") as f:
        f.write(f"活动窗口: {active_title}
")
        f.write(f"总窗口数: {len(wins)}
")
    print(f"已保存窗口报告: 活动={active_title}, 共{len(wins)}个窗口")
```

简单操作仍可用 native 工具（click_target, type_text 等），复杂流程用脚本。
"""

CHAT_MODE_INSTRUCTIONS = """
[对话模式已开启]

你可以边执行任务边与用户对话。当用户发来消息时：
- 如果是询问状态 → 简洁回答当前进展
- 如果是指引反馈 → 调整执行策略后继续
- 如果是新任务 → 完成当前步骤后执行新任务

对话模式下，你可以用自然语言回复用户，不需要每次都用工具。
用户消息在「执行结果」之后出现。按需处理即可。

回复格式:
- 需要执行操作时 → 照常用「工具: 工具名」格式
- 只是想聊天 → 直接回复文字（不带工具: 前缀）
- 想展示截图 → 用 screenshot() 工具
"""

# ── 工具描述生成 (from MimoAgent) ─────────────────────────────

_USE_FLOW_CHART_MODE = False  # Set True to use full tool prompt + flow chart
_FLOW_CHART_MODE_LOCK = threading.Lock()  # Thread safety for mode switching

def set_flow_chart_mode(enabled: bool = True):
    """Enable/disable flow chart mode (full tools + generate_flow/execute_flow)."""
    global _USE_FLOW_CHART_MODE
    with _FLOW_CHART_MODE_LOCK:
        _USE_FLOW_CHART_MODE = enabled
    from agent.meta_actions import set_flow_chart_mode as _set_meta_fc
    _set_meta_fc(enabled)

def _build_feature_tools_compact() -> str:
    """构建 @feature 工具紧凑列表（一行一个，组织分类）。

    供非流程图模式的 text_protocol 使用，让 LLM 知道所有可用工具。
    """
    from features.registry import registry, FeatureCategory
    import features._all  # noqa: F401

    cat_labels = {
        FeatureCategory.ACTION: "交互动作",
        FeatureCategory.PERCEPTION: "感知识别",
        FeatureCategory.CONTROL: "控制流",
        FeatureCategory.AI: "AI 规划",
        FeatureCategory.MACRO: "宏指令",
        FeatureCategory.SYSTEM: "系统功能",
        FeatureCategory.DEBUG: "调试诊断",
    }

    grouped: dict = {}
    for spec in registry.all():
        if spec.deprecated or spec.hidden:
            continue
        cat = spec.category
        grouped.setdefault(cat, []).append(spec)

    lines = [
        "\n\n## @feature 高级工具（按分类）",
        "",
        "除上述元动作外，还有以下底层工具。元动作不够用时按需使用：",
        "同样遵循直接路径优先原则 — 能直接用工具完成就不要操作屏幕。",
        "",
    ]
    for cat in FeatureCategory:
        specs = grouped.get(cat, [])
        if not specs:
            continue
        label = cat_labels.get(cat, cat.value)
        lines.append(f"### {label}（{len(specs)} 个）")
        for s in sorted(specs, key=lambda x: x.name):
            desc = (s.description or "")[:120].replace("\n", " ")
            lines.append(f"  - **{s.name}** — {desc}")
        lines.append("")

    lines.append("使用方式与元动作相同：在「工具:」行写工具名，参数写在参数行。")
    lines.append("元动作和 @feature 工具可混合使用。")

    return "\n".join(lines)


def _build_tools_text() -> str:
    """生成工具描述文本。

    非流程图模式: 元动作 + @feature 工具紧凑列表，LLM 知道所有可用工具。
    Flow chart mode: 按访问模式分组的完整工具列表。
    """
    if _USE_FLOW_CHART_MODE:
        from agent.tool_path_selector import build_path_prompt
        return build_path_prompt()
    from agent.meta_actions import get_prompt
    meta = get_prompt(use_flow_chart=_USE_FLOW_CHART_MODE)
    tools = _build_feature_tools_compact()
    return meta + tools


# ====================================================================
#  LingdongAgent — 统一 Agent
# ====================================================================

from agent.supervisor import Supervisor
from agent.state_persistence import RunSummary, save_run, load_history
from agent.config import AgentConfig, agent_config_from_legacy

class LingdongAgent:
    """统一的桌面自动化 AI Agent。

    属性:
        api_key / api_base / model: LLM 配置
        multimodal: 是否启用多模态视觉定位 (VLM 兜底)
        auto_learn: 是否自动学习 (截图→经验库)
        event_callback: IPC 事件回调
        stop_event: 停止信号
    """

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
        all_count = len([s for s in registry.all() if not s.deprecated and not s.hidden])
        self._tools_text = _build_tools_text()

        if _USE_FLOW_CHART_MODE:
            self._system_prompt = SYSTEM_PROMPT_TEXT.format(
                tool_count=all_count, tools_text=self._tools_text
            )
        else:
            from agent.meta_actions import META_ACTIONS
            meta_count = len(META_ACTIONS)
            self._system_prompt = SYSTEM_PROMPT_TEXT.format(
                tool_count=meta_count, tools_text=self._tools_text
            )
        logger.info("Registry: %d tools total, prompt: %d chars", all_count, len(self._system_prompt))

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

        elements.sort(key=lambda e: (e["y"] // 25, e["x"]))

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
                return data
            except _req.exceptions.Timeout:
                last_err = "API timeout (120s)"
                self._emit("log", {"level": "warn", "message": f"API timeout, retry {attempt+1}/{retries}"})
            except _req.exceptions.RequestException as e:
                last_err = str(e)
                self._emit("log", {"level": "warn", "message": f"API error: {str(e)[:100]}, retry {attempt+1}/{retries}"})
                delay = 0.5 if "ConnectionReset" in str(e) or "Connection aborted" in str(e) else 2 * (attempt + 1)
                time.sleep(delay)
        self._emit("log", {"level": "error", "message": f"LLM API failed after {retries} retries: {last_err}"})
        return None

    @staticmethod
    def _parse_response(content: str) -> tuple:
        """解析文本协议回复，返回 (action, name, args)。

        action: 'tool' | 'done' | 'error'
        """
        if not content:
            return "error", "", {}

        if "总结:" in content and "工具:" not in content:
            return "done", "", {"summary": content}

        tool_match = re.search(r"工具:\s*(\S+)", content)
        if not tool_match:
            return "error", "", {"raw": content[:200]}

        tool_name = tool_match.group(1).strip()
        args = {}
        param_section = re.search(r"参数:[ \t]*(.*?)(?:\n\s*\n|\n\s*总结:|$)", content, re.DOTALL)
        if param_section:
            lines = param_section.group(1).split("\n")
            i = 0
            while i < len(lines):
                raw_line = lines[i]
                m = re.match(r"(\w+)\s*[:：]\s*(.+)", raw_line.strip())
                if m:
                    key = m.group(1)
                    val = m.group(2).strip().strip("'\"")
                    # 支持 YAML 风格 | 多行值
                    if val == "|":
                        indent = len(raw_line) - len(raw_line.lstrip())
                        val_lines = []
                        i += 1
                        while i < len(lines):
                            if lines[i].strip() == "":
                                i += 1
                                continue
                            cur_indent = len(lines[i]) - len(lines[i].lstrip())
                            if cur_indent > indent:
                                val_lines.append(lines[i].rstrip())
                                i += 1
                            else:
                                break
                        args[key] = "\n".join(val_lines)
                        continue
                    else:
                        args[key] = val
                i += 1

        return "tool", tool_name, args

    @staticmethod
    def _parse_params_section(text: str) -> dict:
        """解析 参数: 块内容为字典。"""
        args = {}
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            raw_line = lines[i]
            m = re.match(r"(\w+)\s*[:：]\s*(.+)", raw_line.strip())
            if m:
                key = m.group(1)
                val = m.group(2).strip().strip("'\"")
                if val == "|":
                    indent = len(raw_line) - len(raw_line.lstrip())
                    val_lines = []
                    i += 1
                    while i < len(lines):
                        if lines[i].strip() == "":
                            i += 1
                            continue
                        cur_indent = len(lines[i]) - len(lines[i].lstrip())
                        if cur_indent > indent:
                            val_lines.append(lines[i].rstrip())
                            i += 1
                        else:
                            break
                    args[key] = "\n".join(val_lines)
                    continue
                else:
                    args[key] = val
            i += 1
        return args

    @staticmethod
    def _parse_all_tools(content: str) -> list[tuple[str, dict]]:
        """解析文本协议回复中所有 工具: 块，返回 [(name, args), ...] 列表。

        支持一次 LLM 回复包含多个工具调用。
        """
        if not content:
            return []
        if "总结:" in content and "工具:" not in content:
            return []

        results = []
        blocks = re.split(r'\n(?=工具:\s*\S+)', content)
        for block in blocks:
            if not block.strip():
                continue
            tool_match = re.search(r"工具:\s*(\S+)", block)
            if not tool_match:
                continue
            tool_name = tool_match.group(1).strip()
            args = {}
            param_section = re.search(r"参数:[ \t]*(.*?)(?:\n\s*\n|\n\s*总结:|$)", block, re.DOTALL)
            if param_section:
                args = LingdongAgent._parse_params_section(param_section.group(1))
            results.append((tool_name, args))
        return results

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

        # 1. OCR 获取屏幕上所有文字
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
            return {
                "multimodal": agent_config._multimodal,
                "vision_mode": agent_config._resolved_vision_mode,
                "context_minimal": agent_config._resolved_screen_context == "minimal",
                "use_dedup": agent_config.image_dedup,
                "use_fast": agent_config.fast_mode,
                "on_error": agent_config._resolved_on_error,
                "max_steps": agent_config.max_steps,
                "max_consecutive_failures": agent_config.max_consecutive_failures,
                "min_success_rate": agent_config.min_success_rate,
                "som_enabled": agent_config.som_enabled,
                "flow_chart_mode": agent_config.flow_chart_mode,
                "screenshot_quality": agent_config.screenshot_quality,
                "sliding_window_size": agent_config.sliding_window_size,
                "verify": agent_config.verify,
                "chat_mode": agent_config.chat_mode,
                "visual_strategy": agent_config.visual_strategy,
            }
        multimodal = _is_multimodal_model(self.model)
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
        }

    def _append_step_feedback(self, messages: list, result: dict, name: str,
                               success: bool, content: str, cfg: dict,
                               last_b64_hash: str) -> tuple:
        """添加执行结果到消息历史，包含截图/SoM/去重/窗口列表。"""
        from agent.meta_actions import NO_SS_AFTER_META

        messages.append({"role": "assistant", "content": content})
        hint = "\n\n(如果任务目标已达成，请直接回复 总结: 结束，不要继续多余操作)" if success else ""
        messages.append({
            "role": "user",
            "content": json.dumps(result, ensure_ascii=False)[:1500] + hint,
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
            sq = cfg.get("screenshot_quality", 35 if multimodal else 60)
            raw_b64 = self.supervisor.capture_b64(quality=sq, max_size=1920)
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
                        lines = "\n".join(f"  #{i}: 「{e['text']}」" for i, e in som_result["label_map"].items())
                        som_label = f"\n屏幕元素编号:\n{lines}"

                parts = [
                    {"type": "text", "text": f"执行{status_txt}。{hint}\n\n当前状态:\n{screen_ctx}{win_info}{som_label}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]
                messages.append({"role": "user", "content": parts})

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

        # ── 首次截图 + 第 1 条用户消息 ──
        raw_b64 = self.supervisor.capture_b64(quality=35 if multimodal else 60, max_size=1920)
        screen_ctx = self._get_screen_context(minimal=cfg["context_minimal"])

        som_label = ""
        b64 = raw_b64
        if self._som_enabled:
            som_result = self._apply_som(raw_b64, multimodal=multimodal)
            self.supervisor._last_som_map = som_result["label_map"]
            if vision_mode != "direct" and som_result["count"] > 0:
                b64 = som_result["image_b64"]
                lines = "\n".join(f"  #{i}: 「{e['text']}」" for i, e in som_result["label_map"].items())
                som_label = (
                    f"\n屏幕元素编号（元素上有蓝色框和编号）:\n{lines}\n"
                    f"\n使用 #N 指定目标，如: target: #3  或  target: #5"
                )

        try:
            from features.system_features import list_windows
            win_list = list_windows()
            win_info = f"\n当前已打开的窗口 ({len(win_list)} 个): {win_list}" if win_list else ""
        except Exception:
            win_info = ""

        first_prompt = (
            f"任务目标: {goal}\n\n当前状态:\n{screen_ctx}{win_info}"
            + (som_label if vision_mode != "direct" else "")
            + ("\n\n分析屏幕截图，规划2-3步连续操作，用多个「工具:」块一次输出。\n\n"
               "例如:\n工具: click\n参数:\n  target: 7\n\n工具: click\n参数:\n  target: +\n\n"
               "然后等执行结果截图再做下一步规划。" if vision_mode != "direct"
               else "\n\n先判断是否有直接工具（file_ops/manage_app/run_command 等）可用。有就直接用，不需要操作屏幕。没有再用视觉工具点击操作。截图主要用于验证结果。")
        )
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": first_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
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

                step_start = time.time()
                try:
                    if not _USE_FLOW_CHART_MODE:
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

                step["status"] = "completed" if step_success else "failed"
                step["result"] = {
                    "success": step_success,
                    "error": step_result.get("error", ""),
                    "step_ms": round((time.time() - step_start) * 1000),
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

    _langgraph_tools_cache: Optional[list] = None

    def _load_langgraph_tools(self) -> list:
        """从 @feature 注册表加载所有可用工具（类级缓存，首次后复用）。"""
        if self._langgraph_tools_cache is not None:
            return self._langgraph_tools_cache

        from features.registry import registry
        import features._all  # noqa (~8s 首次导入)

        tools = []
        for spec in registry.all():
            if spec.deprecated or spec.hidden:
                continue
            tools.append(self._make_tool_func(spec))
        LingdongAgent._langgraph_tools_cache = tools
        logger.info("Loaded %d LangGraph tools from registry (cached)", len(tools))
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

        委托给 _supervised_execute，消除监督逻辑重复。
        LangGraph 的 ToolNode 要求返回 JSON 字符串。
        """
        def wrapper(call_req, execute):
            tool_call = call_req.tool_call
            name = tool_call.get("name", "")
            args = tool_call.get("args", {})
            result = self.supervisor.execute(name, args)
            return json.dumps(result, ensure_ascii=False, default=str)

        return wrapper

    def run_langgraph(self, goal: str, config: Optional[dict] = None,
                       _agent_config: Optional[AgentConfig] = None) -> dict:
        """LangGraph 模式：create_react_agent 驱动执行。

        适用于 DeepSeek/GPT/Claude 等支持函数调用的 LLM。

        参数:
            _agent_config: 统一配置（由 _unified_run 传入），None 时保持旧行为
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

        # 用与文本协议相同的路径选择系统提示词
        path_prompt = self._tools_text if self._tools_text else ""
        path_hint = ""
        try:
            from agent.tool_path_selector import select_best_path, AccessPattern
            suggested = select_best_path(goal)
            if suggested is not None:
                path_hint = f"\n建议路径: {suggested.label_cn}(L{suggested.value}) — {suggested.guidance}"
        except Exception:
            pass

        system_prompt = f"""你是一个桌面自动化助手。可用工具已全部注册。

## 工具选择策略

{path_prompt}{path_hint}

## 执行规则
1. 先判断工具访问模式（DIRECT < BROWSER < UIA < OCR < SoM），从对应分组选工具
2. 浏览器操作必须用 browser_* 工具，不走其他路径
3. 对于多步骤工作流，先用 generate_flow_script 生成流程，然后按步骤执行
4. 遇到失败时按路径降级重试（L0→L1→L2→...）
5. 工具调用完成后无需额外标记，直接结束即可
6. 所有坐标默认为屏幕绝对坐标
7. 浏览器通过 CDP 自动连接，首次 browser_navigate 等会自动启动 Chrome"""

        graph = create_react_agent(model=llm, tools=tool_node, prompt=system_prompt)

        self._emit("agent_start", {"goal": goal, "engine": "langgraph", "model": self.model})

        max_steps_lg = _agent_config.max_steps if _agent_config else MAX_STEPS
        thread_config = {"recursion_limit": max_steps_lg}
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
    #  Planned 模式 (Planner + PlanExecutor)
    # ══════════════════════════════════════════════════════════

    def run_planned(self, goal: str, steps: Optional[list] = None,
                     _agent_config: Optional[AgentConfig] = None) -> dict:
        """Planned mode: Planner → Plan DAG → PlanExecutor.

        使用 Planner 将目标分解为 Plan DAG（带 fallback 链），
        然后通过 PlanExecutor 拓扑序执行，支持自动重试 + 路径降级。

        参数:
            goal: 自然语言目标
            steps: 可选预生成步骤，为 None 时自动调用 generate_flow_script
            _agent_config: 统一配置（由 _unified_run 传入），None 时保持旧行为

        返回:
            dict{success, plan, steps, total_steps, summary, elapsed_ms}
        """
        self._ensure_registry()
        self._emit("agent_start", {"goal": goal, "engine": "planned", "model": self.model})
        start = time.time()

        from agent.planner import Planner, PlanExecutor as PlanExec

        planner = Planner(
            api_key=self.api_key,
            api_base=self.api_base,
            model=self.model,
        )

        # ── 生成 Plan DAG ──
        if steps:
            plan = planner.plan_from_steps(goal, steps)
            self._emit("log", {"level": "info",
                               "message": f"Plan from {len(steps)} pre-generated steps ({len(plan.nodes)} nodes)"})
        else:
            plan = planner.plan(goal)
            self._emit("log", {"level": "info",
                               "message": f"Plan generated: {len(plan.nodes)} nodes"})

        if not plan.nodes:
            self._emit("log", {"level": "warn", "message": "Empty plan, falling back to text_protocol"})
            self._inject_memory_prompt(goal)
            return self.run_text_protocol(goal)

        # ── 执行 Plan DAG ──
        executor = PlanExec(
            agent=self,
            plan=plan,
            emit_event=lambda e, d: self._emit(e, d),
            stop_event=self._stop_event,
            log_func=lambda lv, msg: self._emit("log", {"level": lv, "message": msg}),
        )

        exec_result = executor.execute()

        elapsed = time.time() - start
        summary = plan.summary()

        final_status = "completed" if exec_result.get("success") else "failed"
        self._emit("agent_done", {
            "status": final_status,
            "total_steps": summary["total"],
            "summary": f"Plan executed: {summary.get('completed', 0)}/{summary['total']} steps ok",
        })

        return {
            "success": exec_result.get("success", False),
            "plan": plan.to_dict(),
            "steps": [n.to_dict() for n in plan.nodes],
            "total_steps": summary["total"],
            "summary": f"Plan: {summary.get('completed', 0)}/{summary['total']} steps ok, "
                       f"{summary.get('failed', 0)} failed, {summary.get('skipped', 0)} skipped",
            "elapsed_ms": round(elapsed * 1000),
        }

    # ══════════════════════════════════════════════════════════
    #  Computer Use 模式 (Vision-based action loop)
    # ══════════════════════════════════════════════════════════

    def _computer_use_loop(self, goal: str, config: AgentConfig) -> dict:
        """Computer Use 风格循环：截图 → 视觉模型分析 → 执行 → 新截图 → ...

        使用 OpenAI 兼容 API 的 Vision 模型（MIMO-V2-Omni 等）。
        模型直接看截图，输出结构化指令决定点击/输入/按键等动作。
        不需要 SoM/OCR/UIA，端到端视觉推理（取决于模型能力）。
        """
        import pyautogui
        screen_w, screen_h = pyautogui.size()
        start = time.time()
        steps = []
        max_steps = config.max_steps or MAX_STEPS

        self._emit("agent_start", {"goal": goal, "engine": "computer_use/vision",
                                    "model": self.model})

        SYSPROMPT = f"""你是桌面电脑操作助手。通过「看截图 → 决定动作 → 看结果 → 继续动作」的循环完成用户目标。

可用动作:
- mouse_move(x, y) — 移动鼠标到坐标
- left_click(x, y) — 左键点击（省略坐标=点击当前位置）
- right_click(x, y) — 右键点击
- double_click(x, y) — 双击
- type(text) — 输入文字
- key(text) — 按键盘键 (Enter, Tab, Escape, Backspace, Ctrl+C, Alt+Tab 等)
- scroll(dx, dy) — 滚动
- cursor_position — 获取当前鼠标位置
- done(summary) — 任务彻底完成时使用

关键规则:
1. 每轮只做一个动作，不要在一次回复中做多个动作
2. 输入文字前必须先用 click 点击输入框确保焦点
3. done() 只能在实际完成用户目标后使用，不要提前结束
4. 不要猜测坐标位置 - 如果截图里看不到目标，说明需要先做其他操作（如滚动、切换窗口等）
5. PARAMS 为空时写 {{}}，不要省略

屏幕分辨率: {screen_w}x{screen_h}

输出格式:
ACTION: 动作名
PARAMS: {{"参数名": "值"}}
思考: 简短理由

错误做法:
ACTION: done
PARAMS: {{"summary": "打开了浏览器"}}
(× 用户要你操作，不是描述)

正确做法:
ACTION: left_click
PARAMS: {{"x": 960, "y": 540}}
思考: 点击搜索输入框准备输入查询
"""
        self._emit("log", {"level": "info",
                           "message": f"Computer Use Vision: model={self.model}"})

        messages = [{"role": "system", "content": SYSPROMPT}]
        done_noop_count = 0

        for loop in range(1, max_steps + 1):
            if self._stop_event and self._stop_event.is_set():
                break

            b64 = self.supervisor.capture_b64(quality=self._screenshot_quality)
            msg_text = "当前屏幕截图，请分析并决定下一步动作。" if loop > 1 else f"任务目标: {goal}\n\n请分析屏幕截图，执行第一步。"
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": msg_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            })

            raw = self._call_llm(messages)
            if raw is None:
                messages.append({"role": "user", "content": "API请求失败（网络连接中断），我已重试。请基于最后一次成功的截图继续执行任务。"})
                if loop >= 3:
                    break
                continue

            content = raw["choices"][0]["message"].get("content", "") or ""
            self._emit("log", {"level": "info", "message": f"思考: {content[:300]}"})

            action, action_data = self._parse_computer_vision_response(content)

            if action == "done":
                if len(steps) == 0:
                    done_noop_count += 1
                    if done_noop_count >= 3:
                        break
                    # 不走 API：直接注入提示，画面没变无需截新图
                    messages.append({"role": "user", "content": "你没有执行任何动作就完成了。必须执行至少一个动作（如 click / type / key）。先做动作，完成后才能 done。"})
                    self._emit("log", {"level": "info", "message": "模型过早 done，已发出纠正提示"})
                    if len(messages) > 8:
                        messages = [messages[0]] + messages[-6:]
                    continue
                summary = action_data.get("summary", content)[:500]
                self._emit("log", {"level": "info", "message": f"完成: {summary}"})
                ok_count = sum(1 for s in steps if s.get("status") == "completed")
                return {
                    "success": ok_count > 0, "steps": steps,
                    "summary": summary, "total_steps": len(steps),
                    "elapsed_ms": round((time.time() - start) * 1000),
                }

            if action == "error":
                self._emit("log", {"level": "warn", "message": f"无法解析: {content[:200]}"})
                messages.append({
                    "role": "user",
                    "content": "格式错误。请严格使用: ACTION: 动作名\\nPARAMS: {{...}} 格式",
                })
                continue

            action_data = {k: v for k, v in action_data.items() if v is not None}
            step, result_content, success = self._record_computer_step(
                steps, loop, action, action_data, screen_w, screen_h)

            result_text = "成功" if success else f"失败: {result_content.get('text', '')}"
            messages.append({"role": "user", "content": f"执行结果: {result_text}"})

            if len(messages) > self.sliding_window_limit:
                messages = [messages[0]] + messages[-(self.sliding_window_limit - 2):]

        elapsed = time.time() - start
        ok_count = sum(1 for s in steps if s.get("status") == "completed")
        return {
            "success": ok_count > 0, "steps": steps,
            "summary": f"Computer Use 执行 {len(steps)} 步 ({ok_count} 成功)",
            "total_steps": len(steps), "elapsed_ms": round(elapsed * 1000),
        }

    @staticmethod
    def _parse_computer_vision_response(content: str) -> tuple:
        """解析 Vision 模型的响应，返回 (action, params_dict)。

        支持两种格式:
          1. 标准: ACTION: xxx\nPARAMS: {...}
          2. JSON: {"ACTION": "xxx", "PARAMS": {...}}
        """
        import re
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())

        # 尝试 JSON 格式
        action, params = None, None
        if content.startswith("{"):
            try:
                js = json.loads(content)
                if isinstance(js, dict):
                    action = str(js.get("ACTION", js.get("action", ""))).strip().lower()
                    params = js.get("PARAMS", js.get("params", js.get("arguments", {})))
                    if isinstance(params, str):
                        try:
                            params = json.loads(params)
                        except json.JSONDecodeError:
                            params = {}
                    if not isinstance(params, dict):
                        params = {"value": str(params)}
            except json.JSONDecodeError:
                pass

        if not action:
            action_match = re.search(r"ACTION:\s*(\S+)", content)
            if not action_match:
                return "error", {"raw": content[:200]}
            action = action_match.group(1).strip().lower().strip('",')

        if params is None:
            params = {}
            params_match = re.search(r"PARAMS:\s*(\{.*?\})", content, re.DOTALL)
            if params_match:
                try:
                    parsed = json.loads(params_match.group(1))
                    if isinstance(parsed, dict):
                        params = parsed
                except json.JSONDecodeError:
                    pass

        if not params:
            kv_match = re.findall(r"(\w+)\s*[:=]\s*(\S+)", content)
            for k, v in kv_match:
                if k.lower() != "action":
                    params[k] = v.strip('"\'')
            if not params:
                kv_match = re.findall(r"(\w+)\s*[:：]\s*(.+)", content)
                for k, v in kv_match:
                    if k.lower() not in ("action", "思考"):
                        params[k] = v.strip('"\'')
        return action, params

    def _record_computer_step(self, steps: list, loop: int,
                               action: str, action_data: dict,
                               screen_w: int, screen_h: int) -> tuple:
        """记录步骤、执行 computer 动作、返回 (step_dict, result_content, success)。"""
        sn = len(steps) + 1
        self._emit("log", {"level": "info",
                           "message": f"Computer: {action} {json.dumps(action_data, ensure_ascii=False)[:200]}"})
        step = {
            "step_num": sn, "tool": f"computer.{action}",
            "args": action_data, "status": "running",
        }
        self._emit("step_complete", dict(step))
        step_start = time.time()
        try:
            result_content = self._execute_computer_action(action, action_data, screen_w, screen_h,
                                                             screenshot_quality=self._screenshot_quality)
            success = True
        except Exception as e:
            result_content = {"type": "text", "text": f"Error: {str(e)}"}
            success = False
            self._emit("log", {"level": "error", "message": f"Computer action failed: {str(e)[:200]}"})

        step["status"] = "completed" if success else "failed"
        step["result"] = {"success": success, "error": "" if success else result_content.get("text", str(result_content)),
                          "step_ms": round((time.time() - step_start) * 1000)}
        steps.append(step)
        self._emit("step_complete", dict(step))
        return step, result_content, success

    @staticmethod
    def _execute_computer_action(action: str, data: dict,
                                  screen_w: int, screen_h: int,
                                  screenshot_quality: int = 60) -> dict:
        """执行 computer_use 动作，返回 tool_result content block。

        支持:
          screenshot, mouse_move, left_click, right_click, middle_click,
          double_click, left_click_drag, scroll, type, key, cursor_position

        参数兼容 Anthropic 格式 (coordinate: [x, y]) 和 Vision 格式 (x: 500, y: 300)。
        """
        import pyautogui
        import time as _time

        def _get(d: dict, k: str, default=None):
            v = d.get(k, default)
            if isinstance(v, str) and v.lower() == "none":
                return default
            return v

        def _coord(key: str = "coordinate", fallback_x: str = "x", fallback_y: str = "y"):
            """统一取坐标：支持 coordinate:[x,y], x:500/y:300, x:[500,300] 等多种格式。"""
            if key in data:
                c = data[key]
                if isinstance(c, (list, tuple)) and len(c) >= 2:
                    return max(0, min(int(c[0]), screen_w - 1)), max(0, min(int(c[1]), screen_h - 1))
            raw_x = _get(data, fallback_x, -1)
            raw_y = _get(data, fallback_y, -1)
            # 处理 x: [960, 159] 这种数组格式
            if isinstance(raw_x, (list, tuple)) and len(raw_x) >= 2:
                return max(0, min(int(raw_x[0]), screen_w - 1)), max(0, min(int(raw_x[1]), screen_h - 1))
            try:
                x = int(raw_x) if raw_x not in (None, "", -1) else -1
                y = int(raw_y) if raw_y not in (None, "", -1) else -1
            except (TypeError, ValueError):
                return None
            if x >= 0 and y >= 0:
                return max(0, min(x, screen_w - 1)), max(0, min(y, screen_h - 1))
            return None

        pyautogui.FAILSAFE = False

        # ── screenshot → 截图返回 ──
        if action == "screenshot":
            from agent.supervisor import Supervisor
            sup = Supervisor(screenshot_quality=85)
            b64 = sup.capture_b64()
            return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}}

        # ── mouse_move ──
        if action == "mouse_move":
            coord = _coord()
            if coord:
                pyautogui.moveTo(coord[0], coord[1])

        # ── left_click / right_click / middle_click / double_click ──
        elif action in ("left_click", "right_click", "middle_click", "double_click"):
            click_fn = {"left_click": pyautogui.click, "right_click": pyautogui.rightClick,
                        "middle_click": pyautogui.middleClick, "double_click": pyautogui.doubleClick}[action]
            coord = _coord()
            if coord:
                click_fn(coord[0], coord[1])
            else:
                click_fn()

        # ── left_click_drag ──
        elif action in ("left_click_drag", "drag"):
            start_coord = _coord("start_coordinate", "start_x", "start_y") or (0, 0)
            end_coord = _coord("coordinate", "end_x", "end_y") or start_coord
            pyautogui.moveTo(start_coord[0], start_coord[1])
            pyautogui.drag(end_coord[0] - start_coord[0], end_coord[1] - start_coord[1], duration=0.3)

        # ── scroll ──
        elif action == "scroll":
            dx = int(_get(data, "dx", _get(data, "scroll_x", 0)))
            dy = int(_get(data, "dy", _get(data, "scroll_y", -3)))
            pyautogui.scroll(dy)
            if dx:
                pyautogui.hscroll(dx)

        # ── type ──
        elif action == "type":
            text = _get(data, "text", "")
            if text:
                pyautogui.write(text, interval=0.02)

        # ── key ──
        elif action == "key":
            key_text = _get(data, "text", "")
            if key_text:
                # 处理组合键: Ctrl+C, Alt+Tab, Ctrl+Shift+Esc
                if "+" in key_text or "-" in key_text:
                    sep = "+" if "+" in key_text else "-"
                    parts = key_text.split(sep)
                    mods = []
                    main_key = None
                    key_map = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt",
                               "shift": "shift", "super": "win", "win": "win",
                               "meta": "win"}
                    for p in parts:
                        pk = p.strip().lower()
                        if pk in key_map:
                            mods.append(key_map[pk])
                        else:
                            main_key = pk
                    if main_key and mods:
                        hotkey_str = "+".join(mods + [main_key])
                        pyautogui.hotkey(*mods, main_key)
                    elif main_key:
                        pyautogui.press(main_key)
                    else:
                        pyautogui.hotkey(*mods)
                else:
                    key_map = {
                        "Return": "enter", "Enter": "enter", "Tab": "tab", "Space": "space",
                        "Backspace": "backspace", "Delete": "delete",
                        "Escape": "esc", "Esc": "esc",
                        "Super_R": "win", "Super_L": "win", "Super": "win",
                        "Control_L": "ctrl", "Control_R": "ctrl", "Control": "ctrl",
                        "Alt_L": "alt", "Alt_R": "alt", "Alt": "alt",
                        "Shift_L": "shift", "Shift_R": "shift", "Shift": "shift",
                        "CapsLock": "capslock",
                        "PageUp": "pageup", "PageDown": "pagedown",
                        "Home": "home", "End": "end",
                        "Up": "up", "Down": "down", "Left": "left", "Right": "right",
                        "F1": "f1", "F2": "f2", "F3": "f3", "F4": "f4",
                        "F5": "f5", "F6": "f6", "F7": "f7", "F8": "f8",
                        "F9": "f9", "F10": "f10", "F11": "f11", "F12": "f12",
                        "PrintScreen": "printscreen",
                        "ScrollLock": "scrolllock", "Pause": "pause",
                        "Menu": "apps", "NumLock": "numlock",
                    }
                    mapped = key_map.get(key_text, key_text.lower())
                    pyautogui.press(mapped)

        # ── cursor_position ──
        elif action == "cursor_position":
            x, y = pyautogui.position()
            return {"type": "text", "text": f"Cursor position: ({x}, {y})"}

        else:
            return {"type": "text", "text": f"Unknown computer action: {action}"}

        # ── 非 screenshot 动作：执行后截取新截图返回 ──
        _time.sleep(0.15)
        from agent.supervisor import Supervisor
        b64 = Supervisor(screenshot_quality=screenshot_quality).capture_b64(max_size=1920)

    # ══════════════════════════════════════════════════════════
    #  _unified_run — 统一执行入口
    # ══════════════════════════════════════════════════════════

    def _unified_run(self, goal: str, config: AgentConfig,
                     steps: Optional[list] = None) -> dict:
        """统一执行入口。根据 AgentConfig 自动选择执行策略。

        替代旧的 4 路 dispatch（text_protocol / langgraph / planned / direct）。
        """
        config.resolve_auto(self.model)

        # 传播监督层配置
        self.supervisor.on_error = config._resolved_on_error
        self.supervisor.window_aware_ocr = config.window_aware_ocr
        self.supervisor.uia_pre_focus = config.uia_pre_focus
        self.supervisor.scripted_mode = config.scripted_mode
        self.supervisor.verify = config.verify

        # 同步 LLM 参数到实例
        self._temperature = config.temperature
        self._max_tokens = config.max_tokens
        self._llm_retries = config.llm_retries
        self._screenshot_quality = config.screenshot_quality
        self._som_enabled = config.som_enabled
        # 滑动窗口：auto=0 → multimodal 用 8, text 用 12
        self.sliding_window_limit = config.sliding_window_size or (8 if config._multimodal else 12)

        # 脚本执行模式注入提示词（始终注入，run_python_script 已注册为通用元动作）
        self._system_prompt += SCRIPTED_MODE_INSTRUCTIONS

        self._emit("log", {"level": "info", "message":
            f"Unified agent: planning={config._resolved_planning}, "
            f"protocol={config._resolved_llm_protocol}, "
            f"vision={config._resolved_vision_mode}, "
            f"context={config._resolved_screen_context}, "
            f"multimodal={config._multimodal}, "
            f"on_error={config._resolved_on_error}, "
            f"window_ocr={config.window_aware_ocr}, "
            f"uia_focus={config.uia_pre_focus}, "
            f"scripted={config.scripted_mode}, "
            f"max_steps={config.max_steps}, "
            f"som={config.som_enabled}, "
            f"temp={config.temperature}, "
            f"retries={config.llm_retries}, "
            f"quality={config.screenshot_quality}, "
            f"chat_mode={config.chat_mode}, "
            f"visual={config.visual_strategy}"})

        # ── 反思/学习开关 ──
        self._reflection_enabled = getattr(config, "reflection_enabled", True)
        self._step_reflections.clear()

        # ── 历史记忆注入 (统一入口) ──
        self._inject_memory_prompt(goal)

        # Emit memory stats for frontend display (pre-run)
        try:
            self._emit("memory_snapshot", self._memory_store.stats())
        except Exception:
            pass

        # ── Computer Use protocol (Claude API with computer_use beta) ──
        if config._resolved_llm_protocol == "computer_use":
            return self._computer_use_loop(goal, config)

        # ── Planning: pre_plan vs per_step ──
        if config._resolved_planning == "pre_plan":
            return self._run_pre_plan(goal, steps, config)

        elif config._resolved_planning == "per_step":
            if config._resolved_llm_protocol == "text":
                return self.run_text_protocol(goal, _agent_config=config,
                                               guidance_queue=self._guidance_queue)
            elif config._resolved_llm_protocol == "function_calling":
                return self._run_per_step_function_calling(goal, config)

        # Fallback
        return self.run_text_protocol(goal, _agent_config=config,
                                       guidance_queue=self._guidance_queue)

    def _run_pre_plan(self, goal: str, steps: Optional[list],
                      config: AgentConfig) -> dict:
        """Pre-plan mode: generate all steps upfront, then execute via PlanExecutor."""
        return self.run_planned(goal, steps=steps, _agent_config=config)

    def _run_per_step_function_calling(self, goal: str, config: AgentConfig) -> dict:
        """Per-step mode with function calling LLM (LangGraph)."""
        self._emit("log", {"level": "info", "message": f"LangGraph max_steps set to {config.max_steps}"})
        return self.run_langgraph(goal, _agent_config=config)

    # ══════════════════════════════════════════════════════════
    #  统一入口 (保留旧 dispatch 保证向后兼容)
    # ══════════════════════════════════════════════════════════

    def run(self, goal: str, mode: str = "auto", **kwargs) -> dict:
        """执行目标。

        参数:
            goal: 自然语言目标描述
            mode: 执行模式 (当 agent_config 提供时忽略此参数)
                "auto"           — 自动选择 (mimo-model → text_protocol, 否则 → langgraph)
                "text_protocol"  — MIMO 风格文本协议循环
                "langgraph"      — LangGraph create_react_agent
                "direct"         — 仅生成流程图 (generate_flow_script)
            agent_config: AgentConfig 实例（新统一路径，提供时忽略 mode）

        返回:
            dict{success, steps, summary, total_steps, elapsed_ms}
        """
        if self._stop_event and self._stop_event.is_set():
            return {"success": False, "steps": [], "total_steps": 0,
                    "error": "stopped before start", "elapsed_ms": 0}

        # ── 新统一路径：AgentConfig 提供时直接使用 ──
        agent_config = kwargs.get("agent_config")
        agent_config_provided = agent_config is not None
        if agent_config_provided:
            result = self._unified_run(goal, agent_config, steps=kwargs.get("steps"))
            exec_mode = agent_config._resolved_planning or "unified"
        else:
            # ── 旧 4 路 dispatch（向后兼容）──
            # 自动检测模式
            if mode == "auto":
                model_lower = self.model.strip().lower()
                if "mimo" in model_lower:
                    mode = "text_protocol"
                elif self._check_langgraph():
                    mode = "langgraph"
                else:
                    # 无 langgraph → 回退到 planned (最便宜: 1次LLM调用, 然后离线执行)
                    self._emit("log", {"level": "info", "message": "LangGraph not available, falling back to planned"})
                    mode = "planned"

            if mode == "text_protocol":
                self._inject_memory_prompt(goal)
                result = self.run_text_protocol(goal)
            elif mode == "planned":
                self._inject_memory_prompt(goal)
                steps = kwargs.get("steps")
                result = self.run_planned(goal, steps=steps)
            elif mode == "langgraph":
                self._inject_memory_prompt(goal)
                result = self.run_langgraph(goal)
            elif mode == "direct":
                result = self._run_direct(goal)
            else:
                raise ValueError(f"Unknown mode: {mode}")
            exec_mode = mode

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
