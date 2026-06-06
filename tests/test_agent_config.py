"""
Tests for AgentConfig — general-purpose agent configuration.
- Resolution logic is tested via capability flags, not specific model names
- Serialization round-trips are exhaustive
- Model detection is tested by naming PATTERNS (keywords/prefixes), not model lists
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import pytest
from agent.config import AgentConfig, agent_config_from_legacy
from agent.prompts import is_multimodal_model as _is_multimodal_model


# ═══════════════════════════════════════════════════════════════════
#  Default values
# ═══════════════════════════════════════════════════════════════════

class TestDefaults:
    def test_all_auto_by_default(self):
        cfg = AgentConfig()
        assert cfg.llm_protocol == "auto"
        assert cfg.vision_mode == "auto"
        assert cfg.planning == "auto"
        assert cfg.screen_context == "auto"
        assert cfg.multimodal == "auto"

    def test_non_auto_defaults(self):
        cfg = AgentConfig()
        assert cfg.on_error == "retry"
        assert cfg.verify is True
        assert cfg.fast_mode is False
        assert cfg.image_dedup is True
        assert cfg.chat_mode is False
        assert cfg.reflection_enabled is True
        assert cfg.max_steps == 20
        assert cfg.temperature == 0.1


# ═══════════════════════════════════════════════════════════════════
#  Model detection by pattern (not by specific model names)
# ═══════════════════════════════════════════════════════════════════

class TestModelDetectionByPattern:
    def test_vision_keyword_detected(self):
        """Any model with 'vision' in name is multimodal."""
        assert _is_multimodal_model("any-vision-model") is True
        assert _is_multimodal_model("custom-vision-42") is True

    def test_omni_keyword_detected(self):
        """'omni' keyword signals multimodal capability."""
        assert _is_multimodal_model("x-omni") is True
        assert _is_multimodal_model("omni-v2") is True

    def test_multimodal_keyword(self):
        assert _is_multimodal_model("my-multimodal-model") is True

    def test_vl_prefix_detected(self):
        """'vl' in name signals vision-language model."""
        assert _is_multimodal_model("qwen-vl-plus") is True
        assert _is_multimodal_model("deepseek-vl2") is True

    def test_known_prefixes(self):
        """Prefix-based detection catches family naming."""
        assert _is_multimodal_model("gpt-4-anything") is True
        assert _is_multimodal_model("claude-3-something") is True
        assert _is_multimodal_model("gemini-2.0-pro") is True

    def test_deepseek_text_excluded(self):
        """Plain deepseek models are text-only despite 'deepseek' prefix."""
        assert _is_multimodal_model("deepseek-chat") is False
        assert _is_multimodal_model("deepseek-coder") is False

    def test_unknown_model_defaults_to_false(self):
        """Unknown model names should not be assumed multimodal."""
        assert _is_multimodal_model("totally-unknown-model") is False

    def test_empty_model_name(self):
        assert _is_multimodal_model("") is False

    def test_case_insensitive(self):
        assert _is_multimodal_model("GPT-4-VISION") is True


# ═══════════════════════════════════════════════════════════════════
#  resolve_auto — two capability paths
# ═══════════════════════════════════════════════════════════════════

class TestResolveAutoCapabilities:
    """Test resolution behavior via capability overrides, not model names."""

    def test_multimodal_path(self):
        """multimodal=true → direct vision + per-step planning + minimal context."""
        cfg = AgentConfig(multimodal="true")
        cfg.resolve_auto("irrelevant-model-name")
        assert cfg.get_resolved()["llm_protocol"] == "text"
        assert cfg.get_resolved()["vision_mode"] == "direct"
        assert cfg.get_resolved()["planning"] == "per_step"
        assert cfg.get_resolved()["screen_context"] == "minimal"

    def test_text_only_path(self):
        """multimodal=false → function_calling + som + pre_plan + full context."""
        cfg = AgentConfig(multimodal="false")
        cfg.resolve_auto("irrelevant-model-name")
        assert cfg.get_resolved()["llm_protocol"] == "function_calling"
        assert cfg.get_resolved()["vision_mode"] == "som"
        assert cfg.get_resolved()["planning"] == "pre_plan"
        assert cfg.get_resolved()["screen_context"] == "full"

    def test_explicit_values_survive_resolution(self):
        """Non-auto fields must not be overwritten by resolve_auto."""
        cfg = AgentConfig(
            llm_protocol="text",
            vision_mode="som",
            planning="pre_plan",
            screen_context="full",
            multimodal="false",  # would normally set function_calling
        )
        cfg.resolve_auto("any-model")
        assert cfg.get_resolved()["llm_protocol"] == "text"
        assert cfg.get_resolved()["vision_mode"] == "som"
        assert cfg.get_resolved()["planning"] == "pre_plan"
        assert cfg.get_resolved()["screen_context"] == "full"

    def test_deterministic_regardless_of_model_name(self):
        """Same capability flag + same config = same result for any model name."""
        for name in ["", "unknown", "gpt-5-super", "future-model-3000"]:
            c1 = AgentConfig(multimodal="true")
            c1.resolve_auto(name)
            c2 = AgentConfig(multimodal="false")
            c2.resolve_auto(name)
            assert c1._resolved_llm_protocol == "text"
            assert c2._resolved_llm_protocol == "function_calling"

    def test_multimodal_auto_resolves_via_model_name(self):
        """multimodal=auto delegates to _is_multimodal_model."""
        cfg = AgentConfig(multimodal="auto")
        cfg.resolve_auto("some-vision-model")
        assert cfg.get_resolved()["vision_mode"] == "direct"  # detected as multimodal


# ═══════════════════════════════════════════════════════════════════
#  on_error resolution
# ═══════════════════════════════════════════════════════════════════

class TestOnErrorResolution:
    def test_auto_resolves_to_retry(self):
        cfg = AgentConfig(on_error="auto")
        cfg.resolve_auto("any-model")
        assert cfg.get_resolved()["on_error"] == "retry"

    @pytest.mark.parametrize("strategy", ["skip", "stop", "replan"])
    def test_explicit_strategies_respected(self, strategy):
        cfg = AgentConfig(on_error=strategy)
        cfg.resolve_auto("any-model")
        assert cfg.get_resolved()["on_error"] == strategy


# ═══════════════════════════════════════════════════════════════════
#  visual_strategy
# ═══════════════════════════════════════════════════════════════════

class TestVisualStrategyResolution:
    def test_auto_multimodal_direct(self):
        cfg = AgentConfig(visual_strategy="auto", multimodal="true")
        cfg.resolve_auto("any")
        assert cfg.get_resolved()["vision_mode"] == "direct"

    def test_auto_text_only_som(self):
        cfg = AgentConfig(visual_strategy="auto", multimodal="false")
        cfg.resolve_auto("any")
        assert cfg.get_resolved()["vision_mode"] == "som"

    @pytest.mark.parametrize("strategy,expected", [
        ("ocr_llm", "ocr_llm"),
        ("blind_guess", "direct"),
        ("som", "som"),
        ("cli_path", "som"),
    ])
    def test_explicit_strategies(self, strategy, expected):
        cfg = AgentConfig(visual_strategy=strategy)
        cfg.resolve_auto("any")
        assert cfg.get_resolved()["vision_mode"] == expected


# ═══════════════════════════════════════════════════════════════════
#  Serialization
# ═══════════════════════════════════════════════════════════════════

class TestSerialization:
    def test_to_dict_contains_all_keys(self):
        cfg = AgentConfig()
        d = cfg.to_dict()
        # All public fields should be serialized
        assert "llm_protocol" in d
        assert "vision_mode" in d
        assert "on_error" in d
        assert "chat_mode" in d
        assert "visual_strategy" in d
        assert "reflection_enabled" in d

    def test_to_dict_from_dict_full_roundtrip(self):
        original = AgentConfig(
            llm_protocol="text",
            vision_mode="direct",
            planning="per_step",
            screen_context="minimal",
            on_error="skip",
            verify=False,
            fast_mode=True,
            image_dedup=False,
            chat_mode=True,
            visual_strategy="ocr_llm",
            reflection_enabled=False,
            max_steps=50,
            temperature=0.5,
        )
        restored = AgentConfig.from_dict(original.to_dict())
        for field in [
            "llm_protocol", "vision_mode", "planning", "screen_context",
            "on_error", "verify", "fast_mode", "image_dedup",
            "chat_mode", "visual_strategy", "reflection_enabled",
            "max_steps", "temperature",
        ]:
            assert getattr(restored, field) == getattr(original, field), f"Mismatch: {field}"

    def test_from_dict_partial_uses_defaults(self):
        cfg = AgentConfig.from_dict({"llm_protocol": "text"})
        assert cfg.llm_protocol == "text"
        assert cfg.vision_mode == "auto"      # default
        assert cfg.on_error == "retry"         # default
        assert cfg.chat_mode is False          # default
        assert cfg.reflection_enabled is True  # default

    def test_empty_from_dict_produces_defaults(self):
        cfg = AgentConfig.from_dict({})
        assert cfg.llm_protocol == "auto"
        assert cfg.vision_mode == "auto"
        assert cfg.on_error == "retry"

    # Round-trips for individual fields

    @pytest.mark.parametrize("val", [True, False])
    def test_chat_mode_roundtrip(self, val):
        d = AgentConfig(chat_mode=val).to_dict()
        assert AgentConfig.from_dict(d).chat_mode == val

    @pytest.mark.parametrize("val", ["auto", "som", "ocr_llm", "blind_guess", "cli_path"])
    def test_visual_strategy_roundtrip(self, val):
        d = AgentConfig(visual_strategy=val).to_dict()
        assert AgentConfig.from_dict(d).visual_strategy == val

    @pytest.mark.parametrize("val", [True, False])
    def test_reflection_enabled_roundtrip(self, val):
        d = AgentConfig(reflection_enabled=val).to_dict()
        assert AgentConfig.from_dict(d).reflection_enabled == val

    def test_fast_mode_sets_image_dedup(self):
        cfg = AgentConfig(fast_mode=True)
        cfg.resolve_auto("any")
        assert cfg.image_dedup is True

    def test_computer_use_overrides_multimodal(self):
        cfg = AgentConfig(llm_protocol="computer_use", multimodal="false")
        cfg.resolve_auto("any")
        assert cfg.get_resolved()["llm_protocol"] == "computer_use"
        assert cfg.get_resolved()["vision_mode"] == "direct"


# ═══════════════════════════════════════════════════════════════════
#  Legacy conversion
# ═══════════════════════════════════════════════════════════════════

class TestLegacyConversion:
    def test_mimo_mode(self):
        cfg = agent_config_from_legacy(mimo=True, supervise=True)
        assert cfg.llm_protocol == "text"

    def test_direct_mode(self):
        cfg = agent_config_from_legacy(mimo=False, supervise=False)
        assert cfg.planning == "pre_plan"
        assert cfg.fast_mode is True
        assert cfg.verify is False

    def test_supervise_mode(self):
        cfg = agent_config_from_legacy(mimo=False, supervise=True)
        assert cfg.llm_protocol == "function_calling"
        assert cfg.planning == "pre_plan"
