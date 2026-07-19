import json

from winpilot.agent import scope_guard


class _Session:
    def __init__(self):
        self.hwnd = 100
        self.fg = 200
        self.focus_calls = []
        self._screen_epoch = 0

    def _action_target_context(self):
        return {
            "target_hwnd": self.hwnd,
            "foreground_hwnd": self.fg,
            "target_title": "Target",
            "foreground_title": "Other",
            "foreground_matches_target": self.fg == self.hwnd,
        }

    def _window_related_to_target(self, fg, target):
        return bool(fg and target and int(fg) == int(target))

    def _find_target_window(self):
        return self.hwnd

    def _remember_target(self, hwnd=None, hint=""):
        if hwnd:
            self.hwnd = int(hwnd)


def test_scope_guard_blocks_when_exact_target_refocus_fails(monkeypatch):
    session = _Session()
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))
    monkeypatch.setattr(
        scope_guard.executor,
        "focus_window",
        lambda hwnd: session.focus_calls.append(hwnd) or {"ok": False},
    )

    result = scope_guard.ensure_scope(session, "click", {"x": 1, "y": 2})

    assert result is not None
    payload = json.loads(result.text)
    assert payload["blocked"] is True
    assert payload["scope_guard"]["recovery_attempted"] is True
    assert payload["action_executed"] is False
    assert session.focus_calls == [100]
    assert scope_guard.take_scope_recovery(session) is None


def test_scope_guard_uses_real_hit_window_when_auxiliary_window_owns_foreground(monkeypatch):
    session = _Session()
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))
    monkeypatch.setattr(scope_guard.win32gui, "WindowFromPoint", lambda point: 100)
    monkeypatch.setattr(
        scope_guard.executor,
        "focus_window",
        lambda hwnd: (_ for _ in ()).throw(AssertionError("hit-tested target needs no repair")),
    )

    assert scope_guard.ensure_scope(session, "click", {"x": 20, "y": 30}) is None


def test_scope_guard_refocuses_keyboard_target_and_continues(monkeypatch):
    session = _Session()
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))

    def focus(hwnd):
        session.fg = hwnd
        return {"ok": True}

    monkeypatch.setattr(scope_guard.executor, "focus_window", focus)

    assert scope_guard.ensure_scope(session, "type_text", {"text": "hello"}) is None


def test_scope_guard_refocuses_but_withholds_stale_spatial_coordinates(monkeypatch):
    session = _Session()
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))
    monkeypatch.setattr(scope_guard.win32gui, "WindowFromPoint", lambda point: 200)

    def focus(hwnd):
        session.fg = hwnd
        return {"ok": True, "route": "test"}

    monkeypatch.setattr(scope_guard.executor, "focus_window", focus)

    result = scope_guard.ensure_scope(session, "click", {"x": 20, "y": 30})
    payload = json.loads(result.text)

    assert payload["target_refocused"] is True
    assert payload["action_executed"] is False
    assert payload["recovery_action_executed"] is True
    assert session._screen_epoch == 1


def test_scope_guard_records_same_process_window_replacement_as_stale_frame(monkeypatch):
    session = _Session()
    session._same_window_process = lambda foreground, target: (foreground, target) == (200, 100)
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))
    monkeypatch.setattr(scope_guard.win32gui, "WindowFromPoint", lambda point: 200)
    result = scope_guard.ensure_scope(
        session, "scroll", {"x": 10, "y": 10, "amount": -3},
    )

    payload = json.loads(result.text)
    assert payload["blocked"] is True
    assert payload["target_rebound"] is True
    assert payload["action_executed"] is False
    assert session.hwnd == 200


def test_scope_guard_does_not_encode_application_specific_task_order():
    session = _Session()
    session.task = "calculate a quotient, save a screenshot, then open an unknown chat app"
    session.visible_evidence_ledger = []
    session.saved_screenshot_paths = []

    assert scope_guard.ensure_scope(session, "launch", {"command": "unknown-chat-app"}) is None


def test_scope_guard_does_not_use_product_title_blacklists(monkeypatch):
    session = _Session()
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))
    session.task = "inventory installed applications and write a report"
    session._action_target_context = lambda: {
        "target_hwnd": 100,
        "foreground_hwnd": 100,
        "target_title": "Lingdong Script - Agent",
        "foreground_title": "Lingdong Script - Agent",
        "foreground_matches_target": True,
    }

    assert scope_guard.ensure_scope(
        session, "type_text", {"text": "literal input"},
    ) is None


def test_scope_guard_does_not_mistake_winpilot_named_browser_target_for_control_plane(monkeypatch):
    session = _Session()
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))
    session.task = (
        "Open http://127.0.0.1/gui_agent_target.html and click the WinPilot target"
    )
    session.fg = session.hwnd
    session._action_target_context = lambda: {
        "target_hwnd": 100,
        "foreground_hwnd": 100,
        "target_title": "WinPilot GUI Agent Target - Google Chrome",
        "foreground_title": "WinPilot GUI Agent Target - Google Chrome",
        "foreground_matches_target": True,
    }

    assert scope_guard.ensure_scope(session, "click", {"x": 1, "y": 2}) is None


def test_window_title_alone_never_changes_scope_ownership(monkeypatch):
    session = _Session()
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))
    session.task = "save a note as WinPilotE2E_note.txt"
    session.fg = session.hwnd
    session._action_target_context = lambda: {
        "target_hwnd": 100,
        "foreground_hwnd": 100,
        "target_title": "WinPilot",
        "foreground_title": "WinPilot",
        "foreground_matches_target": True,
    }

    assert scope_guard.ensure_scope(
        session, "type_text", {"text": "literal input"},
    ) is None


def test_scope_guard_allows_explicit_control_plane_test(monkeypatch):
    session = _Session()
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))
    session.task = "test the WinPilot control-plane input"
    session.fg = session.hwnd
    session._action_target_context = lambda: {
        "target_hwnd": 100,
        "foreground_hwnd": 100,
        "target_title": "WinPilot",
        "foreground_title": "WinPilot",
        "foreground_matches_target": True,
    }

    assert scope_guard.ensure_scope(session, "click", {"x": 1, "y": 2}) is None


def test_scope_guard_fails_closed_for_spatial_mutation_without_target(monkeypatch):
    session = _Session()
    session.hwnd = None
    session.fg = 55
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))

    result = scope_guard.ensure_scope(session, "drag", {
        "x1": 10, "y1": 20, "x2": 30, "y2": 40,
    })

    payload = json.loads(result.text)
    assert payload["blocked"] is True
    assert payload["missing_live_target"] is True
    assert session.hwnd is None


def test_scope_guard_rebinds_a_previously_proven_app_after_hwnd_replacement(monkeypatch):
    session = _Session()
    session.hwnd = 100
    session.fg = 300
    rebound_calls = []

    def recover_bound_target():
        rebound_calls.append(100)
        session.hwnd = 300
        return 300

    session._recover_bound_target_window = recover_bound_target
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: hwnd == 300)

    assert scope_guard.ensure_scope(session, "click", {"x": 1, "y": 2}) is None
    assert rebound_calls == [100]
    assert session.hwnd == 300


def test_scope_guard_batch_must_bind_target_before_first_spatial_action(monkeypatch):
    session = _Session()
    session.hwnd = None
    session.fg = 55
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))
    unsafe = scope_guard.ensure_scope(session, "run_action_batch", {
        "actions": [{"action": "click", "x": 10, "y": 20}],
    })
    safe = scope_guard.ensure_scope(session, "run_action_batch", {
        "actions": [
            {"action": "focus_window", "hwnd": 77},
            {"action": "click", "x": 10, "y": 20},
        ],
    })

    assert json.loads(unsafe.text)["missing_live_target"] is True
    assert safe is None


def test_scope_guard_requires_target_for_execute_subgoal_drag(monkeypatch):
    session = _Session()
    session.hwnd = None
    session.fg = 55
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))

    result = scope_guard.ensure_scope(session, "execute_subgoal", {
        "drag": {"x1": 10, "y1": 20, "x2": 30, "y2": 40},
    })

    assert json.loads(result.text)["missing_live_target"] is True


def test_tool_session_delegates_composite_scope_to_child_controller(monkeypatch):
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="operate the current visual target")
    calls = []
    session._t_run_action_batch = lambda args: calls.append(
        ("run_action_batch", args)
    ) or ToolResult('{"ok": true, "verified": false}')
    session._t_execute_subgoal = lambda args: calls.append(
        ("execute_subgoal", args)
    ) or ToolResult('{"ok": true, "verified": false}')
    monkeypatch.setattr(
        scope_guard,
        "ensure_scope",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("composite dispatch must not perform an outer scope check")
        ),
    )
    monkeypatch.setattr(
        scope_guard,
        "take_scope_recovery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("composite dispatch must not consume child scope recovery")
        ),
    )

    batch_args = {"actions": [{"action": "click", "x": 10, "y": 20}]}
    subgoal_args = {"subgoal": "interact with the visible target"}
    assert json.loads(session.dispatch("run_action_batch", batch_args).text)["ok"] is True
    assert json.loads(session.dispatch("execute_subgoal", subgoal_args).text)["ok"] is True
    assert calls == [
        ("run_action_batch", batch_args),
        ("execute_subgoal", subgoal_args),
    ]
