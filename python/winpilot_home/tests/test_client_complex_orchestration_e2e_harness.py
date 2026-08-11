from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_harness():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "client_complex_orchestration_e2e_run.py"
    )
    spec = importlib.util.spec_from_file_location(
        "client_complex_orchestration_e2e_run_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_orchestration_evidence_reports_real_parallelism_and_recovery(tmp_path):
    harness = _load_harness()
    trace = tmp_path / "trace.jsonl"
    rows = [
        {"type": "vnext_model_call"},
        {
            "type": "vnext_model_response_shape",
            "finish_reason": "tool_calls",
            "tool_call_count": 2,
        },
        {
            "type": "action",
            "action": "shell",
            "command": "Get-Content acceptance.py",
            "operation_id": "read-source",
            "ts": 1.0,
        },
        {
            "type": "action",
            "action": "shell_blocked",
            "command": "Remove-Item protected.txt",
            "reason": "command rejected by shell boundary",
            "operation_id": "blocked-command",
            "ts": 1.1,
        },
        {
            "type": "action",
            "action": "shell_result",
            "exit_code": 0,
            "ok": True,
            "output_preview": "source text",
            "operation_id": "read-source",
            "ts": 1.2,
        },
        {"type": "vnext_model_call"},
        {
            "type": "vnext_model_response_shape",
            "finish_reason": "tool_calls",
            "tool_call_count": 2,
        },
        {
            "type": "action",
            "action": "shell",
            "command": "python acceptance.py",
            "operation_id": "accept-change",
            "ts": 2.0,
        },
        {
            "type": "action",
            "action": "shell",
            "command": "python -m pytest -q",
            "operation_id": "pytest",
            "ts": 2.05,
        },
        {
            "type": "action",
            "action": "shell_result",
            "exit_code": 0,
            "ok": True,
            "output_preview": "1 passed",
            "operation_id": "pytest",
            "ts": 2.3,
        },
        {
            "type": "action",
            "action": "shell_result",
            "exit_code": 0,
            "ok": True,
            "output_preview": "revision changed\nEXIT_CODE=23",
            "operation_id": "accept-change",
            "ts": 2.4,
        },
        {"type": "vnext_model_call"},
        {
            "type": "vnext_model_response_shape",
            "finish_reason": "tool_calls",
            "tool_call_count": 1,
        },
        {
            "type": "action",
            "action": "shell",
            "command": "python acceptance.py",
            "operation_id": "accept-final",
            "ts": 3.0,
        },
        {
            "type": "action",
            "action": "shell_result",
            "exit_code": 0,
            "ok": True,
            "output_preview": "ACCEPTANCE_OK",
            "operation_id": "accept-final",
            "ts": 3.1,
        },
        {"type": "vnext_model_call"},
        {
            "type": "vnext_model_response_shape",
            "finish_reason": "stop",
            "tool_call_count": 0,
        },
    ]
    trace.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    evidence = harness.orchestration_evidence(
        {"trace_binding": {"trace_file": str(trace)}}
    )

    assert evidence["model_calls"] == 4
    assert evidence["shell_attempts"] == 5
    assert evidence["shell_failures"] == 1
    assert evidence["multi_tool_response_batches"] == [2, 2]
    assert evidence["parallel_shell_batches"] == [2]
    assert evidence["max_parallel_shells"] == 2
    assert evidence["max_tools_in_one_model_response"] == 2
    assert evidence["finish_reasons"] == {"tool_calls": 3, "stop": 1}
    assert evidence["truncated_model_responses"] == 0
    assert evidence["acceptance_exit_codes"] == [23, 0]
    assert evidence["pytest_exit_codes"] == [0]
    assert evidence["timeline"][1]["blocked"] is True
    assert evidence["timeline"][1]["output_preview"] == (
        "command rejected by shell boundary"
    )
    assert evidence["timeline"][2]["reported_exit_code"] == 23
    assert evidence["parse_errors"] == []


def test_orchestration_evidence_keeps_legacy_pairing_and_flags_unmatched(
    tmp_path,
):
    harness = _load_harness()
    trace = tmp_path / "mixed-trace.jsonl"
    rows = [
        {
            "type": "action",
            "action": "shell",
            "command": "python -m pytest -q",
            "ts": 1.0,
        },
        {
            "type": "action",
            "action": "shell_result",
            "operation_id": "unknown",
            "exit_code": 1,
            "ok": False,
            "ts": 1.1,
        },
        {
            "type": "action",
            "action": "shell_result",
            "exit_code": 0,
            "ok": True,
            "ts": 1.2,
        },
        {
            "type": "action",
            "action": "shell",
            "command": "Write-Output dangling",
            "operation_id": "dangling",
            "ts": 2.0,
        },
    ]
    trace.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    evidence = harness.orchestration_evidence(
        {"trace_binding": {"trace_file": str(trace)}}
    )

    assert evidence["shell_attempts"] == 2
    assert evidence["shell_failures"] == 1
    assert evidence["pytest_exit_codes"] == [0]
    assert evidence["timeline"][0]["command"] == "python -m pytest -q"
    assert len(evidence["parse_errors"]) == 2
    assert "unknown" in evidence["parse_errors"][0]
    assert "dangling" in evidence["parse_errors"][1]


def test_ui_activity_probe_does_not_override_task_completion(
    tmp_path,
    monkeypatch,
):
    harness = _load_harness()
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "request.txt").write_text("task", encoding="utf-8")
    monkeypatch.setattr(
        harness,
        "parse_args",
        lambda: SimpleNamespace(seed=1, timeout=30, output_root=tmp_path),
    )
    monkeypatch.setattr(
        harness,
        "prepare_fixture",
        lambda _seed: (fixture, "token", {}),
    )
    monkeypatch.setattr(
        harness.client_harness,
        "run_case",
        lambda *_args, **_kwargs: {
            "passed": True,
            "after_submit_handoff": {"passed": False},
            "trace_binding": {},
        },
    )

    assert harness.main() == 0
    report = next(tmp_path.glob("*-suite/suite-report.json"))
    assert json.loads(report.read_text(encoding="utf-8"))["passed"] is True


def test_live_ui_probe_requires_interleaved_activity_and_task_board():
    harness = _load_harness()

    class Driver:
        def texts(self):
            return ["模型思路", "动态任务板", "开始执行", "执行结果"]

        def capture_window(self, name):
            return name

    verdict = harness.observe_live_ui(Driver(), {})

    assert verdict["passed"] is True
    assert all(verdict["observed"].values())
    assert verdict["screenshot"] == "live-activity.png"


def test_live_ui_probe_accepts_task_board_as_the_models_progress_signal():
    harness = _load_harness()

    class Driver:
        def texts(self):
            return ["动态任务板", "开始执行", "执行结果"]

        def capture_window(self, name):
            return name

    verdict = harness.observe_live_ui(Driver(), {})

    assert verdict["passed"] is True
    assert verdict["observed"]["model_narration"] is False
    assert verdict["observed"]["task_board"] is True
