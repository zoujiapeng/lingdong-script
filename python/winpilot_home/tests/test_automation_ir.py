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
    assert "safety" not in ir
    assert "fallback" not in ir
    assert ir["runtime"] == {"kind": "wps_replay"}
    assert automation_ir.validate_automation_runtime(ir) is None


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


def test_wps_automation_runtime_rejects_empty_execution_projection():
    ir = automation_ir.automation_ir_from_wps({
        "format": "wps/2",
        "name": "empty",
        "steps": [],
    })

    assert automation_ir.validate_automation_ir(ir) is None
    assert "executable leaf" in automation_ir.validate_automation_runtime(ir)


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


def test_automation_ir_validates_and_returns_canonical_arp_runtime_graph():
    ir = {
        "format": "automation-ir/1",
        "id": "air-arp",
        "name": "ARP automation",
        "source": {"kind": "flowscript"},
        "conditions": {},
        "evidence": {"required": []},
        "steps": [
            {"tool": "start", "node_id": "start", "params": {}},
            {"tool": "end", "node_id": "end", "params": {}},
        ],
        "variables": {},
        "safety": {"requires_preflight": False},
        "fallback": {"on_step_fail": "stop"},
        "runtime": {
            "kind": "arp_graph",
            "graph": {
                "format": "arp/1",
                "id": "graph-1",
                "name": "Graph",
                "nodes": [
                    {"id": "start", "kind": "start"},
                    {"id": "end", "kind": "end"},
                ],
                "edges": [
                    {
                        "id": "edge-1",
                        "source": "start",
                        "target": "end",
                        "branch": "next",
                    }
                ],
                "policy": {"require_evidence_on_done": False},
            },
        },
    }
    assert automation_ir.validate_automation_runtime(ir) is None
    graph = automation_ir.arp_graph_from_automation_ir(ir)
    assert graph.id == "graph-1"
    assert graph.nodes[0].id == "start"

    ir["runtime"]["graph"]["edges"][0]["target"] = "missing"
    assert "missing target" in automation_ir.validate_automation_runtime(ir)


def test_automation_ir_runtime_rejects_missing_flow_graph():
    ir = {
        "format": "automation-ir/1",
        "id": "air-old-flow",
        "name": "Old flow",
        "source": {"kind": "flowscript"},
        "conditions": {},
        "evidence": {},
        "steps": [{"tool": "start", "node_id": "start", "params": {}}],
        "variables": {},
        "safety": {},
        "fallback": {},
    }

    assert automation_ir.validate_automation_ir(ir) is None
    assert "runtime.kind" in automation_ir.validate_automation_runtime(ir)


def test_wps_runtime_ignores_removed_safety_settings():
    ir = automation_ir.automation_ir_from_wps({
        "format": "wps/2",
        "steps": [{"tool": "ext.delay", "seconds": 0}],
    })

    ir["safety"] = {"requires_preflight": True, "uses_shell": False}
    ir["fallback"] = {"on_preflight_fail": "stop"}
    assert automation_ir.validate_automation_runtime(ir) is None
    assert "safety" not in automation_ir.wps_from_automation_ir(ir)
    assert "fallback" not in automation_ir.wps_from_automation_ir(ir)


def test_flow_projection_rejects_dangling_editor_edge():
    ir = {
        "format": "automation-ir/1",
        "id": "air-edge",
        "name": "bad edge",
        "created": 1,
        "source": {"kind": "flowscript"},
        "conditions": {},
        "evidence": {},
        "safety": {},
        "fallback": {},
        "variables": {},
        "steps": [{
            "tool": "start",
            "node_id": "start",
            "params": {},
            "outgoing": [{"to_node": "missing"}],
        }],
    }

    assert "unknown to_node" in automation_ir.validate_automation_ir(ir)
