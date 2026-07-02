"""Run one real WinPilot GUI task and write a compact evidence report.

This script assumes the WinPilot server is already running, usually through the
Electron frontend. It does not bypass the GUI path: it submits the same
``/api/chat`` request the frontend uses, waits for completion, reads the trace,
and writes a JSON report that points at screenshots and summarizes suspicious
done-gate / tool behavior.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_OUT_DIR = Path("python/winpilot_home/evals")
REPO_ROOT = Path(__file__).resolve().parents[1]
WINPILOT_PY = REPO_ROOT / "python" / "winpilot_home"
if str(WINPILOT_PY) not in sys.path:
    sys.path.insert(0, str(WINPILOT_PY))

SUITES: dict[str, list[dict[str, Any]]] = {
    "gui-smoke": [
        {
            "id": "calculator_sum",
            "task": "用计算器计算86和79的和，确认结果是165。必须通过GUI操作。",
            "timeout_s": 180,
        },
        {
            "id": "notepad_save",
            "task": (
                "打开记事本，输入两行文字：第一行 WinPilot E2E 记录，"
                "第二行 时间测试通过。然后通过GUI另存为桌面上的 "
                "WinPilotE2E_note.txt。必须通过GUI操作，最后确认文件已保存。"
            ),
            "timeout_s": 360,
            "cleanup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotE2E_note.txt\" -Force -ErrorAction SilentlyContinue"
            ],
        },
    ],
    "gui-manipulation": [
        {
            "id": "desktop_drag",
            "task": (
                "把桌面上的 WinPilotPerfDrag_Eval 文件夹图标通过鼠标拖动到桌面右上角附近。"
                "不要打开它，不要删除它，不要移动其他图标。如果当前窗口遮挡桌面，先显示桌面。"
                "必须通过GUI拖动操作完成。"
            ),
            "timeout_s": 240,
            "setup": [
                "$p = Join-Path $env:USERPROFILE 'Desktop\\WinPilotPerfDrag_Eval'; "
                "New-Item -ItemType Directory -Force -Path $p | Out-Null; "
                "Set-Content -LiteralPath (Join-Path $p 'marker.txt') -Value 'WinPilot drag eval' -Encoding UTF8"
            ],
            "cleanup": [
                "Remove-Item -LiteralPath \"$env:USERPROFILE\\Desktop\\WinPilotPerfDrag_Eval\" -Recurse -Force -ErrorAction SilentlyContinue"
            ],
        },
        {
            "id": "paint_triangle",
            "task": "在当前画图画布空白处画一个小三角形，不用保存。必须通过GUI操作。如果画图没打开就先打开画图。",
            "timeout_s": 240,
        },
    ],
}

DEFAULT_BUDGETS = {
    "duration_s": 240.0,
    "tool_count": 45,
    "observe_avg_ms": 2500.0,
    "cost_cny": 1.5,
}


def _request_json(method: str, url: str, payload: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else None


def _run_powershell(command: str) -> None:
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _load_summary_module():
    try:
        from winpilot.eval import summary as summary_mod
        return summary_mod
    except Exception:
        return None


def _summarize_trace(events: list[dict[str, Any]]) -> dict[str, Any]:
    summary_mod = _load_summary_module()
    if summary_mod is not None:
        return summary_mod.summarize_events(events)
    tool_events = [event for event in events if event.get("type") == "agent_tool"]
    screenshots = [
        event.get("screenshot") for event in events
        if event.get("screenshot")
    ]
    done_checks = [
        {
            "passed": event.get("passed"),
            "reason": event.get("reason", ""),
        }
        for event in events
        if event.get("type") == "done_check"
    ]
    final = next(
        (event for event in reversed(events) if event.get("type") == "agent_done"),
        {},
    )
    tool_sequence = [
        {
            "tool": event.get("tool"),
            "elapsed_ms": event.get("elapsed_ms"),
            "terminal": event.get("terminal"),
            "result_preview": event.get("result_preview", ""),
            "screenshot": event.get("screenshot"),
        }
        for event in tool_events
    ]
    return {
        "status": final.get("status", ""),
        "result": final.get("result", ""),
        "tool_count": len(tool_events),
        "tool_sequence": tool_sequence,
        "screenshots": screenshots,
        "done_checks": done_checks,
        "rejected_done_count": sum(1 for item in done_checks if item.get("passed") is False),
    }


def _budget_result(report: dict[str, Any], budgets: dict[str, float]) -> dict[str, Any]:
    summary = report.get("summary") or report.get("trace_summary") or {}
    usage = report.get("usage") or report.get("status_endpoint", {}).get("usage") or {}
    observe = (summary.get("tool_latency") or {}).get("observe") or {}
    checks = {
        "duration_s": float(report.get("duration_s") or 0) <= float(budgets["duration_s"]),
        "tool_count": int(report.get("tool_count") or 0) <= int(budgets["tool_count"]),
        "observe_avg_ms": float(observe.get("avg_ms") or 0) <= float(budgets["observe_avg_ms"]),
        "cost_cny": float(usage.get("cost_cny") or 0) <= float(budgets["cost_cny"]),
    }
    return {"passed": all(checks.values()), "checks": checks, "budgets": budgets}


def run_eval(
    base_url: str,
    task: str,
    out_dir: Path,
    timeout_s: float,
    case_id: str = "",
    budgets: dict[str, float] | None = None,
) -> Path:
    started_at = time.time()
    started = _request_json("POST", f"{base_url}/api/chat", {"task": task})
    run_id = str(started["run_id"])
    deadline = time.time() + timeout_s
    last_status: dict[str, Any] = {}
    timed_out = False
    while time.time() < deadline:
        last_status = _request_json("GET", f"{base_url}/api/status")
        if not last_status.get("running"):
            break
        time.sleep(1.0)
    else:
        _request_json("POST", f"{base_url}/api/stop")
        timed_out = True
        time.sleep(0.5)
        last_status = _request_json("GET", f"{base_url}/api/status")

    time.sleep(0.5)
    events = _request_json("GET", f"{base_url}/api/trace/{run_id}")
    if not isinstance(events, list):
        raise RuntimeError(f"Trace response for {run_id} was not a list")
    summary = _summarize_trace(events)
    if timed_out and not summary.get("status"):
        summary["status"] = "timeout"
    duration_s = round(time.time() - started_at, 2)
    report = {
        "run_id": run_id,
        "case_id": case_id,
        "task": task,
        "base_url": base_url,
        "timed_out": timed_out,
        "status": summary.get("status", ""),
        "result": summary.get("result", ""),
        "duration_s": duration_s,
        "tool_count": summary.get("tool_count", 0),
        "done_checks": summary.get("done_checks", []),
        "rejected_done_count": summary.get("rejected_done_count", 0),
        "status_endpoint": last_status,
        "trace_file": f"python/winpilot_home/traces/{run_id}.jsonl",
        "screenshot_dir": f"python/winpilot_home/traces/{run_id}/",
        "summary": summary,
    }
    if budgets:
        report["performance_budget"] = _budget_result(report, budgets)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_id}.eval.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_suite(base_url: str, suite_name: str, out_dir: Path, timeout_s: float,
              budgets: dict[str, float]) -> Path:
    cases = SUITES.get(suite_name)
    if not cases:
        raise SystemExit(f"Unknown suite {suite_name!r}. Available: {', '.join(sorted(SUITES))}")
    reports: list[dict[str, Any]] = []
    paths: list[str] = []
    for case in cases:
        for command in case.get("setup", []):
            _run_powershell(command)
        try:
            path = run_eval(
                base_url,
                str(case["task"]),
                out_dir,
                float(case.get("timeout_s") or timeout_s),
                case_id=str(case.get("id") or ""),
                budgets=budgets,
            )
            paths.append(str(path))
            reports.append(json.loads(path.read_text(encoding="utf-8")))
        finally:
            for command in case.get("cleanup", []):
                _run_powershell(command)

    summary_mod = _load_summary_module()
    aggregate = summary_mod.aggregate_reports(reports) if summary_mod else {
        "total": len(reports),
        "reports": reports,
    }
    aggregate["suite"] = suite_name
    aggregate["report_files"] = paths
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suite_path = out_dir / f"{stamp}-{suite_name}.suite.eval.json"
    suite_path.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
    return suite_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real WinPilot GUI eval task.")
    parser.add_argument("task", nargs="?", help="Task text to submit through /api/chat.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"WinPilot server URL. Default: {DEFAULT_BASE_URL}")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                        help=f"Report output directory. Default: {DEFAULT_OUT_DIR}")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="Maximum seconds to wait before stopping the run.")
    parser.add_argument("--suite", choices=sorted(SUITES),
                        help="Run a built-in GUI eval suite through /api/chat.")
    parser.add_argument("--list-suites", action="store_true",
                        help="List built-in suites and exit.")
    parser.add_argument("--budget-duration", type=float, default=DEFAULT_BUDGETS["duration_s"])
    parser.add_argument("--budget-tools", type=int, default=DEFAULT_BUDGETS["tool_count"])
    parser.add_argument("--budget-observe-avg-ms", type=float, default=DEFAULT_BUDGETS["observe_avg_ms"])
    parser.add_argument("--budget-cost", type=float, default=DEFAULT_BUDGETS["cost_cny"])
    args = parser.parse_args()
    if args.list_suites:
        for name, cases in SUITES.items():
            print(f"{name}: {len(cases)} cases")
        return 0
    budgets = {
        "duration_s": args.budget_duration,
        "tool_count": args.budget_tools,
        "observe_avg_ms": args.budget_observe_avg_ms,
        "cost_cny": args.budget_cost,
    }
    try:
        if args.suite:
            path = run_suite(args.base_url.rstrip("/"), args.suite, args.out_dir,
                             args.timeout, budgets)
        else:
            if not args.task:
                raise SystemExit("task is required unless --suite is used")
            path = run_eval(args.base_url.rstrip("/"), args.task, args.out_dir,
                            args.timeout, budgets=budgets)
    except urllib.error.URLError as exc:
        raise SystemExit(f"WinPilot server request failed: {exc}") from exc
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
