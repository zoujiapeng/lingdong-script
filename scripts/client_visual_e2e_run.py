#!/usr/bin/env python3
"""Run generic visual gestures through the visible Electron client.

The task is entered through the real client with cold start enabled.  After the
Run button is clicked, the controlled browser target is returned to the
foreground so the desktop agent receives the same visible scene a user would.
Success is decided by trusted browser events and scenario-owned state, never
by the agent's own completion text or by a direct backend invocation.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
import win32api
import win32con
import win32gui
from PIL import ImageGrab


WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import client_e2e_run as client_harness  # noqa: E402


TARGET_SERVER = (
    WORKSPACE
    / "python"
    / "winpilot_home"
    / "eval_targets"
    / "visual_interaction_server.py"
)
DEFAULT_OUTPUT = WORKSPACE / "output" / "playwright" / "client-visual-e2e"

# The benchmark prompt deliberately carries no interaction recipe.  Rules live
# only in the visible randomized page; a passing run must understand that frame
# and its later state changes just as a user would.
VISUAL_TEST_TASK = "完成当前界面上的测试。"
SPOKEN_GUI_VISUAL_TEST_TASK = "必须使用GUI和真实鼠标完成当前界面上的测试。"
ANSWER_ALL_QUESTIONS_TASK = (
    "必须使用GUI和真实鼠标键盘，帮我在当前开启的页面里答完所有题。"
)
# The calibration grid measures physical pointer placement: the task states
# the visible page's own instruction and requires the real mouse for the
# measured action.  The measured property is the accuracy of the physical
# click itself (the page records every trusted pointer location and verifies
# it after the run), so the goal names the visible targets and the physical
# input.  It does not forbid helper tools; the requirement is only that the
# clicks that hit the squares are real mouse input.
CALIBRATION_GRID_TASK = (
    "必须使用GUI和真实鼠标完成当前界面上的测试。这个测试测量的是你用鼠标"
    "点击紫色方块中心的精度，每次真实点击的位置都会被记录并在任务结束后"
    "验证，所以请直接用鼠标点击当前页面上每个紫色方块的中心，直到全部"
    "方块都变成绿色。"
)
CALIBRATION_FEEDBACK_TASK = (
    "必须使用GUI和真实鼠标完成当前界面上的测试。这个测试测量的是你用鼠标"
    "点击紫色方块中心的精度，每次真实点击的位置都会被记录并在任务结束后"
    "验证；页面状态行会提示上次点击的偏差，请根据提示直接用鼠标修正点击，"
    "直到全部方块都变成绿色。"
)

VISUAL_CASES: dict[str, dict[str, Any]] = {
    "answer-all-questions": {
        "scenario": "quiz_all",
        "task": ANSWER_ALL_QUESTIONS_TASK,
        "seed": 8675309,
        "max_steps": 40,
        "timeout": 600,
        "runtime_version": "vnext",
        "ground_truth": "trusted_browser_form_submit_and_server_evaluation",
    },
    "precise-click": {
        "scenario": "precise_click",
        "task": SPOKEN_GUI_VISUAL_TEST_TASK,
        "seed": 424242,
        "max_steps": 12,
        "timeout": 360,
        "runtime_version": "vnext",
    },
    "ordered-clicks": {
        "scenario": "ordered_clicks",
        "task": SPOKEN_GUI_VISUAL_TEST_TASK,
        "seed": 515151,
        "max_steps": 18,
        "timeout": 420,
        "runtime_version": "vnext",
    },
    "calibration-grid": {
        "scenario": "calibration_grid",
        "task": CALIBRATION_GRID_TASK,
        "seed": 20260806,
        "max_steps": 24,
        "timeout": 420,
        "runtime_version": "vnext",
        "ground_truth": "trusted_browser_pointer_events",
    },
    "calibration-feedback": {
        "scenario": "calibration_feedback",
        "task": CALIBRATION_FEEDBACK_TASK,
        "seed": 20260807,
        "max_steps": 24,
        "timeout": 420,
        "runtime_version": "vnext",
        "ground_truth": "trusted_browser_pointer_events",
    },
    "drag-fit": {
        "scenario": "drag_fit",
        "task": VISUAL_TEST_TASK,
        "seed": 271828,
        "max_steps": 30,
        "timeout": 360,
    },
    "slider": {
        "scenario": "slider",
        "task": VISUAL_TEST_TASK,
        "seed": 314159,
        "max_steps": 30,
        "timeout": 360,
    },
    "dynamic-hold": {
        "scenario": "dynamic_hold",
        # Keep the selector on auto: GUI is a user-spoken requirement, not a
        # hidden harness execution constraint.
        "task": SPOKEN_GUI_VISUAL_TEST_TASK,
        "seed": 161803,
        "max_steps": 36,
        "timeout": 420,
    },
    # These deterministic seeds exercise every mirrored/reversed corridor.
    "corridor-v0": {
        "scenario": "corridor_drag",
        "task": VISUAL_TEST_TASK,
        "seed": 7,
        "expected_variant": 0,
        "max_steps": 36,
        "timeout": 420,
    },
    "corridor-v1": {
        "scenario": "corridor_drag",
        "task": VISUAL_TEST_TASK,
        "seed": 12,
        "expected_variant": 1,
        "max_steps": 36,
        "timeout": 420,
    },
    "game-drag-v2": {
        "scenario": "corridor_drag",
        "task": VISUAL_TEST_TASK,
        "seed": 1,
        "expected_variant": 2,
        "max_steps": 36,
        "timeout": 420,
    },
    "corridor-v3": {
        "scenario": "corridor_drag",
        "task": VISUAL_TEST_TASK,
        "seed": 4,
        "expected_variant": 3,
        "max_steps": 36,
        "timeout": 420,
    },
}

SUITES = {
    "smoke": ("drag-fit", "slider", "dynamic-hold", "game-drag-v2"),
    "goal-admission": ("precise-click", "ordered-clicks"),
    "exam": ("answer-all-questions",),
    "all": tuple(VISUAL_CASES),
}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _json_get(url: str, *, timeout: float = 3.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError(f"expected object from {url}")
    return value


def _wait_json(url: str, predicate, *, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            last = _json_get(url)
            if predicate(last):
                return last
        except Exception:
            pass
        time.sleep(0.15)
    raise RuntimeError(f"timed out waiting for {url}; last={last}")


def _browser_executable() -> Path:
    explicit = os.environ.get("WINPILOT_REAL_VISUAL_BROWSER", "").strip()
    candidates = (
        explicit,
        shutil.which("msedge") or "",
        shutil.which("chrome") or "",
        str(Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe"),
        str(Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe"),
        str(Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"),
    )
    for candidate in candidates:
        path = Path(candidate) if candidate else None
        if path is not None and path.is_file():
            return path
    raise RuntimeError("Edge or Chrome was not found")


def _find_window(title: str, *, timeout: float = 25.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches: list[int] = []

        def visit(hwnd: int, _unused: Any) -> None:
            if win32gui.IsWindowVisible(hwnd) and title in win32gui.GetWindowText(hwnd):
                matches.append(int(hwnd))

        win32gui.EnumWindows(visit, None)
        if matches:
            return matches[-1]
        time.sleep(0.2)
    raise RuntimeError(f"visible target window was not found: {title}")


def _focus_window(hwnd: int) -> dict[str, Any]:
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise RuntimeError("target window no longer exists")
    # Preserve the exact geometry already captured by the native foreground
    # handoff. SW_RESTORE also un-maximizes a visible window, which invalidates
    # the server-owned target binding before the first Runtime frame.
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.BringWindowToTop(hwnd)
    focused = False
    errors: list[str] = []
    for _attempt in range(3):
        try:
            # The temporary ALT press is the generic Windows foreground-unlock
            # path; it does not send input into the evaluation surface.
            win32api.keybd_event(
                win32con.VK_MENU,
                0,
                0,
                client_harness.HARNESS_FOCUS_INPUT_TAG,
            )
            win32gui.SetForegroundWindow(hwnd)
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")
        finally:
            win32api.keybd_event(
                win32con.VK_MENU,
                0,
                win32con.KEYEVENTF_KEYUP,
                client_harness.HARNESS_FOCUS_INPUT_TAG,
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
    ).save(path)
    return str(path)


def _terminate_tree(process: subprocess.Popen[Any] | None) -> None:
    if process is None:
        return
    try:
        root = psutil.Process(process.pid)
    except psutil.Error:
        return
    descendants = root.children(recursive=True)
    for child in reversed(descendants):
        try:
            child.terminate()
        except psutil.Error:
            pass
    try:
        root.terminate()
    except psutil.Error:
        pass
    _gone, alive = psutil.wait_procs([*descendants, root], timeout=4)
    for item in alive:
        try:
            item.kill()
        except psutil.Error:
            pass


def _calibration_summary(state: dict[str, Any]) -> dict[str, Any]:
    """Aggregate calibration_hit offsets (dx/dy per point) for the report.

    The calibration grid records the intended point center and the actual
    click position for every hit, so a systematic visual-location bias shows
    up as a non-zero mean offset, while a coordinate-mapping bug shows up as
    a consistent offset pattern across the grid.
    """
    events = state.get("events") if isinstance(state, dict) else None
    if not isinstance(events, list):
        return {}
    hits = [
        (int(event.get("details", {}).get("dx") or 0),
         int(event.get("details", {}).get("dy") or 0),
         int(event.get("details", {}).get("index") or 0))
        for event in events
        if event.get("type") == "calibration_hit"
    ]
    misses = [
        (int(event.get("details", {}).get("dx") or 0),
         int(event.get("details", {}).get("dy") or 0))
        for event in events
        if event.get("type") == "calibration_miss"
    ]
    if not hits and not misses:
        return {}
    summary: dict[str, Any] = {"hit_count": len(hits), "miss_count": len(misses)}
    if hits:
        dx_values = [dx for dx, _dy, _index in hits]
        dy_values = [dy for _dx, dy, _index in hits]
        summary.update({
            "mean_dx": round(sum(dx_values) / len(dx_values), 2),
            "mean_dy": round(sum(dy_values) / len(dy_values), 2),
            "max_abs_dx": max(abs(value) for value in dx_values),
            "max_abs_dy": max(abs(value) for value in dy_values),
            "points": [
                {"index": index, "dx": dx, "dy": dy}
                for dx, dy, index in sorted(hits, key=lambda item: item[2])
            ],
        })
    if misses:
        # Correction ability: does the distance to the intended point shrink
        # after each visible miss report?  A shrinking |dx|+|dy| sequence
        # means the model reads the on-screen offset feedback and adjusts.
        miss_distances = [abs(dx) + abs(dy) for dx, dy in misses]
        summary["miss_distances"] = miss_distances
        summary["converging"] = (
            len(miss_distances) >= 2
            and miss_distances[-1] < miss_distances[0]
            and min(miss_distances) == miss_distances[-1]
        )
        summary["last_miss_distance"] = miss_distances[-1] if miss_distances else None
    return summary


def external_verdict(
    state: dict[str, Any],
    *,
    case_id: str,
    scenario: str,
    seed: int,
    expected_variant: int | None = None,
) -> dict[str, Any]:
    """Validate ground truth produced by real browser pointer events."""

    errors: list[str] = []
    event = state.get("success_event") or {}
    details = event.get("details") or {}
    if state.get("success") is not True:
        errors.append("no accepted trusted success event")
    if state.get("case_id") != case_id:
        errors.append("case id mismatch")
    if state.get("scenario") != scenario:
        errors.append("scenario mismatch")
    if int(state.get("seed") or 0) != int(seed):
        errors.append("seed mismatch")
    if event.get("trusted") is not True or event.get("succeeded") is not True:
        errors.append("success event is not trusted and successful")
    if details.get("succeeded") is not True:
        errors.append("scenario details are not successful")

    move_count = int(event.get("move_count") or 0)
    if scenario == "drag_fit":
        token = details.get("token") or {}
        target = details.get("target") or {}
        if move_count < 2:
            errors.append("drag did not produce enough pointer moves")
        if not all(
            key in token and key in target
            for key in ("left", "top", "right", "bottom")
        ):
            errors.append("missing drag placement geometry")
        elif not (
            token["left"] >= target["left"]
            and token["top"] >= target["top"]
            and token["right"] <= target["right"]
            and token["bottom"] <= target["bottom"]
        ):
            errors.append("object is not completely inside target")
    elif scenario == "slider":
        goal = details.get("goal") or {}
        center = int(details.get("center_x") or -1)
        if move_count < 2:
            errors.append("slider did not produce enough pointer moves")
        if not goal or not int(goal.get("left") or 0) <= center <= int(goal.get("right") or -1):
            errors.append("slider center is outside goal band")
    elif scenario == "corridor_drag":
        if move_count < 3:
            errors.append("corridor gesture did not produce enough pointer moves")
        if details.get("started_at_origin") is not True:
            errors.append("corridor gesture did not start from the original token")
        if details.get("path_valid") is not True:
            errors.append("gesture left the visible corridor")
        clearance_value = details.get("min_clearance")
        if clearance_value is None or int(clearance_value) < 0:
            errors.append("dragged object footprint has negative corridor clearance")
        if len(details.get("path_points") or []) < 3:
            errors.append("corridor path evidence is incomplete")
        if expected_variant is not None:
            ready = next(
                (
                    item for item in reversed(state.get("events") or [])
                    if item.get("type") == "page_ready"
                ),
                {},
            )
            raw_variant = (ready.get("details") or {}).get("variant")
            variant = int(raw_variant) if raw_variant is not None else -1
            if variant != int(expected_variant):
                errors.append(
                    f"expected corridor variant {expected_variant}, observed {variant}",
                )
    elif scenario == "dynamic_hold":
        all_events = state.get("events") or []
        down_events = [
            item for item in all_events
            if item.get("type") == "pointerdown" and item.get("trusted") is True
        ]
        checkpoint_events = [
            item for item in all_events
            if item.get("type") == "checkpoint_reached" and item.get("trusted") is True
        ]
        release_events = [
            item for item in all_events
            if item.get("type") == "pointerup" and item.get("trusted") is True
        ]
        if len(down_events) != 1 or int(details.get("down_count") or 0) != 1:
            errors.append("dynamic gesture was not completed with exactly one held press")
        if not checkpoint_events or details.get("checkpoint_reached") is not True:
            errors.append("checkpoint was not reached while the pointer was held")
        if details.get("goal_revealed_after_down") is not True:
            errors.append("goal was not revealed after the trusted pointerdown")
        if details.get("inside_goal") is not True:
            errors.append("pointer was not released inside the newly revealed goal")
        if details.get("completion_event") != "pointerup":
            errors.append("dynamic success was not emitted by pointerup")
        if len(release_events) != 1:
            errors.append("dynamic gesture has no unique trusted pointerup evidence")
        else:
            release_details = release_events[0].get("details") or {}
            if (
                release_events[0].get("succeeded") is not True
                or release_details.get("succeeded") is not True
                or int(release_details.get("down_count") or 0) != 1
                or release_details.get("checkpoint_reached") is not True
                or release_details.get("goal_revealed_after_down") is not True
                or release_details.get("inside_goal") is not True
                or release_details.get("completion_event") != "pointerup"
            ):
                errors.append("trusted pointerup did not complete inside the final goal")
        if move_count < 2:
            errors.append("dynamic held gesture did not span multiple pointer moves")
    elif scenario == "precise_click":
        target_index = details.get("target_index")
        target = details.get("target") or {}
        trusted_failures = [
            item for item in state.get("events") or []
            if (
                item.get("type") == "scenario_failure"
                and item.get("trusted") is True
            )
        ]
        if (
            not isinstance(target_index, int)
            or isinstance(target_index, bool)
            or not 0 <= target_index < 12
        ):
            errors.append("precise target index is invalid")
        if not all(
            key in target for key in ("left", "top", "right", "bottom")
        ):
            errors.append("precise target geometry is incomplete")
        if trusted_failures:
            errors.append("a trusted wrong-object click preceded success")
    elif scenario == "ordered_clicks":
        trusted_sequence = [
            item for item in state.get("events") or []
            if (
                item.get("type") == "sequence_click"
                and item.get("trusted") is True
            )
        ]
        trusted_sequence.sort(
            key=lambda item: len((item.get("details") or {}).get("clicked") or [])
        )
        clicked_progress = [
            (item.get("details") or {}).get("clicked")
            for item in trusted_sequence
        ]
        expected_progress = [
            (item.get("details") or {}).get("expected_next")
            for item in trusted_sequence
        ]
        targets = details.get("targets") or []
        trusted_failures = [
            item for item in state.get("events") or []
            if (
                item.get("type") == "scenario_failure"
                and item.get("trusted") is True
            )
        ]
        if details.get("click_sequence") != [1, 2, 3]:
            errors.append("completed click sequence is not 1,2,3")
        if details.get("required_sequence") != [1, 2, 3]:
            errors.append("required click sequence is not 1,2,3")
        if clicked_progress != [[1], [1, 2], [1, 2, 3]]:
            errors.append("trusted sequence-click progression is incomplete")
        if expected_progress != [2, 3, None]:
            errors.append("trusted expected-next progression is invalid")
        if (
            len(targets) != 3
            or any(
                not isinstance(target, dict)
                or not all(
                    key in target
                    for key in ("left", "top", "right", "bottom")
                )
                for target in targets
            )
        ):
            errors.append("ordered target geometry is incomplete")
        if trusted_failures:
            errors.append("a trusted wrong-sequence click preceded success")
    elif scenario == "quiz_all":
        ready = next(
            (
                item for item in reversed(state.get("events") or [])
                if item.get("type") == "page_ready"
            ),
            {},
        )
        ready_details = ready.get("details") or {}
        question_count = int(details.get("question_count") or 0)
        question_ids = details.get("question_ids") or []
        question_types = details.get("question_types") or []
        if event.get("type") != "quiz_submit":
            errors.append("quiz success did not come from a form submission")
        if details.get("server_verified") is not True:
            errors.append("quiz submission was not independently evaluated")
        if details.get("all_correct") is not True:
            errors.append("quiz submission was not completely correct")
        if (
            question_count < 6
            or int(details.get("answer_count") or 0) != question_count
            or int(details.get("correct_count") or 0) != question_count
            or len(question_ids) != question_count
        ):
            errors.append("quiz completion counts are incomplete")
        if (
            len(question_types) != question_count
            or set(question_types) != {"single_choice", "text"}
        ):
            errors.append("quiz did not cover both choice and text inputs")
        if ready_details.get("requires_page_scroll") is not True:
            errors.append("quiz page did not require visible scrolling")
        if ready_details.get("question_ids") != question_ids:
            errors.append("submitted quiz does not match the visible question set")
    elif scenario in {"calibration_grid", "calibration_feedback"}:
        # Every calibration point must be hit with one trusted pointerdown;
        # the page reports one calibration_hit per accepted square center.
        ready = next(
            (
                item for item in reversed(state.get("events") or [])
                if item.get("type") == "page_ready"
            ),
            {},
        )
        point_count = int((ready.get("details") or {}).get("point_count") or 0)
        if point_count < 1:
            errors.append("calibration page did not declare its points")
        hits = [
            item for item in state.get("events") or []
            if item.get("type") == "calibration_hit"
            and item.get("trusted") is True
            and item.get("scenario") == scenario
        ]
        hit_indices = {
            int((item.get("details") or {}).get("index", -1))
            for item in hits
        }
        if event.get("type") != "scenario_success":
            errors.append("calibration success did not come from the grid page")
        if len(hits) != point_count or hit_indices != set(range(point_count)):
            errors.append(
                "calibration did not hit every declared point "
                f"({len(hits)}/{point_count})"
            )
        if details.get("succeeded") is not True:
            errors.append("calibration success details are not successful")
        if scenario == "calibration_feedback":
            tolerance = int((ready.get("details") or {}).get("tolerance_px") or 0)
            if tolerance < 1:
                errors.append("calibration feedback tolerance is invalid")
            miss_events = [
                item for item in state.get("events") or []
                if item.get("type") == "calibration_miss"
                and item.get("trusted") is True
                and item.get("scenario") == scenario
            ]
            if miss_events:
                errors.append("calibration feedback contained a trusted miss")
    else:
        errors.append(f"unsupported scenario: {scenario}")

    return {
        "passed": not errors,
        "errors": errors,
        "trusted": event.get("trusted") is True,
        "pointer_type": event.get("pointer_type"),
        "move_count": move_count,
        "details": details,
    }


def _client_case_spec(source: dict[str, Any]) -> dict[str, Any]:
    """Build only the values a user can submit through the main Agent entry."""

    return {
        "task": str(source["task"]),
        "max_steps": int(source["max_steps"]),
        "timeout": int(source["timeout"]),
        "runtime_version": str(source.get("runtime_version") or "vnext"),
    }


def run_visual_case(
    name: str,
    *,
    output_root: Path,
    seed: int | None = None,
    max_steps: int | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    if os.name != "nt" or not ctypes.windll.user32.GetForegroundWindow():
        raise RuntimeError("an unlocked interactive Windows desktop is required")
    source = dict(VISUAL_CASES[name])
    if seed is not None:
        source["seed"] = int(seed)
    if max_steps is not None:
        source["max_steps"] = int(max_steps)
    if timeout is not None:
        source["timeout"] = int(timeout)

    case_id = f"client-{name}-{uuid.uuid4().hex[:10]}"
    scenario = str(source["scenario"])
    seed = int(source["seed"])
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    state_url = f"{base}/state?case={case_id}"
    url = (
        f"{base}/visual_interaction_target.html?case={case_id}"
        f"&scenario={scenario}&seed={seed}"
    )
    case_dir = output_root / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(tempfile.mkdtemp(prefix=f"winpilot-client-visual-{case_id}-"))
    server_process: subprocess.Popen[Any] | None = None
    browser_process: subprocess.Popen[Any] | None = None
    target_hwnd = 0
    started_at = datetime.now().astimezone().isoformat()
    result: dict[str, Any] = {
        "name": name,
        "case_id": case_id,
        "scenario": scenario,
        "seed": seed,
        "task": source["task"],
        "entrypoint": "electron_client_windows_uia",
        "runtime_version": str(source.get("runtime_version") or "vnext"),
        "goal_admission_required": True,
        "ground_truth": str(
            source.get("ground_truth") or "trusted_browser_pointer_events"
        ),
        "started_at": started_at,
    }
    try:
        server_process = subprocess.Popen(
            [sys.executable, "-u", str(TARGET_SERVER), str(port)],
            cwd=str(TARGET_SERVER.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait_json(f"{base}/state", lambda value: value.get("ready") is True, timeout=12)
        browser_process = subprocess.Popen(
            [
                str(_browser_executable()),
                f"--app={url}",
                "--new-window",
                "--window-size=1100,760",
                "--window-position=80,60",
                "--force-device-scale-factor=1",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-background-mode",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-features=CalculateNativeWinOcclusion",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        title = f"WinPilot Visual Interaction Target {case_id}"
        target_hwnd = _find_window(title)
        ready_state = _wait_json(
            state_url,
            lambda value: any(
                item.get("type") == "page_ready"
                and item.get("scenario") == scenario
                for item in value.get("events") or []
            ),
            timeout=12,
        )
        result["target_ready"] = ready_state
        result["target_before"] = _capture_window(
            target_hwnd, case_dir / "target-before-submit.png",
        )

        def handoff(
            driver: client_harness.ElectronClientDriver,
            _client_result: dict[str, Any],
        ) -> dict[str, Any]:
            evidence = _focus_window(target_hwnd)
            evidence["screenshot"] = _capture_window(
                target_hwnd,
                driver.output_dir / "target-after-submit.png",
            )
            return evidence

        oracle_result: dict[str, Any] = {}

        def verify_external_state(_client_result: dict[str, Any]) -> dict[str, Any]:
            def complete(value: dict[str, Any]) -> bool:
                events = value.get("events") or []
                if scenario == "quiz_all":
                    return value.get("success") is True or any(
                        item.get("type") == "quiz_submit"
                        and item.get("trusted") is True
                        for item in events
                    )
                if scenario in {"calibration_grid", "calibration_feedback"}:
                    if any(
                        item.get("type") == "scenario_failure"
                        and item.get("trusted") is True
                        for item in events
                    ):
                        return True
                    ready = next(
                        (
                            item for item in reversed(events)
                            if item.get("type") == "page_ready"
                        ),
                        {},
                    )
                    point_count = int(
                        (ready.get("details") or {}).get("point_count") or 0
                    )
                    hit_count = len([
                        item for item in events
                        if (
                            item.get("type") == "calibration_hit"
                            and item.get("trusted") is True
                        )
                    ])
                    return point_count > 0 and hit_count >= point_count
                if any(
                    item.get("type") == "scenario_failure"
                    and item.get("trusted") is True
                    for item in events
                ):
                    return True
                if value.get("success") is not True:
                    return False
                if scenario != "ordered_clicks":
                    return True
                return len([
                    item for item in events
                    if (
                        item.get("type") == "sequence_click"
                        and item.get("trusted") is True
                    )
                ]) == 3

            state = _wait_json(
                state_url,
                complete,
                timeout=8,
            )
            # The final sequence_click and scenario_success are separate fetches.
            # Re-read once after they have both had time to reach the oracle.
            time.sleep(0.2)
            state = _json_get(state_url)
            verdict = external_verdict(
                state,
                case_id=case_id,
                scenario=scenario,
                seed=seed,
                expected_variant=source.get("expected_variant"),
            )
            verdict = {**verdict, "source": "external_browser_state"}
            oracle_result.update({"state": state, "verdict": verdict})
            return verdict

        client_result = client_harness.run_case(
            name,
            _client_case_spec(source),
            output_root=case_dir / "client",
            reset_session=True,
            after_submit=handoff,
            independent_verifier=verify_external_state,
        )
        result["client_result"] = client_result
        state = dict(oracle_result.get("state") or {})
        result["external_state"] = state
        verdict = dict(oracle_result.get("verdict") or {})
        result["external_verdict"] = verdict
        result["calibration_summary"] = _calibration_summary(state)
        result["passed"] = client_result.get("passed") is True
    except Exception as error:
        result["runner_error"] = f"{type(error).__name__}: {error}"
        result["passed"] = False
        try:
            result["external_state"] = _json_get(state_url)
        except Exception:
            pass
    finally:
        if target_hwnd and win32gui.IsWindow(target_hwnd):
            try:
                win32gui.PostMessage(target_hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
        _terminate_tree(browser_process)
        _terminate_tree(server_process)
        shutil.rmtree(profile_dir, ignore_errors=True)
        result["finished_at"] = datetime.now().astimezone().isoformat()
        report = case_dir / "report.json"
        report.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        result["report"] = str(report)
        print(json.dumps({"event": "visual_case_finished", **result}, ensure_ascii=False), flush=True)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=tuple(VISUAL_CASES))
    parser.add_argument("--suite", choices=tuple(SUITES))
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--seed", type=int, help="Override the deterministic layout seed.")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--list", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list:
        print(json.dumps({"cases": VISUAL_CASES, "suites": SUITES}, ensure_ascii=False, indent=2))
        return 0
    names = (args.case,) if args.case else SUITES.get(args.suite or "smoke", ())
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root = args.output_root.resolve() / f"{timestamp}-suite"
    results: list[dict[str, Any]] = []
    for name in names:
        item = run_visual_case(
            str(name),
            output_root=output_root,
            seed=args.seed,
            max_steps=args.max_steps,
            timeout=args.timeout,
        )
        results.append(item)
        if args.fail_fast and not item.get("passed"):
            break
    report = {
        "entrypoint": "electron_client_windows_uia",
        "goal_admission_required": True,
        "case_count": len(results),
        "passed_count": sum(item.get("passed") is True for item in results),
        "results": results,
    }
    report["passed"] = bool(results) and report["passed_count"] == len(results)
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / "suite-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    report["report"] = str(report_path)
    print(json.dumps({"event": "visual_suite_finished", **report}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
