#!/usr/bin/env python3
"""Run generic visual interactions through the visible Electron client.

The task is entered through the real client with cold start enabled.  After the
Run button is clicked, the controlled browser target is returned to the
foreground so the desktop agent receives the same visible scene a user would.
Success is decided by trusted browser input events, never by the agent's own
completion text or by a direct backend invocation.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
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
import win32process
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

VISUAL_CASES: dict[str, dict[str, Any]] = {
    "precise-click": {
        "scenario": "precise_click",
        "task": VISUAL_TEST_TASK,
        "seed": 424242,
        "max_steps": 18,
        "timeout": 300,
        "runtime_version": "vnext",
    },
    "scroll-reveal": {
        "scenario": "scroll_reveal",
        "task": VISUAL_TEST_TASK,
        "seed": 20260722,
        "max_steps": 24,
        "timeout": 360,
        "runtime_version": "vnext",
    },
    "keyboard-entry": {
        "scenario": "keyboard_entry",
        "task": VISUAL_TEST_TASK,
        "seed": 31001,
        "max_steps": 24,
        "timeout": 360,
        "runtime_version": "vnext",
    },
    "keyboard-select-all": {
        "scenario": "keyboard_select_all",
        "task": VISUAL_TEST_TASK,
        "seed": 32001,
        "max_steps": 24,
        "timeout": 360,
        "runtime_version": "vnext",
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
        "task": VISUAL_TEST_TASK,
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
    "vnext-canary": ("precise-click",),
    "vnext-scroll": ("scroll-reveal",),
    "vnext-keyboard": ("keyboard-entry", "keyboard-select-all"),
    "all": tuple(VISUAL_CASES),
}

# Only cases supported by both live primitive surfaces may enter a paired run.
PAIRED_PARITY_CASES = frozenset({"precise-click", "scroll-reveal"})
VNEXT_TURN_STATUSES = frozenset({
    "action_recorded", "verification_required", "verification_recorded",
    "mission_verified", "blocked", "observed", "unavailable",
    "route_deferred", "stale_discarded", "canceled", "invalid_proposal",
    "actor_error", "state_rejected", "busy",
})
MODIFIER_KEYS = {
    "left_ctrl": win32con.VK_LCONTROL,
    "right_ctrl": win32con.VK_RCONTROL,
    "left_shift": win32con.VK_LSHIFT,
    "right_shift": win32con.VK_RSHIFT,
    "left_alt": win32con.VK_LMENU,
    "right_alt": win32con.VK_RMENU,
    "left_win": win32con.VK_LWIN,
    "right_win": win32con.VK_RWIN,
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
    last, matched, _final_read_ok, last_error = _poll_json(
        url,
        predicate,
        timeout=timeout,
    )
    if matched:
        return last
    detail = f"; last_error={last_error}" if last_error else ""
    raise RuntimeError(f"timed out waiting for {url}; last={last}{detail}")


def _poll_json(
    url: str,
    predicate,
    *,
    timeout: float,
) -> tuple[dict[str, Any], bool, bool, str | None]:
    """Poll without losing the final transport/read status."""

    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            last = _json_get(url)
            last_error = None
            if predicate(last):
                return last, True, True, None
        except Exception as error:
            last_error = f"{type(error).__name__}: {error}"
        time.sleep(0.15)
    try:
        last = _json_get(url)
        return last, bool(predicate(last)), True, None
    except Exception as error:
        last_error = f"{type(error).__name__}: {error}"
        return last, False, False, last_error


def _event_stream_marker(state: dict[str, Any]) -> tuple[int, Any, Any]:
    events = state.get("events") or []
    if not isinstance(events, list) or not events:
        return (0, None, None)
    tail = events[-1] if isinstance(events[-1], dict) else {}
    return (len(events), tail.get("received_at"), tail.get("type"))


def _settle_external_state(
    url: str,
    initial: dict[str, Any],
    *,
    quiet_seconds: float = 0.6,
    timeout: float = 3.0,
) -> tuple[dict[str, Any], bool, bool, str | None]:
    """Wait for queued browser reports to drain before terminal validation."""

    latest = initial
    marker = _event_stream_marker(latest)
    quiet_since = time.monotonic()
    deadline = quiet_since + timeout
    last_error: str | None = None
    while time.monotonic() < deadline:
        time.sleep(0.1)
        try:
            observed = _json_get(url)
        except Exception as error:
            quiet_since = time.monotonic()
            last_error = f"{type(error).__name__}: {error}"
            continue
        last_error = None
        latest = observed
        observed_marker = _event_stream_marker(observed)
        if observed_marker != marker:
            marker = observed_marker
            quiet_since = time.monotonic()
            continue
        if time.monotonic() - quiet_since >= quiet_seconds:
            return latest, True, True, None
    try:
        latest = _json_get(url)
        return latest, False, True, None
    except Exception as error:
        return latest, False, False, f"{type(error).__name__}: {error}"


def _trusted_scenario_succeeded(state: dict[str, Any]) -> bool:
    """Wait for persisted browser success, never merely ``page_ready``."""

    return state.get("success") is True


def _modifier_state() -> dict[str, bool]:
    """Read physical modifier state without changing the desktop."""

    return {
        name: bool(win32api.GetAsyncKeyState(virtual_key) & 0x8000)
        for name, virtual_key in MODIFIER_KEYS.items()
    }


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


def _process_tree_pids(root_pid: int) -> set[int]:
    """Return the currently live process tree without broad title fallback."""

    try:
        root = psutil.Process(root_pid)
        return {root.pid, *(child.pid for child in root.children(recursive=True))}
    except psutil.Error:
        return set()


def _find_window(
    title: str,
    *,
    owner_root_pid: int,
    timeout: float = 25.0,
) -> int:
    """Find an exact-title window owned by the newly launched process tree."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches: list[int] = []
        owner_pids = _process_tree_pids(owner_root_pid)

        def visit(hwnd: int, _unused: Any) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            if win32gui.GetWindowText(hwnd) != title:
                return
            _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
            if int(process_id) in owner_pids:
                matches.append(int(hwnd))

        win32gui.EnumWindows(visit, None)
        if matches:
            return matches[-1]
        time.sleep(0.2)
    raise RuntimeError(
        f"owned visible target window was not found: title={title!r} "
        f"root_pid={owner_root_pid}",
    )


def _focus_window(hwnd: int) -> dict[str, Any]:
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise RuntimeError("target window no longer exists")
    # Preserve the target geometry the product is about to bind.  Restoring an
    # already visible maximized window races the Runtime's target capture and
    # turns the E2E driver itself into an untrusted layout mutation.  A truly
    # minimized window still has to be restored before it can be observed.
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.BringWindowToTop(hwnd)
    focused = False
    errors: list[str] = []
    for _attempt in range(3):
        try:
            # The temporary ALT press is the generic Windows foreground-unlock
            # path; it does not send input into the evaluation surface.
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")
        finally:
            win32api.keybd_event(
                win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0,
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


def _case_events(
    state: dict[str, Any],
    *,
    case_id: str,
    scenario: str,
    seed: int,
) -> list[dict[str, Any]]:
    """Return only well-formed events bound to the requested oracle case."""

    return [
        item for item in (state.get("events") or [])
        if isinstance(item, dict)
        and item.get("case_id") == case_id
        and item.get("scenario") == scenario
        and type(item.get("seed")) is int
        and item.get("seed") == seed
    ]


def _keyboard_entry_errors(
    state: dict[str, Any],
    success: dict[str, Any],
    *,
    case_id: str,
    scenario: str,
    seed: int,
) -> list[str]:
    """Independently rebuild a trusted, non-paste text insertion chain."""

    errors: list[str] = []
    events = _case_events(
        state, case_id=case_id, scenario=scenario, seed=seed,
    )
    ready_events = [item for item in events if item.get("type") == "page_ready"]
    successes = [item for item in events if item.get("type") == "scenario_success"]
    if len(ready_events) != 1:
        return ["keyboard entry requires exactly one page-ready identity"]
    if len(successes) != 1 or successes[0] != success:
        errors.append("keyboard entry has no unique matching terminal event")
    terminal_index = events.index(successes[0]) if len(successes) == 1 else len(events)
    ready_index = events.index(ready_events[0])
    if ready_index >= terminal_index:
        return [*errors, "keyboard entry page-ready does not precede success"]
    before_ready = events[:ready_index]
    before_terminal = events[ready_index + 1:terminal_index]
    after_terminal = events[terminal_index + 1:] if terminal_index < len(events) else []
    ready = ready_events[0].get("details") or {}
    details = success.get("details") or {}
    target_id = ready.get("target_id")
    expected = ready.get("expected_text")
    if not (
        isinstance(target_id, str) and target_id
        and isinstance(expected, str) and expected
        and ready.get("initial_value") == ""
        and type(ready.get("candidate_count")) is int
        and ready["candidate_count"] >= 2
    ):
        errors.append("keyboard entry page-ready identity is incomplete")
    if not (
        details.get("target_id") == target_id
        and details.get("expected_text") == expected
        and details.get("final_value") == expected
        and details.get("wrong_target_activity") is False
        and details.get("sequence_valid") is True
    ):
        errors.append("keyboard entry terminal state does not match page-ready identity")

    forbidden = {
        "trusted_paste", "trusted_drop", "trusted_cut",
        "untrusted_paste", "untrusted_drop", "untrusted_cut",
        "untrusted_beforeinput", "untrusted_input",
        "compositionstart", "compositionupdate", "compositionend",
    }
    causal_types = forbidden | {"trusted_beforeinput", "trusted_input"}
    if any(item.get("type") in causal_types for item in before_ready):
        errors.append("keyboard entry evidence predates page-ready")
    if any(item.get("type") in forbidden for item in before_terminal):
        errors.append("keyboard entry used an untrusted, clipboard, drop, or composition path")
    if any(
        item.get("type") in forbidden | {"trusted_beforeinput", "trusted_input"}
        for item in after_terminal
    ):
        errors.append("keyboard entry changed after terminal success")

    insertion_events = [
        item for item in before_terminal
        if item.get("type") in {"trusted_beforeinput", "trusted_input"}
    ]
    if not insertion_events or len(insertion_events) % 2:
        errors.append("keyboard entry evidence is not paired")
        return errors
    prefix = ""
    for index in range(0, len(insertion_events), 2):
        before, inserted = insertion_events[index:index + 2]
        before_details = before.get("details") or {}
        inserted_details = inserted.get("details") or {}
        data = before_details.get("data")
        valid = bool(
            before.get("type") == "trusted_beforeinput"
            and inserted.get("type") == "trusted_input"
            and before.get("trusted") is True
            and inserted.get("trusted") is True
            and before_details.get("target_id") == target_id
            and inserted_details.get("target_id") == target_id
            and before_details.get("input_type") == "insertText"
            and inserted_details.get("input_type") == "insertText"
            and before_details.get("is_composing") is False
            and inserted_details.get("is_composing") is False
            and isinstance(data, str) and data
            and inserted_details.get("data") == data
            and before_details.get("value_before") == prefix
        )
        if valid:
            prefix += data
            valid = bool(
                inserted_details.get("value") == prefix
                and isinstance(expected, str)
                and expected.startswith(prefix)
            )
        if not valid:
            errors.append("keyboard entry trusted insertion chain is invalid")
            break
    pair_count = len(insertion_events) // 2
    if (
        prefix != expected
        or type(details.get("beforeinput_count")) is not int
        or details.get("beforeinput_count") != pair_count
        or type(details.get("input_count")) is not int
        or details.get("input_count") != pair_count
    ):
        errors.append("keyboard entry does not reconstruct the exact visible code")
    return errors


def _keyboard_select_all_errors(
    state: dict[str, Any],
    success: dict[str, Any],
    *,
    case_id: str,
    scenario: str,
    seed: int,
) -> list[str]:
    """Independently prove the complete trusted shortcut and unchanged text."""

    errors: list[str] = []
    events = _case_events(
        state, case_id=case_id, scenario=scenario, seed=seed,
    )
    ready_events = [item for item in events if item.get("type") == "page_ready"]
    successes = [item for item in events if item.get("type") == "scenario_success"]
    if len(ready_events) != 1:
        return ["keyboard selection requires exactly one page-ready identity"]
    if len(successes) != 1 or successes[0] != success:
        errors.append("keyboard selection has no unique matching terminal event")
    terminal_index = events.index(successes[0]) if len(successes) == 1 else len(events)
    ready_index = events.index(ready_events[0])
    if ready_index >= terminal_index:
        return [*errors, "keyboard selection page-ready does not precede success"]
    before_ready = events[:ready_index]
    before_terminal = events[ready_index + 1:terminal_index]
    after_terminal = events[terminal_index + 1:] if terminal_index < len(events) else []
    ready = ready_events[0].get("details") or {}
    details = success.get("details") or {}
    target_id = ready.get("target_id")
    original = ready.get("original_text")
    if not (
        isinstance(target_id, str) and target_id
        and isinstance(original, str) and original
        and type(ready.get("candidate_count")) is int
        and ready["candidate_count"] >= 2
        and type(ready.get("selection_start")) is int
        and type(ready.get("selection_end")) is int
        and ready["selection_start"] == ready["selection_end"]
        and 0 <= ready["selection_start"] <= len(original)
    ):
        errors.append("keyboard selection page-ready identity is incomplete")
    if not (
        details.get("target_id") == target_id
        and details.get("original_text") == original
        and details.get("final_text") == original
        and type(details.get("selection_start")) is int
        and details.get("selection_start") == 0
        and isinstance(original, str)
        and type(details.get("selection_end")) is int
        and details.get("selection_end") == len(original)
        and details.get("modifier_released") is True
        and type(details.get("mutation_count")) is int
        and details.get("mutation_count") == 0
        and details.get("wrong_target_activity") is False
        and details.get("sequence_valid") is True
    ):
        errors.append("keyboard selection terminal state is not exact")

    forbidden = {
        "trusted_beforeinput", "trusted_input", "trusted_cut",
        "trusted_paste", "trusted_drop", "untrusted_beforeinput",
        "untrusted_input", "untrusted_cut", "untrusted_paste",
        "untrusted_drop", "untrusted_keydown", "untrusted_keyup",
    }
    causal_types = forbidden | {"trusted_keydown", "trusted_keyup"}
    if any(item.get("type") in causal_types for item in before_ready):
        errors.append("keyboard selection evidence predates page-ready")
    if any(item.get("type") in forbidden for item in before_terminal):
        errors.append("keyboard selection used mutation, clipboard, drop, or untrusted input")
    if any(
        item.get("type") in forbidden | {"trusted_keydown", "trusted_keyup"}
        for item in after_terminal
    ):
        errors.append("keyboard selection changed after terminal success")

    keys = [
        item for item in before_terminal
        if item.get("type") in {"trusted_keydown", "trusted_keyup"}
        and str((item.get("details") or {}).get("key") or "").casefold()
        in {"control", "a"}
    ]
    expected_phases = (
        ("trusted_keydown", "control", True),
        ("trusted_keydown", "a", True),
        ("trusted_keyup", "a", True),
        ("trusted_keyup", "control", False),
    )
    if len(keys) != len(expected_phases):
        errors.append("keyboard selection lacks one complete shortcut sequence")
        return errors
    for index, (event, (event_type, key, ctrl)) in enumerate(
        zip(keys, expected_phases, strict=True),
    ):
        value = event.get("details") or {}
        # Validate logical shortcut identity; DOM ``code`` is a physical,
        # layout-dependent diagnostic and can be empty for VK SendInput.
        valid = bool(
            event.get("type") == event_type
            and event.get("trusted") is True
            and str(value.get("key") or "").casefold() == key
            and value.get("target_id") == target_id
            and value.get("content") == original
            and value.get("ctrl_key") is ctrl
            and value.get("alt_key") is False
            and value.get("shift_key") is False
            and value.get("meta_key") is False
            and value.get("repeat") is False
        )
        if index >= 2:
            valid = bool(
                valid
                and type(value.get("selection_start")) is int
                and value.get("selection_start") == 0
                and isinstance(original, str)
                and type(value.get("selection_end")) is int
                and value.get("selection_end") == len(original)
            )
        if not valid:
            errors.append("keyboard selection trusted shortcut sequence is invalid")
            break
    return errors


def external_verdict(
    state: dict[str, Any],
    *,
    case_id: str,
    scenario: str,
    seed: int,
    expected_variant: int | None = None,
) -> dict[str, Any]:
    """Validate ground truth produced by trusted real browser input events."""

    errors: list[str] = []
    event = state.get("success_event") or {}
    details = event.get("details") or {}
    if state.get("success") is not True:
        errors.append("no trusted scenario_success event")
    if state.get("case_id") != case_id:
        errors.append("case id mismatch")
    if state.get("scenario") != scenario:
        errors.append("scenario mismatch")
    if int(state.get("seed") or 0) != int(seed):
        errors.append("seed mismatch")
    if event.get("trusted") is not True or event.get("succeeded") is not True:
        errors.append("success event is not trusted and successful")
    if event.get("type") != "scenario_success":
        errors.append("success evidence is not a scenario_success event")
    if event.get("case_id") != case_id or event.get("scenario") != scenario:
        errors.append("success event identity mismatch")
    if int(event.get("seed") or 0) != int(seed):
        errors.append("success event seed mismatch")
    if details.get("succeeded") is not True:
        errors.append("scenario details are not successful")

    move_count = int(event.get("move_count") or 0)
    if scenario == "precise_click":
        ready = next(
            (
                item for item in reversed(state.get("events") or [])
                if item.get("type") == "page_ready"
                and item.get("case_id") == case_id
                and item.get("scenario") == scenario
                and int(item.get("seed") or 0) == int(seed)
            ),
            {},
        )
        ready_details = ready.get("details") or {}
        target = details.get("target") or {}
        ready_target = ready_details.get("target") or {}
        geometry_keys = ("left", "top", "right", "bottom", "width", "height")
        geometry_valid = all(
            isinstance(target.get(key), (int, float))
            and isinstance(ready_target.get(key), (int, float))
            for key in geometry_keys
        )
        if move_count != 0:
            errors.append("precise click unexpectedly recorded pointer movement")
        target_index = details.get("target_index")
        if not isinstance(target_index, int) or not 0 <= target_index < 12:
            errors.append("precise click target identity is invalid")
        elif target_index != ready_details.get("target_index"):
            errors.append("success target identity does not match page-ready identity")
        if int(ready_details.get("candidate_count") or -1) != 12:
            errors.append("precise click candidate count is invalid")
        if int(ready_details.get("distractor_count") or -1) != 11:
            errors.append("precise click candidate identity set is incomplete")
        if not geometry_valid:
            errors.append("precise click target geometry is incomplete")
        elif any(target[key] != ready_target[key] for key in geometry_keys):
            errors.append("success target geometry does not match page-ready identity")
        elif not (
            target["width"] > 0
            and target["height"] > 0
            and target["right"] - target["left"] == target["width"]
            and target["bottom"] - target["top"] == target["height"]
            and target["left"] <= int(event.get("client_x") or -1) < target["right"]
            and target["top"] <= int(event.get("client_y") or -1) < target["bottom"]
        ):
            errors.append("trusted click coordinates are outside canonical target geometry")
    elif scenario == "drag_fit":
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
    elif scenario == "scroll_reveal":
        all_events = state.get("events") or []
        ready = next(
            (
                item for item in reversed(all_events)
                if item.get("type") == "page_ready"
                and item.get("case_id") == case_id
                and item.get("scenario") == scenario
                and int(item.get("seed") or 0) == int(seed)
            ),
            {},
        )
        ready_details = ready.get("details") or {}
        trusted_wheels = [
            item for item in all_events
            if item.get("type") == "trusted_wheel"
            and item.get("trusted") is True
            and item.get("case_id") == case_id
            and item.get("scenario") == scenario
            and int(item.get("seed") or 0) == int(seed)
        ]
        def number(value: Any) -> bool:
            return isinstance(value, (int, float)) and not isinstance(value, bool)

        def valid_box(value: Any) -> bool:
            return isinstance(value, dict) and all(
                number(value.get(key))
                for key in ("left", "top", "right", "bottom", "width", "height")
            ) and bool(
                value["width"] > 0
                and value["height"] > 0
                and value["right"] - value["left"] == value["width"]
                and value["bottom"] - value["top"] == value["height"]
            )

        direction = details.get("direction")
        ready_direction = ready_details.get("direction")
        if direction not in {"up", "down"} or direction != ready_direction:
            errors.append("scroll direction identity does not match page-ready state")
        transition_keys = (
            "direction", "initial_scroll_top", "before_scroll_top",
            "after_scroll_top", "scroll_delta", "total_scroll_delta",
            "max_scroll_top", "wheel_delta_y", "wheel_count",
            "target_fully_visible",
        )
        matching_wheels = [
            item for item in trusted_wheels
            if all(
                (item.get("details") or {}).get(key) == details.get(key)
                for key in transition_keys
            )
        ]
        wheel = matching_wheels[-1] if matching_wheels else {}
        if not trusted_wheels:
            errors.append("scroll reveal has no trusted wheel evidence")
        elif not matching_wheels:
            errors.append("success does not match a trusted wheel transition")

        numeric_keys = (
            "initial_scroll_top", "before_scroll_top", "after_scroll_top",
            "scroll_delta", "total_scroll_delta", "max_scroll_top",
            "wheel_delta_y",
        )
        if not all(number(details.get(key)) for key in numeric_keys):
            errors.append("scroll transition metrics are incomplete")
        else:
            initial = details["initial_scroll_top"]
            before = details["before_scroll_top"]
            after = details["after_scroll_top"]
            delta = details["scroll_delta"]
            total_delta = details["total_scroll_delta"]
            maximum = details["max_scroll_top"]
            wheel_delta = details["wheel_delta_y"]
            if (
                initial != ready_details.get("initial_scroll_top")
                or maximum != ready_details.get("max_scroll_top")
            ):
                errors.append("scroll range identity does not match page-ready state")
            if delta == 0 or after == before or after - before != delta:
                errors.append("trusted wheel did not change scrollTop")
            if total_delta == 0 or after - initial != total_delta:
                errors.append("scrollTop did not move away from its initial position")
            if not 0 <= after <= maximum:
                errors.append("scrollTop is outside the canonical pane range")
            direction_valid = (
                direction == "down" and wheel_delta > 0 and delta > 0 and total_delta > 0
            ) or (
                direction == "up" and wheel_delta < 0 and delta < 0 and total_delta < 0
            )
            if not direction_valid:
                errors.append("trusted wheel moved in the wrong reveal direction")

        pane = details.get("pane_viewport") or {}
        ready_pane = ready_details.get("pane_viewport") or {}
        target = details.get("target") or {}
        ready_target = ready_details.get("target") or {}
        if not all(valid_box(value) for value in (pane, ready_pane, target, ready_target)):
            errors.append("scroll reveal geometry is incomplete")
        else:
            if pane != ready_pane:
                errors.append("scroll pane geometry changed after page-ready")
            client_x = event.get("client_x")
            client_y = event.get("client_y")
            if not (
                number(client_x) and number(client_y)
                and pane["left"] <= client_x < pane["right"]
                and pane["top"] <= client_y < pane["bottom"]
            ):
                errors.append("trusted wheel coordinates are outside the scroll pane")
            elif wheel and (
                wheel.get("client_x") != client_x or wheel.get("client_y") != client_y
            ):
                errors.append("success coordinates do not match trusted wheel evidence")
            initially_visible = (
                ready_target["left"] >= ready_pane["left"]
                and ready_target["right"] <= ready_pane["right"]
                and ready_target["top"] >= ready_pane["top"]
                and ready_target["bottom"] <= ready_pane["bottom"]
            )
            fully_visible = (
                target["left"] >= pane["left"]
                and target["right"] <= pane["right"]
                and target["top"] >= pane["top"]
                and target["bottom"] <= pane["bottom"]
            )
            if initially_visible:
                errors.append("scroll target was already fully visible at page-ready")
            if details.get("target_fully_visible") is not True or not fully_visible:
                errors.append("scroll target is not completely visible in the pane")
            total_delta = details.get("total_scroll_delta")
            if number(total_delta) and (
                abs((ready_target["top"] - total_delta) - target["top"]) > 1
                or abs((ready_target["bottom"] - total_delta) - target["bottom"]) > 1
            ):
                errors.append("target geometry does not agree with the scrollTop delta")
    elif scenario == "keyboard_entry":
        errors.extend(_keyboard_entry_errors(
            state,
            event,
            case_id=case_id,
            scenario=scenario,
            seed=seed,
        ))
    elif scenario == "keyboard_select_all":
        errors.extend(_keyboard_select_all_errors(
            state,
            event,
            case_id=case_id,
            scenario=scenario,
            seed=seed,
        ))
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


def observe_external_verdict(
    state_url: str,
    *,
    case_id: str,
    scenario: str,
    seed: int,
    expected_variant: int | None = None,
    timeout: float = 5.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Observe a terminal browser state without turning failure into an error."""

    observed_at = time.monotonic()
    state, success_observed, final_read_ok, final_read_error = _poll_json(
        state_url,
        _trusted_scenario_succeeded,
        timeout=timeout,
    )
    stream_quiet: bool | None = None
    if success_observed and state.get("success") is True:
        state, stream_quiet, final_read_ok, final_read_error = (
            _settle_external_state(state_url, state)
        )
    verdict = external_verdict(
        state,
        case_id=case_id,
        scenario=scenario,
        seed=seed,
        expected_variant=expected_variant,
    )
    identity_conclusive = bool(
        final_read_ok
        and stream_quiet is not False
        and state.get("ready") is True
        and state.get("case_id") == case_id
        and state.get("scenario") == scenario
        and type(state.get("seed")) is int
        and state.get("seed") == seed
    )
    verdict["oracle_status"] = (
        "conclusive" if identity_conclusive else "unavailable"
    )
    verdict["oracle_observation_elapsed_ms"] = max(
        0, round((time.monotonic() - observed_at) * 1000),
    )
    verdict["success_observed_before_timeout"] = success_observed
    verdict["event_stream_quiet"] = stream_quiet
    if final_read_error:
        verdict["oracle_read_error"] = final_read_error
    if not identity_conclusive:
        verdict["errors"] = [
            "external oracle identity or final read is unavailable",
            *list(verdict.get("errors") or []),
        ]
        verdict["passed"] = False
    if not success_observed:
        verdict["errors"] = [
            "trusted scenario success was not observed before timeout",
            *list(verdict.get("errors") or []),
        ]
        verdict["passed"] = False
    return state, verdict


def trace_recovery_evidence(client_result: dict[str, Any]) -> dict[str, Any]:
    """Read measurement-only facts from the exact client-bound trace."""

    binding = client_result.get("trace_binding") or {}
    trace_value = str(binding.get("trace_file") or "")
    run_id = str(binding.get("run_id") or "")
    trace_path = Path(trace_value) if trace_value else None
    events: list[dict[str, Any]] = []
    parse_errors: list[str] = []
    if trace_path is not None and trace_path.is_file():
        try:
            lines = trace_path.read_text(encoding="utf-8").splitlines()
        except Exception as error:
            lines = []
            parse_errors.append(f"{type(error).__name__}: {error}")
        for line_number, raw_line in enumerate(lines, start=1):
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
                if isinstance(event, dict):
                    events.append(event)
                else:
                    parse_errors.append(f"line {line_number}: event is not an object")
            except Exception as error:
                parse_errors.append(
                    f"line {line_number}: {type(error).__name__}: {error}",
                )

    trace_source_complete = bool(
        binding.get("passed") is True
        and run_id
        and trace_path is not None
        and trace_path.is_file()
        and not parse_errors
        and not list(binding.get("parse_errors") or [])
    )
    runtime_version = str(
        (client_result.get("configuration") or {}).get("runtime_version") or "",
    )
    terminal_entries = [
        (index, event) for index, event in enumerate(events)
        if event.get("type") == "arp_run_done" and event.get("run_id") == run_id
    ] if trace_source_complete else []
    terminal_status = (
        str(terminal_entries[0][1].get("status") or "")
        if len(terminal_entries) == 1
        and terminal_entries[0][1].get("status")
        in {"done", "failed", "canceled"}
        else None
    )
    trace_complete = bool(trace_source_complete and terminal_status is not None)
    turn_entries = [
        (index, event) for index, event in enumerate(events)
        if event.get("type") == "vnext_mission_runtime_turn"
    ]
    turns = [event for _index, event in turn_entries]
    turn_indexes = [event.get("turn_index") for event in turns]
    action_turns = [
        event for event in turns if event.get("status") == "action_recorded"
    ]
    turn_trace_complete = bool(
        trace_complete
        and runtime_version == "vnext"
        and (
            bool(turns)
            or terminal_status in {"failed", "canceled"}
        )
        and all(event.get("run_id") == run_id for event in turns)
        and turn_indexes == list(range(len(turns)))
        and all(event.get("status") in VNEXT_TURN_STATUSES for event in turns)
        and all(index < terminal_entries[0][0] for index, _event in turn_entries)
        and all(
            event.get("local_visual_change")
            in {"changed", "unchanged", "unknown"}
            for event in action_turns
        )
        and all(type(event.get("frame_comparable")) is bool for event in action_turns)
        and all(
            type(event.get("no_progress_streak")) is int
            and event["no_progress_streak"] >= 0
            for event in action_turns
        )
    )

    verdict = client_result.get("task_verdict") or {}
    oracle_conclusive = (
        verdict.get("oracle_status") == "conclusive"
        and type(verdict.get("passed")) is bool
    )
    oracle_pass = (
        verdict.get("passed")
        if oracle_conclusive and type(verdict.get("passed")) is bool
        else None
    )
    runtime_done = terminal_status == "done" if terminal_status is not None else None
    return {
        "format": "client-recovery-evidence/1",
        "trace_file": trace_value,
        "run_id": run_id or None,
        "runtime_version": runtime_version or None,
        "trace_complete": trace_complete,
        "turn_trace_complete": (
            turn_trace_complete if runtime_version == "vnext" else None
        ),
        "oracle_status": "conclusive" if oracle_conclusive else "unavailable",
        "external_oracle_pass": oracle_pass,
        "grounded_product_pass": client_result.get("passed") is True,
        "false_done_count": (
            int(runtime_done is True and oracle_pass is False)
            if oracle_pass is not None and runtime_done is not None
            else None
        ),
        "runtime_terminal_status": terminal_status,
        "actor_turn_count": len(turns) if turn_trace_complete else None,
        "action_count": len(action_turns) if turn_trace_complete else None,
        "effect_counts": (
            {
                value: sum(
                    event.get("local_visual_change") == value
                    for event in action_turns
                )
                for value in ("changed", "unchanged", "unknown")
            }
            if turn_trace_complete else None
        ),
        "frame_incomparable_count": (
            sum(event.get("frame_comparable") is False for event in action_turns)
            if turn_trace_complete else None
        ),
        "max_unchanged_frame_streak": (
            max(
                (int(event["no_progress_streak"]) for event in action_turns),
                default=0,
            )
            if turn_trace_complete else None
        ),
        "wall_elapsed_ms": (
            round(float(client_result["duration_seconds"]) * 1000)
            if isinstance(client_result.get("duration_seconds"), (int, float))
            else None
        ),
        "parse_errors": parse_errors,
    }


def _parse_seed_csv(raw: str | None) -> tuple[int, ...]:
    if raw is None or not raw.strip():
        return ()
    values: list[int] = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            raise ValueError("seed list contains an empty item")
        value = int(text)
        if not 0 <= value <= 0xFFFFFFFF:
            raise ValueError("seed must be between 0 and 4294967295")
        if value not in values:
            values.append(value)
    return tuple(values)


def build_run_matrix(
    names: tuple[str, ...],
    *,
    seeds: tuple[int, ...] = (),
    runtime_version: str | None = None,
    paired: bool = False,
) -> tuple[dict[str, Any], ...]:
    """Build a deterministic multi-seed/runtime matrix without running a case."""

    if runtime_version not in {None, "legacy", "vnext"}:
        raise ValueError("runtime_version must be legacy, vnext, or None")
    if paired and runtime_version is not None:
        raise ValueError("paired matrix cannot also force one runtime_version")
    unsupported = set(names) - PAIRED_PARITY_CASES if paired else set()
    if unsupported:
        raise ValueError(
            "paired evaluation requires primitive parity; unsupported cases: "
            + ", ".join(sorted(unsupported)),
        )
    rows: list[dict[str, Any]] = []
    pair_index = 0
    for name in names:
        if name not in VISUAL_CASES:
            raise ValueError(f"unknown visual case: {name}")
        case = VISUAL_CASES[name]
        effective_seeds = tuple(dict.fromkeys(seeds)) or (int(case["seed"]),)
        for seed in effective_seeds:
            if type(seed) is not int or not 0 <= seed <= 0xFFFFFFFF:
                raise ValueError("seed must be an integer between 0 and 4294967295")
            group_id = f"{name}:seed-{int(seed)}"
            pair_id = group_id if paired else None
            if paired:
                runtimes = (
                    ("legacy", "vnext")
                    if pair_index % 2 == 0
                    else ("vnext", "legacy")
                )
            else:
                runtimes = (
                    runtime_version
                    or str(case.get("runtime_version") or "legacy"),
                )
            for order, selected_runtime in enumerate(runtimes):
                rows.append({
                    "name": name,
                    "seed": int(seed),
                    "runtime_version": selected_runtime,
                    "group_id": group_id,
                    "pair_id": pair_id,
                    "pair_order": order,
                })
            pair_index += 1
    return tuple(rows)


def _percentile(values: list[int], ratio: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * ratio) - 1)
    return ordered[index]


def _metric_summary(items: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [
        int(item[key]) for item in items
        if item.get("arm_observation_valid") is True
        and type(item.get(key)) is int
    ]
    return {
        "observed_count": len(values),
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
    }


def _same_identity(items: list[dict[str, Any]], key: str) -> bool:
    values = [item["result"].get(key) for item in items]
    return len(values) == 2 and all(values) and len({
        json.dumps(value, sort_keys=True, separators=(",", ":"))
        for value in values
    }) == 1


def _runtime_identity_current(
    value: Any,
    git_identity: dict[str, Any] | None,
) -> bool:
    if not isinstance(value, dict) or not git_identity:
        return False
    before = value.get("before") or {}
    after = value.get("after") or {}
    if not before or before != after:
        return False
    runtime_git = before.get("git") or {}
    source = before.get("source") or {}
    loaded = str(runtime_git.get("loaded_commit") or "")
    loaded_stamp = str(source.get("loaded_stamp") or "")
    current_stamp = str(source.get("current_stamp") or "")
    return bool(
        before.get("contract_version")
        and type(before.get("pid")) is int
        and before["pid"] > 0
        and str(before.get("repo_root") or "")
        and loaded
        and loaded == str(runtime_git.get("current_commit") or "")
        and str(git_identity.get("revision") or "").startswith(loaded)
        and runtime_git.get("dirty") is False
        and source.get("drifted") is False
        and loaded_stamp
        and loaded_stamp == current_stamp
    )


def aggregate_recovery_evidence(
    results: list[dict[str, Any]],
    *,
    git_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate only measurements explicitly present in case reports."""

    by_runtime: dict[str, list[dict[str, Any]]] = {}
    by_pair: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        runtime = str(result.get("runtime_version") or "unknown")
        evidence = result.get("recovery_evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        client_result = result.get("client_result") or {}
        trace_binding = client_result.get("trace_binding") or {}
        memory_isolation = client_result.get("memory_isolation") or {}
        runner_error = bool(
            result.get("runner_error") or client_result.get("runner_error")
        )
        oracle_status = (
            "conclusive"
            if evidence.get("oracle_status") == "conclusive"
            and type(evidence.get("external_oracle_pass")) is bool
            else "unavailable"
        )
        oracle_pass = (
            evidence.get("external_oracle_pass")
            if oracle_status == "conclusive"
            else None
        )
        observation_valid = bool(
            not runner_error
            and trace_binding.get("passed") is True
            and not list(trace_binding.get("parse_errors") or [])
            and memory_isolation.get("passed") is True
            and evidence.get("trace_complete") is True
            and (
                runtime != "vnext"
                or evidence.get("turn_trace_complete") is True
            )
            and oracle_pass is not None
        )
        item = {
            "result": result,
            "grounded_product_pass": bool(
                observation_valid and result.get("passed") is True
            ),
            "runtime_version": runtime,
            "oracle_status": oracle_status,
            "external_oracle_pass": oracle_pass,
            "runner_error": runner_error,
            "arm_observation_valid": observation_valid,
            "actor_turn_count": evidence.get("actor_turn_count"),
            "max_unchanged_frame_streak": evidence.get(
                "max_unchanged_frame_streak",
            ),
            "false_done_count": evidence.get("false_done_count"),
            "wall_elapsed_ms": evidence.get("wall_elapsed_ms"),
        }
        by_runtime.setdefault(runtime, []).append(item)
        pair_id = result.get("pair_id")
        if isinstance(pair_id, str) and pair_id:
            by_pair.setdefault(pair_id, []).append(item)

    runtime_summary: dict[str, Any] = {}
    for runtime, items in sorted(by_runtime.items()):
        false_done_values = [
            int(item["false_done_count"])
            for item in items
            if item.get("arm_observation_valid") is True
            and type(item.get("false_done_count")) is int
        ]
        runtime_summary[runtime] = {
            "attempted_arm_count": len(items),
            "valid_observation_count": sum(
                item["arm_observation_valid"] for item in items
            ),
            "runner_error_count": sum(item["runner_error"] for item in items),
            "oracle": {
                "conclusive_count": sum(
                    item["oracle_status"] == "conclusive" for item in items
                ),
                "unavailable_count": sum(
                    item["oracle_status"] != "conclusive" for item in items
                ),
                "pass_count": sum(
                    item["external_oracle_pass"] is True for item in items
                ),
                "fail_count": sum(
                    item["external_oracle_pass"] is False for item in items
                ),
            },
            "grounded_product_pass_count": sum(
                item["grounded_product_pass"] for item in items
            ),
            "false_done": {
                "observed_count": len(false_done_values),
                "count": sum(false_done_values) if false_done_values else None,
            },
            "actor_turn_count": _metric_summary(items, "actor_turn_count"),
            "max_unchanged_frame_streak": _metric_summary(
                items,
                "max_unchanged_frame_streak",
            ),
            "wall_elapsed_ms": _metric_summary(items, "wall_elapsed_ms"),
        }

    comparisons: list[dict[str, Any]] = []
    for pair_id, items in sorted(by_pair.items()):
        arms = {
            runtime: [
                item for item in items if item["runtime_version"] == runtime
            ]
            for runtime in ("legacy", "vnext")
        }
        arms_present = len(items) == 2 and all(
            len(arms[runtime]) == 1 for runtime in arms
        )
        legacy = arms["legacy"][0] if len(arms["legacy"]) == 1 else None
        vnext = arms["vnext"][0] if len(arms["vnext"]) == 1 else None
        reasons: list[str] = []
        if not arms_present:
            reasons.append("requires exactly one legacy and one vnext arm")
        if not _same_identity(items, "evaluation_config_fingerprint"):
            reasons.append("evaluation configuration identity is missing or mismatched")
        if not _same_identity(items, "model_configuration_identity"):
            reasons.append("model configuration identity is missing or mismatched")
        if not _same_identity(items, "runtime_service_identity"):
            reasons.append("runtime service identity is missing or mismatched")
        elif not _runtime_identity_current(
            items[0]["result"].get("runtime_service_identity"),
            git_identity,
        ):
            reasons.append("runtime service was stale, dirty, or source-drifted")
        if any(not item["arm_observation_valid"] for item in items):
            reasons.append("one or more arms lack conclusive run-scoped evidence")
        if not git_identity or not git_identity.get("revision"):
            reasons.append("git revision identity is unavailable")
        elif git_identity.get("dirty") is not False:
            reasons.append("git worktree was not clean at evaluation start")
        eligible = arms_present and not reasons

        def observed(item: dict[str, Any] | None) -> dict[str, Any] | None:
            if item is None:
                return None
            return {
                "grounded_product_pass": item["grounded_product_pass"],
                "external_oracle_pass": item["external_oracle_pass"],
                "arm_observation_valid": item["arm_observation_valid"],
                "wall_elapsed_ms": item["wall_elapsed_ms"],
            }

        comparisons.append({
            "pair_id": pair_id,
            "arm_counts": {key: len(value) for key, value in arms.items()},
            "arms_present": arms_present,
            "eligible_for_ab": eligible,
            "ineligibility_reasons": reasons,
            "observations": {
                "legacy": observed(legacy),
                "vnext": observed(vnext),
            },
            "legacy_passed": (
                legacy["grounded_product_pass"] if eligible and legacy else None
            ),
            "vnext_passed": (
                vnext["grounded_product_pass"] if eligible and vnext else None
            ),
            "wall_elapsed_delta_vnext_minus_legacy": (
                int(vnext["wall_elapsed_ms"]) - int(legacy["wall_elapsed_ms"])
                if eligible
                and legacy is not None
                and vnext is not None
                and type(legacy.get("wall_elapsed_ms")) is int
                and type(vnext.get("wall_elapsed_ms")) is int
                else None
            ),
        })
    return {
        "format": "client-recovery-summary/1",
        "by_runtime": runtime_summary,
        "paired_comparisons": comparisons,
        "paired_arm_group_count": len(comparisons),
        "eligible_paired_comparison_count": sum(
            item["eligible_for_ab"] is True for item in comparisons
        ),
    }


def _git_identity() -> dict[str, Any]:
    def command(*args: str) -> str | None:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=str(WORKSPACE),
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        except Exception:
            return None

    status = command("status", "--porcelain")
    return {
        "revision": command("rev-parse", "HEAD"),
        "branch": command("branch", "--show-current"),
        "dirty": None if status is None else bool(status),
    }


def _fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def run_visual_case(
    name: str,
    *,
    output_root: Path,
    seed: int | None = None,
    runtime_version: str | None = None,
    group_id: str | None = None,
    pair_id: str | None = None,
    max_steps: int | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    if os.name != "nt" or not ctypes.windll.user32.GetForegroundWindow():
        raise RuntimeError("an unlocked interactive Windows desktop is required")
    source = dict(VISUAL_CASES[name])
    if seed is not None:
        if type(seed) is not int or not 0 <= seed <= 0xFFFFFFFF:
            raise ValueError("seed must be an integer between 0 and 4294967295")
        source["seed"] = int(seed)
    if runtime_version is not None:
        if runtime_version not in {"legacy", "vnext"}:
            raise ValueError("runtime_version must be legacy or vnext")
        source["runtime_version"] = runtime_version
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
    evaluation_config = {
        "format": "client-visual-evaluation-config/1",
        "name": name,
        "scenario": scenario,
        "seed": seed,
        "task": source["task"],
        "max_steps": int(source["max_steps"]),
        "timeout": int(source["timeout"]),
        "expected_variant": source.get("expected_variant"),
        "entrypoint": "electron_client_windows_uia",
        "cold_start_required": True,
        "ground_truth": "trusted_browser_input_events",
    }
    result: dict[str, Any] = {
        "name": name,
        "case_id": case_id,
        "scenario": scenario,
        "seed": seed,
        "task": source["task"],
        "entrypoint": "electron_client_windows_uia",
        "cold_start_required": True,
        "ground_truth": "trusted_browser_input_events",
        "runtime_version": str(source.get("runtime_version") or "legacy"),
        "group_id": group_id or f"{name}:seed-{seed}",
        "pair_id": pair_id,
        "max_steps": int(source["max_steps"]),
        "timeout": int(source["timeout"]),
        "expected_variant": source.get("expected_variant"),
        "evaluation_config": evaluation_config,
        "evaluation_config_fingerprint": _fingerprint(evaluation_config),
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
        title = "WinPilot Visual Interaction Target"
        target_hwnd = _find_window(
            title,
            owner_root_pid=int(browser_process.pid),
        )
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
            state, verdict = observe_external_verdict(
                state_url,
                case_id=case_id,
                scenario=scenario,
                seed=seed,
                expected_variant=source.get("expected_variant"),
                timeout=5,
            )
            verdict = {**verdict, "source": "external_browser_state"}
            oracle_result.update({"state": state, "verdict": verdict})
            return verdict

        result["modifier_state_before"] = _modifier_state()
        if any(result["modifier_state_before"].values()):
            raise RuntimeError(
                "modifier key was already pressed before the product run",
            )
        client_result = client_harness.run_case(
            name,
            {
                "task": source["task"],
                "max_steps": int(source["max_steps"]),
                "timeout": int(source["timeout"]),
                "runtime_version": str(source.get("runtime_version") or "legacy"),
            },
            output_root=case_dir / "client",
            reset_session=True,
            after_submit=handoff,
            independent_verifier=verify_external_state,
        )
        result["client_result"] = client_result
        model_before = dict(
            (client_result.get("runtime_before") or {}).get("model_profile") or {},
        )
        terminal_runtime = (
            (client_result.get("result") or {}).get("runtime") or {}
        )
        model_after = dict(terminal_runtime.get("model_profile") or {})
        runtime_before = dict(
            (client_result.get("runtime_before") or {}).get("runtime_identity")
            or {},
        )
        runtime_after = dict(terminal_runtime.get("runtime_identity") or {})
        result["model_configuration_identity"] = (
            {
                "provider": str(model_before.get("provider") or ""),
                "model": str(model_before.get("model") or ""),
                "supports_vision": model_before.get("supports_vision") is True,
            }
            if model_before == model_after
            and model_before.get("provider")
            and model_before.get("model")
            else None
        )
        result["runtime_service_identity"] = (
            {"before": runtime_before, "after": runtime_after}
            if runtime_before and runtime_after else None
        )
        result["recovery_evidence"] = trace_recovery_evidence(client_result)
        state = dict(oracle_result.get("state") or {})
        result["external_state"] = state
        verdict = dict(oracle_result.get("verdict") or {})
        result["external_verdict"] = verdict
        result["modifier_state_after"] = _modifier_state()
        modifiers_released = not any(result["modifier_state_after"].values())
        result["modifiers_released"] = modifiers_released
        result["passed"] = bool(
            client_result.get("passed") is True and modifiers_released
        )
        if not modifiers_released:
            result.setdefault("evaluation_errors", []).append(
                "a physical modifier remained pressed after the product run",
            )
    except Exception as error:
        result["runner_error"] = f"{type(error).__name__}: {error}"
        result["passed"] = False
        try:
            result["external_state"] = _json_get(state_url)
        except Exception:
            pass
    finally:
        if "modifier_state_after" not in result:
            try:
                result["modifier_state_after"] = _modifier_state()
                result["modifiers_released"] = not any(
                    result["modifier_state_after"].values()
                )
            except Exception:
                pass
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
    parser.add_argument(
        "--seeds",
        help="Comma-separated deterministic seeds; cannot be combined with --seed.",
    )
    parser.add_argument("--runtime-version", choices=("legacy", "vnext"))
    parser.add_argument(
        "--paired",
        action="store_true",
        help="Run independent legacy and vNext cases for every case/seed pair.",
    )
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.seed is not None and args.seeds:
        parser.error("--seed and --seeds are mutually exclusive")
    if args.paired and args.runtime_version is not None:
        parser.error("--paired and --runtime-version are mutually exclusive")
    try:
        args.seed_values = (
            _parse_seed_csv(args.seeds)
            if args.seeds
            else (args.seed,)
            if args.seed is not None
            else ()
        )
    except ValueError as error:
        parser.error(str(error))
    return args


def main() -> int:
    args = parse_args()
    if args.list:
        print(json.dumps({"cases": VISUAL_CASES, "suites": SUITES}, ensure_ascii=False, indent=2))
        return 0
    names = (args.case,) if args.case else SUITES.get(args.suite or "smoke", ())
    matrix = build_run_matrix(
        tuple(str(name) for name in names),
        seeds=tuple(args.seed_values),
        runtime_version=args.runtime_version,
        paired=args.paired,
    )
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root = args.output_root.resolve() / f"{timestamp}-suite"
    git_identity = _git_identity()
    results: list[dict[str, Any]] = []
    failed_group_id: str | None = None
    for row in matrix:
        if failed_group_id is not None and (
            not args.paired or row["group_id"] != failed_group_id
        ):
            break
        item = run_visual_case(
            str(row["name"]),
            output_root=output_root,
            seed=int(row["seed"]),
            runtime_version=str(row["runtime_version"]),
            group_id=str(row["group_id"]),
            pair_id=row.get("pair_id"),
            max_steps=args.max_steps,
            timeout=args.timeout,
        )
        results.append(item)
        if args.fail_fast and not item.get("passed") and failed_group_id is None:
            failed_group_id = str(row["group_id"])
    planned_pair_ids = {
        str(row["pair_id"]) for row in matrix if row.get("pair_id")
    }
    executed_pair_ids = {
        str(item["pair_id"]) for item in results if item.get("pair_id")
    }
    report = {
        "git": git_identity,
        "entrypoint": "electron_client_windows_uia",
        "cold_start_required": True,
        "matrix": list(matrix),
        "paired": bool(args.paired),
        "planned_arm_count": len(matrix),
        "executed_arm_count": len(results),
        "planned_pair_count": len(planned_pair_ids),
        "executed_pair_group_count": len(executed_pair_ids),
        "case_count": len(results),
        "passed_count": sum(item.get("passed") is True for item in results),
        "results": results,
        "recovery_summary": aggregate_recovery_evidence(
            results,
            git_identity=git_identity,
        ),
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
