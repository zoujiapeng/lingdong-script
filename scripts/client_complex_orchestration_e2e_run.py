#!/usr/bin/env python3
"""Run one real long task that forces exploration, recovery, and replanning."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import client_e2e_run as client_harness  # noqa: E402

DEFAULT_OUTPUT = WORKSPACE / "output" / "complex-orchestration"
TEMP_ROOT = (
    Path(os.environ.get("TEMP") or "C:\\Windows\\Temp")
    / "winpilot-complex-orchestration"
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json(path: Path, value: Any) -> None:
    _write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    _write(
        path,
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_fixture(seed: int) -> tuple[Path, str, dict[str, str]]:
    root = TEMP_ROOT / f"case-{seed}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    token = f"ORCH-{seed}-FINAL"

    metadata = {
        "project": "orion",
        "region": "apac",
        "report_token": token,
        "window": "2026-08-10T10:00:00Z/2026-08-10T11:00:00Z",
    }
    policy = {
        "severity_weights": {"low": 1, "medium": 2, "high": 4, "critical": 7},
        "failure_bonus": 3,
        "grade_thresholds": {"critical": 25, "high": 15},
    }
    _json(root / "metadata.json", metadata)
    _json(root / "policy.json", policy)
    _json(
        root / "manifest.json",
        {
            "revision": 1,
            "active_batches": [
                "data/events-a.jsonl",
                "data/events-b.jsonl",
                "data/events-c.jsonl",
            ],
        },
    )
    _jsonl(
        root / "data/events-a.jsonl",
        [
            {"id": "e1", "ts": "2026-08-10T10:00:00Z", "service": "auth", "severity": "high", "duration_ms": 900, "status": "failed"},
            {"id": "e2", "ts": "2026-08-10T10:01:00Z", "service": "api", "severity": "medium", "duration_ms": 250, "status": "resolved"},
            {"id": "e3", "ts": "2026-08-10T10:02:00Z", "service": "auth", "severity": "low", "duration_ms": 100, "status": "resolved"},
            {"id": "e4", "ts": "2026-08-10T10:03:00Z", "service": "billing", "severity": "critical", "duration_ms": 1200, "status": "failed"},
        ],
    )
    _jsonl(
        root / "data/events-b.jsonl",
        [
            {"id": "e2", "ts": "2026-08-10T10:04:00Z", "service": "api", "severity": "high", "duration_ms": 600, "status": "failed"},
            {"id": "e5", "ts": "2026-08-10T10:05:00Z", "service": "auth", "severity": "critical", "duration_ms": 1500, "status": "failed"},
            {"id": "e6", "ts": "2026-08-10T10:06:00Z", "service": "billing", "severity": "medium", "duration_ms": 300, "status": "resolved"},
            {"id": "e7", "ts": "2026-08-10T10:07:00Z", "service": "api", "severity": "low", "duration_ms": 80, "status": "resolved"},
        ],
    )
    _jsonl(
        root / "data/events-c.jsonl",
        [
            {"id": "e8", "ts": "2026-08-10T10:08:00Z", "service": "billing", "severity": "high", "duration_ms": 700, "status": "failed"},
            {"id": "e9", "ts": "2026-08-10T10:09:00Z", "service": "auth", "severity": "medium", "duration_ms": 400, "status": "resolved"},
            {"id": "e10", "ts": "2026-08-10T10:10:00Z", "service": "api", "severity": "critical", "duration_ms": 2000, "status": "failed"},
            {"id": "e3", "ts": "2026-08-10T10:11:00Z", "service": "auth", "severity": "medium", "duration_ms": 350, "status": "failed"},
        ],
    )
    _jsonl(
        root / "staging/late-delta.jsonl",
        [
            {"id": "e11", "ts": "2026-08-10T10:12:00Z", "service": "billing", "severity": "critical", "duration_ms": 1800, "status": "failed"},
            {"id": "e5", "ts": "2026-08-10T10:13:00Z", "service": "auth", "severity": "high", "duration_ms": 500, "status": "resolved"},
            {"id": "e12", "ts": "2026-08-10T10:14:00Z", "service": "api", "severity": "high", "duration_ms": 750, "status": "failed"},
        ],
    )

    _write(root / "knowledge/global.md", "Global rule: failed events receive the configured failure bonus.\n")
    _write(root / "knowledge/orion_apac.md", "Orion APAC actions: critical = immediate owner escalation; high = same-day remediation plan; moderate = monitor.\n")
    _write(root / "knowledge/orion_emea.md", "Orion EMEA actions: use the weekly review queue.\n")
    _write(root / "knowledge/atlas_apac.md", "Atlas APAC actions: notify the Atlas duty team.\n")
    _write(root / "knowledge/archive_2024.md", "Archived and not applicable.\n")

    _write(
        root / "pipeline.py",
        '''"""Complete this reusable incident-report pipeline."""\nfrom pathlib import Path\n\n\ndef build_report(root: Path) -> dict:\n    raise NotImplementedError("implement from request.txt")\n\n\ndef write_deliverables(root: Path) -> dict:\n    raise NotImplementedError("implement from request.txt")\n''',
    )
    _write(
        root / "tests/test_pipeline.py",
        '''import json\nfrom pathlib import Path\n\nfrom pipeline import build_report\n\n\ndef _fixture(tmp_path: Path) -> Path:\n    (tmp_path / "data").mkdir()\n    (tmp_path / "knowledge").mkdir()\n    (tmp_path / "metadata.json").write_text(json.dumps({"project":"orion","region":"apac","report_token":"T","window":"W"}), encoding="utf-8")\n    (tmp_path / "policy.json").write_text(json.dumps({"severity_weights":{"low":1,"medium":2,"high":4,"critical":7},"failure_bonus":3,"grade_thresholds":{"critical":25,"high":15}}), encoding="utf-8")\n    (tmp_path / "manifest.json").write_text(json.dumps({"revision":1,"active_batches":["data/a.jsonl","data/b.jsonl"]}), encoding="utf-8")\n    (tmp_path / "data/a.jsonl").write_text('{"id":"x","ts":"2026-01-01T00:00:00Z","service":"api","severity":"low","duration_ms":10,"status":"resolved"}\\n', encoding="utf-8")\n    (tmp_path / "data/b.jsonl").write_text('{"id":"x","ts":"2026-01-01T00:01:00Z","service":"api","severity":"high","duration_ms":20,"status":"failed"}\\n', encoding="utf-8")\n    (tmp_path / "knowledge/global.md").write_text("global", encoding="utf-8")\n    (tmp_path / "knowledge/orion_apac.md").write_text("Orion APAC actions: critical = immediate owner escalation; high = same-day remediation plan; moderate = monitor.", encoding="utf-8")\n    return tmp_path\n\n\ndef test_latest_duplicate_wins_and_policy_applies(tmp_path):\n    report = build_report(_fixture(tmp_path))\n    assert report["summary"] == {"event_count":1,"failure_count":1,"duration_ms":20,"risk_score":7}\n    assert report["services"]["api"]["risk_score"] == 7\n    assert report["policy_sources"] == ["knowledge/global.md", "knowledge/orion_apac.md"]\n''',
    )
    _write(
        root / "request.txt",
        f'''Own this task end to end. Explore the files before changing code.\n\nComplete pipeline.py so it reads only manifest.json active_batches, deduplicates event ids by latest ISO timestamp, and computes deterministic totals per service. Risk is severity weight plus failure_bonus for failed events. Grades use thresholds critical first, then high, otherwise moderate. Select global.md plus the project_region knowledge file from metadata; attach the matching action text to each service.\n\nwrite_deliverables must create deliverables/report.json, report.md, and decisions.log. JSON keys: report_token, manifest_revision, policy_sources, summary, services, top_risk_service, late_delta_applied. Markdown must include {token}, each service, totals, grades/actions, and whether the late delta applied. decisions.log should concisely record policy selection, deduplication, the late requirement change, and final verification.\n\nRun python -m pytest -q and then python acceptance.py. acceptance.py deliberately activates a late data delta and fails its first run with actionable feedback. Treat that as a real requirement change: inspect it, update only what is needed, regenerate, and rerun both checks until they pass. Do not edit acceptance.py or tests. Finish only after the deliverables and both checks pass. Briefly report your approach, replanning, and final result.\n''',
    )
    _write(
        root / "acceptance.py",
        '''from __future__ import annotations\nimport json\nimport shutil\nimport sys\nfrom pathlib import Path\n\nROOT = Path(__file__).resolve().parent\nMARKER = ROOT / ".late-delta-activated"\n\ndef activate() -> None:\n    target = ROOT / "data/events-late.jsonl"\n    shutil.copyfile(ROOT / "staging/late-delta.jsonl", target)\n    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))\n    manifest["revision"] = 2\n    manifest["active_batches"].append("data/events-late.jsonl")\n    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")\n    (ROOT / "change_notice.txt").write_text("Revision 2 activated: include data/events-late.jsonl, regenerate every deliverable, and rerun tests plus acceptance.\\n", encoding="utf-8")\n    MARKER.write_text("activated\\n", encoding="utf-8")\n\ndef expected() -> dict:\n    return {\n        "report_token": json.loads((ROOT / "metadata.json").read_text(encoding="utf-8"))["report_token"],\n        "manifest_revision": 2,\n        "policy_sources": ["knowledge/global.md", "knowledge/orion_apac.md"],\n        "summary": {"event_count": 12, "failure_count": 8, "duration_ms": 9580, "risk_score": 72},\n        "services": {\n            "api": {"event_count":4,"failure_count":3,"duration_ms":3430,"risk_score":25,"grade":"critical","action":"immediate owner escalation"},\n            "auth": {"event_count":4,"failure_count":2,"duration_ms":2150,"risk_score":18,"grade":"high","action":"same-day remediation plan"},\n            "billing": {"event_count":4,"failure_count":3,"duration_ms":4000,"risk_score":29,"grade":"critical","action":"immediate owner escalation"},\n        },\n        "top_risk_service": "billing",\n        "late_delta_applied": True,\n    }\n\ndef main() -> int:\n    verify_only = "--verify-only" in sys.argv\n    if not MARKER.exists():\n        if verify_only:\n            print("late delta was never activated", file=sys.stderr)\n            return 31\n        activate()\n        print("ACCEPTANCE_CHANGE: revision 2 is now active; inspect change_notice.txt, regenerate, and rerun", file=sys.stderr)\n        return 23\n    from pipeline import build_report\n    built = build_report(ROOT)\n    saved_path = ROOT / "deliverables/report.json"\n    if not saved_path.exists():\n        print("deliverables/report.json is missing", file=sys.stderr)\n        return 2\n    saved = json.loads(saved_path.read_text(encoding="utf-8"))\n    wanted = expected()\n    errors = []\n    if built != wanted: errors.append("build_report output mismatch")\n    if saved != wanted: errors.append("saved report mismatch")\n    md = (ROOT / "deliverables/report.md").read_text(encoding="utf-8") if (ROOT / "deliverables/report.md").exists() else ""\n    decisions = (ROOT / "deliverables/decisions.log").read_text(encoding="utf-8") if (ROOT / "deliverables/decisions.log").exists() else ""\n    for needle in (wanted["report_token"], "api", "auth", "billing", "72", "late_delta_applied=true"):\n        if needle not in md: errors.append(f"report.md missing {needle}")\n    for needle in ("policy", "dedup", "late", "acceptance"):\n        if needle not in decisions.lower(): errors.append(f"decisions.log missing {needle}")\n    if errors:\n        print("\\n".join(errors), file=sys.stderr)\n        return 3\n    print("ACCEPTANCE_OK revision=2 risk=72 top=billing")\n    return 0\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n''',
    )
    protected = {
        str(path.relative_to(root)): _sha256(path)
        for path in (root / "tests/test_pipeline.py", root / "acceptance.py")
    }
    return root, token, protected


def verify_fixture(root: Path, token: str, protected: dict[str, str]) -> dict[str, Any]:
    errors: list[str] = []
    for relative, digest in protected.items():
        path = root / relative
        if not path.exists() or _sha256(path) != digest:
            errors.append(f"protected file changed: {relative}")
    completed = subprocess.run(
        [sys.executable, "acceptance.py", "--verify-only"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    tests = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if completed.returncode != 0:
        errors.append("acceptance verification failed")
    if tests.returncode != 0:
        errors.append("pytest verification failed")
    report_path = root / "deliverables/report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        report = {}
        errors.append(f"report unreadable: {exc}")
    if report.get("report_token") != token:
        errors.append("report token mismatch")
    if not (root / ".late-delta-activated").exists():
        errors.append("late delta was not activated")
    return {
        "source": "external_complex_fixture",
        "passed": not errors,
        "errors": errors,
        "acceptance_exit_code": completed.returncode,
        "acceptance_output": (completed.stdout + completed.stderr)[-2000:],
        "pytest_exit_code": tests.returncode,
        "pytest_output": (tests.stdout + tests.stderr)[-2000:],
        "report_summary": report.get("summary"),
        "top_risk_service": report.get("top_risk_service"),
        "manifest_revision": report.get("manifest_revision"),
    }


def _short(value: Any, limit: int) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "..."


def _invokes_python_script(command: str, script: str) -> bool:
    script_pattern = re.escape(script)
    return bool(
        re.search(
            rf"(?:^|[;&|]\s*)(?:python|py)(?:\.exe)?\s+"
            rf"(?:-\S+\s+)*[\"']?(?:[^\"'\s]+[\\/])?"
            rf"{script_pattern}[\"']?(?:\s|$)",
            command,
            flags=re.IGNORECASE,
        )
    )


def orchestration_evidence(result: dict[str, Any]) -> dict[str, Any]:
    """Summarize what the model and shell actually did without grading its plan."""
    trace_path = Path(
        str((result.get("trace_binding") or {}).get("trace_file") or "")
    )
    evidence: dict[str, Any] = {
        "source": "bound_runtime_trace",
        "trace_file": str(trace_path),
        "model_calls": 0,
        "shell_attempts": 0,
        "shell_failures": 0,
        "parallel_tool_batches": [],
        "max_tools_in_one_model_response": 0,
        "finish_reasons": {},
        "truncated_model_responses": 0,
        "acceptance_exit_codes": [],
        "pytest_exit_codes": [],
        "timeline": [],
        "parse_errors": [],
    }
    if not trace_path.is_file():
        evidence["parse_errors"].append("bound trace file is missing")
        return evidence

    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        trace_path.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        try:
            record = json.loads(line)
        except Exception as exc:
            evidence["parse_errors"].append(f"line {line_number}: {exc}")
            continue
        if isinstance(record, dict):
            records.append(record)

    evidence["model_calls"] = sum(
        record.get("type") == "vnext_model_call" for record in records
    )
    for record in records:
        if record.get("type") != "vnext_model_response_shape":
            continue
        finish_reason = str(record.get("finish_reason") or "unknown")
        finish_reasons = evidence["finish_reasons"]
        finish_reasons[finish_reason] = finish_reasons.get(finish_reason, 0) + 1
        if finish_reason == "length":
            evidence["truncated_model_responses"] += 1
        count = int(record.get("tool_call_count") or 0)
        evidence["max_tools_in_one_model_response"] = max(
            evidence["max_tools_in_one_model_response"], count
        )
        if count > 1:
            evidence["parallel_tool_batches"].append(count)

    pending_command = ""
    for record in records:
        if record.get("type") != "action":
            continue
        action = record.get("action")
        if action == "shell":
            pending_command = str(record.get("command") or "")
            continue
        if action != "shell_result":
            continue
        exit_code = int(record.get("exit_code") or 0)
        command_lower = pending_command.lower()
        item = {
            "index": len(evidence["timeline"]) + 1,
            "exit_code": exit_code,
            "command": _short(pending_command, 1000),
            "output_preview": _short(record.get("output_preview"), 500),
        }
        evidence["timeline"].append(item)
        if _invokes_python_script(pending_command, "acceptance.py"):
            evidence["acceptance_exit_codes"].append(exit_code)
        if "pytest" in command_lower:
            evidence["pytest_exit_codes"].append(exit_code)
        pending_command = ""

    evidence["shell_attempts"] = len(evidence["timeline"])
    evidence["shell_failures"] = sum(
        item["exit_code"] != 0 for item in evidence["timeline"]
    )
    return evidence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260811)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root, token, protected = prepare_fixture(args.seed)
    output_root = args.output_root / datetime.now().strftime("%Y%m%d-%H%M%S-suite")
    output_root.mkdir(parents=True, exist_ok=True)
    goal = (
        "请完整处理这个真实软件工程任务，不要问我步骤，由你决定如何探索、规划、"
        "执行、验证和在失败后调整：" + str(root / "request.txt")
    )
    result = client_harness.run_case(
        "complex-orchestration",
        {"task": goal, "timeout": args.timeout, "max_steps": 120, "runtime_version": "vnext"},
        output_root=output_root,
        reset_session=True,
        independent_verifier=lambda _result: verify_fixture(root, token, protected),
    )
    result["orchestration_evidence"] = orchestration_evidence(result)
    suite = {
        "name": "complex-orchestration",
        "fixture": str(root),
        "passed": result.get("passed") is True,
        "result": result,
    }
    report_path = output_root / "suite-report.json"
    report_path.write_text(json.dumps(suite, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"event": "suite_finished", "passed": suite["passed"], "report": str(report_path)}, ensure_ascii=False), flush=True)
    return 0 if suite["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
