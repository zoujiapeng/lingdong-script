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


def test_loop_deduplicates_unchanged_world_advisories(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("open settings")
    runner.session.world_enabled = True
    monkeypatch.setattr(runner.session.world, "state", lambda: {})
    monkeypatch.setattr(runner.session.world, "screen_advisory", lambda: "same screen")
    monkeypatch.setattr(runner.session.world, "advisory", lambda: "same advice")
    monkeypatch.setattr(runner.session.world, "working_set_advisory", lambda: "")
    monkeypatch.setattr(runner.run_state, "guidance", lambda: "")
    monkeypatch.setattr(runner, "_maybe_structured_replan", lambda: None)

    runner._surface_world_advisory()
    runner._surface_world_advisory()

    contents = [message["content"] for message in runner.messages]
    assert contents.count("[screen watch] same screen") == 1
    assert contents.count("[系统观察] same advice") == 1

    runner.messages = runner.messages[-1:]
    runner._surface_world_advisory()

    contents = [message["content"] for message in runner.messages]
    assert contents.count("[screen watch] same screen") == 1


def test_native_visual_loop_keeps_world_and_playbook_tactics_out_of_context(monkeypatch):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("operate the current visible application")
    runner.session.world_enabled = True
    runner.messages = [{"role": "system", "content": "visual contract"}]
    monkeypatch.setattr(runner, "_native_visual_context_enabled", lambda: True)
    monkeypatch.setattr(runner.session.world, "state", lambda: {"phase": "live"})
    monkeypatch.setattr(
        "winpilot.agent.context_guidance.playbooks.for_window_title",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("native visual execution must not query a title playbook")
        ),
    )

    runner._surface_world_advisory()
    runner._maybe_inject_playbook()

    assert runner.messages == [{"role": "system", "content": "visual contract"}]
