#!/usr/bin/env python3
"""Run the exact user-requested 3-turn desktop sequence through the visible client.

Turn 1 (task):  用画板画一只猫然后截图用QQ发给我的手机并附带文字
Turn 2 (chat):  你刚刚说的如何去完成我的任务的
Turn 3 (task):  把刚刚那只猫重新画一个不一样的给我

All three turns run in the SAME thread so the actor's history is exercised
(exactly what the user asked).  No task-specific rules or tools are added;
the harness only submits the unchanged goals through the visible Electron
client and records independent external state (files, QQ process, claim text)
plus full trace analysis for layered diagnosis.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
WINPILOT_HOME = WORKSPACE / "python" / "winpilot_home"
for import_root in (WINPILOT_HOME, SCRIPT_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from winpilot.utils.dpi import ensure_process_dpi_awareness  # noqa: E402

ensure_process_dpi_awareness()

import psutil  # noqa: E402
import win32gui  # noqa: E402
import win32process  # noqa: E402

import client_e2e_run as client_harness  # noqa: E402

DEFAULT_OUTPUT = WORKSPACE / "output" / "cat-qq-3turn-e2e"

TURN1_TASK = "用画板画一只猫然后截图用QQ发给我的手机并附带文字"
TURN2_CHAT = "你刚刚说的如何去完成我的任务的"
TURN3_TASK = "把刚刚那只猫重新画一个不一样的给我"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}
SCAN_ROOTS = ("Desktop", "Documents", "Pictures", "Downloads")
SEND_KEYWORDS = ("QQ", "qq", "发送", "发给", "手机", "发送成功", "已发送")

GUARD_TOKENS = (
    "approve_mode", "decide_gate", "permit", "scope_guard", "no_mouse_steal",
    "multi_input_cooperation", "cooperative_", "approval_gate",
    "permission_handshake", "MutationPermit",
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(_json_safe(value), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _scan_new_images(since: float, until: float) -> list[dict[str, Any]]:
    home = Path(os.environ.get("USERPROFILE") or "C:\\Users\\Public")
    workspace_lower = str(WORKSPACE.resolve()).casefold()
    found: list[dict[str, Any]] = []
    for root_name in SCAN_ROOTS:
        root = home / root_name
        if not root.is_dir():
            continue
        for folder, _dirs, files in os.walk(root):
            if str(folder).casefold().startswith(workspace_lower):
                continue
            for name in files:
                suffix = Path(name).suffix.lower()
                if suffix not in IMAGE_EXTS:
                    continue
                path = Path(folder) / name
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if since - 120 <= mtime <= until + 120:
                    found.append({
                        "path": str(path),
                        "size": path.stat().st_size,
                        "mtime": mtime,
                        "ext": suffix,
                    })
    found.sort(key=lambda item: item["mtime"], reverse=True)
    return found[:12]


def _qq_process_alive() -> dict[str, Any]:
    matches = []
    for proc in psutil.process_iter(["pid", "name", "create_time"]):
        try:
            name = (proc.info["name"] or "").casefold()
        except (psutil.Error, OSError):
            continue
        if name in {"qq.exe", "qqnt.exe"} or (
            "qq" in name and name.endswith(".exe")
        ):
            matches.append({
                "pid": proc.info["pid"],
                "name": proc.info["name"],
                "create_time": proc.info["create_time"],
            })
    return {"alive": bool(matches), "processes": matches[:8]}


def _read_claim_texts(run_id: str) -> list[str]:
    checkpoint = (
        WINPILOT_HOME / "traces" / ".agent-thread-continuation.json"
    )
    texts: list[str] = []
    try:
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    except Exception:
        payload = None
    threads = payload.get("threads") if isinstance(payload, dict) else None
    if isinstance(threads, list):
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            for turn in thread.get("turns") or []:
                if not isinstance(turn, dict):
                    continue
                if run_id and str(turn.get("run_id") or "") == run_id:
                    result = str(turn.get("result") or "")
                    if result.strip():
                        texts.append(result.strip())
    if run_id:
        for text in _read_run_user_result(run_id):
            if text.strip() and text.strip() not in texts:
                texts.append(text.strip())
    return texts[-12:]


def _read_run_user_result(run_id: str) -> list[str]:
    """Fetch the run's user_result (the model's own words) from the ARP API.

    Failed runs are not persisted into the thread checkpoint, so the client
    UI's honest failure explanation lives on the run record itself.
    """
    import urllib.error
    import urllib.request

    texts: list[str] = []
    url = token = ""
    for proc in psutil.process_iter(["pid", "name", "cmdline", "environ"]):
        try:
            name = (proc.info["name"] or "").casefold()
            if name != "electron.exe":
                continue
            for child in proc.children(recursive=True):
                command = child.cmdline()
                if not any(
                    Path(item).name.casefold() == "serve.py"
                    for item in command
                ):
                    continue
                port = next(
                    (
                        int(item)
                        for item in command
                        if str(item).isdigit() and 1024 <= int(item) <= 65535
                    ),
                    0,
                )
                child_token = str(
                    child.environ().get("WINPILOT_CONTROL_TOKEN") or ""
                )
                if port and child_token:
                    url = f"http://127.0.0.1:{port}"
                    token = child_token
                    break
            if url:
                break
        except (psutil.Error, OSError):
            continue
    if not url or not token:
        return texts
    request = urllib.request.Request(
        f"{url}/api/v2/arp/runs/{run_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError):
        return texts
    run = payload.get("run") if isinstance(payload, dict) else None
    if not isinstance(run, dict):
        return texts
    result = run.get("user_result") or run.get("result")
    if type(result) is str and result.strip():
        texts.append(result.strip())
    return texts


def _analyze_trace(result: dict[str, Any]) -> dict[str, Any]:
    trace_ids = list(result.get("bound_trace_ids") or [])
    counts: dict[str, Counter] = {
        "receipt_outcomes": Counter(),
        "receipt_actions": Counter(),
        "turn_statuses": Counter(),
        "model_errors": Counter(),
        "frame_scopes": Counter(),
    }
    shell_commands: list[str] = []
    adopted: list[dict[str, str]] = []
    invalid_proposals = 0
    blocked_calls = 0
    guard_hits = 0
    usage_total = 0
    done_events: list[dict[str, Any]] = []
    drag_calls = 0
    type_calls = 0
    hotkey_calls = 0
    click_calls = 0
    launch_calls = 0
    for trace_id in trace_ids:
        path = client_harness.TRACE_ROOT / f"{trace_id}.jsonl"
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except Exception:
                continue
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "")
            lowered = raw_line.casefold()
            if any(token in lowered for token in GUARD_TOKENS):
                guard_hits += 1
            if event_type == "vnext_model_call":
                error_kind = str(event.get("error_kind") or "")
                if error_kind:
                    counts["model_errors"][error_kind] += 1
            elif event_type == "live_agent_turn":
                status = str(event.get("status") or "")
                counts["turn_statuses"][status] += 1
                if status == "invalid_proposal":
                    invalid_proposals += 1
                if status == "blocked":
                    blocked_calls += 1
            elif event_type == "vnext_gui_observation":
                counts["frame_scopes"][
                    str(event.get("frame_scope") or "")
                ] += 1
            elif event_type == "action":
                if str(event.get("action") or "") == "shell":
                    command = str(event.get("command") or "")
                    if command:
                        shell_commands.append(command[:200])
            elif event_type == "vnext_raw_receipt":
                action = str(
                    event.get("controller_action")
                    or event.get("raw_action")
                    or ""
                )
                outcome = str(event.get("outcome") or "")
                counts["receipt_outcomes"][outcome] += 1
                counts["receipt_actions"][action] += 1
                if action == "drag":
                    drag_calls += 1
                elif action == "type_text":
                    type_calls += 1
                elif action in {"hotkey", "press_key"}:
                    hotkey_calls += 1
                elif action == "click":
                    click_calls += 1
                elif action == "launch_application":
                    launch_calls += 1
            elif event_type == "vnext_workspace_target_adopted":
                window = event.get("target_window")
                if isinstance(window, dict):
                    adopted.append({
                        "title": str(window.get("title") or "")[:40],
                        "process": str(window.get("process") or ""),
                    })
            elif event_type == "live_agent_done":
                done_events.append({
                    "failure_stage": str(event.get("failure_stage") or ""),
                    "status": str(event.get("status") or ""),
                    "turns_used": event.get("turns_used"),
                    "detail": str(event.get("detail") or "")[:120],
                })
            elif event_type == "agent_usage":
                total = event.get("total")
                if isinstance(total, (int, float)):
                    usage_total += int(total)
    return {
        "model_calls": len(trace_ids) and _count_model_calls(trace_ids),
        "receipt_outcomes": dict(counts["receipt_outcomes"]),
        "receipt_actions": dict(counts["receipt_actions"]),
        "turn_statuses": dict(counts["turn_statuses"]),
        "model_errors": dict(counts["model_errors"]),
        "frame_scopes": dict(counts["frame_scopes"]),
        "shell_commands": shell_commands[:16],
        "adopted_targets": adopted[:12],
        "invalid_proposals": invalid_proposals,
        "blocked_calls": blocked_calls,
        "guard_hits": guard_hits,
        "usage_total": usage_total,
        "done_events": done_events,
        "drag_calls": drag_calls,
        "click_calls": click_calls,
        "type_calls": type_calls,
        "hotkey_calls": hotkey_calls,
        "launch_calls": launch_calls,
    }


def _count_model_calls(trace_ids: list[str]) -> int:
    total = 0
    for trace_id in trace_ids:
        path = client_harness.TRACE_ROOT / f"{trace_id}.jsonl"
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            if '"type": "vnext_model_call"' in raw_line:
                total += 1
    return total


def _copy_bound_traces(result: dict[str, Any], destination: Path) -> list[str]:
    copied: list[str] = []
    for trace_id in result.get("bound_trace_ids") or []:
        source = client_harness.TRACE_ROOT / f"{trace_id}.jsonl"
        if not source.is_file():
            continue
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / source.name
        shutil.copy2(source, target)
        copied.append(str(target))
    return copied


def run_three_turn_sequence(
    *,
    output_root: Path,
    timeout_turn1: int = 1500,
    timeout_turn2: int = 300,
    timeout_turn3: int = 1200,
    max_steps: int = 200,
) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    case_dir = output_root / f"{timestamp}-cat-qq-3turn"
    case_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "kind": "cat_qq_three_turn_e2e",
        "entrypoint": "electron_client_windows_uia",
        "started_at": datetime.now().astimezone().isoformat(),
        "turns": [],
        "qq_before": _qq_process_alive(),
    }

    # ---- Turn 1: draw + screenshot + QQ send with text ----
    started_1 = time.time()
    turn1 = client_harness.run_case(
        "turn1-draw-cat-send-qq",
        {
            "task": TURN1_TASK,
            "max_steps": max_steps,
            "timeout": timeout_turn1,
            "runtime_version": "vnext",
        },
        output_root=case_dir / "client-turn1",
        reset_session=True,
    )
    ended_1 = time.time()
    images_1 = _scan_new_images(started_1, ended_1)
    claim_1 = _read_claim_texts(
        str((turn1.get("result") or {}).get("run_id") or "")
    )
    claim_text_1 = "\n".join(claim_1)
    qq_after_1 = _qq_process_alive()
    verdict_1 = {
        "source": "external_file_qq_claim_oracle",
        "passed": bool(images_1) and bool(
            any(keyword in claim_text_1 for keyword in SEND_KEYWORDS)
        ),
        "partial": bool(images_1 or qq_after_1["alive"]),
        "status": "saved_and_send_claimed"
        if bool(images_1) and any(
            keyword in claim_text_1 for keyword in SEND_KEYWORDS
        )
        else "incomplete",
        "errors": [
            *([] if images_1 else ["no new image file inside the run window"]),
            *(
                []
                if any(keyword in claim_text_1 for keyword in SEND_KEYWORDS)
                else ["completion claim does not mention QQ/send/phone"]
            ),
        ],
        "new_images": images_1,
        "qq_alive": qq_after_1,
        "claim_texts": claim_1[-3:],
    }
    turn1["task_verdict"] = verdict_1
    report["turns"].append({
        "kind": "task",
        "goal": TURN1_TASK,
        "verdict": verdict_1,
        "client_result": turn1,
        "trace_analysis": _analyze_trace(turn1),
        "meta": {
            "run_id": str((turn1.get("result") or {}).get("run_id") or ""),
            "outcome": str((turn1.get("result") or {}).get("outcome") or ""),
            "status_label": str(
                (turn1.get("result") or {}).get("status_label") or ""
            ),
            "duration_seconds": turn1.get("duration_seconds"),
            "runner_error": turn1.get("runner_error") or "",
        },
        "copied_traces": _copy_bound_traces(turn1, case_dir / "traces-turn1"),
    })
    first_run_id = str((turn1.get("result") or {}).get("run_id") or "")

    # ---- Turn 2: chat about how the task was completed ----
    started_2 = time.time()
    turn2 = client_harness.run_case(
        "turn2-chat-about-completion",
        {
            "task": TURN2_CHAT,
            "max_steps": 60,
            "timeout": timeout_turn2,
            "runtime_version": "vnext",
        },
        output_root=case_dir / "client-turn2",
        reset_session=False,
        required_context_run_id=first_run_id,
    )
    ended_2 = time.time()
    claim_2 = _read_claim_texts(
        str((turn2.get("result") or {}).get("run_id") or "")
    )
    claim_text_2 = "\n".join(claim_2)
    trace2 = _analyze_trace(turn2)
    desktop_used_2 = bool(
        trace2["receipt_actions"] or trace2["frame_scopes"]
    )
    verdict_2 = {
        "source": "external_chat_reply_oracle",
        "passed": bool(claim_text_2.strip())
        and not desktop_used_2
        and len(claim_text_2.strip()) >= 8,
        "partial": bool(claim_text_2.strip()),
        "status": (
            "chat_replied_without_desktop"
            if bool(claim_text_2.strip()) and not desktop_used_2
            else "chat_replied_but_used_desktop"
            if bool(claim_text_2.strip())
            else "no_reply"
        ),
        "errors": [
            *(
                []
                if claim_text_2.strip()
                else ["no user-visible chat reply was produced"]
            ),
            *(
                []
                if not desktop_used_2
                else ["chat turn used the desktop (lease/observations)"]
            ),
        ],
        "claim_texts": claim_2[-3:],
        "desktop_used": desktop_used_2,
    }
    turn2["task_verdict"] = verdict_2
    report["turns"].append({
        "kind": "chat",
        "goal": TURN2_CHAT,
        "verdict": verdict_2,
        "client_result": turn2,
        "trace_analysis": trace2,
        "meta": {
            "run_id": str((turn2.get("result") or {}).get("run_id") or ""),
            "outcome": str((turn2.get("result") or {}).get("outcome") or ""),
            "status_label": str(
                (turn2.get("result") or {}).get("status_label") or ""
            ),
            "duration_seconds": turn2.get("duration_seconds"),
            "runner_error": turn2.get("runner_error") or "",
        },
        "copied_traces": _copy_bound_traces(turn2, case_dir / "traces-turn2"),
    })
    second_run_id = str((turn2.get("result") or {}).get("run_id") or "")

    # ---- Turn 3: redraw a DIFFERENT cat ----
    started_3 = time.time()
    turn3 = client_harness.run_case(
        "turn3-redraw-different-cat",
        {
            "task": TURN3_TASK,
            "max_steps": max_steps,
            "timeout": timeout_turn3,
            "runtime_version": "vnext",
        },
        output_root=case_dir / "client-turn3",
        reset_session=False,
        required_context_run_id=second_run_id,
    )
    ended_3 = time.time()
    images_3 = _scan_new_images(started_3, ended_3)
    claim_3 = _read_claim_texts(
        str((turn3.get("result") or {}).get("run_id") or "")
    )
    claim_text_3 = "\n".join(claim_3)
    verdict_3 = {
        "source": "external_redraw_claim_oracle",
        "passed": bool(images_3) and bool(claim_text_3.strip()),
        "partial": bool(images_3 or claim_text_3.strip()),
        "status": (
            "redrawn_and_claimed"
            if bool(images_3) and bool(claim_text_3.strip())
            else "incomplete"
        ),
        "errors": [
            *(
                []
                if images_3
                else ["no new image file created inside the run window"]
            ),
            *(
                []
                if claim_text_3.strip()
                else ["no completion claim for the redraw"]
            ),
        ],
        "new_images": images_3,
        "claim_texts": claim_3[-3:],
    }
    turn3["task_verdict"] = verdict_3
    report["turns"].append({
        "kind": "task",
        "goal": TURN3_TASK,
        "verdict": verdict_3,
        "client_result": turn3,
        "trace_analysis": _analyze_trace(turn3),
        "meta": {
            "run_id": str((turn3.get("result") or {}).get("run_id") or ""),
            "outcome": str((turn3.get("result") or {}).get("outcome") or ""),
            "status_label": str(
                (turn3.get("result") or {}).get("status_label") or ""
            ),
            "duration_seconds": turn3.get("duration_seconds"),
            "runner_error": turn3.get("runner_error") or "",
        },
        "copied_traces": _copy_bound_traces(turn3, case_dir / "traces-turn3"),
    })

    report["qq_after"] = _qq_process_alive()
    report["thread_run_ids"] = [first_run_id, second_run_id]
    report["passed"] = all(
        bool(turn.get("verdict", {}).get("passed") is True)
        for turn in report["turns"]
    )
    report["finished_at"] = datetime.now().astimezone().isoformat()
    report_path = case_dir / "report.json"
    _write_json(report_path, report)
    report["report"] = str(report_path)
    print(json.dumps({"event": "finished", "report": str(report_path)}, ensure_ascii=False))
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout-turn1", type=int, default=1500)
    parser.add_argument("--timeout-turn2", type=int, default=300)
    parser.add_argument("--timeout-turn3", type=int, default=1200)
    parser.add_argument("--max-steps", type=int, default=200)
    args = parser.parse_args()
    report = run_three_turn_sequence(
        output_root=args.output_root,
        timeout_turn1=args.timeout_turn1,
        timeout_turn2=args.timeout_turn2,
        timeout_turn3=args.timeout_turn3,
        max_steps=args.max_steps,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
