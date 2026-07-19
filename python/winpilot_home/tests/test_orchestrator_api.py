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
