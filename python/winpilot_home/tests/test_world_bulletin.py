from types import SimpleNamespace


def _status(sequence: int = 3) -> dict:
    return {
        "sequence": sequence,
        "running": True,
        "last_changed": True,
        "last_ratio": 0.02,
        "changed_regions": [
            {"left": 10, "top": 20, "width": 30, "height": 40},
        ],
        "samples": 5,
        "capture_latency_ms": 4,
        "diff_latency_ms": 1,
    }


def test_world_model_screen_bulletin_advisory_is_edge_triggered():
    from winpilot.perception.world import WorldModel

    world = WorldModel()
    world.record_screen_bulletin(_status())

    state = world.state()
    assert state["screen_watch"]["sequence"] == 3
    assert state["screen_watch"]["last_changed"] is True

    first = world.screen_advisory()
    second = world.screen_advisory()

    assert "ratio=0.0200" in first
    assert "roi=(10,20,30x40)" in first
    assert second == ""


def test_tool_session_records_fast_screen_bulletin_after_dispatch():
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    class Watcher:
        def status(self):
            return _status(sequence=7)

    session = ToolSession(task="demo")
    session._fast_gui_controller = SimpleNamespace(watcher=Watcher())
    session._t_ping = lambda args: ToolResult("ok")

    result = session.dispatch("ping", {})

    assert result.text == "ok"
    assert session.world.state()["screen_watch"]["sequence"] == 7


def test_tool_session_attaches_screen_delta_to_visible_action():
    import json

    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession
    from winpilot.fast.types import ScreenDelta
    from winpilot.utils.screenshot import Region

    class Watcher:
        def __init__(self):
            self.samples = 0

        def sample_once(self):
            self.samples += 1
            if self.samples == 1:
                return ScreenDelta(False, 0.0, [], 1)
            return ScreenDelta(True, 0.0123, [Region(10, 20, 30, 40)], 2)

        def status(self):
            return _status(sequence=self.samples)

    watcher = Watcher()
    session = ToolSession(task="demo")
    session._fast_gui_controller = SimpleNamespace(watcher=watcher)
    session._t_click = lambda args: ToolResult('{"ok": true, "verified": false}')

    result = session.dispatch("click", {"x": 1, "y": 2})
    data = json.loads(result.text)

    assert watcher.samples == 2
    assert data["screen_delta"]["changed"] is True
    assert data["screen_delta"]["ratio"] == 0.0123
    assert data["screen_delta"]["regions"][0]["left"] == 10
    assert "action_target" in data
    assert session.world.state()["screen_watch"]["sequence"] == 2


def test_tool_session_can_skip_screen_delta_for_visible_action():
    import json

    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.tools import ToolSession

    class Watcher:
        def sample_once(self):
            raise AssertionError("screen delta should be skipped")

        def status(self):
            return {}

    session = ToolSession(task="demo")
    session._fast_gui_controller = SimpleNamespace(watcher=Watcher())
    session._t_click = lambda args: ToolResult('{"ok": true}')

    result = session.dispatch("click", {"x": 1, "y": 2, "skip_screen_delta": True})

    assert "screen_delta" not in json.loads(result.text)
