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
