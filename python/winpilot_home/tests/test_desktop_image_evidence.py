import base64
import json
import time

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
    from winpilot.recorder.trace import _BATCH_SCREENSHOT_TOOLS, _SCREENSHOT_TOOLS

    assert "draw_strokes" in _SCREENSHOT_TOOLS
    assert "draw_strokes" in _BATCH_SCREENSHOT_TOOLS


def test_trace_saves_batch_child_before_after_screenshots(tmp_path, monkeypatch):
    from winpilot.recorder import trace as trace_mod

    recorder = trace_mod.TraceRecorder()
    trace_mod.BUS.remove_sink(recorder._on_event)
    published = []
    trace_mod.BUS.add_sink(published.append)
    image = np.full((40, 60, 3), 255, dtype=np.uint8)
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(trace_mod, "capture", lambda _region=None: image.copy())
    monkeypatch.delenv("WINPILOT_STRICT_TRACE_BASELINE", raising=False)
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

    assert "r1/batch_before_001.jpg" in screenshots
    assert "r1/batch_after_001.jpg" in screenshots
    action_events = [event for event in events if event.get("action_id") == "a1"]
    assert [event.get("screenshot_region") for event in action_events] == [region, region]
    assert action_events[0]["baseline_capture_source"] == "sync_pre_action"
    assert "baseline_capture_source" not in action_events[1]
    assert action_events[1]["post_capture_source"] == "sync_post_action"
    assert (tmp_path / "r1" / "batch_before_001.jpg").exists()
    assert (tmp_path / "r1" / "batch_after_001.jpg").exists()
    live_screenshots = [
        event for event in published
        if event.get("type") == "trace_screenshot" and event.get("run_id") == "r1"
    ]
    assert [event["screenshot"] for event in live_screenshots] == [
        "r1/batch_before_001.jpg",
        "r1/batch_after_001.jpg",
    ]


def test_trace_writer_is_async_and_pairs_interleaved_action_ids(tmp_path, monkeypatch):
    from winpilot.fast import perception_daemon
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
    monkeypatch.setenv("WINPILOT_STRICT_TRACE_BASELINE", "0")
    monkeypatch.setattr(
        perception_daemon.PERCEPTION_DAEMON,
        "latest_frame",
        lambda **_kwargs: (image.copy(), region),
    )
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
    assert by_action["a"] == ["async/before_001.jpg", "async/after_001.jpg"]
    assert by_action["b"] == ["async/before_002.jpg", "async/after_002.jpg"]


def test_trace_after_screenshot_does_not_reuse_pre_action_daemon_frame(tmp_path, monkeypatch):
    from winpilot.fast import perception_daemon
    from winpilot.recorder import trace as trace_mod
    from winpilot.utils.screenshot import Region

    recorder = trace_mod.TraceRecorder()
    trace_mod.BUS.remove_sink(recorder._on_event)
    stale = np.zeros((30, 40, 3), dtype=np.uint8)
    current = np.full((30, 40, 3), 255, dtype=np.uint8)
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    # Exercise the explicit low-latency opt-out path here; strict mode now
    # synchronously captures the before-frame by default.
    monkeypatch.setenv("WINPILOT_STRICT_TRACE_BASELINE", "0")
    region = Region(5, 6, 40, 30)
    monkeypatch.setattr(trace_mod, "capture", lambda _region=None: current.copy())
    monkeypatch.setattr(
        perception_daemon.PERCEPTION_DAEMON,
        "latest_frame",
        lambda **_kwargs: (stale.copy(), region),
    )
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

    before = cv2.imread(str(tmp_path / "frame-order" / "batch_before_001.jpg"))
    after = cv2.imread(str(tmp_path / "frame-order" / "batch_after_001.jpg"))
    assert float(before.mean()) < 5
    assert float(after.mean()) > 250


def test_strict_trace_seals_after_frame_before_next_action(tmp_path, monkeypatch):
    from winpilot.recorder import trace as trace_mod

    recorder = trace_mod.TraceRecorder()
    trace_mod.BUS.remove_sink(recorder._on_event)
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    monkeypatch.delenv("WINPILOT_STRICT_TRACE_BASELINE", raising=False)
    current = np.zeros((24, 32, 3), dtype=np.uint8)
    monkeypatch.setattr(trace_mod, "capture", lambda _region=None: current.copy())
    region = {"left": 2, "top": 3, "width": 32, "height": 24}
    envelope = {
        "action_id": "a",
        "type": "click",
        "target": {"hwnd": 77, "region": region},
    }

    try:
        recorder.start("strict-order")
        recorder._on_event({
            "type": "agent_tool_start",
            "action_id": "a",
            "tool": "click",
            "action_envelope": envelope,
        })
        current[:] = 120  # effect of action A
        recorder._on_event({
            "type": "agent_tool",
            "action_id": "a",
            "tool": "click",
            "action_envelope": envelope,
        })
        # A later action changes the live desktop before the writer drains.
        current[:] = 255
        recorder.stop()
    finally:
        trace_mod.BUS.remove_sink(recorder._on_event)

    after = cv2.imread(str(tmp_path / "strict-order" / "after_001.jpg"))
    assert 115 <= float(after.mean()) <= 125
    events = [
        json.loads(line)
        for line in (tmp_path / "strict-order.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    finished = next(event for event in events if event.get("type") == "agent_tool")
    assert finished["post_capture_source"] == "sync_post_action"


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
