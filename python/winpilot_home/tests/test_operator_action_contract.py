from __future__ import annotations

from types import SimpleNamespace


def test_atomic_action_parser_preserves_metadata_and_top_level_precedence():
    from winpilot.fast.action_contract import parse_atomic_action

    parsed = parse_atomic_action({
        "tool": "Click-Element",
        "params": {
            "label": "old",
            "expect": {"text": "old"},
            "trace_metadata": {"source": "model"},
        },
        "label": "new",
        "name": "Save",
        "expect": {"text": "new"},
    })

    assert parsed is not None
    assert parsed.name == "click_element"
    assert parsed.inputs == {
        "label": "new",
        "name": "Save",
        "expect": {"text": "new"},
        "trace_metadata": {"source": "model"},
    }


def test_atomic_action_parser_matches_controller_shorthand_and_skips_malformed_children():
    from winpilot.fast.action_contract import iter_atomic_actions, parse_atomic_action
    from winpilot.fast.controller import _normalise_action

    assert parse_atomic_action({"hotkey": ["ctrl", "a"]}).inputs == {
        "keys": ["ctrl", "a"],
    }
    assert parse_atomic_action({"type_text": "hello"}).inputs == {"text": "hello"}
    assert _normalise_action({"action": "click", "name": "Save", "x": 4, "y": 5}) == (
        "click",
        {"name": "Save", "x": 4, "y": 5},
    )
    parsed = list(iter_atomic_actions("run_action_batch", {
        "actions": [None, {"hotkey": ["ctrl", "a"]}, "bad", {"type_text": "hello"}],
    }))
    assert [(item.ordinal, item.name, item.source_path) for item in parsed] == [
        (0, "hotkey", "actions[1]"),
        (1, "type_text", "actions[3]"),
    ]


def test_batch_validator_reuses_precanonicalized_actions_without_reparsing(monkeypatch):
    from winpilot.fast import action_contract

    canonical, errors = action_contract.canonicalize_batch_actions([
        {"action": "click", "x": 10, "y": 20},
        {"hotkey": ["ctrl", "a"]},
    ])

    assert errors == []
    assert [(item.ordinal, item.name) for item in canonical] == [
        (0, "click"),
        (1, "hotkey"),
    ]
    monkeypatch.setattr(
        action_contract,
        "parse_atomic_action",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("canonical actions must not be parsed again")
        ),
    )
    assert action_contract.validate_batch_actions(canonical) == []


def test_focus_window_contract_accepts_either_window_identity():
    from winpilot.fast.action_contract import canonicalize_batch_actions

    by_hwnd, hwnd_errors = canonicalize_batch_actions([
        {"action": "focus_window", "hwnd": 42},
    ])
    by_title, title_errors = canonicalize_batch_actions([
        {"action": "focus_window", "title": "Nebula"},
    ])

    assert len(by_hwnd) == 1 and hwnd_errors == []
    assert len(by_title) == 1 and title_errors == []


def test_execute_subgoal_atomic_actions_have_stable_declared_order():
    from winpilot.fast.action_contract import iter_atomic_actions

    parsed = list(iter_atomic_actions("execute_subgoal", {
        "click": {"label": "one"},
        "clicks": [{"label": "two"}],
        "input": {"text": "hello"},
        "draw": {"strokes": []},
        "drag": {"x1": 1, "y1": 2, "x2": 3, "y2": 4},
        "draws": [{"strokes": []}],
        "drags": [{"x1": 5, "y1": 6, "x2": 7, "y2": 8}],
        "actions": [{"action": "observe"}],
    }, include_container=True))

    assert [(item.name, item.source_path, item.is_container) for item in parsed] == [
        ("execute_subgoal", "$", True),
        ("click_element", "click", False),
        ("click_element", "clicks[0]", False),
        ("type_into_element", "input", False),
        ("draw_strokes", "draw", False),
        ("drag", "drag", False),
        ("draw_strokes", "draws[0]", False),
        ("drag", "drags[0]", False),
        ("observe", "actions[0]", False),
    ]


def test_action_envelope_binds_window_frame_region_and_policy():
    from winpilot.fast.action_contract import build_action_envelope
    from winpilot.utils.screenshot import Region

    envelope = build_action_envelope(
        "click",
        {"x": 320, "y": 240},
        action_id="batch-1:2",
        hwnd=55,
        frame_id="frame-9",
        epoch=4,
        region=Region(100, 80, 800, 600),
    ).to_dict()

    assert envelope["action_id"] == "batch-1:2"
    assert envelope["type"] == "click"
    assert envelope["target"] == {
        "hwnd": 55,
        "reference_system": "screen_physical_px",
        "source_reference_system": None,
        "frame_id": "frame-9",
        "epoch": 4,
        "region": {"left": 100, "top": 80, "width": 800, "height": 600},
    }
    assert envelope["policy"]["requires_post_frame"] is True
    assert envelope["policy"]["interruptible"] is False


def test_action_envelope_distinguishes_frame_and_canvas_coordinates():
    from winpilot.fast.action_contract import build_action_envelope

    frame = build_action_envelope(
        "click",
        {"frame_x": 500, "frame_y": 250},
        action_id="a",
    )
    canvas = build_action_envelope(
        "draw_strokes",
        {"normalized_strokes": [[[10, 20], [30, 40]]]},
        action_id="b",
    )

    assert frame.target.reference_system == "frame_normalized_0_1000"
    assert canvas.target.reference_system == "canvas_normalized_0_1000"

    mapped = build_action_envelope(
        "click",
        {
            "x": 500,
            "y": 250,
            "coordinate_space": "current_frame_normalized_0_1000",
            "source_frame_coordinates": {"frame_x": 500, "frame_y": 250},
        },
        action_id="c",
    )
    assert mapped.target.reference_system == "screen_physical_px"
    assert mapped.target.source_reference_system == "frame_normalized_0_1000"


def test_controller_emits_typed_action_lifecycle_envelope():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast.controller import FastGuiController
    from winpilot.utils.log import BUS
    from winpilot.utils.screenshot import Region

    controller = object.__new__(FastGuiController)
    controller.session = SimpleNamespace(
        run_id="run-1",
        hwnd=77,
        _screen_epoch=3,
        _visual_frame_epoch=2,
        _visual_frame_id="frame-4",
        _visual_frame_region=Region(10, 20, 900, 700),
        snapshot=None,
    )
    events = []
    BUS.add_sink(events.append)
    try:
        envelope = controller._publish_child_start(
            "batch-8", 1, "click", {"x": 100, "y": 200},
        )
        controller.session._screen_epoch = 99
        controller.session._visual_frame_id = "newer-frame"
        controller._publish_child_done(
            "batch-8",
            1,
            "click",
            {"x": 100, "y": 200},
            ToolResult('{"ok": true}'),
            12,
            True,
            {"ok": True},
            envelope,
        )
    finally:
        BUS.remove_sink(events.append)

    lifecycle = [
        event for event in events
        if event.get("event_name") in {"action.started", "action.finished"}
    ]
    assert [event["event_name"] for event in lifecycle] == [
        "action.started", "action.finished",
    ]
    assert {event["action_id"] for event in lifecycle} == {"batch-8:1"}
    assert lifecycle[0]["action_envelope"]["target"]["hwnd"] == 77
    assert lifecycle[0]["action_envelope"]["target"]["frame_id"] == "frame-4"
    assert lifecycle[0]["action_envelope"]["target"]["epoch"] == 2
    assert lifecycle[0]["action_envelope"] == lifecycle[1]["action_envelope"]


def test_agent_runner_direct_tool_emits_one_immutable_typed_lifecycle(monkeypatch):
    import json
    from types import SimpleNamespace

    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult
    from winpilot.utils.log import BUS

    runner = AgentRunner("click the visible target")
    runner._visual_closed_loop_active = False
    runner._visual_frame_id = 7
    runner._visual_frame_epoch = 3
    runner._visual_frame_region = (10, 20, 900, 700)
    runner.session.hwnd = 55
    monkeypatch.setattr(
        runner,
        "_dispatch_with_fast_visual_wrap",
        lambda _name, _args: ToolResult(
            '{"ok": false, "blocked": true, "cooperative_input": true, '
            '"error": "user owns another window"}'
        ),
    )
    monkeypatch.setattr(
        runner._failure_recovery_layer,
        "handle_tool_result",
        lambda **_kwargs: None,
    )
    call = SimpleNamespace(
        id="direct-1",
        function=SimpleNamespace(name="click", arguments=json.dumps({"x": 100, "y": 200})),
    )
    events = []
    BUS.add_sink(events.append)
    try:
        assert runner._handle_tool_calls([call]) is None
    finally:
        BUS.remove_sink(events.append)

    lifecycle = [
        event for event in events
        if event.get("action_id") == "direct-1"
        and event.get("event_name") in {"action.started", "action.finished"}
    ]
    assert [event["event_name"] for event in lifecycle] == [
        "action.started", "action.finished",
    ]
    assert lifecycle[0]["action_envelope"] == lifecycle[1]["action_envelope"]
    assert lifecycle[1]["result_meta"]["cooperative_input"] is True
    assert lifecycle[1]["result_meta"]["blocked"] is True
    assert lifecycle[0]["action_envelope"]["target"] == {
        "hwnd": 55,
        "reference_system": "screen_physical_px",
        "source_reference_system": None,
        "frame_id": 7,
        "epoch": 3,
        "region": {"left": 10, "top": 20, "width": 900, "height": 700},
    }


def test_supported_actions_is_stable_and_minimal_contract_projection():
    from winpilot.agent.tool_schemas import tool_schemas
    from winpilot.fast.action_contract import supported_actions

    actions = supported_actions()

    assert actions == tuple(sorted(actions))
    assert "click" in actions
    assert "draw_strokes" in actions
    assert "ensure_document_surface" not in actions
    assert "run_shell" not in actions

    batch = next(
        tool for tool in tool_schemas()
        if tool["function"]["name"] == "run_action_batch"
    )
    projected = (
        batch["function"]["parameters"]["properties"]["actions"]
        ["items"]["properties"]["action"]["enum"]
    )
    assert projected == list(actions)

    native_batch = next(
        tool for tool in tool_schemas(native_vision=True)
        if tool["function"]["name"] == "run_action_batch"
    )
    native_projected = (
        native_batch["function"]["parameters"]["properties"]["actions"]
        ["items"]["properties"]["action"]["enum"]
    )
    assert native_projected == list(actions)


def test_top_level_controller_transactions_require_post_frame():
    from winpilot.fast.action_contract import build_action_envelope

    for name in ("run_action_batch", "execute_subgoal", "open_app"):
        envelope = build_action_envelope(name, {}, action_id=name)
        assert envelope.policy.requires_post_frame is True
