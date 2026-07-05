import json

from winpilot import automation_ir
from winpilot.recorder import trace as trace_mod


def _write_trace(path, events):
    with open(path, "w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def test_automation_ir_from_wps_preserves_replay_metadata():
    wps = {
        "format": "wps/2",
        "name": "send message",
        "task": "open app and send text",
        "source": "agent",
        "source_run": "run-1",
        "created": 123,
        "target_window": {"title": "Target"},
        "vars": {"msg": "hello"},
        "evidence": ["message visible"],
        "steps": [
            {"tool": "click", "coords": [10, 20]},
            {"tool": "type_text", "text_input": "${msg}"},
        ],
    }

    ir = automation_ir.automation_ir_from_wps(wps, ir_id="air-test")

    assert automation_ir.validate_automation_ir(ir) is None
    assert ir["format"] == "automation-ir/1"
    assert ir["id"] == "air-test"
    assert ir["source"]["kind"] == "wps"
    assert ir["source"]["source_run"] == "run-1"
    assert ir["conditions"]["target_window"] == {"title": "Target"}
    assert ir["variables"] == {"msg": "hello"}
    assert ir["evidence"]["required"] == ["message visible"]
    assert ir["safety"]["mutates_desktop"] is True
    assert ir["safety"]["desktop_action_queue_required"] is True


def test_automation_ir_roundtrips_to_wps():
    wps = {
        "format": "wps/2",
        "name": "demo",
        "task": "demo task",
        "conditions": {"target_window": {"title": "Demo"}},
        "vars": {"a": "1"},
        "steps": [{"tool": "click", "coords": [1, 2]}],
    }
    ir = automation_ir.automation_ir_from_wps(wps, ir_id="air-roundtrip")

    out = automation_ir.wps_from_automation_ir(ir)

    assert out["format"] == "wps/2"
    assert out["source"] == "automation-ir"
    assert out["source_ir"] == "air-roundtrip"
    assert out["target_window"] == {"title": "Demo"}
    assert out["vars"] == {"a": "1"}
    assert out["steps"] == [{"tool": "click", "coords": [1, 2]}]
    assert trace_mod.validate_script(out) is None


def test_non_wps_automation_ir_does_not_convert_to_wps():
    ir = {
        "format": "automation-ir/1",
        "id": "air-flow",
        "name": "flow",
        "source": {"kind": "flowscript"},
        "conditions": {},
        "evidence": {},
        "safety": {},
        "fallback": {},
        "variables": {},
        "steps": [{"tool": "click", "node_id": "n1", "params": {"x": 1, "y": 2}}],
    }

    try:
        automation_ir.wps_from_automation_ir(ir)
    except ValueError as exc:
        assert "WPS-sourced" in str(exc)
    else:
        raise AssertionError("FlowScript IR must not silently convert to WPS")


def test_export_automation_ir_from_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(trace_mod, "SCRIPTS_DIR", tmp_path / "scripts")
    _write_trace(tmp_path / "r1.jsonl", [
        {"type": "agent_start", "task": "calibrate"},
        {"type": "agent_tool", "tool": "click",
         "replay": {"tool": "click", "coords": [7, 8]}},
    ])

    path = trace_mod.export_automation_ir("r1")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["format"] == "automation-ir/1"
    assert data["source"]["task"] == "calibrate"
    assert data["steps"] == [{"tool": "click", "coords": [7, 8]}]
    assert automation_ir.validate_automation_ir(data) is None


def test_reject_invalid_automation_ir():
    assert "format" in automation_ir.validate_automation_ir({"format": "wps/2"})
    bad = {
        "format": "automation-ir/1",
        "id": "x",
        "name": "x",
        "source": {},
        "conditions": {},
        "evidence": {},
        "safety": {},
        "fallback": {},
        "variables": {},
        "steps": [{}],
    }
    assert "requires tool" in automation_ir.validate_automation_ir(bad)
