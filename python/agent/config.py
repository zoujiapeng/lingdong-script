"""
AgentConfig — 4 种执行模式的统一配置

每种模式有独立的子配置，默认值 = 已验证成功的配置。
模式切换时只暴露该模式相关的字段。
"""
from __future__ import annotations

from dataclasses import dataclass, field


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
    yolo_enabled: bool = False      # YOLO 图标检测（默认关闭，需 GPU/ONNX）
    som_enabled: bool = True        # SoM 标号
    window_aware_ocr: bool = True   # 窗口限定（只扫活动窗口）
    local_vision: bool = False      # 本地视觉引擎 (YOLO ONNX，默认关闭)

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

    def resolve(self) -> dict:
        """根据 execution_mode 展平成运行时字典。

        返回的字典包含 supervisor 和 LLM 需要的所有参数。
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

    # ── 序列化 ──

    def to_dict(self) -> dict:
        """序列化为 IPC 传输用的扁平字典。"""
        base = {
            "execution_mode": self.execution_mode,
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
        }
        # 包含子配置字段
        if self.execution_mode == "flow_chart":
            base["supervise"] = self.flow_chart_cfg.supervise
        elif self.execution_mode == "multimodal":
            base["screenshot_quality"] = self.multimodal_cfg.screenshot_quality
        elif self.execution_mode == "text":
            tv = self.text_vision_cfg
            base["uia_enabled"] = tv.uia_enabled
            base["ocr_enabled"] = tv.ocr_enabled
            base["yolo_enabled"] = tv.yolo_enabled
            base["som_enabled"] = tv.som_enabled
            base["window_aware_ocr"] = tv.window_aware_ocr
            base["local_vision"] = tv.local_vision
            base["visual_strategy"] = tv.visual_strategy
            base["llm_protocol"] = tv.llm_protocol
            base["planning"] = tv.planning
        return base

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

        return cfg
