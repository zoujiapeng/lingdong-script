"""End-to-end behavioural tests for "用QQ找我的手机持续聊天".

These tests do NOT hit a real QQ process or LLM. They verify that:

1. The agent is wired correctly for a vision-capable main model:
   - system prompt declares native vision instead of "text-only"
   - screenshot injection is attempted after each observe
2. Experience memory is bypassed (no hints, no reuse suggestions).
3. Communication task hint is injected.
4. Done-gate rejects done without chat-window read_text evidence.
5. Done-gate accepts done once read_text confirms an open conversation.

The test mimics the real loop's message assembly and done-gate path without
spawning threads or making network calls.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch, call

import pytest


TASK = "用QQ找我的手机持续聊天"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runner(task: str = TASK):
    from winpilot.agent.loop import AgentRunner
    return AgentRunner(task=task, stop_event=threading.Event())


# ---------------------------------------------------------------------------
# 1. Vision-aware system prompt
# ---------------------------------------------------------------------------

class TestVisionPrompt:
    def test_system_prompt_declares_native_vision_for_mimo(self):
        from winpilot.agent.prompts import system_prompt, _VISION_NATIVE, _VISION_TEXT_ONLY

        prompt_with_vision = system_prompt(main_model_has_vision=True)
        prompt_without_vision = system_prompt(main_model_has_vision=False)

        assert _VISION_NATIVE in prompt_with_vision
        assert _VISION_TEXT_ONLY not in prompt_with_vision

        assert _VISION_TEXT_ONLY in prompt_without_vision
        assert _VISION_NATIVE not in prompt_without_vision

    def test_system_prompt_allows_literal_braces_in_tool_examples(self):
        from winpilot.agent.prompts import system_prompt

        prompt = system_prompt(main_model_has_vision=False)

        assert "draw={shape,color}" in prompt
        assert "verify-only execute_subgoal" in prompt
        assert "launch/find_app/focus_window" in prompt
        prompt_with_plan = system_prompt(main_model_has_vision=False, planning_enabled=True)
        assert "If a current todo plan is already shown, follow it" in prompt_with_plan

    def test_main_model_vision_capability_detection(self):
        from winpilot.agent.loop import _main_model_has_vision

        vision_models = [
            "mimo-v2-omni", "qwen2.5-vl-72b", "gpt-4o",
            "gpt-4o-mini", "omni-latest", "llava-vision",
        ]
        text_models = [
            "deepseek-v4-flash", "deepseek-v4-pro",
            "deepseek-chat", "deepseek-reasoner",
            "mimo-v2.5-pro", "mimo-v2.5", "mimo-v2-pro",
        ]

        with patch("winpilot.agent.loop.CONFIG") as mock_cfg:
            for model in vision_models:
                mock_cfg.get.return_value = model
                assert _main_model_has_vision() is True, f"expected vision for {model}"
            for model in text_models:
                mock_cfg.get.return_value = model
                assert _main_model_has_vision() is False, f"expected no vision for {model}"

    def test_screenshot_injected_after_observe_for_vision_model(self):
        """_inject_observe_screenshot should append an image_url message when
        the main model is vision-capable and an hwnd is set.

        cv2/base64/capture are imported inside the function body (lazy), so we
        patch them at their canonical module paths, not on the loop module.
        """
        runner = _make_runner()
        runner.messages = [{"role": "system", "content": "sys"}]

        import numpy as np
        fake_img = np.zeros((200, 300, 3), dtype="uint8")
        fake_buf = MagicMock()
        fake_buf.tobytes.return_value = b"\xff\xd8\xff"

        with patch("winpilot.agent.loop._main_model_has_vision", return_value=True), \
             patch.object(runner.session, "hwnd", 12345), \
             patch("winpilot.utils.screenshot.window_region", return_value=(0, 0, 300, 200), create=True), \
             patch("winpilot.utils.screenshot.capture", return_value=fake_img, create=True), \
             patch("cv2.resize", return_value=fake_img), \
             patch("cv2.imencode", return_value=(True, fake_buf)):

            runner._inject_observe_screenshot()

        # An image_url message should have been appended.
        image_msgs = [
            m for m in runner.messages
            if isinstance(m.get("content"), list)
            and any(p.get("type") == "image_url" for p in m["content"])
        ]
        assert len(image_msgs) == 1

    def test_no_screenshot_injected_for_text_model(self):
        """No injection when model lacks vision."""
        runner = _make_runner()
        runner.messages = [{"role": "system", "content": "sys"}]

        with patch("winpilot.agent.loop._main_model_has_vision", return_value=False):
            runner._inject_observe_screenshot()

        assert len(runner.messages) == 1  # unchanged


# ---------------------------------------------------------------------------
# 2. No memory: experience hints and reuse skipped when hints empty
# ---------------------------------------------------------------------------

class TestNoMemory:
    def test_run_injects_no_experience_section_when_hints_empty(self):
        """When experience.hints_with_ids returns empty string, the system
        prompt must not contain a "Past successful experience" section."""
        from winpilot.agent.prompts import system_prompt

        prompt = system_prompt(experience_hints="")
        assert "# Past successful experience" not in prompt

    def test_no_reuse_suggestion_injected_when_no_script(self):
        """When experience.reusable_script_for returns (None, None), no
        reuse message should be added to runner.messages."""
        runner = _make_runner()

        with patch("winpilot.agent.loop._client", side_effect=RuntimeError("no key")), \
             patch("winpilot.agent.loop.playbooks") as mock_pb, \
             patch("winpilot.agent.loop.experience") as mock_exp, \
             patch("winpilot.agent.loop.vlm") as mock_vlm, \
             patch("winpilot.agent.loop.prompts") as mock_prompts:
            mock_exp.hints_with_ids.return_value = ("", [])
            mock_exp.note_hints.return_value = ""
            mock_exp.reusable_script_for.return_value = (None, None)
            mock_exp.feedback.return_value = None
            mock_pb.for_task.return_value = []
            mock_pb.render_section.return_value = ""
            mock_pb.for_window_title.return_value = None
            mock_vlm.is_configured.return_value = False
            mock_prompts.system_prompt.return_value = "sys"
            runner.run()

        reuse_msgs = [
            m for m in runner.messages
            if "⚡" in (m.get("content") or "") or "reuse_skill" in (m.get("content") or "")
        ]
        assert len(reuse_msgs) == 0


# ---------------------------------------------------------------------------
# 3. Communication task hint injection
# ---------------------------------------------------------------------------

class TestCommHint:
    def test_comm_hint_injected_for_qq_chat_task(self):
        runner = _make_runner()

        with patch("winpilot.agent.loop._client", side_effect=RuntimeError("no key")), \
             patch("winpilot.agent.loop.playbooks") as mock_pb, \
             patch("winpilot.agent.loop.experience") as mock_exp, \
             patch("winpilot.agent.loop.vlm") as mock_vlm, \
             patch("winpilot.agent.loop.prompts") as mock_prompts:
            mock_exp.hints_with_ids.return_value = ("", [])
            mock_exp.note_hints.return_value = ""
            mock_exp.reusable_script_for.return_value = (None, None)
            mock_exp.feedback.return_value = None
            mock_pb.for_task.return_value = []
            mock_pb.render_section.return_value = ""
            mock_pb.for_window_title.return_value = None
            mock_vlm.is_configured.return_value = False
            mock_prompts.system_prompt.return_value = "sys"
            runner.run()

        hint_msgs = [
            m for m in runner.messages
            if m.get("role") == "user" and "[通信任务提示]" in (m.get("content") or "")
        ]
        assert len(hint_msgs) == 1
        content = hint_msgs[0]["content"]
        # No-payload path: must mention read_text and done evidence.
        assert "read_text" in content
        assert "done" in content


# ---------------------------------------------------------------------------
# 4 & 5. Done-gate: reject without evidence, accept with read_text
# ---------------------------------------------------------------------------

class TestDoneGate:
    def test_done_rejected_without_chat_evidence(self):
        runner = _make_runner()
        runner._recent_results = []
        runner._done_rejects = 0

        with patch.object(runner.session, "hwnd", None), \
             patch("winpilot.agent.loop.fusion"):
            client = MagicMock()
            result = runner._verify_done(client, steps_left=10)

        assert result is False
        assert runner._done_rejects == 1

    def test_done_accepted_with_read_text_evidence(self):
        runner = _make_runner()
        # Simulate a real chat window: multiple long lines resembling message bubbles.
        runner._recent_results = [
            ("read_text",
             "妈妈 在吗 帮我看一下 我发了个消息\n"
             "2025-06-27 15:00  嗯，在的，你说吧，有什么事情吗？\n"
             "2025-06-27 15:01  我发了一条很重要的消息给你，你收到了吗？需要你帮忙确认一下。\n"
             "2025-06-27 15:02  收到了，已经看到了，放心吧一切都很好。\n"),
        ]
        runner._done_rejects = 0

        with patch.object(runner.session, "hwnd", None), \
             patch("winpilot.agent.loop.fusion") as mock_fusion, \
             patch("winpilot.agent.loop.intent") as mock_intent:
            mock_snap = MagicMock()
            mock_snap.to_text.return_value = "QQ 聊天界面 妈妈 在吗"
            mock_snap.title = "QQ"
            mock_fusion.observe.return_value = mock_snap
            mock_intent.perception_mode.return_value = "visual"

            client = MagicMock()
            client.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(
                    content='{"done": true, "reason": "聊天窗口已打开，read_text 证据充分"}',
                    reasoning_content="",
                ))]
            )
            result = runner._verify_done(client, steps_left=10)

        assert result is True
        assert runner._done_verified is True

    def test_done_force_accepted_after_max_rejects(self):
        """After done_max_rejects bounces the done is accepted but flagged.

        The max_rejects gate runs before the communication-task quick checks,
        so we patch CONFIG to return a fixed max_rejects=2 and set _done_rejects=2.
        """
        runner = _make_runner()
        runner._recent_results = []
        runner._done_rejects = 2

        with patch.object(runner.session, "hwnd", None), \
             patch("winpilot.agent.loop.fusion"), \
             patch("winpilot.agent.loop.CONFIG") as mock_cfg:
            # Return max_rejects=2 for the agent.done_max_rejects lookup,
            # and verify_done=True for the verify_done gate, steps_left >> 0.
            def cfg_get(*path, default=None):
                if path == ("agent", "verify_done"):
                    return True
                if path == ("agent", "done_max_rejects"):
                    return 2
                return default
            mock_cfg.get.side_effect = cfg_get

            client = MagicMock()
            result = runner._verify_done(client, steps_left=10)

        assert result is True
        assert runner._done_unverified is True


# ---------------------------------------------------------------------------
# 6. Perception: QQ window triggers visual mode, not normal mode
# ---------------------------------------------------------------------------

class TestPerceptionMode:
    def test_qq_chat_task_triggers_visual_mode(self):
        from winpilot.perception.intent import perception_mode

        mode = perception_mode(TASK, "QQ")
        assert mode == "visual", f"expected visual, got {mode}"

    def test_qq_window_title_triggers_visual_mode(self):
        from winpilot.perception.intent import perception_mode

        mode = perception_mode("打开应用", "QQ - 我的手机")
        assert mode == "visual"

    def test_generic_dialog_stays_normal_mode(self):
        from winpilot.perception.intent import perception_mode

        mode = perception_mode("打开记事本", "Notepad")
        assert mode == "normal"
