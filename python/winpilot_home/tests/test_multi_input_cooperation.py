from __future__ import annotations

import json

from winpilot.action.raw_device import ClickCount, MouseButton, RawBackendResult


def test_multi_input_cooperation_is_explicitly_opt_in():
    from winpilot.config import _DEFAULTS

    controller = _DEFAULTS["controller"]
    assert controller["multi_input_cooperation"] is False
    assert controller["cooperative_cursor_tolerance_px"] == 12


def test_multi_input_mode_implies_no_mouse_steal(monkeypatch):
    from winpilot.action import executor

    monkeypatch.setattr(
        executor.CONFIG,
        "get",
        lambda *keys, default=None: (
            True if keys == ("controller", "multi_input_cooperation") else default
        ),
    )

    assert executor.multi_input_cooperation_enabled() is True
    assert executor._no_mouse_steal() is True


def test_cooperative_keyboard_guard_requires_bound_foreground(monkeypatch):
    from winpilot.action import executor

    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)

    allowed = executor.cooperative_input_guard(
        "type_text", 123, foreground_hwnd=123,
    )
    blocked = executor.cooperative_input_guard(
        "type_text", 123, foreground_hwnd=999,
    )

    assert allowed is None
    assert blocked["blocked"] is True
    assert blocked["cooperative_input"] is True
    assert blocked["target_hwnd"] == 123
    assert blocked["foreground_hwnd"] == 999


def test_cooperative_click_never_falls_back_to_physical_on_other_foreground(monkeypatch):
    from winpilot.action import executor

    calls = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: True)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)
    monkeypatch.setattr(
        executor, "_post_click",
        lambda *_args, **_kwargs: RawBackendResult(False, {}),
    )
    monkeypatch.setattr(executor.win32gui, "GetForegroundWindow", lambda: 999)
    monkeypatch.setattr(
        executor,
        "_raw_click",
        lambda *_args, **_kwargs: calls.append("physical"),
    )

    result = executor.click(10, 20, hwnd=123)

    assert result["blocked"] is True
    assert result["cooperative_input"] is True
    assert calls == []


def test_cooperative_targetless_click_cannot_post_to_arbitrary_window(monkeypatch):
    from winpilot.action import executor

    posted = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: True)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)
    monkeypatch.setattr(
        executor, "_post_click",
        lambda *_args, **_kwargs: posted.append(True) or True,
    )

    result = executor.click(10, 20, hwnd=None)

    assert result["ok"] is False
    assert result["blocked"] is True
    assert result["cooperative_input"] is True
    assert posted == []


def test_cooperative_cursor_restore_does_not_overwrite_human_move(monkeypatch):
    from winpilot.action import executor

    restored = []
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)
    monkeypatch.setattr(executor, "_cursor_pos", lambda: (400, 300))
    monkeypatch.setattr(executor, "_restore_cursor", restored.append)
    monkeypatch.setattr(
        executor.CONFIG,
        "get",
        lambda *keys, default=None: default,
    )

    did_restore = executor._restore_cursor_after_agent_input(
        (25, 35), (100, 100),
    )

    assert did_restore is False
    assert restored == []


def test_cooperative_cursor_restore_restores_when_agent_still_owns_cursor(monkeypatch):
    from winpilot.action import executor

    restored = []
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)
    monkeypatch.setattr(executor, "_cursor_pos", lambda: (104, 96))
    monkeypatch.setattr(executor, "_restore_cursor", restored.append)
    monkeypatch.setattr(
        executor.CONFIG,
        "get",
        lambda *keys, default=None: default,
    )

    did_restore = executor._restore_cursor_after_agent_input(
        (25, 35), (100, 100),
    )

    assert did_restore is True
    assert restored == [(25, 35)]


def test_cooperative_typing_stops_if_user_changes_foreground(monkeypatch):
    from winpilot.action import executor

    foregrounds = iter((123, 123, 999))
    sent = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)
    monkeypatch.setattr(
        executor.win32gui,
        "GetForegroundWindow",
        lambda: next(foregrounds),
    )
    monkeypatch.setattr(executor, "_should_type_via_vk", lambda _text: False)
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.append(inputs))
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    result = executor.type_text("ab", hwnd=123, interval_s=0)

    assert result["ok"] is False
    assert result["blocked"] is True
    assert result["typed"] == 1
    assert result["requested"] == 2
    assert len(sent) == 1


def test_cooperative_hotkey_is_not_sent_to_unrelated_foreground(monkeypatch):
    from winpilot.action import executor

    sent = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)
    monkeypatch.setattr(executor.win32gui, "GetForegroundWindow", lambda: 999)
    monkeypatch.setattr(executor, "_raw_press_keys", lambda keys: sent.append(keys))

    result = executor.press_keys(["ctrl", "s"], hwnd=123)

    assert result["blocked"] is True
    assert result["cooperative_input"] is True
    assert sent == []


def test_cooperative_mode_disables_physical_visual_servo(monkeypatch):
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.model import UIElement

    monkeypatch.setattr(
        "winpilot.fast.controller.CONFIG.get",
        lambda *keys, default=None: (
            True if keys == ("controller", "multi_input_cooperation") else default
        ),
    )
    controller = object.__new__(FastGuiController)
    element = UIElement(
        elem_id=1,
        text="Save",
        role="button",
        left=10,
        top=10,
        width=80,
        height=30,
        source="uia",
    )

    enabled, reason = controller._should_visual_servo(element, {})

    assert enabled is False
    assert reason == "cooperative_input"


def test_scope_guard_never_focuses_or_adopts_user_window_in_cooperative_mode(monkeypatch):
    from winpilot.agent import scope_guard

    focused = []
    adopted = []

    class Session:
        hwnd = 123
        task = "edit the target window"

        @staticmethod
        def _action_target_context():
            return {
                "target_hwnd": 123,
                "foreground_hwnd": 999,
                "target_title": "Target",
                "foreground_title": "User window",
            }

        @staticmethod
        def _window_related_to_target(_foreground, _target):
            return False

        @staticmethod
        def _same_window_process(_foreground, _target):
            return True

        @staticmethod
        def _remember_target(hwnd, *_args):
            adopted.append(hwnd)

    monkeypatch.setattr(scope_guard, "_window_alive", lambda _hwnd: True)
    monkeypatch.setattr(
        scope_guard.executor,
        "focus_window",
        lambda hwnd: focused.append(hwnd) or {"ok": True},
    )
    monkeypatch.setattr(
        scope_guard.CONFIG,
        "get",
        lambda *keys, default=None: (
            True if keys == ("controller", "multi_input_cooperation") else default
        ),
    )

    result = scope_guard.ensure_scope(Session(), "hotkey", {"keys": ["ctrl", "s"]})

    assert result is not None
    assert '"cooperative_input": true' in result.text
    assert focused == []
    assert adopted == []


def test_scope_guard_allows_background_safe_click_without_focus(monkeypatch):
    from winpilot.agent import scope_guard

    class Session:
        hwnd = 123
        task = "click the target"

        @staticmethod
        def _action_target_context():
            return {"target_hwnd": 123, "foreground_hwnd": 999}

        @staticmethod
        def _window_related_to_target(_foreground, _target):
            return False

    monkeypatch.setattr(scope_guard, "_window_alive", lambda _hwnd: True)
    monkeypatch.setattr(
        scope_guard.CONFIG,
        "get",
        lambda *keys, default=None: (
            True if keys == ("controller", "multi_input_cooperation") else default
        ),
    )
    monkeypatch.setattr(
        scope_guard.executor,
        "focus_window",
        lambda _hwnd: (_ for _ in ()).throw(AssertionError("must not focus")),
    )

    assert scope_guard.ensure_scope(Session(), "click", {"x": 10, "y": 20}) is None


def test_type_text_does_not_refocus_after_user_takes_window(monkeypatch):
    from types import SimpleNamespace

    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    state = {"foreground": 10}
    focused = []
    typed = []

    def cooperative_guard(_action, target):
        if state["foreground"] == target:
            return None
        return {
            "ok": False,
            "verified": False,
            "blocked": True,
            "cooperative_input": True,
        }

    def click(*_args, **_kwargs):
        state["foreground"] = 99
        return {"ok": True}

    fake_executor = SimpleNamespace(
        cooperative_input_guard=cooperative_guard,
        uia_set_value=lambda _element, _text: False,
        click=click,
        focus_window=lambda hwnd: focused.append(hwnd) or {"ok": True},
        type_text=lambda text, hwnd=None, **_kwargs: typed.append((text, hwnd)) or {"ok": True},
    )
    fake_verify = SimpleNamespace(
        run_with_verify=lambda _name, action, **_kwargs: action(),
    )
    monkeypatch.setattr(tools_mod, "executor", fake_executor)
    monkeypatch.setattr(tools_mod, "verify", fake_verify)
    monkeypatch.setattr(
        tools_mod.win32gui, "GetForegroundWindow",
        lambda: state["foreground"],
    )
    monkeypatch.setattr("winpilot.agent.interaction_tools.time.sleep", lambda _s: None)

    session = ToolSession(task="type into the target")
    session.hwnd = 10
    session.snapshot = Snapshot(
        hwnd=10,
        title="Target",
        region=Region(0, 0, 500, 400),
        elements=[UIElement(
            elem_id=1,
            role="combobox",
            text="File name:=draft.txt",
            cx=100,
            cy=80,
            source="uia",
        )],
    )

    result = session._t_type_text({"element_id": 1, "text": "C:\\tmp\\x.txt"})

    assert '"blocked": true' in result.text
    assert focused == []
    assert typed == []


def test_replay_anchor_lookup_never_steals_focus_in_cooperative_mode(monkeypatch):
    from winpilot.recorder import trace

    replay = trace.Replayer()
    replay.hwnd = 123
    focused = []
    monkeypatch.setattr(trace.win32.win32gui, "GetForegroundWindow", lambda: 999)
    monkeypatch.setattr(
        trace.CONFIG,
        "get",
        lambda *keys, default=None: (
            True if keys == ("controller", "multi_input_cooperation") else default
        ),
    )
    monkeypatch.setattr(
        trace.executor,
        "focus_window",
        lambda hwnd: focused.append(hwnd) or {"ok": True},
    )

    assert replay._focus_for_anchor_lookup() is False
    assert focused == []


def test_draw_strokes_rejects_empty_or_degenerate_geometry(monkeypatch):
    from winpilot.action import executor

    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: False)

    empty = executor.draw_strokes([], hwnd=None)
    degenerate = executor.draw_strokes([[[1, 2]]], hwnd=None)

    assert empty["ok"] is False and empty["invalid_geometry"] is True
    assert degenerate["ok"] is False and degenerate["invalid_geometry"] is True
    assert empty["strokes_drawn"] == 0


def test_cooperative_click_fallback_rechecks_ownership_before_mouse_down(monkeypatch):
    from winpilot.action import executor

    sent = []
    focused = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: True)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)
    monkeypatch.setattr(
        executor, "_post_click",
        lambda *_args, **_kwargs: RawBackendResult(False, {}),
    )
    monkeypatch.setattr(executor, "cooperative_input_guard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(executor, "_cooperative_action_still_owned", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(executor, "_cursor_pos", lambda: (5, 5))
    monkeypatch.setattr(executor, "_raw_move_mouse", lambda *_args: None)
    monkeypatch.setattr(executor, "_raw_focus_window", lambda hwnd: focused.append(hwnd))
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.append(inputs))
    monkeypatch.setattr(executor, "_restore_cursor_after_agent_input", lambda *_args: False)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    result = executor.click(100, 120, hwnd=123)

    assert result["ok"] is False
    assert result["blocked"] is True
    assert sent == []
    assert focused == []


def test_cooperative_scroll_fallback_rechecks_cursor_before_wheel(monkeypatch):
    from winpilot.action import executor

    sent = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: True)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)
    monkeypatch.setattr(executor, "cooperative_input_guard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(executor.win32gui, "WindowFromPoint", lambda _point: 0)
    monkeypatch.setattr(executor, "_cursor_pos", lambda: (5, 5))
    monkeypatch.setattr(executor, "_raw_move_mouse", lambda *_args: None)
    monkeypatch.setattr(executor, "_cooperative_action_still_owned", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.append(inputs))
    monkeypatch.setattr(executor, "_restore_cursor_after_agent_input", lambda *_args: False)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    result = executor.scroll(100, 120, -2, hwnd=123)

    assert result["ok"] is False
    assert result["blocked"] is True
    assert sent == []


def test_cooperative_mode_blocks_standalone_cursor_move_and_held_mouse(monkeypatch):
    from winpilot.action import executor

    sent = []
    moved = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.append(inputs))
    monkeypatch.setattr(executor, "_raw_move_mouse", lambda x, y: moved.append((x, y)))

    move = executor.move_mouse(100, 120, hwnd=123)
    down = executor.mouse_down("left", hwnd=123)

    assert move["ok"] is False and move["cooperative_input"] is True
    assert down["ok"] is False and down["cooperative_input"] is True
    assert moved == []
    assert sent == []


def test_first_mutating_actions_require_preexisting_live_target(monkeypatch):
    from winpilot.agent import scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(
        scope_guard.CONFIG,
        "get",
        lambda *keys, default=None: (
            True if keys == ("controller", "multi_input_cooperation") else default
        ),
    )
    monkeypatch.setattr(scope_guard, "_window_alive", lambda _hwnd: False)

    cases = {
        "hotkey": {"keys": ["ctrl", "s"]},
        "type_text": {"text": "hello"},
        "click": {"x": 10, "y": 20},
        "scroll": {"x": 10, "y": 20, "amount": -2},
    }
    for name, args in cases.items():
        session = ToolSession(task="edit the target window")
        session.world_enabled = False
        calls = []
        monkeypatch.setattr(
            session,
            "_find_target_window",
            lambda: (_ for _ in ()).throw(AssertionError("must not infer target")),
        )
        monkeypatch.setattr(
            session,
            f"_t_{name}",
            lambda _args, action=name: calls.append(action) or ToolResult('{"ok": true}'),
        )

        payload = json.loads(session.dispatch(name, args).text)

        assert payload["ok"] is False
        assert payload["blocked"] is True
        assert payload["cooperative_input"] is True
        assert payload["missing_live_target"] is True
        assert session.hwnd is None
        assert calls == []


def test_same_process_sibling_never_rebinds_action_target(monkeypatch):
    from winpilot.agent import interaction_tools, scope_guard
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    cooperative_get = lambda *keys, default=None: (
        True if keys == ("controller", "multi_input_cooperation") else default
    )
    monkeypatch.setattr(scope_guard.CONFIG, "get", cooperative_get)
    monkeypatch.setattr(interaction_tools.CONFIG, "get", cooperative_get)
    monkeypatch.setattr(scope_guard, "_window_alive", lambda _hwnd: True)

    cases = {
        "hotkey": ({"keys": ["ctrl", "s"]}, False),
        "type_text": ({"text": "hello"}, False),
        "click": ({"x": 10, "y": 20}, True),
        "scroll": ({"x": 10, "y": 20, "amount": -2}, True),
    }
    for name, (args, background_safe) in cases.items():
        session = ToolSession(task="edit target A")
        session.world_enabled = False
        session.hwnd = 100
        session._window_alive = lambda hwnd: int(hwnd or 0) == 100
        session._action_target_context = lambda: {
            "target_hwnd": 100,
            "foreground_hwnd": 200,
            "target_title": "Target A",
            "foreground_title": "Sibling B",
        }
        session._window_related_to_target = lambda _foreground, _target: False
        session._same_window_process = lambda foreground, target: (
            foreground, target
        ) == (200, 100)
        bound_targets = []
        monkeypatch.setattr(
            session,
            f"_t_{name}",
            lambda _args: (
                bound_targets.append(session._action_hwnd())
                or ToolResult('{"ok": true}')
            ),
        )

        payload = json.loads(session.dispatch(name, args).text)

        assert session.hwnd == 100
        if background_safe:
            assert payload["ok"] is True
            assert bound_targets == [100]
        else:
            assert payload["blocked"] is True
            assert payload["cooperative_input"] is True
            assert bound_targets == []


def test_cooperative_resolve_hwnd_cannot_launder_observed_foreground(monkeypatch):
    import winpilot.agent.tools as tools_mod
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(
        tools_mod.CONFIG,
        "get",
        lambda *keys, default=None: (
            True if keys == ("controller", "multi_input_cooperation") else default
        ),
    )

    unbound = ToolSession(task="inspect")
    unbound._window_alive = lambda _hwnd: False
    monkeypatch.setattr(
        unbound,
        "_foreground_target_window",
        lambda: (_ for _ in ()).throw(AssertionError("must not inspect foreground")),
    )
    assert unbound._resolve_hwnd({}) is None
    assert unbound._resolve_hwnd({"hwnd": 200}) is None
    assert unbound.hwnd is None

    bound = ToolSession(task="inspect target A")
    bound.hwnd = 100
    bound._window_alive = lambda hwnd: int(hwnd or 0) in {100, 200}
    monkeypatch.setattr(
        bound,
        "_foreground_target_window",
        lambda: (_ for _ in ()).throw(AssertionError("must not inspect foreground")),
    )
    assert bound._resolve_hwnd({}) == 100
    assert bound._resolve_hwnd({"hwnd": 200}) is None
    assert bound.hwnd == 100


def test_cooperative_subgoal_never_adopts_foreground_without_target(monkeypatch):
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.controller import FastGuiController

    monkeypatch.setattr(
        subgoal_mod.CONFIG,
        "get",
        lambda *keys, default=None: (
            True if keys == ("controller", "multi_input_cooperation") else default
        ),
    )
    monkeypatch.setattr(
        subgoal_mod.win32gui,
        "GetForegroundWindow",
        lambda: (_ for _ in ()).throw(AssertionError("must not adopt foreground")),
    )

    class Session:
        hwnd = None

    session = Session()
    executor = subgoal_mod.SubgoalExecutor(session, FastGuiController(session))
    request = subgoal_mod.parse_subgoal_request({"subgoal": "click the visible button"})

    assert executor._target_hwnd(request, allow_foreground=True) is None
    assert session.hwnd is None


def test_subgoal_focus_block_is_not_treated_as_success(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.controller import FastGuiController

    monkeypatch.setattr(
        subgoal_mod.CONFIG,
        "get",
        lambda *keys, default=None: default,
    )
    monkeypatch.setattr(subgoal_mod.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(subgoal_mod.win32gui, "IsWindowVisible", lambda _hwnd: True)

    class Session:
        hwnd = 100
        snapshot = None
        task = "click target A"

        @staticmethod
        def dispatch(name, _args):
            assert name == "focus_window"
            return ToolResult(
                '{"ok": false, "blocked": true, "cooperative_input": true}'
            )

    session = Session()
    executor = subgoal_mod.SubgoalExecutor(session, FastGuiController(session))
    request = subgoal_mod.parse_subgoal_request({"subgoal": "click target A"})

    snapshot, prelude, _skip = executor._ensure_observed(request)

    assert snapshot is None
    assert prelude[0]["action"] == "focus_window"
    assert prelude[0]["ok"] is False
    assert prelude[0]["blocked"] is True


def test_replay_type_preclick_block_stops_before_typing(monkeypatch):
    from winpilot.recorder import trace

    replay = trace.Replayer()
    replay.hwnd = 100
    replay._relocate = lambda _step, _anchor_key="": (20, 30)
    typed = []
    monkeypatch.setattr(
        trace.executor,
        "click",
        lambda *_args, **_kwargs: {
            "ok": False,
            "blocked": True,
            "cooperative_input": True,
        },
    )
    monkeypatch.setattr(
        trace.executor,
        "type_text",
        lambda *_args, **_kwargs: typed.append(True) or {"ok": True},
    )
    monkeypatch.setattr(
        trace.verify,
        "run_with_verify",
        lambda _name, action, **_kwargs: action(),
    )

    result = replay._run_step(0, {
        "tool": "type_text",
        "coords": [20, 30],
        "text_input": "hello",
    })

    assert result["ok"] is False
    assert result["blocked"] is True
    assert typed == []


def test_replay_visual_servo_propagates_blocked_cursor_move(monkeypatch):
    from winpilot.recorder import trace

    replay = trace.Replayer()
    replay.hwnd = None
    monkeypatch.setattr(
        trace.executor,
        "move_mouse",
        lambda *_args, **_kwargs: {
            "ok": False,
            "blocked": True,
            "cooperative_input": True,
        },
    )

    point, servo = replay._visual_servo_replay_target(
        {"anchor": {"text": "Save"}},
        (100, 100),
    )

    assert point is None
    assert servo["confirmed"] is False
    assert servo["blocked"] is True
    assert servo["lost_reason"] == "cursor_move_blocked"


def test_cursor_restore_rechecks_foreground_ownership(monkeypatch):
    from winpilot.action import executor

    restored = []
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)
    monkeypatch.setattr(executor, "_cooperative_action_still_owned", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(executor, "_restore_cursor", restored.append)

    result = executor._restore_cursor_after_agent_input(
        (5, 6), (100, 120), hwnd=123,
    )

    assert result is False
    assert restored == []


def test_raw_click_reports_interruption_between_down_and_up(monkeypatch):
    from winpilot.action import executor

    ownership = iter((True, True, False))
    sent = []
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)
    monkeypatch.setattr(executor, "_cooperative_action_still_owned", lambda *_args, **_kwargs: next(ownership))
    monkeypatch.setattr(executor, "_raw_move_mouse", lambda *_args: None)
    monkeypatch.setattr(executor, "_sample_pressed_state", lambda: False)
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.append(inputs))
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    completed = executor._raw_click(
        10, 20, MouseButton.LEFT, ClickCount.SINGLE, 123,
    )

    assert completed.input_completed is True
    assert completed.facts["blocked"] is True
    assert len(sent) == 2  # DOWN and mandatory cleanup UP


def test_stroke_rechecks_ownership_after_final_move(monkeypatch):
    from winpilot.action import executor

    ownership = iter((True, True, True, False))
    sent = []
    # This case isolates cooperative cursor ownership.  Long gestures now
    # independently fail closed when their bound target window loses focus;
    # that guard has dedicated coverage in test_desktop_action_queue.py.
    monkeypatch.setattr(executor, "_target_window_still_foreground", lambda *_args: True)
    monkeypatch.setattr(executor, "_cooperative_action_still_owned", lambda *_args, **_kwargs: next(ownership))
    monkeypatch.setattr(executor, "_abs_mouse_move", lambda *_args: None)
    monkeypatch.setattr(executor, "_emit_move", lambda *_args: None)
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.append(inputs))
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    completed = executor._stroke_path([(10, 10), (11, 10)], 0, hwnd=123)

    assert completed is False
    assert len(sent) == 2  # DOWN and mandatory cleanup UP


def test_hotkey_chord_releases_keys_and_blocks_when_foreground_changes(monkeypatch):
    from winpilot.action import executor

    sent = []
    ownership = iter((True, False))
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)
    monkeypatch.setattr(executor, "cooperative_input_guard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        executor,
        "_cooperative_action_still_owned",
        lambda *_args, **_kwargs: next(ownership),
    )
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.append(inputs))
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    result = executor.press_keys(["ctrl", "s"], hwnd=123)

    assert result["ok"] is False
    assert result["blocked"] is True
    assert len(sent) == 2  # chord DOWN and mandatory reversed UP


def test_ctrl_n_does_not_adopt_same_process_sibling_in_cooperative_mode(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import controller as controller_module
    from winpilot.fast.controller import FastGuiController

    class Session:
        hwnd = 10
        snapshot = None

        @staticmethod
        def _window_related_to_target(_hwnd, _target):
            return False

        def _remember_target(self, hwnd, _title):
            self.hwnd = hwnd

        @staticmethod
        def dispatch(name, _args):
            assert name == "hotkey"
            return ToolResult(json.dumps({
                "ok": True,
                "post_state": {"foreground_hwnd": 20, "foreground_title": "Workbook2"},
            }))

    monkeypatch.setattr(
        controller_module.CONFIG,
        "get",
        lambda *keys, default=None: (
            True if keys == ("controller", "multi_input_cooperation") else default
        ),
    )
    monkeypatch.setattr(controller_module.win32gui, "GetWindow", lambda *_args: 0)
    monkeypatch.setattr(
        controller_module.win32process,
        "GetWindowThreadProcessId",
        lambda hwnd: (1, 500 if hwnd in {10, 20} else 900),
    )
    session = Session()

    result = FastGuiController(session).execute([
        {"action": "hotkey", "keys": ["ctrl", "n"]},
    ], sync=False)

    assert session.hwnd == 10
    assert "controller_adopted_foreground_hwnd" not in result["results"][0]["data"]


def test_scan_windows_does_not_show_hidden_candidates_in_cooperative_mode(monkeypatch):
    from winpilot.agent import window_tools
    from winpilot.agent.tools import ToolSession

    shown = []
    focused = []
    monkeypatch.setattr(
        window_tools.CONFIG,
        "get",
        lambda *keys, default=None: (
            True if keys == ("controller", "multi_input_cooperation") else default
        ),
    )
    monkeypatch.setattr(window_tools.win32, "list_windows", lambda: [])
    monkeypatch.setattr(
        window_tools.win32,
        "find_app",
        lambda _query: {"windows": [{
            "hwnd": 123,
            "title": "Hidden App",
            "class_name": "AppWindow",
            "rect": [0, 0, 100, 100],
        }]},
    )
    monkeypatch.setattr(window_tools.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(window_tools.win32gui, "IsWindowVisible", lambda _hwnd: False)
    monkeypatch.setattr(window_tools.win32gui, "GetWindowText", lambda _hwnd: "Hidden App")
    monkeypatch.setattr(window_tools.win32gui, "GetClassName", lambda _hwnd: "AppWindow")
    monkeypatch.setattr(window_tools.win32gui, "GetWindowRect", lambda _hwnd: (0, 0, 100, 100))
    monkeypatch.setattr(window_tools.win32gui, "GetForegroundWindow", lambda: 999)
    monkeypatch.setattr(window_tools.win32gui, "ShowWindow", lambda hwnd, _cmd: shown.append(hwnd))
    monkeypatch.setattr(
        window_tools.executor,
        "focus_window",
        lambda hwnd: focused.append(hwnd) or {"ok": True},
    )

    result = ToolSession(task="inspect app")._t_scan_windows({"query": "app"})

    assert "skipped=hidden_candidate_in_cooperative_mode" in result.text
    assert shown == []
    assert focused == []


def test_target_change_clears_snapshot_and_last_input_anchor(monkeypatch):
    from types import SimpleNamespace

    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="switch target")
    session.hwnd = 100
    session.snapshot = SimpleNamespace(hwnd=100)
    session._last_input_element = object()
    session._observe_cache = {"stale": True}
    session._window_alive = lambda hwnd: int(hwnd or 0) in {100, 200}
    monkeypatch.setattr(session, "_bind_perception_target", lambda _hwnd: None)

    session._remember_target(200)

    assert session.hwnd == 200
    assert session.snapshot is None
    assert session._last_input_element is None
    assert session._observe_cache is None


def test_remember_target_preserves_fresh_snapshot_for_new_hwnd(monkeypatch):
    from types import SimpleNamespace

    from winpilot.agent.tools import ToolSession

    fresh = SimpleNamespace(hwnd=200)
    session = ToolSession(task="observe new target")
    session.hwnd = 100
    session.snapshot = fresh
    session._observe_cache = {"snapshot": fresh}
    session._last_input_element = object()
    session._window_alive = lambda hwnd: int(hwnd or 0) in {100, 200}
    monkeypatch.setattr(session, "_bind_perception_target", lambda _hwnd: None)

    session._remember_target(200)

    assert session.snapshot is fresh
    assert session._observe_cache == {"snapshot": fresh}
    assert session._last_input_element is None


def test_resolve_element_rejects_snapshot_from_previous_target():
    from winpilot.agent.tools import ToolSession
    from winpilot.perception.fusion import Snapshot
    from winpilot.perception.model import UIElement
    from winpilot.utils.screenshot import Region

    session = ToolSession(task="use live target")
    session.hwnd = 200
    session._window_alive = lambda hwnd: int(hwnd or 0) == 200
    session.snapshot = Snapshot(
        hwnd=100,
        title="Old target",
        region=Region(0, 0, 100, 100),
        elements=[UIElement(elem_id=1, text="Old", role="button", cx=10, cy=10)],
    )

    element, error = session._resolve_element(1)

    assert element is None
    assert "stale snapshot" in error
    assert "live target hwnd=200" in error


def test_coordinate_click_test_cannot_override_cooperative_target(monkeypatch):
    from winpilot.agent import desktop_tools
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(
        desktop_tools.CONFIG,
        "get",
        lambda *keys, default=None: (
            True if keys == ("controller", "multi_input_cooperation") else default
        ),
    )
    session = ToolSession(task="click target A")
    session.hwnd = 100

    payload = json.loads(session._t_coordinate_click_test({
        "x": 10,
        "y": 20,
        "hwnd": 200,
    }).text)

    assert payload["ok"] is False
    assert payload["blocked"] is True
    assert payload["cooperative_input"] is True
    assert payload["target_hwnd"] == 100


def test_cooperative_subgoal_skips_focus_and_scopes_observe(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.fast import subgoal as subgoal_mod
    from winpilot.fast.controller import FastGuiController
    from winpilot.perception.fusion import Snapshot
    from winpilot.utils.screenshot import Region

    monkeypatch.setattr(
        subgoal_mod.CONFIG,
        "get",
        lambda *keys, default=None: (
            True if keys == ("controller", "multi_input_cooperation") else default
        ),
    )
    monkeypatch.setattr(subgoal_mod.win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(subgoal_mod.win32gui, "IsWindowVisible", lambda _hwnd: True)
    calls = []

    class Session:
        hwnd = 100
        snapshot = None
        task = "click target A"

        def dispatch(self, name, args):
            calls.append((name, dict(args)))
            assert name == "observe"
            self.snapshot = Snapshot(
                hwnd=100,
                title="Target A",
                region=Region(0, 0, 100, 100),
                elements=[],
            )
            return ToolResult('{"ok": true}', is_observe=True)

    session = Session()
    request = subgoal_mod.parse_subgoal_request({
        "subgoal": "click target A",
        "click": {"label": "Save"},
    })

    snapshot, prelude, _skip = subgoal_mod.SubgoalExecutor(
        session, FastGuiController(session),
    )._ensure_observed(request)

    assert snapshot is session.snapshot
    assert calls == [("observe", {"full": True, "force_full": True, "hwnd": 100})]
    assert prelude[0]["action"] == "preserve_cooperative_foreground"


def test_newline_chord_blocks_and_releases_when_foreground_changes(monkeypatch):
    from winpilot.action import executor

    ownership = iter((True, False))
    sent = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)
    monkeypatch.setattr(executor, "cooperative_input_guard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(executor.win32gui, "GetForegroundWindow", lambda: 123)
    monkeypatch.setattr(executor, "_should_type_via_vk", lambda _text: False)
    monkeypatch.setattr(
        executor,
        "_cooperative_action_still_owned",
        lambda *_args, **_kwargs: next(ownership),
    )
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.append(inputs))
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    result = executor.type_text("\n", hwnd=123, interval_s=0)

    assert result["ok"] is False
    assert result["blocked"] is True
    assert result["typed"] == 0
    assert len(sent) == 2


def test_clipboard_paste_chord_blocks_and_releases_on_ownership_loss(monkeypatch):
    from winpilot.action import executor

    ownership = iter((True, False))
    sent = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)
    monkeypatch.setattr(executor, "cooperative_input_guard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        executor,
        "_cooperative_action_still_owned",
        lambda *_args, **_kwargs: next(ownership),
    )
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.append(inputs))
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    result = executor.clipboard_paste(hwnd=123)

    assert result["ok"] is False
    assert result["blocked"] is True
    assert len(sent) == 2


def test_physical_scroll_rechecks_ownership_after_wheel(monkeypatch):
    from winpilot.action import executor

    ownership = iter((True, False))
    sent = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: True)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)
    monkeypatch.setattr(executor, "cooperative_input_guard", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(executor.win32gui, "WindowFromPoint", lambda _point: 0)
    monkeypatch.setattr(executor, "_cursor_pos", lambda: (5, 5))
    monkeypatch.setattr(executor, "_raw_move_mouse", lambda *_args: None)
    monkeypatch.setattr(
        executor,
        "_cooperative_action_still_owned",
        lambda *_args, **_kwargs: next(ownership),
    )
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.append(inputs))
    monkeypatch.setattr(executor, "_restore_cursor_after_agent_input", lambda *_args: False)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    result = executor.scroll(10, 20, -1, hwnd=123)

    assert len(sent) == 1
    assert result["ok"] is False
    assert result["blocked"] is True


def test_system_launch_does_not_bind_unproven_foreground_in_cooperative_mode(monkeypatch):
    from types import SimpleNamespace

    import winpilot.agent.tools as tools_mod
    from winpilot.agent import system_tools
    from winpilot.agent.tools import ToolSession

    monkeypatch.setattr(
        system_tools.CONFIG,
        "get",
        lambda *keys, default=None: (
            True if keys == ("controller", "multi_input_cooperation") else default
        ),
    )
    monkeypatch.setattr(
        tools_mod,
        "verify",
        SimpleNamespace(run_with_verify=lambda *_args, **_kwargs: {
            "ok": True,
            "verified": True,
            "post_state": {"foreground_hwnd": 200, "foreground_title": "User B"},
        }),
    )
    monkeypatch.setattr(
        tools_mod,
        "executor",
        SimpleNamespace(
            launch=lambda _command: {"ok": True},
            focus_window=lambda _hwnd: {"ok": True},
        ),
    )
    session = ToolSession(task="launch example")
    session.hwnd = 100
    session._window_alive = lambda hwnd: int(hwnd or 0) in {100, 200}
    monkeypatch.setattr(session, "_resolve_launch_target", lambda _command: None)

    payload = json.loads(session._t_launch({"command": "example"}).text)

    assert session.hwnd == 100
    assert payload.get("target_hwnd") != 200
    assert all(item != ("app", 200) for item in session._undo_stack)


def test_replay_launch_does_not_bind_unproven_foreground_in_cooperative_mode(monkeypatch):
    from winpilot.recorder import trace

    monkeypatch.setattr(
        trace.CONFIG,
        "get",
        lambda *keys, default=None: (
            True if keys == ("controller", "multi_input_cooperation") else default
        ),
    )
    monkeypatch.setattr(
        trace.verify,
        "run_with_verify",
        lambda *_args, **_kwargs: {
            "ok": True,
            "verified": False,
            "post_state": {"foreground_hwnd": 200, "foreground_title": "User B"},
        },
    )
    replay = trace.Replayer()
    replay.hwnd = 100

    result = replay._run_step(0, {"tool": "launch", "command": "example"})

    assert replay.hwnd == 100
    assert result["ok"] is False
    assert result["blocked"] is True
    assert result["needs_target_window"] is True


def test_cooperative_mouse_up_only_releases_agent_owned_button(monkeypatch):
    from winpilot.action import executor

    sent = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: True)
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.append(inputs))
    with executor._held_mouse_lock:
        executor._held_mouse_buttons.clear()

    blocked = executor.mouse_up("left")

    assert blocked["ok"] is False
    assert blocked["blocked"] is True
    assert blocked["cooperative_input"] is True
    assert sent == []

    with executor._held_mouse_lock:
        executor._held_mouse_buttons.add("left")
    released = executor.mouse_up("left")

    assert released["ok"] is True
    assert len(sent) == 1
    with executor._held_mouse_lock:
        assert "left" not in executor._held_mouse_buttons
