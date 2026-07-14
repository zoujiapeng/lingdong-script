def test_visual_state_summary_does_not_inject_task_specific_pixel_rules():
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="draw a blue house on the canvas")
    assert session._visual_state_summary(123) == ""


def test_loop_surfaces_visual_advisory(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("draw a red sun")
    runner.session.world_enabled = True
    runner.session._pending_visual_advisory = "close the color modal before drawing"
    monkeypatch.setattr(runner.session.world, "state", lambda: {})
    monkeypatch.setattr(runner.session.world, "advisory", lambda: "")
    monkeypatch.setattr(runner, "_maybe_structured_replan", lambda: None)

    runner._surface_world_advisory()

    assert runner.messages[-1]["content"] == (
        "[visual state warning] close the color modal before drawing"
    )
