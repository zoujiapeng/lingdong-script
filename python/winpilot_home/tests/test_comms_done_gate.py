"""Tests for communication task done-gate fixes.

Covers:
1. No-payload communication tasks require read_text evidence before done.
2. With-payload tasks still check the payload is visible.
3. Communication prompt hint is injected regardless of payload presence.
"""
import json
import threading
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mocked_visual_judge_capability(monkeypatch):
    """Declare an image-capable judge for tests that submit mocked pixels."""
    from winpilot.agent import done_verifier as done_mod

    class _Config:
        _data = {
            "api": {"model": "mock-vision-judge", "temperature": 0.0},
            "agent": {
                "escalation_model": "",
                "verify_done": True,
                "done_max_rejects": 2,
            },
            "vlm": {"enabled": False, "base_url": "", "model": ""},
        }

        def get(self, *path, default=None):
            node = self._data
            for key in path:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return node

    monkeypatch.setattr(done_mod, "_config", lambda: _Config())
    monkeypatch.setattr(
        done_mod,
        "model_supports_vision",
        lambda model, provider="": model == "mock-vision-judge",
    )


def test_communication_mode_honors_draft_only_instruction():
    from winpilot.agent.task_rules import _communication_mode

    task = (
        "\u6253\u5f00QQ\uff0c\u5728\u8f93\u5165\u6846\u4e2d\u8f93\u5165"
        '"HELLO_0712"\uff0c\u505c\u5728\u53d1\u9001\u524d\uff0c\u4e0d\u8981\u53d1\u9001'
    )

    assert _communication_mode(task) == "draft"


def test_draft_communication_accepts_grounded_typed_payload_without_readback():
    runner = _make_runner(
        "\u6253\u5f00QQ\uff0c\u8f93\u5165\u6d88\u606f\u4f46\u4e0d\u8981\u53d1\u9001"
    )
    runner._tool_history = [
        ("type_text", {"text": "HELLO_0712"}, '{"ok": true}'),
    ]

    assert runner._deterministic_communication_verdict() == (
        True,
        "Requested communication text was entered as a draft without a send action.",
    )


def test_draft_communication_rejects_any_successful_send_action():
    runner = _make_runner(
        'Open QQ, type "HELLO_0712", but leave it as a draft and do not send'
    )
    runner._tool_history = [
        ("type_text", {"text": "HELLO_0712"}, '{"ok": true}'),
        ("hotkey", {"keys": ["ENTER"]}, '{"ok": true}'),
    ]

    verdict = runner._deterministic_communication_verdict()

    assert verdict is not None
    assert verdict[0] is False
    assert "successful send action" in verdict[1]
    assert runner._communication_native_mutation_ready() is False


def test_draft_runtime_hard_blocks_send_click_and_enter():
    from winpilot.agent.tools import ToolSession

    session = ToolSession(
        task='\u5728QQ\u8f93\u5165"HELLO_0712"\uff0c\u505c\u5728\u53d1\u9001\u524d\uff0c\u4e0d\u8981\u53d1\u9001'
    )

    assert session._draft_communication_block(
        "execute_subgoal",
        {"input": {"text": "HELLO_0712"}, "click": {"label": "Send"}},
    )
    assert session._draft_communication_block(
        "run_action_batch",
        {"actions": [{"action": "hotkey", "keys": ["Enter"]}]},
    )
    assert session._draft_communication_block(
        "execute_subgoal",
        {"input": {"text": "HELLO_0712"}},
    ) == ""


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


def test_comms_done_gate_no_payload_rejects_read_text_without_send_action():
    """Existing chat history is not causal proof that this run sent anything."""
    runner = _make_runner("打开QQ给我的手机发一条消息")
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

    assert result is False
    assert runner._done_verified is False
    assert "causal evidence" in runner._last_done_reject_reason


def test_image_send_done_gate_accepts_current_attachment_delivery_evidence():
    runner = _make_runner("用计算器计算88和76的商，然后打开QQ给我的手机发一张计算器结果的截图")
    runner._tool_history = [
        ("screenshot", {"path": r"C:\\Users\\zou\\Desktop\\calculator_result.png"}, '{"ok": true}'),
        ("paste_image_file", {"path": r"C:\\Users\\zou\\Desktop\\calculator_result.png"}, '{"ok": true, "via": "clipboard_dib"}'),
        ("hotkey", {"keys": ["ENTER"]}, '{"ok": true}'),
        ("observe", {"query": "已发送"}, '我的手机\n28.58KB已发送'),
    ]

    verdict = runner._deterministic_image_send_verdict()

    assert verdict is not None
    assert verdict[0] is True
    assert "图片附件" in verdict[1]


@pytest.mark.parametrize(
    ("task", "recipient"),
    [
        ("把截图用QQ发给项目讨论群", "项目讨论群"),
        ("通过QQ把图片发送给设计评审", "设计评审"),
    ],
)
def test_legacy_image_send_gate_reuses_structured_recipient_intent(task, recipient):
    """The compatibility gate must not depend on a built-in recipient alias."""
    runner = _make_runner(task)
    runner._tool_history = [
        ("paste_image_file", {"path": r"C:\\tmp\\result.png"}, '{"ok": true}'),
        ("hotkey", {"keys": ["ENTER"]}, '{"ok": true}'),
        ("observe", {"query": "delivery"}, f"{recipient}\n28.58KB已发送"),
    ]

    verdict = runner._deterministic_image_send_verdict()

    assert verdict is not None
    assert verdict[0] is True


def test_image_send_done_gate_rejects_paste_without_send_action():
    runner = _make_runner("Open QQ and send an image to my phone")
    runner._tool_history = [
        ("paste_image_file", {"path": r"C:\\tmp\\result.png"}, '{"ok": true}'),
        ("read_text", {}, "my phone\n28.58KB sent"),
    ]

    verdict = runner._deterministic_image_send_verdict()

    assert verdict is not None
    assert verdict[0] is False
    assert "no successful Send/Enter" in verdict[1]


@pytest.mark.parametrize(
    "keys",
    [
        ["SHIFT", "ENTER"],
        ["CTRL", "ENTER"],
        ["ALT", "RETURN"],
        ["ENTER", "SHIFT"],
    ],
)
def test_communication_send_requires_bare_enter_or_return(keys):
    runner = _make_runner('Open QQ and send "HELLO" to my phone')

    assert runner._communication_entry_sends(
        "hotkey", {"keys": keys}, '{"ok": true}',
    ) is False


@pytest.mark.parametrize("key", ["ENTER", "return"])
def test_communication_send_accepts_successful_bare_enter_or_return(key):
    runner = _make_runner('Open QQ and send "HELLO" to my phone')

    assert runner._communication_entry_sends(
        "hotkey", {"keys": [key]}, '{"ok": true}',
    ) is True


def test_unconsumed_press_enter_fields_do_not_count_as_send_actions():
    runner = _make_runner('Open QQ and send "HELLO" to my phone')
    runner._tool_history = [
        (
            "type_text",
            {"text": "HELLO", "press_enter": True},
            '{"ok": true}',
        ),
        (
            "execute_subgoal",
            {"input": {"text": "SECOND", "press_enter": True}},
            json.dumps({
                "ok": True,
                "actions_executed": [
                    {"action": "type_into_element", "text": "SECOND"},
                ],
                "results": [
                    {"index": 1, "action": "type_into_element", "ok": True},
                ],
            }),
        ),
        ("read_text", {}, "composer: HELLO SECOND"),
    ]

    assert runner._communication_native_mutation_ready() is False
    assert runner._causally_visible_outbound_payloads() == []


def test_one_enter_consumes_at_most_one_recent_typed_turn():
    runner = _make_runner("用QQ和我的手机聊天")
    runner._tool_history = [
        ("type_text", {"text": "FIRST_MESSAGE"}, '{"ok": true}'),
        ("type_text", {"text": "SECOND_MESSAGE"}, '{"ok": true}'),
        ("hotkey", {"keys": ["ENTER"]}, '{"ok": true}'),
        ("read_text", {}, "FIRST_MESSAGE SECOND_MESSAGE"),
    ]

    assert runner._communication_native_mutation_ready() is False
    assert runner._causally_visible_outbound_payloads() == ["SECOND_MESSAGE"]
    verdict = runner._deterministic_communication_verdict()
    assert verdict is not None
    assert verdict[0] is False


@pytest.mark.parametrize(
    ("type_ok", "send_ok", "send_data", "expected"),
    [
        (True, True, {"ok": True}, True),
        (False, True, {"ok": True}, False),
        ("true", True, {"ok": True}, False),
        (True, False, {"ok": False}, False),
        (True, "true", {"ok": True}, False),
        (True, True, {"ok": False}, False),
    ],
)
def test_batch_communication_requires_literal_success_for_each_nested_action(
    type_ok, send_ok, send_data, expected,
):
    runner = _make_runner('Open QQ and send "BATCH_MESSAGE" to my phone')
    runner._tool_history = [(
        "run_action_batch",
        {"actions": [
            {"action": "type_text", "text": "BATCH_MESSAGE"},
            {"action": "hotkey", "keys": ["ENTER"]},
        ]},
        json.dumps({
            "ok": True,
            "results": [
                {"index": 1, "action": "type_text", "ok": type_ok},
                {
                    "index": 2,
                    "action": "hotkey",
                    "ok": send_ok,
                    "data": send_data,
                },
            ],
        }),
    )]

    assert runner._communication_native_mutation_ready() is expected


def test_execute_subgoal_counts_only_successful_executed_send_child():
    runner = _make_runner('Open QQ and send "SUBGOAL_MESSAGE" to my phone')
    runner._tool_history = [(
        "execute_subgoal",
        {
            "input": {"text": "SUBGOAL_MESSAGE"},
            "click": {"label": "Send"},
        },
        json.dumps({
            "ok": True,
            "actions_executed": [
                {"action": "type_into_element", "text": "SUBGOAL_MESSAGE"},
                {"action": "click_element", "label": "Send"},
            ],
            "results": [
                {"index": 1, "action": "type_into_element", "ok": True},
                {"index": 2, "action": "click_element", "ok": True},
            ],
        }),
    )]

    assert runner._communication_native_mutation_ready() is True


def test_bounded_chat_requires_a_reply_turn_before_done():
    runner = _make_runner("用QQ和我的手机聊天")
    runner._tool_history = [
        ("type_text", {"text": "你好，这是一条测试消息"}, "{\"ok\": true}"),
        ("hotkey", {"keys": ["ENTER"]}, "{\"ok\": true}"),
        ("read_text", {}, "我的手机\n17:20\n你好，这是一条测试消息\n好的"),
    ]

    verdict = runner._deterministic_communication_verdict()

    assert verdict is not None
    assert verdict[0] is False
    assert "一轮往返" in verdict[1]


def test_chat_history_evidence_phrase_does_not_turn_one_shot_send_into_chat():
    from winpilot.agent.task_rules import _communication_mode

    task = '打开QQ给我的手机发送"HELLO_0712"并确认消息出现在聊天记录中'

    assert _communication_mode(task) == "send"


def test_bounded_chat_accepts_two_visible_outbound_turns():
    runner = _make_runner("用QQ和我的手机聊天")
    chat = (
        "我的手机\n17:20\n你好，这是一条测试消息\n好的\n"
        "17:21\n收到，那我们稍后再聊"
    )
    runner._tool_history = [
        ("type_text", {"text": "你好，这是一条测试消息"}, "{\"ok\": true}"),
        ("hotkey", {"keys": ["ENTER"]}, "{\"ok\": true}"),
        ("read_text", {}, chat),
        ("type_text", {"text": "收到，那我们稍后再聊"}, "{\"ok\": true}"),
        ("hotkey", {"keys": ["ENTER"]}, "{\"ok\": true}"),
        ("read_text", {}, chat),
    ]

    verdict = runner._deterministic_communication_verdict()

    assert runner._communication_native_mutation_ready() is True
    assert verdict == (True, "普通聊天已完成一轮往返，且两条实际发送内容均在聊天记录中可见。")


def test_continuous_chat_waits_for_user_stop():
    runner = _make_runner("打开QQ找我的手机持续聊天")
    runner._tool_history = [
        ("type_text", {"text": "你好"}, "{\"ok\": true}"),
        ("read_text", {}, "我的手机\n你好\n好的"),
    ]

    verdict = runner._deterministic_communication_verdict()

    assert verdict is not None
    assert verdict[0] is False
    assert "持续聊天" in verdict[1]


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


def test_native_send_uses_fresh_frame_without_forcing_read_text(
    monkeypatch,
    mocked_visual_judge_capability,
):
    runner = _make_runner('Open QQ and send "HELLO_NATIVE_42" to my phone.')
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._tool_history = [
        ("type_text", {"text": "HELLO_NATIVE_42"}, '{"ok": true}'),
        ("hotkey", {"keys": ["ENTER"]}, '{"ok": true}'),
    ]

    def fresh_screen():
        runner._fresh_done_image_b64 = "fresh-chat-frame"
        return "Fresh native-vision frame attached"

    monkeypatch.setattr(runner, "_fresh_done_screen", fresh_screen)
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(
            message=MagicMock(content=(
                '{"done": true, "reason": "message is visibly in the target transcript"}'
            ))
        )]
    )

    assert runner._verify_done(client) is True
    request = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert isinstance(request, list)
    prompt = next(part["text"] for part in request if part.get("type") == "text")
    assert "AUTHORITATIVE COMMUNICATION STATE CHECK" in prompt
    assert "sent message/transcript" in prompt
    assert any(part.get("type") == "image_url" for part in request)


def test_native_send_rejects_failed_type_even_when_enter_succeeded(monkeypatch):
    runner = _make_runner('Open QQ and send "HELLO_NATIVE_42" to my phone.')
    runner.session.hwnd = 123
    runner.result_text = "done"
    runner._tool_history = [
        ("type_text", {"text": "HELLO_NATIVE_42"}, '{"ok": false, "error": "not focused"}'),
        ("hotkey", {"keys": ["ENTER"]}, '{"ok": true}'),
    ]

    def fresh_screen():
        runner._fresh_done_image_b64 = "fresh-chat-frame"
        return "Fresh native-vision frame attached"

    monkeypatch.setattr(runner, "_fresh_done_screen", fresh_screen)
    client = MagicMock()

    assert runner._verify_done(client) is False
    client.chat.completions.create.assert_not_called()
    assert "lacks causal evidence" in runner._last_done_reject_reason


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
