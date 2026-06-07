"""
Tests for AgentConfig — general-purpose agent configuration.
- Resolution logic for 4 execution modes
- Serialization round-trips
- Model detection patterns
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import pytest
from agent.config import AgentConfig
from agent.prompts import is_multimodal_model as _is_multimodal_model


# ═══════════════════════════════════════════════════════════════════
#  Default values
# ═══════════════════════════════════════════════════════════════════

class TestDefaults:
    def test_default_execution_mode_is_flow_chart(self):
        cfg = AgentConfig()
        assert cfg.execution_mode == "flow_chart"

    def test_non_auto_defaults(self):
        cfg = AgentConfig()
        assert cfg.on_error == "retry"
        assert cfg.max_steps == 20
        assert cfg.temperature == 0.1


# ═══════════════════════════════════════════════════════════════════
#  Model detection by pattern (not by specific model names)
# ═══════════════════════════════════════════════════════════════════

class TestModelDetectionByPattern:
    def test_vision_keyword_detected(self):
        assert _is_multimodal_model("any-vision-model") is True
        assert _is_multimodal_model("custom-vision-42") is True

    def test_omni_keyword_detected(self):
        assert _is_multimodal_model("x-omni") is True
        assert _is_multimodal_model("omni-v2") is True

    def test_multimodal_keyword(self):
        assert _is_multimodal_model("my-multimodal-model") is True

    def test_vl_prefix_detected(self):
        assert _is_multimodal_model("qwen-vl-plus") is True
        assert _is_multimodal_model("deepseek-vl2") is True

    def test_known_prefixes(self):
        assert _is_multimodal_model("gpt-4-anything") is True
        assert _is_multimodal_model("claude-3-something") is True
        assert _is_multimodal_model("gemini-2.0-pro") is True

    def test_deepseek_text_excluded(self):
        assert _is_multimodal_model("deepseek-chat") is False
        assert _is_multimodal_model("deepseek-coder") is False

    def test_unknown_model_defaults_to_false(self):
        assert _is_multimodal_model("totally-unknown-model") is False

    def test_empty_model_name(self):
        assert _is_multimodal_model("") is False

    def test_case_insensitive(self):
        assert _is_multimodal_model("GPT-4-VISION") is True


# ═══════════════════════════════════════════════════════════════════
#  resolve() — 4 execution modes
# ═══════════════════════════════════════════════════════════════════

class TestResolve:
    def test_flow_chart_mode(self):
        """flow_chart → multimodal=False, flow_chart_mode=True."""
        cfg = AgentConfig(execution_mode="flow_chart")
        r = cfg.resolve()
        assert r["flow_chart_mode"] is True
        assert r["multimodal"] is False
        assert r["supervise"] is True

    def test_multimodal_mode(self):
        """multimodal → direct vision + per_step planning + minimal context."""
        cfg = AgentConfig(execution_mode="multimodal")
        r = cfg.resolve()
        assert r["multimodal"] is True
        assert r["vision_mode"] == "direct"
        assert r["planning"] == "per_step"
        assert r["screen_context"] == "minimal"
        assert r["som_enabled"] is False
        assert r["visual_strategy"] == "direct"

    def test_text_mode(self):
        """text → UIA/OCR/SoM + per_step planning + full context."""
        cfg = AgentConfig(execution_mode="text")
        r = cfg.resolve()
        assert r["multimodal"] is False
        assert r["llm_protocol"] == "text"
        assert r["planning"] == "per_step"
        assert r["screen_context"] == "full"
        assert r["visual_strategy"] == "som"

    def test_text_mode_subconfig(self):
        """text mode respects TextVisionConfig overrides."""
        cfg = AgentConfig(execution_mode="text")
        cfg.text_vision_cfg.uia_enabled = True
        cfg.text_vision_cfg.visual_strategy = "ocr_llm"
        r = cfg.resolve()
        assert r["uia_enabled"] is True
        assert r["visual_strategy"] == "ocr_llm"

    def test_offline_mode(self):
        """offline → multimodal=False, planning=pre_plan."""
        cfg = AgentConfig(execution_mode="offline")
        r = cfg.resolve()
        assert r["multimodal"] is False
        assert r["llm_protocol"] == "text"
        assert r["planning"] == "pre_plan"


# ═══════════════════════════════════════════════════════════════════
#  on_error resolution
# ═══════════════════════════════════════════════════════════════════

class TestOnErrorResolution:
    def test_default_resolves_to_retry(self):
        cfg = AgentConfig(execution_mode="text")
        r = cfg.resolve()
        assert r["on_error"] == "retry"

    @pytest.mark.parametrize("strategy", ["skip", "stop", "replan"])
    def test_explicit_strategies_respected(self, strategy):
        cfg = AgentConfig(execution_mode="text", on_error=strategy)
        r = cfg.resolve()
        assert r["on_error"] == strategy


# ═══════════════════════════════════════════════════════════════════
#  visual_strategy
# ═══════════════════════════════════════════════════════════════════

class TestVisualStrategyResolution:
    def test_multimodal_uses_direct(self):
        cfg = AgentConfig(execution_mode="multimodal")
        r = cfg.resolve()
        assert r["visual_strategy"] == "direct"

    def test_text_respects_subconfig(self):
        cfg = AgentConfig(execution_mode="text")
        cfg.text_vision_cfg.visual_strategy = "ocr_llm"
        r = cfg.resolve()
        assert r["visual_strategy"] == "ocr_llm"

    def test_text_default_som(self):
        cfg = AgentConfig(execution_mode="text")
        r = cfg.resolve()
        assert r["visual_strategy"] == "som"


# ═══════════════════════════════════════════════════════════════════
#  Serialization
# ═══════════════════════════════════════════════════════════════════

class TestSerialization:
    def test_to_dict_contains_core_keys(self):
        cfg = AgentConfig()
        d = cfg.to_dict()
        assert "execution_mode" in d
        assert "memory_enabled" in d
        assert "on_error" in d
        assert "max_steps" in d
        assert "temperature" in d

    def test_to_dict_from_dict_full_roundtrip(self):
        original = AgentConfig(
            execution_mode="text",
            memory_enabled=True,
            on_error="skip",
            max_steps=50,
            temperature=0.5,
        )
        original.text_vision_cfg.uia_enabled = True
        original.text_vision_cfg.visual_strategy = "ocr_llm"

        restored = AgentConfig.from_dict(original.to_dict())
        assert restored.execution_mode == original.execution_mode
        assert restored.memory_enabled == original.memory_enabled
        assert restored.on_error == original.on_error
        assert restored.max_steps == original.max_steps
        assert restored.temperature == original.temperature

    def test_from_dict_partial_uses_defaults(self):
        cfg = AgentConfig.from_dict({"execution_mode": "text"})
        assert cfg.execution_mode == "text"
        assert cfg.on_error == "retry"
        assert cfg.max_steps == 20

    def test_empty_from_dict_produces_defaults(self):
        cfg = AgentConfig.from_dict({})
        assert cfg.execution_mode == "flow_chart"
        assert cfg.on_error == "retry"

    def test_from_dict_sets_text_subconfig(self):
        cfg = AgentConfig.from_dict({
            "execution_mode": "text",
            "uia_enabled": True,
            "visual_strategy": "ocr_llm",
        })
        assert cfg.text_vision_cfg.uia_enabled is True
        assert cfg.text_vision_cfg.visual_strategy == "ocr_llm"

    def test_from_dict_sets_multimodal_subconfig(self):
        cfg = AgentConfig.from_dict({
            "execution_mode": "multimodal",
            "screenshot_quality": 85,
        })
        assert cfg.multimodal_cfg.screenshot_quality == 85

    def test_flow_chart_supervise_roundtrip(self):
        cfg = AgentConfig(execution_mode="flow_chart")
        cfg.flow_chart_cfg.supervise = False
        d = cfg.to_dict()
        restored = AgentConfig.from_dict(d)
        assert restored.flow_chart_cfg.supervise is False
