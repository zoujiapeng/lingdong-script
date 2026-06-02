"""
AgentConfig — unified agent execution configuration.

All fields use string enums for JSON-serializability across the IPC boundary.
"auto" values are resolved at runtime based on model capability detection
via _is_multimodal_model().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AgentConfig:
    # ── LLM protocol: how the agent communicates with the LLM ──
    llm_protocol: str = "auto"          # "auto"|"text"|"function_calling"|"computer_use"

    # ── Vision: how the agent sees the screen ──
    vision_mode: str = "auto"           # "auto"|"direct"|"som"|"omniparser"

    # ── Planning: when and how the agent plans ──
    planning: str = "auto"              # "auto"|"pre_plan"|"per_step"

    # ── Screen context verbosity ──
    screen_context: str = "auto"        # "auto"|"minimal"|"full"

    # ── Error handling strategy ──
    on_error: str = "retry"             # "retry"|"replan"|"skip"|"stop"

    # ── Multimodal override ──
    multimodal: str = "auto"            # "auto"|"true"|"false" — user override

    # ── Boolean toggles ──
    verify: bool = True
    fast_mode: bool = False
    image_dedup: bool = True
    window_aware_ocr: bool = True   # OCR 搜索时限定到活动窗口区域
    uia_pre_focus: bool = True      # type_text 前 UIA 自动聚焦输入框
    scripted_mode: bool = False     # 脚本执行模式：写 Python 脚本代替逐工具调用
    auto_learn: bool = False        # 自动学习（截图→经验库）
    som_enabled: bool = True        # SoM 标号（截图元素编号）
    flow_chart_mode: bool = False   # 流程图模式（全工具提示词 + generate_flow/execute_flow）
    chat_mode: bool = False         # 对话模式：边执行边聊天，接收实时反馈/新任务（默认关闭保持原行为）
    reflection_enabled: bool = True  # 自我反思与学习：任务后分析提取规则/偏好/经验（默认开启）
    local_vision: bool = False      # 本地视觉引擎（YOLO + MobileNet + RapidOCR），仅对纯文本模型生效

    # ── Visual strategy: how the agent "sees" the screen ──
    # "auto"       — 自动选择（多模态=direct，纯文本=som）
    # "cli_path"   — 方案1: 先将软件转CLI/已探索路径，AI直接看操作逻辑
    # "blind_guess" — 方案2: LLM自主盲猜像素坐标（原始方式）
    # "ocr_llm"    — 方案3: 截图OCR→LLM回复最可能文字→点击对应坐标
    # "som"        — 方案4: 截图LLM标记→标记转坐标（已实现，默认关闭因本地模型慢）
    visual_strategy: str = "auto"   # 视觉策略选择器

    # ── Safety limits ──
    max_steps: int = 20              # 单次任务最大步数
    max_consecutive_failures: int = 5  # 连续失败上限
    min_success_rate: float = 0.1   # 最低成功率

    # ── LLM parameters ──
    temperature: float = 0.1        # LLM temperature (0.0-2.0)
    max_tokens: int = 4096          # LLM max_tokens
    llm_retries: int = 2            # LLM API 调用重试次数

    # ── Screenshot ──
    screenshot_quality: int = 50    # JPEG 压缩质量 (30-95)

    # ── Sliding window ──
    sliding_window_size: int = 0   # 0 = auto (8=multimodal, 12=text)

    # ── Vision backend ──
    vision_backend: str = "gpt-4o-mini"  # VLM 后端模型

    # ── Internal resolved state (set by resolve_auto()) ──
    _resolved_llm_protocol: str = ""
    _resolved_vision_mode: str = ""
    _resolved_planning: str = ""
    _resolved_screen_context: str = ""
    _resolved_on_error: str = ""
    _multimodal: bool = False

    def resolve_auto(self, model_name: str) -> None:
        """Resolve all "auto" fields based on the model's capabilities.

        For multimodal models (MIMO, GPT-4o, Claude 3.5+, Gemini, Qwen-VL, etc.):
          - llm_protocol -> "text"
          - vision_mode -> "direct"
          - planning -> "per_step"
          - screen_context -> "minimal"
          - image_dedup -> True

        For text-only models (DeepSeek-chat, GPT-3.5, etc.):
          - llm_protocol -> "function_calling"
          - vision_mode -> "som"
          - planning -> "pre_plan"
          - screen_context -> "full"
        """
        from agent.agent import _is_multimodal_model
        if self.multimodal == "true":
            self._multimodal = True
        elif self.multimodal == "false":
            self._multimodal = False
        else:
            self._multimodal = _is_multimodal_model(model_name)

        # computer_use protocol overrides everything
        if self.llm_protocol == "computer_use":
            self._multimodal = True
            resolved = {
                "llm_protocol": "computer_use",
                "vision_mode": "direct",
                "planning": "per_step",
                "screen_context": "minimal",
                "on_error": self.on_error if self.on_error != "auto" else "retry",
            }
            for field_name in ("llm_protocol", "vision_mode", "planning",
                               "screen_context", "on_error"):
                setattr(self, f"_resolved_{field_name}", resolved[field_name])
            if self.fast_mode:
                self.image_dedup = True
            return

        resolved = {}
        if self._multimodal:
            if self.llm_protocol == "auto":
                resolved["llm_protocol"] = "text"
            if self.vision_mode == "auto":
                resolved["vision_mode"] = "direct"
            if self.planning == "auto":
                resolved["planning"] = "per_step"
            if self.screen_context == "auto":
                resolved["screen_context"] = "minimal"
            if self.on_error == "auto":
                resolved["on_error"] = "retry"
        else:
            if self.llm_protocol == "auto":
                resolved["llm_protocol"] = "function_calling"
            if self.vision_mode == "auto":
                resolved["vision_mode"] = "som"
            if self.planning == "auto":
                resolved["planning"] = "pre_plan"
            if self.screen_context == "auto":
                resolved["screen_context"] = "full"
            if self.on_error == "auto":
                resolved["on_error"] = "retry"

        # Set resolved values
        for field_name in ("llm_protocol", "vision_mode", "planning",
                           "screen_context", "on_error"):
            if field_name in resolved:
                setattr(self, f"_resolved_{field_name}", resolved[field_name])
            else:
                raw = getattr(self, field_name)
                setattr(self, f"_resolved_{field_name}", raw)

        # Resolve visual_strategy "auto"
        if self.visual_strategy == "auto":
            if self._multimodal:
                self._resolved_vision_mode = "direct"
            else:
                self._resolved_vision_mode = "som"
        else:
            # Override vision_mode based on visual_strategy
            if self.visual_strategy == "ocr_llm":
                self._resolved_vision_mode = "ocr_llm"
                # ocr_llm 策略需要走 text protocol + per_step planning
                # 这样才能 OCR→LLM→点击 的闭环
                if self._resolved_llm_protocol != "text":
                    self._resolved_llm_protocol = "text"
                    self._resolved_planning = "per_step"
            elif self.visual_strategy == "blind_guess":
                self._resolved_vision_mode = "direct"  # 盲猜：直接截图，无标注
            elif self.visual_strategy == "som":
                self._resolved_vision_mode = "som"
            elif self.visual_strategy == "cli_path":
                self._resolved_vision_mode = "som"  # CLI路径用SoM做兜底

        # Apply boolean-derived behavior
        if self.fast_mode:
            self.image_dedup = True

    def get_resolved(self) -> dict:
        """Return resolved configuration as a public dict.

        After resolve_auto() is called, this exposes the effective values
        that the agent will actually use, regardless of whether each field
        was "auto" or explicitly set.
        """
        return {
            "llm_protocol": self._resolved_llm_protocol,
            "vision_mode": self._resolved_vision_mode,
            "planning": self._resolved_planning,
            "screen_context": self._resolved_screen_context,
            "on_error": self._resolved_on_error,
            "multimodal": self._multimodal,
        }

    def to_dict(self) -> dict:
        """Serialize to dict for IPC transmission."""
        return {
            "llm_protocol": self.llm_protocol,
            "vision_mode": self.vision_mode,
            "planning": self.planning,
            "screen_context": self.screen_context,
            "on_error": self.on_error,
            "multimodal": self.multimodal,
            "verify": self.verify,
            "fast_mode": self.fast_mode,
            "image_dedup": self.image_dedup,
            "window_aware_ocr": self.window_aware_ocr,
            "uia_pre_focus": self.uia_pre_focus,
            "scripted_mode": self.scripted_mode,
            "auto_learn": self.auto_learn,
            "som_enabled": self.som_enabled,
            "flow_chart_mode": self.flow_chart_mode,
            "max_steps": self.max_steps,
            "max_consecutive_failures": self.max_consecutive_failures,
            "min_success_rate": self.min_success_rate,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "llm_retries": self.llm_retries,
            "screenshot_quality": self.screenshot_quality,
            "sliding_window_size": self.sliding_window_size,
            "vision_backend": self.vision_backend,
            "chat_mode": self.chat_mode,
            "visual_strategy": self.visual_strategy,
            "reflection_enabled": self.reflection_enabled,
            "local_vision": self.local_vision,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AgentConfig:
        """Deserialize from dict (from IPC)."""
        return cls(
            llm_protocol=d.get("llm_protocol", "auto"),
            multimodal=d.get("multimodal", "auto"),
            vision_mode=d.get("vision_mode", "auto"),
            planning=d.get("planning", "auto"),
            screen_context=d.get("screen_context", "auto"),
            on_error=d.get("on_error", "retry"),
            verify=d.get("verify", True),
            fast_mode=d.get("fast_mode", False),
            image_dedup=d.get("image_dedup", True),
            window_aware_ocr=d.get("window_aware_ocr", True),
            uia_pre_focus=d.get("uia_pre_focus", True),
            scripted_mode=d.get("scripted_mode", False),
            auto_learn=d.get("auto_learn", False),
            som_enabled=d.get("som_enabled", True),
            flow_chart_mode=d.get("flow_chart_mode", False),
            max_steps=d.get("max_steps", 20),
            max_consecutive_failures=d.get("max_consecutive_failures", 5),
            min_success_rate=d.get("min_success_rate", 0.1),
            temperature=d.get("temperature", 0.1),
            max_tokens=d.get("max_tokens", 4096),
            llm_retries=d.get("llm_retries", 2),
            screenshot_quality=d.get("screenshot_quality", 60),
            sliding_window_size=d.get("sliding_window_size", 0),
            vision_backend=d.get("vision_backend", "gpt-4o-mini"),
            chat_mode=d.get("chat_mode", False),
            visual_strategy=d.get("visual_strategy", "auto"),
            reflection_enabled=d.get("reflection_enabled", True),
            local_vision=d.get("local_vision", False),
        )


def agent_config_from_legacy(mimo: bool, supervise: bool, model_name: str = "") -> AgentConfig:
    """Convert legacy mimo/supervise flags to AgentConfig.

    This ensures backward compatibility for existing IPC messages
    that use the old boolean flag dispatch.
    """
    if mimo:
        cfg = AgentConfig(
            llm_protocol="text",
            vision_mode="auto",
            planning="per_step",
            screen_context="auto",
        )
        cfg.resolve_auto(model_name or "")
        return cfg

    if not supervise:
        # "direct" mode — generate only
        return AgentConfig(
            planning="pre_plan",
            fast_mode=True,
            verify=False,
        )

    # supervise=True, text-only model
    return AgentConfig(
        llm_protocol="function_calling",
        planning="pre_plan",
    )
