from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


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
            "tool_call_count": 3,
        },
        {
            "type": "action",
            "action": "shell",
            "command": "Get-Content acceptance.py",
        },
        {
            "type": "action",
            "action": "shell_result",
            "exit_code": 0,
            "output_preview": "source text",
        },
        {
            "type": "action",
            "action": "shell",
            "command": "python acceptance.py",
        },
        {
            "type": "action",
            "action": "shell_result",
            "exit_code": 23,
            "output_preview": "revision changed",
        },
        {
            "type": "action",
            "action": "shell",
            "command": "python -m pytest -q",
        },
        {
            "type": "action",
            "action": "shell_result",
            "exit_code": 0,
            "output_preview": "1 passed",
        },
        {
            "type": "action",
            "action": "shell",
            "command": "python acceptance.py",
        },
        {
            "type": "action",
            "action": "shell_result",
            "exit_code": 0,
            "output_preview": "ACCEPTANCE_OK",
        },
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

    assert evidence["model_calls"] == 1
    assert evidence["shell_attempts"] == 4
    assert evidence["shell_failures"] == 1
    assert evidence["parallel_tool_batches"] == [3]
    assert evidence["max_tools_in_one_model_response"] == 3
    assert evidence["finish_reasons"] == {"tool_calls": 1, "stop": 1}
    assert evidence["truncated_model_responses"] == 0
    assert evidence["acceptance_exit_codes"] == [23, 0]
    assert evidence["pytest_exit_codes"] == [0]
    assert evidence["timeline"][1]["output_preview"] == "revision changed"
    assert evidence["parse_errors"] == []


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
