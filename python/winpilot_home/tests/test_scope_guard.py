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

    assert scope_guard.ensure_scope(session, "click", {"x": 1, "y": 2}) is None
    assert session.focus_calls == [100]


def test_scope_guard_blocks_when_refocus_cannot_recover(monkeypatch):
    session = _Session()
    monkeypatch.setattr(scope_guard, "_window_alive", lambda hwnd: bool(hwnd))
    monkeypatch.setattr(scope_guard, "_focus", lambda hwnd: False)

    result = scope_guard.ensure_scope(session, "type_text", {"text": "hello"})

    assert result is not None
    payload = json.loads(result.text)
    assert payload["blocked"] is True
    assert payload["scope_guard"]["recovery_attempted"] is True
    assert payload["tool"] == "type_text"
