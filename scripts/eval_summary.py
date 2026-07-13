"""Print a compact summary of recent WinPilot GUI eval reports.

This reads local JSON files under python/winpilot_home/evals and does not
contact the running server. It is meant as a quick handoff/debug command for
agents and humans after a GUI eval suite finishes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
WINPILOT_PY = REPO_ROOT / "python" / "winpilot_home"
if str(WINPILOT_PY) not in sys.path:
    sys.path.insert(0, str(WINPILOT_PY))

from winpilot.eval import summary  # noqa: E402


def _fmt_cost(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "0.0000"


def _usage(report: dict[str, Any]) -> dict[str, Any]:
    return report.get("usage") or report.get("status_endpoint", {}).get("usage") or {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize recent WinPilot eval reports.")
    parser.add_argument("--limit", type=int, default=12, help="Number of reports to include.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table.")
    args = parser.parse_args()

    reports = summary.list_eval_reports(limit=max(1, args.limit))
    aggregate = summary.aggregate_reports(reports)
    if args.json:
        print(json.dumps(aggregate, ensure_ascii=False, indent=2))
        return 0

    print(
        f"evals={aggregate['total']} verified={aggregate['verified_successes']} "
        f"rate={aggregate['verified_success_rate']:.1%} "
        f"ungrounded={aggregate['ungrounded_count']} "
        f"duration={aggregate['total_duration_s']}s "
        f"cost=CNY {_fmt_cost(aggregate['total_cost_cny'])} "
        f"tokens={aggregate['total_prompt_tokens']}/{aggregate['total_completion_tokens']}"
    )
    print("")
    print(f"{'case/run':<28} {'status':<8} {'gt':<4} {'pol':<4} {'bud':<4} {'sec':>7} {'tools':>6} {'cost':>8} slowest")
    for report in reports:
        usage = _usage(report)
        slowest = (report.get("slow_tools") or report.get("summary", {}).get("slow_tools") or [{}])[0]
        label = str(report.get("case_id") or report.get("run_id") or "")[:28]
        status = str(report.get("status") or "?")[:8]
        if report.get("ground_truth_status") == "ungrounded":
            gt = "UNG"
        elif "ground_truth_passed" in report:
            gt = "PASS" if report.get("ground_truth_passed") else "FAIL"
        else:
            gt = "-"
        if "policy_passed" in report:
            pol = "PASS" if report.get("policy_passed") else "FAIL"
        else:
            pol = "-"
        budget = report.get("performance_budget")
        bud = "PASS" if isinstance(budget, dict) and budget.get("passed") is True else "FAIL"
        print(
            f"{label:<28} {status:<8} {gt:<4} {pol:<4} {bud:<4} "
            f"{float(report.get('duration_s') or 0):>7.1f} "
            f"{int(report.get('tool_count') or 0):>6} "
            f"{_fmt_cost(usage.get('cost_cny')):>8} "
            f"{slowest.get('tool', '-') or '-'}:{slowest.get('elapsed_ms', 0) or 0}ms"
        )
    if aggregate["slow_tools"]:
        top = aggregate["slow_tools"][0]
        print("")
        print(
            "top slow tool: "
            f"{top.get('tool')} {top.get('elapsed_ms')}ms "
            f"run={top.get('run_id')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
