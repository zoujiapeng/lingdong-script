import threading
import time

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
    response = client.post(
        "/api/v2/task-contracts/compile",
        json={"task": "用画板画一只鸟然后截图用QQ发给测试联系人", "max_steps": 50},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["contract"]["satisfiable"] is True
    assert [stage["type"] for stage in data["contract"]["stages"]] == [
        "DrawArtifact", "CaptureArtifact", "OpenConversation", "SendArtifact", "VerifyDelivery",
    ]
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


def test_compile_reports_missing_adapter_before_starting_a_run(client):
    response = client.post(
        "/api/v2/task-contracts/compile",
        json={"task": "把截图发给测试联系人", "max_steps": 20},
    )
    assert response.status_code == 422
    assert "missing capabilities" in response.json()["detail"]


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
