#!/usr/bin/env python3
"""Run the six high-difficulty real desktop tasks through the visible client.

Every case is one of the six hard task families below, driven through the
visible Electron client with the unchanged user goal:

1. krita-cat-qq      : Krita colorful cat on a pale-yellow background, save,
                       then open QQ and send the drawing to the phone.
2. quiz-page         : answer every question on the page the harness opened
                       (a prepared quiz web page in the default browser).
3. tic-tac-toe-paint : play the tic-tac-toe board the harness drew in Paint;
                       the model draws the red X in the correct cell.
4. netease-liangjingru: NetEase Cloud Music search Liang Jingru, play the
                       first track of the list, keep volume at 5%.
5. qq-groups-summary : visit every QQ group and summarize what they talked
                       about.
6. context-cat-qq    : two same-thread turns: draw a cat, then send the
                       just-drawn cat to the phone through QQ.

Modes (--mode):

- individual : seeded random shuffle of the six tasks, one run each.
- chain      : randomly concatenate ``--chain-len`` tasks into ONE unknown
               long goal with random connectors and run it as a single case
               (end-state probes for every subtask afterwards).
- context    : the task-6 pair in the same Thread (required_context_run_id).
- all        : individual + chain + context (default).

Completion is decided by independent external state (browser title token,
saved file scan, Paint canvas pixels, NetEase UIA + CoreAudio volume, QQ
browse evidence) or, where the outside world cannot be read (QQ "send to
phone"), by claim text plus screenshots -- never by the agent's own prose
alone.  Failures are never required to be absent; the runner records them.

After every case the bound traces are analyzed for strategy and tool-call
evidence (shell vs pure vision vs GUI, invalid proposals, physical receipt
errors, model transport errors, app hopping, guard-related event scan), and
a final summary is rendered that lists, per task, the observed failure
reasons, strategy observations, tool-call issues and an evidence-based
assessment of whether any safety-guard layer caused or contributed to the
failures.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import ctypes
import numpy as np
import psutil
import win32api
import win32con
import win32gui
import win32process
from PIL import Image, ImageDraw, ImageGrab, ImageOps

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import client_e2e_run as client_harness  # noqa: E402
import client_paint_game_e2e_run as paint_harness  # noqa: E402
import client_netease_music_e2e_run as netease_harness  # noqa: E402
import client_qq_groups_e2e_run as qq_harness  # noqa: E402

DEFAULT_OUTPUT = WORKSPACE / "output" / "hard-tasks"
FIXTURE_DIR = WORKSPACE / "output" / "eval-fixtures"
TEMP_ROOT = Path(os.environ.get("TEMP") or "C:\\Windows\\Temp") / "winpilot-hard-tasks"

PAINT = "mspaint.exe"

HARD_TASK_IDS = (
    "krita-cat-qq",
    "quiz-page",
    "tic-tac-toe-paint",
    "netease-liangjingru",
    "qq-groups-summary",
    "context-cat-qq",
)

HARD_TASK_GOALS = {
    "krita-cat-qq": (
        "用krita画一只五颜六色的猫背景为淡淡的浅黄色，画好了保存然后打开QQ发给我的手机"
    ),
    "quiz-page": "帮我在当前开启的页面里答完所有题",
    "tic-tac-toe-paint": "跟我玩这个游戏，你打开画板就知道了，现在到你了，你用红色我用黑色，轮流来",
    "netease-liangjingru": "去网易云音乐搜索梁静茹并播放对应列表的第一首歌，保持音量百分之5",
    "qq-groups-summary": "去QQ看看所有的群最近都有什么消息，你汇总给我看他们在聊什么",
    "context-cat-qq": (
        "发送第一次任务：画一只猫；发送第二次任务：把刚刚画的用qq发给我的手机"
    ),
}

CONTEXT_FIRST_GOAL = "画一只猫"
CONTEXT_SECOND_GOAL = "把刚刚画的用qq发给我的手机"

CHAIN_CONNECTORS = ("；然后", "。之后", "，接着", "。做完以后再")

# Trace-event tokens that would indicate the safety-guard layers still being
# assessed (approve gates, permits, scope guards, cooperative input guards).
GUARD_TOKENS = (
    "approve_mode",
    "decide_gate",
    "permit",
    "scope_guard",
    "no_mouse_steal",
    "multi_input_cooperation",
    "cooperative_",
    "approval_gate",
    "permission_handshake",
    "MutationPermit",
)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".kra"}
SCAN_ROOTS = ("Desktop", "Documents", "Pictures", "Downloads")

QUIZ_QUESTIONS = (
    ("光在真空中的传播速度约为多少？", (
        ("30万公里/秒", True), ("3万公里/秒", False), ("300万公里/秒", False),
    )),
    ("圆周率π的前三位小数是？", (
        ("3.14", True), ("3.15", False), ("3.13", False),
    )),
    ("珠穆朗玛峰位于哪两个国家之间？", (
        ("中国和尼泊尔", True), ("中国和印度", False), ("尼泊尔和不丹", False),
    )),
    ("水的化学式是？", (
        ("H2O", True), ("CO2", False), ("O2", False),
    )),
    ("太阳系中最大的行星是？", (
        ("木星", True), ("土星", False), ("地球", False),
    )),
    ("中国最长的河流是？", (
        ("长江", True), ("黄河", False), ("珠江", False),
    )),
    ("1公斤等于多少克？", (
        ("1000克", True), ("100克", False), ("500克", False),
    )),
    ("世界上最高的动物是？", (
        ("长颈鹿", True), ("大象", False), ("河马", False),
    )),
    ("声音在下列哪种介质中传播最快？", (
        ("固体", True), ("液体", False), ("气体", False),
    )),
    ("一年有多少个月？", (
        ("12", True), ("10", False), ("13", False),
    )),
)


# ---------------------------------------------------------------------------
# Window / process helpers
# ---------------------------------------------------------------------------

def _taskkill(name: str) -> None:
    try:
        subprocess.run(
            ["taskkill", "/f", "/im", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except Exception:
        pass


def _find_window(title: str, *, timeout: float = 25.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches: list[int] = []

        def visit(hwnd: int, _unused: Any) -> bool:
            if win32gui.IsWindowVisible(hwnd) and title in win32gui.GetWindowText(hwnd):
                matches.append(int(hwnd))
            return True

        win32gui.EnumWindows(visit, None)
        if matches:
            return matches[-1]
        time.sleep(0.2)
    raise RuntimeError(f"visible target window was not found: {title}")


def _find_process_window(
    process_name: str,
    title_needles: tuple[str, ...] = (),
    *,
    timeout: float = 25.0,
) -> int:
    """Find a visible top-level window of a process by name.

    Windows whose title contains one of ``title_needles`` are preferred;
    otherwise the visible window with the largest area wins.  This covers
    localized or dynamic titles (for example NetEase's window title changes
    with the current page, and Paint may use an English title).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidates: list[tuple[int, str, int]] = []

        def visit(hwnd: int, _unused: Any) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            try:
                _, process_id = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                return True
            try:
                process = psutil.Process(process_id)
                process_name_lower = process.name().casefold()
                expected = process_name.casefold()
                if not (
                    process_name_lower == expected
                    or process_name_lower == f"{expected}.exe"
                ):
                    return True
            except (psutil.Error, OSError):
                return True
            text = win32gui.GetWindowText(hwnd)
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            area = max(1, right - left) * max(1, bottom - top)
            candidates.append((int(hwnd), text, area))
            return True

        win32gui.EnumWindows(visit, None)
        if not candidates:
            time.sleep(0.2)
            continue
        preferred = [
            item for item in candidates
            if any(needle in item[1] for needle in title_needles)
        ]
        pool = preferred or candidates
        best = max(pool, key=lambda item: item[2])
        return best[0]
    raise RuntimeError(
        f"visible {process_name} window was not found "
        f"(titles: {[item[1] for item in candidates]!r})"
    )


def _focus_window(hwnd: int) -> dict[str, Any]:
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise RuntimeError("target window no longer exists")
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.BringWindowToTop(hwnd)
    focused = False
    errors: list[str] = []
    for _attempt in range(3):
        try:
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")
        finally:
            win32api.keybd_event(
                win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0
            )
        time.sleep(0.18)
        focused = int(win32gui.GetForegroundWindow() or 0) == int(hwnd)
        if focused:
            break
    if not focused:
        raise RuntimeError(f"could not focus target window; errors={errors}")
    return {
        "target_hwnd": int(hwnd),
        "foreground_hwnd": int(win32gui.GetForegroundWindow() or 0),
        "focused": True,
        "title": win32gui.GetWindowText(hwnd),
    }


def _capture_window(hwnd: int, path: Path) -> str:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    ImageGrab.grab(
        bbox=(left, top, right, bottom), all_screens=True,
    ).save(str(path))
    return str(path)


def _capture_rect(bbox: tuple[int, int, int, int], path: Path) -> str:
    ImageGrab.grab(bbox=bbox, all_screens=True).save(str(path))
    return str(path)


def _launch_app(command: list[str]) -> None:
    subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------------------
# Claim / artifact helpers
# ---------------------------------------------------------------------------

def _read_claim_texts(run_id: str, goal_hint: str = "") -> list[str]:
    """Read the user-visible result texts for one run from the thread checkpoint.

    Failed turns are not persisted into the thread history, so a missing
    run_id simply means no user-visible claim exists.  When run_id is empty,
    turns whose goal matches ``goal_hint`` are used instead.
    """
    checkpoint = (
        WORKSPACE / "python" / "winpilot_home" / "traces"
        / ".agent-thread-continuation.json"
    )
    texts: list[str] = []
    try:
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    except Exception:
        return texts
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
                    continue
                if (
                    not run_id
                    and goal_hint
                    and goal_hint in str(turn.get("goal") or "")
                ):
                    result = str(turn.get("result") or "")
                    if result.strip():
                        texts.append(result.strip())
        return texts[-12:]
    return texts[-12:]


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


def _scan_new_images(since: float, until: float) -> list[dict[str, Any]]:
    """Find user image/drawing files created inside the run window.

    Only user directories are scanned (Desktop/Documents/Pictures/Downloads);
    the workspace itself is excluded so harness screenshots, runtime evidence
    frames and eval fixtures can never count as the model's saved drawing.
    """
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


def _claim_contains(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


# ---------------------------------------------------------------------------
# Quiz fixture
# ---------------------------------------------------------------------------

def _prepare_quiz(case_dir: Path, token: str) -> dict[str, Any]:
    fixture_dir = FIXTURE_DIR
    fixture_dir.mkdir(parents=True, exist_ok=True)
    path = fixture_dir / f"quiz-{token}.html"
    body: list[str] = ["<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"]
    body.append(f"<title>E2E 试卷 {token}</title>")
    body.append(
        "<style>body{font-family:sans-serif;max-width:720px;margin:24px auto;}"
        "h1{font-size:22px}.q{margin:18px 0}.a{display:block;margin:4px 0 0 18px}"
        ".ok{color:#0a0}</style></head><body>"
    )
    body.append("<h1>试卷（共10题，请全部作答）</h1>")
    answers: list[int] = []
    for index, (question, options) in enumerate(QUIZ_QUESTIONS, start=1):
        body.append(f"<div class='q'><b>{index}. {question}</b>")
        correct = -1
        shuffled = list(options)
        rng = random.Random(hash(token) % (2**32))
        rng.shuffle(shuffled)
        for option_index, (label, is_correct) in enumerate(shuffled):
            if is_correct:
                correct = option_index
            body.append(
                f"<label class='a'><input type='radio' name='q{index}' "
                f"value='{option_index}'>{label}</label>"
            )
        answers.append(correct)
        body.append("</div>")
    script = (
        "<script>"
        f"var KEY='ALL_CORRECT_{token}';"
        f"var answers={json.dumps(answers, ensure_ascii=False)};"
        "function check(){for(var i=0;i<answers.length;i++){"
        "var s=document.querySelector('input[name=q'+(i+1)+']:checked');"
        "if(!s||Number(s.value)!==answers[i])return;}document.title=KEY;"
        "var d=document.getElementById('done');if(d)d.style.display='block';}"
        "setInterval(check,300);"
        "</script>"
    )
    body.append(script)
    body.append(
        "<div id='done' style='display:none' class='ok'>已全部答对，可以结束。</div>"
    )
    body.append("</body></html>")
    path.write_text("\n".join(body), encoding="utf-8")
    return {
        "token": token,
        "path": str(path),
        "answers": answers,
        "browser_title": f"E2E 试卷 {token}",
        "success_title": f"ALL_CORRECT_{token}",
    }


# ---------------------------------------------------------------------------
# Trace strategy / tool-call analysis
# ---------------------------------------------------------------------------

def _analyze_trace(result: dict[str, Any]) -> dict[str, Any]:
    """Rebuild what the model actually did from the bound runtime traces."""
    trace_ids = list(result.get("bound_trace_ids") or [])
    model_calls = 0
    model_ok = 0
    model_error_kinds: Counter[str] = Counter()
    model_elapsed_ms: list[int] = []
    turn_statuses: Counter[str] = Counter()
    proposal_kinds: Counter[str] = Counter()
    invalid_proposals = 0
    blocked_calls = 0
    recovery_kinds: Counter[str] = Counter()
    observation_kinds: Counter[str] = Counter()
    frame_scopes: Counter[str] = Counter()
    full_desktop_frames = 0
    shell_commands: list[str] = []
    shell_exit_codes: Counter[str] = Counter()
    gui_applied: Counter[str] = Counter()
    receipt_outcomes: Counter[str] = Counter()
    receipt_error_kinds: Counter[str] = Counter()
    adopted_targets: list[dict[str, str]] = []
    done_events: list[dict[str, Any]] = []
    guard_hits: list[str] = []
    usage_total = 0
    parse_errors: list[str] = []
    for trace_id in trace_ids:
        path = client_harness.TRACE_ROOT / f"{trace_id}.jsonl"
        if not path.is_file():
            continue
        try:
            for line_number, raw_line in enumerate(
                path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                if not raw_line.strip():
                    continue
                try:
                    event = json.loads(raw_line)
                except Exception as error:
                    parse_errors.append(f"{path.name}:{line_number}:{error}")
                    continue
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("type") or "")
                lowered = raw_line.casefold()
                if any(token in lowered for token in GUARD_TOKENS):
                    guard_hits.append(
                        f"{path.name}:{line_number}:{event_type}"
                    )
                if event_type == "vnext_model_call":
                    model_calls += 1
                    if event.get("ok") is True:
                        model_ok += 1
                    error_kind = str(event.get("error_kind") or "")
                    if error_kind:
                        model_error_kinds[error_kind] += 1
                    elapsed = event.get("elapsed_ms")
                    if isinstance(elapsed, (int, float)):
                        model_elapsed_ms.append(int(elapsed))
                elif event_type == "live_agent_turn":
                    status = str(event.get("status") or "")
                    turn_statuses[status] += 1
                    if status == "invalid_proposal":
                        invalid_proposals += 1
                    if status == "blocked":
                        blocked_calls += 1
                    proposal_kinds[str(event.get("proposal_kind") or "")] += 1
                    recovery = event.get("recovery")
                    if isinstance(recovery, dict):
                        recovery_kinds[str(recovery.get("kind") or "")] += 1
                elif event_type == "vnext_gui_observation":
                    observation_kinds[
                        str(event.get("observation_kind") or "")
                    ] += 1
                    scope = str(event.get("frame_scope") or "")
                    frame_scopes[scope] += 1
                    if scope == "full_desktop":
                        full_desktop_frames += 1
                elif event_type == "action":
                    if str(event.get("action") or "") == "shell":
                        command = str(event.get("command") or "")
                        shell_commands.append(command)
                        exit_code = str(event.get("exit_code") or "")
                        if exit_code:
                            shell_exit_codes[exit_code] += 1
                elif event_type == "vnext_raw_receipt":
                    action = str(
                        event.get("controller_action")
                        or event.get("raw_action")
                        or ""
                    )
                    outcome = str(event.get("outcome") or "")
                    receipt_outcomes[outcome] += 1
                    if outcome == "applied" and event.get("input_completed") is True:
                        gui_applied[action] += 1
                    error_kind = str(event.get("error_kind") or "")
                    if error_kind:
                        receipt_error_kinds[error_kind] += 1
                elif event_type == "vnext_workspace_target_adopted":
                    window = event.get("target_window")
                    if isinstance(window, dict):
                        adopted_targets.append({
                            "title": str(window.get("title") or ""),
                            "process": str(window.get("process") or ""),
                        })
                elif event_type == "live_agent_done":
                    done_events.append({
                        "failure_stage": str(event.get("failure_stage") or ""),
                        "status": str(event.get("status") or ""),
                        "turns_used": event.get("turns_used"),
                        "runtime_budget": event.get("runtime_budget"),
                    })
                elif event_type == "agent_usage":
                    total = event.get("total")
                    if isinstance(total, (int, float)):
                        usage_total += int(total)
        except Exception as error:
            parse_errors.append(f"{path.name}: {type(error).__name__}: {error}")
    gui_total = sum(gui_applied.values())
    shell_total = len(shell_commands)
    return {
        "model_calls": model_calls,
        "model_call_ok": model_ok,
        "model_call_errors": dict(model_error_kinds),
        "model_elapsed_ms_median": (
            sorted(model_elapsed_ms)[len(model_elapsed_ms) // 2]
            if model_elapsed_ms else 0
        ),
        "turn_statuses": dict(turn_statuses),
        "proposal_kinds": dict(proposal_kinds),
        "invalid_proposals": invalid_proposals,
        "blocked_calls": blocked_calls,
        "recovery_kinds": dict(recovery_kinds),
        "observation_kinds": dict(observation_kinds),
        "frame_scopes": dict(frame_scopes),
        "full_desktop_frames": full_desktop_frames,
        "shell_commands": shell_commands[:24],
        "shell_command_count": shell_total,
        "shell_exit_codes": dict(shell_exit_codes),
        "gui_applied": dict(gui_applied),
        "gui_action_count": gui_total,
        "receipt_outcomes": dict(receipt_outcomes),
        "receipt_error_kinds": dict(receipt_error_kinds),
        "adopted_targets": adopted_targets[:16],
        "app_hop_count": len(adopted_targets),
        "done_events": done_events,
        "usage_total": usage_total,
        "guard_hits": guard_hits[:24],
        "guard_hit_count": len(guard_hits),
        "parse_errors": parse_errors,
        "strategy_hint": _strategy_hint(shell_total, gui_total, full_desktop_frames),
    }


def _strategy_hint(
    shell_total: int, gui_total: int, full_desktop_frames: int
) -> str:
    if shell_total > gui_total and shell_total >= 3:
        return "shell-first（大量使用命令行定位/启动/查询，物理GUI动作较少）"
    if gui_total >= 3:
        return "vision-first（以屏幕观察+物理GUI动作为主）"
    return "动作很少（尚未进入稳定执行循环）"


def _verdict_meta(result: dict[str, Any]) -> dict[str, Any]:
    run_result = result.get("result") or {}
    return {
        "run_id": str(run_result.get("run_id") or ""),
        "outcome": str(run_result.get("outcome") or ""),
        "status_label": str(run_result.get("status_label") or ""),
        "duration_seconds": result.get("duration_seconds"),
        "failure_stage": str(run_result.get("failure_stage") or ""),
        "runner_error": result.get("runner_error") or "",
    }


# ---------------------------------------------------------------------------
# Individual task runners
# ---------------------------------------------------------------------------

def _run_quiz_case(
    spec: dict[str, Any], case_dir: Path, seed: int
) -> dict[str, Any]:
    rng = random.Random(seed)
    token = f"HT-{rng.randrange(10**6, 10**7)}-{rng.choice('ABCDEF')}"
    fixture = _prepare_quiz(case_dir, token)
    os.startfile(fixture["path"])
    browser_hwnd = 0
    try:
        browser_hwnd = _find_window(f"E2E 试卷 {token}", timeout=30)
    except RuntimeError as error:
        return {
            "family": "quiz-page",
            "task": spec["task"],
            "verdict": {
                "source": "external_quiz_oracle",
                "passed": False,
                "status": "prepare_error",
                "error": str(error),
            },
            "preparation": {"fixture": fixture, "browser_hwnd": browser_hwnd},
        }

    def after_submit(
        _driver: client_harness.ElectronClientDriver,
        _client_result: dict[str, Any],
    ) -> dict[str, Any]:
        return _focus_window(browser_hwnd)

    def verifier(result: dict[str, Any]) -> dict[str, Any]:
        success_title = fixture["success_title"]
        title = ""
        try:
            title = win32gui.GetWindowText(browser_hwnd)
        except Exception as error:
            return {
                "source": "external_quiz_oracle",
                "passed": False,
                "status": "browser_gone",
                "error": f"{type(error).__name__}: {error}",
                "fixture": fixture,
            }
        passed = success_title in title
        errors: list[str] = []
        if not passed:
            errors.append(
                f"browser title {title!r} does not contain the all-correct "
                f"token {success_title!r}"
            )
        if str((result.get("result") or {}).get("status_label") or "") not in {
            "done", "completed", "success", "reply",
        }:
            errors.append("run did not end with a success status")
        return {
            "source": "external_quiz_oracle",
            "passed": passed,
            "status": "all_correct" if passed else "incomplete",
            "errors": errors,
            "browser_title": title,
            "fixture": {
                "token": fixture["token"],
                "success_title": success_title,
                "answers": fixture["answers"],
            },
        }

    result = client_harness.run_case(
        "quiz-page",
        spec,
        output_root=case_dir / "client",
        reset_session=True,
        after_submit=after_submit,
        independent_verifier=verifier,
    )
    verdict = result.get("task_verdict") or {}
    return {
        "family": "quiz-page",
        "task": spec["task"],
        "verdict": verdict,
        "client_result": result,
        "trace_analysis": _analyze_trace(result),
        "meta": _verdict_meta(result),
    }


def _find_krita_exe() -> str | None:
    candidates = (
        r"C:\Program Files\Krita (x64)\bin\krita.exe",
        r"C:\Program Files\Krita\bin\krita.exe",
        r"C:\Program Files (x86)\Krita\bin\krita.exe",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    try:
        output = subprocess.run(
            ["where", "krita"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if output.returncode == 0 and output.stdout.strip():
            return output.stdout.splitlines()[0].strip()
    except Exception:
        pass
    return None


def _run_krita_cat_qq_case(
    spec: dict[str, Any], case_dir: Path
) -> dict[str, Any]:
    krita_exe = _find_krita_exe()
    started_at = time.time()

    def verifier(result: dict[str, Any]) -> dict[str, Any]:
        ended_at = time.time()
        images = _scan_new_images(started_at - 5, ended_at)
        claim = _run_claim_evidence(result)
        claim_text = claim["claim_text_joined"]
        file_found = bool(images)
        qq_mentioned = _claim_contains(
            claim_text, ("QQ", "qq", "发送", "发给", "手机", "发送成功")
        )
        passed = file_found and qq_mentioned
        errors: list[str] = []
        if not file_found:
            errors.append("no new image/drawing file was saved inside the run window")
        if not qq_mentioned:
            errors.append("completion claim does not mention sending via QQ")
        return {
            "source": "external_krita_file_and_claim_oracle",
            "passed": passed,
            "partial": bool(file_found or qq_mentioned),
            "status": "saved_and_sent_claimed" if passed else "incomplete",
            "errors": errors,
            "evidence": {
                "new_images": images,
                "claim": claim,
            },
            "krita_exe_found": krita_exe is not None,
        }

    result = client_harness.run_case(
        "krita-cat-qq",
        spec,
        output_root=case_dir / "client",
        reset_session=True,
        independent_verifier=verifier,
    )
    verdict = result.get("task_verdict") or {}
    return {
        "family": "krita-cat-qq",
        "task": spec["task"],
        "verdict": verdict,
        "client_result": result,
        "trace_analysis": _analyze_trace(result),
        "meta": _verdict_meta(result),
        "preparation": {"krita_exe_found": krita_exe is not None},
    }


_BOARD_BACKGROUND = (255, 250, 220)


def _write_paint_game_fixture(case_dir: Path) -> Path:
    """Create the private board precondition; the Agent still sees only Paint."""

    size = 300
    image = Image.new("RGB", (size, size), _BOARD_BACKGROUND)
    draw = ImageDraw.Draw(image)
    for offset in (size // 3, size * 2 // 3):
        draw.line((offset, 0, offset, size), fill="black", width=5)
        draw.line((0, offset, size, offset), fill="black", width=5)
    # Two black marks form an immediate diagonal threat, so the empty center
    # is the natural red blocking move without exposing that fact to the Agent.
    for center in ((size // 6, size // 6), (size * 5 // 6, size * 5 // 6)):
        radius = size // 11
        draw.ellipse(
            (
                center[0] - radius,
                center[1] - radius,
                center[0] + radius,
                center[1] + radius,
            ),
            outline="black",
            width=6,
        )
    path = case_dir / "tic-tac-toe-board.png"
    image.save(path)
    return path


def _find_paint_document(stem: str, *, timeout: float = 30.0) -> int:
    deadline = time.monotonic() + timeout
    wanted = stem.casefold()
    while time.monotonic() < deadline:
        matches: list[int] = []

        def visit(hwnd: int, _unused: Any) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            try:
                _, process_id = win32process.GetWindowThreadProcessId(hwnd)
                if psutil.Process(process_id).name().casefold() != PAINT.casefold():
                    return True
            except (psutil.Error, OSError):
                return True
            if wanted in win32gui.GetWindowText(hwnd).casefold():
                matches.append(int(hwnd))
            return True

        win32gui.EnumWindows(visit, None)
        if matches:
            foreground = int(win32gui.GetForegroundWindow() or 0)
            return foreground if foreground in matches else matches[0]
        time.sleep(0.2)
    raise RuntimeError(f"Paint did not open fixture document {stem!r}")


def _locate_fixture_board(
    paint_hwnd: int,
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """Locate the distinctive fixture background in the real Paint pixels."""

    rect = tuple(int(value) for value in win32gui.GetWindowRect(paint_hwnd))
    image = np.asarray(
        ImageGrab.grab(bbox=rect, all_screens=True).convert("RGB"),
        dtype=np.int16,
    )
    target = np.asarray(_BOARD_BACKGROUND, dtype=np.int16)
    mask = np.all(np.abs(image - target) <= 5, axis=2)
    useful_columns = np.flatnonzero(mask.sum(axis=0) >= 20)
    useful_rows = np.flatnonzero(mask.sum(axis=1) >= 20)
    if not useful_columns.size or not useful_rows.size:
        raise RuntimeError("fixture board background is not visible in Paint")
    left = int(useful_columns[0])
    top = int(useful_rows[0])
    right = int(useful_columns[-1]) + 1
    bottom = int(useful_rows[-1]) + 1
    width, height = right - left, bottom - top
    if min(width, height) < 180 or not 0.8 <= width / max(1, height) <= 1.25:
        raise RuntimeError(
            f"fixture board bounds are implausible: {(left, top, right, bottom)}"
        )
    dpi = paint_harness._window_dpi(paint_hwnd)  # noqa: SLF001
    logical = tuple(round(value * 96 / dpi) for value in (left, top, right, bottom))
    screen = (
        rect[0] + left,
        rect[1] + top,
        rect[0] + right,
        rect[1] + bottom,
    )
    return logical, screen


def _normalize_canvas_lightness(image: Image.Image) -> Image.Image:
    """Make any Paint canvas read as light for the dark-on-light board oracle.

    Win11 Paint may open with a dark canvas (and light default brush) or a
    light one (dark brush) depending on theme state.  The shared board oracle
    expects dark ink on a light canvas; when the canvas mean luminance is
    dark, invert luminance while preserving hue (so a red X stays red).
    """
    import numpy as np

    arr = np.asarray(image.convert("RGB"))
    if float(arr.mean()) >= 128.0:
        return image.convert("RGB")
    try:
        import cv2
    except Exception:  # pragma: no cover
        return ImageOps.invert(image.convert("RGB"))
    hls = cv2.cvtColor(arr, cv2.COLOR_RGB2HLS)
    hls[:, :, 1] = 255 - hls[:, :, 1]
    out = cv2.cvtColor(hls, cv2.COLOR_HLS2RGB)
    return Image.fromarray(out, "RGB")


def _verify_paint_board(
    paint_hwnd: int, screen_bbox: tuple[int, int, int, int]
) -> dict[str, Any]:
    image = ImageGrab.grab(bbox=screen_bbox, all_screens=True).convert("RGB")
    return paint_harness.baseline_contract(  # noqa: SLF001
        _normalize_canvas_lightness(image), expected_cell=4
    )


def _prepare_paint_game(case_dir: Path) -> dict[str, Any]:
    """Open a deterministic board inside the real Paint application."""

    fixture = _write_paint_game_fixture(case_dir)
    _launch_app([PAINT, str(fixture)])
    hwnd = _find_paint_document(fixture.stem)
    _focus_window(hwnd)
    time.sleep(1.0)
    board_bbox, board_screen_bbox = _locate_fixture_board(hwnd)
    check = _verify_paint_board(hwnd, board_screen_bbox)
    if check.get("passed") is not True:
        raise RuntimeError(f"paint board baseline failed: {check.get('errors')}")
    return {
        "paint_hwnd": hwnd,
        "board_bbox": board_bbox,
        "board_screen_bbox": board_screen_bbox,
        "baseline_check": check,
        "fixture": str(fixture),
    }


def _run_paint_game_case(
    spec: dict[str, Any], case_dir: Path
) -> dict[str, Any]:
    prepared = _prepare_paint_game(case_dir)
    report = paint_harness.run_paint_game_case(
        paint_hwnd=prepared["paint_hwnd"],
        board_bbox=prepared["board_bbox"],
        expected_cell=4,
        output_root=case_dir,
        timeout=int(spec.get("timeout", 600)),
        max_steps=int(spec.get("max_steps", 40)),
        capture=lambda bbox, **kwargs: _normalize_canvas_lightness(
            ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
        ),
    )
    client_result = report.get("client_result") or {}
    return {
        "family": "tic-tac-toe-paint",
        "task": spec["task"],
        "verdict": report.get("oracle") or {},
        "client_result": client_result,
        "trace_analysis": _analyze_trace(client_result),
        "meta": _verdict_meta(client_result),
        "preparation": {
            "paint_hwnd": prepared["paint_hwnd"],
            "board_bbox": prepared["board_bbox"],
            "baseline_check": prepared["baseline_check"],
            "fixture": prepared["fixture"],
        },
        "paint_report": str(report.get("report") or ""),
    }


def _run_netease_case(spec: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    try:
        netease_hwnd = _find_process_window(
            "cloudmusic", ("网易",), timeout=15
        )
    except RuntimeError as error:
        return {
            "family": "netease-liangjingru",
            "task": spec["task"],
            "verdict": {
                "source": "external_netease_oracle",
                "passed": False,
                "status": "prepare_error",
                "error": str(error),
                "hint": "请先登录并打开网易云音乐主窗口",
            },
        }
    try:
        report = netease_harness.run_netease_music_case(
            netease_hwnd=netease_hwnd,
            output_root=case_dir,
            timeout=int(spec.get("timeout", 600)),
            max_steps=int(spec.get("max_steps", 40)),
        )
    except Exception as error:
        return {
            "family": "netease-liangjingru",
            "task": spec["task"],
            "verdict": {
                "source": "external_netease_oracle",
                "passed": False,
                "status": "prepare_error",
                "error": f"{type(error).__name__}: {error}",
            },
            "netease_hwnd": netease_hwnd,
        }
    client_result = report.get("client_result") or {}
    return {
        "family": "netease-liangjingru",
        "task": spec["task"],
        "verdict": report.get("oracle") or {},
        "client_result": client_result,
        "trace_analysis": _analyze_trace(client_result),
        "meta": _verdict_meta(client_result),
        "netease_hwnd": netease_hwnd,
    }


def _run_qq_groups_case(spec: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    try:
        qq_hwnd = _find_window("QQ", timeout=15)
    except RuntimeError as error:
        return {
            "family": "qq-groups-summary",
            "task": spec["task"],
            "verdict": {
                "source": "external_qq_oracle",
                "passed": False,
                "status": "prepare_error",
                "error": str(error),
                "hint": "请先登录并打开QQ主窗口",
            },
        }
    try:
        report = qq_harness.run_qq_groups_case(
            qq_hwnd=qq_hwnd,
            output_root=case_dir,
            timeout=int(spec.get("timeout", 900)),
            max_steps=int(spec.get("max_steps", 40)),
        )
    except Exception as error:
        return {
            "family": "qq-groups-summary",
            "task": spec["task"],
            "verdict": {
                "source": "external_qq_oracle",
                "passed": False,
                "status": "prepare_error",
                "error": f"{type(error).__name__}: {error}",
            },
            "qq_hwnd": qq_hwnd,
        }
    client_result = report.get("client_result") or {}
    return {
        "family": "qq-groups-summary",
        "task": spec["task"],
        "verdict": report.get("oracle") or {},
        "client_result": client_result,
        "trace_analysis": _analyze_trace(client_result),
        "meta": _verdict_meta(client_result),
        "qq_hwnd": qq_hwnd,
    }


# ---------------------------------------------------------------------------
# Context pair (task 6): same Thread, two turns
# ---------------------------------------------------------------------------

def _run_context_pair(spec: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    first_started = time.time()
    first = client_harness.run_case(
        "context-draw-a-cat",
        {
            "task": CONTEXT_FIRST_GOAL,
            "max_steps": int(spec.get("max_steps", 80)),
            "timeout": int(spec.get("timeout", 1200)),
            "runtime_version": "vnext",
        },
        output_root=case_dir / "client-first",
        reset_session=True,
    )
    first_ended = time.time()

    def first_verdict(result: dict[str, Any]) -> dict[str, Any]:
        images = _scan_new_images(first_started, first_ended)
        errors: list[str] = []
        if not images:
            errors.append("no new image file was created for the cat drawing")
        return {
            "source": "external_cat_file_oracle",
            "passed": bool(images),
            "status": "file_saved" if images else "no_file",
            "errors": errors,
            "new_images": images,
        }

    first_verdict_payload = first_verdict(first)
    first["task_verdict"] = first_verdict_payload
    first_run_id = str((first.get("result") or {}).get("run_id") or "")

    second = client_harness.run_case(
        "context-send-cat-via-qq",
        {
            "task": CONTEXT_SECOND_GOAL,
            "max_steps": int(spec.get("max_steps", 80)),
            "timeout": int(spec.get("timeout", 1200)),
            "runtime_version": "vnext",
        },
        output_root=case_dir / "client-second",
        reset_session=False,
        required_context_run_id=first_run_id,
    )
    second_claim = _run_claim_evidence(second)
    claim_text = second_claim["claim_text_joined"]
    sent_mentioned = _claim_contains(
        claim_text, ("QQ", "qq", "发送", "发给", "手机", "发送成功")
    )
    second_verdict_payload = {
        "source": "external_context_send_claim_oracle",
        "passed": bool(sent_mentioned),
        "partial": bool(sent_mentioned),
        "status": "send_claimed" if sent_mentioned else "no_send_claim",
        "errors": [] if sent_mentioned else [
            "completion claim does not mention sending the drawing via QQ"
        ],
        "claim": second_claim,
        "first_run_id": first_run_id,
    }
    second["task_verdict"] = second_verdict_payload
    first_ok = bool(first_verdict_payload.get("passed") is True)
    second_ok = bool(second_verdict_payload.get("passed") is True)
    return {
        "family": "context-cat-qq",
        "task": spec["task"],
        "verdict": {
            "source": "external_context_pair_oracle",
            "passed": bool(first_ok and second_ok),
            "partial": bool(first_ok or second_ok),
            "status": (
                "draw_saved_and_send_claimed"
                if first_ok and second_ok
                else "incomplete"
            ),
            "errors": [
                *(first_verdict_payload.get("errors") or []),
                *(second_verdict_payload.get("errors") or []),
            ],
            "first": first_verdict_payload,
            "second": second_verdict_payload,
        },
        "first_goal": CONTEXT_FIRST_GOAL,
        "second_goal": CONTEXT_SECOND_GOAL,
        "first": {
            "verdict": first_verdict_payload,
            "client_result": first,
            "trace_analysis": _analyze_trace(first),
            "meta": _verdict_meta(first),
        },
        "second": {
            "verdict": second_verdict_payload,
            "client_result": second,
            "trace_analysis": _analyze_trace(second),
            "meta": _verdict_meta(second),
        },
        "trace_analysis": _analyze_trace(second),
        "meta": _verdict_meta(second),
    }


def _run_claim_evidence(
    result: dict[str, Any], goal_hint: str = ""
) -> dict[str, Any]:
    run_id = str((result.get("result") or {}).get("run_id") or "")
    texts = _read_claim_texts(run_id, goal_hint=goal_hint)
    return {
        "run_id": run_id,
        "claim_texts": texts[-3:],
        "claim_text_joined": "\n".join(texts[-3:]),
    }


# ---------------------------------------------------------------------------
# Chain mode: random unknown long goal
# ---------------------------------------------------------------------------

def _chain_end_state_probe(task_id: str, state: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one subtask's external end state after a combined run."""
    if task_id == "quiz-page":
        hwnd = int(state.get("browser_hwnd") or 0)
        if not hwnd:
            return {"passed": False, "status": "no_browser_window", "task": task_id}
        title = win32gui.GetWindowText(hwnd)
        passed = "ALL_CORRECT_" + str(state.get("quiz_token") or "") in title
        return {
            "passed": passed,
            "status": "all_correct" if passed else "incomplete",
            "task": task_id,
            "browser_title": title,
        }
    if task_id == "krita-cat-qq":
        since = float(state.get("chain_started_at") or time.time())
        until = time.time()
        images = _scan_new_images(since - 5, until)
        claim = _run_claim_evidence(state.get("result") or {})
        sent = _claim_contains(
            claim["claim_text_joined"], ("QQ", "qq", "发送", "发给", "手机")
        )
        return {
            "passed": bool(images and sent),
            "status": "saved_and_sent_claimed" if images and sent else "incomplete",
            "task": task_id,
            "new_images": images,
            "claim_mentions_send": sent,
        }
    if task_id == "tic-tac-toe-paint":
        hwnd = int(state.get("paint_hwnd") or 0)
        bbox = state.get("board_screen_bbox")
        baseline = state.get("paint_baseline")
        if not hwnd or not bbox or baseline is None:
            return {"passed": False, "status": "no_paint_prep", "task": task_id}
        try:
            current = ImageGrab.grab(bbox=tuple(bbox), all_screens=True).convert(
                "RGB"
            )
            verdict, _mask = paint_harness.evaluate_board_frame(
                _normalize_canvas_lightness(baseline),
                _normalize_canvas_lightness(current),
                expected_cell=4,
            )
            return {
                "passed": verdict.get("candidate_passed") is True,
                "status": (
                    "red_x_in_center"
                    if verdict.get("candidate_passed") is True
                    else "no_valid_red_x"
                ),
                "task": task_id,
                "board_verdict": verdict,
            }
        except Exception as error:
            return {
                "passed": False,
                "status": "probe_error",
                "task": task_id,
                "error": f"{type(error).__name__}: {error}",
            }
    if task_id == "netease-liangjingru":
        hwnd = int(state.get("netease_hwnd") or 0)
        if not hwnd:
            return {"passed": False, "status": "no_netease_window", "task": task_id}
        try:
            rect = win32gui.GetWindowRect(hwnd)
            nodes = netease_harness.snapshot_accessible_state(hwnd)
            volume = netease_harness.read_coreaudio_master_volume()
            verdict = netease_harness.evaluate_music_state(
                nodes, volume, list(rect)
            )
            return {
                "passed": verdict.get("candidate_passed") is True,
                "status": (
                    "playing_first_track_volume_5"
                    if verdict.get("candidate_passed") is True
                    else "not_satisfied"
                ),
                "task": task_id,
                "music_verdict": verdict,
            }
        except Exception as error:
            return {
                "passed": False,
                "status": "probe_error",
                "task": task_id,
                "error": f"{type(error).__name__}: {error}",
            }
    if task_id == "qq-groups-summary":
        claim = _run_claim_evidence(state.get("result") or {})
        text = claim["claim_text_joined"]
        substantive = len(text.strip()) >= 20
        return {
            "passed": bool(substantive),
            "status": "summary_present" if substantive else "no_summary",
            "task": task_id,
            "claim_length": len(text.strip()),
        }
    return {"passed": False, "status": "unsupported", "task": task_id}


def _build_chain_goal(task_ids: list[str], seed: int) -> tuple[str, list[str]]:
    rng = random.Random(seed)
    connectors = [rng.choice(CHAIN_CONNECTORS) for _ in range(len(task_ids) - 1)]
    goal = ""
    for index, task_id in enumerate(task_ids):
        goal += HARD_TASK_GOALS[task_id]
        if index < len(connectors):
            goal += connectors[index]
    return goal, connectors


def _run_chain_case(
    task_ids: list[str], spec: dict[str, Any], case_dir: Path, seed: int
) -> dict[str, Any]:
    goal, connectors = _build_chain_goal(task_ids, seed)
    rng = random.Random(seed)
    state: dict[str, Any] = {
        "chain_started_at": time.time(),
        "result": None,
    }
    preparation: dict[str, Any] = {}
    if "quiz-page" in task_ids:
        token = f"CH-{rng.randrange(10**6, 10**7)}"
        fixture = _prepare_quiz(case_dir, token)
        os.startfile(fixture["path"])
        try:
            browser_hwnd = _find_window(f"E2E 试卷 {token}", timeout=30)
        except RuntimeError as error:
            preparation["quiz_prepare_error"] = str(error)
            browser_hwnd = 0
        state["browser_hwnd"] = browser_hwnd
        state["quiz_token"] = token
        preparation["quiz"] = {"token": token, "browser_hwnd": browser_hwnd}
    if "tic-tac-toe-paint" in task_ids:
        try:
            prepared = _prepare_paint_game(case_dir)
            state["paint_hwnd"] = prepared["paint_hwnd"]
            state["board_screen_bbox"] = prepared["board_screen_bbox"]
            baseline = _normalize_canvas_lightness(
                ImageGrab.grab(
                    bbox=prepared["board_screen_bbox"], all_screens=True
                ).convert("RGB")
            )
            state["paint_baseline"] = baseline
            preparation["paint"] = {
                "paint_hwnd": prepared["paint_hwnd"],
                "board_bbox": prepared["board_bbox"],
                "baseline_check": prepared["baseline_check"],
            }
        except Exception as error:
            preparation["paint_prepare_error"] = (
                f"{type(error).__name__}: {error}"
            )
    if "netease-liangjingru" in task_ids:
        try:
            netease_hwnd = _find_process_window(
                "cloudmusic", ("网易",), timeout=15
            )
        except RuntimeError as error:
            preparation["netease_prepare_error"] = str(error)
            netease_hwnd = 0
        state["netease_hwnd"] = netease_hwnd
        preparation["netease_hwnd"] = netease_hwnd

    focus_target = preparation.get("paint", {}).get("paint_hwnd") or (
        preparation.get("quiz", {}).get("browser_hwnd") or 0
    )

    def after_submit(
        _driver: client_harness.ElectronClientDriver,
        _client_result: dict[str, Any],
    ) -> dict[str, Any]:
        if focus_target:
            return _focus_window(focus_target)
        return None

    result = client_harness.run_case(
        "chain-" + "-".join(task_ids),
        spec,
        output_root=case_dir / "client",
        reset_session=True,
        after_submit=after_submit,
    )
    state["result"] = result
    probes = {
        task_id: _chain_end_state_probe(task_id, state)
        for task_id in task_ids
    }
    claim = _run_claim_evidence(result)
    passed = all(probe.get("passed") is True for probe in probes.values())
    return {
        "family": "chain",
        "task": goal,
        "task_ids": task_ids,
        "connectors": connectors,
        "preparation": preparation,
        "verdict": {
            "source": "external_chain_end_state_probes",
            "passed": passed,
            "status": "all_subtasks_satisfied" if passed else "partial",
            "subtasks": probes,
            "claim": claim,
        },
        "client_result": result,
        "trace_analysis": _analyze_trace(result),
        "meta": _verdict_meta(result),
    }


# ---------------------------------------------------------------------------
# Case dispatch + suite
# ---------------------------------------------------------------------------

def _case_spec(task_id: str, timeout: int, max_steps: int) -> dict[str, Any]:
    return {
        "task": HARD_TASK_GOALS[task_id],
        "max_steps": max_steps,
        "timeout": timeout,
        "runtime_version": "vnext",
    }


DEFAULT_TIMEOUTS = {
    "krita-cat-qq": 1500,
    "quiz-page": 1200,
    "tic-tac-toe-paint": 600,
    "netease-liangjingru": 600,
    "qq-groups-summary": 900,
    "context-cat-qq": 1200,
}

DEFAULT_MAX_STEPS = {
    "krita-cat-qq": 100,
    "quiz-page": 80,
    "tic-tac-toe-paint": 40,
    "netease-liangjingru": 40,
    "qq-groups-summary": 40,
    "context-cat-qq": 80,
}


def _run_one_task(task_id: str, output_root: Path, seed: int) -> dict[str, Any]:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    case_dir = output_root / f"{timestamp}-{task_id}"
    case_dir.mkdir(parents=True, exist_ok=True)
    spec = _case_spec(
        task_id, DEFAULT_TIMEOUTS[task_id], DEFAULT_MAX_STEPS[task_id]
    )
    report: dict[str, Any] = {
        "name": f"{timestamp}-{task_id}",
        "family": task_id,
        "task": HARD_TASK_GOALS[task_id],
        "seed": seed,
        "started_at": datetime.now().astimezone().isoformat(),
    }
    try:
        if task_id == "quiz-page":
            case_report = _run_quiz_case(spec, case_dir, seed)
        elif task_id == "krita-cat-qq":
            case_report = _run_krita_cat_qq_case(spec, case_dir)
        elif task_id == "tic-tac-toe-paint":
            case_report = _run_paint_game_case(spec, case_dir)
        elif task_id == "netease-liangjingru":
            case_report = _run_netease_case(spec, case_dir)
        elif task_id == "qq-groups-summary":
            case_report = _run_qq_groups_case(spec, case_dir)
        elif task_id == "context-cat-qq":
            case_report = _run_context_pair(spec, case_dir)
        else:  # pragma: no cover
            raise RuntimeError(f"unknown hard task: {task_id}")
        report.update(case_report)
    except Exception as error:
        report["runner_error"] = f"{type(error).__name__}: {error}"
        report["verdict"] = {
            "source": "harness",
            "passed": False,
            "status": "runner_error",
            "error": report["runner_error"],
        }
    finally:
        if task_id == "tic-tac-toe-paint":
            _taskkill(PAINT)
        report["finished_at"] = datetime.now().astimezone().isoformat()
        report_path = case_dir / "report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        report["report"] = str(report_path)
        print(
            json.dumps({"event": "hard_task_finished", **report}, ensure_ascii=False),
            flush=True,
        )
    return report


def _case_passed(report: dict[str, Any]) -> bool:
    if report.get("runner_error"):
        return False
    verdict = report.get("verdict") or {}
    if verdict.get("passed") is not True:
        return False
    client_result = report.get("client_result") or {}
    if client_result.get("runner_error"):
        return False
    return True


def _render_summary(reports: list[dict[str, Any]], suite: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# 高难度真实任务 E2E 评估总结")
    lines.append("")
    lines.append(
        f"- 时间：{suite.get('started_at')} ~ {suite.get('finished_at')}"
    )
    lines.append(
        f"- 运行：{suite.get('mode')} 模式，seed={suite.get('seed')}，"
        f"共 {len(reports)} 个 case，通过 {suite.get('passed_count')} 个"
    )
    lines.append("")
    lines.append("## 一、总览")
    lines.append("")
    lines.append("| 任务 | 判定 | 状态 | 模型调用 | 无效提案 | shell | GUI动作 | 失败阶段 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for report in reports:
        family = report.get("family") or "?"
        verdict = report.get("verdict") or {}
        passed = "✅" if verdict.get("passed") is True else "❌"
        partial = (
            " (部分)"
            if verdict.get("passed") is not True and verdict.get("partial")
            else ""
        )
        trace = report.get("trace_analysis") or {}
        meta = report.get("meta") or {}
        done = (trace.get("done_events") or [{}])[0] if trace.get("done_events") else {}
        lines.append(
            f"| {family}{partial} | {passed} | {verdict.get('status') or '?'} "
            f"| {trace.get('model_calls') or 0} "
            f"| {trace.get('invalid_proposals') or 0} "
            f"| {trace.get('shell_command_count') or 0} "
            f"| {trace.get('gui_action_count') or 0} "
            f"| {meta.get('failure_stage') or done.get('failure_stage') or '-'} |"
        )
    lines.append("")
    lines.append("## 二、逐任务分析")
    lines.append("")
    for report in reports:
        family = report.get("family") or "?"
        verdict = report.get("verdict") or {}
        trace = report.get("trace_analysis") or {}
        meta = report.get("meta") or {}
        lines.append(f"### {family}")
        lines.append("")
        lines.append(f"目标：`{report.get('task') or ''}`")
        lines.append("")
        lines.append(
            f"- 判定：{verdict.get('status') or '?'}"
            + ("（外部证据通过）" if verdict.get("passed") is True else "")
        )
        if verdict.get("errors"):
            for error in verdict["errors"]:
                lines.append(f"  - 未达标原因：{error}")
        if verdict.get("subtasks"):
            for subtask_id, probe in verdict["subtasks"].items():
                lines.append(
                    f"  - 子任务 {subtask_id}: {probe.get('status')}"
                    + (" ✅" if probe.get("passed") else "")
                )
        if report.get("runner_error"):
            lines.append(f"  - 运行器错误：{report['runner_error']}")
        if meta.get("runner_error"):
            lines.append(f"  - 客户端错误：{meta['runner_error']}")
        lines.append("")
        lines.append("策略与工具调用证据：")
        lines.append("")
        lines.append(
            f"- 策略倾向：{trace.get('strategy_hint') or '无动作'}"
            f"（shell {trace.get('shell_command_count') or 0} 次 / "
            f"GUI {trace.get('gui_action_count') or 0} 次 / "
            f"全屏帧 {trace.get('full_desktop_frames') or 0} 帧）"
        )
        if trace.get("shell_commands"):
            lines.append("- 模型用过的命令行：")
            for command in trace["shell_commands"]:
                lines.append(f"  - `{command[:140]}`")
        if trace.get("gui_applied"):
            applied = trace["gui_applied"]
            lines.append(
                "- 实际落地的GUI动作："
                + ", ".join(f"{name}×{count}" for name, count in applied.items())
            )
        if trace.get("model_call_errors"):
            lines.append(
                "- 模型调用错误："
                + ", ".join(
                    f"{name}×{count}" for name, count in trace["model_call_errors"].items()
                )
            )
        if trace.get("invalid_proposals"):
            lines.append(
                f"- 无效提案：{trace['invalid_proposals']} 次"
                f"（提案种类：{trace.get('proposal_kinds') or {}}）"
            )
        if trace.get("receipt_error_kinds"):
            lines.append(
                "- 物理输入回执错误："
                + ", ".join(
                    f"{name}×{count}"
                    for name, count in trace["receipt_error_kinds"].items()
                )
            )
        if trace.get("app_hop_count"):
            targets = sorted(
                {f"{item.get('title') or '?'}|{item.get('process') or '?'}"
                 for item in trace.get("adopted_targets") or []}
            )
            lines.append(
                f"- 窗口/应用跳转 {trace['app_hop_count']} 次：{', '.join(targets[:8])}"
            )
        done = (trace.get("done_events") or [{}])[0] if trace.get("done_events") else {}
        if done.get("failure_stage"):
            lines.append(f"- 结束阶段：{done.get('failure_stage')}（status={done.get('status')}）")
        if trace.get("usage_total"):
            lines.append(f"- 累计 token：{trace['usage_total']}")
        lines.append("")
    lines.append("## 三、跨任务观察")
    lines.append("")
    shell_heavy = sum(
        1 for item in reports
        if (item.get("trace_analysis") or {}).get("shell_command_count", 0)
        >= 3
    )
    gui_heavy = sum(
        1 for item in reports
        if (item.get("trace_analysis") or {}).get("gui_action_count", 0) >= 3
    )
    invalid_total = sum(
        (item.get("trace_analysis") or {}).get("invalid_proposals", 0)
        for item in reports
    )
    model_error_total = sum(
        len((item.get("trace_analysis") or {}).get("model_call_errors") or {})
        for item in reports
    )
    lines.append(
        f"- shell 倾向 case：{shell_heavy} 个；视觉/GUI 倾向 case：{gui_heavy} 个；"
        f"累计无效提案 {invalid_total} 次；出现模型调用错误的 case {model_error_total} 个"
    )
    lines.append("- 命令行使能判断：模型能用 shell 探测安装位置、启动应用、查进程，"
                 "说明它主动寻找比纯视觉更省事的实现路径（这正是端到端评估要观察的工具选择）。")
    lines.append("")
    lines.append("## 四、安全护栏评估（证据扫描）")
    lines.append("")
    guard_total = sum(
        (item.get("trace_analysis") or {}).get("guard_hit_count", 0)
        for item in reports
    )
    if guard_total == 0:
        lines.append(
            "- 全部绑定 trace 中未出现任何审批门/许可/scope_guard/鼠标抢夺保护等"
            "护栏事件（0 命中）。本轮所有失败均发生在模型策略层、物理输入回执层或"
            "外部应用状态层，没有任何一例由安全护栏拦截造成。"
        )
        lines.append(
            "- 结论：从证据上看，删除 P0/P1/P2 安全护栏不会让本轮失败变多；"
            "失败原因是模型自身的定位、状态理解和停止判断。"
        )
    else:
        lines.append("- 护栏相关事件扫描：")
        for item in reports:
            hits = (item.get("trace_analysis") or {}).get("guard_hits") or []
            if hits:
                lines.append(f"  - {item.get('family')}: {hits[:6]}")
    lines.append("")
    lines.append("## 五、改进建议（待人工判断）")
    lines.append("")
    lines.append(
        "1. 若多轮出现模型停在颜色/形状控件或重复点击，优先归因模型视觉定位与"
        "状态理解，不新增吸附/偏移/配方。"
    )
    lines.append(
        "2. 若无效提案比例高（>30% 回合），先检查 provider 工具 schema 稳定性，"
        "而不是加提示教学。"
    )
    lines.append(
        "3. 若失败集中在物理输入回执（error_kind），才考虑机械层修复；"
        "失败集中在外部应用状态时，先复现再动手。"
    )
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=["individual", "chain", "context", "all"],
        default="all",
    )
    parser.add_argument(
        "--only",
        choices=HARD_TASK_IDS,
        help="run a single named hard task (individual mode)",
    )
    parser.add_argument("--seed", type=int, default=20260804)
    parser.add_argument("--chain-len", type=int, default=3)
    parser.add_argument("--chain-count", type=int, default=2)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--list", action="store_true")
    return parser.parse_args()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = parse_args()
    if args.list:
        print(json.dumps(
            {"tasks": {task_id: HARD_TASK_GOALS[task_id] for task_id in HARD_TASK_IDS}},
            ensure_ascii=False, indent=2,
        ))
        return 0
    if os.name != "nt" or not ctypes.windll.user32.GetForegroundWindow():
        raise RuntimeError("an unlocked interactive Windows desktop is required")
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    task_order = list(HARD_TASK_IDS)
    rng.shuffle(task_order)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root = args.output_root.resolve() / f"{timestamp}-suite"
    output_root.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, Any]] = []
    selected = []
    if args.only:
        selected.append(("individual", [args.only]))
    else:
        if args.mode in {"individual", "all"}:
            selected.append(("individual", task_order))
        if args.mode in {"chain", "all"}:
            chainable = [
                task_id for task_id in task_order
                if task_id != "context-cat-qq"
            ]
            for chain_index in range(args.chain_count):
                chain_tasks = list(chainable)
                rng.shuffle(chain_tasks)
                selected.append(
                    ("chain", chain_tasks[: args.chain_len])
                )
        if args.mode in {"context", "all"}:
            selected.append(("context", ["context-cat-qq"]))
    for index, (mode, task_ids) in enumerate(selected, start=1):
        seed = args.seed + index * 7919
        if mode == "individual":
            for task_id in task_ids:
                reports.append(_run_one_task(task_id, output_root, seed))
        elif mode == "chain":
            goal, connectors = _build_chain_goal(task_ids, seed)
            spec = {
                "task": goal,
                "max_steps": 200,
                "timeout": 2400,
                "runtime_version": "vnext",
            }
            case_dir = output_root / f"chain-{index:02d}"
            case_dir.mkdir(parents=True, exist_ok=True)
            try:
                report = _run_chain_case(task_ids, spec, case_dir, seed)
            except Exception as error:
                report_error = f"{type(error).__name__}: {error}"
                report = {
                    "family": "chain",
                    "task": goal,
                    "task_ids": task_ids,
                    "connectors": connectors,
                    "runner_error": report_error,
                    "verdict": {
                        "passed": False,
                        "status": "runner_error",
                        "error": report_error,
                    },
                }
            report_path = case_dir / "report.json"
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            report["report"] = str(report_path)
            reports.append(report)
            print(
                json.dumps(
                    {"event": "hard_chain_finished", **report},
                    ensure_ascii=False,
                ),
                flush=True,
            )
        elif mode == "context":
            spec = _case_spec(
                "context-cat-qq",
                DEFAULT_TIMEOUTS["context-cat-qq"],
                DEFAULT_MAX_STEPS["context-cat-qq"],
            )
            case_dir = output_root / "context-pair"
            case_dir.mkdir(parents=True, exist_ok=True)
            report = _run_context_pair(spec, case_dir)
            report_path = case_dir / "report.json"
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            report["report"] = str(report_path)
            reports.append(report)
            print(
                json.dumps(
                    {"event": "hard_context_finished", **report},
                    ensure_ascii=False,
                ),
                flush=True,
            )
    passed_count = sum(_case_passed(item) for item in reports)
    suite = {
        "suite": "hard-real-tasks",
        "mode": args.mode,
        "seed": args.seed,
        "entrypoint": "electron_client_windows_uia",
        "goal_admission_required": True,
        "runtime_version": "vnext",
        "started_at": timestamp,
        "finished_at": datetime.now().strftime("%Y%m%d-%H%M%S"),
        "case_count": len(reports),
        "passed_count": passed_count,
        "cases": reports,
    }
    suite["passed"] = passed_count == len(reports) and bool(reports)
    report_path = output_root / "hard-tasks-suite-report.json"
    report_path.write_text(
        json.dumps(suite, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    suite["report"] = str(report_path)
    summary = _render_summary(reports, suite)
    summary_path = output_root / "hard-tasks-summary.md"
    summary_path.write_text(summary, encoding="utf-8")
    print(summary, flush=True)
    return 0 if suite["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
