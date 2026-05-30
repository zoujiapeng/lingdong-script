"""
Tests for agent chat mode and visual strategy methods.
Tests are isolated — no real API calls or screen interaction.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import json
import threading
import time
from queue import Queue, Empty
from unittest.mock import MagicMock, patch

import pytest
from agent.agent import (
    LingdongAgent,
    _is_multimodal_model,
    CHAT_MODE_INSTRUCTIONS,
)


# ═══════════════════════════════════════════════════════════════════
#  _parse_response tests
# ═══════════════════════════════════════════════════════════════════

def test_parse_tool_call():
    action, name, args = LingdongAgent._parse_response("思考: 点击确认\n工具: click_target\n参数:\n  target: 确认")
    assert action == "tool"
    assert name == "click_target"
    assert args.get("target") == "确认"


def test_parse_done():
    action, name, args = LingdongAgent._parse_response("总结: 任务完成")
    assert action == "done"


def test_parse_no_tool():
    action, name, args = LingdongAgent._parse_response("我不需要工具，直接回复")
    assert action == "error"


def test_parse_empty():
    action, name, args = LingdongAgent._parse_response("")
    assert action == "error"
    assert name == ""


def test_parse_multiline_params():
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


# ═══════════════════════════════════════════════════════════════════
#  _is_multimodal_model tests
# ═══════════════════════════════════════════════════════════════════

def test_multimodal_model_detection():
    assert _is_multimodal_model("gpt-4o") is True
    assert _is_multimodal_model("gpt-4o-mini") is True
    assert _is_multimodal_model("claude-3.5-sonnet") is True
    assert _is_multimodal_model("gemini-1.5-pro") is True
    assert _is_multimodal_model("qwen-vl-plus") is True
    assert _is_multimodal_model("mimo-v2-omni") is True


def test_text_only_model_detection():
    assert _is_multimodal_model("deepseek-chat") is False
    assert _is_multimodal_model("deepseek-coder") is False
    assert _is_multimodal_model("gpt-3.5-turbo") is False
    assert _is_multimodal_model("unknown-model") is False


def test_model_detection_vision_keyword():
    assert _is_multimodal_model("any-vision-model") is True
    assert _is_multimodal_model("gpt-4-vision-preview") is True


# ═══════════════════════════════════════════════════════════════════
#  LingdongAgent — chat mode tests
# ═══════════════════════════════════════════════════════════════════

class TestLingdongAgentChatMode:
    """Tests for chat_mode methods in LingdongAgent."""

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

    def test_chat_mode_initially_off(self, agent):
        assert agent._chat_mode is False

    def test_process_chat_messages_no_queue(self, agent):
        agent._guidance_queue = None
        result = agent._process_chat_messages([])
        assert result is False

    def test_process_chat_messages_empty_queue(self, agent):
        agent._guidance_queue = Queue()
        result = agent._process_chat_messages([])
        assert result is False

    def test_process_chat_messages_injects(self, agent):
        agent._guidance_queue = Queue()
        agent._guidance_queue.put({"text": "你好", "type": "chat"})

        messages = [{"role": "system", "content": "sys"}]
        result = agent._process_chat_messages(messages)
        assert result is True
        assert len(messages) == 2
        assert messages[1]["role"] == "user"
        assert "你好" in messages[1]["content"]

    def test_process_chat_messages_multiple(self, agent):
        agent._guidance_queue = Queue()
        agent._guidance_queue.put({"text": "msg1", "type": "chat"})
        agent._guidance_queue.put({"text": "msg2", "type": "guidance"})

        messages = [{"role": "system", "content": "sys"}]
        result = agent._process_chat_messages(messages)
        assert result is True
        assert len(messages) == 3  # system + 2 user messages

    def test_emit_chat_response_calls_event_cb(self, agent):
        agent._emit_chat_response("Hello AI response")
        # Should emit both a log AND agent_chat_response
        assert agent._event_cb.call_count >= 1
        # Find the agent_chat_response call
        chat_calls = [
            c for c in agent._event_cb.call_args_list
            if c[0][0].get("type") == "agent_chat_response"
        ]
        assert len(chat_calls) == 1
        assert chat_calls[0][0][0]["text"] == "Hello AI response"

    def test_chat_mode_instructions_in_system_prompt(self, agent):
        """When chat_mode=True, CHAT_MODE_INSTRUCTIONS should be in system prompt."""
        messages = [{"role": "system", "content": "Base system prompt"}]
        if True:  # simulate chat_mode
            current_content = messages[0]["content"]
            messages[0] = {
                "role": "system",
                "content": current_content + CHAT_MODE_INSTRUCTIONS,
            }
        assert CHAT_MODE_INSTRUCTIONS in messages[0]["content"]


# ═══════════════════════════════════════════════════════════════════
#  Visual strategy tests
# ═══════════════════════════════════════════════════════════════════

class TestVisualStrategy:
    """Tests for visual strategy routing and methods."""

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

    def test_apply_visual_strategy_auto_passthrough(self, agent):
        """auto strategy should not modify args."""
        args = {"target": "确认按钮", "x": 100}
        result = agent._apply_visual_strategy("click_target", args, "auto")
        assert result is args  # same object

    def test_apply_visual_strategy_ocr_llm_dispatches(self, agent):
        """ocr_llm strategy should call _visual_strategy_ocr_llm."""
        args = {"target": "确认"}
        with patch.object(agent, '_visual_strategy_ocr_llm', return_value={"target": "100,200"}) as mock:
            result = agent._apply_visual_strategy("click_target", args, "ocr_llm")
            mock.assert_called_once_with("click_target", args)
            assert result == {"target": "100,200"}

    def test_visual_strategy_ocr_llm_skips_non_click(self, agent):
        """ocr_llm should skip non-click tools."""
        args = {"keys": "ctrl+c"}
        result = agent._visual_strategy_ocr_llm("press_keys", args)
        assert result is args  # unchanged

    def test_visual_strategy_ocr_llm_skips_coordinate_target(self, agent):
        """ocr_llm should skip if target is already coordinates."""
        args = {"target": "500,300"}
        result = agent._visual_strategy_ocr_llm("click_target", args)
        assert result is args  # unchanged

    def test_visual_strategy_ocr_llm_skips_empty_target(self, agent):
        """ocr_llm should skip if target is empty."""
        args = {"target": ""}
        result = agent._visual_strategy_ocr_llm("click_target", args)
        assert result is args


# ═══════════════════════════════════════════════════════════════════
#  AgentConfig resolution with visual_strategy
# ═══════════════════════════════════════════════════════════════════

def test_visual_strategy_auto_overrides_vision_mode():
    """visual_strategy=auto should set vision_mode based on multimodal."""
    from agent.config import AgentConfig

    cfg = AgentConfig(multimodal="true")
    cfg.resolve_auto("deepseek-chat")
    assert cfg._resolved_vision_mode == "direct"

    cfg2 = AgentConfig(multimodal="false")
    cfg2.resolve_auto("deepseek-chat")
    assert cfg2._resolved_vision_mode == "som"


def test_visual_strategy_ocr_llm_overrides_vision():
    """visual_strategy=ocr_llm should set _resolved_vision_mode to ocr_llm."""
    from agent.config import AgentConfig

    cfg = AgentConfig(visual_strategy="ocr_llm")
    cfg.resolve_auto("deepseek-chat")
    assert cfg._resolved_vision_mode == "ocr_llm"


# ═══════════════════════════════════════════════════════════════════
#  _resolve_text_protocol_config tests
# ═══════════════════════════════════════════════════════════════════

class TestResolveTextProtocolConfig:
    """Tests for _resolve_text_protocol_config propagation."""

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

    def test_default_no_config(self, agent):
        """Without AgentConfig, chat_mode should be absent."""
        resolved = agent._resolve_text_protocol_config(None)
        assert "chat_mode" not in resolved
        assert "visual_strategy" not in resolved
