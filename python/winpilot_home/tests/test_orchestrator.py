import threading
import time


def test_runtime_preview_preserves_replay_evidence_structure():
    from winpilot.orchestrator.runtime import _json_preview

    payload = {
        "results": [{
            "tool": "click",
            "resolved": {"source": "ocr", "point": [296, 613]},
            "screen_change_ratio": 0.008,
        }],
        "large_text": "x" * 800,
    }

    preview = _json_preview(payload)

    assert isinstance(preview, dict)
    assert preview["results"][0]["resolved"]["point"] == [296, 613]
    assert preview["results"][0]["screen_change_ratio"] == 0.008
    assert preview["large_text"].endswith("...")


def test_interaction_snapshot_never_drops_causal_action_or_verification_fields():
    from winpilot.orchestrator.runtime import _interaction_state_preview

    large_plan = {
        "scene_summary": "x" * 4000,
        "roles": {f"actor-{index}": {"description": "y" * 300} for index in range(30)},
        "actions": [{"action": "drag", "frame_x1": 200, "frame_y1": 300}],
    }
    preview = _interaction_state_preview({
        "planned_action": large_plan,
        "detected_surface": {f"candidate-{index}": "z" * 300 for index in range(100)},
        "last_mutating_plan": {"actions": [{"action": "drag"}]},
        "last_executed_action": {"deferred": False, "transaction_id": "tx-1"},
        "last_local_verification": {"verified": True, "artifact_id": "post-frame"},
    })

    assert preview["last_mutating_plan"]["actions"][0]["action"] == "drag"
    assert preview["last_executed_action"]["transaction_id"] == "tx-1"
    assert preview["last_local_verification"]["verified"] is True


def test_stage_trace_preserves_model_capture_and_contract_budget_metrics():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind
    from winpilot.orchestrator.runtime import ArpExecution, NodeResult

    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(
                id="stage-02-infer_interaction",
                kind=ArpNodeKind.TASK_STAGE,
                config={"stage": {
                    "id": "stage-02-infer_interaction",
                    "type": "InferInteraction",
                    "name": "infer interaction",
                    "execution": {"satisfiable": True},
                }},
            ),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [
            ArpEdge(
                id="start-stage",
                source="start",
                target="stage-02-infer_interaction",
            ),
            ArpEdge(
                id="stage-end",
                source="stage-02-infer_interaction",
                target="end",
            ),
        ],
    )
    huge_state = {
        "planned_action": {
            f"large-{index}": "x" * 500
            for index in range(600)
        },
        "budget": {
            "model_calls_used": 2,
            "model_calls_limit": 8,
            "replans_used": 1,
            "replans_limit": 2,
        },
    }

    def handler(_node, _context, _stop_event):
        return NodeResult(output={
            "stage_id": "stage-02-infer_interaction",
            "stage_type": "InferInteraction",
            "status": "done",
            "model_calls": 1,
            "model_elapsed_ms": 1234,
            "controller_elapsed_ms": 17,
            "capture_elapsed_ms": 41,
            "selected_evidence_providers": {
                "interaction.protocol_inferred": "interaction_protocol_inference",
            },
            # Deliberately place the budget after a payload larger than the
            # generic history preview budget.  Dedicated runtime_metrics must
            # keep the numbers exact rather than depending on traversal order.
            "interaction_state": huge_state,
        })

    snapshot = ArpExecution(
        graph,
        {ArpNodeKind.TASK_STAGE: handler},
        run_id="trace-performance-proof",
    ).run()

    trace = snapshot["stage_trace"][0]

    assert trace["model_calls"] == 1
    assert trace["model_elapsed_ms"] == 1234
    assert trace["controller_elapsed_ms"] == 17
    assert trace["capture_elapsed_ms"] == 41
    assert trace["selected_evidence_providers"] == {
        "interaction.protocol_inferred": "interaction_protocol_inference",
    }
    assert trace["budget"] == {
        "model_calls_used": 2,
        "model_calls_limit": 8,
        "replans_used": 1,
        "replans_limit": 2,
    }
    stage_history = next(
        item for item in snapshot["history"]
        if item["kind"] == "task_stage"
    )
    assert stage_history["runtime_metrics"]["model_elapsed_ms"] == 1234
    assert stage_history["runtime_metrics"]["budget"] == trace["budget"]


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


def test_graph_validation_rejects_empty_evidence_gate_and_unknown_legacy_node():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind, validate_graph

    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(id="gate", kind=ArpNodeKind.EVIDENCE_GATE, config={"required": []}),
            ArpNode(
                id="legacy",
                kind=ArpNodeKind.LEGACY_FLOW,
                config={"node_type": "tool_call", "params": {"feature_name": "missing"}},
            ),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [
            ArpEdge(id="e1", source="start", target="gate"),
            ArpEdge(id="e2", source="gate", target="legacy", branch="pass"),
            ArpEdge(id="e3", source="legacy", target="end"),
        ],
    )

    result = validate_graph(graph)

    assert result.valid is False
    assert any("concrete evidence token" in error for error in result.errors)
    assert any("cannot execute legacy node type 'tool_call'" in error for error in result.errors)


def test_graph_validation_uses_exact_wps_contract_for_replay_steps():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind, validate_graph

    valid = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(
                id="replay",
                kind=ArpNodeKind.REPLAY_STEP,
                config={"step": {"tool": "click", "coords": [10, 20]}},
            ),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [
            ArpEdge(id="e1", source="start", target="replay"),
            ArpEdge(id="e2", source="replay", target="end"),
        ],
    )
    invalid = valid.model_copy(deep=True)
    invalid.nodes[1].config = {"step": {"tool": "click"}}

    assert validate_graph(valid).valid is True
    result = validate_graph(invalid)
    assert result.valid is False
    assert any("click requires" in error for error in result.errors)


def test_replay_steps_share_one_session_and_mix_with_native_arp(monkeypatch):
    from winpilot.orchestrator import adapters
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind, ArpPolicy
    from winpilot.orchestrator.runtime import ArpExecution

    class FakeReplayer:
        instances = []

        def __init__(self, stop_event):
            self.stop_event = stop_event
            self.vars = {}
            self.hwnd = 777
            self.step_delay = 0
            self.calls = []
            self.anchor_profiles = []
            self.__class__.instances.append(self)

        def configure_anchor_cache(self, profile):
            self.anchor_profiles.append(profile)

        def run_steps(self, steps, *, target_window=None, variables=None, prefix=""):
            self.vars.update({str(key): str(value) for key, value in (variables or {}).items()})
            step = steps[0]
            if step["tool"] == "ext.var_set":
                self.vars[str(step["name"])] = str(step["value"])
            self.calls.append((step["tool"], target_window, prefix, dict(self.vars)))
            return {
                "ok": True,
                "results": [{"index": f"{prefix}1", "tool": step["tool"], "ok": True}],
                "preflight": {"ok": True, "required": bool(target_window)},
                "variables": dict(self.vars),
            }

    monkeypatch.setattr(adapters.trace_mod, "Replayer", FakeReplayer)
    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(
                id="seed",
                kind=ArpNodeKind.REPLAY_STEP,
                config={
                    "step": {"tool": "ext.var_set", "name": "from_replay", "value": "yes"},
                    "target_window": "Target App",
                    "session": "demo",
                },
            ),
            ArpNode(
                id="native",
                kind=ArpNodeKind.SET_VARIABLE,
                config={"name": "from_arp", "value": "ready"},
            ),
            ArpNode(
                id="press",
                kind=ArpNodeKind.REPLAY_STEP,
                config={
                    "step": {"tool": "hotkey", "keys": ["enter"]},
                    "target_window": "Target App",
                    "session": "demo",
                },
            ),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [
            ArpEdge(id="e1", source="start", target="seed"),
            ArpEdge(id="e2", source="seed", target="native"),
            ArpEdge(id="e3", source="native", target="press"),
            ArpEdge(id="e4", source="press", target="end"),
        ],
        policy=ArpPolicy(require_evidence_on_done=True),
    )

    execution = ArpExecution(graph, adapters.default_handlers(), run_id="mixed-offline-replay")
    result = execution.run()

    assert result["status"] == "done"
    assert len(FakeReplayer.instances) == 1
    replayer = FakeReplayer.instances[0]
    assert [call[0] for call in replayer.calls] == ["ext.var_set", "hotkey"]
    assert [call[2] for call in replayer.calls] == ["seed.", "press."]
    assert replayer.anchor_profiles == ["arp:graph-test:demo", "arp:graph-test:demo"]
    assert replayer.calls[-1][3]["from_replay"] == "yes"
    assert replayer.calls[-1][3]["from_arp"] == "ready"
    assert [item["predicate"] for item in result["evidence"]] == ["replay.step", "replay.step"]


def test_graph_validation_rejects_unsupported_controller_action_before_run():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind, validate_graph

    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(
                id="batch",
                kind=ArpNodeKind.ACTION_BATCH,
                config={"actions": [{"action": "invent_button"}]},
            ),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [
            ArpEdge(id="e1", source="start", target="batch"),
            ArpEdge(id="e2", source="batch", target="end"),
        ],
    )

    result = validate_graph(graph)

    assert result.valid is False
    assert any("unsupported controller action" in error for error in result.errors)


def test_evidence_gate_satisfies_done_policy():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind, ArpPolicy
    from winpilot.orchestrator.runtime import ArpExecution

    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(id="gate", kind=ArpNodeKind.EVIDENCE_GATE, config={"required": ["seed"]}),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [
            ArpEdge(id="e1", source="start", target="gate"),
            ArpEdge(id="e2", source="gate", target="end", branch="pass"),
        ],
        policy=ArpPolicy(require_evidence_on_done=True),
    )

    execution = ArpExecution(graph, run_id="arp-evidence-policy")
    execution.evidence.append({"type": "seed", "verified": True})
    result = execution.run()

    assert result["status"] == "done"
    assert result["evidence"][-1]["predicate"] == "evidence.gate"


def test_typed_evidence_gate_rejects_agent_text_and_stale_records():
    import time

    from winpilot.orchestrator.contracts import EvidenceRecord, EvidenceRequirement
    from winpilot.orchestrator.evidence import evidence_matches, normalize_evidence

    claim = EvidenceRequirement(predicate="message.sent", max_age_ms=1000)
    agent_text = normalize_evidence("message sent successfully")
    stale = EvidenceRecord(
        predicate="message.sent",
        value="hello",
        source="chat_observe",
        verified=True,
        captured_at=time.time() - 5,
    )
    fresh = stale.model_copy(update={"captured_at": time.time()})

    assert evidence_matches(agent_text, claim) is False
    assert evidence_matches(stale, claim) is False
    assert evidence_matches(fresh, claim) is True


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
                config={"actions": [{"action": "wait", "duration_ms": 0}]},
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
    assert result["evidence"][0]["predicate"] == "legacy.unverified"
    assert result["evidence"][0]["verified"] is False
    assert result["evidence"][0]["value"] == "batch verified"


def test_arp_execution_routes_explicit_failure_before_fail_fast():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind, ArpPolicy
    from winpilot.orchestrator.runtime import ArpExecution, NodeResult

    def handler(node, context, stop_event):
        return NodeResult(ok=False, branch="fail", error="cached skill diverged")

    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(id="skill", kind=ArpNodeKind.SKILL_REPLAY, config={"script": "known.wps.json"}),
            ArpNode(id="recover", kind=ArpNodeKind.HUMAN_GATE, config={"approved": True}),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [
            ArpEdge(id="e1", source="start", target="skill"),
            ArpEdge(id="e2", source="skill", target="end", branch="next"),
            ArpEdge(id="e3", source="skill", target="recover", branch="fail"),
            ArpEdge(id="e4", source="recover", target="end", branch="approved"),
        ],
        policy=ArpPolicy(fail_fast=True, require_evidence_on_done=True),
    )

    result = ArpExecution(
        graph,
        {ArpNodeKind.SKILL_REPLAY: handler},
        run_id="arp-recover-failure",
    ).run()

    assert result["status"] == "done"
    assert [item["node_id"] for item in result["history"]] == ["start", "skill", "recover", "end"]
    assert result["history"][1]["ok"] is False


def test_memory_miss_routes_to_agent_exception_handler():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind, ArpPolicy
    from winpilot.orchestrator.runtime import ArpExecution, NodeResult

    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(id="skill", kind=ArpNodeKind.SKILL_REPLAY, config={"task": "send report"}),
            ArpNode(id="agent", kind=ArpNodeKind.AGENT_GOAL, config={"goal": "send report"}),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [
            ArpEdge(id="e1", source="start", target="skill"),
            ArpEdge(id="e2", source="skill", target="end", branch="next"),
            ArpEdge(id="e3", source="skill", target="agent", branch="miss"),
            ArpEdge(id="e4", source="agent", target="end", branch="next"),
        ],
        policy=ArpPolicy(require_evidence_on_done=True),
    )
    handlers = {
        ArpNodeKind.SKILL_REPLAY: lambda node, context, stop: NodeResult(ok=True, branch="miss"),
        ArpNodeKind.AGENT_GOAL: lambda node, context, stop: NodeResult(
            ok=True,
            evidence=[{"type": "agent", "verified": True}],
        ),
    }

    result = ArpExecution(graph, handlers, run_id="arp-memory-miss").run()

    assert result["status"] == "done"
    assert [item["node_id"] for item in result["history"]] == ["start", "skill", "agent", "end"]


def test_memory_backed_skill_miss_is_a_local_route_not_a_failed_run(monkeypatch):
    from winpilot.orchestrator import adapters
    from winpilot.orchestrator.contracts import ArpNode, ArpNodeKind

    monkeypatch.setattr(adapters.experience, "reusable_script_for", lambda task, memory_id: ("", None))
    result = adapters._skill_handler(
        ArpNode(id="skill", kind=ArpNodeKind.SKILL_REPLAY, config={"task": "send report"}),
        {"run_id": "memory-miss"},
        threading.Event(),
    )

    assert result.ok is True
    assert result.branch == "miss"
    assert result.output["reason"] == "no_verified_skill"


def test_memory_backed_skill_replay_emits_verified_evidence(monkeypatch, tmp_path):
    from winpilot.orchestrator import adapters
    from winpilot.orchestrator.contracts import ArpNode, ArpNodeKind

    script = tmp_path / "send-report.wps.json"
    script.write_text('{"format":"wps/1","steps":[]}', encoding="utf-8")
    attempts = []

    class Store:
        def record_attempt(self, *args, **kwargs):
            attempts.append((args, kwargs))

    monkeypatch.setattr(adapters, "SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(
        adapters.experience,
        "reusable_script_for",
        lambda task, memory_id: (script.name, {"id": "skill-memory", "task": task}),
    )
    monkeypatch.setattr(adapters.experience, "_store", lambda: Store())
    monkeypatch.setattr(
        adapters.trace_mod,
        "replay",
        lambda path, stop_event=None: {"ok": True, "results": [{"index": 1, "tool": "click", "ok": True}]},
    )

    result = adapters._skill_handler(
        ArpNode(id="skill", kind=ArpNodeKind.SKILL_REPLAY, config={"task": "send report"}),
        {"run_id": "memory-hit"},
        threading.Event(),
    )

    assert result.ok is True
    assert result.branch == "next"
    assert result.output["memory_id"] == "skill-memory"
    assert result.evidence[0]["predicate"] == "skill.replay"
    assert result.evidence[0]["metadata"]["verified_steps"] == 1
    assert [call[0][1] for call in attempts] == ["used", "replay_ok"]


def test_empty_skill_replay_cannot_satisfy_done_evidence(monkeypatch, tmp_path):
    from winpilot.orchestrator import adapters
    from winpilot.orchestrator.contracts import ArpNode, ArpNodeKind

    script = tmp_path / "empty.wps.json"
    script.write_text('{"format":"wps/1","steps":[]}', encoding="utf-8")
    monkeypatch.setattr(adapters, "SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(adapters.trace_mod, "replay", lambda path, stop_event=None: {"ok": True, "results": []})

    result = adapters._skill_handler(
        ArpNode(id="skill", kind=ArpNodeKind.SKILL_REPLAY, config={"script": script.name}),
        {"run_id": "empty-skill"},
        threading.Event(),
    )

    assert result.ok is False
    assert result.branch == "fail"
    assert result.evidence == []
    assert "no verified steps" in result.error


def test_arp_memory_feedback_uses_final_run_outcome(monkeypatch):
    from types import SimpleNamespace

    from winpilot.orchestrator import adapters

    feedback = []
    monkeypatch.setattr(
        adapters.experience,
        "feedback",
        lambda ids, success: feedback.append((tuple(ids), success)),
    )
    execution = SimpleNamespace(
        run_id="arp-memory-feedback",
        status=SimpleNamespace(value="failed"),
        outputs={
            "skill": {"memory_id": "memory-1", "memory_replay_ok": True},
            "other": {"ok": True},
        },
    )

    adapters.finalize_memory_feedback(execution)

    assert feedback == [(('memory-1',), False)]


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


def test_run_projection_freezes_vnext_identity_and_records_mismatch():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    run_id = "projection-vnext-frozen"
    store.on_event({
        "type": "trace",
        "ts": 0.5,
        "run_id": run_id,
        "status": "recording",
    })
    assert store.get(run_id)["runtime_version"] == "legacy"
    store.on_event({
        "type": "arp_run_start",
        "ts": 1.0,
        "run_id": run_id,
        "runtime_version": "vnext",
        "runtime_budget": {
            "max_turns": 30,
            "max_runtime_s": 300,
            "max_model_calls": 60,
        },
        "graph_id": "graph",
        "graph_name": "Frozen vNext run",
    })
    store.on_event({
        "type": "arp_node_start",
        "ts": 2.0,
        "run_id": run_id,
        "runtime_version": "legacy",
        "node_id": "full-goal",
    })
    store.on_event({
        "type": "arp_run_done",
        "ts": 3.0,
        "run_id": run_id,
        "runtime_snapshot": {
            "runtime_version": "legacy",
            "status": "succeeded",
        },
    })

    run = store.get(run_id)

    assert run["runtime_version"] == "vnext"
    assert run["runtime_budget"] == {
        "max_turns": 30,
        "max_runtime_s": 300,
        "max_model_calls": 60,
    }
    assert run["runtime_version_mismatch"] == {
        "frozen": "vnext",
        "observed": "legacy",
        "event_type": "arp_run_done",
    }


def test_run_projection_preserves_manual_pause_across_child_events():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    store.on_event({
        "type": "arp_run_start", "ts": 1.0, "run_id": "parent-pause",
        "graph_id": "graph", "graph_name": "Pausable workflow",
    })
    store.on_event({
        "type": "arp_child_run", "ts": 2.0, "run_id": "parent-pause",
        "child_run_id": "child-pause", "child_kind": "agent", "task": "work",
    })
    store.on_event({"type": "arp_run_paused", "ts": 3.0, "run_id": "parent-pause"})
    store.on_event({"type": "agent_tool", "ts": 4.0, "run_id": "child-pause", "tool": "click"})

    paused = store.get("parent-pause")
    assert paused["status"] == "paused"
    assert paused["phase"] == "paused"
    assert paused["manual_paused"] is True

    store.on_event({"type": "arp_run_resumed", "ts": 5.0, "run_id": "parent-pause"})
    resumed = store.get("parent-pause")
    assert resumed["status"] == "running"
    assert resumed["manual_paused"] is False


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
    assert parent["phase"] != "terminal"
    assert parent["metrics"]["action_decision_latency_avg_ms"] == 125
    assert parent["timeline"][-1]["child_run_id"] == "child"
    assert store.get("child") is None

    store.on_event({"type": "arp_run_done", "ts": 6.0, "run_id": "parent", "status": "done"})
    store.on_event({"type": "trace", "ts": 6.1, "run_id": "parent", "state": "stopped"})
    assert store.current()["status"] == "done"


def test_run_projection_folds_live_child_trace_screenshot_into_parent_once():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    store.on_event({
        "type": "arp_run_start", "ts": 1.0, "run_id": "parent-shot",
        "graph_id": "graph", "graph_name": "Screenshot workflow",
    })
    store.on_event({
        "type": "arp_child_run", "ts": 2.0, "run_id": "parent-shot",
        "child_run_id": "child-shot", "child_kind": "agent",
    })
    event = {
        "type": "trace_screenshot",
        "ts": 3.0,
        "run_id": "child-shot",
        "trace_run_id": "parent-shot",
        "screenshot": "parent-shot/batch_after_001.jpg",
        "screenshot_kind": "batch_after",
    }
    store.on_event(event)
    # Hydration sees the screenshot on both the action record and the
    # trace_screenshot follow-up. The projection must remain idempotent.
    store.on_event({**event, "type": "agent_batch_action", "ts": 3.1})

    parent = store.get("parent-shot")

    assert parent is not None
    screenshots = [item for item in parent["evidence"] if item["type"] == "screenshot"]
    assert screenshots == [{
        "type": "screenshot",
        "path": "parent-shot/batch_after_001.jpg",
        "kind": "batch_after",
        "region": None,
        "transient": False,
        "ts": 3.0,
    }]


def test_run_projection_terminal_phase_survives_late_perception_prefetch():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    run_id = "late-prefetch"
    store.on_event({
        "type": "arp_run_start", "ts": 1.0, "run_id": run_id,
        "graph_name": "Late perception",
    })
    store.on_event({
        "type": "arp_run_done", "ts": 2.0, "run_id": run_id,
        "status": "done",
    })
    store.on_event({
        "type": "perception_prefetch", "ts": 2.5, "run_id": run_id,
        "elapsed_ms": 37, "texts": ["READY"],
    })

    run = store.get(run_id)

    assert run is not None
    assert run["status"] == "done"
    assert run["phase"] == "terminal"
    assert run["finished_at"] == 2.0
    assert run["metrics"]["perception_count"] == 1
    assert run["timeline"][-1]["type"] == "perception_prefetch"
    assert run["timeline"][-1]["phase"] == "terminal"


def test_run_projection_late_event_does_not_replace_newer_current_run():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    store.on_event({"type": "agent_start", "ts": 1.0, "run_id": "old", "task": "Old"})
    store.on_event({"type": "agent_done", "ts": 2.0, "run_id": "old", "status": "done"})
    store.on_event({"type": "agent_start", "ts": 3.0, "run_id": "new", "task": "New"})
    store.on_event({
        "type": "perception_prefetch", "ts": 3.5, "run_id": "old",
        "elapsed_ms": 10,
    })

    current = store.current()

    assert current is not None
    assert current["run_id"] == "new"
    assert store.get("old")["phase"] == "terminal"


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
    assert run["metrics"]["screen_delta_latency_avg_ms"] == 3
    assert run["metrics"]["semantic_verify_latency_avg_ms"] == 0
    assert run["evidence"][-1] == {
        "type": "screenshot",
        "path": "press/evidence_001_transient_press_roi.jpg",
        "kind": "transient_press_roi",
        "region": {"left": 36, "top": 609, "width": 573, "height": 196},
        "transient": True,
        "ts": 2.0,
    }


def test_run_projection_tracks_native_visual_feedback_frames_and_latency():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    store.on_event({
        "type": "agent_start", "ts": 1.0, "run_id": "native-vision",
        "task": "Operate from real frames",
    })
    for index, latency_ms in enumerate((5, 10, 25), start=1):
        store.on_event({
            "type": "visual_feedback",
            "ts": 1.0 + index,
            "run_id": "native-vision",
            "frame_id": index,
            "phase": "post_action",
            "capture_latency_ms": latency_ms,
        })

    run = store.get("native-vision")

    assert run is not None
    assert run["metrics"]["visual_feedback_frame_count"] == 3
    assert run["metrics"]["visual_feedback_capture_latency_latest_ms"] == 25
    assert run["metrics"]["visual_feedback_capture_latency_avg_ms"] == 13.3
    assert run["metrics"]["visual_feedback_capture_latency_p95_ms"] == 25
    assert run["metrics"]["screen_capture_latency_avg_ms"] == 13.3


def test_run_projection_visual_feedback_latency_is_fail_safe():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    store.on_event({"type": "agent_start", "ts": 1.0, "run_id": "safe-frame"})
    for index, latency_ms in enumerate((None, "unknown", -4, float("nan"), 8), start=1):
        event = {
            "type": "visual_feedback",
            "ts": 1.0 + index,
            "run_id": "safe-frame",
            "frame_id": index,
        }
        if latency_ms is not None:
            event["capture_latency_ms"] = latency_ms
        store.on_event(event)

    run = store.get("safe-frame")

    assert run is not None
    assert run["metrics"]["visual_feedback_frame_count"] == 5
    assert run["metrics"]["visual_feedback_capture_latency_latest_ms"] == 8
    assert run["metrics"]["visual_feedback_capture_latency_avg_ms"] == 8
    assert run["metrics"]["visual_feedback_capture_latency_p95_ms"] == 8
    assert run["metrics"]["screen_capture_latency_avg_ms"] == 8


def test_run_projection_tracks_semantic_verify_method_and_true_latency():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    store.on_event({
        "type": "agent_start", "ts": 1.0, "run_id": "verify-fast",
        "task": "Verify visible result",
    })
    store.on_event({
        "type": "screen_delta", "ts": 2.0, "run_id": "verify-fast",
        "changed": True, "capture_latency_ms": 22, "diff_latency_ms": 7,
    })
    store.on_event({
        "type": "verify", "ts": 2.1, "run_id": "verify-fast",
        "action": "click", "verified": True, "method": "uia-query",
        "elapsed_ms": 207,
    })

    run = store.get("verify-fast")

    assert run is not None
    assert run["metrics"]["post_action_verify_latency_avg_ms"] == 207
    assert run["metrics"]["semantic_verify_latency_avg_ms"] == 207
    assert run["metrics"]["screen_delta_latency_avg_ms"] == 7
    assert run["metrics"]["semantic_verify_count"] == 1
    assert run["metrics"]["semantic_verify_passed_count"] == 1
    assert run["metrics"]["semantic_verify_fast_path_rate"] == 1.0
    assert run["metrics"]["semantic_verify_ocr_rate"] == 0.0


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
        "result_meta": {
            "visual_servo": {
                "enabled": True,
                "confirmed": True,
                "correction_count": 2,
                "elapsed_ms": 18,
            },
        },
    })

    run = store.get("batch")

    assert run is not None
    assert run["metrics"]["tool_count"] == 1
    assert run["metrics"]["batch_action_count"] == 1
    assert run["metrics"]["batch_action_ratio"] == 1.0
    assert run["metrics"]["action_execution_latency_avg_ms"] == 28
    assert run["metrics"]["visual_servo_count"] == 1
    assert run["metrics"]["visual_servo_correction_count"] == 2
    assert run["metrics"]["visual_servo_failure_count"] == 0
    assert run["metrics"]["visual_servo_latency_avg_ms"] == 18
    assert run["metrics"]["visual_servo_latency_max_ms"] == 18


def test_run_projection_aggregates_replay_anchor_cache_metrics():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    store.on_event({
        "type": "arp_run_start", "ts": 1.0, "run_id": "anchors",
        "graph_name": "Cached replay",
    })
    store.on_event({
        "type": "replay_anchor_cache", "ts": 2.0, "run_id": "anchors",
        "lookup_count": 3, "hit_count": 2, "miss_count": 1,
        "fallback_count": 1, "learned_count": 1,
        "lookup_latency_avg_ms": 12.0, "lookup_latency_max_ms": 18,
    })

    run = store.get("anchors")

    assert run is not None
    assert run["metrics"]["anchor_cache_hit_rate"] == 0.6667
    assert run["metrics"]["anchor_cache_lookup_latency_avg_ms"] == 12.0
    assert run["metrics"]["anchor_cache_lookup_latency_max_ms"] == 18
    assert run["metrics"]["anchor_cache_fallback_count"] == 1
    assert run["metrics"]["anchor_cache_learned_count"] == 1


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


def test_run_projection_rebuilds_historical_arp_audit_and_summarizes_lists():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    run_id = "persisted-arp-audit"
    store.on_event({
        "type": "arp_run_start",
        "ts": 1.0,
        "run_id": run_id,
        "graph_id": "task-contract-id",
        "graph_name": "Cross app artifact task",
        "task_contract": {"format": "task-contract/1", "stages": []},
        "satisfiability": {"satisfiable": True, "missing_capabilities": []},
    })
    store.on_event({
        "type": "arp_node_done",
        "ts": 2.0,
        "run_id": run_id,
        "node_id": "stage-01-capture",
        "kind": "task_stage",
        "ok": True,
        "attempts": 1,
        "output": {
            "stage_id": "stage-01-capture",
            "stage_type": "CaptureArtifact",
            "status": "done",
            "tools": ["capture_artifact"],
            "controller_tools": ["capture_artifact"],
            "required_capabilities": ["artifact.capture"],
            "verifier_requirements": ["artifact.hash"],
            "exposed_capabilities": ["artifact.capture", "artifact.hash"],
            "missing_capabilities": [],
            "satisfiable": True,
            "artifact_id": "art-persisted",
        },
        "runtime_metrics": {"controller_elapsed_ms": 21, "model_calls": 0},
    })
    store.on_event({
        "type": "arp_evidence",
        "ts": 3.0,
        "run_id": run_id,
        "node_id": "end",
        "passed": True,
        "evidence": {
            "id": "evidence-complete",
            "predicate": "contract.complete",
            "source": "completion_contract_verifier",
            "verified": True,
            "captured_at": 3.0,
            "run_id": run_id,
            "node_id": "end",
            "metadata": {
                "ok": True,
                "run_id": run_id,
                "contract_id": "task-contract-id",
                "obligations": [],
                "final_conditions": [],
                "missing": [],
                "contract_errors": [],
                "artifact_refs": {},
                "primary_artifact_id": "art-persisted",
                "reason": "all conditions satisfied",
            },
        },
    })
    store.on_event({
        "type": "arp_run_done", "ts": 4.0, "run_id": run_id, "status": "done",
    })

    audit = store.arp_snapshot(run_id)
    detail = store.get(run_id)
    summary = store.list(1)[0]

    assert audit is not None
    assert audit["audit_source"] == "trace_projection"
    assert audit["task_contract"]["format"] == "task-contract/1"
    assert audit["stage_trace"][0]["stage_type"] == "CaptureArtifact"
    assert audit["stage_trace"][0]["controller_elapsed_ms"] == 21
    assert audit["contract_evidence"]["records"][0]["provider"] == "completion_contract_verifier"
    assert audit["completion_contract"]["ok"] is True
    assert detail is not None and detail["timeline"]
    assert summary["timeline"] == []
    assert summary["evidence"] == []
    assert summary["result"] is None


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


def test_run_projection_marks_incomplete_hydrated_run_interrupted(tmp_path):
    import json

    from winpilot.orchestrator.projection import RunProjectionStore

    path = tmp_path / "crashed.jsonl"
    path.write_text(
        "\n".join([
            json.dumps({
                "type": "agent_start", "ts": 1.0,
                "run_id": "crashed", "task": "Blocked perception",
            }),
            json.dumps({
                "type": "agent_tool_start", "ts": 2.0,
                "run_id": "crashed", "tool": "observe",
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    store = RunProjectionStore()

    assert store.hydrate_directory(
        tmp_path,
        mark_incomplete_interrupted=True,
    ) == 1
    run = store.get("crashed")

    assert run is not None
    assert run["status"] == "interrupted"
    assert run["phase"] == "terminal"
    assert run["current_tool"] is None
    assert run["timeline"][-1]["type"] == "run_interrupted"


def test_background_reminder_finishes_without_hijacking_current_run():
    from winpilot.orchestrator.projection import RunProjectionStore

    store = RunProjectionStore()
    store.on_event({
        "type": "arp_run_start", "ts": 1.0, "run_id": "parent",
        "graph_name": "Schedule a reminder",
    })
    store.on_event({
        "type": "schedule", "ts": 2.0, "run_id": "reminder",
        "action": "queued", "schedule_id": "schedule-1",
    })
    store.on_event({
        "type": "user_notification", "ts": 3.0, "run_id": "reminder",
        "title": "Lunch", "body": "Eat now",
    })

    assert store.current()["run_id"] == "parent"

    store.on_event({
        "type": "schedule", "ts": 4.0, "run_id": "reminder",
        "action": "finished", "status": "done", "result": {"ok": True},
    })
    reminder = store.get("reminder")

    assert reminder["status"] == "done"
    assert reminder["phase"] == "terminal"
    assert reminder["finished_at"] == 4.0
    assert store.current()["run_id"] == "parent"


def test_handler_node_timeout_cancels_local_work_without_waiting_for_completion():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind
    from winpilot.orchestrator.runtime import ArpExecution, ArpRunStatus, NodeResult

    stopped = threading.Event()

    def slow_handler(_node, _context, stop_event):
        while not stop_event.wait(0.01):
            pass
        stopped.set()
        return NodeResult(ok=False, branch="canceled", error="stopped")

    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(
                id="slow",
                kind=ArpNodeKind.AGENT_GOAL,
                config={"goal": "slow operation"},
                timeout_s=0.05,
            ),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [
            ArpEdge(id="a", source="start", target="slow"),
            ArpEdge(id="b", source="slow", target="end"),
        ],
    )
    started = time.monotonic()

    execution = ArpExecution(graph, {ArpNodeKind.AGENT_GOAL: slow_handler})
    result = execution.run()

    assert time.monotonic() - started < 0.5
    assert stopped.wait(0.5)
    assert execution.status == ArpRunStatus.FAILED
    assert "timed out" in result["error"]


def test_manual_pause_blocks_the_next_node_until_resume():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind
    from winpilot.orchestrator.runtime import ArpExecution, ArpRunStatus, NodeResult

    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    def handler(node, _context, stop_event):
        if node.id == "first":
            first_started.set()
            while not release_first.wait(0.01):
                if stop_event.is_set():
                    return NodeResult(ok=False, branch="canceled", error="canceled")
        else:
            second_started.set()
        return NodeResult(ok=True)

    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(
                id="first",
                kind=ArpNodeKind.ACTION_BATCH,
                config={"actions": [{"action": "wait", "duration_ms": 0}]},
            ),
            ArpNode(
                id="second",
                kind=ArpNodeKind.ACTION_BATCH,
                config={"actions": [{"action": "wait", "duration_ms": 0}]},
            ),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [
            ArpEdge(id="a", source="start", target="first"),
            ArpEdge(id="b", source="first", target="second"),
            ArpEdge(id="c", source="second", target="end"),
        ],
    )
    execution = ArpExecution(graph, {ArpNodeKind.ACTION_BATCH: handler})
    thread = threading.Thread(target=execution.run)
    thread.start()

    assert first_started.wait(1), execution.to_dict()
    assert execution.pause() is True
    assert execution.status == ArpRunStatus.PAUSED
    assert execution.to_dict()["manual_paused"] is True
    release_first.set()
    assert not second_started.wait(0.1)

    assert execution.resume() is True
    assert second_started.wait(1)
    thread.join(1)
    assert not thread.is_alive()
    assert execution.status == ArpRunStatus.DONE


def test_vnext_run_prepared_returns_and_publishes_post_finish_snapshot():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind
    from winpilot.orchestrator.runtime import ArpExecution, ArpRunManager
    from winpilot.utils.log import BUS

    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [ArpEdge(id="a", source="start", target="end")],
    )
    execution = ArpExecution(graph, run_id="vnext-finalized-snapshot")
    execution.runtime_state.update({
        "runtime_version": "vnext",
        "no_legacy_fallback": True,
        "trace_status": "recording",
    })
    manager = ArpRunManager()
    events = BUS.subscribe()

    def finish(run):
        run.runtime_state["trace_status"] = "stopped"
        run.runtime_state["runtime_lifecycle"] = {
            "phase": "finalized",
            "terminal": True,
            "status": "done",
            "trace_status": "stopped",
            "no_legacy_fallback": True,
        }

    try:
        snapshot = manager.run_prepared(execution, on_finish=finish)
        observed = []
        while not events.empty():
            observed.append(events.get_nowait())
    finally:
        BUS.unsubscribe(events)

    done_events = [
        event for event in observed
        if event.get("type") == "arp_run_done"
        and event.get("run_id") == execution.run_id
    ]
    assert snapshot["status"] == "done"
    assert snapshot["trace_status"] == "stopped"
    assert snapshot["runtime_lifecycle"]["terminal"] is True
    assert len(done_events) == 1
    assert done_events[0]["status"] == "done"
    assert done_events[0]["runtime_snapshot"]["trace_status"] == "stopped"
    assert (
        done_events[0]["runtime_snapshot"]["runtime_lifecycle"]["terminal"]
        is True
    )


def test_run_manager_prunes_old_terminal_runs():
    from winpilot.orchestrator.contracts import ArpEdge, ArpNode, ArpNodeKind
    from winpilot.orchestrator.runtime import ArpExecution, ArpRunManager, ArpRunStatus

    graph = _graph(
        [
            ArpNode(id="start", kind=ArpNodeKind.START),
            ArpNode(id="end", kind=ArpNodeKind.END),
        ],
        [ArpEdge(id="a", source="start", target="end")],
    )
    manager = ArpRunManager(max_runs=20)
    for index in range(25):
        run = ArpExecution(graph)
        run.status = ArpRunStatus.DONE
        run.created_at = float(index)
        run.finished_at = float(index)
        manager._runs[run.run_id] = run
        manager._threads[run.run_id] = threading.Thread()

    with manager._lock:
        manager._prune_locked()

    assert len(manager._runs) == 20
    assert set(manager._threads) == set(manager._runs)
