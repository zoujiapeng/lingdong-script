import base64

import cv2
import numpy as np


def _jpeg_b64(image: np.ndarray) -> str:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    assert ok
    return base64.b64encode(buf.tobytes()).decode("ascii")


def test_fold_base64_adds_text_only_note_and_nonvisual_summary():
    from winpilot.agent.desktop_tools import DesktopToolMixin

    image = np.full((100, 140, 3), 255, dtype=np.uint8)
    cv2.line(image, (20, 30), (120, 30), (0, 0, 0), 3)
    result = {"ok": True, "base64": _jpeg_b64(image)}

    DesktopToolMixin._fold_base64(result)

    assert "base64" not in result
    assert result["base64_available"] is True
    assert "text-only main model cannot inspect" in result["text_only_note"]
    assert result["nonvisual_summary"]["blankish"] is False
    assert result["nonvisual_summary"]["ink_ratio"] > 0


def test_trace_saves_draw_strokes_before_after_screenshots():
    from winpilot.recorder.trace import _SCREENSHOT_TOOLS

    assert "draw_strokes" in _SCREENSHOT_TOOLS


def test_fold_base64_adds_likely_canvas_summary_for_mixed_zoom():
    from winpilot.agent.desktop_tools import DesktopToolMixin

    image = np.full((420, 300, 3), 25, dtype=np.uint8)
    image[:, :190] = 255
    cv2.line(image, (40, 70), (150, 260), (0, 0, 0), 2)
    result = {"ok": True, "base64": _jpeg_b64(image)}

    DesktopToolMixin._fold_base64(result)

    summary = result["nonvisual_summary"]
    assert summary["ink_ratio"] > summary["likely_canvas_summary"]["ink_ratio"]
    assert summary["likely_canvas_bbox"][2] <= 200
    assert summary["likely_canvas_summary"]["blankish"] is False
