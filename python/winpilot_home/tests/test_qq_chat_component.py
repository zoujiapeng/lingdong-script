"""Component-level behavioural tests for "用QQ找我的手机聊天".

These tests do NOT hit a real QQ process or LLM and therefore are not E2E. They verify that:

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


TASK = "用QQ找我的手机聊天"


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

    def test_text_only_prompt_keeps_controller_recovery_contract(self):
        from winpilot.agent.prompts import system_prompt

        prompt = system_prompt(main_model_has_vision=False)

        assert "verify-only execute_subgoal" in prompt
        assert "open_app/launch/focus_window" in prompt
        prompt_with_plan = system_prompt(main_model_has_vision=False, planning_enabled=True)
        assert "add/update/remove/move" in prompt_with_plan
        assert "do not keep a stale plan" in prompt_with_plan

    def test_compact_prompt_is_domain_neutral_and_fail_closed(self):
        from winpilot.agent.prompts import system_prompt
        from winpilot.agent.tool_schemas import tool_schemas

        prompt = system_prompt(
            main_model_has_vision=True,
            planning_enabled=True,
            style="compact",
        )

        assert len(prompt) < 2000
        assert "execution is not semantic" in prompt
        assert "fresh pixels against the user's criteria" in prompt
        assert "detailed creature" not in prompt
        assert "For a dog" not in prompt
        assert "15-30" not in prompt
        assert "# Todo planning" not in prompt
        assert "revise_plan" not in prompt
        for scenario_rule in (
            "suggestions are open",
            "6+7=",
            "calculator",
            "canvas",
            "game",
            "handwriting",
            "sliders",
        ):
            assert scenario_rule not in prompt.casefold()
        native_tools = {
            item["function"]["name"]
            for item in tool_schemas(
                planning=True,
                coding_runtime=False,
                allow_shell=False,
                allow_files=False,
                profile="lean_gui",
                native_vision=True,
            )
        }
        assert {"list_windows", "focus_window", "open_app", "observe"} <= native_tools
        for unavailable in ("scan_windows", "find_app", "find_program"):
            assert unavailable not in native_tools
            assert unavailable not in prompt

    def test_main_model_vision_capability_detection(self):
        from winpilot.agent.loop import _main_model_has_vision

        vision_models = [
            "mimo-v2-omni", "mimo-v2.5", "qwen2.5-vl-72b", "gpt-4o",
            "gpt-4o-mini", "omni-latest", "llava-vision",
        ]
        text_models = [
            "deepseek-v4-flash", "deepseek-v4-pro",
            "deepseek-chat", "deepseek-reasoner",
            "mimo-v2.5-pro", "mimo-v2-pro",
        ]

        with patch("winpilot.agent.loop.CONFIG") as mock_cfg:
            for model in vision_models:
                mock_cfg.get.return_value = {
                    "base_url": "https://api.xiaomimimo.com/v1",
                    "model": model,
                }
                assert _main_model_has_vision() is True, f"expected vision for {model}"
            for model in text_models:
                mock_cfg.get.return_value = {
                    "base_url": "https://api.xiaomimimo.com/v1",
                    "model": model,
                }
                assert _main_model_has_vision() is False, f"expected no vision for {model}"

    def test_screenshot_injects_the_authoritative_image_coordinate_contract(self):
        """_inject_observe_screenshot should append an image_url message when
        the main model is vision-capable and an hwnd is set.  The model should
        receive one exact image-pixel coordinate space, without physical-window
        geometry that the runtime already owns.
        """
        from winpilot.utils.image_capture import VisualCaptureScope
        from winpilot.utils.screenshot import Region

        runner = _make_runner()
        runner.messages = [{"role": "system", "content": "sys"}]

        with patch("winpilot.agent.loop._main_model_has_vision", return_value=True), \
             patch.object(runner.session, "hwnd", 12345), \
             patch(
                 "winpilot.agent.loop.select_visual_capture_scope",
                 return_value=VisualCaptureScope(
                     Region(120, 80, 1400, 900),
                     "target_window",
                     "verified_foreground_target",
                     target_hwnd=12345,
                     target_verified=True,
                 ),
             ), \
             patch("winpilot.agent.loop.capture_window_jpeg_b64", return_value="ZmFrZQ=="):

            runner._inject_observe_screenshot()

        # An image_url message should have been appended.
        image_msgs = [
            m for m in runner.messages
            if isinstance(m.get("content"), list)
            and any(p.get("type") == "image_url" for p in m["content"])
        ]
        assert len(image_msgs) == 1
        text_part = next(part for part in image_msgs[0]["content"] if part.get("type") == "text")
        assert "Exact authoritative full-image size=1120x720 pixels" in text_part["text"]
        assert "image_* pixel values" in text_part["text"]
        assert "x=0..1119, y=0..719" in text_part["text"]
        assert "left=120" not in text_part["text"]

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

    def test_native_done_is_decided_from_fresh_pixels_not_read_text(self, monkeypatch):
        from winpilot.agent import done_verifier
        from winpilot.agent.loop import AgentRunner
        from winpilot.agent.runtime_profile import RuntimeProfile

        runner = AgentRunner(
            TASK,
            stop_event=threading.Event(),
            runtime_profile=RuntimeProfile(
                stage_type="ExecuteDesktopTask",
                experience_enabled=False,
            ),
        )
        chat_readout = (
            "妈妈\nHELLO_FROM_AGENT\n好的，我看到了\nTHANKS_FOR_CONFIRMING"
        )
        runner._tool_history = [
            ("type_text", {"text": "HELLO_FROM_AGENT"}, '{"ok": true}'),
            ("hotkey", {"keys": ["ENTER"]}, '{"ok": true}'),
            ("read_text", {}, chat_readout),
            ("type_text", {"text": "THANKS_FOR_CONFIRMING"}, '{"ok": true}'),
            ("hotkey", {"keys": ["ENTER"]}, '{"ok": true}'),
            ("read_text", {}, chat_readout),
        ]
        # Legacy read_text may remain as factual history, but it is not an
        # acceptance shortcut for the unified native visual runtime.
        runner._recent_results = [
            ("read_text",
             "妈妈 在吗 帮我看一下 我发了个消息\n"
             "2025-06-27 15:00  嗯，在的，你说吧，有什么事情吗？\n"
             "2025-06-27 15:01  我发了一条很重要的消息给你，你收到了吗？需要你帮忙确认一下。\n"
             "2025-06-27 15:02  收到了，已经看到了，放心吧一切都很好。\n"),
        ]
        runner._done_rejects = 0

        class _Config:
            def get(self, *path, default=None):
                return {
                    ("agent", "verify_done"): True,
                    ("agent", "done_max_rejects"): 2,
                    ("api",): {"model": "mock-vision-judge"},
                }.get(tuple(path), default)

        monkeypatch.setattr(done_verifier, "_config", lambda: _Config())
        monkeypatch.setattr(
            done_verifier,
            "model_supports_vision",
            lambda _model: True,
        )

        def fresh_screen():
            runner._fresh_done_image_b64 = "fresh-chat-frame"
            return "fresh authoritative frame"

        monkeypatch.setattr(runner, "_fresh_done_screen", fresh_screen)
        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content=(
                    '{"done": true, "reason": '
                    '"latest pixels satisfy the original request"}'
                ),
                reasoning_content="",
            ))]
        )

        result = runner._verify_done(client, steps_left=10)

        assert result is True
        assert runner._done_verified is True
        request = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        assert isinstance(request, list)
        assert any(part.get("type") == "image_url" for part in request)

    def test_done_force_accept_disabled_after_max_rejects(self):
        """Communication tasks must not turn repeated rejects into fake success."""
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

        assert result is False
        assert runner._done_unverified is False
        assert "cannot be force-accepted" in runner._last_done_reject_reason


# ---------------------------------------------------------------------------
# 6. Perception: structured intent, not product/window vocabulary, selects mode
# ---------------------------------------------------------------------------

class TestPerceptionMode:
    def test_structured_send_task_triggers_visual_mode(self):
        from winpilot.perception.intent import perception_mode

        mode = perception_mode({
            "operation": ["send"],
            "interaction": {"active": False},
        }, "QQ")
        assert mode == "visual", f"expected visual, got {mode}"

    def test_window_title_does_not_infer_perception_mode(self):
        from winpilot.perception.intent import perception_mode

        mode = perception_mode("打开应用", "QQ - 我的手机")
        assert mode == "normal"

    def test_generic_dialog_stays_normal_mode(self):
        from winpilot.perception.intent import perception_mode

        mode = perception_mode("Open the notes application", "Nebula Notes")
        assert mode == "normal"
