"""
AgentConfig — 4 种执行模式的统一配置

每种模式有独立的子配置，默认值 = 已验证成功的配置。
模式切换时只暴露该模式相关的字段。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ── 模式 1: 流程图模式 ─────────────────────────────────────────

@dataclass
class FlowChartConfig:
    """模式1: 生成流程图 → 用户决定执行。"""
    supervise: bool = True          # 监督执行开关


# ── 模式 2: 多模态模式 ─────────────────────────────────────────

@dataclass
class MultimodalConfig:
    """模式2: 多模态模型直接截图→思考→执行 (MIMO 成功路径)。"""
    screenshot_quality: int = 60    # 唯一可配参数


# ── 模式 3: 非多模态模式 ───────────────────────────────────────

@dataclass
class TextVisionConfig:
    """模式3: 纯文本模型 + 本地视觉 (DeepSeek 成功路径)。

    默认值 = 上次验证成功的配置 (2026-06-02):
      - UIA 关闭，纯视觉 (YOLO+OCR+SoM)
      - 窗口限定开启，只扫活动窗口
      - 记忆/反思关闭
    """
    # ── 感知层开关 ──
    uia_enabled: bool = False       # UIA 控件树
    ocr_enabled: bool = True        # OCR 文字检测
    yolo_enabled: bool = False      # YOLO 图标检测
    som_enabled: bool = True        # SoM 标号
    window_aware_ocr: bool = True   # 窗口限定（只扫活动窗口）
    local_vision: bool = False      # 本地视觉引擎 (YOLO ONNX)

    # ── 视觉策略 ──
    visual_strategy: str = "som"    # som | ocr_llm | blind_guess | cli_path

    # ── 执行协议 ──
    llm_protocol: str = "text"      # text | function_calling
    planning: str = "per_step"      # per_step | pre_plan


# ── 模式 4: 离线模式 ───────────────────────────────────────────

@dataclass
class OfflineConfig:
    """模式4: 全离线，直接执行流程图 (预留)。"""
    pass


# ── 主配置 ─────────────────────────────────────────────────────

@dataclass
class AgentConfig:
    """Agent 统一配置，4 种执行模式。

    用法:
        cfg = AgentConfig(execution_mode="text")
        cfg.text_vision_cfg.uia_enabled = False  # example
        resolved = cfg.resolve()  # → 运行时扁平字典
    """
    execution_mode: str = "flow_chart"  # flow_chart | multimodal | text | offline

    # ── 全局 ──
    memory_enabled: bool = False    # 记忆系统 (默认关闭)
    max_steps: int = 20
    max_consecutive_failures: int = 5
    min_success_rate: float = 0.1
    temperature: float = 0.1
    max_tokens: int = 4096
    llm_retries: int = 2
    screenshot_quality: int = 60
    sliding_window_size: int = 0    # 0=auto
    on_error: str = "retry"         # retry | replan | skip | stop

    # ── 各模式子配置（用 _cfg 后缀避免与旧字段名冲突）──
    flow_chart_cfg: FlowChartConfig = field(default_factory=FlowChartConfig)
    multimodal_cfg: MultimodalConfig = field(default_factory=MultimodalConfig)
    text_vision_cfg: TextVisionConfig = field(default_factory=TextVisionConfig)
    offline_cfg: OfflineConfig = field(default_factory=OfflineConfig)

    # ── 已弃用: 旧扁平字段 (仅向后兼容, 新代码勿用) ──
    # 以下字段保留供 agent_config_from_legacy() 使用，resolve() 不再读取
    llm_protocol: str = "auto"
    vision_mode: str = "auto"
    planning: str = "auto"
    screen_context: str = "auto"
    multimodal: str = "auto"
    verify: bool = True
    fast_mode: bool = False
    image_dedup: bool = True
    window_aware_ocr: bool = True
    uia_pre_focus: bool = True
    scripted_mode: bool = False
    auto_learn: bool = False
    som_enabled: bool = True
    flow_chart_mode: bool = False
    chat_mode: bool = False
    reflection_enabled: bool = True
    local_vision: bool = False
    visual_strategy: str = "auto"
    vision_backend: str = "gpt-4o-mini"

    # ── 内部解析状态 ──
    _resolved_llm_protocol: str = ""
    _resolved_vision_mode: str = ""
    _resolved_planning: str = ""
    _resolved_screen_context: str = ""
    _resolved_on_error: str = ""
    _multimodal: bool = False

    def resolve(self) -> dict:
        """根据 execution_mode 展平成运行时字典。

        替代旧 resolve_auto()。返回的字典包含 supervisor 和 LLM 需要的所有参数。
        """
        base = {
            "memory_enabled": self.memory_enabled,
            "max_steps": self.max_steps,
            "max_consecutive_failures": self.max_consecutive_failures,
            "min_success_rate": self.min_success_rate,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "llm_retries": self.llm_retries,
            "screenshot_quality": self.screenshot_quality,
            "sliding_window_size": self.sliding_window_size,
            "on_error": self.on_error,
            "verify": True,
            "image_dedup": True,
            "fast_mode": False,
            "chat_mode": False,
            "flow_chart_mode": False,
        }

        if self.execution_mode == "flow_chart":
            return {**base,
                "supervise": self.flow_chart_cfg.supervise,
                "flow_chart_mode": True,
                "multimodal": False,
            }

        elif self.execution_mode == "multimodal":
            return {**base,
                "multimodal": True,
                "screenshot_quality": self.multimodal_cfg.screenshot_quality,
                "llm_protocol": "text",
                "planning": "per_step",
                "vision_mode": "direct",
                "screen_context": "minimal",
                "som_enabled": False,
                "uia_enabled": False,
                "window_aware_ocr": False,
                "visual_strategy": "direct",
            }

        elif self.execution_mode == "text":
            tv = self.text_vision_cfg
            return {**base,
                "multimodal": False,
                "uia_enabled": tv.uia_enabled,
                "uia_pre_focus": tv.uia_enabled,   # UIA 预聚焦跟随 uia_enabled
                "ocr_enabled": tv.ocr_enabled,
                "yolo_enabled": tv.yolo_enabled,
                "som_enabled": tv.som_enabled,
                "window_aware_ocr": tv.window_aware_ocr,
                "local_vision": tv.local_vision,
                "visual_strategy": tv.visual_strategy,
                "llm_protocol": tv.llm_protocol,
                "planning": tv.planning,
                "vision_mode": tv.visual_strategy,
                "screen_context": "full",
            }

        else:  # offline
            return {**base,
                "multimodal": False,
                "llm_protocol": "text",
                "planning": "pre_plan",
            }

    # ── 已弃用: 旧 resolve_auto (保留向后兼容) ──

    def resolve_auto(self, model_name: str) -> None:
        """已弃用: 请使用 resolve() 替代。

        旧版自动检测逻辑，仅用于 agent_config_from_legacy() 向后兼容。
        新代码直接设置 execution_mode + 子配置，调用 resolve()。
        """
        from agent.agent import _is_multimodal_model
        if self.multimodal == "true":
            self._multimodal = True
        elif self.multimodal == "false":
            self._multimodal = False
        else:
            self._multimodal = _is_multimodal_model(model_name)

        if self.llm_protocol == "computer_use":
            self._multimodal = True
            resolved = {
                "llm_protocol": "computer_use",
                "vision_mode": "direct",
                "planning": "per_step",
                "screen_context": "minimal",
                "on_error": self.on_error if self.on_error != "auto" else "retry",
            }
            for fn in ("llm_protocol", "vision_mode", "planning", "screen_context", "on_error"):
                setattr(self, f"_resolved_{fn}", resolved[fn])
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

        for fn in ("llm_protocol", "vision_mode", "planning", "screen_context", "on_error"):
            if fn in resolved:
                setattr(self, f"_resolved_{fn}", resolved[fn])
            else:
                raw = getattr(self, fn)
                setattr(self, f"_resolved_{fn}", raw)

        if self.visual_strategy == "auto":
            if self._multimodal:
                self._resolved_vision_mode = "direct"
            else:
                self._resolved_vision_mode = "som"
        else:
            if self.visual_strategy == "ocr_llm":
                self._resolved_vision_mode = "ocr_llm"
                if self._resolved_llm_protocol != "text":
                    self._resolved_llm_protocol = "text"
                    self._resolved_planning = "per_step"
            elif self.visual_strategy == "blind_guess":
                self._resolved_vision_mode = "direct"
            elif self.visual_strategy == "som":
                self._resolved_vision_mode = "som"
            elif self.visual_strategy == "cli_path":
                self._resolved_vision_mode = "som"

        if self.fast_mode:
            self.image_dedup = True

    def get_resolved(self) -> dict:
        """已弃用: 返回旧版已解析字段 (向后兼容)。"""
        return {
            "llm_protocol": self._resolved_llm_protocol,
            "vision_mode": self._resolved_vision_mode,
            "planning": self._resolved_planning,
            "screen_context": self._resolved_screen_context,
            "on_error": self._resolved_on_error,
            "multimodal": self._multimodal,
        }

    # ── 序列化 ──

    def to_dict(self) -> dict:
        """序列化为 IPC 传输用的扁平字典 (含所有旧字段兼容)。"""
        return {
            "execution_mode": self.execution_mode,
            "memory_enabled": self.memory_enabled,
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
            "chat_mode": self.chat_mode,
            "reflection_enabled": self.reflection_enabled,
            "local_vision": self.local_vision,
            "visual_strategy": self.visual_strategy,
            "max_steps": self.max_steps,
            "max_consecutive_failures": self.max_consecutive_failures,
            "min_success_rate": self.min_success_rate,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "llm_retries": self.llm_retries,
            "screenshot_quality": self.screenshot_quality,
            "sliding_window_size": self.sliding_window_size,
            "vision_backend": self.vision_backend,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentConfig":
        """从 IPC 字典反序列化。"""
        cfg = cls(
            execution_mode=d.get("execution_mode", "flow_chart"),
            memory_enabled=d.get("memory_enabled", False),
            max_steps=d.get("max_steps", 20),
            max_consecutive_failures=d.get("max_consecutive_failures", 5),
            min_success_rate=d.get("min_success_rate", 0.1),
            temperature=d.get("temperature", 0.1),
            max_tokens=d.get("max_tokens", 4096),
            llm_retries=d.get("llm_retries", 2),
            screenshot_quality=d.get("screenshot_quality", 60),
            sliding_window_size=d.get("sliding_window_size", 0),
            on_error=d.get("on_error", "retry"),
        )
        # 填入子配置
        mode = cfg.execution_mode
        if mode == "flow_chart":
            cfg.flow_chart_cfg.supervise = d.get("supervise", True)
        elif mode == "multimodal":
            cfg.multimodal_cfg.screenshot_quality = d.get("screenshot_quality", 60)
        elif mode == "text":
            tv = cfg.text_vision_cfg
            tv.uia_enabled = d.get("uia_enabled", False)
            tv.ocr_enabled = d.get("ocr_enabled", True)
            tv.yolo_enabled = d.get("yolo_enabled", False)
            tv.som_enabled = d.get("som_enabled", True)
            tv.window_aware_ocr = d.get("window_aware_ocr", True)
            tv.local_vision = d.get("local_vision", False)
            tv.visual_strategy = d.get("visual_strategy", "som")
            tv.llm_protocol = d.get("llm_protocol", "text")
            tv.planning = d.get("planning", "per_step")
        # offline 模式无子配置

        # ── 旧版扁平字段向后兼容 ──
        for field in ("llm_protocol", "vision_mode", "planning", "screen_context",
                      "on_error", "verify", "fast_mode", "image_dedup",
                      "window_aware_ocr", "uia_pre_focus", "scripted_mode",
                      "auto_learn", "som_enabled", "flow_chart_mode", "chat_mode",
                      "reflection_enabled", "local_vision", "visual_strategy",
                      "multimodal", "vision_backend"):
            if field in d and hasattr(cfg, field):
                setattr(cfg, field, d[field])

        return cfg


# ── 已弃用: 旧版转换函数 ───────────────────────────────────────

def agent_config_from_legacy(mimo: bool, supervise: bool, model_name: str = "") -> AgentConfig:
    """已弃用: 旧 mimo/supervise 标志 → AgentConfig 转换。

    仅向后兼容旧版 IPC 消息，新代码应直接构建 AgentConfig。
    """
    if mimo:
        cfg = AgentConfig(
            execution_mode="multimodal",
            llm_protocol="text",
            vision_mode="auto",
            planning="per_step",
            screen_context="auto",
        )
        cfg.resolve_auto(model_name or "")
        return cfg

    if not supervise:
        return AgentConfig(
            execution_mode="flow_chart",
            planning="pre_plan",
            fast_mode=True,
            verify=False,
        )

    return AgentConfig(
        execution_mode="text",
        llm_protocol="function_calling",
        planning="pre_plan",
    )
