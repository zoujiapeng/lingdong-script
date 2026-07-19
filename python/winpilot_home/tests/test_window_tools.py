import json


class _WindowSession:
    def __init__(self):
        from winpilot.agent.window_tools import WindowToolMixin

        self._handler = WindowToolMixin._t_focus_window.__get__(self, type(self))
        self.target = None

    @staticmethod
    def _fmt(value):
        return json.dumps(value, ensure_ascii=False)

    def _remember_target(self, hwnd, title):
        self.target = (hwnd, title)

    def focus(self, args):
        return self._handler(args)


def test_focus_window_resolves_visible_title(monkeypatch):
    from winpilot.agent import window_tools

    monkeypatch.setattr(window_tools.win32, "list_windows", lambda: [
        {"hwnd": 42, "title": "WinPilot GUI Agent Target - Chrome", "class_name": "Chrome_WidgetWin_1"},
    ])
    monkeypatch.setattr(window_tools.win32, "_window_text", lambda hwnd: "WinPilot GUI Agent Target - Chrome")
    monkeypatch.setattr(window_tools.win32gui, "IsWindow", lambda hwnd: hwnd == 42)
    monkeypatch.setattr(window_tools.executor, "focus_window", lambda hwnd: {"ok": True})
    session = _WindowSession()

    result = json.loads(session.focus({"title": "WinPilot GUI Agent Target"}).text)

    assert result == {
        "ok": True,
        "hwnd": 42,
        "title": "WinPilot GUI Agent Target - Chrome",
        "resolved_by": "title_contains",
    }
    assert session.target == (42, "WinPilot GUI Agent Target - Chrome")


def test_focus_window_returns_structured_error_for_missing_target(monkeypatch):
    from winpilot.agent import window_tools

    monkeypatch.setattr(window_tools.win32, "list_windows", lambda: [])
    session = _WindowSession()

    result = json.loads(session.focus({"title": "Missing app"}).text)

    assert result["ok"] is False
    assert result["query"] == "Missing app"


def test_focus_window_does_not_scan_or_activate_background_browser_tabs(monkeypatch):
    from winpilot.agent import window_tools

    focus_calls = []
    monkeypatch.setattr(window_tools.win32, "list_windows", lambda: [
        {
            "hwnd": 42,
            "title": "WinPilot - Chrome",
            "class_name": "Chrome_WidgetWin_1",
        },
    ])
    monkeypatch.setattr(window_tools.win32gui, "IsWindow", lambda hwnd: hwnd == 42)
    monkeypatch.setattr(
        window_tools.executor,
        "focus_window",
        lambda hwnd: focus_calls.append(hwnd) or {"ok": True},
    )
    monkeypatch.setattr(
        window_tools.executor,
        "click",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("focus_window must not click a guessed browser tab")
        ),
    )
    session = _WindowSession()

    result = json.loads(session.focus({"title": "WinPilot GUI Agent Target"}).text)

    assert result["ok"] is False
    assert result["query"] == "WinPilot GUI Agent Target"
    assert focus_calls == []
    assert session.target is None


def test_focus_window_can_bind_explicit_browser_hwnd_without_tab_inference(monkeypatch):
    from winpilot.agent import window_tools

    focus_calls = []
    monkeypatch.setattr(
        window_tools.win32,
        "list_windows",
        lambda: (_ for _ in ()).throw(AssertionError("explicit hwnd needs no scan")),
    )
    monkeypatch.setattr(window_tools.win32gui, "IsWindow", lambda hwnd: hwnd == 7)
    monkeypatch.setattr(
        window_tools.executor,
        "focus_window",
        lambda hwnd: focus_calls.append(hwnd) or {"ok": True},
    )
    monkeypatch.setattr(window_tools.win32, "_window_text", lambda hwnd: "App - Edge")
    session = _WindowSession()

    result = json.loads(session.focus({"hwnd": 7}).text)

    assert result == {
        "ok": True,
        "hwnd": 7,
        "title": "App - Edge",
        "resolved_by": "hwnd",
    }
    assert focus_calls == [7]
    assert session.target == (7, "App - Edge")
