import base64
import json
import time

import cv2
import numpy as np


def _jpeg_b64(image: np.ndarray) -> str:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    assert ok
    return base64.b64encode(buf.tobytes()).decode("ascii")


def test_trace_saves_draw_strokes_completion_screenshots():
    from winpilot.recorder.trace import _BATCH_SCREENSHOT_TOOLS, _SCREENSHOT_TOOLS

    assert "draw_strokes" in _SCREENSHOT_TOOLS
    assert "draw_strokes" in _BATCH_SCREENSHOT_TOOLS


def test_trace_saves_batch_child_after_screenshot(tmp_path, monkeypatch):
    from winpilot.recorder import trace as trace_mod

    recorder = trace_mod.TraceRecorder()
    trace_mod.BUS.remove_sink(recorder._on_event)
    published = []
    trace_mod.BUS.add_sink(published.append)
    image = np.full((40, 60, 3), 255, dtype=np.uint8)
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(trace_mod, "capture", lambda _region=None: image.copy())
    region = {"left": 1, "top": 2, "width": 60, "height": 40}
    envelope = {
        "action_id": "a1",
        "type": "draw_strokes",
        "target": {"hwnd": 44, "region": region},
    }

    try:
        recorder.start("r1")
        recorder._on_event({
            "type": "agent_batch_action_start",
            "event_name": "action.started",
            "action_id": "a1",
            "action_envelope": envelope,
            "tool": "draw_strokes",
            "batch_index": 1,
        })
        recorder._on_event({
            "type": "agent_batch_action",
            "event_name": "action.finished",
            "action_id": "a1",
            "action_envelope": envelope,
            "tool": "draw_strokes",
            "batch_index": 1,
            "elapsed_ms": 12,
        })
    finally:
        recorder.stop()
        trace_mod.BUS.remove_sink(published.append)

    events = [
        json.loads(line)
        for line in (tmp_path / "r1.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    screenshots = [event.get("screenshot") for event in events]

    assert screenshots[0] is None
    assert "r1/batch_after_001.jpg" in screenshots
    action_events = [event for event in events if event.get("action_id") == "a1"]
    assert [event.get("screenshot_region") for event in action_events] == [None, region]
    assert not (tmp_path / "r1" / "batch_before_001.jpg").exists()
    assert (tmp_path / "r1" / "batch_after_001.jpg").exists()
    live_screenshots = [
        event for event in published
        if event.get("type") == "trace_screenshot" and event.get("run_id") == "r1"
    ]
    assert [event["screenshot"] for event in live_screenshots] == [
        "r1/batch_after_001.jpg"
    ]


def test_trace_writer_is_async_and_pairs_interleaved_action_ids(tmp_path, monkeypatch):
    from winpilot.recorder import trace as trace_mod
    from winpilot.utils.screenshot import Region

    recorder = trace_mod.TraceRecorder()
    trace_mod.BUS.remove_sink(recorder._on_event)
    image = np.full((20, 30, 3), 255, dtype=np.uint8)
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)

    region = Region(2, 3, 30, 20)

    def slow_capture(_region=None):
        time.sleep(0.05)
        return image.copy()

    monkeypatch.setattr(trace_mod, "capture", slow_capture)
    envelopes = {
        action_id: {
            "action_id": action_id,
            "type": "click",
            "target": {
                "hwnd": 77,
                "region": {
                    "left": region.left,
                    "top": region.top,
                    "width": region.width,
                    "height": region.height,
                },
            },
        }
        for action_id in ("a", "b")
    }
    try:
        recorder.start("async")
        started = time.monotonic()
        recorder._on_event({"type": "agent_tool_start", "action_id": "a", "tool": "click", "action_envelope": envelopes["a"]})
        recorder._on_event({"type": "agent_tool_start", "action_id": "b", "tool": "click", "action_envelope": envelopes["b"]})
        recorder._on_event({"type": "agent_tool", "action_id": "a", "tool": "click", "action_envelope": envelopes["a"]})
        recorder._on_event({"type": "agent_tool", "action_id": "b", "tool": "click", "action_envelope": envelopes["b"]})
        enqueue_elapsed = time.monotonic() - started
        recorder.stop()
    finally:
        trace_mod.BUS.remove_sink(recorder._on_event)

    assert enqueue_elapsed < 0.04
    events = [
        json.loads(line)
        for line in (tmp_path / "async.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    by_action = {}
    for event in events:
        by_action.setdefault(event.get("action_id"), []).append(event.get("screenshot"))
    assert by_action["a"] == [None, "async/after_001.jpg"]
    assert by_action["b"] == [None, "async/after_002.jpg"]
    assert not (tmp_path / "async" / "before_001.jpg").exists()
    assert not (tmp_path / "async" / "before_002.jpg").exists()


def test_trace_captures_after_without_before_screenshot(tmp_path, monkeypatch):
    from winpilot.recorder import trace as trace_mod
    from winpilot.utils.screenshot import Region

    recorder = trace_mod.TraceRecorder()
    trace_mod.BUS.remove_sink(recorder._on_event)
    current = np.full((30, 40, 3), 255, dtype=np.uint8)
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    region = Region(5, 6, 40, 30)
    monkeypatch.setattr(trace_mod, "capture", lambda _region=None: current.copy())
    monkeypatch.setattr(trace_mod, "_POST_ACTION_CAPTURE_SETTLE_S", 0.0)

    try:
        recorder.start("frame-order")
        envelope = {
            "action_id": "b:1",
            "type": "click",
            "target": {
                "hwnd": 88,
                "region": {
                    "left": region.left,
                    "top": region.top,
                    "width": region.width,
                    "height": region.height,
                },
            },
        }
        recorder._on_event({
            "type": "agent_batch_action_start",
            "action_id": "b:1",
            "action_envelope": envelope,
            "tool": "click",
            "batch_id": "b",
            "batch_index": 1,
        })
        recorder._on_event({
            "type": "agent_batch_action",
            "action_id": "b:1",
            "action_envelope": envelope,
            "tool": "click",
            "batch_id": "b",
            "batch_index": 1,
        })
        recorder.stop()
    finally:
        trace_mod.BUS.remove_sink(recorder._on_event)

    before_path = tmp_path / "frame-order" / "batch_before_001.jpg"
    after = cv2.imread(str(tmp_path / "frame-order" / "batch_after_001.jpg"))
    assert not before_path.exists()
    assert float(after.mean()) > 250
