#!/usr/bin/env python3
"""Guided super-complex mixed long task through the visible Electron client.

Scenario: browser + PDF + QQ + office in one natural-language goal.

Purpose: exercise the product's resumable Agent Thread / Actor todolist
continuation (``waiting_for_followup`` -> ``agent-continuation/1``) through
the real client entrance, let it fail mid-way, then guide it forward with
"继续" turns in the SAME thread, including across a full product restart.

The harness only submits the unchanged goals through the visible client; no
task-specific rules, tools or prompts are added.  All observations come from
independent artifacts: the bound run projection (continuation + task_state
todos), the server-owned continuation checkpoint, the trace, and the visible
client window screenshot.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
WINPILOT_HOME = WORKSPACE / "python" / "winpilot_home"
CONFIG_PATH = WINPILOT_HOME / "config.json"
CHECKPOINT_PATH = WINPILOT_HOME / "traces" / ".agent-thread-continuation.json"
ELECTRON_EXE = WORKSPACE / "node_modules" / "electron" / "dist" / "electron.exe"
VITE_SCRIPT = WORKSPACE / "node_modules" / "vite" / "bin" / "vite.js"
ELECTRON_CLI = WORKSPACE / "node_modules" / "electron" / "cli.js"

for import_root in (WINPILOT_HOME, SCRIPT_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import psutil  # noqa: E402

import client_e2e_run as client_harness  # noqa: E402

DEFAULT_OUTPUT = WORKSPACE / "output" / "mixed-long-guided-e2e"

GOAL = (
    "请一步一步完成下面这个多应用的连续办公任务，每完成一步再做下一步："
    "1) 用 GitHub 连接器（connector 工具）读取我的私有仓库 zoujiapeng/lingdong-script 的最近一次提交，"
    "把提交的 SHA 和提交信息写入桌面的 github.txt；"
    "2) 打开任意一个已安装的浏览器（Chrome、Edge、Firefox 任选其一），访问 https://www.bing.com 首页并截图，"
    "保存到桌面 browser.png；"
    "3) 用 WPS 打开桌面上的 PDF 文件 fuxi_9up_landscape.pdf，把该页截图保存到桌面 pdfpage.png；"
    "4) 打开腾讯 QQ，把 pdfpage.png 图片发送到“我的手机”，并附带文字“这是设计稿”；"
    "5) 用 Microsoft Excel 新建工作簿，A1 输入“步骤表”，A2 到 A6 依次输入 连接器、浏览器、PDF、QQ、Excel，"
    "保存到桌面 office_steps.xlsx。全部完成后告诉我每一步的结果。"
)

GUIDE = "继续"


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _log(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# config budget control (deterministic mid-task stop for the controlled phase)
# --------------------------------------------------------------------------- #
def set_budget(
    *,
    max_turns: int | None,
    max_runtime_s: int | None,
    max_model_calls: int | None,
) -> dict[str, Any]:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    vnext = payload.get("vnext")
    if not isinstance(vnext, dict):
        vnext = {}
        payload["vnext"] = vnext
    before = {k: vnext.get(k) for k in ("max_turns", "max_runtime_s", "max_model_calls")}
    if max_turns is not None:
        vnext["max_turns"] = int(max_turns)
    if max_runtime_s is not None:
        vnext["max_runtime_s"] = int(max_runtime_s)
    if max_model_calls is not None:
        vnext["max_model_calls"] = int(max_model_calls)
    CONFIG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    after = {k: vnext.get(k) for k in ("max_turns", "max_runtime_s", "max_model_calls")}
    return {"before": before, "after": after}


def restore_budget(backup_path: Path) -> None:
    if backup_path.is_file():
        shutil.copy2(backup_path, CONFIG_PATH)


# --------------------------------------------------------------------------- #
# product lifecycle (restart via the project's own dev path)
# --------------------------------------------------------------------------- #
def _workspace_electron_pids() -> list[int]:
    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            if str(proc.info.get("name") or "").casefold() != "electron.exe":
                continue
            exe = str(proc.info.get("exe") or "")
            if str(WORKSPACE).casefold() in exe.casefold():
                pids.append(int(proc.info["pid"]))
        except (psutil.Error, OSError):
            continue
    return pids


def kill_workspace_product() -> dict[str, Any]:
    killed: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = str(proc.info.get("name") or "").casefold()
            cmdline = " ".join(proc.info.get("cmdline") or [])
        except (psutil.Error, OSError):
            continue
        hit = (
            name == "electron.exe"
            or (
                name in {"node.exe", "node"}
                and any(
                    token in cmdline.casefold()
                    for token in (
                        "vite",
                        "electron\\cli.js",
                        "electron/cli.js",
                        "concurrently",
                        "npm-cli.js\" run dev",
                    )
                )
            )
        )
        if not hit:
            continue
        try:
            proc.kill()
            killed.append({"pid": proc.info["pid"], "name": proc.info.get("name")})
        except (psutil.Error, OSError):
            pass
    return {"killed": killed, "electron_remaining": _workspace_electron_pids()}


def _node() -> str:
    return shutil.which("node") or "node"


def _url_ready(url: str, timeout_s: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            return 200 <= int(response.status) < 500
    except Exception:
        return False


def start_product(log_dir: Path, *, vite_port: int = 5200) -> dict[str, Any]:
    log_dir.mkdir(parents=True, exist_ok=True)
    started: dict[str, Any] = {}
    if not _url_ready(f"http://127.0.0.1:{vite_port}"):
        vite_log = (log_dir / "vite.log").open("ab")
        vite = subprocess.Popen(
            [_node(), str(VITE_SCRIPT), "--host", "127.0.0.1", "--port", str(vite_port)],
            cwd=str(WORKSPACE),
            stdin=subprocess.PIPE,
            stdout=vite_log,
            stderr=subprocess.STDOUT,
            creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
        started["vite_pid"] = vite.pid
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and not _url_ready(
            f"http://127.0.0.1:{vite_port}"
        ):
            if vite.poll() is not None:
                break
            time.sleep(0.5)
        started["vite_ready"] = _url_ready(f"http://127.0.0.1:{vite_port}")
    else:
        started["vite_ready"] = True
        started["vite_pid"] = None
    electron_log = (log_dir / "electron.log").open("ab")
    env = dict(os.environ)
    env["ELECTRON_DISABLE_SECURITY_WARNINGS"] = "true"
    electron = subprocess.Popen(
        [_node(), str(ELECTRON_CLI), "."],
        cwd=str(WORKSPACE),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=electron_log,
        stderr=subprocess.STDOUT,
        creationflags=int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)),
    )
    started["electron_pid"] = electron.pid
    return started


def wait_client_ready(log_dir: Path, timeout_s: float = 180.0) -> Any:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            driver = client_harness.ElectronClientDriver(log_dir)
            if driver.identity.pid in _workspace_electron_pids():
                try:
                    status = driver.runtime_status()
                    if status:
                        return driver
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(1.0)
    raise RuntimeError(f"visible client did not become ready: {last_error}")


def restart_product(case_dir: Path, step: str) -> dict[str, Any]:
    evidence: dict[str, Any] = {"step": step, "at": _now()}
    evidence["kill"] = kill_workspace_product()
    time.sleep(2.0)
    evidence["start"] = start_product(case_dir / f"restart-{step}")
    driver = wait_client_ready(case_dir / f"restart-{step}")
    evidence["ready_pid"] = driver.identity.pid
    evidence["ready_hwnd"] = driver.identity.hwnd
    return evidence


# --------------------------------------------------------------------------- #
# independent observations
# --------------------------------------------------------------------------- #
def _service_endpoint() -> tuple[str, str]:
    for proc in psutil.process_iter(["pid", "name", "cmdline", "environ"]):
        try:
            if str(proc.info.get("name") or "").casefold() != "electron.exe":
                continue
            for child in proc.children(recursive=True):
                command = child.cmdline()
                if not any(
                    Path(item).name.casefold() == "serve.py" for item in command
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
                token = str(child.environ().get("WINPILOT_CONTROL_TOKEN") or "")
                if port and token:
                    return f"http://127.0.0.1:{port}", token
        except (psutil.Error, OSError):
            continue
    return "", ""


def _api_get(path: str, timeout: float = 10.0) -> dict[str, Any]:
    url, token = _service_endpoint()
    if not url:
        return {}
    request = urllib.request.Request(
        f"{url}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except Exception:
        return {}


def run_detail(run_id: str) -> dict[str, Any]:
    payload = _api_get(f"/api/v2/arp/runs/{run_id}")
    if not payload:
        payload = _api_get("/api/v2/runs")
        for item in payload.get("runs") or []:
            if str(item.get("run_id") or "") == run_id:
                return item
        return {}
    run = payload.get("run")
    return run if isinstance(run, dict) else payload


def thread_snapshot(thread_id: str) -> dict[str, Any]:
    if not thread_id:
        return {}
    payload = _api_get(f"/api/v2/threads/{thread_id}")
    thread = payload.get("thread")
    return thread if isinstance(thread, dict) else payload


def read_checkpoint() -> dict[str, Any]:
    if not CHECKPOINT_PATH.is_file():
        return {}
    try:
        return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def analyze_trace(trace_ids: list[str]) -> dict[str, Any]:
    todos_seen: list[dict[str, Any]] = []
    turn_statuses: list[str] = []
    done_events: list[dict[str, Any]] = []
    receipt_actions: list[str] = []
    model_calls = 0
    for trace_id in trace_ids:
        path = client_harness.TRACE_ROOT / f"{trace_id}.jsonl"
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except Exception:
                continue
            if not isinstance(event, dict):
                continue
            etype = str(event.get("type") or "")
            if etype == "vnext_model_call":
                model_calls += 1
            elif etype == "live_agent_turn":
                turn_statuses.append(str(event.get("status") or ""))
            elif etype == "live_agent_activity" and event.get("kind") == "task_state":
                todos = event.get("task_state_todos")
                if isinstance(todos, list) and todos:
                    todos_seen.append(
                        {
                            "ts": event.get("ts"),
                            "todos": [
                                {
                                    "content": str(t.get("content") or ""),
                                    "status": str(t.get("status") or ""),
                                }
                                for t in todos
                                if isinstance(t, dict)
                            ],
                        }
                    )
            elif etype == "live_agent_done":
                done_events.append(
                    {
                        "status": str(event.get("status") or ""),
                        "failure_stage": str(event.get("failure_stage") or ""),
                        "detail": str(event.get("detail") or "")[:200],
                        "turns_used": event.get("turns_used"),
                        "model_calls": event.get("model_calls"),
                    }
                )
            elif etype == "vnext_raw_receipt":
                action = str(
                    event.get("controller_action") or event.get("raw_action") or ""
                )
                if action:
                    receipt_actions.append(action)
    return {
        "model_calls": model_calls,
        "turn_statuses": turn_statuses,
        "done_events": done_events,
        "receipt_action_counts": {
            action: receipt_actions.count(action) for action in sorted(set(receipt_actions))
        },
        "todos_timeline": todos_seen,
        "last_todos": todos_seen[-1]["todos"] if todos_seen else [],
    }


def summarize_run(run_id: str, trace_ids: list[str], thread_id: str) -> dict[str, Any]:
    detail = run_detail(run_id)
    continuation = detail.get("continuation")
    continuation = continuation if isinstance(continuation, dict) else {}
    remaining = continuation.get("remaining_items")
    return {
        "run_id": run_id,
        "thread_id": thread_id,
        "status": str(detail.get("status") or ""),
        "result_text": str(detail.get("result") or "")[:4000],
        "error": str(detail.get("error") or ""),
        "failure_stage": str(detail.get("failure_stage") or ""),
        "continuation": continuation,
        "remaining_items": remaining if isinstance(remaining, list) else [],
        "task_state_todos": detail.get("task_state_todos") or [],
        "task_state_scratchpad": str(detail.get("task_state_scratchpad") or "")[:2000],
        "trace": analyze_trace(trace_ids),
    }


def checkpoint_thread(thread_id: str) -> dict[str, Any]:
    payload = read_checkpoint()
    for thread in payload.get("threads") or []:
        if isinstance(thread, dict) and str(thread.get("thread_id") or "") == thread_id:
            return thread
    return {}


# --------------------------------------------------------------------------- #
# turn execution
# --------------------------------------------------------------------------- #
def run_turn(
    *,
    name: str,
    goal: str,
    case_dir: Path,
    reset_session: bool,
    timeout: int,
    max_steps: int,
    required_context_run_id: str = "",
) -> dict[str, Any]:
    spec = {
        "task": goal,
        "max_steps": max_steps,
        "timeout": timeout,
        "runtime_version": "vnext",
    }
    _log("turn_start", name=name, reset_session=reset_session, goal=goal)
    result = client_harness.run_case(
        name,
        spec,
        output_root=case_dir,
        reset_session=reset_session,
        required_context_run_id=required_context_run_id,
    )
    run_id = str((result.get("result") or {}).get("run_id") or "")
    trace_ids = list(result.get("bound_trace_ids") or [])
    thread_id = ""
    snap = run_detail(run_id).get("thread_id")
    if snap:
        thread_id = str(snap)
    summary = summarize_run(run_id, trace_ids, thread_id)
    summary["name"] = name
    summary["goal"] = goal
    summary["outcome"] = str((result.get("result") or {}).get("outcome") or "")
    summary["status_label"] = str((result.get("result") or {}).get("status_label") or "")
    summary["duration_seconds"] = result.get("duration_seconds")
    summary["runner_error"] = result.get("runner_error") or ""
    summary["screenshots"] = result.get("screenshots") or {}
    summary["report"] = result.get("report") or ""
    _log(
        "turn_end",
        name=name,
        run_id=run_id,
        status=summary["status"],
        outcome=summary["outcome"],
        remaining=summary["remaining_items"],
    )
    return summary


# --------------------------------------------------------------------------- #
# phases
# --------------------------------------------------------------------------- #
def guided_sequence(
    *,
    case_dir: Path,
    label: str,
    first_goal: str,
    guide_goal: str,
    turn_timeout: int,
    max_steps: int,
    max_guided_turns: int,
    restart_after_first: bool = False,
) -> dict[str, Any]:
    """Submit the big goal, then keep sending ``继续`` in the same thread."""

    phase: dict[str, Any] = {
        "label": label,
        "started_at": _now(),
        "turns": [],
        "restart": None,
        "thread_id": "",
        "continuation_chain": [],
    }

    first = run_turn(
        name=f"{label}-turn1",
        goal=first_goal,
        case_dir=case_dir,
        reset_session=True,
        timeout=turn_timeout,
        max_steps=max_steps,
    )
    phase["turns"].append(first)
    phase["thread_id"] = first.get("thread_id") or ""
    first_run_id = first.get("run_id") or ""
    phase["continuation_chain"].append(
        {
            "run_id": first_run_id,
            "status": first.get("status"),
            "remaining": first.get("remaining_items"),
            "checkpoint": checkpoint_thread(phase["thread_id"]),
        }
    )

    if restart_after_first:
        phase["restart"] = restart_product(case_dir, f"{label}-after-turn1")
        restored = {}
        try:
            thread = thread_snapshot(phase["thread_id"])
            restored = {
                "thread_id": thread.get("thread_id"),
                "turn_count": len(thread.get("turns") or []),
                "turn_goals": [
                    str(t.get("goal") or "")[:80]
                    for t in (thread.get("turns") or [])
                    if isinstance(t, dict)
                ],
            }
        except Exception as exc:
            restored = {"error": f"{type(exc).__name__}: {exc}"}
        phase["restart"]["thread_after_restart"] = restored

    required_context_run_id = first_run_id
    for index in range(2, max_guided_turns + 2):
        turn = run_turn(
            name=f"{label}-turn{index}",
            goal=guide_goal,
            case_dir=case_dir,
            reset_session=False,
            timeout=turn_timeout,
            max_steps=max_steps,
            required_context_run_id=required_context_run_id,
        )
        phase["turns"].append(turn)
        phase["continuation_chain"].append(
            {
                "run_id": turn.get("run_id"),
                "status": turn.get("status"),
                "remaining": turn.get("remaining_items"),
                "checkpoint": checkpoint_thread(phase["thread_id"]),
            }
        )
        required_context_run_id = turn.get("run_id") or required_context_run_id
        status = str(turn.get("status") or "").casefold()
        if status in {"done", "completed", "success"}:
            break
        if not turn.get("continuation"):
            # No mechanical followup reason: the run either finished or failed
            # hard; a further bare "继续" is unlikely to add evidence.
            if str(turn.get("outcome") or "") == "terminal" and status not in {
                "reply",
                "waiting_for_followup",
            }:
                break

    phase["finished_at"] = _now()
    phase["checkpoint_final"] = read_checkpoint()
    passed = any(
        bool(t.get("continuation") or t.get("remaining_items")) for t in phase["turns"]
    )
    phase["resumable_evidence_present"] = passed
    return phase


def run(
    *,
    output_root: Path,
    phases: list[str],
    turn_timeout: int,
    max_steps: int,
    max_guided_turns: int,
    controlled_max_model_calls: int,
    controlled_max_turns: int,
) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    case_dir = output_root / f"{timestamp}-mixed-long-guided"
    case_dir.mkdir(parents=True, exist_ok=True)
    backup_path = case_dir / "config.json.bak"
    shutil.copy2(CONFIG_PATH, backup_path)

    report: dict[str, Any] = {
        "kind": "mixed_long_guided_e2e",
        "entrypoint": "electron_client_windows_uia",
        "started_at": _now(),
        "goal": GOAL,
        "phases": [],
        "config_backup": str(backup_path),
    }
    try:
        if "controlled" in phases:
            _log("phase", name="controlled")
            report["budget_change_controlled"] = set_budget(
                max_turns=controlled_max_turns,
                max_runtime_s=360,
                max_model_calls=controlled_max_model_calls,
            )
            report["controlled_restart_pre"] = restart_product(
                case_dir, "controlled-pre"
            )
            report["phases"].append(
                guided_sequence(
                    case_dir=case_dir / "controlled",
                    label="controlled",
                    first_goal=GOAL,
                    guide_goal=GUIDE,
                    turn_timeout=turn_timeout,
                    max_steps=max_steps,
                    max_guided_turns=max_guided_turns,
                    restart_after_first=True,
                )
            )
        if "natural" in phases:
            _log("phase", name="natural")
            restore_budget(backup_path)
            report["natural_restart_pre"] = restart_product(case_dir, "natural-pre")
            report["phases"].append(
                guided_sequence(
                    case_dir=case_dir / "natural",
                    label="natural",
                    first_goal=GOAL,
                    guide_goal=GUIDE,
                    turn_timeout=turn_timeout,
                    max_steps=max_steps,
                    max_guided_turns=max_guided_turns,
                    restart_after_first=False,
                )
            )
    finally:
        restore_budget(backup_path)
        report["config_restored"] = True
        report["budget_after_restore"] = set_budget(
            max_turns=None, max_runtime_s=None, max_model_calls=None
        )
        # A restart is required for the hosted service to re-read config.json.
        report["final_restart"] = restart_product(case_dir, "final-restore")
        report["finished_at"] = _now()

    report_path = case_dir / "report.json"
    _write_json(report_path, report)
    report["report"] = str(report_path)
    _log("finished", report=str(report_path))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phases",
        default="controlled,natural",
        help="comma list: controlled,natural",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--turn-timeout", type=int, default=900)
    parser.add_argument("--max-steps", type=int, default=200)
    parser.add_argument("--max-guided-turns", type=int, default=4)
    parser.add_argument("--controlled-max-model-calls", type=int, default=6)
    parser.add_argument("--controlled-max-turns", type=int, default=4)
    args = parser.parse_args()
    phases = [p.strip() for p in args.phases.split(",") if p.strip()]
    report = run(
        output_root=args.output_root.resolve(),
        phases=phases,
        turn_timeout=args.turn_timeout,
        max_steps=args.max_steps,
        max_guided_turns=args.max_guided_turns,
        controlled_max_model_calls=args.controlled_max_model_calls,
        controlled_max_turns=args.controlled_max_turns,
    )
    # Print a compact verdict.
    verdict = {
        "report": report.get("report"),
        "phases": [
            {
                "label": p.get("label"),
                "thread_id": p.get("thread_id"),
                "resumable_evidence_present": p.get("resumable_evidence_present"),
                "turns": [
                    {
                        "name": t.get("name"),
                        "run_id": t.get("run_id"),
                        "status": t.get("status"),
                        "remaining_items": t.get("remaining_items"),
                        "last_todos": (t.get("trace") or {}).get("last_todos"),
                    }
                    for t in p.get("turns") or []
                ],
            }
            for p in report.get("phases") or []
        ],
    }
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
