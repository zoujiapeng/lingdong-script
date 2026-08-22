import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest


def test_server_import_does_not_load_legacy_agent_composition():
    package_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        value
        for value in (str(package_root), env.get("PYTHONPATH", ""))
        if value
    )
    script = (
        "import importlib.abc\n"
        "import sys\n"
        "class BlockLegacyAgent(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname == 'winpilot.agent' or fullname.startswith('winpilot.agent.'):\n"
        "            raise ModuleNotFoundError(fullname)\n"
        "        return None\n"
        "sys.meta_path.insert(0, BlockLegacyAgent())\n"
        "from winpilot import server\n"
        "from types import SimpleNamespace\n"
        "assert any(route.path == '/api/v2/goals/runs' for route in server.app.routes)\n"
        "server.runtime_source_drifted = lambda: False\n"
        "server.CONFIG.snapshot = lambda: {'vnext': {'live_agent_enabled': True}}\n"
        "server.AGENT_THREADS.begin_turn = lambda goal, thread_id='': ('thread', 'turn', [])\n"
        "server._start_arp_execution = lambda graph, inputs, **kwargs: SimpleNamespace(\n"
        "    run_id='run', graph=graph, to_dict=lambda: {'status': 'queued'}\n"
        ")\n"
        "started = server.start_goal_run(server.GoalRunRequest(goal='Say hello'))\n"
        "assert started['runtime_version'] == 'vnext'\n"
        "assert started['admission']['no_legacy_fallback'] is True\n"
        "loaded = [name for name in sys.modules if name.startswith('winpilot.agent')]\n"
        "assert 'winpilot.orchestrator.adapters' not in sys.modules\n"
        "assert not loaded, loaded\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=package_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.fixture
def client(monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from winpilot import server

    with server.STATE.lock:
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
        "policy": {"fail_fast": True},
        "nodes": [
            {"id": "start", "kind": "start"},
            {"id": "set", "kind": "set_variable", "config": {"name": "ready", "value": True}},
            {"id": "end", "kind": "end"},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "set"},
            {"id": "e2", "source": "set", "target": "end"},
        ],
    }


def test_platform_overview_exposes_contracts_and_capabilities(client):
    response = client.get("/api/v2/platform")

    assert response.status_code == 200
    data = response.json()
    assert data["api_version"] == "2"
    assert data["contracts"]["arp"] == "arp/1"
    assert "task_contract" not in data["contracts"]
    assert "mcp" not in data["contracts"]
    capability_ids = {item["id"] for item in data["capabilities"]}
    assert {"agent.goal", "controller.batch", "replay.step", "skill.replay", "evidence.gate", "task.stage", "control.variable"} <= capability_ids
    assert "legacy.flow" not in capability_ids
    advertised_nodes = {
        node_type
        for item in data["capabilities"]
        for node_type in item.get("flow_node_types", [])
    }
    assert {"agent_goal", "action_batch", "skill_replay", "variable_set"} <= advertised_nodes
    assert "human_gate" not in advertised_nodes


def test_product_and_example_config_default_to_vnext():
    from winpilot import config

    example = json.loads(
        (config.ROOT_DIR / "config.example.json").read_text(encoding="utf-8")
    )

    assert config._DEFAULTS["vnext"]["live_agent_enabled"] is True
    assert example["vnext"]["live_agent_enabled"] is True


def test_platform_overview_exposes_fail_closed_vnext_live_agent_budget(
    client,
    monkeypatch,
):
    from winpilot import server

    original_get = server.CONFIG.get

    def frozen_get(*path, default=None):
        if path == ("vnext",):
            return {
                "live_agent_enabled": False,
                "max_turns": 30,
                "max_runtime_s": 300,
                "max_model_calls": 60,
            }
        return original_get(*path, default=default)

    monkeypatch.setattr(server.CONFIG, "get", frozen_get)

    status = client.get("/api/v2/platform").json()["status"]["vnext"]

    assert status == {
        "live_agent_enabled": False,
        "budget": {
            "max_turns": 30,
            "max_runtime_s": 300,
            "max_model_calls": 60,
        },
    }


def test_public_trace_routes_reject_private_runtime_evidence_paths(
    client,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from winpilot import server

    traces = tmp_path / "traces"
    private_run = traces / "vnext_runtime_evidence" / "run-private"
    private_run.mkdir(parents=True)
    private_text = "PRIVATE-TEXT-public-trace-route-7f2d"
    (private_run / "evidence.jsonl").write_text(
        json.dumps(
            {
                "type": "physical_attempt",
                "canonical_arguments": {"text": private_text},
                "physical_arguments": {
                    "x": 77889911,
                    "y": 66778822,
                    "amount": -99887766,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    artifacts = private_run / "artifacts"
    artifacts.mkdir()
    (artifacts / "private.jpg").write_bytes(
        b"private-runtime-evidence-image"
    )
    (traces / "vnext_runtime_evidence.jsonl").write_text(
        '{"type":"public-trace-owner"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "TRACES_DIR", traces)
    monkeypatch.setattr(server.trace_mod, "TRACES_DIR", traces)
    downstream_calls: list[str] = []
    original_read_trace = server.trace_mod.read_trace
    original_export_script = server.trace_mod.export_script
    original_export_ir = server.trace_mod.export_automation_ir
    original_ensure_inside = server._ensure_inside

    def observed_read_trace(*args, **kwargs):
        downstream_calls.append("read_trace")
        return original_read_trace(*args, **kwargs)

    def observed_export_script(*args, **kwargs):
        downstream_calls.append("export_script")
        return original_export_script(*args, **kwargs)

    def observed_export_ir(*args, **kwargs):
        downstream_calls.append("export_ir")
        return original_export_ir(*args, **kwargs)

    def observed_ensure_inside(*args, **kwargs):
        downstream_calls.append("ensure_inside")
        return original_ensure_inside(*args, **kwargs)

    monkeypatch.setattr(
        server.trace_mod,
        "read_trace",
        observed_read_trace,
    )
    monkeypatch.setattr(
        server.trace_mod,
        "export_script",
        observed_export_script,
    )
    monkeypatch.setattr(
        server.trace_mod,
        "export_automation_ir",
        observed_export_ir,
    )
    monkeypatch.setattr(
        server,
        "_ensure_inside",
        observed_ensure_inside,
    )

    private_urls = (
        "/api/trace/"
        "vnext_runtime_evidence%5Crun-private%5Cevidence",
        "/api/export/"
        "vnext_runtime_evidence%5Crun-private%5Cevidence",
        "/api/export/"
        "vnext_runtime_evidence%5Crun-private%5Cevidence/ir",
    )
    responses = [client.get(url) for url in private_urls[:1]]
    responses.extend(client.post(url) for url in private_urls[1:])
    responses.append(
        client.get(
            "/api/trace/vnext_runtime_evidence/shot/"
            "run-private%5Cartifacts%5Cprivate.jpg"
        )
    )

    assert all(response.status_code == 404 for response in responses)
    assert downstream_calls == []
    public_wire = "\n".join(response.text for response in responses)
    assert private_text not in public_wire
    assert "canonical_arguments" not in public_wire
    assert "physical_arguments" not in public_wire
    assert "private-runtime-evidence-image" not in public_wire


def test_public_trace_routes_preserve_safe_historical_trace_and_image(
    client,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from winpilot import server

    traces = tmp_path / "traces"
    run_id = "historical trace"
    public_summary = {
        "available": True,
        "status": "complete",
        "journal_head_sha256": "a" * 64,
        "journal_event_count": 9,
        "report_sha256": "b" * 64,
        "audit_locator": "vnext-audit-0123456789abcdef0123456789abcdef",
        "parameter_visibility": "private_runtime_evidence",
    }
    traces.mkdir()
    (traces / f"{run_id}.jsonl").write_text(
        "\n".join(
            json.dumps(event)
            for event in (
                {
                    "type": "agent_start",
                    "run_id": run_id,
                    "task": "historical trace compatibility",
                    "ts": 1.0,
                },
                {
                    "type": "live_agent_done",
                    "run_id": run_id,
                    "runtime_evidence": public_summary,
                    "ts": 2.0,
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    image = traces / run_id / "after 1.jpg"
    image.parent.mkdir()
    image.write_bytes(b"safe-public-trace-image")
    monkeypatch.setattr(server, "TRACES_DIR", traces)
    monkeypatch.setattr(server.trace_mod, "TRACES_DIR", traces)

    detail = client.get("/api/trace/historical%20trace")
    screenshot = client.get(
        "/api/trace/historical%20trace/shot/after%201.jpg"
    )

    assert detail.status_code == 200
    assert detail.json()[-2] == {
        "type": "live_agent_done",
        "run_id": run_id,
        "runtime_evidence": public_summary,
        "ts": 2.0,
    }
    assert detail.json()[-1] == {
        "type": "trace_read_integrity",
        "run_id": run_id,
        "complete": True,
        "total_lines": 2,
        "returned_events": 2,
        "malformed_lines": 0,
        "truncated": False,
    }
    assert screenshot.status_code == 200
    assert screenshot.content == b"safe-public-trace-image"


def test_public_trace_detail_is_bounded_and_reports_read_integrity(
    client,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from winpilot import server

    traces = tmp_path / "traces"
    traces.mkdir()
    (traces / "long-trace.jsonl").write_text(
        "".join(
            json.dumps({"type": "note", "index": index}) + "\n"
            for index in range(2_005)
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server.trace_mod, "TRACES_DIR", traces)

    detail = client.get("/api/trace/long-trace?limit=3")

    assert detail.status_code == 200
    assert [event["index"] for event in detail.json()[:-1]] == [0, 1, 2]
    assert detail.json()[-1] == {
        "type": "trace_read_integrity",
        "run_id": "long-trace",
        "complete": False,
        "total_lines": 2_005,
        "returned_events": 3,
        "malformed_lines": 0,
        "truncated": True,
    }
    capped = client.get("/api/trace/long-trace?limit=999999")
    assert len(capped.json()) == 2_001
    assert capped.json()[-1]["returned_events"] == 2_000
    assert capped.json()[-1]["truncated"] is True


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


def test_vnext_selection_fails_closed_when_live_agent_is_disabled(
    client,
    monkeypatch,
):
    from winpilot import server

    monkeypatch.setattr(
        server.CONFIG,
        "snapshot",
        lambda: {
            "vnext": {
                "live_agent_enabled": False,
                "max_turns": 30,
                "max_runtime_s": 300,
                "max_model_calls": 60,
            },
        },
    )
    # The legacy TaskContractCompiler was deleted with the tasking chain;
    # there is no compile path left for a vNext selection to invoke.
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
        "vNext live Agent is disabled; no legacy fallback was run"
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
            "live_agent_enabled": True,
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
    # The legacy TaskContractCompiler was deleted with the tasking chain;
    # there is no compile path left for a vNext selection to invoke.

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


def test_product_goal_admission_owns_exact_graph_and_vnext_runtime(
    client,
    monkeypatch,
):
    from winpilot import server
    from winpilot.orchestrator.contracts import ArpRuntimeVersion
    from winpilot.orchestrator import live_agent

    frozen = {
        "api": {"model": "test-vision-model"},
        "vnext": {
            "live_agent_enabled": True,
            "max_turns": 30,
            "max_runtime_s": 300,
            "max_model_calls": 60,
        },
    }
    calls = []
    history = (
        {"role": "user", "content": "Remember marker A"},
        {"role": "assistant", "content": "I will remember marker A"},
    )
    monkeypatch.setattr(server.CONFIG, "snapshot", lambda: frozen)
    monkeypatch.setattr(
        server.AGENT_THREADS,
        "begin_turn",
        lambda goal, *, thread_id="": (
            "thread-existing",
            "turn-new",
            list(history),
        )
        if goal == "Complete the visible task"
        and thread_id == "thread-existing"
        else pytest.fail("goal admission lost the requested thread binding"),
    )
    # The legacy TaskContractCompiler was deleted with the tasking chain;
    # product goal admission has no compile path left to invoke.

    def start(graph, inputs, **kwargs):
        calls.append((graph, inputs, kwargs))
        return SimpleNamespace(
            run_id="arp-goal-admission",
            graph=graph,
            to_dict=lambda: {
                "run_id": "arp-goal-admission",
                "graph_id": graph.id,
                "runtime_version": "vnext",
                "status": "queued",
            },
        )

    monkeypatch.setattr(server, "_start_arp_execution", start)
    response = client.post(
        "/api/v2/goals/runs",
        json={
            "goal": "  Complete the visible task  ",
            "thread_id": "thread-existing",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_version"] == "vnext"
    assert payload["admission"] == {
        "format": "goal-admission/1",
        "goal": "Complete the visible task",
        "route": "live_agent",
        "runtime_version": "vnext",
        "reason": "single_semantic_agent",
        "no_legacy_fallback": True,
    }
    assert payload["thread_id"] == "thread-existing"
    assert payload["turn_id"] == "turn-new"
    assert len(calls) == 1
    graph, inputs, kwargs = calls[0]
    assert live_agent._validate_exact_graph(graph) == (
        "Complete the visible task"
    )
    assert graph.metadata == {}
    assert graph.policy.model_dump(mode="json") == {
        "max_node_visits": 1,
        "fail_fast": True,
    }
    assert kwargs["runtime_version"] is ArpRuntimeVersion.VNEXT
    assert kwargs["frozen_config"] is frozen
    assert kwargs["goal_admission"] == payload["admission"]
    assert inputs == {}
    assert kwargs["thread_id"] == "thread-existing"
    assert kwargs["turn_id"] == "turn-new"
    assert kwargs["thread_history"] == history


@pytest.mark.parametrize(
    "field",
    [
        "execution_context",
        "execution_constraint",
        "graph",
        "runtime_version",
    ],
)
def test_product_goal_admission_rejects_client_owned_projection_fields(
    client,
    field,
):
    body = {
        "goal": "Complete the visible task",
        field: (
            {}
            if field in {"execution_context", "graph"}
            else "gui_required"
            if field == "execution_constraint"
            else "vnext"
        ),
    }

    response = client.post("/api/v2/goals/runs", json=body)

    assert response.status_code == 422
    assert any(
        item["loc"][-1] == field and item["type"] == "extra_forbidden"
        for item in response.json()["detail"]
    )


def test_product_goal_admission_fails_closed_without_legacy_fallback(
    client,
    monkeypatch,
):
    from winpilot import server

    monkeypatch.setattr(
        server.CONFIG,
        "snapshot",
        lambda: {"vnext": {"live_agent_enabled": False}},
    )
    # The legacy TaskContractCompiler was deleted with the tasking chain.
    monkeypatch.setattr(
        server,
        "_start_arp_execution",
        lambda *_args, **_kwargs: pytest.fail(
            "disabled vNext must not start either runtime"
        ),
    )

    response = client.post(
        "/api/v2/goals/runs",
        json={"goal": "Complete the visible task"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "vNext live Agent is disabled; no legacy fallback was run"
    )


def test_start_execution_freezes_goal_admission_before_job_submission(
    monkeypatch,
):
    from winpilot import server
    from winpilot.orchestrator.contracts import ArpRuntimeVersion
    from winpilot.orchestrator import live_agent

    graph = live_agent.build_exact_live_vnext_graph(
        "Complete the visible task"
    )
    execution = SimpleNamespace(
        run_id="arp-goal-admission-audit",
        runtime_state={},
        stop_event=threading.Event(),
    )
    submitted = []
    monkeypatch.setattr(server.human_mod.RECORDER, "is_recording", lambda: False)
    prepared = []

    def prepare(*args, **kwargs):
        prepared.append((args, kwargs))
        return execution

    monkeypatch.setattr(
        live_agent,
        "prepare_live_vnext_execution",
        prepare,
    )
    bound = []
    monkeypatch.setattr(
        server.AGENT_THREADS,
        "bind_run",
        lambda **kwargs: bound.append(kwargs),
    )
    monkeypatch.setattr(
        server.RUNTIME_JOBS,
        "submit",
        lambda *args, **kwargs: submitted.append((args, kwargs)),
    )
    audit = {
        "format": "goal-admission/1",
        "goal": "Complete the visible task",
        "route": "live_agent",
        "runtime_version": "vnext",
        "reason": "single_semantic_agent",
        "no_legacy_fallback": True,
    }
    history = (
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
    )

    try:
        started = server._start_arp_execution(
            graph,
            {},
            runtime_version=ArpRuntimeVersion.VNEXT,
            frozen_config={"vnext": {"live_agent_enabled": True}},
            goal_admission=audit,
            thread_id="thread-audit",
            turn_id="turn-audit",
            thread_history=history,
        )
    finally:
        with server.STATE.lock:
            server.STATE.run_jobs.pop(execution.run_id, None)

    assert started is execution
    assert submitted
    assert execution.runtime_state["runtime_version"] == "vnext"
    assert execution.runtime_state["goal_admission"] == audit
    assert execution.runtime_state["goal_admission"] is not audit
    assert len(prepared) == 1
    assert prepared[0][0][1] == {}
    assert prepared[0][1]["thread_id"] == "thread-audit"
    assert prepared[0][1]["turn_id"] == "turn-audit"
    assert prepared[0][1]["history"] == history
    assert bound == [{
        "thread_id": "thread-audit",
        "turn_id": "turn-audit",
        "run_id": execution.run_id,
    }]

    from winpilot.orchestrator.runtime import ArpExecution

    persisted = ArpExecution(graph)
    persisted.runtime_state.update({
        "runtime_version": "vnext",
        "no_legacy_fallback": True,
        "goal_admission": dict(audit),
    })
    assert persisted.to_dict()["goal_admission"] == audit
    assert persisted.audit_snapshot()["goal_admission"] == audit


def test_unstarted_vnext_cancel_is_retained_without_touching_trace(
    monkeypatch,
):
    from winpilot import server
    from winpilot.orchestrator.contracts import ArpGraph, ArpRunStatus
    from winpilot.orchestrator.runtime import ArpExecution

    execution = ArpExecution(ArpGraph.model_validate(_graph_payload()))
    published = []
    monkeypatch.setattr(
        ArpExecution,
        "_publish_run_done",
        lambda self: published.append(self.status.value),
    )
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
    assert published == ["canceled"]
    assert server._terminalize_unstarted_vnext_execution(
        execution,
        status=ArpRunStatus.FAILED,
        detail="must remain canceled",
        trace_status="failed_to_start",
    ) is False
    assert published == ["canceled"]
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
    from winpilot.orchestrator import live_agent

    execution = ArpExecution(ArpGraph.model_validate(_graph_payload()))
    submitted: dict[str, object] = {}
    monkeypatch.setattr(
        live_agent,
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
    from winpilot.orchestrator import live_agent
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
        live_agent,
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
        # Trace finalization is diagnostic: the run keeps its own status and
        # its own error. A failed trace stop must not rewrite either, only the
        # trace_status/trace_error side channel.
        assert job.status == "done"
        assert job.result["ok"] is True
        assert job.result["run"]["status"] == "done"
        assert job.result["run"]["trace_status"] == "stop_failed"
        assert "trace flush failed" not in job.error
        assert done_events[0]["status"] == "done"
        assert final_snapshot["runtime_lifecycle"]["status"] == "done"
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
    from winpilot.orchestrator import live_agent

    execution = ArpExecution(ArpGraph.model_validate(_graph_payload()))
    monkeypatch.setattr(
        live_agent,
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
        "policy": {},
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
        "policy": {},
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


def test_source_drift_diagnostic_does_not_block_frontend_run(client, monkeypatch):
    from winpilot import runtime_info, server

    monkeypatch.setattr(runtime_info, "runtime_source_drifted", lambda: True)
    monkeypatch.setattr(
        server,
        "_start_arp_execution",
        lambda graph, _inputs, **_kwargs: SimpleNamespace(
            run_id="run-drift-advisory",
            graph=graph,
            to_dict=lambda: {"status": "queued"},
        ),
    )
    response = client.post(
        "/api/v2/arp/runs",
        json={"graph": _graph_payload(), "inputs": {}},
    )

    assert response.status_code == 200
    assert response.json()["run_id"] == "run-drift-advisory"


def test_common_mimo_pro_alias_is_canonicalized():
    from winpilot.server import _canonicalize_model_aliases

    patch = {
        "api": {"model": "mimo-v2.5pro"},
        "vlm": {"model": "MiMo-V2.5Pro"},
    }
    _canonicalize_model_aliases(patch)

    assert patch["api"]["model"] == "mimo-v2.5-pro"
    assert patch["vlm"]["model"] == "mimo-v2.5-pro"


def test_chatgpt_desktop_integration_reports_legacy_bridge_removed():
    from fastapi.testclient import TestClient
    from winpilot import server

    with TestClient(server.app, base_url="http://127.0.0.1") as mcp_client:
        integration = mcp_client.get("/api/v2/integrations/chatgpt")

    assert integration.status_code == 200
    assert integration.json()["ok"] is False
    assert integration.json()["connection_status"] == "removed"
    assert integration.json()["server_ready"] is False
    assert "allow_desktop_mutation" not in integration.json()
    assert integration.json()["requires_openai_api_key"] is False


def test_flow_fragment_job_waits_for_interactive_resource_and_is_cancelable(
    client,
    monkeypatch,
):
    from winpilot import server
    from winpilot.orchestrator import live_agent

    blocker_entered = threading.Event()
    release_blocker = threading.Event()
    proposal_entered = threading.Event()
    proposal_kwargs = {}

    def hold_interactive_resource(_stop_event):
        blocker_entered.set()
        release_blocker.wait(2)
        return {"ok": True}

    def fake_proposal(*_args, stop_event, **_kwargs):
        proposal_kwargs.update(_kwargs)
        proposal_entered.set()
        stop_event.wait(2)
        return {"ok": False, "error": "canceled"}

    monkeypatch.setattr(live_agent, "propose_live_flow_fragment", fake_proposal)
    blocker = server.RUNTIME_JOBS.submit(
        "test_interactive_blocker",
        hold_interactive_resource,
        resources=(server._INTERACTIVE_RUNTIME_RESOURCE,),
    )
    proposal_job = None
    try:
        assert blocker_entered.wait(1)
        response = client.post(
            "/api/v2/flow-fragments/proposals",
            json={
                "goal": "click",
                "document": {
                    "node_count": 2,
                    "edge_count": 1,
                    "node_types": ["start", "end"],
                },
                "catalog": [{
                    "type": "click",
                    "label": "Click",
                    "params": {"x": 0, "y": 0},
                    "output_ports": ["output"],
                }],
            },
        )
        assert response.status_code == 200
        proposal_job = server.RUNTIME_JOBS.get(response.json()["proposal_job_id"])
        assert proposal_job is not None
        deadline = time.time() + 1
        while proposal_job.status == "queued" and time.time() < deadline:
            time.sleep(0.01)
        assert proposal_job.resources == (server._INTERACTIVE_RUNTIME_RESOURCE,)
        assert proposal_job.status == "waiting_resources"
        assert not proposal_entered.is_set()

        release_blocker.set()
        assert proposal_entered.wait(1)
        assert set(proposal_kwargs) == {"document", "catalog", "config_snapshot"}
        canceled = client.post(f"/api/v2/jobs/{proposal_job.id}/cancel")
        assert canceled.status_code == 200
        assert proposal_job.done.wait(1)
        assert proposal_job.status == "canceled"
    finally:
        release_blocker.set()
        if proposal_job is not None and not proposal_job.done.is_set():
            server.RUNTIME_JOBS.cancel(proposal_job.id)
            proposal_job.done.wait(1)
        blocker.done.wait(1)
