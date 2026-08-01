"""Integration: human-recorded steps → validate → offline replay.

Proves the P4 (human recorder) output is consumable by the P1 (Replayer)
engine end-to-end, without any real desktop input or screen — the StepBuilder
produces steps, they pass schema validation, and the Replayer executes them in
order (leaf I/O spied out).
"""
import json

from winpilot import automation_ir
from winpilot.recorder.human import StepBuilder
from winpilot.recorder import trace as trace_mod


def test_recorded_steps_validate_and_replay(tmp_path, monkeypatch):
    # 1. simulate a human demonstration: type "hi", click a button
    target = {"title": "Document - Editor", "process": "editor.exe"}
    b = StepBuilder()
    b.key_down("h", target); b.key_down("i", target)
    b.mouse_press(
        10, 20, "left", {"text": "确定", "role": "button", "source": "uia"}, target,
    )
    b.mouse_release(10, 20, "left")
    steps = trace_mod._distill_steps(b.finish())

    script = {
        "format": "wps/2",
        "source": "human",
        "name": "demo",
        "target_window": target,
        "steps": steps,
    }

    # 2. WPS validation and the canonical runtime preserve the target boundary.
    assert trace_mod.validate_script(script) is None
    canonical = automation_ir.automation_ir_from_wps(script, ir_id="air-human-demo")
    assert canonical["conditions"]["target_window"] == script["target_window"]
    assert canonical["steps"] == steps
    assert automation_ir.validate_automation_runtime(canonical) is None

    # 3. Replayer consumes the canonical document and binds preflight first.
    path = tmp_path / "demo.automation.json"
    path.write_text(json.dumps(canonical, ensure_ascii=False), encoding="utf-8")

    calls: list[str] = []

    class Spy(trace_mod.Replayer):
        def __init__(self) -> None:
            super().__init__()
            self.step_delay = 0

        def _run_step(self, idx, step):
            calls.append(step.get("tool"))
            return {"ok": True}

        def _find_target_window(self, hint):
            assert hint["title"] == "Document - Editor"
            assert hint["process"] == "editor.exe"
            return {"hwnd": 20, "title": "Document - Editor", "pid": 2}

    monkeypatch.setattr(trace_mod.executor, "focus_window", lambda hwnd: {"ok": True, "hwnd": hwnd})
    out = Spy().run(path)
    assert out["ok"] is True
    assert out["preflight"]["required"] is True
    assert out["preflight"]["hwnd"] == 20
    assert calls == ["type_text", "click"]
    # the click step kept its semantic selector (text), not just coords
    click = next(s for s in steps if s["tool"] == "click")
    assert click["text"] == "确定"
