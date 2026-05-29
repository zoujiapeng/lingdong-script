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
from typing import Any, Callable, Optional

logger = logging.getLogger("agent.lingdong")

DEFAULT_API_BASE = "https://api.xiaomimimo.com/v1"
DEFAULT_MODEL = "mimo-v2-omni"
MAX_STEPS = 50

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

SYSTEM_PROMPT_TEXT = """你是桌面自动化 AI。你有 {tool_count} 个工具可用，在工具列表中按需选择。

## 工作方式
每次我给你：截图 + 屏幕状态。你选择下一个工具调用。
完成后回复 总结。

## 格式
```
思考: <你的推理>
工具: <工具名>
参数:
  参数名1: 值1
```
完成:
```
总结: <结果>
```

## 工具
{tools_text}
"""

# ── 工具描述生成 (from MimoAgent) ─────────────────────────────

def _build_tools_text() -> str:
    """生成按访问模式分组的工具描述文本（替代旧的按场景平铺分组）。"""
    from agent.tool_path_selector import build_path_prompt
    return build_path_prompt()


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
                 omniparser_enabled: bool = False):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
        self.api_base = (api_base or os.environ.get("OPENAI_API_BASE", "")
                         or os.environ.get("LLM_API_BASE", "")
                         or DEFAULT_API_BASE).rstrip("/")
        self.model = model or os.environ.get("VLM_MODEL", "") or os.environ.get("LLM_MODEL", "") or DEFAULT_MODEL
        self.multimodal = multimodal
        self.auto_learn = auto_learn
        self._event_cb = event_callback
        self._stop_event = stop_event or threading.Event()
        self._tools_text: str = ""
        self._system_prompt: str = ""
        self._registry_loaded = False
        self._som_enabled = True  # SoM 标号（截图元素编号）
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
        )

        # Load run history for context continuity
        self._history: list = []
        try:
            self._history = load_history(limit=10)
        except Exception:
            pass

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
                content = (msg.get("content", "") or
                          msg.get("reasoning_content", "") or
                          msg.get("reasoning", "") or "")  # MIMO 可能用 "reasoning" 字段
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

        if "总结:" in content and "工具:" not in content:
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

    def run_text_protocol(self, goal: str, _agent_config: Optional[AgentConfig] = None) -> dict:
        """文本协议模式：循环 调用 LLM → 解析 → 监督执行 → 截图。

        适用于 MIMO V2 Omni 等不支持 function calling 的纯文本模型。

        参数:
            goal: 自然语言目标
            _agent_config: 统一配置（由 _unified_run 传入），None 时自动检测
        """
        self._ensure_registry()

        # ── 从 AgentConfig 或自动检测确定多模态状态 ──
        if _agent_config is not None:
            multimodal = _agent_config._multimodal
            vision_mode = _agent_config._resolved_vision_mode
            context_minimal = _agent_config._resolved_screen_context == "minimal"
            use_dedup = _agent_config.image_dedup
            use_fast = _agent_config.fast_mode
        else:
            multimodal = _is_multimodal_model(self.model)
            vision_mode = "direct" if multimodal else "som"
            context_minimal = multimodal
            use_dedup = multimodal
            use_fast = False

        if multimodal:
            self._emit("log", {
                "level": "info",
                "message": f"多模态快速路径: {self.model} 可直接理解截图，跳过 SoM 标注",
            })

        self._emit("agent_start", {"goal": goal, "engine": "text_protocol", "model": self.model, "multimodal": multimodal})

        start = time.time()
        steps = []
        last_b64_hash = ""  # 用于截图去重

        messages = [{"role": "system", "content": self._system_prompt}]

        # ── 路径提示：为目标选择合适的工具访问模式 ──
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

        # ── 首次截图 + 上下文 ──
        # 多模态: 原始截图 + minimal 上下文
        # 非多模态: SoM 标注 + 完整上下文
        raw_b64 = self.supervisor.capture_b64(quality=50 if multimodal else 60)
        screen_ctx = self._get_screen_context(minimal=context_minimal)

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

        # 多模态提示词：直接让模型看图
        first_prompt = (
            f"任务目标: {goal}\n\n当前状态:\n{screen_ctx}"
            + (som_label if vision_mode != "direct" else "")
            + ("\n\n分析屏幕截图，执行第一步。" if vision_mode != "direct"
               else "\n\n屏幕截图可见。请基于截图内容判断下一步操作，返回工具调用。")
        )
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": first_prompt},
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
            reasoning = (msg.get("reasoning_content", "") or
                        msg.get("reasoning", "") or "")  # MIMO 兼容

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
                result = self.supervisor.execute(name, args)
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
            hint = "\n\n(如果任务目标已达成，请直接回复 总结: 结束，不要继续多余操作)" if success else ""
            messages.append({
                "role": "user",
                "content": json.dumps(result, ensure_ascii=False)[:1500] + hint,
            })

            status_txt = "成功" if success else f"失败: {result.get('error', 'unknown')}"
            if name in Supervisor.NO_SCREENSHOT_TOOLS:
                messages.append({
                    "role": "user",
                    "content": f"执行{status_txt}。{hint}",
                })
            else:
                raw_b64 = self.supervisor.capture_b64(quality=50 if multimodal else 60)

                # ── 截图去重：如果截图内容没变，跳过发送 ──
                cur_hash = hashlib.md5(raw_b64.encode()).hexdigest()[:16]
                if cur_hash == last_b64_hash and use_dedup:
                    messages.append({
                        "role": "user",
                        "content": f"执行{status_txt} (截图未变化，沿用上一帧)。{hint}",
                    })
                else:
                    last_b64_hash = cur_hash
                    screen_ctx = self._get_screen_context(minimal=context_minimal)

                    # SoM 标号（非多模态模式才需要）
                    som_label = ""
                    b64 = raw_b64
                    if self._som_enabled:
                        som_result = self._apply_som(raw_b64, multimodal=multimodal)
                        self.supervisor._last_som_map = som_result["label_map"]
                        if vision_mode != "direct" and som_result["count"] > 0:
                            b64 = som_result["image_b64"]
                            lines = "\n".join(f"  #{i}: 「{e['text']}」" for i, e in som_result["label_map"].items())
                            som_label = f"\n屏幕元素编号:\n{lines}"

                    parts = [
                        {"type": "text", "text": f"执行{status_txt}。{hint}\n\n当前状态:\n{screen_ctx}{som_label}"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ]
                    messages.append({"role": "user", "content": parts})

            if not success:
                time.sleep(1.0)

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

            # ── 滑动窗口：减少历史大小以加速 LLM 处理 ──
            # 多模态：每步都带大图，保留更少历史
            # 快速模式：保留最少历史（4条）
            if use_fast:
                MAX_HISTORY = 4
            else:
                MAX_HISTORY = 8 if multimodal else 12
            if len(messages) > MAX_HISTORY:
                messages = [messages[0], messages[1]] + messages[-(MAX_HISTORY - 2):]

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
    #  _unified_run — 统一执行入口
    # ══════════════════════════════════════════════════════════

    def _unified_run(self, goal: str, config: AgentConfig,
                     steps: Optional[list] = None) -> dict:
        """统一执行入口。根据 AgentConfig 自动选择执行策略。

        替代旧的 4 路 dispatch（text_protocol / langgraph / planned / direct）。
        """
        config.resolve_auto(self.model)

        self._emit("log", {"level": "info", "message":
            f"Unified agent: planning={config._resolved_planning}, "
            f"protocol={config._resolved_llm_protocol}, "
            f"vision={config._resolved_vision_mode}, "
            f"context={config._resolved_screen_context}, "
            f"multimodal={config._multimodal}"})

        # ── Planning: pre_plan vs per_step ──
        if config._resolved_planning == "pre_plan":
            return self._run_pre_plan(goal, steps, config)

        elif config._resolved_planning == "per_step":
            if config._resolved_llm_protocol == "text":
                return self.run_text_protocol(goal, _agent_config=config)
            elif config._resolved_llm_protocol == "function_calling":
                return self._run_per_step_function_calling(goal, config)

        # Fallback
        return self.run_text_protocol(goal, _agent_config=config)

    def _run_pre_plan(self, goal: str, steps: Optional[list],
                      config: AgentConfig) -> dict:
        """Pre-plan mode: generate all steps upfront, then execute via PlanExecutor."""
        return self.run_planned(goal, steps=steps, _agent_config=config)

    def _run_per_step_function_calling(self, goal: str, config: AgentConfig) -> dict:
        """Per-step mode with function calling LLM (LangGraph)."""
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
        if agent_config is not None:
            return self._unified_run(goal, agent_config, steps=kwargs.get("steps"))

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
            result = self.run_text_protocol(goal)
        elif mode == "planned":
            steps = kwargs.get("steps")
            result = self.run_planned(goal, steps=steps)
        elif mode == "langgraph":
            result = self.run_langgraph(goal)
        elif mode == "direct":
            result = self._run_direct(goal)
        else:
            raise ValueError(f"Unknown mode: {mode}")

        # Persist run summary
        try:
            save_run(RunSummary(
                goal=goal[:200],
                mode=mode,
                model=self.model,
                success=result.get("success", False),
                total_steps=result.get("total_steps", 0),
                elapsed_ms=result.get("elapsed_ms", 0),
                timestamp=datetime.now(timezone.utc).isoformat(),
                summary=(result.get("summary") or "")[:500],
                error=result.get("error", ""),
            ))
        except Exception as e:
            logger.warning("Failed to persist run summary: %s", e)

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
            return {"success": success, "steps": [], "total_steps": 1,
                    "summary": "Flow script generated" if success else "Generation failed"}
        except Exception as exc:
            self._emit("error", {"message": f"generate_flow_script failed: {exc}"})
            self._emit("agent_done", {"status": "failed", "total_steps": 0, "fail_reason": str(exc)})
            return {"success": False, "steps": [], "total_steps": 0, "error": str(exc)}
