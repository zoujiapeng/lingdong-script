import threading
import time


def _graph(nodes, edges, **extra):
    from winpilot.orchestrator.contracts import ArpGraph, ArpPolicy

    return ArpGraph(
        id=extra.pop("id", "graph-test"),
        name=extra.pop("name", "Test graph"),
        nodes=nodes,
        edges=edges,
        policy=extra.pop("policy", ArpPolicy(require_evidence_on_done=False)),
        **extra,
    )


def test_graph_validation_rejects_missing_route_target():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind, validate_graph

    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [ArpEdge(id="bad", source="start", target="missing")],
    )

    result = validate_graph(graph)

    assert result.valid is False
    assert any("missing target" in error for error in result.errors)


def test_graph_validation_enforces_evidence_policy_shape():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind, ArpPolicy, validate_graph

    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [ArpEdge(id="e1", source="start", target="end")],
        policy=ArpPolicy(require_evidence_on_done=True),
    )

    result = validate_graph(graph)

    assert result.valid is False
    assert any("evidence-capable" in error for error in result.errors)


def test_evidence_gate_satisfies_done_policy():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind, ArpPolicy
    from winpilot.orchestrator.runtime import ArpExecution

    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(id="gate", kind=ArpNodeKind.EVIDENCE_GATE, config={"required": []}),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [
            ArpEdge(id="e1", source="start", target="gate"),
            ArpEdge(id="e2", source="gate", target="end", branch="pass"),
        ],
        policy=ArpPolicy(require_evidence_on_done=True),
    )

    execution = ArpExecution(graph, run_id="arp-evidence-policy")
    result = execution.run()

    assert result["status"] == "done"
    assert result["evidence"][-1]["type"] == "evidence_gate"


def test_arp_execution_routes_condition_and_updates_variables():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind
    from winpilot.orchestrator.runtime import ArpExecution

    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(id="set", kind=ArpNodeKind.SET_VARIABLE, config={"name": "ready", "value": True}),
            ArpNode(id="condition", kind=ArpNodeKind.CONDITION, config={"source": "variables.ready"}),
            ArpNode(id="yes", kind=ArpNodeKind.END),
            ArpNode(id="no", kind=ArpNodeKind.END),
        ],
        [
            ArpEdge(id="e1", source="start", target="set"),
            ArpEdge(id="e2", source="set", target="condition"),
            ArpEdge(id="e3", source="condition", target="yes", branch="yes"),
            ArpEdge(id="e4", source="condition", target="no", branch="no"),
        ],
    )

    execution = ArpExecution(graph, run_id="arp-condition")
    result = execution.run()

    assert result["status"] == "done"
    assert result["variables"]["ready"] is True
    assert [item["node_id"] for item in result["history"]] == ["start", "set", "condition", "yes"]


def test_arp_execution_retries_registered_handler():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind, ArpRetryPolicy
    from winpilot.orchestrator.runtime import ArpExecution, NodeResult

    calls = []

    def handler(node, context, stop_event):
        calls.append(node.id)
        if len(calls) == 1:
            return NodeResult(ok=False, error="temporary")
        return NodeResult(ok=True, output={"verified": True}, evidence=["batch verified"])

    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(
                id="batch",
                kind=ArpNodeKind.ACTION_BATCH,
                retry=ArpRetryPolicy(max_attempts=2),
            ),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [
            ArpEdge(id="e1", source="start", target="batch"),
            ArpEdge(id="e2", source="batch", target="end"),
        ],
    )

    execution = ArpExecution(graph, {ArpNodeKind.ACTION_BATCH: handler}, run_id="arp-retry")
    result = execution.run()

    assert result["status"] == "done"
    assert calls == ["batch", "batch"]
    assert result["history"][1]["attempts"] == 2
    assert "batch verified" in result["evidence"]


def test_arp_human_gate_can_resume_from_manager():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind
    from winpilot.orchestrator.runtime import ArpRunManager

    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(id="gate", kind=ArpNodeKind.HUMAN_GATE, timeout_s=2),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [
            ArpEdge(id="e1", source="start", target="gate"),
            ArpEdge(id="e2", source="gate", target="end", branch="approved"),
        ],
    )
    manager = ArpRunManager()
    execution = manager.start(graph)
    deadline = time.time() + 2
    while execution.status.value != "paused" and time.time() < deadline:
        time.sleep(0.01)

    assert execution.status.value == "paused"
    assert manager.decide_gate(execution.run_id, "gate", True, "approved in test") is True
    deadline = time.time() + 2
    while execution.status.value not in {"done", "failed"} and time.time() < deadline:
        time.sleep(0.01)

    assert execution.status.value == "done"


def test_run_projection_unifies_agent_metrics_and_done_evidence():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    run_id = "projection-test"
    store.on_event({"type": "agent_start", "ts": 1.0, "run_id": run_id, "task": "Open target"})
    store.on_event({"type": "agent_step", "ts": 2.0, "run_id": run_id})
    store.on_event({"type": "agent_tool", "ts": 3.0, "run_id": run_id, "tool": "click", "elapsed_ms": 40})
    store.on_event({
        "type": "perception",
        "ts": 4.0,
        "run_id": run_id,
        "dims": {"ocr": {"ms": 20, "count": 2}, "vlm": {"ms": 0, "count": 0}},
    })
    store.on_event({"type": "done_check", "ts": 5.0, "run_id": run_id, "passed": True, "reason": "target visible"})
    store.on_event({"type": "agent_done", "ts": 6.0, "run_id": run_id, "status": "done", "result": "ok"})

    run = store.get(run_id)

    assert run is not None
    assert run["status"] == "done"
    assert run["phase"] == "terminal"
    assert run["metrics"]["decision_count"] == 1
    assert run["metrics"]["tool_count"] == 1
    assert run["metrics"]["tool_latency_avg_ms"] == 40
    assert run["metrics"]["perception_latency_avg_ms"] == 20
    assert run["metrics"]["done_check_passed"] == 1
    assert run["evidence"][0]["reason"] == "target visible"


def test_run_projection_folds_agent_child_into_parent_arp_run():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    store.on_event({
        "type": "arp_run_start", "ts": 1.0, "run_id": "parent",
        "graph_id": "graph", "graph_name": "Parent workflow",
    })
    store.on_event({
        "type": "arp_child_run", "ts": 2.0, "run_id": "parent",
        "child_run_id": "child", "child_kind": "agent", "task": "Do the GUI work",
    })
    store.on_event({"type": "agent_start", "ts": 3.0, "run_id": "child", "task": "Do the GUI work"})
    store.on_event({"type": "agent_decision", "ts": 4.0, "run_id": "child", "elapsed_ms": 125})
    store.on_event({"type": "agent_done", "ts": 5.0, "run_id": "child", "status": "done"})

    parent = store.current()

    assert parent is not None
    assert parent["run_id"] == "parent"
    assert parent["kind"] == "arp"
    assert parent["goal"] == "Parent workflow"
    assert parent["status"] == "running"
    assert parent["metrics"]["action_decision_latency_avg_ms"] == 125
    assert parent["timeline"][-1]["child_run_id"] == "child"
    assert store.get("child") is None

    store.on_event({"type": "arp_run_done", "ts": 6.0, "run_id": "parent", "status": "done"})
    store.on_event({"type": "trace", "ts": 6.1, "run_id": "parent", "state": "stopped"})
    assert store.current()["status"] == "done"


def test_run_projection_preserves_transient_roi_evidence():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    store.on_event({"type": "agent_start", "ts": 1.0, "run_id": "press", "task": "Press target"})
    store.on_event({
        "type": "screen_delta",
        "ts": 2.0,
        "run_id": "press",
        "changed": True,
        "transient_observed": True,
        "delta_source": "press",
        "screenshot": "press/evidence_001_transient_press_roi.jpg",
        "screenshot_kind": "transient_press_roi",
        "screenshot_region": {"left": 36, "top": 609, "width": 573, "height": 196},
        "capture_latency_ms": 19,
        "diff_latency_ms": 3,
    })

    run = store.get("press")

    assert run is not None
    assert run["metrics"]["screen_capture_latency_avg_ms"] == 19
    assert run["metrics"]["post_action_verify_latency_avg_ms"] == 3
    assert run["evidence"][-1] == {
        "type": "screenshot",
        "path": "press/evidence_001_transient_press_roi.jpg",
        "kind": "transient_press_roi",
        "region": {"left": 36, "top": 609, "width": 573, "height": 196},
        "transient": True,
        "ts": 2.0,
    }


def test_run_projection_records_predictive_transient_ocr():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    store.on_event({"type": "agent_start", "ts": 1.0, "run_id": "prefetch", "task": "Read press"})
    store.on_event({
        "type": "perception_prefetch",
        "ts": 2.0,
        "run_id": "prefetch",
        "elapsed_ms": 44,
        "transient": True,
        "texts": ["PRESS", "PRESSPULSEACTLVE1"],
    })

    run = store.get("prefetch")

    assert run is not None
    assert run["metrics"]["perception_count"] == 1
    assert run["metrics"]["perception_latency_avg_ms"] == 44
    assert run["evidence"][-1]["type"] == "transient_ocr"
    assert "PRESSPULSEACTLVE1" in run["evidence"][-1]["reason"]


def test_run_projection_counts_direct_controller_batch_actions():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    store.on_event({"type": "arp_run_start", "ts": 1.0, "run_id": "batch", "graph_name": "Batch"})
    store.on_event({
        "type": "agent_batch_action",
        "ts": 2.0,
        "run_id": "batch",
        "tool": "click_element",
        "elapsed_ms": 28,
        "ok": True,
    })

    run = store.get("batch")

    assert run is not None
    assert run["metrics"]["tool_count"] == 1
    assert run["metrics"]["batch_action_count"] == 1
    assert run["metrics"]["batch_action_ratio"] == 1.0
    assert run["metrics"]["action_execution_latency_avg_ms"] == 28


def test_run_projection_counts_arp_evidence_as_done_check():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    store.on_event({"type": "arp_run_start", "ts": 1.0, "run_id": "evidence", "graph_name": "Evidence"})
    store.on_event({
        "type": "arp_evidence",
        "ts": 2.0,
        "run_id": "evidence",
        "node_id": "batch",
        "passed": True,
        "evidence": {"type": "controller", "verified": True},
    })

    run = store.get("evidence")

    assert run is not None
    assert run["phase"] == "verifying"
    assert run["metrics"]["done_checks"] == 1
    assert run["metrics"]["done_check_passed"] == 1
    assert run["evidence"][-1]["type"] == "controller"


def test_run_projection_hydrates_persisted_trace_once(tmp_path):
    import json

    from winpilot.orchestrator.projection import RunProjectionStore

    events = [
        {"type": "agent_start", "ts": 1.0, "run_id": "persisted", "task": "Saved run"},
        {"type": "done_check", "ts": 2.0, "run_id": "persisted", "passed": True, "reason": "visible"},
        {"type": "agent_done", "ts": 3.0, "run_id": "persisted", "status": "done", "result": "ok"},
    ]
    path = tmp_path / "persisted.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    store = RunProjectionStore()

    assert store.hydrate_directory(tmp_path) == 1
    assert store.hydrate_directory(tmp_path) == 0
    run = store.get("persisted")

    assert run is not None
    assert run["status"] == "done"
    assert run["goal"] == "Saved run"
    assert run["metrics"]["done_check_passed"] == 1
    assert len(run["timeline"]) == 3
