from types import SimpleNamespace

import cv2
import numpy as np


def test_visual_state_summary_for_drawing_task(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    image = np.full((120, 180, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (80, 40), (150, 100), (255, 0, 0), -1)

    monkeypatch.setattr(tools, "window_region", lambda hwnd: Region(10, 20, 180, 120))
    monkeypatch.setattr(tools, "capture", lambda region: image)

    session = ToolSession(task="draw a blue house on the canvas")
    session.snapshot = SimpleNamespace(
        title="Paint",
        elements=[SimpleNamespace(text="Line", role="text")],
    )

    summary = session._visual_state_summary(123)

    assert "Visual state summary" in summary
    assert "blue@(90,60,161,121)" in summary
    assert "popup/tool panel appears open" in summary


def test_visual_state_summary_skipped_for_plain_task():
    from winpilot.agent.tools import ToolSession

    session = ToolSession(task="open calculator")
    session.snapshot = SimpleNamespace(title="Calculator", elements=[])

    assert session._needs_visual_state_summary() is False


def test_visual_state_summary_marks_tool_panel_colors(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession
    from winpilot.utils.screenshot import Region

    image = np.full((80, 100, 3), 30, dtype=np.uint8)
    cv2.rectangle(image, (10, 10), (30, 60), (0, 0, 255), -1)

    monkeypatch.setattr(tools, "window_region", lambda hwnd: Region(0, 0, 100, 80))
    monkeypatch.setattr(tools, "capture", lambda region: image)

    session = ToolSession(task="draw red sun")
    session.snapshot = SimpleNamespace(
        title="Paint",
        elements=[SimpleNamespace(text="Edit colors", role="text")],
    )

    summary = session._visual_state_summary(123)

    assert "tool panel, not the canvas" in summary
    assert "tool/color modal is foreground" in session.take_visual_advisory()
    assert session.take_visual_advisory() == ""


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
