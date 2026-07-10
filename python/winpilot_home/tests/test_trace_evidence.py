import numpy as np


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
