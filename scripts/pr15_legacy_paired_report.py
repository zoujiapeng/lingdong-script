#!/usr/bin/env python3
"""Build the grounded paired PR #15 legacy regression report.

This script does not reinterpret model completion as success.  It compares only
the external verdicts already emitted by the visible Electron harness and the
deterministic legacy unknown/no-replay integration oracle.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


BASELINE_SHA = "dab1bbf88e506dd4947347f7da5127f3bf90ab18"
CANDIDATE_SHA = "10a707dcf1e535d54efc7579a55a43876943cdb4"
EXPECTED_CASES = (
    "click-double-click",
    "scroll-bidirectional",
    "text-hotkey",
    "dynamic-hold",
    "stop-cancel",
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


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
    result: dict[str, dict[str, Any]] = {}
    for item in report.get("results") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if name:
            result[name] = item
    return result


def _case_projection(item: dict[str, Any]) -> dict[str, Any]:
    client = item.get("client_result") or {}
    verdict = item.get("external_verdict") or {}
    return {
        "commit": (item.get("product") or {}).get("product_revision"),
        "seed": item.get("seed"),
        "budget": item.get("budget"),
        "outer_run_ids": item.get("outer_run_ids") or [],
        "child_run_ids": item.get("child_run_ids") or [],
        "trace": item.get("trace") or {},
        "screenshots": {
            "target_before": item.get("target_before"),
            "target_after": item.get("target_after"),
            "client": (client.get("screenshots") or {}),
        },
        "trusted_oracle": verdict.get("source"),
        "action_count": item.get("action_count"),
        "duplicate_input_count": item.get("duplicate_input_count"),
        "valid": item.get("valid") is True,
        "grounded_pass": item.get("grounded_verdict") is True,
        "oracle_status": verdict.get("oracle_status"),
        "errors": verdict.get("errors") or [],
        "invalid_reasons": verdict.get("invalid_reasons") or [],
        "first_failure_owner": item.get("first_failure_owner"),
        "report": item.get("report"),
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
    _check(
        (baseline_unknown.get("harness_revision") or {}).get("file_sha256")
        == (candidate_unknown.get("harness_revision") or {}).get("file_sha256"),
        "unknown oracle harness identity mismatch",
        integrity_errors,
    )
    _check(
        baseline_unknown.get("seed") == candidate_unknown.get("seed"),
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
        "schema": "pr15-legacy-paired-report/v1",
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
        "integrity_errors": integrity_errors,
        "invalid_cases": invalid_cases,
        "candidate_grounded_regression_cases": regression_cases,
        "visual_pairs": pairs,
        "unknown_no_replay": {
            "valid": unknown_valid,
            "baseline": {
                "comparison_role": baseline_unknown.get("comparison_role"),
                "call_counts": baseline_unknown.get("call_counts"),
                "duplicate_input_count": baseline_unknown.get(
                    "duplicate_input_count",
                ),
                "grounded_verdict": baseline_unknown_verdict,
                "report": str(baseline_unknown_path),
            },
            "candidate": {
                "comparison_role": candidate_unknown.get("comparison_role"),
                "call_counts": candidate_unknown.get("call_counts"),
                "duplicate_input_count": candidate_unknown.get(
                    "duplicate_input_count",
                ),
                "grounded_verdict": candidate_unknown_verdict,
                "report": str(candidate_unknown_path),
            },
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
