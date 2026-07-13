from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


def _tool_call(call_id: str, name: str, args: dict):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def test_native_visual_observe_skips_lossy_structured_stack(monkeypatch):
    from winpilot.agent import perception_tools
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    # This task is intentionally not tagged as chat/canvas/visual. Native
    # vision should still receive the whole frame directly for an unqueried
    # observe instead of paying the generic structured stack first.
    session = ToolSession(task="open the settings page and click calibration")
    session.hwnd = 7
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(perception_tools, "window_region", lambda _hwnd: Region(10, 20, 800, 600))
    monkeypatch.setattr("winpilot.agent.tools.window_region", lambda _hwnd: Region(10, 20, 800, 600))
    monkeypatch.setattr(perception_tools.win32, "_window_text", lambda _hwnd: "QQ")
    monkeypatch.setattr(
        perception_tools,
        "capture_window_jpeg_b64",
        lambda _hwnd, **_kwargs: "frame",
    )
    monkeypatch.setattr(
        perception_tools.fusion,
        "observe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("native visual direct path must not call fusion.observe")
        ),
    )

    result = session._t_observe({"include_icons": True})

    assert result.image_b64 == "frame"
    assert "Native visual direct observation" in result.text
    assert session.snapshot is not None
    assert session.snapshot.dim_stats["native_frame"]["authoritative"] is True
    assert session.snapshot.dim_stats["uia"]["skipped"] == "native_vision_direct"
    assert session._last_native_frame_capture["image_b64"] == "frame"
    assert session._last_native_frame_capture["region"] == Region(10, 20, 800, 600)


def test_visual_frame_marks_current_screen_epoch(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("draw on the canvas")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 99
    runner.session._screen_epoch = 4
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(50, 70, 900, 600))
    monkeypatch.setattr(
        "winpilot.agent.loop.capture_window_jpeg_b64",
        lambda _hwnd, **_kwargs: "jpeg",
    )

    assert runner._inject_visual_frame(phase="post_action", action="click") is True
    assert runner._has_fresh_visual_grounding() is True
    assert runner._visual_frame_epoch == 4
    assert runner._visual_frame_hwnd == 99
    assert runner._last_visual_feedback_action == "click"
    frame = runner.messages[-1]["content"]
    assert any(part.get("type") == "image_url" for part in frame)
    assert "authoritative current state" in frame[0]["text"]


def test_new_visual_frame_folds_old_image_before_next_model_request(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("inspect and operate from the current frame only")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 99
    runner.session._screen_epoch = 1
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: Region(10, 20, 800, 600),
    )

    assert runner._inject_visual_frame(
        phase="initial",
        image_b64="first-frame-pixels",
    ) is True
    first_index = len(runner.messages) - 1
    runner.session._screen_epoch = 2
    assert runner._inject_visual_frame(
        phase="post_action",
        action="click",
        image_b64="second-frame-pixels",
    ) is True

    assert isinstance(runner.messages[first_index]["content"], str)
    assert "folded" in runner.messages[first_index]["content"]
    live_images = [
        part["image_url"]["url"]
        for message in runner.messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    assert live_images == ["data:image/jpeg;base64,second-frame-pixels"]

    captured = {}

    class Completions:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=[]),
                finish_reason="stop",
            )])

    runner._trace_debug = lambda *_args, **_kwargs: None
    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    assert runner._chat(client, []) is not None
    sent_images = [
        part["image_url"]["url"]
        for message in captured["messages"]
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    assert sent_images == ["data:image/jpeg;base64,second-frame-pixels"]


def test_model_turn_executes_only_one_visual_transaction(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("click the first button, then inspect the result")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 1000, 500)
    runner._last_visual_feedback_b64 = "before"
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: SimpleNamespace(
            left=10, top=20, width=1000, height=500,
            clamp_to_screen=lambda: SimpleNamespace(left=10, top=20, width=1000, height=500),
        ),
    )
    dispatched = []

    def dispatch(name, args):
        dispatched.append((name, args))
        return ToolResult(json.dumps({"ok": True, "verified": False}))

    monkeypatch.setattr(runner, "_dispatch_with_fast_visual_wrap", dispatch)
    feedback = []
    monkeypatch.setattr(
        runner,
        "_inject_visual_frame",
        lambda **kwargs: feedback.append(kwargs) or True,
    )

    runner._handle_tool_calls([
        _tool_call("one", "click", {"frame_x": 100, "frame_y": 120}),
        _tool_call("two", "hotkey", {"keys": ["Enter"]}),
    ])

    assert [name for name, _args in dispatched] == ["click"]
    tool_messages = [message for message in runner.messages if message.get("role") == "tool"]
    assert len(tool_messages) == 2
    assert json.loads(tool_messages[1]["content"])["visual_turn_barrier"] is True
    assert feedback and feedback[0]["action"] == "click"


def test_observe_image_is_injected_after_all_tool_results(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("inspect the screen, then click only after seeing it")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    dispatched = []

    def dispatch(name, args):
        dispatched.append((name, args))
        return ToolResult("observed", is_observe=True, image_b64="fresh-frame")

    monkeypatch.setattr(runner, "_dispatch_with_fast_visual_wrap", dispatch)
    injected = []
    monkeypatch.setattr(
        runner,
        "_inject_observe_screenshot",
        lambda image_b64="": injected.append((image_b64, list(runner.messages))) or True,
    )

    runner._handle_tool_calls([
        _tool_call("one", "observe", {}),
        _tool_call("two", "click", {"x": 100, "y": 120}),
    ])

    assert [name for name, _args in dispatched] == ["observe"]
    assert injected and injected[0][0] == "fresh-frame"
    roles_at_injection = [message.get("role") for message in injected[0][1]]
    assert roles_at_injection[-2:] == ["tool", "tool"]
    second_payload = json.loads(injected[0][1][-1]["content"])
    assert second_payload["visual_turn_barrier"] is True


def test_terminal_visual_action_injects_post_frame_before_return(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("click the requested control")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 1000, 500)
    runner._last_visual_feedback_b64 = "before"
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: SimpleNamespace(
            left=10, top=20, width=1000, height=500,
            clamp_to_screen=lambda: SimpleNamespace(left=10, top=20, width=1000, height=500),
        ),
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: ToolResult('{"ok": true}', terminal="done"),
    )
    feedback = []
    monkeypatch.setattr(
        runner,
        "_inject_visual_frame",
        lambda **kwargs: feedback.append(kwargs) or True,
    )

    terminal = runner._handle_tool_calls([
        _tool_call("one", "click", {"frame_x": 100, "frame_y": 120}),
    ])

    assert terminal == "done"
    assert feedback and feedback[0]["phase"] == "post_action"
    tool_payload = json.loads(next(
        message["content"] for message in runner.messages
        if message.get("role") == "tool"
    ))
    assert tool_payload["needs_observe"] is False
    assert tool_payload["native_post_action_frame_pending"] is True
    assert tool_payload["native_action_executed"] is True
    assert "Do not call observe" in tool_payload["fast_path_advice"]
    recent_tool, recent_evidence = runner._recent_results[-1]
    assert recent_tool == "click"
    assert json.loads(recent_evidence)["native_action_executed"] is True


def test_hidden_local_recovery_epoch_change_returns_real_frame(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner("click the target in the current browser")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._last_visual_feedback_b64 = "before"
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: ToolResult('{"ok": false, "blocked": true}'),
    )

    def hidden_recovery(**_kwargs):
        runner.session._screen_epoch += 1
        return None

    monkeypatch.setattr(
        runner._failure_recovery_layer,
        "handle_tool_result",
        hidden_recovery,
    )
    feedback = []
    monkeypatch.setattr(
        runner,
        "_inject_visual_frame",
        lambda **kwargs: feedback.append(kwargs) or True,
    )

    runner._handle_tool_calls([
        _tool_call("one", "execute_subgoal", {"subgoal": "click target"}),
    ])

    assert feedback
    assert feedback[0]["phase"] == "post_action"
    assert feedback[0]["action"] == "execute_subgoal:local_recovery"


def test_low_level_action_requires_fresh_real_frame(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("click the visible button")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 3
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(AssertionError("ungrounded action executed")),
    )

    runner._handle_tool_calls([_tool_call("one", "click", {"x": 100, "y": 120})])

    payload = json.loads(runner._tool_history[-1][2])
    assert payload["blocked"] is True
    assert payload["visual_grounding_required"] is True


def test_done_judge_receives_fresh_real_frame(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        message = SimpleNamespace(content='{"done": true, "reason": "visible result"}')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    runner = AgentRunner("open the requested settings page")
    runner.result_text = "done"
    runner.messages = []
    runner._fresh_done_image_b64 = "fresh-frame"
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setitem(runner.session.__dict__, "hwnd", 3)

    assert runner._call_done_judge(client, "window", "click", "evidence") is True
    user_content = captured["messages"][1]["content"]
    assert isinstance(user_content, list)
    image = next(part for part in user_content if part.get("type") == "image_url")
    assert image["image_url"]["url"].endswith("fresh-frame")


def test_drawing_done_rejection_allows_only_one_additive_quality_repair(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent import done_verifier as done_mod

    monkeypatch.setattr(done_mod, "model_supports_vision", lambda _model: True)

    def create(**_kwargs):
        message = SimpleNamespace(content=(
            '{"done": false, "reason": "the dog needs more facial detail"}'
        ))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    runner = AgentRunner("draw a good-looking dog in Paint")
    runner.result_text = "done"
    runner._fresh_done_image_b64 = "fresh-frame"
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    assert runner._call_done_judge(client, "", "draw_strokes", "evidence") is False
    recovery = runner.messages[-1]["content"]
    assert runner._drawing_quality_repair_history_start == len(runner._tool_history)
    assert "one incremental execute_subgoal repair" in recovery
    assert "do not clear, restart, redraw the whole subject" in recovery
    assert "call blocked/fail honestly" in recovery

    assert runner._call_done_judge(client, "", "draw_strokes", "new evidence") is False
    exhausted = runner.messages[-1]["content"]
    assert "single safe additive repair opportunity has been consumed" in exhausted
    assert "Do not mutate, clear, redraw, undo" in exhausted


def test_normalized_frame_coordinates_map_inside_exact_window_bounds(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click and drag using the current real frame")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (-100, 50, 1001, 501)
    runner._visual_frame_id = 9
    runner._last_visual_feedback_b64 = "frame"
    region = Region(-100, 50, 1001, 501)
    monkeypatch.setattr(Region, "clamp_to_screen", lambda self: self)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: region)

    top_left = runner._map_normalized_frame_coordinates(
        "click", {"frame_x": 0, "frame_y": 0},
    )
    bottom_right = runner._map_normalized_frame_coordinates(
        "click", {"frame_x": 1000, "frame_y": 1000},
    )
    drag = runner._map_normalized_frame_coordinates("drag", {
        "frame_x1": 0,
        "frame_y1": 0,
        "frame_x2": 1000,
        "frame_y2": 1000,
        "frame_points": [[500, 500]],
    })

    assert (top_left["x"], top_left["y"]) == (-100, 50)
    assert (bottom_right["x"], bottom_right["y"]) == (900, 550)
    assert (drag["x1"], drag["y1"], drag["x2"], drag["y2"]) == (-100, 50, 900, 550)
    assert drag["points"] == [[400, 300]]
    assert drag["coordinate_space"] == "current_frame_normalized_0_1000"
    assert drag["visual_frame_id"] == 9


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("click", {"frame_x": 10}),
        ("click", {"frame_x": -1, "frame_y": 20}),
        ("click", {"frame_x": float("nan"), "frame_y": 20}),
        ("click", {"frame_x": 10, "frame_y": 20, "x": 10, "y": 20}),
        ("scroll", {"amount": -120}),
        ("drag", {"frame_x1": 1, "frame_y1": 2}),
        ("draw_strokes", {"frame_strokes": [[[1, 2]]]}),
    ],
)
def test_invalid_frame_coordinates_fail_atomically(monkeypatch, tool_name, args):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("use the current real frame")
    runner.session.hwnd = 77
    runner.session._screen_epoch = 5
    runner._visual_frame_hwnd = 77
    runner._visual_frame_epoch = 5
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "frame"
    region = Region(10, 20, 800, 600)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: region)
    original = json.loads(json.dumps(args)) if tool_name != "click" or not any(
        isinstance(value, float) for value in args.values()
    ) else dict(args)

    with pytest.raises(ValueError):
        runner._map_normalized_frame_coordinates(tool_name, args)

    if not any(isinstance(value, float) and value != value for value in args.values()):
        assert args == original


def test_native_physical_coordinates_are_blocked_without_dispatch(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the visible button")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "synthetic-frame"
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(10, 20, 800, 600))
    monkeypatch.setattr(
        runner,
        "_preflight_visual_frame",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("invalid physical coordinates reached visual preflight")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(AssertionError("physical model coordinates executed")),
    )

    runner._handle_tool_calls([_tool_call("one", "click", {"x": 100, "y": 120})])

    payload = json.loads(runner._tool_history[-1][2])
    assert payload["blocked"] is True
    assert payload["frame_coordinate_invalid"] is True
    assert "rejected model-supplied physical" in payload["error"]


@pytest.mark.parametrize(
    ("tool_name", "tool_args"),
    [
        ("click", {"x": 100, "y": 120, "label": "Continue"}),
        (
            "run_action_batch",
            {"actions": [{"action": "click", "x": 100, "y": 120, "label": "Continue"}]},
        ),
    ],
)
def test_native_semantic_hint_cannot_bypass_physical_coordinate_rejection(
    monkeypatch, tool_name, tool_args,
):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the visible Continue button")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "synthetic-frame"
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(10, 20, 800, 600))
    monkeypatch.setattr(
        runner,
        "_preflight_visual_frame",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("semantic physical-coordinate bypass reached visual preflight")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("physical coordinates with a semantic hint executed")
        ),
    )

    runner._handle_tool_calls([_tool_call("bad", tool_name, tool_args)])

    payload = json.loads(runner._tool_history[-1][2])
    assert payload["blocked"] is True
    assert payload["frame_coordinate_invalid"] is True
    assert "rejected model-supplied physical" in payload["error"]


def test_native_batch_coordinates_cannot_bypass_frame_mapping(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click once from the current grounded frame")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (100, 200, 1000, 500)
    runner._last_visual_feedback_b64 = "synthetic-frame"
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(100, 200, 1000, 500))
    calls = []
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda name, args: calls.append((name, args)) or ToolResult('{"ok": true}'),
    )
    monkeypatch.setattr(runner, "_inject_visual_frame", lambda **_kwargs: True)

    runner._handle_tool_calls([_tool_call("bad", "run_action_batch", {
        "actions": [{"action": "click", "x": 20, "y": 30}],
    })])
    bad = json.loads(runner._tool_history[-1][2])
    assert bad["frame_coordinate_invalid"] is True
    assert calls == []

    runner._handle_tool_calls([_tool_call("good", "run_action_batch", {
        "actions": [{"action": "click", "frame_x": 250, "frame_y": 400}],
    })])
    assert calls[0][0] == "run_action_batch"
    mapped = calls[0][1]["actions"][0]
    assert (mapped["x"], mapped["y"]) == (350, 400)
    assert mapped["coordinate_space"] == "current_frame_normalized_0_1000"


@pytest.mark.parametrize(
    "actions",
    [
        [
            {"action": "click", "frame_x": 100, "frame_y": 120},
            {
                "action": "drag",
                "frame_x1": 200,
                "frame_y1": 220,
                "frame_x2": 700,
                "frame_y2": 720,
            },
        ],
        [
            {"action": "click", "element_id": 7},
            {
                "tool": "click_element",
                "params": {"label": "Continue", "fallback_text": ["Next"]},
            },
        ],
    ],
    ids=["frame-coordinates", "element-id-and-semantic-hint"],
)
def test_native_batch_rejects_two_spatial_mutations_before_preflight_or_dispatch(
    monkeypatch, actions,
):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("act only from each new real frame")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "synthetic-frame"
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(10, 20, 800, 600))
    monkeypatch.setattr(
        runner,
        "_preflight_visual_frame",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("multi-spatial native batch reached visual preflight")
        ),
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("multi-spatial native batch executed any child action")
        ),
    )

    runner._handle_tool_calls([
        _tool_call("multi", "run_action_batch", {"actions": actions}),
    ])

    payload = json.loads(runner._tool_history[-1][2])
    assert payload["blocked"] is True
    assert payload["frame_coordinate_invalid"] is True
    assert payload["native_batch_spatial_limit"] is True
    assert "at most one spatial UI mutation" in payload["error"]
    assert "No batch action was executed" in payload["error"]
    assert "next real post-action frame" in payload["fast_path_advice"]


def test_native_batch_allows_one_spatial_mutation_plus_keyboard_input(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the field and type the exact text")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (100, 200, 1000, 500)
    runner._last_visual_feedback_b64 = "synthetic-frame"
    region = Region(100, 200, 1000, 500)
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: region)
    preflights = []
    monkeypatch.setattr(
        runner,
        "_preflight_visual_frame",
        lambda *args: preflights.append(args) or ("", 0.0, "", region),
    )
    calls = []
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda name, args: calls.append((name, args)) or ToolResult('{"ok": true}'),
    )
    monkeypatch.setattr(runner, "_inject_visual_frame", lambda **_kwargs: True)

    runner._handle_tool_calls([_tool_call("one", "run_action_batch", {
        "actions": [
            {
                "action": "click",
                "frame_x": 250,
                "frame_y": 400,
                "label": "Search field",
            },
            {"action": "type_text", "text": "exact query"},
            {"action": "hotkey", "keys": ["enter"]},
        ],
    })])

    assert len(preflights) == 1
    assert len(calls) == 1
    name, mapped_args = calls[0]
    assert name == "run_action_batch"
    click, typed, hotkey = mapped_args["actions"]
    assert (click["x"], click["y"]) == (350, 400)
    assert click["label"] == "Search field"
    assert click["coordinate_space"] == "current_frame_normalized_0_1000"
    assert typed == {"action": "type_text", "text": "exact query"}
    assert hotkey == {"action": "hotkey", "keys": ["enter"]}


def test_preflight_external_change_cancels_action_and_returns_new_frame(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the still-visible target")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "x" * 256
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(10, 20, 800, 600))
    capture_region = Region(10, 20, 800, 600)
    monkeypatch.setattr(
        runner,
        "_preflight_visual_frame",
        lambda *_args: ("new-frame", 0.12, "", capture_region),
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(AssertionError("changed-frame action executed")),
    )
    injected = []
    monkeypatch.setattr(
        runner,
        "_inject_visual_frame",
        lambda **kwargs: injected.append(kwargs) or True,
    )

    runner._handle_tool_calls([
        _tool_call("one", "click", {"frame_x": 100, "frame_y": 120}),
    ])

    payload = json.loads(runner._tool_history[-1][2])
    assert payload["blocked"] is True
    assert payload["visual_frame_changed"] is True
    assert injected == [{
        "phase": "preflight",
        "action": "visual_preflight_changed",
        "image_b64": "new-frame",
        "frame_region": capture_region,
    }]


def test_preflight_external_change_atomically_cancels_normalized_action_batch(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click one visible target and type in one grounded batch")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "x" * 256
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(10, 20, 800, 600))
    capture_region = Region(10, 20, 800, 600)
    monkeypatch.setattr(
        runner,
        "_preflight_visual_frame",
        lambda *_args: ("new-frame", 0.12, "", capture_region),
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("changed-frame batch executed partially")
        ),
    )
    injected = []
    monkeypatch.setattr(
        runner,
        "_inject_visual_frame",
        lambda **kwargs: injected.append(kwargs) or True,
    )

    runner._handle_tool_calls([_tool_call("one", "run_action_batch", {
        "actions": [
            {"action": "click", "frame_x": 100, "frame_y": 120},
            {"action": "type_text", "text": "exact input"},
        ],
    })])

    payload = json.loads(runner._tool_history[-1][2])
    assert payload["blocked"] is True
    assert payload["visual_frame_changed"] is True
    assert injected == [{
        "phase": "preflight",
        "action": "visual_preflight_changed",
        "image_b64": "new-frame",
        "frame_region": capture_region,
    }]


def test_preflight_replacement_frame_invalidates_old_element_and_observe_cache(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the target only if it belongs to the current frame")
    runner._visual_closed_loop_active = True
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    runner.session._screen_epoch = 2
    runner._visual_frame_hwnd = 11
    runner._visual_frame_epoch = 2
    runner._visual_frame_region = (10, 20, 800, 600)
    runner._last_visual_feedback_b64 = "old-frame" * 32
    region = Region(10, 20, 800, 600)
    stale = Snapshot(
        hwnd=11,
        title="Old UI",
        region=region,
        elements=[UIElement(
            elem_id=7,
            source="uia",
            role="button",
            text="Old target",
            left=100,
            top=120,
            width=80,
            height=30,
            cx=140,
            cy=135,
        )],
    )
    cache_key = (11, True, "normal", "full", (), 2)
    runner.session.snapshot = stale
    runner.session.store_observe_cache(cache_key, stale)
    runner.session._last_native_frame_capture = {
        "image_b64": runner._last_visual_feedback_b64,
        "hwnd": 11,
        "region": region,
    }
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: region)
    monkeypatch.setattr(
        runner,
        "_preflight_visual_frame",
        lambda *_args: ("replacement-frame" * 16, 0.25, "", region),
    )
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda *_args: (_ for _ in ()).throw(AssertionError("stale element action executed")),
    )

    runner._handle_tool_calls([_tool_call("one", "click", {"element_id": 7})])

    assert runner.session.snapshot is None
    assert runner.session._observe_cache is None
    assert runner.session._last_native_frame_capture is None
    assert runner.session.cached_observe(cache_key, 2.0) is None
    stale_click = runner.session._t_click({"element_id": 7})
    assert "observe" in stale_click.text


def test_preflight_ignores_unrelated_animation_when_grounded_target_roi_is_stable(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the stable target")
    runner.session.hwnd = 11
    runner._last_visual_feedback_b64 = "old" * 128
    monkeypatch.setattr(runner, "_has_fresh_visual_grounding", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(0, 0, 800, 600))
    monkeypatch.setattr(
        "winpilot.agent.loop.capture_window_jpeg_b64",
        lambda _hwnd, **_kwargs: "new" * 128,
    )
    monkeypatch.setattr("winpilot.agent.loop.jpeg_diff_ratio", lambda *_args: 0.18)
    monkeypatch.setattr(
        "winpilot.agent.loop.jpeg_diff_ratio_in_normalized_roi",
        lambda *_args: 0.0,
    )
    events = []
    monkeypatch.setattr(
        "winpilot.agent.loop.BUS.publish",
        lambda name, **payload: events.append((name, payload)),
    )

    changed_frame, ratio, error, capture_region = runner._preflight_visual_frame(
        "click", {"frame_x": 500, "frame_y": 500},
    )

    assert (changed_frame, ratio, error) == ("", 0.0, "")
    assert capture_region == Region(0, 0, 800, 600)
    event = events[-1][1]
    assert event["decision_scope"] == "action_target_roi"
    assert event["global_change_ratio"] == 0.18
    assert event["target_change_ratio"] == 0.0
    assert event["target_roi"][0] < 500 < event["target_roi"][2]
    assert event["target_roi"][1] < 500 < event["target_roi"][3]


def test_preflight_allows_semantic_draw_to_reground_after_paint_settles(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("draw a polished dog in Paint")
    runner.session.hwnd = 11
    runner._last_visual_feedback_b64 = "old" * 128
    monkeypatch.setattr(runner, "_has_fresh_visual_grounding", lambda: True)
    monkeypatch.setattr(runner, "_visual_preflight_target_roi", lambda *_args: None)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(0, 0, 800, 600))
    monkeypatch.setattr(
        "winpilot.agent.loop.capture_window_jpeg_b64",
        lambda _hwnd, **_kwargs: "new" * 128,
    )
    monkeypatch.setattr("winpilot.agent.loop.jpeg_diff_ratio", lambda *_args: 0.54)
    events = []
    monkeypatch.setattr(
        "winpilot.agent.loop.BUS.publish",
        lambda name, **payload: events.append((name, payload)),
    )

    draw_frame, draw_ratio, draw_error, _region = runner._preflight_visual_frame(
        "execute_subgoal",
        {"subgoal": "draw dog", "draws": [{"shape": "ellipse", "box": [200, 200, 300, 220]}]},
    )
    click_frame, click_ratio, click_error, _region = runner._preflight_visual_frame(
        "click", {"label": "Brushes"},
    )

    assert (draw_frame, draw_ratio, draw_error) == ("", 0.0, "")
    assert events[-2][1]["decision_scope"] == "controller_regrounded_drawing"
    assert events[-2][1]["global_change_ratio"] == 0.54
    assert (click_frame, click_ratio, click_error) == ("new" * 128, 0.54, "")


def test_preflight_cancels_small_global_change_when_grounded_target_roi_changed(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the target only if its pixels are stable")
    runner.session.hwnd = 11
    runner._last_visual_feedback_b64 = "old" * 128
    monkeypatch.setattr(runner, "_has_fresh_visual_grounding", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(0, 0, 800, 600))
    monkeypatch.setattr(
        "winpilot.agent.loop.capture_window_jpeg_b64",
        lambda _hwnd, **_kwargs: "new" * 128,
    )
    monkeypatch.setattr("winpilot.agent.loop.jpeg_diff_ratio", lambda *_args: 0.0001)
    monkeypatch.setattr(
        "winpilot.agent.loop.jpeg_diff_ratio_in_normalized_roi",
        lambda *_args: 0.20,
    )

    changed_frame, ratio, error, capture_region = runner._preflight_visual_frame(
        "click", {"frame_x": 500, "frame_y": 500},
    )

    assert (changed_frame, ratio, error) == ("new" * 128, 0.20, "")
    assert capture_region == Region(0, 0, 800, 600)


def test_preflight_cancels_same_pixel_window_move_before_coordinate_mapping(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click only while the target window geometry is unchanged")
    runner.session.hwnd = 11
    runner._visual_frame_region = (0, 0, 800, 600)
    runner._last_visual_feedback_b64 = "same-pixels" * 32
    moved = Region(120, 80, 800, 600)
    monkeypatch.setattr(runner, "_has_fresh_visual_grounding", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: moved)
    monkeypatch.setattr(
        "winpilot.agent.loop.capture_window_jpeg_b64",
        lambda _hwnd, **_kwargs: runner._last_visual_feedback_b64,
    )
    monkeypatch.setattr(
        "winpilot.agent.loop.jpeg_diff_ratio",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("geometry changes must cancel before pixel equality can pass")
        ),
    )
    events = []
    monkeypatch.setattr(
        "winpilot.agent.loop.BUS.publish",
        lambda name, **payload: events.append((name, payload)),
    )

    changed_frame, ratio, error, capture_region = runner._preflight_visual_frame(
        "click", {"frame_x": 500, "frame_y": 500},
    )

    assert (changed_frame, ratio, error) == (runner._last_visual_feedback_b64, 1.0, "")
    assert capture_region == moved
    assert events[-1][1]["geometry_changed"] is True
    assert events[-1][1]["decision_scope"] == "window_geometry"


def test_preflight_injection_binds_image_to_exact_capture_region(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("inspect the replacement frame")
    runner.messages = [{"role": "system", "content": "sys"}]
    runner.session.hwnd = 11
    captured = Region(120, 80, 800, 600)
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: True)
    monkeypatch.setattr(
        "winpilot.agent.loop.window_region",
        lambda _hwnd: (_ for _ in ()).throw(
            AssertionError("injection re-queried geometry instead of using capture metadata")
        ),
    )

    injected = runner._inject_visual_frame(
        phase="preflight",
        action="visual_preflight_changed",
        image_b64="replacement-frame",
        frame_region=captured,
    )

    assert injected is True
    assert runner._visual_frame_region == (120, 80, 800, 600)
    assert runner._last_visual_feedback_b64 == "replacement-frame"


def test_normalized_roi_diff_ignores_pixels_outside_target():
    import base64

    import cv2
    import numpy as np

    from winpilot.utils.image_capture import jpeg_diff_ratio_in_normalized_roi

    before = np.zeros((100, 100, 3), dtype=np.uint8)
    after = before.copy()
    after[:20, :20] = 255

    def encode(image) -> str:
        ok, payload = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        assert ok
        return base64.b64encode(payload.tobytes()).decode()

    before_b64, after_b64 = encode(before), encode(after)
    changed = jpeg_diff_ratio_in_normalized_roi(
        before_b64, after_b64, (0, 0, 250, 250),
    )
    stable = jpeg_diff_ratio_in_normalized_roi(
        before_b64, after_b64, (750, 750, 1000, 1000),
    )

    assert changed is not None and changed > 0.5
    assert stable == 0.0


def test_preflight_threshold_is_strict_and_reported(monkeypatch):
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.screenshot import Region

    runner = AgentRunner("click the target if the grounded frame is still current")
    runner.session.hwnd = 11
    runner._last_visual_feedback_b64 = "x" * 256
    monkeypatch.setattr(runner, "_has_fresh_visual_grounding", lambda: True)
    monkeypatch.setattr("winpilot.agent.loop.window_region", lambda _hwnd: Region(0, 0, 800, 600))
    monkeypatch.setattr(
        "winpilot.agent.loop.capture_window_jpeg_b64",
        lambda _hwnd, region=None: "y" * 256,
    )
    ratios = iter([0.0025, 0.002501])
    monkeypatch.setattr("winpilot.agent.loop.jpeg_diff_ratio", lambda *_args: next(ratios))
    events = []
    monkeypatch.setattr(
        "winpilot.agent.loop.BUS.publish",
        lambda name, **payload: events.append((name, payload)),
    )

    unchanged_frame, unchanged_ratio, unchanged_error, unchanged_region = runner._preflight_visual_frame()
    changed_frame, changed_ratio, changed_error, changed_region = runner._preflight_visual_frame()

    assert (unchanged_frame, unchanged_ratio, unchanged_error) == ("", 0.0025, "")
    assert (changed_frame, changed_ratio, changed_error) == ("y" * 256, 0.002501, "")
    assert unchanged_region == Region(0, 0, 800, 600)
    assert changed_region == Region(0, 0, 800, 600)
    assert events[0][0] == "visual_preflight"
    assert events[0][1]["threshold"] == 0.0025
    assert events[0][1]["changed"] is False
    assert events[1][1]["changed"] is True


@pytest.mark.parametrize("style", ["full", "compact"])
def test_native_prompt_uses_runtime_frame_coordinates(style):
    from winpilot.agent.prompts import system_prompt

    prompt = system_prompt(style=style, main_model_has_vision=True)

    assert "frame_*" in prompt
    assert "0..1000" in prompt
    assert "never" in prompt.casefold()
    assert "at most one spatial mutation" in prompt
    assert "next real post-action frame" in prompt
