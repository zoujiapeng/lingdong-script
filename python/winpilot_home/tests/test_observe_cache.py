from winpilot.agent.tools import ToolSession
from winpilot.perception import fusion
from winpilot.perception.model import UIElement
from winpilot.utils.screenshot import Region


def _snapshot(title: str = "Demo") -> fusion.Snapshot:
    snap = fusion.Snapshot(hwnd=123, title=title, region=Region(0, 0, 200, 100))
    snap.elements = [
        UIElement(source="uia", role="button", text="Search", cx=10, cy=10, left=1, top=1, width=20, height=20)
    ]
    return snap


def test_observe_reuses_cache_for_same_epoch(monkeypatch):
    session = ToolSession(task="demo")
    monkeypatch.setattr(session, "_resolve_hwnd", lambda args: 123)
    monkeypatch.setattr(session, "_remember_target", lambda hwnd=None, hint="": None)
    monkeypatch.setattr(session, "_visual_state_summary", lambda hwnd: "")
    monkeypatch.setattr(session, "_capture_window_b64", lambda hwnd: "")
    monkeypatch.setattr(session, "_perception_mode", lambda: "normal")
    monkeypatch.setattr("winpilot.agent.perception_tools.CONFIG.get", lambda *a, **k: 10.0)

    calls = {"n": 0}

    def fake_observe(*_args, **_kwargs):
        calls["n"] += 1
        return _snapshot()

    monkeypatch.setattr("winpilot.agent.perception_tools.fusion.observe", fake_observe)

    first = session._t_observe({"query": "Search"})
    second = session._t_observe({"query": "Search"})

    assert calls["n"] == 1
    assert "Search" in first.text
    assert "Search" in second.text
    assert session.snapshot is not None
    assert "cache" in session.snapshot.dim_stats


def test_mutating_dispatch_invalidates_observe_cache(monkeypatch):
    session = ToolSession(task="demo")
    key = (123, True, "normal", session._screen_epoch)
    session.store_observe_cache(key, _snapshot())
    assert session.cached_observe(key, 10.0) is not None

    monkeypatch.setattr(session, "_t_click", lambda args: type("R", (), {
        "text": "{}",
        "is_observe": False,
        "terminal": None,
        "summary": "",
    })())

    session.dispatch("click", {"x": 1, "y": 1})

    assert session._screen_epoch == 1
    assert session.cached_observe(key, 10.0) is None
