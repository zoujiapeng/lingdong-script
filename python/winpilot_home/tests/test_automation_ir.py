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
    assert ir["fallback"] == {
        "on_preflight_fail": "stop",
        "on_step_fail": "stop",
    }
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
    ir["runtime"]["projection_fingerprint"] = (
        automation_ir.automation_projection_fingerprint(
            ir["steps"],
            ir["variables"],
            ir["runtime"]["graph"],
        )
    )

    assert automation_ir.validate_automation_runtime(ir) is None
    graph = automation_ir.arp_graph_from_automation_ir(ir)
    assert graph.id == "graph-1"
    assert graph.nodes[0].id == "start"

    ir["steps"][0]["tool"] = "changed-after-compile"
    assert "projection is stale" in automation_ir.validate_automation_runtime(ir)


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


def test_projection_fingerprint_matches_cross_runtime_contract():
    assert automation_ir.automation_projection_fingerprint(
        [{"tool": "start", "node_id": "s", "params": {"x": 1, "text": "你好"}}],
        {"token": "x"},
    ) == "sha256:f6996f5a13fe5cbb9a6c41e57a3ee5feb366e844d6055b54737d6f28fafcb302"


def test_wps_runtime_safety_cannot_understate_detached_shell_execution():
    ir = automation_ir.automation_ir_from_wps({
        "format": "wps/2",
        "steps": [{"tool": "run_detached", "script": "echo ready"}],
    })

    assert ir["safety"]["uses_shell"] is True
    assert ir["safety"]["shell_requires_explicit_runtime"] is True
    ir["safety"]["uses_shell"] = False
    assert "safety.uses_shell" in automation_ir.validate_automation_runtime(ir)


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
