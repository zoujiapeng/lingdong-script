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


def test_focus_window_activates_matching_background_browser_tab(monkeypatch):
    from winpilot.agent import window_tools
    from winpilot.perception.model import UIElement

    focus_calls = []
    monkeypatch.setattr(window_tools.win32, "list_windows", lambda: [
        {
            "hwnd": 42,
            "title": "WinPilot - Chrome",
            "class_name": "Chrome_WidgetWin_1",
        },
    ])
    monkeypatch.setattr(window_tools.win32gui, "IsWindow", lambda hwnd: hwnd == 42)
    monkeypatch.setattr(window_tools.uia, "collect", lambda hwnd: [
        UIElement(
            role="tab",
            text="WinPilot GUI Agent Target",
            cx=320,
            cy=18,
        ),
    ])
    monkeypatch.setattr(window_tools.executor, "uia_invoke", lambda element: True)
    monkeypatch.setattr(
        window_tools.executor,
        "focus_window",
        lambda hwnd: focus_calls.append(hwnd) or {"ok": True},
    )
    monkeypatch.setattr(
        window_tools.win32,
        "_window_text",
        lambda hwnd: "WinPilot GUI Agent Target - Chrome",
    )
    session = _WindowSession()

    result = json.loads(session.focus({"title": "WinPilot GUI Agent Target"}).text)

    assert result["ok"] is True
    assert result["hwnd"] == 42
    assert result["resolved_by"] == "browser_tab"
    assert focus_calls == [42, 42]
    assert session.target == (42, "WinPilot GUI Agent Target - Chrome")


def test_focus_window_clicks_browser_tab_when_uia_invoke_is_unavailable(monkeypatch):
    from winpilot.agent import window_tools
    from winpilot.perception.model import UIElement

    click_calls = []
    monkeypatch.setattr(window_tools.win32, "list_windows", lambda: [
        {"hwnd": 7, "title": "App - Edge", "class_name": "Chrome_WidgetWin_1"},
    ])
    monkeypatch.setattr(window_tools.win32gui, "IsWindow", lambda hwnd: hwnd == 7)
    monkeypatch.setattr(window_tools.uia, "collect", lambda hwnd: [
        UIElement(role="tab", text="Target board", cx=260, cy=18),
    ])
    monkeypatch.setattr(window_tools.executor, "uia_invoke", lambda element: False)
    monkeypatch.setattr(window_tools.executor, "focus_window", lambda hwnd: {"ok": True})
    monkeypatch.setattr(
        window_tools.executor,
        "click",
        lambda x, y, hwnd=None: click_calls.append((x, y, hwnd)) or {"ok": True},
    )
    monkeypatch.setattr(window_tools.win32, "_window_text", lambda hwnd: "Target board - Edge")
    session = _WindowSession()

    result = json.loads(session.focus({"title": "Target board"}).text)

    assert result["resolved_by"] == "browser_tab"
    assert click_calls == [(260, 18, 7)]
