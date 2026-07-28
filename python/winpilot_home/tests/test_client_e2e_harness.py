from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def _load_harness():
    path = Path(__file__).resolve().parents[3] / "scripts" / "client_e2e_run.py"
    spec = importlib.util.spec_from_file_location("client_e2e_run", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves the module by name while decorating ClientIdentity.
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_harness_keeps_the_exact_eleven_and_one_combined_long_goal():
    harness = _load_harness()

    assert list(harness.ORIGINAL_CASES) == [str(index) for index in range(1, 12)]
    assert harness.ORIGINAL_CASES["11"]["task"] == (
        "去qq找我的手机持续聊天,他如果发@电脑就说明在命令你,你要照做"
    )
    combined = harness.MIXED_CASES["mix-all-11"]["task"]
    positions = [
        combined.index(harness.ORIGINAL_CASES[str(index)]["task"])
        for index in range(1, 12)
    ]
    assert positions == sorted(positions)
    assert harness.MIXED_CASES["mix-all-11"]["max_steps"] == 400


def test_trace_snapshot_tracks_outer_arp_jsonl_files_not_artifact_directories(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    (tmp_path / "arp-one.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "arp-directory").mkdir()
    (tmp_path / "20260718-artifacts").mkdir()
    (tmp_path / "other.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(harness, "TRACE_ROOT", tmp_path)

    assert harness._trace_names() == {"arp-one"}


def test_runtime_status_retries_a_transient_managed_endpoint_timeout(
    monkeypatch,
):
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    driver._runtime_url = "http://127.0.0.1:1"
    driver._runtime_token = "test-token"
    calls = {"count": 0}

    class Response:
        def __enter__(self):
            return SimpleNamespace(
                read=lambda: b"",
            )

        def __exit__(self, *_args):
            return False

    def urlopen(_request, *, timeout):
        assert timeout == 3
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("transient status timeout")
        return Response()

    monkeypatch.setattr(harness.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(
        harness.json,
        "load",
        lambda _response: {
            "running": True,
            "arp_running": True,
            "runtime_jobs": [
                {
                    "job_id": "arp-job-test",
                    "kind": "arp",
                    "status": "running",
                    "created_at": 10.0,
                    "started_at": 10.1,
                    "metadata": {
                        "run_id": "arp-test",
                        "runtime_version": "vnext",
                        "graph_id": "vnext-live-canary-test",
                        "private": "must-not-project",
                    },
                    "error": "",
                    "result": {"canonical_arguments": {"x": 17}},
                }
            ],
            "desktop_action_queue": {
                "submitted": 1,
                "completed": 0,
                "last_action": "click",
                "async_latest": [
                    {"physical_arguments": {"x": 17, "y": 19}}
                ],
            },
            "current_run": {
                "run_id": "arp-test",
                "kind": "arp",
                "status": "running",
                "phase": "executing",
                "goal": "test",
                "current_node_id": "full-goal",
                "current_tool": None,
                "error": None,
            },
        },
    )
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)

    status = driver.runtime_status()
    assert status["current_run"]["run_id"] == "arp-test"
    assert status["runtime_jobs"] == [
        {
            "job_id": "arp-job-test",
            "kind": "arp",
            "status": "running",
            "created_at": 10.0,
            "started_at": 10.1,
            "metadata": {
                "run_id": "arp-test",
                "runtime_version": "vnext",
                "graph_id": "vnext-live-canary-test",
            },
            "error": "",
        }
    ]
    assert status["desktop_action_queue"] == {
        "submitted": 1,
        "completed": 0,
        "last_action": "click",
    }
    serialized = json.dumps(status, sort_keys=True)
    assert "must-not-project" not in serialized
    assert "canonical_arguments" not in serialized
    assert "physical_arguments" not in serialized
    assert calls["count"] == 2


def test_wait_for_result_accepts_vnext_job_before_current_run_projection():
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    statuses = [
        {
            "running": True,
            "arp_running": True,
            "current_run": {
                "run_id": "arp-previous",
                "goal": "previous",
                "status": "failed",
            },
            "runtime_jobs": [
                {
                    "job_id": "arp-job-new",
                    "kind": "arp",
                    "status": "running",
                    "metadata": {
                        "run_id": "arp-new",
                        "runtime_version": "vnext",
                        "graph_id": "graph-new",
                    },
                }
            ],
        },
        {
            "running": False,
            "arp_running": False,
            "current_run": {
                "run_id": "arp-new",
                "goal": "goal",
                "status": "failed",
            },
            "runtime_jobs": [
                {
                    "job_id": "arp-job-new",
                    "kind": "arp",
                    "status": "failed",
                    "metadata": {
                        "run_id": "arp-new",
                        "runtime_version": "vnext",
                        "graph_id": "graph-new",
                    },
                }
            ],
        },
    ]
    driver.runtime_status = (
        lambda: statuses.pop(0) if len(statuses) > 1 else statuses[0]
    )
    driver.buttons = lambda: []
    driver.texts = lambda: []
    driver._topbar_status = lambda _task: ""

    result = driver.wait_for_result(
        "goal",
        5,
        previous_run_id="arp-previous",
        accepted_run_id="arp-new",
    )

    assert result["outcome"] == "terminal"
    assert result["run_id"] == "arp-new"
    assert result["status"] == "failed"


def test_memory_isolation_requires_client_toggle_contract_and_clean_runner(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    trace = tmp_path / "arp-cold.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "type": "arp_run_start",
                    "task_contract": {"experience_policy": "disabled"},
                },
                {
                    "type": "agent_start",
                    "run_id": "child-run",
                    "has_experience": False,
                    "runtime": {"experience_enabled": False},
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(harness, "TRACE_ROOT", tmp_path)

    passed = harness._memory_isolation_evidence(
        ["arp-cold"], {"cold_start_toggle": 1},
    )
    assert passed["passed"] is True
    assert passed["contract_experience_policies"] == ["disabled"]
    assert passed["agent_starts"][0]["has_experience"] is False

    toggle_off = harness._memory_isolation_evidence(
        ["arp-cold"], {"cold_start_toggle": 0},
    )
    assert toggle_off["passed"] is False

    dirty = trace.read_text(encoding="utf-8").replace(
        '"has_experience": false', '"has_experience": true',
    )
    trace.write_text(dirty, encoding="utf-8")
    replayed = harness._memory_isolation_evidence(
        ["arp-cold"], {"cold_start_toggle": 1},
    )
    assert replayed["passed"] is False


def test_independent_session_reset_requires_empty_contract_context(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    trace = tmp_path / "arp-reset.jsonl"
    base = {
        "type": "arp_run_start",
        "run_id": "arp-reset",
        "task_contract": {
            "task": "generic task",
            "experience_policy": "disabled",
            "session_context": [],
        },
    }
    trace.write_text(json.dumps(base) + "\n", encoding="utf-8")
    monkeypatch.setattr(harness, "TRACE_ROOT", tmp_path)

    clean = harness._memory_isolation_evidence(
        ["arp-reset"],
        {"cold_start_toggle": 1},
        require_empty_session=True,
    )
    assert clean["passed"] is True
    assert clean["session_empty"] is True

    base["task_contract"]["session_context"] = [{"run_id": "old-run"}]
    trace.write_text(json.dumps(base) + "\n", encoding="utf-8")
    contaminated = harness._memory_isolation_evidence(
        ["arp-reset"],
        {"cold_start_toggle": 1},
        require_empty_session=True,
    )
    assert contaminated["passed"] is False


def test_trace_binding_uses_only_the_exact_client_run(monkeypatch, tmp_path):
    harness = _load_harness()
    client_run_id = "20260718-010203-client"
    # The real ARP file may be named a second before the client-visible run id.
    bound_trace_id = "arp-20260718-010202-bound"
    (tmp_path / f"{bound_trace_id}.jsonl").write_text("\n".join([
        json.dumps({
            "type": "arp_run_start",
            "run_id": bound_trace_id,
            "task_contract": {"task": "target task"},
        }),
        json.dumps({
            "type": "agent_start",
            "run_id": client_run_id,
            "task": "target task",
        }),
    ]) + "\n", encoding="utf-8")
    (tmp_path / "arp-20260718-010203-other.jsonl").write_text("\n".join([
        json.dumps({
            "type": "arp_run_start",
            "run_id": "arp-20260718-010203-other",
            "task_contract": {"task": "target task"},
        }),
        json.dumps({
            "type": "agent_start",
            "run_id": "20260718-010203-other",
            "task": "target task",
        }),
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(harness, "TRACE_ROOT", tmp_path)

    evidence = harness._bound_trace_evidence(client_run_id, "target task")

    assert evidence["passed"] is True
    assert evidence["trace_ids"] == [bound_trace_id]
    assert evidence["matching_trace_count"] == 1
    assert harness._bound_trace_evidence(client_run_id, "other task")["passed"] is False


def test_wait_for_result_binds_one_matching_run_and_rejects_replacement(monkeypatch):
    harness = _load_harness()

    def driver_for(statuses):
        driver = object.__new__(harness.ElectronClientDriver)
        queue = list(statuses)
        driver.runtime_status = lambda: queue.pop(0) if len(queue) > 1 else queue[0]
        driver.buttons = lambda: []
        driver.texts = lambda: []
        driver._topbar_status = lambda _task: ""
        return driver

    clock = [0.0]

    def now():
        clock[0] += 0.1
        return clock[0]

    monkeypatch.setattr(harness.time, "time", now)
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)

    completed = driver_for([
        {
            "running": True,
            "current_run": {
                "run_id": "arp-target", "goal": "target task", "status": "running",
            },
        },
        {
            "running": False,
            "current_run": {
                "run_id": "arp-target", "goal": "target task", "status": "done",
            },
        },
    ]).wait_for_result("target task", 5, previous_run_id="arp-old")
    assert completed["outcome"] == "terminal"
    assert completed["run_id"] == "arp-target"

    clock[0] = 0.0
    replaced = driver_for([
        {
            "running": True,
            "current_run": {
                "run_id": "arp-target", "goal": "target task", "status": "running",
            },
        },
        {
            "running": True,
            "current_run": {
                "run_id": "arp-other", "goal": "other task", "status": "running",
            },
        },
    ]).wait_for_result("target task", 5, previous_run_id="arp-old")
    assert replaced["outcome"] == "run_replaced"
    assert replaced["run_id"] == "arp-target"
    assert replaced["replacement_run_id"] == "arp-other"


def test_wait_for_result_returns_runtime_fail_without_waiting_for_timeout(monkeypatch):
    harness = _load_harness()
    driver = object.__new__(harness.ElectronClientDriver)
    statuses = [
        {
            "running": False,
            "current_run": {
                "run_id": "arp-target", "goal": "target task", "status": "fail",
            },
        },
    ]
    driver.runtime_status = lambda: statuses.pop(0) if len(statuses) > 1 else statuses[0]
    driver.buttons = lambda: []
    driver.texts = lambda: []
    driver._topbar_status = lambda _task: ""
    clock = [0.0]

    def now():
        clock[0] += 0.1
        return clock[0]

    monkeypatch.setattr(harness.time, "time", now)
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)

    result = driver.wait_for_result("target task", 300, previous_run_id="arp-old")

    assert result["outcome"] == "terminal"
    assert result["status"] == "failed"
    assert result["status_label"] == "fail"
    assert result["run_id"] == "arp-target"
    assert result["active_seen"] is False
    assert clock[0] < 5


def test_persistent_evidence_requires_a_bound_conversation_and_monitoring(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    trace = tmp_path / "arp-persistent.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "type": "agent_tool",
                    "tool": "open_conversation",
                    "result_meta": {"ok": True, "verified": True},
                },
                {
                    "type": "agent_tool",
                    "tool": "read_text",
                    "result_meta": {"ok": True},
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(harness, "TRACE_ROOT", tmp_path)

    evidence = harness._execution_evidence(["arp-persistent"])
    assert evidence["conversation_bound"] is True
    assert evidence["monitoring_started"] is True
    assert evidence["persistent_ready"] is True

    result = {
        "expected": "persistent",
        "result": {"outcome": "persistent_active", "status": "running"},
        "memory_isolation": {"passed": True},
        "trace_binding": {"passed": True},
        "execution_evidence": evidence,
        "task_verdict": {"source": "external_probe", "passed": True},
    }
    assert harness._case_execution_valid(result) is True
    assert harness._case_passed(result) is True
    result["execution_evidence"] = {"persistent_ready": False}
    assert harness._case_execution_valid(result) is False
    assert harness._case_passed(result) is False


def test_runtime_done_without_an_independent_oracle_is_not_a_pass():
    harness = _load_harness()
    result = {
        "expected": "terminal",
        "result": {"outcome": "terminal", "status": "done"},
        "memory_isolation": {"passed": True},
        "trace_binding": {"passed": True},
        "execution_evidence": {},
        "task_verdict": {"source": "none", "status": "unverified", "passed": False},
    }

    assert harness._case_execution_valid(result) is True
    assert harness._case_passed(result) is False

    result["task_verdict"] = {"source": "external_probe", "passed": True}
    assert harness._case_passed(result) is True

    result["task_verdict"] = {"source": "runtime_status", "passed": True}
    assert harness._case_passed(result) is False


def test_suite_preserves_only_case_two_to_case_three_short_term_context(
    monkeypatch,
    tmp_path,
):
    harness = _load_harness()
    calls: list[tuple[str, bool, str]] = []

    def fake_run_case(
        name,
        spec,
        *,
        output_root,
        reset_session,
        required_context_run_id="",
        **_kwargs,
    ):
        calls.append((name, reset_session, required_context_run_id))
        persistent = spec.get("expected") == "persistent"
        return {
            "name": name,
            "task": spec["task"],
            "expected": spec.get("expected", "terminal"),
            "result": {
                "outcome": "persistent_active" if persistent else "terminal",
                "status": "running" if persistent else "done",
                "run_id": f"run-{name}",
            },
            "memory_isolation": {"passed": True},
            "trace_binding": {"passed": True},
            "execution_evidence": {"persistent_ready": persistent},
            "task_verdict": {"source": "external_probe", "passed": True},
            "passed": True,
        }

    monkeypatch.setattr(harness, "run_case", fake_run_case)
    report = harness.run_suite("original", output_root=tmp_path)

    assert report["passed"] is True
    assert len(calls) == 11
    assert calls[0] == ("original-1", True, "")
    assert calls[1] == ("original-2", True, "")
    assert calls[2] == ("original-3", False, "run-original-2")
    assert all(reset is True for _name, reset, _context in calls[3:])
