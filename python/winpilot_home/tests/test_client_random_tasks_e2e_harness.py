from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from PIL import Image, ImageDraw


def _load_harness():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "client_random_tasks_e2e_run.py"
    )
    spec = importlib.util.spec_from_file_location(
        "client_random_tasks_e2e_run_test", path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _paint_frame(*, line: bool = False) -> Image.Image:
    image = Image.new("RGB", (800, 600), (32, 32, 32))
    draw = ImageDraw.Draw(image)
    draw.rectangle((90, 170, 760, 560), fill=(255, 255, 255))
    if line:
        draw.line((220, 340, 650, 340), fill=(20, 20, 20), width=5)
    return image


def test_find_window_prefers_foreground_then_topmost_z_order(monkeypatch):
    harness = _load_harness()

    def enum_windows(callback, extra):
        for hwnd in (30, 20, 10):
            callback(hwnd, extra)

    monkeypatch.setattr(harness.win32gui, "EnumWindows", enum_windows)
    monkeypatch.setattr(harness.win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(
        harness.win32gui,
        "GetWindowText",
        lambda hwnd: "Untitled - Paint" if hwnd in {30, 20} else "Other",
    )
    monkeypatch.setattr(harness.win32gui, "GetForegroundWindow", lambda: 20)

    assert harness._find_window("Paint", timeout=0.01) == 20

    monkeypatch.setattr(harness.win32gui, "GetForegroundWindow", lambda: 10)
    assert harness._find_window("Paint", timeout=0.01) == 30


def test_paint_oracle_accepts_a_horizontal_canvas_stroke(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    monkeypatch.setattr(harness.win32gui, "IsWindow", lambda _hwnd: True)
    blank = tmp_path / "blank.png"
    completed = tmp_path / "completed.png"
    _paint_frame().save(blank)
    _paint_frame(line=True).save(completed)

    no_change = harness._paint_stroke_evidence(55, str(blank))
    stroke = harness._paint_stroke_evidence(55, str(completed))

    assert no_change["stroke_detected"] is False
    assert stroke["stroke_detected"] is True
    assert stroke["spread_ratio"] >= 0.25


def test_paint_verifier_focuses_live_window_before_terminal_capture(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    terminal = tmp_path / "paint-terminal.png"
    events: list[tuple[str, int]] = []
    monkeypatch.setattr(
        harness,
        "_current_target_hwnd",
        lambda _case, _fallback: 55,
    )
    monkeypatch.setattr(harness.win32gui, "IsWindow", lambda _hwnd: True)

    def focus(hwnd: int):
        events.append(("focus", hwnd))
        return {"focused": True, "target_hwnd": hwnd}

    def capture(hwnd: int, path: Path):
        assert events == [("focus", 55)]
        events.append(("capture", hwnd))
        image = Image.new("RGB", (800, 600), "white")
        ImageDraw.Draw(image).line(
            (120, 300, 680, 300), fill="black", width=5,
        )
        image.save(path)
        return str(path)

    monkeypatch.setattr(harness, "_focus_window", focus)
    monkeypatch.setattr(harness, "_capture_window", capture)
    case = {
        "family": "paint-stroke",
        "paint_hwnd": 44,
        "paint_screenshot": str(terminal),
    }

    verdict = harness._external_verifier(case)({
        "result": {"status_label": "done", "outcome": "terminal"},
    })

    assert events == [("focus", 55), ("capture", 55)]
    assert case["paint_hwnd"] == 55
    assert verdict["passed"] is True
    assert verdict["evidence"]["terminal_focus"]["focused"] is True
