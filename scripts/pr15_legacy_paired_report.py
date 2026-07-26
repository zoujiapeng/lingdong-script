#!/usr/bin/env python3
"""Build the grounded paired PR #15 legacy regression report.

This script does not reinterpret model completion as success.  It compares only
the external verdicts already emitted by the visible Electron harness and the
deterministic legacy unknown/no-replay integration oracle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[1]
BASELINE_SHA = "dab1bbf88e506dd4947347f7da5127f3bf90ab18"
CANDIDATE_SHA = "10a707dcf1e535d54efc7579a55a43876943cdb4"
UNKNOWN_SEED = 22022
EXPECTED_CASES = (
    "click-double-click",
    "scroll-bidirectional",
    "text-hotkey",
    "dynamic-hold",
    "stop-cancel",
)
PHYSICAL_ACTIONS = {
    "click",
    "double_click",
    "type_text",
    "hotkey",
    "scroll",
    "move_mouse",
    "mouse_down",
    "mouse_up",
    "drag",
    "draw_strokes",
    "paste_image_file",
}


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expected_adjudication_dependencies() -> dict[str, str]:
    paths = (
        WORKSPACE / "scripts" / "pr15_legacy_saved_artifact_oracle.py",
        WORKSPACE / "scripts" / "client_visual_e2e_run.py",
        WORKSPACE / "scripts" / "client_e2e_run.py",
    )
    return {
        str(path.relative_to(WORKSPACE)): _sha256(path)
        for path in paths
    }


def _product_identity(report: dict[str, Any]) -> dict[str, Any]:
    product = report.get("product") or {}
    return {
        key: product.get(key)
        for key in (
            "config_sha256",
            "provider",
            "base_url",
            "model",
            "request_timeout_s",
            "request_total_timeout_s",
            "configured_max_retries",
        )
    }


def _case_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items = [
        item
        for item in (report.get("results") or [])
        if isinstance(item, dict) and item.get("name")
    ]
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        name = str(item.get("name") or "")
        if name in result:
            raise ValueError(f"duplicate suite case: {name}")
        result[name] = item
    if len(items) != len(EXPECTED_CASES):
        raise ValueError("suite case count mismatch")
    return result


def _trace_action_metrics(trace_file: str) -> dict[str, Any]:
    executed: list[str] = []
    proposed: list[str] = []
    batch_starts: list[str] = []
    parse_errors: list[str] = []
    path = Path(trace_file)
    if not trace_file or not path.is_file():
        return {
            "executed_physical_action_count": None,
            "executed_physical_actions": [],
            "proposed_physical_action_attempt_count": None,
            "batch_physical_action_start_count": None,
            "parse_errors": ["bound trace file is unavailable"],
        }
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        try:
            event = json.loads(raw)
        except Exception as error:
            parse_errors.append(
                f"line {line_number}: {type(error).__name__}: {error}",
            )
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        action = str(event.get("action") or "")
        envelope = event.get("action_envelope") or {}
        tool = str(envelope.get("type") or event.get("tool") or "")
        if event_type == "action" and action in PHYSICAL_ACTIONS:
            executed.append(action)
        elif event_type == "agent_tool_start" and tool in PHYSICAL_ACTIONS:
            proposed.append(tool)
        elif (
            event_type == "agent_batch_action_start"
            and tool in PHYSICAL_ACTIONS
        ):
            batch_starts.append(tool)
    return {
        "executed_physical_action_count": len(executed),
        "executed_physical_actions": executed,
        "proposed_physical_action_attempt_count": len(proposed),
        "batch_physical_action_start_count": len(batch_starts),
        "parse_errors": parse_errors,
    }


def _case_projection(item: dict[str, Any]) -> dict[str, Any]:
    client = item.get("client_result") or {}
    verdict = item.get("external_verdict") or {}
    trace = item.get("trace") or {}
    action_metrics = _trace_action_metrics(str(trace.get("file") or ""))
    return {
        "commit": (item.get("product") or {}).get("product_revision"),
        "seed": item.get("seed"),
        "task": item.get("task"),
        "budget": item.get("budget"),
        "entrypoint": item.get("entrypoint"),
        "cold_start_required": item.get("cold_start_required"),
        "outer_run_ids": item.get("outer_run_ids") or [],
        "child_run_ids": item.get("child_run_ids") or [],
        "trace": trace,
        "screenshots": {
            "target_before": item.get("target_before"),
            "target_after": item.get("target_after"),
            "client": (client.get("screenshots") or {}),
        },
        "trusted_oracle": verdict.get("source"),
        "action_count": action_metrics["executed_physical_action_count"],
        "action_metrics": action_metrics,
        "legacy_reported_action_count": item.get("action_count"),
        "duplicate_input_count": item.get("duplicate_input_count"),
        "valid": item.get("valid") is True,
        "grounded_pass": item.get("grounded_verdict") is True,
        "oracle_status": verdict.get("oracle_status"),
        "errors": verdict.get("errors") or [],
        "invalid_reasons": verdict.get("invalid_reasons") or [],
        "first_failure_owner": item.get("first_failure_owner"),
        "report": item.get("report"),
        "posthoc_adjudication": item.get("posthoc_adjudication"),
    }


def _unknown_projection(item: dict[str, Any], path: Path) -> dict[str, Any]:
    verdict = item.get("grounded_verdict") or {}
    return {
        "commit": (item.get("product") or {}).get("actual_commit"),
        "seed": item.get("seed"),
        "entrypoint": item.get("entrypoint"),
        "outer_run_ids": item.get("outer_run_ids") or [],
        "child_run_ids": item.get("child_run_ids") or [],
        "runtime_run_identity": item.get("runtime_run_identity"),
        "trace": {
            "kind": "deterministic_in_process_integration_oracle",
            "report": str(path),
        },
        "screenshots": item.get("screenshots"),
        "trusted_oracle": verdict.get("proof_basis"),
        "action_count": item.get("action_count"),
        "duplicate_input_count": item.get("duplicate_input_count"),
        "retry_budget": item.get("retry_budget"),
        "call_counts": item.get("call_counts"),
        "first_failure_owner": item.get("first_failure_owner"),
        "comparison_role": item.get("comparison_role"),
        "grounded_verdict": verdict,
        "report": str(path),
    }


def _check(condition: bool, name: str, errors: list[str]) -> None:
    if not condition:
        errors.append(name)


def build_report(
    baseline_suite_path: Path,
    candidate_suite_path: Path,
    baseline_unknown_path: Path,
    candidate_unknown_path: Path,
) -> dict[str, Any]:
    baseline_suite = _read(baseline_suite_path)
    candidate_suite = _read(candidate_suite_path)
    baseline_unknown = _read(baseline_unknown_path)
    candidate_unknown = _read(candidate_unknown_path)
    baseline_cases = _case_map(baseline_suite)
    candidate_cases = _case_map(candidate_suite)
    integrity_errors: list[str] = []

    _check(
        tuple(baseline_cases) == EXPECTED_CASES,
        "baseline case order/identity mismatch",
        integrity_errors,
    )
    _check(
        tuple(candidate_cases) == EXPECTED_CASES,
        "candidate case order/identity mismatch",
        integrity_errors,
    )
    baseline_product = baseline_suite.get("product") or {}
    candidate_product = candidate_suite.get("product") or {}
    _check(
        baseline_product.get("product_revision") == BASELINE_SHA,
        "baseline product commit mismatch",
        integrity_errors,
    )
    _check(
        candidate_product.get("product_revision") == CANDIDATE_SHA,
        "candidate product commit mismatch",
        integrity_errors,
    )
    _check(
        baseline_product.get("tracked_worktree_clean") is True
        and candidate_product.get("tracked_worktree_clean") is True,
        "one product worktree was not clean",
        integrity_errors,
    )
    _check(
        _product_identity(baseline_suite) == _product_identity(candidate_suite),
        "provider/model/config identity mismatch",
        integrity_errors,
    )
    baseline_harness = baseline_suite.get("harness") or {}
    candidate_harness = candidate_suite.get("harness") or {}
    _check(
        baseline_harness.get("revision") == candidate_harness.get("revision")
        and baseline_harness.get("artifact_sha256")
        == candidate_harness.get("artifact_sha256"),
        "visible harness identity mismatch",
        integrity_errors,
    )
    baseline_posthoc = baseline_suite.get("posthoc_adjudication") or {}
    candidate_posthoc = candidate_suite.get("posthoc_adjudication") or {}
    _check(
        baseline_posthoc.get("schema")
        == candidate_posthoc.get("schema")
        == "pr15-saved-artifact-suite-adjudication/v1"
        and bool(baseline_posthoc.get("oracle_script_sha256"))
        and baseline_posthoc.get("oracle_script_sha256")
        == candidate_posthoc.get("oracle_script_sha256"),
        "saved-artifact oracle identity mismatch",
        integrity_errors,
    )
    expected_dependencies = _expected_adjudication_dependencies()
    _check(
        baseline_posthoc.get("adjudication_dependency_sha256")
        == expected_dependencies
        and candidate_posthoc.get("adjudication_dependency_sha256")
        == expected_dependencies,
        "saved-artifact adjudication dependency mismatch",
        integrity_errors,
    )
    _check(
        baseline_posthoc.get("acquisition_harness_revision")
        == baseline_harness.get("revision")
        and candidate_posthoc.get("acquisition_harness_revision")
        == candidate_harness.get("revision")
        and baseline_posthoc.get("acquisition_harness_artifact_sha256")
        == baseline_harness.get("artifact_sha256")
        and candidate_posthoc.get("acquisition_harness_artifact_sha256")
        == candidate_harness.get("artifact_sha256"),
        "saved-artifact acquisition harness binding mismatch",
        integrity_errors,
    )
    _check(
        (baseline_unknown.get("harness_revision") or {}).get("file_sha256")
        == (candidate_unknown.get("harness_revision") or {}).get("file_sha256"),
        "unknown oracle harness identity mismatch",
        integrity_errors,
    )
    _check(
        baseline_unknown.get("seed")
        == candidate_unknown.get("seed")
        == UNKNOWN_SEED,
        "unknown oracle seed mismatch",
        integrity_errors,
    )
    _check(
        (baseline_unknown.get("product") or {}).get("actual_commit") == BASELINE_SHA
        and (candidate_unknown.get("product") or {}).get("actual_commit")
        == CANDIDATE_SHA,
        "unknown oracle product commit mismatch",
        integrity_errors,
    )
    baseline_unknown_calls = baseline_unknown.get("call_counts") or {}
    candidate_unknown_calls = candidate_unknown.get("call_counts") or {}
    baseline_retry = baseline_unknown.get("retry_budget") or {}
    candidate_retry = candidate_unknown.get("retry_budget") or {}
    _check(
        baseline_unknown.get("comparison_role")
        == "baseline_legacy_fallback_reference"
        and baseline_unknown.get("entrypoint")
        == "legacy_toolsession_run_action_batch_integration"
        and baseline_unknown.get("oracle_execution_passed") is True
        and (baseline_unknown.get("product") or {}).get(
            "clean_tracked_worktree",
        )
        is True
        and baseline_unknown_calls.get("selected_backend_calls") == 1
        and baseline_unknown_calls.get("typed_scroll_backend_returns") == 0
        and baseline_unknown_calls.get("physical_pointer_moves") == 1
        and baseline_unknown_calls.get("physical_wheel_calls") == 1
        and baseline_unknown_calls.get("cursor_restores") == 1
        and baseline_unknown_calls.get("mechanism_switches") == 1
        and baseline_unknown_calls.get("mutation_replays") == 1
        and baseline_unknown_calls.get("controller_action_count") == 1
        and baseline_retry.get("maximum") == 0
        and baseline_retry.get("zero_budget_proved") is True
        and baseline_unknown.get("duplicate_input_count") == 1,
        "baseline unknown reference contract mismatch",
        integrity_errors,
    )
    candidate_receipt = candidate_unknown.get("raw_receipt") or {}
    candidate_transaction = candidate_unknown.get("transaction") or {}
    _check(
        candidate_unknown.get("comparison_role")
        == "candidate_unknown_no_replay_subject"
        and candidate_unknown.get("entrypoint")
        == "legacy_toolsession_run_action_batch_integration"
        and candidate_unknown.get("oracle_execution_passed") is True
        and (candidate_unknown.get("product") or {}).get(
            "clean_tracked_worktree",
        )
        is True
        and candidate_unknown_calls.get("selected_backend_calls") == 1
        and candidate_unknown_calls.get("typed_scroll_backend_returns") == 1
        and candidate_unknown_calls.get("physical_pointer_moves") == 0
        and candidate_unknown_calls.get("physical_wheel_calls") == 0
        and candidate_unknown_calls.get("cursor_restores") == 0
        and candidate_unknown_calls.get("mechanism_switches") == 0
        and candidate_unknown_calls.get("mutation_replays") == 0
        and candidate_unknown_calls.get("controller_action_count") == 1
        and candidate_retry.get("maximum") == 0
        and candidate_retry.get("zero_budget_proved") is True
        and candidate_unknown.get("duplicate_input_count") == 0
        and candidate_receipt.get("outcome") == "outcome_unknown"
        and candidate_transaction.get("planned_action_count") == 1
        and candidate_transaction.get("attempted_action_count") == 1
        and candidate_transaction.get("committed_action_count") == 0
        and candidate_transaction.get("remaining_action_count") == 1,
        "candidate unknown no-replay contract mismatch",
        integrity_errors,
    )

    pairs: list[dict[str, Any]] = []
    regression_cases: list[str] = []
    invalid_cases: list[str] = []
    for name in EXPECTED_CASES:
        baseline = baseline_cases.get(name, {})
        candidate = candidate_cases.get(name, {})
        _check(
            baseline.get("seed") == candidate.get("seed")
            and baseline.get("task") == candidate.get("task")
            and baseline.get("budget") == candidate.get("budget"),
            f"{name} seed/task/budget mismatch",
            integrity_errors,
        )
        baseline_projection = _case_projection(baseline)
        candidate_projection = _case_projection(candidate)
        for role, case, projection, expected_commit, suite_product, suite_harness in (
            (
                "baseline",
                baseline,
                baseline_projection,
                BASELINE_SHA,
                baseline_product,
                baseline_harness,
            ),
            (
                "candidate",
                candidate,
                candidate_projection,
                CANDIDATE_SHA,
                candidate_product,
                candidate_harness,
            ),
        ):
            case_product = case.get("product") or {}
            case_harness = case.get("harness") or {}
            screenshots = projection["screenshots"]
            client_screenshots = screenshots["client"]
            target_after = screenshots["target_after"] or {}
            _check(
                projection["commit"] == expected_commit
                and _product_identity(case) == _product_identity({
                    "product": suite_product,
                }),
                f"{name} {role} case product identity mismatch",
                integrity_errors,
            )
            _check(
                case_harness.get("revision") == suite_harness.get("revision")
                and case_harness.get("artifact_sha256")
                == suite_harness.get("artifact_sha256"),
                f"{name} {role} case harness identity mismatch",
                integrity_errors,
            )
            _check(
                projection["entrypoint"] == "electron_client_windows_uia"
                and projection["cold_start_required"] is True,
                f"{name} {role} legacy entrypoint/cold-start mismatch",
                integrity_errors,
            )
            _check(
                bool(projection["outer_run_ids"])
                and bool(projection["child_run_ids"])
                and bool((projection["trace"] or {}).get("ids")),
                f"{name} {role} run identity is incomplete",
                integrity_errors,
            )
            _check(
                Path(str(screenshots["target_before"] or "")).is_file()
                and Path(str(target_after.get("path") or "")).is_file()
                and target_after.get("print_window_succeeded") is True
                and target_after.get("visually_nonblank") is True
                and all(
                    Path(str(client_screenshots.get(key) or "")).is_file()
                    for key in (
                        "before_submit",
                        "desktop_terminal",
                        "client_terminal",
                    )
                )
                and Path(str(projection["report"] or "")).is_file(),
                f"{name} {role} report/screenshot evidence is incomplete",
                integrity_errors,
            )
        for role, projection in (
            ("baseline", baseline_projection),
            ("candidate", candidate_projection),
        ):
            metrics = projection["action_metrics"]
            _check(
                metrics["executed_physical_action_count"] is not None
                and not metrics["parse_errors"],
                f"{name} {role} bound trace action audit failed",
                integrity_errors,
            )
        if name in {"text-hotkey", "dynamic-hold", "stop-cancel"}:
            _check(
                bool(baseline_projection["posthoc_adjudication"])
                and bool(candidate_projection["posthoc_adjudication"]),
                f"{name} saved-artifact adjudication is missing",
                integrity_errors,
            )
        valid = bool(
            baseline_projection["valid"] and candidate_projection["valid"]
        )
        regression = bool(
            valid
            and baseline_projection["grounded_pass"]
            and not candidate_projection["grounded_pass"]
        )
        if not valid:
            invalid_cases.append(name)
        if regression:
            regression_cases.append(name)
        pairs.append({
            "name": name,
            "same_seed_task_budget": (
                baseline.get("seed") == candidate.get("seed")
                and baseline.get("task") == candidate.get("task")
                and baseline.get("budget") == candidate.get("budget")
            ),
            "valid": valid,
            "candidate_grounded_regression": regression,
            "baseline": baseline_projection,
            "candidate": candidate_projection,
        })

    baseline_unknown_verdict = baseline_unknown.get("grounded_verdict") or {}
    candidate_unknown_verdict = candidate_unknown.get("grounded_verdict") or {}
    unknown_valid = bool(
        baseline_unknown_verdict.get("role_expectation_pass") is True
        and candidate_unknown_verdict.get("role_expectation_pass") is True
        and candidate_unknown_verdict.get("unknown_no_replay_pass") is True
    )
    if not unknown_valid:
        invalid_cases.append("unknown-no-replay")

    matrix_valid = not integrity_errors and not invalid_cases
    no_new_regression = bool(
        matrix_valid and not regression_cases and unknown_valid
    )
    return {
        "schema": "pr15-legacy-paired-report/v2",
        "report_builder": {
            "file": str(Path(__file__).resolve()),
            "sha256": _sha256(Path(__file__).resolve()),
        },
        "baseline_commit": BASELINE_SHA,
        "candidate_commit": CANDIDATE_SHA,
        "inputs": {
            "baseline_suite": str(baseline_suite_path),
            "candidate_suite": str(candidate_suite_path),
            "baseline_unknown": str(baseline_unknown_path),
            "candidate_unknown": str(candidate_unknown_path),
        },
        "shared_product_configuration": _product_identity(baseline_suite),
        "shared_harness": baseline_harness,
        "saved_artifact_adjudication": {
            "baseline": baseline_suite.get("posthoc_adjudication"),
            "candidate": candidate_suite.get("posthoc_adjudication"),
        },
        "integrity_errors": integrity_errors,
        "invalid_cases": invalid_cases,
        "candidate_grounded_regression_cases": regression_cases,
        "visual_pairs": pairs,
        "unknown_no_replay": {
            "valid": unknown_valid,
            "baseline": _unknown_projection(
                baseline_unknown,
                baseline_unknown_path,
            ),
            "candidate": _unknown_projection(
                candidate_unknown,
                candidate_unknown_path,
            ),
        },
        "matrix_valid": matrix_valid,
        "no_new_grounded_regression": no_new_regression,
        "grounded_verdict": (
            "no_new_candidate_regression"
            if no_new_regression
            else "invalid_matrix"
            if not matrix_valid
            else "candidate_regression"
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-suite", type=Path, required=True)
    parser.add_argument("--candidate-suite", type=Path, required=True)
    parser.add_argument("--baseline-unknown", type=Path, required=True)
    parser.add_argument("--candidate-unknown", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(
        args.baseline_suite.resolve(),
        args.candidate_suite.resolve(),
        args.baseline_unknown.resolve(),
        args.candidate_unknown.resolve(),
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "report": str(output),
        "matrix_valid": report["matrix_valid"],
        "grounded_verdict": report["grounded_verdict"],
        "candidate_grounded_regression_cases": (
            report["candidate_grounded_regression_cases"]
        ),
    }, ensure_ascii=False))
    return 0 if report["no_new_grounded_regression"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
