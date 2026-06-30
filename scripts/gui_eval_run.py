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
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_OUT_DIR = Path("python/winpilot_home/evals")


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


def _summarize_trace(events: list[dict[str, Any]]) -> dict[str, Any]:
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


def run_eval(base_url: str, task: str, out_dir: Path, timeout_s: float) -> Path:
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
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_id}.eval.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real WinPilot GUI eval task.")
    parser.add_argument("task", help="Task text to submit through /api/chat.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"WinPilot server URL. Default: {DEFAULT_BASE_URL}")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR,
                        help=f"Report output directory. Default: {DEFAULT_OUT_DIR}")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="Maximum seconds to wait before stopping the run.")
    args = parser.parse_args()
    try:
        path = run_eval(args.base_url.rstrip("/"), args.task, args.out_dir, args.timeout)
    except urllib.error.URLError as exc:
        raise SystemExit(f"WinPilot server request failed: {exc}") from exc
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
