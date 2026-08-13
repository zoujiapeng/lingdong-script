import numpy as np


def _pointer_jpeg_b64(image):
    import base64
    import cv2

    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def test_llm_trace_redacts_frame_without_mutating_api_request(tmp_path, monkeypatch):
    import base64
    import hashlib
    import json
    from types import SimpleNamespace

    from winpilot.agent import loop
    from winpilot.agent.loop import AgentRunner

    raw_frame = b"\xff\xd8diagnostic-jpeg-bytes\xff\xd9"
    encoded = base64.b64encode(raw_frame).decode("ascii")
    image_url = f"data:image/jpeg;base64,{encoded}"
    runner = AgentRunner("inspect the current screen")
    runner.run_id = "trace-frame-redaction"
    runner._visual_frame_id = 7
    runner.messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "[Visual closed-loop frame #7; phase=initial]"},
            {"type": "image_url", "image_url": {"url": image_url}},
        ],
    }]
    monkeypatch.setattr(loop, "TRACES_DIR", tmp_path)
    captured = {}

    class Completions:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=[]),
                finish_reason="stop",
            )])

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    assert runner._chat(client, []) is not None

    # The provider still receives the real pixels and the live history is not
    # mutated by trace sanitization.
    sent_url = captured["messages"][0]["content"][1]["image_url"]["url"]
    assert sent_url == image_url
    assert runner.messages[0]["content"][1]["image_url"]["url"] == image_url

    request_path = next((tmp_path / runner.run_id).glob("*_llm_request.json"))
    trace_text = request_path.read_text(encoding="utf-8")
    assert "data:image" not in trace_text
    assert ";base64," not in trace_text
    assert encoded not in trace_text
    request = json.loads(trace_text)
    metadata = request["messages"][0]["content"][1]["image_url"]
    assert metadata == {
        "frame_present": True,
        "frame_id": 7,
        "frame_byte_count": len(raw_frame),
        "frame_sha256": hashlib.sha256(raw_frame).hexdigest(),
    }


def test_jsonl_trace_event_redacts_inline_frame_in_worker(tmp_path, monkeypatch):
    import base64
    import hashlib
    import json

    from winpilot.recorder import trace
    from winpilot.utils.log import BUS

    raw_frame = b"event-frame-bytes"
    encoded = base64.b64encode(raw_frame).decode("ascii")
    recorder = trace.TraceRecorder()
    monkeypatch.setattr(trace, "TRACES_DIR", tmp_path)
    try:
        recorder.start("inline-event")
        recorder._on_event({
            "type": "agent_debug",
            "run_id": "inline-event",
            "frame_id": 11,
            "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
        })
        recorder.stop()
    finally:
        BUS.remove_sink(recorder._on_event)

    trace_text = (tmp_path / "inline-event.jsonl").read_text(encoding="utf-8")
    assert "data:image" not in trace_text
    assert ";base64," not in trace_text
    assert encoded not in trace_text
    records = [json.loads(line) for line in trace_text.splitlines()]
    event = records[0]
    assert event["image_url"] == {
        "frame_present": True,
        "frame_id": 11,
        "frame_byte_count": len(raw_frame),
        "frame_sha256": hashlib.sha256(raw_frame).hexdigest(),
    }
    assert records[-1] == {
        "type": "trace_integrity",
        "ts": records[-1]["ts"],
        "run_id": "inline-event",
        "accepted_events": 1,
        "dropped_events": 0,
        "write_failures": 0,
        "complete": True,
    }


def test_trace_stop_waits_for_an_accepted_callback_before_integrity(tmp_path, monkeypatch):
    import json
    import threading
    import time

    from winpilot.recorder import trace
    from winpilot.utils.log import BUS

    monkeypatch.setattr(trace, "TRACES_DIR", tmp_path)
    recorder = trace.TraceRecorder()
    entered = threading.Event()
    release = threading.Event()
    original = recorder._record_accepted_event

    def delayed(event):
        entered.set()
        assert release.wait(2)
        original(event)

    monkeypatch.setattr(recorder, "_record_accepted_event", delayed)
    try:
        recorder.start("stop-race")
        producer = threading.Thread(target=recorder._on_event, args=({
            "type": "agent_debug",
            "run_id": "stop-race",
            "value": "last accepted event",
        },))
        producer.start()
        assert entered.wait(1)
        stopper = threading.Thread(target=recorder.stop)
        stopper.start()
        time.sleep(0.03)
        assert stopper.is_alive()
        release.set()
        producer.join(2)
        stopper.join(2)
        assert not producer.is_alive()
        assert not stopper.is_alive()
    finally:
        release.set()
        BUS.remove_sink(recorder._on_event)

    records = [
        json.loads(line)
        for line in (tmp_path / "stop-race.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["value"] == "last accepted event"
    assert records[-1]["type"] == "trace_integrity"
    assert records[-1]["accepted_events"] == 1
    assert records[-1]["complete"] is True


def test_trace_recorder_persists_exact_evidence_frame(tmp_path, monkeypatch):
    from winpilot.recorder import trace
    from winpilot.utils.log import BUS

    monkeypatch.setattr(trace, "TRACES_DIR", tmp_path)
    recorder = trace.TraceRecorder()
    frame = np.zeros((48, 96, 3), dtype=np.uint8)
    frame[8:32, 20:72] = (20, 190, 220)
    try:
        recorder.start("transient-run")
        relative = recorder.save_evidence_image(frame, "transient press ROI")
        recorder.stop()
    finally:
        BUS.remove_sink(recorder._on_event)

    assert relative == "transient-run/evidence_001_transient_press_roi.jpg"
    path = tmp_path / relative
    assert path.exists()
    assert path.stat().st_size > 100


def test_trace_recorder_visualizes_every_pointer_action_with_stable_colors(
    tmp_path, monkeypatch,
):
    import cv2

    from winpilot.recorder import trace
    from winpilot.utils.log import BUS

    monkeypatch.setattr(trace, "TRACES_DIR", tmp_path)
    recorder = trace.TraceRecorder()
    frame = np.full((180, 300, 3), 238, dtype=np.uint8)
    encoded = _pointer_jpeg_b64(frame)
    published = []
    BUS.add_sink(published.append)
    common = {
        "run_id": "pointer-colors",
        "frame_image_b64": encoded,
        "frame_id": 4,
        "frame_region": (100, 200, 300, 180),
        "submitted_image_size": (300, 180),
        "cursor_before": (120, 220),
    }
    actions = [
        (
            "move_mouse",
            {"image_x": 80.0, "image_y": 70.0},
            {"kind": "pointer_move", "x": 180, "y": 270},
        ),
        (
            "click",
            {"image_x": 90.0, "image_y": 80.0},
            {"kind": "pointer_click", "x": 190, "y": 280},
        ),
        (
            "double_click",
            {"image_x": 100.0, "image_y": 90.0},
            {"kind": "pointer_click", "x": 200, "y": 290},
        ),
        (
            "drag",
            {"image_path": [[30.0, 30.0], [110.0, 70.0], [210.0, 120.0]]},
            {
                "kind": "pointer_drag",
                "x1": 130,
                "y1": 230,
                "points": [[210, 270]],
                "x2": 310,
                "y2": 320,
            },
        ),
        (
            "scroll",
            {"image_x": 160.0, "image_y": 90.0, "amount": -240},
            {
                "kind": "pointer_scroll",
                "x": 260,
                "y": 290,
                "signed_wheel_amount": -240,
            },
        ),
        (
            "input_sequence",
            {},
            {
                "kind": "input_sequence",
                "pointer_events": [
                    {"index": 0, "kind": "move", "x": 160, "y": 250},
                    {"index": 1, "kind": "mouse_down", "button": "left"},
                    {"index": 2, "kind": "move_relative", "x": 70, "y": 35},
                    {"index": 3, "kind": "wheel", "amount": 120},
                    {"index": 4, "kind": "hwheel", "amount": -120},
                    {"index": 5, "kind": "mouse_up", "button": "left"},
                ],
            },
        ),
    ]
    metadata = []
    try:
        recorder.start("pointer-colors")
        for index, (action, source, physical) in enumerate(actions, start=1):
            metadata.append(recorder.record_pointer_debug(
                **common,
                decision_id=f"decision-{index}",
                action=action,
                image_coordinates=source,
                physical_arguments=physical,
            ))
        recorder.stop()
    finally:
        BUS.remove_sink(published.append)
        BUS.remove_sink(recorder._on_event)

    assert all(item is not None for item in metadata)
    paths = [tmp_path / str(item["screenshot"]) for item in metadata]
    assert all(path.exists() and path.stat().st_size > 1000 for path in paths)
    assert all(cv2.imread(str(path)).shape[:2] == (180, 300) for path in paths)
    # Every diagnostic is derived from the submitted frame, but has visible
    # colored markings; no model-authoritative pixels were mutated in place.
    assert np.all(frame == 238)
    rendered = [cv2.imread(str(path)) for path in paths]
    assert all(np.any(image != image[0, 0]) for image in rendered)
    assert metadata[0]["colors"] == trace._POINTER_DEBUG_COLORS
    screenshots = [
        event
        for event in published
        if event.get("type") == "trace_screenshot"
        and event.get("screenshot_kind") == "pointer_debug"
    ]
    assert [event["action"] for event in screenshots] == [
        action for action, _source, _physical in actions
    ]


def test_pointer_debug_failure_never_blocks_or_fakes_an_artifact(
    tmp_path, monkeypatch,
):
    from winpilot.recorder import trace
    from winpilot.utils.log import BUS

    monkeypatch.setattr(trace, "TRACES_DIR", tmp_path)
    recorder = trace.TraceRecorder()
    try:
        recorder.start("broken-pointer-image")
        metadata = recorder.record_pointer_debug(
            run_id="broken-pointer-image",
            decision_id="decision-broken",
            action="click",
            frame_image_b64="not-base64",
            frame_id=1,
            frame_region=(0, 0, 100, 100),
            submitted_image_size=(100, 100),
            image_coordinates={"image_x": 20, "image_y": 30},
            physical_arguments={"kind": "pointer_click", "x": 20, "y": 30},
            cursor_before=None,
        )
        # Enqueue success is useful immediately to the dispatcher. The worker
        # reports an integrity failure instead of affecting physical input.
        assert metadata is not None
        recorder.stop()
    finally:
        BUS.remove_sink(recorder._on_event)

    assert not (tmp_path / str(metadata["screenshot"])).exists()
    import json

    records = [
        json.loads(line)
        for line in (tmp_path / "broken-pointer-image.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert records[-1]["type"] == "trace_integrity"
    assert records[-1]["write_failures"] == 1
