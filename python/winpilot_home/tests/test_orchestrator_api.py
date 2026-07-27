import threading
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def client(monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from winpilot import server

    with server.STATE.lock:
        server.STATE.runner = None
        server.STATE.runners.clear()
        server.STATE.run_jobs.clear()
    server.RUNTIME_JOBS.clear_finished()
    monkeypatch.setattr(server.trace_mod.RECORDER, "start", lambda run_id: None)
    monkeypatch.setattr(server.trace_mod.RECORDER, "stop", lambda: None)
    return TestClient(server.app)


def _graph_payload():
    return {
        "format": "arp/1",
        "id": "api-graph",
        "name": "API graph",
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "set", "kind": "set_variable", "config": {"name": "ready", "value": True}},
            {"id": "gate", "kind": "human_gate", "config": {"approved": True}},
            {"id": "end", "kind": "end"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "set"},
            {"id": "e2", "source": "set", "target": "gate"},
            {"id": "e3", "source": "gate", "target": "end", "branch": "approved"},
        ],
    }


def test_platform_overview_exposes_contracts_and_capabilities(client):
    response = client.get("/api/v2/platform")

    assert response.status_code == 200
    data = response.json()
    assert data["api_version"] == "2"
    assert data["contracts"]["arp"] == "arp/1"
    capability_ids = {item["id"] for item in data["capabilities"]}
    assert {"agent.goal", "controller.batch", "replay.step", "skill.replay", "evidence.gate", "task.stage", "control.variable"} <= capability_ids
    advertised_nodes = {
        node_type
        for item in data["capabilities"]
        for node_type in item.get("flow_node_types", [])
    }
    assert {"agent_goal", "action_batch", "skill_replay", "variable_set", "human_gate"} <= advertised_nodes


def test_platform_overview_exposes_fail_closed_vnext_canary_budget(
    client,
    monkeypatch,
):
    from winpilot import server

    original_get = server.CONFIG.get

    def frozen_get(*path, default=None):
        if path == ("vnext",):
            return {
                "live_canary_enabled": False,
                "max_turns": 30,
                "max_runtime_s": 300,
                "max_model_calls": 60,
            }
        return original_get(*path, default=default)

    monkeypatch.setattr(server.CONFIG, "get", frozen_get)

    status = client.get("/api/v2/platform").json()["status"]["vnext"]

    assert status == {
        "live_canary_enabled": False,
        "budget": {
            "max_turns": 30,
            "max_runtime_s": 300,
            "max_model_calls": 60,
        },
    }


@pytest.mark.parametrize("runtime_version", ["future", "", 7, None])
def test_arp_run_request_rejects_unknown_runtime_version(
    client,
    runtime_version,
):
    response = client.post(
        "/api/v2/arp/runs",
        json={
            "graph": _graph_payload(),
            "inputs": {},
            "runtime_version": runtime_version,
        },
    )

    assert response.status_code == 422


def test_arp_run_request_rejects_unknown_top_level_fields(client):
    response = client.post(
        "/api/v2/arp/runs",
        json={
            "graph": _graph_payload(),
            "inputs": {},
            "runtime_version": "legacy",
            "fallback": "vnext",
        },
    )

    assert response.status_code == 422
    assert any(
        item["loc"][-1] == "fallback"
        and item["type"] == "extra_forbidden"
        for item in response.json()["detail"]
    )


def test_vnext_selection_fails_closed_when_canary_is_disabled(
    client,
    monkeypatch,
):
    from winpilot import server

    monkeypatch.setattr(
        server.CONFIG,
        "snapshot",
        lambda: {
            "vnext": {
                "live_canary_enabled": False,
                "max_turns": 30,
                "max_runtime_s": 300,
                "max_model_calls": 60,
            },
        },
    )
    monkeypatch.setattr(
        server.TaskContractCompiler,
        "compile_graph",
        lambda *_args, **_kwargs: pytest.fail(
            "vNext must not invoke the legacy compiler"
        ),
    )
    monkeypatch.setattr(
        server,
        "_start_arp_execution",
        lambda *_args, **_kwargs: pytest.fail(
            "disabled vNext must not start either runtime"
        ),
    )

    response = client.post(
        "/api/v2/arp/runs",
        json={
            "graph": _graph_payload(),
            "inputs": {},
            "runtime_version": "vnext",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "vNext live canary is disabled; no legacy fallback was run"
    )


def test_vnext_selection_is_frozen_once_without_legacy_compilation(
    client,
    monkeypatch,
):
    from winpilot import server
    from winpilot.orchestrator.contracts import ArpRuntimeVersion

    frozen = {
        "api": {"model": "test-vision-model"},
        "vnext": {
            "live_canary_enabled": True,
            "max_turns": 30,
            "max_runtime_s": 300,
            "max_model_calls": 60,
        },
    }
    calls = []
    execution = SimpleNamespace(
        run_id="arp-vnext-frozen",
        to_dict=lambda: {
            "run_id": "arp-vnext-frozen",
            "runtime_version": "vnext",
            "status": "queued",
        },
    )
    monkeypatch.setattr(server.CONFIG, "snapshot", lambda: frozen)
    monkeypatch.setattr(
        server.TaskContractCompiler,
        "compile_graph",
        lambda *_args, **_kwargs: pytest.fail(
            "vNext must not invoke the legacy compiler"
        ),
    )

    def start(graph, inputs, *, runtime_version, frozen_config):
        calls.append((graph, inputs, runtime_version, frozen_config))
        execution.graph = graph
        return execution

    monkeypatch.setattr(server, "_start_arp_execution", start)

    response = client.post(
        "/api/v2/arp/runs",
        json={
            "graph": _graph_payload(),
            "inputs": {"execution_context": {"format": "execution-context/1"}},
            "runtime_version": "vnext",
        },
    )

    assert response.status_code == 200
    assert response.json()["runtime_version"] == "vnext"
    assert response.json()["run"]["runtime_version"] == "vnext"
    assert len(calls) == 1
    assert calls[0][2] is ArpRuntimeVersion.VNEXT
    assert calls[0][3] is frozen


def test_unstarted_vnext_cancel_is_retained_without_touching_trace():
    from winpilot import server
    from winpilot.orchestrator.contracts import ArpGraph, ArpRunStatus
    from winpilot.orchestrator.runtime import ArpExecution

    execution = ArpExecution(ArpGraph.model_validate(_graph_payload()))
    execution.runtime_state.update({
        "runtime_version": "vnext",
        "runtime_budget": {
            "max_turns": 30,
            "max_runtime_s": 300,
            "max_model_calls": 60,
        },
        "no_legacy_fallback": True,
    })

    changed = server._terminalize_unstarted_vnext_execution(
        execution,
        status=ArpRunStatus.CANCELED,
        detail="canceled before resources",
        trace_status="not_started",
    )

    assert changed is True
    assert server._terminalize_unstarted_vnext_execution(
        execution,
        status=ArpRunStatus.FAILED,
        detail="must remain canceled",
        trace_status="failed_to_start",
    ) is False
    snapshot = execution.to_dict()
    assert snapshot["status"] == "canceled"
    assert snapshot["runtime_version"] == "vnext"
    assert snapshot["no_legacy_fallback"] is True
    assert snapshot["trace_status"] == "not_started"
    assert snapshot["runtime_lifecycle"] == {
        "phase": "pre_start",
        "terminal": True,
        "status": "canceled",
        "detail": "canceled before resources",
        "trace_status": "not_started",
        "no_legacy_fallback": True,
    }


def test_vnext_trace_start_failure_terminalizes_without_legacy_fallback(
    monkeypatch,
):
    from winpilot import server
    from winpilot.orchestrator.contracts import (
        ArpGraph,
        ArpRuntimeVersion,
    )
    from winpilot.orchestrator.runtime import ArpExecution
    from winpilot.orchestrator import vnext_live_canary

    execution = ArpExecution(ArpGraph.model_validate(_graph_payload()))
    submitted: dict[str, object] = {}
    monkeypatch.setattr(
        vnext_live_canary,
        "prepare_live_vnext_execution",
        lambda *_args, **_kwargs: execution,
    )

    def submit(_kind, runner, **kwargs):
        submitted.update({"runner": runner, **kwargs})
        return SimpleNamespace()

    monkeypatch.setattr(server.RUNTIME_JOBS, "submit", submit)
    monkeypatch.setattr(
        server.trace_mod.RECORDER,
        "start",
        lambda _run_id: (_ for _ in ()).throw(
            OSError("trace open failed")
        ),
    )
    stop_calls = []
    monkeypatch.setattr(
        server.trace_mod.RECORDER,
        "stop",
        lambda: stop_calls.append(True),
    )

    started = server._start_arp_execution(
        execution.graph,
        {},
        runtime_version=ArpRuntimeVersion.VNEXT,
        frozen_config={},
    )
    try:
        result = submitted["runner"](execution.stop_event)
    finally:
        with server.STATE.lock:
            server.STATE.run_jobs.pop(execution.run_id, None)

    assert started is execution
    assert result["ok"] is False
    assert result["runtime_version"] == "vnext"
    assert execution.status.value == "failed"
    assert execution.to_dict()["trace_status"] == "failed_to_start"
    assert execution.to_dict()["no_legacy_fallback"] is True
    assert stop_calls == []


@pytest.mark.parametrize("stop_fails", [False, True])
def test_vnext_trace_finalization_is_frozen_into_job_run_and_done_event(
    client,
    monkeypatch,
    stop_fails,
):
    from winpilot import server
    from winpilot.orchestrator import vnext_live_canary
    from winpilot.orchestrator.contracts import (
        ArpGraph,
        ArpRuntimeVersion,
    )
    from winpilot.orchestrator.runtime import ArpExecution
    from winpilot.runtime_jobs import RuntimeJobManager
    from winpilot.utils.log import BUS

    execution = ArpExecution(ArpGraph.model_validate(_graph_payload()))
    execution.runtime_state.update({
        "runtime_budget": {
            "max_turns": 30,
            "max_runtime_s": 300,
            "max_model_calls": 60,
        },
        "no_legacy_fallback": True,
        "trace_status": "not_started",
    })
    jobs = RuntimeJobManager()
    monkeypatch.setattr(server, "RUNTIME_JOBS", jobs)
    monkeypatch.setattr(
        vnext_live_canary,
        "prepare_live_vnext_execution",
        lambda *_args, **_kwargs: execution,
    )
    monkeypatch.setattr(
        server.trace_mod.RECORDER,
        "owns",
        lambda run_id: run_id == execution.run_id,
    )

    if stop_fails:
        def stop():
            raise OSError("trace flush failed")
    else:
        def stop():
            return None

    monkeypatch.setattr(server.trace_mod.RECORDER, "stop", stop)
    events = BUS.subscribe()
    try:
        started = server._start_arp_execution(
            execution.graph,
            {},
            runtime_version=ArpRuntimeVersion.VNEXT,
            frozen_config={},
        )
        job = jobs.get(f"arp-job-{execution.run_id}")
        assert job is not None
        assert job.done.wait(2.0)
        observed = []
        while not events.empty():
            observed.append(events.get_nowait())
    finally:
        BUS.unsubscribe(events)
        with server.STATE.lock:
            server.STATE.run_jobs.pop(execution.run_id, None)

    assert started is execution
    assert job.result["runtime_version"] == "vnext"
    assert job.result["run"] == execution.to_dict()
    done_events = [
        event for event in observed
        if event.get("type") == "arp_run_done"
        and event.get("run_id") == execution.run_id
    ]
    assert len(done_events) == 1
    final_snapshot = done_events[0]["runtime_snapshot"]
    assert final_snapshot == execution.audit_snapshot()
    assert final_snapshot["runtime_lifecycle"]["terminal"] is True
    assert final_snapshot["no_legacy_fallback"] is True
    if stop_fails:
        assert job.status == "failed"
        assert job.result["ok"] is False
        assert job.result["run"]["status"] == "failed"
        assert job.result["run"]["trace_status"] == "stop_failed"
        assert "trace flush failed" in job.error
        assert done_events[0]["status"] == "failed"
        assert final_snapshot["runtime_lifecycle"]["status"] == "failed"
        assert final_snapshot["trace_error"] == "trace flush failed"
    else:
        assert job.status == "done"
        assert job.result["ok"] is True
        assert job.result["run"]["status"] == "done"
        assert job.result["run"]["trace_status"] == "stopped"
        assert done_events[0]["status"] == "done"
        assert final_snapshot["runtime_lifecycle"]["status"] == "done"
        assert final_snapshot["trace_error"] == ""


def test_vnext_submit_failure_rolls_back_execution_and_job_binding(
    monkeypatch,
):
    from winpilot import server
    from winpilot.orchestrator.contracts import (
        ArpGraph,
        ArpRuntimeVersion,
    )
    from winpilot.orchestrator.runtime import ArpExecution
    from winpilot.orchestrator import vnext_live_canary

    execution = ArpExecution(ArpGraph.model_validate(_graph_payload()))
    monkeypatch.setattr(
        vnext_live_canary,
        "prepare_live_vnext_execution",
        lambda *_args, **_kwargs: execution,
    )
    monkeypatch.setattr(
        server.RUNTIME_JOBS,
        "submit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("thread start failed")
        ),
    )

    with pytest.raises(RuntimeError, match="thread start failed"):
        server._start_arp_execution(
            execution.graph,
            {},
            runtime_version=ArpRuntimeVersion.VNEXT,
            frozen_config={},
        )

    assert execution.status.value == "failed"
    assert execution.to_dict()["trace_status"] == "not_started"
    assert execution.to_dict()["no_legacy_fallback"] is True
    with server.STATE.lock:
        assert execution.run_id not in server.STATE.run_jobs


def test_frontend_task_contract_compile_endpoint_returns_typed_satisfiable_graph(client):
    task = "用画板画一只鸟然后截图用QQ发给测试联系人"
    response = client.post(
        "/api/v2/task-contracts/compile",
        json={"task": task, "max_steps": 50, "require_gui": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["contract"]["satisfiable"] is True
    assert [stage["type"] for stage in data["contract"]["stages"]] == [
        "ExecuteDesktopTask",
    ]
    assert data["contract"]["stages"][0]["parameters"]["goal"] == task
    assert "task_stage" in {node["kind"] for node in data["graph"]["nodes"]}
    assert "agent_goal" not in {node["kind"] for node in data["graph"]["nodes"]}


def test_frontend_compile_cold_start_disables_skill_and_memory_paths(client):
    response = client.post(
        "/api/v2/task-contracts/compile",
        json={
            "task": "用画板画一只鸟然后截图用QQ发给测试联系人",
            "max_steps": 50,
            "cold_start": True,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["contract"]["experience_policy"] == "disabled"
    assert data["graph"]["metadata"]["experience_policy"] == "disabled"
    assert all(stage["allow_skill_replay"] is False for stage in data["contract"]["stages"])
    assert "skill_replay" not in {node["kind"] for node in data["graph"]["nodes"]}


def test_visible_client_can_require_the_lossless_generic_gui_route(client):
    task = "把这个弄好"
    response = client.post(
        "/api/v2/task-contracts/compile",
        json={"task": task, "cold_start": True, "require_gui": True},
    )

    assert response.status_code == 200
    data = response.json()
    assert [stage["type"] for stage in data["contract"]["stages"]] == [
        "ExecuteDesktopTask",
    ]
    assert data["contract"]["stages"][0]["parameters"]["goal"] == task
    assert isinstance(data["graph"]["metadata"]["session_generation"], int)


def test_session_clear_invalidates_a_previously_compiled_graph(client):
    compiled = client.post(
        "/api/v2/task-contracts/compile",
        json={"task": "把这个弄好", "cold_start": True, "require_gui": True},
    )
    assert compiled.status_code == 200
    graph = compiled.json()["graph"]

    cleared = client.delete("/api/session")
    assert cleared.status_code == 200
    assert isinstance(cleared.json()["generation"], int)

    started = client.post(
        "/api/v2/arp/runs",
        json={"graph": graph, "inputs": {}},
    )
    assert started.status_code == 409
    assert "Session context changed" in started.json()["detail"]


def test_compile_defers_discoverable_fields_to_the_live_desktop(client):
    response = client.post(
        "/api/v2/task-contracts/compile",
        json={"task": "把截图发给测试联系人", "max_steps": 20},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["needs_input"] is False
    assert data["contract"]["satisfiable"] is True
    assert data["clarification_questions"] == []
    assert [stage["type"] for stage in data["contract"]["stages"]] == [
        "ExecuteDesktopTask",
    ]
    stage = data["contract"]["stages"][0]
    assert stage["parameters"]["runtime_resolution_fields"] == ["channel"]
    assert stage["parameters"]["preserve_original_intent"] is True


def test_validate_and_run_arp_graph_through_api(client):
    graph = _graph_payload()

    validation = client.post("/api/v2/arp/validate", json=graph)
    assert validation.status_code == 200
    assert validation.json()["valid"] is True

    created = client.post("/api/v2/arp/runs", json={"graph": graph, "inputs": {"source": "test"}})
    assert created.status_code == 200
    assert created.json()["runtime_version"] == "legacy"
    assert created.json()["run"]["runtime_version"] == "legacy"
    run_id = created.json()["run_id"]

    direct = client.get(f"/api/v2/arp/runs/{run_id}")
    assert direct.status_code == 200
    assert direct.json()["run"]["run_id"] == run_id

    deadline = time.time() + 2
    detail = None
    while time.time() < deadline:
        response = client.get(f"/api/v2/runs/{run_id}")
        assert response.status_code == 200
        detail = response.json()
        if detail["arp"]["status"] in {"done", "failed"}:
            break
        time.sleep(0.01)

    assert detail is not None
    assert detail["arp"]["status"] == "done"
    assert detail["arp"]["variables"]["ready"] is True
    assert detail["run"]["kind"] == "arp"
    assert detail["run"]["status"] == "done"

    jobs = client.get("/api/v2/jobs").json()["jobs"]
    job = next(item for item in jobs if item["metadata"].get("run_id") == run_id)
    assert job["kind"] == "arp"
    assert job["status"] == "done"
    assert "result" not in job

    platform_jobs = client.get("/api/v2/platform").json()["status"]["runtime_jobs"]
    platform_job = next(item for item in platform_jobs if item["metadata"].get("run_id") == run_id)
    assert "result" not in platform_job


def test_generic_run_cancel_route_cancels_arp_runtime_job(client):
    graph = {
        "format": "arp/1",
        "id": "cancel-api-graph",
        "name": "Cancelable API graph",
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "delay", "kind": "delay", "config": {"duration_ms": 5000}},
            {"id": "end", "kind": "end"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "delay"},
            {"id": "e2", "source": "delay", "target": "end"},
        ],
        "policy": {"require_evidence_on_done": False},
    }
    created = client.post("/api/v2/arp/runs", json={"graph": graph, "inputs": {}})
    assert created.status_code == 200
    run_id = created.json()["run_id"]

    canceled = client.post(f"/api/v2/runs/{run_id}/cancel")
    assert canceled.status_code == 200
    assert canceled.json()["ok"] is True

    deadline = time.time() + 2
    job = None
    while time.time() < deadline:
        jobs = client.get("/api/v2/jobs").json()["jobs"]
        job = next(item for item in jobs if item["metadata"].get("run_id") == run_id)
        if job["status"] == "canceled":
            break
        time.sleep(0.01)
    assert job is not None
    assert job["status"] == "canceled"
    assert client.post(f"/api/v2/runs/{run_id}/cancel").status_code == 409


def test_generic_run_pause_and_resume_routes_control_arp_run(client):
    graph = {
        "format": "arp/1",
        "id": "pause-api-graph",
        "name": "Pausable API graph",
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "delay", "kind": "delay", "config": {"duration_ms": 5000}},
            {"id": "end", "kind": "end"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "delay"},
            {"id": "e2", "source": "delay", "target": "end"},
        ],
        "policy": {"require_evidence_on_done": False},
    }
    created = client.post("/api/v2/arp/runs", json={"graph": graph, "inputs": {}})
    assert created.status_code == 200
    run_id = created.json()["run_id"]

    paused = client.post(f"/api/v2/runs/{run_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["arp"]["manual_paused"] is True
    assert paused.json()["arp"]["status"] == "paused"
    assert client.post(f"/api/v2/runs/{run_id}/pause").status_code == 409

    resumed = client.post(f"/api/v2/runs/{run_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["arp"]["manual_paused"] is False
    assert resumed.json()["arp"]["status"] in {"queued", "running"}
    assert client.post(f"/api/v2/runs/{run_id}/resume").status_code == 409
    assert client.post(f"/api/v2/runs/{run_id}/cancel").status_code == 200


def test_generic_run_cancel_route_also_cancels_agent_job(client):
    from winpilot import server

    run_id = "agent-generic-cancel"
    stop_event = threading.Event()
    runner = SimpleNamespace(run_id=run_id, stop_event=stop_event)

    def wait_for_cancel(runtime_stop):
        runtime_stop.wait(2)
        return {"ok": False, "error": "canceled"}

    job = server.RUNTIME_JOBS.submit(
        "agent",
        wait_for_cancel,
        metadata={"run_id": run_id, "task": "cancel me"},
        stop_event=stop_event,
    )
    server.STATE.register_runner(runner, job.id)

    response = client.post(f"/api/v2/runs/{run_id}/cancel")

    assert response.status_code == 200
    assert stop_event.is_set()
    assert response.json()["arp"] is None


def test_generic_run_cancel_rejects_stale_active_projection(client, monkeypatch):
    from winpilot import server

    run_id = "stale-active-projection"
    monkeypatch.setattr(
        server.RUN_PROJECTIONS,
        "get",
        lambda requested: {
            "run_id": run_id,
            "status": "running",
            "kind": "arp",
        } if requested == run_id else None,
    )
    monkeypatch.setattr(server.ARP_RUNS, "get", lambda _requested: None)
    monkeypatch.setattr(server.STATE, "job_for_run", lambda _requested: None)

    response = client.post(f"/api/v2/runs/{run_id}/cancel")

    assert response.status_code == 409
    assert response.json()["detail"] == "run is not active"


def test_run_artifact_endpoint_serves_only_registered_current_run_artifact(client, tmp_path):
    from winpilot.orchestrator.contracts import ArpGraph
    from winpilot.orchestrator.runtime import ARP_RUNS, ArpRunStatus
    from winpilot.tasking.artifacts import ArtifactStore

    execution = ARP_RUNS.prepare(ArpGraph.model_validate(_graph_payload()))
    execution.status = ArpRunStatus.DONE
    execution.finished_at = time.time()
    store = ArtifactStore(execution.run_id, tmp_path / "managed")
    source = tmp_path / "proof.txt"
    source.write_text("verified artifact payload", encoding="utf-8")
    artifact = store.import_file(source)
    execution.runtime_state["artifact_store"] = store

    response = client.get(
        f"/api/v2/runs/{execution.run_id}/artifacts/{artifact.artifact_id}",
    )
    assert response.status_code == 200
    assert response.text == "verified artifact payload"
    assert response.headers["x-winpilot-artifact-id"] == artifact.artifact_id
    assert client.get(
        f"/api/v2/runs/{execution.run_id}/artifacts/art-unknown",
    ).status_code == 404


def test_historical_arp_detail_and_artifact_survive_runtime_restart(client, tmp_path, monkeypatch):
    from winpilot import server
    from winpilot.tasking.artifacts import ArtifactStore

    run_id = "arp-historical-audit-api"
    traces = tmp_path / "traces"
    root = traces / run_id / "artifacts"
    monkeypatch.setattr(server, "TRACES_DIR", traces)
    source = tmp_path / "proof.txt"
    source.write_text("persisted proof", encoding="utf-8")
    artifact = ArtifactStore(run_id, root).import_file(source)

    server.RUN_PROJECTIONS.on_event({
        "type": "arp_run_start",
        "ts": 1.0,
        "run_id": run_id,
        "graph_id": "historical-contract",
        "graph_name": "Historical typed run",
    })
    server.RUN_PROJECTIONS.on_event({
        "type": "arp_node_done",
        "ts": 2.0,
        "run_id": run_id,
        "node_id": "stage-capture",
        "kind": "task_stage",
        "ok": True,
        "attempts": 1,
        "output": {
            "stage_id": "stage-capture",
            "stage_type": "CaptureArtifact",
            "status": "done",
            "tools": ["capture_artifact"],
            "required_capabilities": ["artifact.capture"],
            "verifier_requirements": ["artifact.hash"],
            "exposed_capabilities": ["artifact.capture", "artifact.hash"],
            "missing_capabilities": [],
            "satisfiable": True,
            "artifact_id": artifact.artifact_id,
        },
        "runtime_metrics": {"controller_elapsed_ms": 4},
    })
    server.RUN_PROJECTIONS.on_event({
        "type": "arp_run_done", "ts": 3.0, "run_id": run_id, "status": "done",
    })

    detail = client.get(f"/api/v2/runs/{run_id}")
    downloaded = client.get(f"/api/v2/runs/{run_id}/artifacts/{artifact.artifact_id}")

    assert detail.status_code == 200
    assert detail.json()["arp"]["audit_source"] == "trace_projection"
    assert detail.json()["arp"]["stage_trace"][0]["stage_type"] == "CaptureArtifact"
    assert detail.json()["arp"]["artifacts"]["count"] == 1
    assert downloaded.status_code == 200
    assert downloaded.text == "persisted proof"


def test_frontend_run_fails_closed_when_loaded_python_is_stale(client, monkeypatch):
    from winpilot import server

    monkeypatch.setattr(server, "runtime_source_drifted", lambda: True)
    response = client.post(
        "/api/v2/arp/runs",
        json={"graph": _graph_payload(), "inputs": {}},
    )

    assert response.status_code == 409
    assert "Restart the runtime" in response.json()["detail"]


def test_common_mimo_pro_alias_is_canonicalized():
    from winpilot.server import _canonicalize_model_aliases

    patch = {
        "api": {"model": "mimo-v2.5pro"},
        "vlm": {"model": "MiMo-V2.5Pro"},
    }
    _canonicalize_model_aliases(patch)

    assert patch["api"]["model"] == "mimo-v2.5-pro"
    assert patch["vlm"]["model"] == "mimo-v2.5-pro"


def test_chatgpt_desktop_integration_exposes_real_mcp_handshake(monkeypatch):
    pytest.importorskip("mcp")
    from fastapi.testclient import TestClient
    from winpilot import server

    monkeypatch.setattr(server.ocr, "warm_async", lambda: False)
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "winpilot-test", "version": "1"},
        },
    }

    with TestClient(server.app, base_url="http://127.0.0.1") as mcp_client:
        integration = mcp_client.get("/api/v2/integrations/chatgpt")
        handshake = mcp_client.post("/mcp", headers=headers, json=initialize)
        tools = mcp_client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        active_integration = mcp_client.get("/api/v2/integrations/chatgpt")

    assert integration.status_code == 200
    assert integration.json()["requires_openai_api_key"] is False
    assert integration.json()["server_ready"] is True
    assert integration.json()["endpoint"] == "http://127.0.0.1/mcp"
    assert handshake.status_code == 200
    assert handshake.json()["result"]["serverInfo"]["name"] == "WinPilot GUI Agent + ARP"
    tool_names = {tool["name"] for tool in tools.json()["result"]["tools"]}
    assert {"observe_desktop", "execute_gui_subgoal", "run_arp_graph"} <= tool_names
    active = active_integration.json()
    assert active["client_active"] is True
    assert active["connection_status"] == "active"
    assert active["request_count"] >= 2
    assert active["security_mode"] == "loopback_only"
    assert active["proxy_bypass"] == "NO_PROXY=127.0.0.1,localhost"
