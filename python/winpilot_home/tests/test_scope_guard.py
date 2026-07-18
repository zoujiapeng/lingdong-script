import json

from winpilot.agent import scope_guard


class _Session:
    def __init__(self):
        self.hwnd = 100
        self.fg = 200
        self.focus_calls = []

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


def test_scope_guard_recovers_by_refocusing_target(monkeypatch):
    session = _Session()
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))

    def fake_focus(hwnd):
        session.focus_calls.append(hwnd)
        session.fg = hwnd
        return True

    monkeypatch.setattr(scope_guard, "_focus", fake_focus)
    monkeypatch.setattr(scope_guard.time, "sleep", lambda _seconds: None)

    assert scope_guard.ensure_scope(session, "click", {"x": 1, "y": 2}) is None
    assert session.focus_calls == [100]
    recovery = scope_guard.take_scope_recovery(session)
    assert recovery["attempts"] == 1
    assert recovery["authoritative_frame_required"] is True


def test_scope_guard_retries_transient_focus_reclaim_and_proves_stability(monkeypatch):
    session = _Session()
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))
    monkeypatch.setattr(scope_guard.time, "sleep", lambda _seconds: None)

    def fake_focus(hwnd):
        session.focus_calls.append(hwnd)
        if len(session.focus_calls) >= 2:
            session.fg = hwnd
        return True

    monkeypatch.setattr(scope_guard, "_focus", fake_focus)

    assert scope_guard.ensure_scope(session, "draw_strokes", {}) is None
    recovery = scope_guard.take_scope_recovery(session)
    assert session.focus_calls == [100, 100]
    assert recovery["attempts"] == 2
    assert recovery["after"]["foreground_hwnd"] == 100


def test_scope_guard_blocks_when_refocus_cannot_recover(monkeypatch):
    session = _Session()
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))
    monkeypatch.setattr(scope_guard, "_focus", lambda hwnd: False)
    monkeypatch.setattr(scope_guard.time, "sleep", lambda _seconds: None)

    result = scope_guard.ensure_scope(session, "type_text", {"text": "hello"})

    assert result is not None
    payload = json.loads(result.text)
    assert payload["blocked"] is True
    assert payload["scope_guard"]["recovery_attempted"] is True
    assert payload["scope_guard"]["recovery_attempts"] == 3
    assert payload["tool"] == "type_text"


def test_scope_guard_adopts_same_process_foreground_window(monkeypatch):
    session = _Session()
    session._same_window_process = lambda foreground, target: (foreground, target) == (200, 100)
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))
    monkeypatch.setattr(
        scope_guard,
        "_focus",
        lambda _hwnd: (_ for _ in ()).throw(AssertionError("must adopt before refocus")),
    )

    assert scope_guard.ensure_scope(session, "scroll", {"x": 10, "y": 10, "amount": -3}) is None
    assert session.hwnd == 200


def test_scope_guard_does_not_encode_application_specific_task_order():
    session = _Session()
    session.task = "calculate a quotient, save a screenshot, then open QQ"
    session.visible_evidence_ledger = []
    session.saved_screenshot_paths = []

    assert scope_guard.ensure_scope(session, "launch", {"command": "QQ"}) is None


def test_scope_guard_blocks_mutating_own_control_plane(monkeypatch):
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

    result = scope_guard.ensure_scope(session, "type_text", {"text": "unsafe"})

    assert result is not None
    payload = json.loads(result.text)
    assert payload["blocked"] is True
    assert payload["scope_guard"]["self_control_plane"] is True


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


def test_winpilot_payload_name_does_not_disable_control_plane_guard(monkeypatch):
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

    result = scope_guard.ensure_scope(session, "type_text", {"text": "unsafe"})

    assert result is not None
    assert json.loads(result.text)["scope_guard"]["self_control_plane"] is True


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
