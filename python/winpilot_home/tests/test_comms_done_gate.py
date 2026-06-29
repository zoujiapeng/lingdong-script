"""Tests for communication task done-gate fixes.

Covers:
1. No-payload communication tasks require read_text evidence before done.
2. With-payload tasks still check the payload is visible.
3. Communication prompt hint is injected regardless of payload presence.
"""
import threading
from unittest.mock import MagicMock, patch

import pytest


# ---- Helpers -----------------------------------------------------------

def _make_runner(task: str):
    """Create an AgentRunner in a lightweight state (no real config needed)."""
    from winpilot.agent.loop import AgentRunner
    runner = AgentRunner(task=task, stop_event=threading.Event())
    return runner


# ---- done-gate: no-payload path ----------------------------------------

def test_comms_done_gate_no_payload_requires_read_text():
    """Without quoted payload, done must be rejected if no read_text evidence."""
    runner = _make_runner("打开QQ找我的手机持续聊天")
    # No recent_results at all → should reject
    runner._recent_results = []
    runner._done_rejects = 0

    # Patch the expensive parts: no real LLM call, no real observe.
    with patch.object(runner.session, "hwnd", None), \
         patch("winpilot.agent.loop.fusion") as mock_fusion:
        mock_snap = MagicMock()
        mock_snap.to_text.return_value = "QQ 聊天界面"
        mock_fusion.observe.return_value = mock_snap
        client = MagicMock()  # judge LLM not reached because early reject

        result = runner._verify_done(client, steps_left=10)

    assert result is False
    assert runner._done_rejects == 1


def test_comms_done_gate_no_payload_passes_with_read_text():
    """Without quoted payload, done is allowed when read_text has real chat content."""
    runner = _make_runner("打开QQ找我的手机持续聊天")
    # Simulate a real chat window read_text: long enough lines that look like
    # actual message bubbles, not a short contacts-list preview.
    runner._recent_results = [(
        "read_text",
        "你好 在吗 收到了吗 今天怎么样\n"
        "2025-06-27 16:00  你好，这是一条很长的测试消息，让我来测试一下这个功能是否正常工作\n"
        "2025-06-27 16:01  好的，已经收到了你发来的消息，一切正常，感谢你的测试\n"
        "2025-06-27 16:02  没问题，有什么需要帮忙的请继续发消息给我哦\n"
    )]
    runner._done_rejects = 0

    with patch.object(runner.session, "hwnd", None), \
         patch("winpilot.agent.loop.fusion") as mock_fusion, \
         patch("winpilot.agent.loop.intent") as mock_intent:
        mock_snap = MagicMock()
        mock_snap.to_text.return_value = "QQ 聊天界面"
        mock_fusion.observe.return_value = mock_snap
        mock_intent.perception_mode.return_value = "visual"
        # Judge says done.
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content='{"done": true, "reason": "聊天窗口已打开且有内容"}',
                reasoning_content="",
            ))]
        )
        result = runner._verify_done(client, steps_left=10)

    assert result is True


# ---- done-gate: with-payload path (existing behaviour, must not regress) ---

def test_comms_done_gate_with_payload_rejects_when_not_on_screen():
    runner = _make_runner('用QQ发消息给我的手机说"你好吗"')
    runner._recent_results = []
    runner._done_rejects = 0

    with patch.object(runner.session, "hwnd", None), \
         patch("winpilot.agent.loop.fusion") as mock_fusion, \
         patch("winpilot.agent.loop.intent") as mock_intent:
        mock_snap = MagicMock()
        # Screen does NOT contain the payload text.
        mock_snap.to_text.return_value = "QQ 联系人列表"
        mock_fusion.observe.return_value = mock_snap
        mock_intent.perception_mode.return_value = "visual"
        client = MagicMock()

        result = runner._verify_done(client, steps_left=10)

    assert result is False
    assert runner._done_rejects == 1


# ---- prompt hint injection: must fire even without payload -------------

def test_comms_prompt_hint_injected_without_payload():
    """run() should inject a communication hint even when no quoted text."""
    from winpilot.agent.loop import AgentRunner
    runner = AgentRunner(task="打开QQ找我的手机持续聊天",
                         stop_event=threading.Event())

    # Simulate the message assembly part of run() (up to the hint injection).
    # We stop before actually calling the LLM by patching _client to raise.
    with patch("winpilot.agent.loop._client", side_effect=RuntimeError("no key")), \
         patch("winpilot.agent.loop.playbooks"), \
         patch("winpilot.agent.loop.experience") as mock_exp, \
         patch("winpilot.agent.loop.vlm") as mock_vlm, \
         patch("winpilot.agent.loop.prompts") as mock_prompts:
        mock_exp.hints_with_ids.return_value = ("", [])
        mock_exp.reusable_script_for.return_value = (None, None)
        mock_vlm.is_configured.return_value = False
        mock_prompts.system_prompt.return_value = "sys"
        runner.run()  # will raise then set status=error

    # Check that a [通信任务提示] message was injected.
    hint_msgs = [m for m in runner.messages
                 if m.get("role") == "user" and "[通信任务提示]" in (m.get("content") or "")]
    assert len(hint_msgs) == 1
    assert "read_text" in hint_msgs[0]["content"] or "done" in hint_msgs[0]["content"]


# ---- reflect loop ------------------------------------------------------

def test_reflect_injected_at_interval():
    """_maybe_reflect should inject a message at step=8 (default interval)."""
    runner = _make_runner("打开QQ找我的手机持续聊天")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner._last_reflect_step = 0
    runner._consecutive_unverified = 0

    with patch.object(runner.session, "snapshot", None), \
         patch("winpilot.agent.loop.CONFIG") as mock_cfg, \
         patch.object(runner.session, "world_enabled", False):
        mock_cfg.get.return_value = 8  # reflect_interval
        runner._maybe_reflect(step=8)

    reflect_msgs = [m for m in runner.messages
                    if "[反思检查点" in (m.get("content") or "")]
    assert len(reflect_msgs) == 1


def test_reflect_injected_on_consecutive_unverified():
    """_maybe_reflect should inject when consecutive_unverified >= 3."""
    runner = _make_runner("打开QQ找我的手机持续聊天")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner._last_reflect_step = 0
    runner._consecutive_unverified = 3

    with patch.object(runner.session, "snapshot", None), \
         patch("winpilot.agent.loop.CONFIG") as mock_cfg, \
         patch.object(runner.session, "world_enabled", False):
        mock_cfg.get.return_value = 8
        runner._maybe_reflect(step=4)

    reflect_msgs = [m for m in runner.messages
                    if "[反思检查点" in (m.get("content") or "")]
    assert len(reflect_msgs) == 1


def test_reflect_not_injected_too_soon():
    """_maybe_reflect should not re-inject within 3 steps of last inject."""
    runner = _make_runner("打开QQ找我的手机持续聊天")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner._last_reflect_step = 8
    runner._consecutive_unverified = 0

    with patch.object(runner.session, "snapshot", None), \
         patch("winpilot.agent.loop.CONFIG") as mock_cfg, \
         patch.object(runner.session, "world_enabled", False):
        mock_cfg.get.return_value = 8
        runner._maybe_reflect(step=9)  # only 1 step after last inject

    reflect_msgs = [m for m in runner.messages
                    if "[反思检查点" in (m.get("content") or "")]
    assert len(reflect_msgs) == 0
