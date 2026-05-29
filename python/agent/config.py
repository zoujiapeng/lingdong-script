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
    llm_protocol: str = "auto"          # "auto"|"text"|"function_calling"

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

        # Apply boolean-derived behavior
        if self.fast_mode:
            self.image_dedup = True

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
