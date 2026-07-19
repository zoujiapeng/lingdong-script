from __future__ import annotations

import json
from types import SimpleNamespace

from winpilot.agent.interaction_tools import InteractionToolMixin
from winpilot.agent.loop import AgentRunner
from winpilot.agent.tool_result import ToolResult
from winpilot.agent.tools import ToolSession


def test_explicit_double_click_reuses_click_adapter_and_forces_double() -> None:
    seen: list[dict] = []

    def click(args: dict) -> ToolResult:
        seen.append(dict(args))
        return ToolResult('{"ok": true}', replay={"tool": "click"})

    fake = SimpleNamespace(_t_click=click)
    result = InteractionToolMixin._t_double_click(
        fake,
        {"x": 12, "y": 34, "double": False},
    )

    assert seen == [{"x": 12, "y": 34, "double": True}]
    assert result.replay == {"tool": "double_click", "double": True}


def test_double_click_dispatch_is_a_mutation_and_advances_epoch(monkeypatch) -> None:
    session = ToolSession()
    session.world_enabled = False
    seen: list[dict] = []
    monkeypatch.setattr(
        "winpilot.agent.tools.scope_guard.ensure_scope",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "winpilot.agent.tools.scope_guard.take_scope_recovery",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(session, "_action_screen_delta_enabled", lambda *_args: False)
    monkeypatch.setattr(
        session,
        "_t_click",
        lambda args: seen.append(dict(args)) or ToolResult(json.dumps({
            "ok": True,
            "action_executed": True,
            "received": args,
        })),
    )

    before = session._screen_epoch
    result = session.dispatch("double_click", {"x": 12, "y": 34})

    assert json.loads(result.text)["ok"] is True
    assert seen == [{"x": 12, "y": 34, "double": True}]
    assert session._screen_epoch == before + 1


def test_fast_controller_wrap_canonicalizes_double_click_to_click_double_true() -> None:
    payload = AgentRunner._normalise_fast_wrapped_action(
        "double_click",
        {"x": 12, "y": 34, "double": False},
    )

    assert payload == {"action": "click", "x": 12, "y": 34, "double": True}


def test_fast_controller_dispatches_double_click_as_one_click_transaction(monkeypatch) -> None:
    runner = AgentRunner("operate the visible interface")
    seen: list[tuple[str, dict]] = []

    def dispatch(name: str, args: dict) -> ToolResult:
        seen.append((name, args))
        return ToolResult(json.dumps({
            "ok": True,
            "results": [{
                "action": "click",
                "ok": True,
                "data": {"ok": True, "verified": False},
            }],
        }))

    monkeypatch.setattr(runner.session, "dispatch", dispatch)
    result = runner._dispatch_with_fast_visual_wrap(
        "double_click",
        {"x": 12, "y": 34},
    )

    assert seen == [(
        "run_action_batch",
        {"actions": [{"action": "click", "x": 12, "y": 34, "double": True}]},
    )]
    payload = json.loads(result.text)
    assert payload["ok"] is True
    assert payload["tool"] == "double_click"
    assert payload["source_action"] == "click"


def test_no_progress_distinguishes_hover_single_double_and_mouse_button() -> None:
    runner = AgentRunner("operate the visible interface")
    runner._visual_frame_region = (0, 0, 1920, 1080)
    runner._visual_submitted_size = (1120, 630)

    point = {"image_x": 240, "image_y": 310}
    signature = lambda name, args: runner._native_spatial_action_signature(
        name,
        args,
        frame_region=runner._visual_frame_region,
        submitted_size=runner._visual_submitted_size,
    )
    single = signature("click", point)
    explicit_double = signature("double_click", point)
    compatible_double = runner._native_spatial_action_signature(
        "click",
        {**point, "double": True},
        frame_region=runner._visual_frame_region,
        submitted_size=runner._visual_submitted_size,
    )
    hover = signature("move_mouse", point)
    right_click = signature("click", {**point, "button": "right"})

    assert explicit_double == compatible_double
    assert len({single, explicit_double, hover, right_click}) == 4

    runner._record_native_visual_progress(single, 0.0, comparable=True)
    runner._record_native_visual_progress(single, 0.0, comparable=True)

    assert runner._native_no_progress_guard("click", point) is not None
    assert runner._native_no_progress_guard("double_click", point) is None
    assert runner._native_no_progress_guard("move_mouse", point) is None
    assert runner._native_no_progress_guard(
        "click", {**point, "button": "right"},
    ) is None
