import json
from types import SimpleNamespace

import numpy as np


def test_draw_strokes_visual_change_marks_action_verified(monkeypatch):
    from winpilot.agent import tools
    from winpilot.agent.tools import ToolSession

    before = np.full((80, 80, 3), 255, dtype=np.uint8)
    after = before.copy()
    after[20:60, 20:60] = 0
    captures = [before, after]

    monkeypatch.setattr(tools, "capture", lambda _region: captures.pop(0))
    monkeypatch.setattr(
        tools,
        "executor",
        SimpleNamespace(
            draw_strokes=lambda strokes, duration_s, hwnd: {
                "ok": True,
                "strokes_drawn": len(strokes),
            }
        ),
    )

    session = ToolSession(task="draw a simple visible shape")
    session.hwnd = 123
    result = session._t_draw_strokes({
        "strokes": [[[10, 10], [50, 50]]],
        "duration": 0.1,
    })
    data = json.loads(result.text)

    assert data["visual_change_detected"] is True
    assert data["verified"] is True
    assert data["verify_method"] == "cv-diff"
    assert "instead of redrawing" in data["visual_feedback"]
