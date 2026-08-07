#!/usr/bin/env python3
"""Export one Agent run trace as a readable troubleshooting report.

The raw trace lives in python/winpilot_home/traces/arp-<timestamp>-<id>.jsonl
as line-delimited JSON.  This script renders it as an ordered Markdown report
so a manual test session can be inspected and copied into an issue:

  - run identity (goal, budget, model, timings)
  - per-turn narration (what the model said it was doing)
  - every physical receipt (action, outcome, error_kind, window)
  - target binding changes and frame observations
  - model call summary (count, elapsed, usage, cost)
  - terminal status and failure stage

Usage:
  python scripts/export_run_log.py                 # latest run
  python scripts/export_run_log.py <run_id>        # exact run
  python scripts/export_run_log.py --last 3        # last N runs, one file each
  python scripts/export_run_log.py --output DIR    # output directory
  python scripts/export_run_log.py --e2e SUITE_DIR # package an E2E suite:
                                                   #   per-case run log + screenshots
                                                   #   + suite summary index
  python scripts/export_run_log.py --latest-e2e    # latest visual-e2e suite
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[1]
TRACE_ROOT = WORKSPACE / "python" / "winpilot_home" / "traces"
DEFAULT_OUTPUT = WORKSPACE / "output" / "run-logs"
E2E_ROOT = WORKSPACE / "output" / "playwright" / "client-visual-e2e"


def _load_trace(run_id: str) -> list[dict[str, Any]]:
    path = TRACE_ROOT / f"{run_id}.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"trace not found: {path}")
    lines: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            if raw.strip():
                lines.append(json.loads(raw))
    return lines


def _fmt_ts(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M:%S")
    except Exception:
        return f"{ts:.1f}"


def _find_runs() -> list[str]:
    names = sorted(
        (path.stem for path in TRACE_ROOT.glob("arp-*.jsonl")),
        key=lambda name: name,
    )
    return names


def _safe_text(value: Any, limit: int = 2000) -> str:
    text = str(value or "")
    return text[:limit] + ("…" if len(text) > limit else "")


def render_report(run_id: str) -> str:
    trace = _load_trace(run_id)
    out: list[str] = []
    out.append(f"# Run 日志: {run_id}\n")
    out.append(f"trace 文件: `{TRACE_ROOT / (run_id + '.jsonl')}`\n")

    start = next((l for l in trace if l.get("type") == "arp_run_start"), None)
    done = next((l for l in trace if l.get("type") == "live_agent_done"), None)

    if start:
        goal = (start.get("goal_admission") or {}).get("goal") or ""
        budget = start.get("runtime_budget") or {}
        out.append("## 概览\n")
        out.append(f"- goal: `{goal}`")
        out.append(
            f"- budget: max_turns={budget.get('max_turns')} "
            f"max_runtime_s={budget.get('max_runtime_s')} "
            f"max_model_calls={budget.get('max_model_calls')}"
        )
        out.append(f"- 开始: {_fmt_ts(start.get('ts') or 0)}")
        if done:
            out.append(f"- 结束: {_fmt_ts(done.get('ts') or 0)}")
            out.append(
                f"- 状态: `{done.get('status')}` "
                f"failure_stage=`{done.get('failure_stage')}` "
                f"turns={done.get('turns_used')} "
                f"model_calls={done.get('model_calls')}"
            )
            detail = done.get("detail")
            if detail:
                out.append(f"- detail: `{_safe_text(detail, 400)}`")
            usage = done.get("model_usage") or {}
            if usage:
                out.append(
                    f"- usage: prompt={usage.get('prompt_tokens')} "
                    f"completion={usage.get('completion_tokens')} "
                    f"cache_hit={usage.get('cache_hit_tokens')} "
                    f"cost≈{usage.get('cost_cny')} CNY"
                )
        out.append("")

    # --- timeline -------------------------------------------------------
    out.append("## 时间线\n")
    call_count = 0
    for event in trace:
        etype = event.get("type")
        ts = _fmt_ts(event.get("ts") or 0)

        if etype == "live_agent_turn":
            index = event.get("turn_index")
            status = event.get("status")
            narration = (event.get("narration") or "").strip()
            recovery = event.get("recovery")
            proposal = event.get("proposal_kind")
            line = (
                f"**T{index}** [{ts}] `{status}`"
                + (f" proposal={proposal}" if proposal else "")
                + (
                    f" recovery={recovery.get('kind')}(available={recovery.get('available')})"
                    if recovery
                    else ""
                )
            )
            out.append(line)
            if narration:
                for para in narration.splitlines():
                    out.append(f"> {para}")
            out.append("")

        elif etype == "vnext_raw_receipt":
            out.append(
                f"- `{ts}` **{event.get('controller_action')}** "
                f"outcome=`{event.get('outcome')}` "
                f"input_completed={event.get('input_completed')} "
                f"error_kind=`{event.get('error_kind')}` "
                f"hwnd={event.get('target_hwnd')} frame={event.get('frame_id')}"
            )

        elif etype == "vnext_model_call":
            call_count += 1
            ok = event.get("ok")
            error = event.get("error_kind")
            line = (
                f"- `{ts}` model_call #{call_count} "
                f"attempt={event.get('attempt')} ok={ok} "
                f"elapsed={event.get('elapsed_ms')}ms"
            )
            if error:
                line += f" error=`{error}`"
            out.append(line)

        elif etype == "vnext_workspace_target_adopted":
            out.append(
                f"- `{ts}` **workspace 重绑定** → hwnd={event.get('target_hwnd')} "
                f"causal=`{event.get('causal_action')}` "
                f"generation={event.get('generation')}"
            )

        elif etype == "desktop_lease_bound":
            target = event.get("target") or {}
            out.append(
                f"- `{ts}` 桌面租约交付: hwnd={target.get('hwnd')} "
                f"binding_mode={target.get('binding_mode')} "
                f"region={target.get('region')}"
            )

        elif etype == "physical_input_guard":
            out.append(
                f"- `{ts}` **物理输入守卫** state=`{event.get('state')}` "
                f"target={event.get('target_hwnd')} "
                f"foreground={event.get('foreground_hwnd')}"
            )

        elif etype == "vnext_gui_observation":
            out.append(
                f"- `{ts}` 观察 frame={event.get('frame_id')} "
                f"scope={event.get('frame_scope')} "
                f"authoritative={event.get('authoritative')} fresh={event.get('fresh')}"
            )

        elif etype == "action":
            action = event.get("action")
            if action == "shell":
                command = event.get("command") or ""
                out.append(f"- `{ts}` shell: `{_safe_text(command, 300)}`")
            else:
                out.append(f"- `{ts}` action={action} input_completed={event.get('input_completed')}")

        elif etype == "live_agent_done":
            out.append(
                f"- `{ts}` **结束**: status=`{event.get('status')}` "
                f"failure_stage=`{event.get('failure_stage')}` "
                f"turns={event.get('turns_used')} calls={event.get('model_calls')}"
            )
            detail = event.get("detail")
            if detail:
                out.append(f"  detail: `{_safe_text(detail, 600)}`")

    out.append("")
    out.append("---")
    out.append("_由 scripts/export_run_log.py 导出_")
    return "\n".join(out)


def export_e2e_suite(suite_dir: Path, output: Path) -> list[str]:
    """Package one visual E2E suite into per-case run logs + screenshots.

    Reads the suite-report.json, renders each case's bound run as a Markdown
    log, copies its screenshots into the export directory, and writes an
    index of case name / passed / verdict errors / run id / export path.
    """
    report_path = suite_dir / "suite-report.json"
    if not report_path.is_file():
        raise FileNotFoundError(f"suite report not found: {report_path}")
    suite = json.loads(report_path.read_text(encoding="utf-8"))
    results = suite.get("results") or []
    export_dir = output / suite_dir.name
    export_dir.mkdir(parents=True, exist_ok=True)

    rows: list[str] = []
    rows.append(f"# E2E 套件: {suite_dir.name}\n")
    rows.append(
        f"- 结果: {suite.get('passed_count')}/{suite.get('case_count')} 通过 "
        f"(suite passed={suite.get('passed')})"
    )
    rows.append(f"- 来源: `{report_path}`\n")
    rows.append("| case | passed | run_id | verdict errors |")
    rows.append("| --- | --- | --- | --- |")

    for case in results:
        name = str(case.get("name") or "?")
        passed = case.get("passed")
        client = case.get("client_result") or {}
        trace_ids = client.get("bound_trace_ids") or []
        verdict = case.get("external_verdict") or {}
        errors = verdict.get("errors") or []
        run_id = str(trace_ids[0]) if trace_ids else ""
        row = (
            f"| {name} | {passed} | {run_id or '-'} | "
            f"{('; '.join(map(str, errors)))[:160] or '-'} |"
        )
        rows.append(row)

        case_dir = export_dir / (name or f"case-{len(rows)}")
        case_dir.mkdir(parents=True, exist_ok=True)

        if run_id:
            try:
                report = render_report(run_id)
                (case_dir / f"{run_id}.md").write_text(report, encoding="utf-8")
            except FileNotFoundError as exc:
                (case_dir / "TRACE-MISSING.txt").write_text(str(exc), encoding="utf-8")
                run_id = run_id + " (trace missing)"

        screenshots = client.get("screenshots") or {}
        copied: list[str] = []
        for label, shot in screenshots.items():
            if not isinstance(shot, str) or not shot:
                continue
            source = Path(shot)
            if source.is_file():
                target = case_dir / f"{label}{source.suffix}"
                shutil.copy2(source, target)
                copied.append(str(target))
        if copied:
            index = case_dir / "screenshots.txt"
            index.write_text("\n".join(copied) + "\n", encoding="utf-8")

    index_path = export_dir / "INDEX.md"
    index_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return [str(export_dir), str(index_path)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", nargs="?", help="exact run id (default: latest)")
    parser.add_argument("--last", type=int, default=1, help="export last N runs")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--e2e",
        type=Path,
        help="package one visual E2E suite directory (with suite-report.json)",
    )
    parser.add_argument(
        "--latest-e2e",
        action="store_true",
        help="package the most recent visual E2E suite directory",
    )
    args = parser.parse_args()

    if args.latest_e2e or args.e2e is not None:
        if args.e2e is not None:
            suite_dir = args.e2e
        else:
            suites = sorted(E2E_ROOT.glob("20*-suite"))
            if not suites:
                print(f"no E2E suite under {E2E_ROOT}")
                return 1
            suite_dir = suites[-1]
        try:
            exported = export_e2e_suite(suite_dir, args.output)
        except FileNotFoundError as exc:
            print(f"error: {exc}")
            return 1
        for path in exported:
            print(f"exported {path}")
        print(f"total {len(exported)} item(s) -> {args.output}")
        return 0

    runs = _find_runs()
    if not runs:
        print("no arp-*.jsonl traces found")
        return 1

    if args.run_id:
        selected = [args.run_id]
    else:
        selected = runs[-max(1, args.last):]

    args.output.mkdir(parents=True, exist_ok=True)
    exported: list[str] = []
    for run_id in selected:
        try:
            report = render_report(run_id)
        except FileNotFoundError as exc:
            print(f"skip {run_id}: {exc}")
            continue
        path = args.output / f"{run_id}.md"
        path.write_text(report, encoding="utf-8")
        exported.append(str(path))
        print(f"exported {path}")

    if exported:
        print(f"total {len(exported)} run log(s) -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
