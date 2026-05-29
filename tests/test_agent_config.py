"""
Tests for AgentConfig — resolve_auto(), from_dict(), agent_config_from_legacy().
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import pytest
from agent.config import AgentConfig, agent_config_from_legacy


class TestAgentConfig:
    def test_defaults(self):
        cfg = AgentConfig()
        assert cfg.llm_protocol == "auto"
        assert cfg.vision_mode == "auto"
        assert cfg.planning == "auto"
        assert cfg.screen_context == "auto"
        assert cfg.on_error == "retry"
        assert cfg.verify is True
        assert cfg.fast_mode is False
        assert cfg.image_dedup is True

    def test_resolve_auto_multimodal(self):
        """Multimodal model (MIMO) → text + direct + per_step + minimal"""
        cfg = AgentConfig()
        cfg.resolve_auto("mimo-v2-omni")
        assert cfg._resolved_llm_protocol == "text"
        assert cfg._resolved_vision_mode == "direct"
        assert cfg._resolved_planning == "per_step"
        assert cfg._resolved_screen_context == "minimal"
        assert cfg._resolved_on_error == "retry"
        assert cfg._multimodal is True

    def test_resolve_auto_text_only(self):
        """Text-only model (DeepSeek) → function_calling + som + pre_plan + full"""
        cfg = AgentConfig()
        cfg.resolve_auto("deepseek-chat")
        assert cfg._resolved_llm_protocol == "function_calling"
        assert cfg._resolved_vision_mode == "som"
        assert cfg._resolved_planning == "pre_plan"
        assert cfg._resolved_screen_context == "full"
        assert cfg._resolved_on_error == "retry"
        assert cfg._multimodal is False

    def test_resolve_auto_respects_explicit(self):
        """Explicit non-auto values should NOT be overwritten by resolve_auto"""
        cfg = AgentConfig(
            llm_protocol="text",
            vision_mode="som",
            planning="pre_plan",
            screen_context="full",
        )
        cfg.resolve_auto("deepseek-chat")  # text-only, would normally set function_calling
        assert cfg._resolved_llm_protocol == "text"  # kept
        assert cfg._resolved_vision_mode == "som"     # kept
        assert cfg._resolved_planning == "pre_plan"   # kept
        assert cfg._resolved_screen_context == "full"  # kept

    def test_fast_mode_sets_image_dedup(self):
        cfg = AgentConfig(fast_mode=True)
        cfg.resolve_auto("deepseek-chat")
        assert cfg.image_dedup is True

    def test_to_dict_from_dict_roundtrip(self):
        original = AgentConfig(
            llm_protocol="text", vision_mode="direct",
            planning="per_step", screen_context="minimal",
            on_error="skip", verify=False, fast_mode=True, image_dedup=False,
        )
        d = original.to_dict()
        restored = AgentConfig.from_dict(d)
        assert restored.llm_protocol == "text"
        assert restored.vision_mode == "direct"
        assert restored.planning == "per_step"
        assert restored.screen_context == "minimal"
        assert restored.on_error == "skip"
        assert restored.verify is False
        assert restored.fast_mode is True
        assert restored.image_dedup is False

    def test_from_dict_partial(self):
        cfg = AgentConfig.from_dict({"llm_protocol": "text"})
        assert cfg.llm_protocol == "text"
        assert cfg.vision_mode == "auto"  # default
        assert cfg.on_error == "retry"    # default

    def test_on_error_auto_resolves_to_retry(self):
        cfg = AgentConfig(on_error="auto")
        cfg.resolve_auto("deepseek-chat")
        assert cfg._resolved_on_error == "retry"

    def test_on_error_skip_respected(self):
        cfg = AgentConfig(on_error="skip")
        cfg.resolve_auto("mimo-v2-omni")
        assert cfg._resolved_on_error == "skip"

    def test_on_error_stop_respected(self):
        cfg = AgentConfig(on_error="stop")
        cfg.resolve_auto("mimo-v2-omni")
        assert cfg._resolved_on_error == "stop"

    def test_on_error_replan_respected(self):
        cfg = AgentConfig(on_error="replan")
        cfg.resolve_auto("mimo-v2-omni")
        assert cfg._resolved_on_error == "replan"

    # ── agent_config_from_legacy ──

    def test_legacy_mimo(self):
        cfg = agent_config_from_legacy(mimo=True, supervise=True, model_name="mimo-v2-omni")
        assert cfg.llm_protocol == "text"
        assert cfg._resolved_planning == "per_step"

    def test_legacy_direct(self):
        cfg = agent_config_from_legacy(mimo=False, supervise=False)
        assert cfg.planning == "pre_plan"
        assert cfg.fast_mode is True
        assert cfg.verify is False

    def test_legacy_supervise(self):
        cfg = agent_config_from_legacy(mimo=False, supervise=True)
        assert cfg.llm_protocol == "function_calling"
        assert cfg.planning == "pre_plan"

    def test_legacy_mimo_with_model(self):
        cfg = agent_config_from_legacy(mimo=True, supervise=False, model_name="mimo-v2-omni")
        assert cfg._multimodal is True
