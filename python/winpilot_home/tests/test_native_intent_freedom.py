from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def _assistant_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content, tool_calls=[]),
            finish_reason="stop",
        )],
    )


def _native_desktop_runner(task: str, *, max_steps: int = 2):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.runtime_profile import RuntimeProfile

    return AgentRunner(
        task,
        max_steps_override=max_steps,
        runtime_profile=RuntimeProfile(
            stage_type="ExecuteDesktopTask",
            experience_enabled=False,
        ),
    )


def _prepare_run(monkeypatch, runner, responses: list[str]) -> None:
    from winpilot.agent import loop

    pending = [_assistant_response(item) for item in responses]
    monkeypatch.setattr(loop, "_main_model_has_vision", lambda: True)
    monkeypatch.setattr(loop, "_client", lambda: object())
    monkeypatch.setattr(loop, "build_model_adapter", lambda client, _settings: client)
    monkeypatch.setattr(loop, "_main_agent_api_settings", lambda: {})
    monkeypatch.setattr(loop, "tool_schemas", lambda **_kwargs: [])
    monkeypatch.setattr(loop.BUS, "publish", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(loop.playbooks, "for_task", lambda _task: [])
    runner._native_visual_context_enabled = lambda: True
    runner._inject_visual_frame = lambda **_kwargs: True
    runner._maybe_plan_upfront = lambda _client: None
    runner._track_usage = lambda _response: None
    runner._chat = lambda _client, _tools: pending.pop(0)
    runner.session.close = lambda: None


def test_native_prompt_does_not_require_a_gui_action_for_an_answer(monkeypatch):
    from winpilot.agent import prompts

    monkeypatch.setattr(
        prompts,
        "virtual_screen_region",
        lambda: SimpleNamespace(left=0, top=0, width=1920, height=1080),
    )
    rendered = prompts.system_prompt(main_model_has_vision=True)

    assert "Choose one exposed action from that frame" not in rendered
    assert "whether GUI interaction is needed" in rendered
    assert "answer or explanation" in rendered


def test_native_execute_desktop_does_not_prebind_before_first_model_decision(monkeypatch):
    from winpilot.agent import loop

    runner = _native_desktop_runner("open the named application and explain the visible state")
    calls = []
    monkeypatch.setattr(loop, "_main_model_has_vision", lambda: True)
    monkeypatch.setattr(
        runner.session,
        "dispatch",
        lambda tool, args: calls.append((tool, args)),
    )

    runner._prebind_initial_gui_target()

    assert calls == []


def test_native_execute_desktop_plain_reply_is_verified_and_special_hints_are_absent(monkeypatch):
    from winpilot.agent import loop

    task = (
        "GUI-only test: upload C:\\clip.mp4, compare multiple sources, publish it, "
        "and save as C:\\result.txt"
    )
    runner = _native_desktop_runner(task, max_steps=1)
    _prepare_run(monkeypatch, runner, ["Here is the frame-grounded answer."])
    monkeypatch.setattr(loop, "_is_upload_publish_task", lambda _task: True)
    monkeypatch.setattr(loop, "_is_gui_only_task", lambda _task: True)
    monkeypatch.setattr(loop, "_is_multi_source_research_task", lambda _task: True)
    monkeypatch.setattr(loop, "_explicit_saved_paths", lambda _task: [Path("C:/result.txt")])
    monkeypatch.setattr(
        runner.session,
        "dispatch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("native ExecuteDesktopTask must not perform a hidden prebind")
        ),
    )
    pending_flags = []

    def verify(_client, steps_left=99):
        pending_flags.append((runner._candidate_reply_pending, steps_left))
        runner._done_verified = True
        return True

    runner._verify_done = verify

    result = runner.run()

    assert result["status"] == "done"
    assert pending_flags == [(True, 0)]
    static_text = "\n".join(
        str(message.get("content") or "")
        for message in runner._native_visual_static_messages
    )
    assert task in static_text
    assert "[Upload/publish task guidance]" not in static_text
    assert "[GUI-only task guidance]" not in static_text
    assert "[Multi-source research guidance]" not in static_text
    assert "[Exact GUI output-path requirement]" not in static_text


def test_rejected_native_plain_reply_continues_until_judge_accepts(monkeypatch):
    runner = _native_desktop_runner("explain the best response to the current frame", max_steps=2)
    _prepare_run(monkeypatch, runner, ["first candidate", "corrected candidate"])
    verdicts = iter([False, True])
    seen = []

    def verify(_client, steps_left=99):
        seen.append((runner.result_text, runner._candidate_reply_pending, steps_left))
        passed = next(verdicts)
        if passed:
            runner._done_verified = True
        return passed

    runner._verify_done = verify

    result = runner.run()

    assert result["status"] == "done"
    assert [item[0] for item in seen] == ["first candidate", "corrected candidate"]
    assert all(item[1] is True for item in seen)


def test_unverified_native_plain_reply_cannot_succeed(monkeypatch):
    runner = _native_desktop_runner("explain the current frame", max_steps=1)
    _prepare_run(monkeypatch, runner, ["unchecked candidate"])
    runner._verify_done = lambda _client, steps_left=99: True

    result = runner.run()

    assert result["status"] == "fail"
    assert runner._done_verified is False


def test_fresh_image_judge_receives_full_candidate_frame_actions_and_evidence(monkeypatch):
    from winpilot.agent import done_verifier

    runner = _native_desktop_runner("answer from the currently visible position")
    runner.result_text = "complete candidate response " + ("detail " * 80)
    runner._steps_log = ["click({\"frame_x\": 500, \"frame_y\": 500})"]
    runner._fresh_done_image_b64 = "fresh-frame-bytes"
    runner._fresh_done_capture_scope = "target_window"
    runner._trace_debug = lambda *_args, **_kwargs: None
    runner._track_aux_model_response = lambda *_args, **_kwargs: None
    runner._native_judge_nonvisual_evidence = lambda _evidence: "deterministic-proof"
    monkeypatch.setattr(done_verifier, "model_supports_vision", lambda _model: True)
    monkeypatch.setattr(
        done_verifier,
        "_config",
        lambda: SimpleNamespace(get=lambda *args, default=None: (
            {"model": "vision-model"} if args == ("api",) else default
        )),
    )
    captured = {}

    class Completions:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"done": true, "reason": "grounded"}'),
            )])

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    assert runner._call_done_judge(
        client,
        "fresh frame marker",
        "click then observe",
        "- run_shell: deterministic output",
    ) is True
    user_content = captured["messages"][1]["content"]
    text = user_content[0]["text"]
    assert runner.task in text
    assert runner.result_text in text
    assert "click" in text
    assert "deterministic-proof" in text
    assert user_content[1]["image_url"]["url"].endswith("fresh-frame-bytes")
    assert runner._done_verified is True


def test_native_candidate_reply_bypasses_keyword_preclassification_and_reaches_judge(monkeypatch):
    from winpilot.agent import loop

    monkeypatch.setattr(loop, "_main_model_has_vision", lambda: True)
    runner = _native_desktop_runner("explain how the visible text could be entered")
    runner._candidate_reply_pending = True
    runner._fresh_done_screen = lambda: "fresh-current-frame"
    seen = {}

    def fresh_screen():
        seen["screen"] = "fresh-current-frame"
        return "fresh-current-frame"

    def judge(_client, recent, evidence):
        seen.update(recent=recent, evidence=evidence)
        runner._done_verified = True
        return True

    runner._fresh_done_screen = fresh_screen
    runner._call_native_done_judge = judge
    runner._done_evidence = lambda: ("no mutation", "deterministic context")
    monkeypatch.setattr(
        "winpilot.agent.done_verifier._config",
        lambda: SimpleNamespace(get=lambda *args, default=None: True),
    )

    assert runner._verify_done(object()) is True
    assert seen == {
        "screen": "fresh-current-frame",
        "recent": "no mutation",
        "evidence": "deterministic context",
    }


def test_native_verify_done_sends_bounded_prior_visual_effects_to_judge(monkeypatch):
    from winpilot.agent import done_verifier, loop

    runner = _native_desktop_runner(
        "complete the visible sequence across the current and previous windows"
    )
    runner.session.hwnd = 22
    runner.result_text = "sequence complete"
    runner._trace_debug = lambda *_args, **_kwargs: None
    runner._track_aux_model_response = lambda *_args, **_kwargs: None
    runner._historical_visual_checkpoints = [{
        "image_b64": "previous-window-effect",
        "hwnd": 11,
        "title": "Previous Window",
        "action": "type_text",
        "frame_id": 4,
    }]
    runner._visual_effect_checkpoints = [
        {
            "image_b64": "transient-click-effect",
            "hwnd": 22,
            "title": "Current Window",
            "action": "click",
            "frame_id": 5,
        },
        {
            "image_b64": "previous-window-effect",
            "hwnd": 11,
            "title": "Previous Window",
            "action": "type_text",
            "frame_id": 4,
        },
        {
            "image_b64": "current-final-frame",
            "hwnd": 22,
            "title": "Current Window",
            "action": "drag",
            "frame_id": 6,
        },
    ]

    def fresh_screen():
        runner._fresh_done_image_b64 = "current-final-frame"
        return "fresh frame"

    runner._fresh_done_screen = fresh_screen
    monkeypatch.setattr(loop, "_main_model_has_vision", lambda: True)
    monkeypatch.setattr(done_verifier, "model_supports_vision", lambda _model: True)
    monkeypatch.setattr(
        done_verifier,
        "_config",
        lambda: SimpleNamespace(get=lambda *args, default=None: {
            ("agent", "verify_done"): True,
            ("agent", "done_max_rejects"): 2,
            ("api",): {"model": "vision-model"},
        }.get(tuple(args), default)),
    )
    captured = {}

    class Completions:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=(
                    '{"done": true, "reason": "ordered effects are visible"}'
                )),
            )])

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    assert runner._verify_done(client) is True
    content = captured["messages"][1]["content"]
    urls = [
        part["image_url"]["url"]
        for part in content
        if part.get("type") == "image_url"
    ]
    assert urls == [
        "data:image/jpeg;base64,current-final-frame",
        "data:image/jpeg;base64,previous-window-effect",
        "data:image/jpeg;base64,transient-click-effect",
    ]
    labels = "\n".join(
        part.get("text", "") for part in content if part.get("type") == "text"
    )
    assert "action_kind='type_text'" in labels
    assert "action_kind='click'" in labels
