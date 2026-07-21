import numpy as np


def test_trace_sidecar_does_not_mutate_bus_event_and_records_private_baseline(tmp_path, monkeypatch):
    import json

    from winpilot.recorder import trace
    from winpilot.utils.log import BUS

    monkeypatch.setattr(trace, "TRACES_DIR", tmp_path)
    recorder = trace.TraceRecorder()
    try:
        recorder.start("telemetry-sidecar")
        event = BUS.publish(
            "agent_start",
            run_id="telemetry-sidecar",
            step=1,
            model_role="legacy_visual_agent",
            model_tool_count=2,
        )
        # The object returned by BUS is the same object sent to all product
        # subscribers.  Trace-side telemetry must not add its private fields.
        assert "vnext_baseline" not in event
        recorder.stop()
    finally:
        BUS.remove_sink(recorder._on_event)

    records = [
        json.loads(line)
        for line in (tmp_path / "telemetry-sidecar.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    agent_start = next(record for record in records if record.get("type") == "agent_start")
    assert agent_start["step"] == 1
    assert agent_start["model_role"] == "legacy_visual_agent"
    assert agent_start["vnext_baseline"]["format"] == "vnext-baseline/0"
    assert "vnext_baseline" not in event


def test_explicit_trace_sidecar_never_reaches_product_bus(tmp_path, monkeypatch):
    import json

    from winpilot.recorder import trace
    from winpilot.utils.log import BUS

    monkeypatch.setattr(trace, "TRACES_DIR", tmp_path)
    recorder = trace.TraceRecorder()
    events = None
    try:
        recorder.start("private-shadow")
        events = BUS.subscribe()
        assert recorder.record_sidecar(
            {
                "type": "vnext_profiler_shadow",
                "status": "completed",
                "profile_call_count": 1,
            },
            run_id="private-shadow",
        ) is True
        assert events.empty()
        recorder.stop()
    finally:
        if events is not None:
            BUS.unsubscribe(events)
        BUS.remove_sink(recorder._on_event)

    records = [
        json.loads(line)
        for line in (tmp_path / "private-shadow.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    shadow = next(item for item in records if item.get("type") == "vnext_profiler_shadow")
    assert shadow["run_id"] == "private-shadow"
    assert shadow["profile_call_count"] == 1


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


def test_agent_decision_has_explicit_step_role_without_image_history_serialization(monkeypatch):
    import base64
    from types import SimpleNamespace

    from winpilot.agent import loop
    from winpilot.agent.loop import AgentRunner
    from winpilot.utils.log import BUS

    image_url = "data:image/jpeg;base64," + base64.b64encode(b"pixels").decode("ascii")
    runner = AgentRunner("inspect")
    runner.run_id = "decision-telemetry"
    runner._decision_step = 4
    runner._trace_debug = lambda *args, **kwargs: None
    runner.messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "current frame"},
            {"type": "image_url", "image_url": {"url": image_url}},
        ],
    }]
    captured_json_args = []
    original_dumps = loop.json.dumps

    def spy_dumps(value, *args, **kwargs):
        captured_json_args.append(value)
        return original_dumps(value, *args, **kwargs)

    monkeypatch.setattr(loop.json, "dumps", spy_dumps)
    events = BUS.subscribe()

    class Completions:
        @staticmethod
        def create(**kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="ok", tool_calls=[]),
                finish_reason="stop",
            )])

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    try:
        assert runner._chat(client, []) is not None
        decision = None
        while not events.empty():
            item = events.get_nowait()
            if item.get("type") == "agent_decision":
                decision = item
        assert decision is not None
        assert decision["step"] == 4
        assert decision["model_role"] == "legacy_visual_agent"
        assert decision["prompt_context_measurement"] == "text_structure_without_binary"
        # The only JSON measurement call is for tool schema.  The image-bearing
        # message list is passed to the provider but not serialized for metrics.
        assert all(value is not runner.messages for value in captured_json_args)
        assert all(image_url not in repr(value) for value in captured_json_args)
    finally:
        BUS.unsubscribe(events)


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
    event = json.loads(trace_text)
    assert event["image_url"] == {
        "frame_present": True,
        "frame_id": 11,
        "frame_byte_count": len(raw_frame),
        "frame_sha256": hashlib.sha256(raw_frame).hexdigest(),
    }


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
