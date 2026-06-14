"""Integration: human-recorded steps → validate → offline replay.

Proves the P4 (human recorder) output is consumable by the P1 (Replayer)
engine end-to-end, without any real desktop input or screen — the StepBuilder
produces steps, they pass schema validation, and the Replayer executes them in
order (leaf I/O spied out).
"""
import json

from winpilot.recorder.human import StepBuilder
from winpilot.recorder import trace as trace_mod


def test_recorded_steps_validate_and_replay(tmp_path):
    # 1. simulate a human demonstration: type "hi", click a button
    b = StepBuilder()
    b.key_down("h"); b.key_down("i")
    b.mouse_press(10, 20, "left", {"text": "确定", "role": "button", "source": "uia"})
    b.mouse_release(10, 20, "left")
    steps = trace_mod._distill_steps(b.finish())

    script = {"format": "wps/2", "source": "human", "name": "demo", "steps": steps}

    # 2. the produced script passes structural validation (PUT /api/script gate)
    assert trace_mod.validate_script(script) is None

    # 3. the Replayer executes it in order (leaf I/O spied; no real screen)
    path = tmp_path / "demo.wps.json"
    path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    calls: list[str] = []

    class Spy(trace_mod.Replayer):
        def __init__(self) -> None:
            super().__init__()
            self.step_delay = 0

        def _run_step(self, idx, step):
            calls.append(step.get("tool"))
            return {"ok": True}

    out = Spy().run(path)
    assert out["ok"] is True
    assert calls == ["type_text", "click"]
    # the click step kept its semantic selector (text), not just coords
    click = next(s for s in steps if s["tool"] == "click")
    assert click["text"] == "确定"
