"""
Tests for agent chat mode and visual strategy methods.

Tests verify parsing, routing, and configuration propagation —
not model-specific behavior or specific software assumptions.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import json
import threading
import time
from queue import Queue, Empty
from unittest.mock import MagicMock, patch

import pytest
from agent.agent import LingdongAgent
from agent.prompts import (
    is_multimodal_model as _is_multimodal_model,
    CHAT_MODE_INSTRUCTIONS,
)


# ═══════════════════════════════════════════════════════════════════
#  _parse_response — general-purpose text protocol parsing
# ═══════════════════════════════════════════════════════════════════

class TestParseResponse:
    def test_parse_tool_call(self):
        action, name, args = LingdongAgent._parse_response(
            "思考: 点击确认\n工具: click_target\n参数:\n  target: 确认"
        )
        assert action == "tool"
        assert name == "click_target"
        assert args.get("target") == "确认"

    def test_parse_done(self):
        action, name, args = LingdongAgent._parse_response("总结: 任务完成")
        assert action == "done"

    def test_parse_no_tool(self):
        action, name, args = LingdongAgent._parse_response("我不需要工具，直接回复")
        assert action == "error"

    def test_parse_empty(self):
        action, name, args = LingdongAgent._parse_response("")
        assert action == "error"
        assert name == ""

    def test_parse_multiline_params(self):
        content = """思考: 写文件
工具: file_ops
参数:
action: write
path: test.txt
content: |
  Hello World
  Line 2
总结: 完成"""
        action, name, args = LingdongAgent._parse_response(content)
        assert action == "tool"
        assert name == "file_ops"
        assert args.get("action") == "write"
        assert "Hello World" in args.get("content", "")

    def test_parse_without_think_still_works(self):
        """Tool call without '思考:' prefix should still parse."""
        action, name, args = LingdongAgent._parse_response("工具: click_target\n参数:\n  x: 100\n  y: 200")
        assert action == "tool"
        assert name == "click_target"

    def test_parse_done_various_formats(self):
        """'总结:' without '工具:' is recognized as done."""
        for text in ["总结: 完成", "总结: 任务已完成"]:
            action, _, _ = LingdongAgent._parse_response(text)
            assert action == "done", f"Failed for: {text}"
        # Without '总结:' prefix it's not recognized as done
        action, _, _ = LingdongAgent._parse_response("完成")
        assert action != "done"


# ═══════════════════════════════════════════════════════════════════
#  _is_multimodal_model — pattern-based detection
# ═══════════════════════════════════════════════════════════════════

class TestMultimodalDetection:
    def test_vision_keyword(self):
        assert _is_multimodal_model("any-vision-model") is True

    def test_omni_keyword(self):
        assert _is_multimodal_model("my-omni-model") is True

    def test_vl_in_name(self):
        assert _is_multimodal_model("qwen-vl-plus") is True

    def test_text_only_unknown(self):
        assert _is_multimodal_model("unknown-text-model") is False

    def test_deepseek_excluded(self):
        assert _is_multimodal_model("deepseek-chat") is False

    def test_empty_string(self):
        assert _is_multimodal_model("") is False

    def test_case_insensitive(self):
        assert _is_multimodal_model("CLAUDE-4-VISION") is True


# ═══════════════════════════════════════════════════════════════════
#  Chat mode
# ═══════════════════════════════════════════════════════════════════

class TestChatMode:
    """Chat mode behavior — user interaction during agent execution."""

    @pytest.fixture
    def agent(self):
        agent = LingdongAgent(
            api_key="sk-test-key",
            api_base="https://test.api/v1",
            model="deepseek-chat",
        )
        agent._registry_loaded = True
        agent._system_prompt = "Test system prompt"
        agent._event_cb = MagicMock()
        return agent

    def test_initially_off(self, agent):
        assert agent._chat_mode is False

    def test_no_queue_returns_false(self, agent):
        agent._guidance_queue = None
        assert agent._process_chat_messages([]) is False

    def test_empty_queue_returns_false(self, agent):
        agent._guidance_queue = Queue()
        assert agent._process_chat_messages([]) is False

    def test_chat_message_injected(self, agent):
        agent._guidance_queue = Queue()
        agent._guidance_queue.put({"text": "user message", "type": "chat"})
        messages = [{"role": "system", "content": "sys"}]
        assert agent._process_chat_messages(messages) is True
        assert len(messages) == 2
        assert messages[1]["role"] == "user"

    def test_multiple_messages_injected_in_order(self, agent):
        agent._guidance_queue = Queue()
        agent._guidance_queue.put({"text": "first", "type": "chat"})
        agent._guidance_queue.put({"text": "second", "type": "guidance"})
        messages = [{"role": "system", "content": "sys"}]
        assert agent._process_chat_messages(messages) is True
        assert len(messages) == 3  # system + 2 user messages
        # verify order preserved
        contents = [m.get("content", "") for m in messages]
        assert "first" in contents[1]
        assert "second" in contents[2]

    def test_emit_chat_response_calls_event_cb(self, agent):
        agent._emit_chat_response("Hello AI response")
        chat_calls = [
            c for c in agent._event_cb.call_args_list
            if c[0][0].get("type") == "agent_chat_response"
        ]
        assert len(chat_calls) == 1
        assert chat_calls[0][0][0]["text"] == "Hello AI response"


# ═══════════════════════════════════════════════════════════════════
#  Visual strategies — all four routing paths
# ═══════════════════════════════════════════════════════════════════

class TestVisualStrategyRouting:
    """Verify all visual strategies route to the correct handler."""

    @pytest.fixture
    def agent(self):
        agent = LingdongAgent(
            api_key="sk-test-key",
            api_base="https://test.api/v1",
            model="deepseek-chat",
        )
        agent._event_cb = MagicMock()
        agent._registry_loaded = True
        return agent

    def test_auto_passthrough(self, agent):
        """auto strategy should not route to any specific handler."""
        args = {"target": "確認", "x": 100}
        result = agent._apply_visual_strategy("click_target", args, "auto")
        assert result is args  # unchanged passthrough

    def test_ocr_llm_routes_to_handler(self, agent):
        """ocr_llm strategy should call _visual_strategy_ocr_llm."""
        with patch.object(agent, '_visual_strategy_ocr_llm', return_value={"resolved": True}) as mock:
            result = agent._apply_visual_strategy("click_target", {"target": "確認"}, "ocr_llm")
            mock.assert_called_once()
            assert result == {"resolved": True}

    def test_som_falls_through(self, agent):
        """som strategy has no dedicated handler — args pass through."""
        args = {"target": "確認"}
        result = agent._apply_visual_strategy("click_target", args, "som")
        assert result is args

    def test_cli_path_falls_through(self, agent):
        """cli_path strategy has no dedicated handler — handled in supervisor."""
        args = {"target": "確認"}
        result = agent._apply_visual_strategy("click_target", args, "cli_path")
        assert result is args

    def test_blind_guess_falls_through(self, agent):
        """blind_guess strategy has no dedicated handler — uses existing path."""
        args = {"target": "確認"}
        result = agent._apply_visual_strategy("click_target", args, "blind_guess")
        assert result is args

    def test_unknown_strategy_falls_back_to_auto(self, agent):
        """Unknown strategy name should fall through to auto."""
        result = agent._apply_visual_strategy("click_target", {"target": "x"}, "nonexistent")
        assert result is not None


class TestOcrLlmHandler:
    """Unit behavior of the OCR+LLM visual strategy handler."""

    @pytest.fixture
    def agent(self):
        agent = LingdongAgent(
            api_key="sk-test-key",
            api_base="https://test.api/v1",
            model="deepseek-chat",
        )
        agent._event_cb = MagicMock()
        agent._registry_loaded = True
        return agent

    def test_skips_non_click_tools(self, agent):
        args = {"keys": "ctrl+c"}
        result = agent._visual_strategy_ocr_llm("press_keys", args)
        assert result is args  # unchanged

    def test_skips_coordinate_target(self, agent):
        args = {"target": "500,300"}
        result = agent._visual_strategy_ocr_llm("click_target", args)
        assert result is args  # already resolved

    def test_skips_empty_target(self, agent):
        args = {"target": ""}
        result = agent._visual_strategy_ocr_llm("click_target", args)
        assert result is args  # nothing to resolve


# ═══════════════════════════════════════════════════════════════════
#  Config propagation
# ═══════════════════════════════════════════════════════════════════

class TestConfigPropagation:
    @pytest.fixture
    def agent(self):
        return LingdongAgent(
            api_key="sk-test-key",
            api_base="https://test.api/v1",
            model="deepseek-chat",
        )

    def test_propagates_chat_mode(self, agent):
        from agent.config import AgentConfig
        cfg = AgentConfig(chat_mode=True)
        cfg.resolve_auto("deepseek-chat")
        resolved = agent._resolve_text_protocol_config(cfg)
        assert resolved.get("chat_mode") is True

    def test_propagates_visual_strategy(self, agent):
        from agent.config import AgentConfig
        cfg = AgentConfig(visual_strategy="ocr_llm")
        cfg.resolve_auto("deepseek-chat")
        resolved = agent._resolve_text_protocol_config(cfg)
        assert resolved.get("visual_strategy") == "ocr_llm"

    def test_no_config_returns_defaults(self, agent):
        resolved = agent._resolve_text_protocol_config(None)
        assert "chat_mode" not in resolved
        assert "visual_strategy" not in resolved
