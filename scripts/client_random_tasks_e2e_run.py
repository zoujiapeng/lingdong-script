#!/usr/bin/env python3
"""Run random real desktop tasks through the visible Electron client.

Every case is generated from a seeded random source: random tokens, operands,
phrasing variants, and family order, so the same suite never runs the same
wording twice.  Families cover ordinary user scenarios (text edit + save,
calculator arithmetic, chat followed by a desktop task in the same thread,
paint drawing) plus tasks that are deliberately impossible; the impossible
family must fail gracefully (explicit terminal status) instead of hanging or
fabricating success.

Success is decided by an independent external oracle (real file content,
UIA text on the real application, canvas pixels), never by the agent's own
completion text.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import winreg
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import ctypes
import win32api
import win32con
import win32gui
from PIL import Image, ImageGrab

from pywinauto import Desktop as pywinauto_desktop

# The console on Chinese Windows uses GBK; UTF-8 reports must not crash the
# runner just because a model reply contains an emoji.
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

DEFAULT_OUTPUT = WORKSPACE / "output" / "random-tasks"
TEMP_ROOT = Path(os.environ.get("TEMP") or "C:\\Windows\\Temp") / "winpilot-random-tasks"

NOTEPAD = "notepad.exe"
CALCULATOR = "CalculatorApp.exe"
PAINT = "mspaint.exe"

FAMILIES = (
    "notepad-edit-save",
    "calculator-arithmetic",
    "chat-then-task",
    "impossible-open-missing-file",
    "paint-stroke",
)

GOAL_PHRASINGS = {
    "notepad-edit-save": (
        "把记事本里的内容全部替换成 {token} 并保存，保存用 Ctrl+S",
        "打开记事本，选中全部内容输入 {token} 覆盖原来的文字，然后按 Ctrl+S 保存",
        "把记事本内容改成 {token} 然后保存",
    ),
    "calculator-arithmetic": (
        "打开计算器，计算 {a} + {b}，确认显示结果是 {total}",
        "用计算器算一下 {a} 加 {b} 等于多少，结果应该是 {total}",
        "打开计算器计算 {a} + {b}，屏幕上要显示 {total}",
    ),
    "chat-then-task": (
        "先简单回答我：{question} 然后打开记事本输入 {token} 并保存（保存按 Ctrl+S）",
        "回答一下：{question} 之后打开记事本把内容替换成 {token} 并保存",
    ),
    "impossible-open-missing-file": (
        "打开这个文件看看里面写了什么：{path}，然后告诉我内容",
        "帮我打开 {path} 查看内容，并把内容回复给我",
    ),
    "paint-stroke": (
        "打开画图，在画布中间画一条水平的直线，不用保存",
        "打开画图，在画布中央画一条直线",
    ),
}

CHAT_QUESTIONS = (
    "周杰伦最出名的一首歌是哪一首？",
    "如果要在10秒内学会一首歌，你会推荐哪首？",
    "今天适合听什么类型的音乐？",
)


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


def _find_window(
    title: str | tuple[str, ...],
    *,
    timeout: float = 25.0,
) -> int:
    titles = (title,) if isinstance(title, str) else title
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches: list[int] = []

        def visit(hwnd: int, _unused: Any) -> None:
            window_title = win32gui.GetWindowText(hwnd)
            if (
                win32gui.IsWindowVisible(hwnd)
                and any(candidate in window_title for candidate in titles)
            ):
                matches.append(int(hwnd))

        win32gui.EnumWindows(visit, None)
        if matches:
            # EnumWindows yields top-level windows in Z order.  Prefer the
            # foreground match and otherwise the first (topmost) match.  The
            # desktop Agent may legitimately open another instance; choosing
            # the bottom-most same-title window would verify an untouched
            # preparation window instead of the surface it just operated.
            foreground = int(win32gui.GetForegroundWindow() or 0)
            return foreground if foreground in matches else matches[0]
        time.sleep(0.2)
    raise RuntimeError(
        "visible target window was not found: " + " / ".join(titles)
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


def _uia_visible_texts(hwnd: int) -> list[str]:
    """Best-effort UIA text read of one real window (locate/read only)."""
    try:
        window = pywinauto_desktop(backend="uia").window(handle=hwnd)
        values: list[str] = []
        for control in window.descendants():
            try:
                name = str(control.window_text() or "")
            except Exception:
                name = ""
            if name.strip():
                values.append(name.strip())
            try:
                value = str(control.get_value() or "")
            except Exception:
                value = ""
            if value.strip() and value != name.strip():
                values.append(value.strip())
        return values
    except Exception as error:
        raise RuntimeError(f"UIA text read failed: {type(error).__name__}: {error}")


def _launch_app(command: list[str]) -> None:
    subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _notepad_file_case(seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    token = f"RD-{rng.randrange(10**7, 10**8)}-{rng.choice('ABCDEF')}"
    initial = f"INIT-{rng.randrange(10**6, 10**7)}"
    case_dir = TEMP_ROOT / f"seed-{seed}"
    case_dir.mkdir(parents=True, exist_ok=True)
    target = case_dir / f"note-{seed}.txt"
    target.write_text(initial, encoding="utf-8")
    goal = rng.choice(GOAL_PHRASINGS["notepad-edit-save"]).format(token=token)
    return {
        "family": "notepad-edit-save",
        "goal": goal,
        "token": token,
        "target": target,
        "expected": {"file_content": token},
    }


def _calculator_case(seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    a = rng.randint(11, 98)
    b = rng.randint(11, 98)
    total = a + b
    goal = rng.choice(GOAL_PHRASINGS["calculator-arithmetic"]).format(
        a=a, b=b, total=total
    )
    return {
        "family": "calculator-arithmetic",
        "goal": goal,
        "a": a,
        "b": b,
        "total": total,
        "expected": {"display": str(total)},
    }


def _chat_then_task_case(seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    token = f"CT-{rng.randrange(10**7, 10**8)}-{rng.choice('XYZ')}"
    initial = f"INIT-{rng.randrange(10**6, 10**7)}"
    case_dir = TEMP_ROOT / f"seed-{seed}"
    case_dir.mkdir(parents=True, exist_ok=True)
    target = case_dir / f"chat-{seed}.txt"
    target.write_text(initial, encoding="utf-8")
    question = rng.choice(CHAT_QUESTIONS)
    chat_message = "今天心情不错，随便聊聊。"
    goal = rng.choice(GOAL_PHRASINGS["chat-then-task"]).format(
        question=question, token=token
    )
    return {
        "family": "chat-then-task",
        "chat_message": chat_message,
        "goal": goal,
        "token": token,
        "target": target,
        "question": question,
        "expected": {"file_content": token},
    }


def _impossible_case(seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    path = TEMP_ROOT / f"missing-{seed}" / f"data-{rng.randrange(1000, 9999)}.txt"
    goal = rng.choice(GOAL_PHRASINGS["impossible-open-missing-file"]).format(
        path=path
    )
    return {
        "family": "impossible-open-missing-file",
        "goal": goal,
        "path": path,
        "expected": {"file_absent": True},
    }


def _paint_case(seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    goal = rng.choice(GOAL_PHRASINGS["paint-stroke"])
    return {
        "family": "paint-stroke",
        "goal": goal,
        "expected": {"stroke": True},
    }


def generate_case(seed: int, family: str | None = None) -> dict[str, Any]:
    selected = family or random.Random(seed).choice(FAMILIES)
    factories = {
        "notepad-edit-save": _notepad_file_case,
        "calculator-arithmetic": _calculator_case,
        "chat-then-task": _chat_then_task_case,
        "impossible-open-missing-file": _impossible_case,
        "paint-stroke": _paint_case,
    }
    case = factories[selected](seed)
    case["seed"] = seed
    return case


def prepare_case(case: dict[str, Any]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    family = case["family"]
    if family == "notepad-edit-save":
        _taskkill(NOTEPAD)
        _launch_app([NOTEPAD, str(case["target"])])
        hwnd = _find_window("记事本", timeout=20)
        evidence["notepad_hwnd"] = hwnd
        evidence["initial_content"] = case["target"].read_text(encoding="utf-8")
    elif family == "calculator-arithmetic":
        _taskkill(CALCULATOR)
        _launch_app(["calc.exe"])
        hwnd = _find_window(("计算器", "Calculator"), timeout=20)
        evidence["calculator_hwnd"] = hwnd
    elif family == "chat-then-task":
        _taskkill(NOTEPAD)
        _launch_app([NOTEPAD, str(case["target"])])
        hwnd = _find_window("记事本", timeout=20)
        evidence["notepad_hwnd"] = hwnd
        evidence["initial_content"] = case["target"].read_text(encoding="utf-8")
    elif family == "impossible-open-missing-file":
        if case["path"].exists():
            raise RuntimeError("impossible target unexpectedly exists")
        evidence["target_absent_before"] = not case["path"].exists()
    elif family == "paint-stroke":
        _taskkill(PAINT)
        _launch_app(["mspaint.exe"])
        hwnd = _find_window(("画图", "Paint"), timeout=20)
        evidence["paint_hwnd"] = hwnd
    return evidence


def _target_hwnd_for(case: dict[str, Any], evidence: dict[str, Any]) -> int:
    family = case["family"]
    if family == "calculator-arithmetic":
        return int(evidence["calculator_hwnd"])
    if family == "paint-stroke":
        return int(evidence["paint_hwnd"])
    if family in {"notepad-edit-save", "chat-then-task"}:
        return int(evidence["notepad_hwnd"])
    return 0


def _current_target_hwnd(case: dict[str, Any], fallback: int) -> int:
    """Find the live top-level window after client handoff.

    Packaged Windows apps can replace their launch-time HWND while starting.
    The remembered handle is therefore only a hint; the visible window at the
    moment of handoff is what the desktop agent should receive.
    """
    family = case["family"]
    if family == "calculator-arithmetic":
        hwnd = _find_window(("计算器", "Calculator"), timeout=15)
        case["calculator_hwnd"] = hwnd
        return hwnd
    if family == "paint-stroke":
        hwnd = _find_window(("画图", "Paint"), timeout=15)
        case["paint_hwnd"] = hwnd
        return hwnd
    if family in {"notepad-edit-save", "chat-then-task"}:
        hwnd = _find_window(("记事本", "Notepad"), timeout=15)
        case["notepad_hwnd"] = hwnd
        return hwnd
    return fallback if fallback and win32gui.IsWindow(fallback) else 0


def _read_claim_texts(run_id: str) -> list[str]:
    """Best-effort user-visible result texts from the private thread checkpoint."""
    checkpoint = (
        WORKSPACE / "python" / "winpilot_home" / "traces"
        / ".agent-thread-continuation.json"
    )
    texts: list[str] = []
    try:
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    except Exception:
        return texts

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("user_result", "result", "content"):
                if key in value and isinstance(value[key], str) and value[key].strip():
                    texts.append(value[key].strip())
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(payload)
    return texts[-12:]


def _run_claim_evidence(result: dict[str, Any]) -> dict[str, Any]:
    run_id = str((result.get("result") or {}).get("run_id") or "")
    texts = _read_claim_texts(run_id)
    return {
        "run_id": run_id,
        "claim_texts": texts[-3:],
        "claim_text_joined": "\n".join(texts[-3:]),
    }


def _external_verifier(
    case: dict[str, Any],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    family = case["family"]

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        run_result = result.get("result") or {}
        status_label = str(run_result.get("status_label") or "")
        outcome = str(run_result.get("outcome") or "")
        errors: list[str] = []
        evidence: dict[str, Any] = {}
        if family == "notepad-edit-save":
            target = Path(case["target"])
            exists = target.is_file()
            content = target.read_text(encoding="utf-8") if exists else ""
            evidence["file_exists"] = exists
            evidence["file_content"] = content
            evidence["expected"] = case["expected"]["file_content"]
            if not exists or content != case["expected"]["file_content"]:
                errors.append("file content does not equal the requested token")
            if status_label not in {"done", "completed", "success", "reply"}:
                errors.append(f"run did not end with success status: {status_label}")
        elif family == "calculator-arithmetic":
            expected = case["expected"]["display"]
            hwnd = _current_target_hwnd(
                case,
                int(case.get("calculator_hwnd") or 0),
            )
            texts = _uia_visible_texts(hwnd)
            evidence["uia_texts"] = texts[-40:]
            evidence["expected"] = expected
            numeric_token = re.compile(
                rf"(?<!\d){re.escape(expected)}(?!\d)"
            )
            display_matches = [
                text for text in texts if numeric_token.search(text)
            ]
            evidence["display_matches"] = display_matches
            if not display_matches:
                errors.append(f"calculator display does not contain {expected}")
            if status_label not in {"done", "completed", "success", "reply"}:
                errors.append(f"run did not end with success status: {status_label}")
        elif family == "chat-then-task":
            target = Path(case["target"])
            exists = target.is_file()
            content = target.read_text(encoding="utf-8") if exists else ""
            evidence["file_exists"] = exists
            evidence["file_content"] = content
            evidence["expected"] = case["expected"]["file_content"]
            if not exists or content != case["expected"]["file_content"]:
                errors.append("file content does not equal the requested token")
            if status_label not in {"done", "completed", "success", "reply"}:
                errors.append(f"run did not end with success status: {status_label}")
            claim = _run_claim_evidence(result)
            evidence["claim"] = claim
            if not claim["claim_text_joined"].strip():
                errors.append("no user-visible result text answered the chat question")
        elif family == "impossible-open-missing-file":
            target = Path(case["path"])
            absent = not target.exists()
            evidence["target_absent_after"] = absent
            evidence["status_label"] = status_label
            evidence["outcome"] = outcome
            claim = _run_claim_evidence(result)
            evidence["claim"] = claim
            if outcome != "terminal":
                errors.append("impossible task did not reach an explicit terminal state")
            if status_label in {"done", "completed", "success", "reply"}:
                claim_text = claim["claim_text_joined"]
                if "不存在" in claim_text or "打不开" in claim_text or "失败" in claim_text:
                    evidence["graceful_claim"] = True
                else:
                    errors.append(
                        "impossible task ended with success status without an honest "
                        "missing-file explanation"
                    )
            if not absent:
                errors.append("impossible target file was unexpectedly created")
        elif family == "paint-stroke":
            hwnd = _current_target_hwnd(
                case,
                int(case.get("paint_hwnd") or 0),
            )
            case["paint_hwnd"] = hwnd
            shot_path = case["paint_screenshot"]
            if shot_path:
                try:
                    # client_e2e_run restores Electron before calling the
                    # independent verifier.  Bring the real Paint window back
                    # before sampling its pixels; otherwise the old window
                    # rectangle can contain Electron and fabricate a stroke.
                    evidence["terminal_focus"] = _focus_window(hwnd)
                    _capture_window(hwnd, Path(shot_path))
                except Exception:
                    shot_path = None
            evidence.update(_paint_stroke_evidence(hwnd, shot_path))
            if not evidence["stroke_detected"]:
                errors.append("no clear horizontal stroke was detected on the canvas")
            if status_label not in {"done", "completed", "success", "reply"}:
                errors.append(f"run did not end with success status: {status_label}")
        else:  # pragma: no cover
            errors.append(f"unsupported family: {family}")
        return {
            "source": f"external_real_app_{family}",
            "passed": not errors,
            "errors": errors,
            "evidence": evidence,
        }

    return verify


def _paint_stroke_evidence(hwnd: int, screenshot: str | None) -> dict[str, Any]:
    if not hwnd or not win32gui.IsWindow(hwnd):
        return {"stroke_detected": False, "error": "paint window is gone"}
    path = Path(screenshot) if screenshot else None
    if path is None or not path.is_file():
        return {"stroke_detected": False, "error": "paint screenshot is unavailable"}
    try:
        image = Image.open(str(path)).convert("L")
        width, height = image.size
        if width < 200 or height < 200:
            return {"stroke_detected": False, "error": "paint capture is too small"}
        # Paint's usable canvas occupies the broad middle/lower area.  This is
        # intentionally loose supporting evidence, not a second completion
        # judge: find the longest contiguous dark run on any row where the
        # requested centered horizontal stroke should be visible.
        left, right = int(width * 0.12), int(width * 0.96)
        top, bottom = int(height * 0.42), int(height * 0.82)
        pixels = image.load()
        best = {"length": 0, "x": left, "y": top}
        for y in range(top, bottom):
            start: int | None = None
            for x in range(left, right + 1):
                is_dark = x < right and pixels[x, y] < 225
                if is_dark and start is None:
                    start = x
                elif not is_dark and start is not None:
                    length = x - start
                    if length > best["length"]:
                        best = {"length": length, "x": start, "y": y}
                    start = None
        inspected_width = max(1, right - left)
        spread = best["length"] / inspected_width
        return {
            "stroke_detected": spread >= 0.25,
            "strongest_horizontal_run": best,
            "spread_ratio": round(spread, 3),
            "inspected_band": [left, top, right, bottom],
            "screenshot": str(path),
        }
    except Exception as error:
        return {
            "stroke_detected": False,
            "error": f"{type(error).__name__}: {error}",
        }


def run_random_case(
    name: str,
    case: dict[str, Any],
    *,
    output_root: Path,
    reset_session: bool,
    chat_first: str = "",
) -> dict[str, Any]:
    family = case["family"]
    spec = {
        "task": case["goal"],
        "max_steps": 60,
        "timeout": 900,
        "runtime_version": "vnext",
    }
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    case_dir = output_root / f"{timestamp}-{name}"
    case_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "name": name,
        "family": family,
        "seed": case["seed"],
        "task": case["goal"],
        "generated": {
            key: (str(value) if isinstance(value, Path) else value)
            for key, value in case.items()
            if key not in {"goal"}
        },
        "started_at": datetime.now().astimezone().isoformat(),
    }
    evidence: dict[str, Any] = {}
    try:
        evidence = prepare_case(case)
        report["preparation"] = evidence
        target_hwnd = _target_hwnd_for(case, evidence)
        if evidence.get("calculator_hwnd"):
            case["calculator_hwnd"] = int(evidence["calculator_hwnd"])
        if evidence.get("paint_hwnd"):
            case["paint_hwnd"] = int(evidence["paint_hwnd"])
            case["paint_screenshot"] = str(case_dir / "paint-terminal.png")

        def after_submit(
            driver: client_harness.ElectronClientDriver,
            _client_result: dict[str, Any],
        ) -> dict[str, Any]:
            live_hwnd = _current_target_hwnd(case, target_hwnd)
            if not live_hwnd:
                return {"focused": False}
            focused = _focus_window(live_hwnd)
            focused["screenshot"] = _capture_window(
                live_hwnd,
                driver.output_dir / "target-after-submit.png",
            )
            return focused

        verifier = _external_verifier(case)
        chat_result: dict[str, Any] | None = None
        if chat_first:
            chat_result = client_harness.run_case(
                f"{name}-chat",
                {
                    "task": chat_first,
                    "max_steps": 10,
                    "timeout": 300,
                    "runtime_version": "vnext",
                },
                output_root=case_dir,
                reset_session=True,
            )
            report["chat_first"] = {
                "task": chat_first,
                "result": chat_result.get("result"),
                "trace_ids": chat_result.get("bound_trace_ids"),
            }
        result = client_harness.run_case(
            name,
            spec,
            output_root=case_dir,
            reset_session=reset_session and not chat_first,
            after_submit=after_submit,
            independent_verifier=verifier,
        )
        report["client_result"] = {
            "result": result.get("result"),
            "trace_ids": result.get("bound_trace_ids"),
            "runner_error": result.get("runner_error"),
            "execution_valid": result.get("execution_valid"),
            "passed": result.get("passed"),
        }
        report["task_verdict"] = result.get("task_verdict")
    except Exception as error:
        report["runner_error"] = f"{type(error).__name__}: {error}"
    finally:
        if family == "impossible-open-missing-file":
            shutil.rmtree(Path(case["path"]).parent, ignore_errors=True)
        elif family in {"notepad-edit-save", "chat-then-task"}:
            _taskkill(NOTEPAD)
        elif family == "calculator-arithmetic":
            _taskkill(CALCULATOR)
        elif family == "paint-stroke":
            _taskkill(PAINT)
        report["finished_at"] = datetime.now().astimezone().isoformat()
        report_path = case_dir / "report.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        report["report"] = str(report_path)
        print(json.dumps({"event": "random_case_finished", **report}, ensure_ascii=False), flush=True)
    return report


def _verdict_passed(report: dict[str, Any]) -> bool:
    if report.get("runner_error"):
        return False
    verdict = report.get("task_verdict") or {}
    if verdict.get("passed") is not True:
        return False
    client = report.get("client_result") or {}
    if client.get("runner_error") or client.get("execution_valid") is not True:
        return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--seed", type=int, default=20260803)
    parser.add_argument("--family", choices=FAMILIES)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--list", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list:
        print(json.dumps({"families": FAMILIES}, ensure_ascii=False, indent=2))
        return 0
    if os.name != "nt" or not ctypes.windll.user32.GetForegroundWindow():
        raise RuntimeError("an unlocked interactive Windows desktop is required")
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    seeds = [args.seed + index * 7919 for index in range(args.count)]
    if args.family:
        cases = [
            generate_case(seed, family=args.family)
            for seed in seeds
        ]
    else:
        families = [rng.choice(FAMILIES) for _ in range(args.count)]
        cases = [
            generate_case(seed, family=family)
            for seed, family in zip(seeds, families, strict=True)
        ]
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root = args.output_root.resolve() / f"{timestamp}-suite"
    output_root.mkdir(parents=True, exist_ok=True)
    reports: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        name = f"random-{index:02d}-{case['family']}"
        chat_first = (
            case.get("chat_message", "")
            if case["family"] == "chat-then-task"
            else ""
        )
        report = run_random_case(
            name,
            case,
            output_root=output_root,
            reset_session=True,
            chat_first=chat_first,
        )
        report["suite_pass"] = _verdict_passed(report)
        reports.append(report)
    passed_count = sum(item.get("suite_pass") is True for item in reports)
    suite = {
        "suite": "random-real-tasks",
        "seed": args.seed,
        "entrypoint": "electron_client_windows_uia",
        "goal_admission_required": True,
        "runtime_version": "vnext",
        "started_at": datetime.now().astimezone().isoformat(),
        "finished_at": datetime.now().astimezone().isoformat(),
        "case_count": len(reports),
        "passed_count": passed_count,
        "cases": reports,
    }
    suite["passed"] = passed_count == len(reports) and bool(reports)
    report_path = output_root / "suite-report.json"
    report_path.write_text(
        json.dumps(suite, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    suite["report"] = str(report_path)
    print(json.dumps({"event": "random_suite_finished", **suite}, ensure_ascii=False), flush=True)
    return 0 if suite["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
