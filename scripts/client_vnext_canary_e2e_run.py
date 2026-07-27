#!/usr/bin/env python3
"""Run the fixed paired legacy/vNext canary through the visible Electron UI.

This runner has no case, seed, budget, timeout, retry, or success-selection
overrides.  Each declared pair runs legacy and vNext exactly once, in its
predeclared order, against fresh browser state.  A failed or invalid pair is
retained and stops the suite after both members have been attempted.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
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
    / "vnext_canary_server.py"
)
DEFAULT_OUTPUT = WORKSPACE / "output" / "playwright" / "vnext-live-canary-e2e"
CAMPAIGN_LEDGER = DEFAULT_OUTPUT / "campaign-ledger.json"
CANARY_TASK = "Complete the task shown in the current visible window exactly as instructed."
RUNTIMES = ("legacy", "vnext")
VNEXT_MODEL_ROLES = frozenset({
    "vnext_visual_actor",
    "vnext_visual_obligation_verifier",
})
FROZEN_RUNTIME_BUDGET = {
    "max_turns": 30,
    "max_runtime_s": 300,
    "max_model_calls": 60,
}
MUTEX_NAME = "Local\\WinPilot-vNext-Minimal-Live-Canary-E2E"
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
ORACLE_SETTLE_SECONDS = 1.0

# Order alternates by seed but is fixed before any result exists.
CANARY_MATRIX: tuple[dict[str, Any], ...] = (
    {
        "case": "click-double-4101",
        "family": "click_double",
        "scenario": "click_double",
        "seed": 4101,
        "runtime_order": ("legacy", "vnext"),
    },
    {
        "case": "click-double-4102",
        "family": "click_double",
        "scenario": "click_double",
        "seed": 4102,
        "runtime_order": ("vnext", "legacy"),
    },
    {
        "case": "scroll-roundtrip-5201",
        "family": "scroll_roundtrip",
        "scenario": "scroll_roundtrip",
        "seed": 5201,
        "runtime_order": ("legacy", "vnext"),
    },
    {
        "case": "scroll-roundtrip-5202",
        "family": "scroll_roundtrip",
        "scenario": "scroll_roundtrip",
        "seed": 5202,
        "runtime_order": ("vnext", "legacy"),
    },
    {
        "case": "type-hotkey-6301",
        "family": "type_hotkey",
        "scenario": "type_hotkey",
        "seed": 6301,
        "runtime_order": ("legacy", "vnext"),
    },
    {
        "case": "type-hotkey-6302",
        "family": "type_hotkey",
        "scenario": "type_hotkey",
        "seed": 6302,
        "runtime_order": ("vnext", "legacy"),
    },
)


class DesktopSuiteMutex:
    """Non-waiting cross-process lock for the entire physical desktop suite."""

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self.name = name
        self.handle = 0

    def __enter__(self) -> "DesktopSuiteMutex":
        if os.name != "nt":
            raise RuntimeError("the paired canary requires Windows")
        kernel32 = ctypes.windll.kernel32
        handle = int(kernel32.CreateMutexW(None, False, self.name) or 0)
        if not handle:
            raise OSError("CreateMutexW failed")
        outcome = int(kernel32.WaitForSingleObject(handle, 0))
        if outcome not in {WAIT_OBJECT_0, WAIT_ABANDONED}:
            kernel32.CloseHandle(handle)
            raise RuntimeError(
                "another physical desktop E2E suite owns the named mutex"
            )
        self.handle = handle
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        if self.handle:
            ctypes.windll.kernel32.ReleaseMutex(self.handle)
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = 0


def _utc_now() -> str:
    return datetime.now().astimezone().isoformat()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _new_manifest() -> dict[str, Any]:
    return {
        "format": "vnext-live-canary-paired-suite/1",
        "entrypoint": "visible_electron_windows_uia",
        "created_at": _utc_now(),
        "status": "planned",
        "task": CANARY_TASK,
        "runtime_budget": dict(FROZEN_RUNTIME_BUDGET),
        "mutex_name": MUTEX_NAME,
        "matrix": [
            {
                **dict(spec),
                "runtime_order": list(spec["runtime_order"]),
            }
            for spec in CANARY_MATRIX
        ],
        "pairs": [
            {
                "case": spec["case"],
                "family": spec["family"],
                "scenario": spec["scenario"],
                "seed": spec["seed"],
                "runtime_order": list(spec["runtime_order"]),
                "status": "unrun",
                "runs": [
                    {"runtime_version": runtime, "status": "unrun"}
                    for runtime in spec["runtime_order"]
                ],
            }
            for spec in CANARY_MATRIX
        ],
        "errors": [],
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
        raise RuntimeError(f"expected JSON object from {url}")
    return value


def _wait_json(url: str, predicate: Any, *, timeout: float) -> dict[str, Any]:
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
    raise RuntimeError(f"timed out waiting for fixed canary state; last={last}")


def _settled_external_state(
    url: str,
    *,
    timeout: float = 5.0,
    stable_seconds: float = ORACLE_SETTLE_SECONDS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    last_count = -1
    stable_since: float | None = None
    terminal_seen = False
    polls = 0
    while time.monotonic() < deadline:
        polls += 1
        last = _json_get(url)
        events = last.get("events")
        if not isinstance(events, list):
            events = []
        now = time.monotonic()
        count = len(events)
        if count != last_count:
            last_count = count
            stable_since = now
        terminal_seen = any(
            isinstance(item, dict)
            and item.get("type") in {"scenario_success", "wrong_input"}
            for item in events
        )
        if (
            terminal_seen
            and stable_since is not None
            and now - stable_since >= stable_seconds
        ):
            return last, {
                "passed": True,
                "terminal_seen": True,
                "event_count": count,
                "stable_seconds": round(now - stable_since, 3),
                "required_stable_seconds": stable_seconds,
                "polls": polls,
            }
        time.sleep(0.1)
    return last, {
        "passed": False,
        "terminal_seen": terminal_seen,
        "event_count": last_count,
        "stable_seconds": (
            round(time.monotonic() - stable_since, 3)
            if stable_since is not None
            else 0.0
        ),
        "required_stable_seconds": stable_seconds,
        "polls": polls,
        "errors": ["external event stream did not reach a stable terminal snapshot"],
    }


def _browser_executable() -> Path:
    candidates = (
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError("the closed canary requires an installed Chrome or Edge")


def _find_window(title: str, *, timeout: float = 25.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        matches: list[int] = []

        def collect(hwnd: int, _extra: Any) -> bool:
            if (
                win32gui.IsWindowVisible(hwnd)
                and win32gui.GetWindowText(hwnd) == title
            ):
                matches.append(int(hwnd))
            return True

        win32gui.EnumWindows(collect, None)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(f"target title is not unique: {title!r}")
        time.sleep(0.15)
    raise RuntimeError(f"target window did not appear: {title!r}")


def _focus_window(hwnd: int) -> dict[str, Any]:
    errors: list[str] = []
    for attempt in range(1, 6):
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
            win32gui.SetForegroundWindow(hwnd)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            win32api.keybd_event(
                win32con.VK_MENU,
                0,
                win32con.KEYEVENTF_KEYUP,
                0,
            )
        time.sleep(0.15)
        if int(win32gui.GetForegroundWindow() or 0) == int(hwnd):
            return {"focused": True, "attempt": attempt, "errors": errors}
    raise RuntimeError(f"could not focus exact target HWND {hwnd}; errors={errors}")


def _window_identity(hwnd: int) -> dict[str, Any]:
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise RuntimeError("target HWND is no longer live")
    _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
    process = psutil.Process(int(process_id))
    root_hwnd = int(win32gui.GetAncestor(hwnd, win32con.GA_ROOT) or hwnd)
    return {
        "hwnd": int(hwnd),
        "root_hwnd": root_hwnd,
        "title": win32gui.GetWindowText(hwnd),
        "class_name": win32gui.GetClassName(hwnd),
        "process_id": int(process_id),
        "executable": str(Path(process.exe()).resolve()),
        "rect": list(map(int, win32gui.GetWindowRect(hwnd))),
    }


def _capture_window(hwnd: int, path: Path) -> str:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    path.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(
        bbox=(left, top, right, bottom),
        all_screens=True,
    ).save(path)
    return str(path)


def _process_tree_identities(
    process: subprocess.Popen[Any] | None,
) -> list[dict[str, Any]]:
    if process is None:
        return []
    try:
        root = psutil.Process(process.pid)
        processes = [root, *root.children(recursive=True)]
    except psutil.Error:
        return []
    identities: dict[int, dict[str, Any]] = {}
    for item in processes:
        try:
            identities[item.pid] = {
                "pid": int(item.pid),
                "create_time": float(item.create_time()),
            }
        except psutil.Error:
            continue
    return [identities[pid] for pid in sorted(identities)]


def _terminate_tree(
    process: subprocess.Popen[Any] | None,
    *,
    known_identities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    expected = {
        int(item["pid"]): float(item["create_time"])
        for item in (known_identities or [])
        if type(item) is dict
        and type(item.get("pid")) is int
        and isinstance(item.get("create_time"), (int, float))
    }
    for item in _process_tree_identities(process):
        expected[int(item["pid"])] = float(item["create_time"])
    errors: list[str] = []

    def live_expected() -> list[psutil.Process]:
        live: list[psutil.Process] = []
        for pid, create_time in expected.items():
            try:
                candidate = psutil.Process(pid)
                if abs(float(candidate.create_time()) - create_time) <= 0.001:
                    live.append(candidate)
            except psutil.NoSuchProcess:
                continue
            except psutil.Error as exc:
                errors.append(f"pid {pid} inspection failed: {type(exc).__name__}: {exc}")
        return live

    initial = live_expected()
    for item in reversed(initial):
        try:
            item.terminate()
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as exc:
            errors.append(
                f"pid {item.pid} terminate failed: {type(exc).__name__}: {exc}"
            )
    if initial:
        _gone, alive = psutil.wait_procs(initial, timeout=4)
    else:
        alive = []
    for item in alive:
        try:
            item.kill()
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as exc:
            errors.append(f"pid {item.pid} kill failed: {type(exc).__name__}: {exc}")
    if alive:
        psutil.wait_procs(alive, timeout=4)
    remaining = sorted(item.pid for item in live_expected())
    return {
        "passed": not errors and not remaining,
        "expected": [
            {"pid": pid, "create_time": expected[pid]}
            for pid in sorted(expected)
        ],
        "remaining_pids": remaining,
        "errors": errors,
    }


def _remove_profile(path: Path) -> dict[str, Any]:
    errors: list[str] = []
    try:
        if path.exists():
            shutil.rmtree(path)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    remains = path.exists()
    if remains:
        errors.append("temporary browser profile still exists after cleanup")
    return {
        "passed": not errors and not remains,
        "path": str(path),
        "exists_after_cleanup": remains,
        "errors": errors,
    }


def _config_request(
    driver: client_harness.ElectronClientDriver,
    *,
    method: str = "GET",
    patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = (
        json.dumps(patch, ensure_ascii=False).encode("utf-8")
        if patch is not None
        else None
    )
    request = urllib.request.Request(
        f"{driver._runtime_url}/api/config",
        headers={"Authorization": f"Bearer {driver._runtime_token}"},
        data=data,
        method=method,
    )
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=3) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("runtime config response is not an object")
    return value


def _authenticated_config(
    driver: client_harness.ElectronClientDriver,
) -> dict[str, Any]:
    value = _config_request(driver)
    api = value.get("api") if isinstance(value.get("api"), dict) else {}
    vlm = value.get("vlm") if isinstance(value.get("vlm"), dict) else {}
    agent = value.get("agent") if isinstance(value.get("agent"), dict) else {}
    vnext = value.get("vnext") if isinstance(value.get("vnext"), dict) else {}
    api_model = str(api.get("model") or "").strip()
    vlm_model = str(vlm.get("model") or "").strip()
    if not api_model or api_model != vlm_model:
        raise RuntimeError(
            f"paired canary requires one identical API/VLM model: "
            f"api={api_model!r}, vlm={vlm_model!r}"
        )
    if str(agent.get("escalation_model") or "").strip():
        raise RuntimeError("paired canary forbids a legacy escalation model")
    done_judge_model = str(api.get("done_judge_model") or "").strip()
    if done_judge_model and done_judge_model != api_model:
        raise RuntimeError("paired canary requires the done judge to use the same model")
    if agent.get("llm_trace") is not True:
        raise RuntimeError("paired canary requires agent.llm_trace=true")
    if api.get("max_retries") != 0:
        raise RuntimeError("paired canary requires api.max_retries=0")
    if vnext.get("live_canary_enabled") is not True:
        raise RuntimeError("vnext.live_canary_enabled must be explicitly true")
    observed_budget = {
        name: vnext.get(name)
        for name in FROZEN_RUNTIME_BUDGET
    }
    if observed_budget != FROZEN_RUNTIME_BUDGET:
        raise RuntimeError(
            "vNext runtime budget is not the fixed paired-canary budget: "
            f"{observed_budget!r}"
        )
    frozen = {
        "model": api_model,
        "api_provider": str(api.get("provider") or ""),
        "api_base_url": str(api.get("base_url") or ""),
        "api_max_retries": 0,
        "vlm_provider": str(vlm.get("provider") or ""),
        "vlm_base_url": str(vlm.get("base_url") or ""),
        "live_canary_enabled": True,
        "runtime_budget": dict(FROZEN_RUNTIME_BUDGET),
    }
    frozen["sha256"] = hashlib.sha256(
        json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return frozen


def _enable_fixed_canary(
    driver: client_harness.ElectronClientDriver,
) -> dict[str, Any]:
    before = _config_request(driver)
    original = {
        "vnext": (
            dict(before.get("vnext"))
            if isinstance(before.get("vnext"), dict)
            else {}
        ),
        "api": {
            "max_retries": (
                before.get("api", {}).get("max_retries", 2)
                if isinstance(before.get("api"), dict)
                else 2
            ),
        },
    }
    try:
        _config_request(
            driver,
            method="PATCH",
            patch={
                "vnext": {
                    "live_canary_enabled": True,
                    **FROZEN_RUNTIME_BUDGET,
                },
                "api": {"max_retries": 0},
            },
        )
    except Exception as exc:
        try:
            restored = _restore_canary_config(driver, original)
        except Exception as restore_exc:
            raise RuntimeError(
                "canary enable failed and original config could not be restored: "
                f"enable={type(exc).__name__}: {exc}; "
                f"restore={type(restore_exc).__name__}: {restore_exc}"
            ) from exc
        if restored.get("passed") is not True:
            raise RuntimeError(
                "canary enable failed and config restoration was not exact: "
                f"{restored!r}"
            ) from exc
        raise
    return original


def _restore_canary_config(
    driver: client_harness.ElectronClientDriver,
    original: dict[str, Any],
) -> dict[str, Any]:
    original_vnext = (
        original.get("vnext")
        if isinstance(original.get("vnext"), dict)
        else {}
    )
    original_api = (
        original.get("api")
        if isinstance(original.get("api"), dict)
        else {}
    )
    restored = {
        "live_canary_enabled": (
            original_vnext.get("live_canary_enabled") is True
        ),
        **{
            name: original_vnext.get(name, value)
            for name, value in FROZEN_RUNTIME_BUDGET.items()
        },
    }
    restored_api = {
        "max_retries": original_api.get("max_retries", 2),
    }
    _config_request(
        driver,
        method="PATCH",
        patch={"vnext": restored, "api": restored_api},
    )
    observed = _config_request(driver)
    current = (
        observed.get("vnext")
        if isinstance(observed.get("vnext"), dict)
        else {}
    )
    current_api = (
        observed.get("api")
        if isinstance(observed.get("api"), dict)
        else {}
    )
    return {
        "passed": (
            all(current.get(key) == value for key, value in restored.items())
            and current_api.get("max_retries") == restored_api["max_retries"]
        ),
        "restored": restored,
        "restored_api": restored_api,
        "observed": {
            key: current.get(key)
            for key in restored
        },
        "observed_api": {
            "max_retries": current_api.get("max_retries"),
        },
    }


def _load_trace_events(client_result: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    path = Path(str((client_result.get("trace_binding") or {}).get("trace_file") or ""))
    if not path.is_file():
        return [], str(path)
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            events.append(value)
    return events, str(path)


def _event_model(event: dict[str, Any]) -> str:
    direct = str(event.get("model") or "").strip()
    if direct:
        return direct
    request = event.get("request")
    if isinstance(request, dict):
        return str(request.get("model") or "").strip()
    return ""


def _forensic_json(directory: Path, pattern: str) -> tuple[list[dict[str, Any]], list[str]]:
    values: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in sorted(directory.glob(pattern)):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("payload is not an object")
            values.append(value)
        except Exception as exc:
            errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
    return values, errors


def _receipt_value(event: dict[str, Any], key: str) -> Any:
    metadata = event.get("metadata")
    if isinstance(metadata, dict) and key in metadata:
        return metadata.get(key)
    if key in event:
        return event.get(key)
    receipt = event.get("receipt")
    if isinstance(receipt, dict) and key in receipt:
        return receipt.get(key)
    envelope = event.get("envelope")
    if isinstance(envelope, dict):
        if key in envelope:
            return envelope.get(key)
        proposal = envelope.get("proposal")
        if isinstance(proposal, dict) and key in proposal:
            return proposal.get(key)
    return None


def _expected_target_trace(identity: dict[str, Any]) -> dict[str, Any]:
    rect = identity.get("rect")
    if (
        type(rect) is not list
        or len(rect) != 4
        or any(type(item) is not int for item in rect)
    ):
        raise ValueError("target identity has no exact Win32 rectangle")
    executable = os.path.normcase(
        os.path.normpath(str(identity.get("executable") or ""))
    )
    return {
        "hwnd": int(identity.get("hwnd") or 0),
        "root_hwnd": int(identity.get("root_hwnd") or 0),
        "process_id": int(identity.get("process_id") or 0),
        "class_name": str(identity.get("class_name") or ""),
        "region": [rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]],
        "executable_identity_sha256": hashlib.sha256(
            executable.encode("utf-8")
        ).hexdigest(),
    }


def _trace_audit(
    client_result: dict[str, Any],
    *,
    runtime_version: str,
    expected_model: str,
    target_identity: dict[str, Any],
    family: str,
) -> dict[str, Any]:
    events, trace_file = _load_trace_events(client_result)
    errors: list[str] = []
    visible_result = (
        client_result.get("result")
        if isinstance(client_result.get("result"), dict)
        else {}
    )
    visible_run_id = str(visible_result.get("run_id") or "")
    visible_runtime = (
        visible_result.get("runtime")
        if isinstance(visible_result.get("runtime"), dict)
        else {}
    )
    visible_projection = (
        visible_runtime.get("current_run")
        if isinstance(visible_runtime.get("current_run"), dict)
        else {}
    )
    expected_target = _expected_target_trace(target_identity)
    target_hwnd = int(expected_target["hwnd"])
    starts = [
        item for item in events
        if item.get("type") == "arp_run_start"
        and (
            str(item.get("graph_name") or "") == CANARY_TASK
            or str((item.get("task_contract") or {}).get("task") or "")
            == CANARY_TASK
        )
    ]
    if len(starts) != 1:
        errors.append(f"expected one exact arp_run_start, observed {len(starts)}")
        start: dict[str, Any] = {}
    else:
        start = starts[0]
        if start.get("runtime_version") != runtime_version:
            errors.append("outer arp_run_start runtime_version mismatch")
    if not visible_run_id:
        errors.append("visible client did not bind a run id")
    if runtime_version == "vnext":
        if str(start.get("run_id") or "") != visible_run_id:
            errors.append("vNext outer run id is not the visible client run id")
        if (
            visible_projection.get("runtime_version") != "vnext"
            or visible_projection.get("no_legacy_fallback") is not True
        ):
            errors.append(
                "visible vNext projection does not prove the frozen runtime "
                "and no legacy fallback"
            )
    else:
        legacy_child = [
            item for item in events
            if item.get("type") == "agent_start"
            and str(item.get("run_id") or "") == visible_run_id
            and str(item.get("task") or "") == CANARY_TASK
        ]
        if str(start.get("run_id") or "") != visible_run_id and len(legacy_child) != 1:
            errors.append("legacy outer/child run binding is not exact")

    event_types = [str(item.get("type") or "") for item in events]
    frozen: dict[str, Any] = {}
    target_binding_id = ""
    mission_id = ""
    if runtime_version == "vnext":
        forbidden = [
            event_type for event_type in event_types
            if event_type.startswith("agent_")
            or "task_stage" in event_type
            or "fallback" in event_type
        ]
        forbidden.extend(
            str(item.get("type") or "")
            for item in events
            if (
                item.get("node_kind") == "task_stage"
                or (
                    isinstance(item.get("node"), dict)
                    and item["node"].get("kind") == "task_stage"
                )
                or item.get("legacy_fallback") is True
                or item.get("fallback_to_legacy") is True
            )
        )
        if forbidden:
            errors.append(
                "vNext trace contains legacy/task-stage/fallback events: "
                f"{forbidden!r}"
            )
        frozen_events = [
            item for item in events
            if item.get("type") == "vnext_canary_start"
            and isinstance(item.get("runtime_budget"), dict)
        ]
        if len(frozen_events) != 1:
            errors.append(
                f"expected one vNext frozen budget event, observed {len(frozen_events)}"
            )
        else:
            frozen = frozen_events[0]
            mission_id = str(frozen.get("mission_id") or "")
            if frozen.get("runtime_budget") != FROZEN_RUNTIME_BUDGET:
                errors.append("vNext frozen runtime budget mismatch")
            if frozen.get("no_legacy_fallback") is not True:
                errors.append("vNext start does not prove no_legacy_fallback=true")
            if str(frozen.get("run_id") or "") != visible_run_id:
                errors.append("vNext frozen event run id mismatch")
            if frozen.get("model") != expected_model:
                errors.append("vNext frozen start model differs from paired model")
            frozen_target = (
                frozen.get("target")
                if isinstance(frozen.get("target"), dict)
                else {}
            )
            target_binding_id = str(frozen_target.get("binding_id") or "")
            if not target_binding_id:
                errors.append("vNext frozen target binding id is missing")
            for key, expected in expected_target.items():
                if frozen_target.get(key) != expected:
                    errors.append(f"vNext frozen target identity mismatch: {key}")
    else:
        unexpected = [
            event_type for event_type in event_types
            if event_type.startswith("vnext_")
        ]
        if unexpected:
            errors.append(f"legacy trace contains vNext events: {unexpected!r}")

    model_event_types = (
        {"vnext_model_call"}
        if runtime_version == "vnext"
        else {"agent_decision", "agent_model_call"}
    )
    model_events = [
        item for item in events if item.get("type") in model_event_types
    ]
    models = [_event_model(item) for item in model_events]
    if not model_events:
        errors.append(
            "no model-call trace evidence for "
            + "/".join(sorted(model_event_types))
        )
    if runtime_version == "legacy" and not any(
        item.get("type") == "agent_decision" for item in model_events
    ):
        errors.append("legacy main agent_decision model evidence is missing")
    if any(str(item.get("run_id") or "") != visible_run_id for item in model_events):
        errors.append("model-call trace contains a different run id")
    if any(model != expected_model for model in models):
        errors.append(f"model trace differs from frozen model: {models!r}")
    if len(model_events) > FROZEN_RUNTIME_BUDGET["max_model_calls"]:
        errors.append("model-call ceiling exceeded")
    forensic_summary: dict[str, Any] = {}
    if runtime_version == "vnext":
        roles = [str(item.get("model_role") or "") for item in model_events]
        if (
            any(role not in VNEXT_MODEL_ROLES for role in roles)
            or not VNEXT_MODEL_ROLES.issubset(roles)
        ):
            errors.append(f"vNext model role trace is not exact and independent: {roles!r}")
        if any(str(item.get("mission_id") or "") != mission_id for item in model_events):
            errors.append("vNext model call mission binding mismatch")
    else:
        main_events = [
            item for item in model_events if item.get("type") == "agent_decision"
        ]
        auxiliary_events = [
            item for item in model_events if item.get("type") == "agent_model_call"
        ]
        if len(main_events) > FROZEN_RUNTIME_BUDGET["max_turns"]:
            errors.append("legacy main model-call ceiling exceeded")
        if any(item.get("accounted") is not True for item in auxiliary_events):
            errors.append("legacy auxiliary model call is not accounted")
        legacy_starts = [
            item for item in events
            if item.get("type") == "agent_start"
            and str(item.get("run_id") or "") == visible_run_id
        ]
        if len(legacy_starts) != 1 or legacy_starts[0].get("max_steps") != 30:
            errors.append("legacy agent start does not prove the same 30-turn budget")
        forensic_dir = client_harness.TRACE_ROOT / visible_run_id
        requests, request_errors = _forensic_json(
            forensic_dir,
            "*_llm_request.json",
        )
        attempts, attempt_errors = _forensic_json(
            forensic_dir,
            "*_llm_request_attempt.json",
        )
        done_requests, done_errors = _forensic_json(
            forensic_dir,
            "*_done_check_request.json",
        )
        forensic_errors = [*request_errors, *attempt_errors, *done_errors]
        if not forensic_dir.is_dir():
            forensic_errors.append("legacy child forensic directory is missing")
        if len(requests) != len(main_events) or len(attempts) != len(main_events):
            forensic_errors.append(
                "legacy main request/attempt files do not match agent_decision count"
            )
        if any(
            str(item.get("run_id") or "") != visible_run_id
            or item.get("model") != expected_model
            or item.get("max_attempts") != 1
            for item in requests
        ):
            forensic_errors.append(
                "legacy request forensic model/run/max_attempts is not exact"
            )
        if any(
            str(item.get("run_id") or "") != visible_run_id
            or item.get("attempt") != 1
            or item.get("retry_kind") != "initial_request"
            for item in attempts
        ):
            forensic_errors.append(
                "legacy provider attempt forensic contains retry or foreign binding"
            )
        done_judge_events = [
            item for item in auxiliary_events
            if item.get("purpose") == "done_judge"
        ]
        if len(done_requests) != len(done_judge_events) or any(
            str(item.get("run_id") or "") != visible_run_id
            or item.get("model") != expected_model
            for item in done_requests
        ):
            forensic_errors.append(
                "legacy done-judge request forensic is not exact"
            )
        if forensic_errors:
            errors.extend(forensic_errors)
        forensic_summary = {
            "directory": str(forensic_dir),
            "main_request_count": len(requests),
            "main_provider_attempt_count": len(attempts),
            "done_judge_request_count": len(done_requests),
            "errors": forensic_errors,
        }

    receipt_summary: dict[str, Any] = {
        "count": 0,
        "controller_actions": [],
        "decision_ids": [],
        "target_binding_ids": [],
    }
    done_events: list[dict[str, Any]] = []
    turn_events: list[dict[str, Any]] = []
    if runtime_version == "vnext":
        receipts = [
            item for item in events
            if item.get("type") == "vnext_raw_receipt"
        ]
        actions = [str(_receipt_value(item, "controller_action") or "") for item in receipts]
        decisions = [str(_receipt_value(item, "decision_id") or "") for item in receipts]
        bindings = [str(_receipt_value(item, "target_binding_id") or "") for item in receipts]
        outcomes = [str(_receipt_value(item, "outcome") or "") for item in receipts]
        run_ids = [str(_receipt_value(item, "run_id") or "") for item in receipts]
        hwnds = [int(_receipt_value(item, "target_hwnd") or 0) for item in receipts]
        receipt_summary = {
            "count": len(receipts),
            "controller_actions": actions,
            "decision_ids": decisions,
            "target_binding_ids": bindings,
            "outcomes": outcomes,
            "run_ids": run_ids,
            "target_hwnds": hwnds,
        }
        if not receipts:
            errors.append("vNext raw receipt trace evidence is missing")
        if (
            any(not item for item in decisions)
            or len(decisions) != len(set(decisions))
        ):
            errors.append("vNext receipt decision ids are missing or duplicated")
        if any(item != target_binding_id for item in bindings):
            errors.append("vNext receipt target binding is not the frozen binding")
        if any(item != visible_run_id for item in run_ids):
            errors.append("vNext receipt run binding mismatch")
        if any(item != target_hwnd for item in hwnds):
            errors.append("vNext receipt target HWND mismatch")
        if any(item != "applied" for item in outcomes):
            errors.append(f"vNext receipt contains non-applied outcomes: {outcomes!r}")
        if family == "click_double":
            if (
                actions.count("click") != 1
                or actions.count("double_click") != 1
                or len(actions) != 2
            ):
                errors.append(f"click/double receipt sequence is not exact: {actions!r}")
        elif family == "scroll_roundtrip":
            if (
                len(actions) < 2
                or len(actions) > FROZEN_RUNTIME_BUDGET["max_turns"]
                or any(item != "scroll" for item in actions)
            ):
                errors.append(
                    f"scroll receipts are not a bounded scroll-only sequence: {actions!r}"
                )
        elif family == "type_hotkey":
            if (
                actions.count("click") != 1
                or actions.count("type_text") != 1
                or actions.count("hotkey") != 2
                or len(actions) != 4
            ):
                errors.append(f"type/hotkey receipt sequence is not exact: {actions!r}")

        turn_events = [
            item for item in events if item.get("type") == "vnext_canary_turn"
        ]
        turn_indices = [item.get("turn_index") for item in turn_events]
        if turn_indices != list(range(len(turn_events))):
            errors.append(f"vNext turn indices are not contiguous: {turn_indices!r}")
        if any(
            str(item.get("run_id") or "") != visible_run_id
            or str(item.get("mission_id") or "") != mission_id
            or item.get("no_legacy_fallback") is not True
            for item in turn_events
        ):
            errors.append("vNext turn run/mission/fallback binding mismatch")
        done_events = [
            item for item in events if item.get("type") == "vnext_canary_done"
        ]
        if len(done_events) != 1:
            errors.append(
                f"expected one vNext terminal event, observed {len(done_events)}"
            )
        else:
            done = done_events[0]
            if (
                str(done.get("run_id") or "") != visible_run_id
                or str(done.get("mission_id") or "") != mission_id
                or done.get("runtime_version") != "vnext"
                or done.get("runtime_budget") != FROZEN_RUNTIME_BUDGET
                or done.get("no_legacy_fallback") is not True
                or done.get("status") != "mission_verified"
                or done.get("target_binding_id") != target_binding_id
                or done.get("turns_used") != len(turn_events)
                or done.get("model_calls") != len(model_events)
            ):
                errors.append("vNext terminal status/budget/count/binding is not exact")

    return {
        "passed": not errors,
        "trace_file": trace_file,
        "trace_event_count": len(events),
        "run_id": visible_run_id,
        "runtime_version": runtime_version,
        "outer_start_count": len(starts),
        "model_event_types": sorted(model_event_types),
        "model_call_count": len(model_events),
        "models": models,
        "forensic_summary": forensic_summary,
        "target_expectation": expected_target,
        "target_binding_id": target_binding_id,
        "turn_count": len(turn_events),
        "done_count": len(done_events),
        "receipt_summary": receipt_summary,
        "errors": errors,
    }


def _identity_audit(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    case_id: str,
) -> dict[str, Any]:
    errors: list[str] = []
    expected_title = f"WinPilot vNext Canary {case_id}"
    if before.get("title") != expected_title:
        errors.append("target title does not bind the unique case id")
    for key in (
        "hwnd",
        "root_hwnd",
        "title",
        "class_name",
        "process_id",
        "executable",
        "rect",
    ):
        if before.get(key) != after.get(key):
            errors.append(f"target identity changed during run: {key}")
    return {"passed": not errors, "before": before, "after": after, "errors": errors}


def _status_for_result(result: dict[str, Any]) -> str:
    outcome = str(((result.get("client_result") or {}).get("result") or {}).get("outcome") or "")
    if outcome == "timeout":
        return "timeout"
    if (
        not (result.get("target_identity_audit") or {}).get("passed")
        or not (result.get("trace_audit") or {}).get("passed")
        or not (result.get("client_result") or {}).get("execution_valid")
    ):
        return "invalid"
    if (
        not (result.get("external_oracle") or {}).get("passed")
        or not (result.get("client_result") or {}).get("passed")
    ):
        return "failed"
    return "passed"


def run_runtime_case(
    spec: dict[str, Any],
    runtime_version: str,
    *,
    output_root: Path,
    frozen_config: dict[str, Any],
    expected_client_identity: client_harness.ClientIdentity,
) -> dict[str, Any]:
    if runtime_version not in RUNTIMES:
        raise ValueError(f"unsupported runtime {runtime_version!r}")
    case_id = (
        f"{spec['case']}-{runtime_version}-{uuid.uuid4().hex[:10]}"
    )
    scenario = str(spec["scenario"])
    seed = int(spec["seed"])
    page_id = f"vnext-canary:{case_id}:{scenario}:{seed}"
    case_dir = output_root / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    report_path = case_dir / "report.json"
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    state_query = urllib.parse.urlencode({
        "case": case_id,
        "scenario": scenario,
        "seed": seed,
        "page": page_id,
    })
    state_url = f"{base}/state?{state_query}"
    page_query = urllib.parse.urlencode({
        "case": case_id,
        "scenario": scenario,
        "seed": seed,
    })
    target_url = f"{base}/vnext_canary_target.html?{page_query}"
    profile_dir = Path(tempfile.mkdtemp(prefix=f"winpilot-vnext-canary-{case_id}-"))
    server_process: subprocess.Popen[Any] | None = None
    browser_process: subprocess.Popen[Any] | None = None
    target_hwnd = 0
    external_state: dict[str, Any] = {}
    external_verdict: dict[str, Any] = {}
    external_settlement: dict[str, Any] = {}
    server_identities: list[dict[str, Any]] = []
    browser_identities: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "format": "vnext-live-canary-run/1",
        "case": spec["case"],
        "case_id": case_id,
        "page_id": page_id,
        "family": spec["family"],
        "scenario": scenario,
        "seed": seed,
        "runtime_version": runtime_version,
        "task": CANARY_TASK,
        "runtime_budget": dict(FROZEN_RUNTIME_BUDGET),
        "frozen_config_sha256": frozen_config["sha256"],
        "started_at": _utc_now(),
        "status": "running",
    }
    _write_json(report_path, result)
    try:
        server_process = subprocess.Popen(
            [sys.executable, "-u", str(TARGET_SERVER), str(port)],
            cwd=str(TARGET_SERVER.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        server_identities = _process_tree_identities(server_process)
        _wait_json(f"{base}/state", lambda value: value.get("ready") is True, timeout=12)
        server_identities = _process_tree_identities(server_process)
        browser = _browser_executable()
        result["browser_executable"] = str(browser)
        browser_process = subprocess.Popen(
            [
                str(browser),
                f"--app={target_url}",
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
        browser_identities = _process_tree_identities(browser_process)
        title = f"WinPilot vNext Canary {case_id}"
        target_hwnd = _find_window(title)
        ready = _wait_json(
            state_url,
            lambda value: any(
                item.get("type") == "page_ready"
                for item in value.get("events") or []
            ),
            timeout=12,
        )
        browser_identities = _process_tree_identities(browser_process)
        result["target_ready"] = ready
        target_before = _window_identity(target_hwnd)
        result["target_before"] = target_before
        result["screenshots"] = {
            "target_before": _capture_window(
                target_hwnd,
                case_dir / "target-before.png",
            ),
        }

        def handoff(
            driver: client_harness.ElectronClientDriver,
            _client_result: dict[str, Any],
        ) -> dict[str, Any]:
            observed_config = _authenticated_config(driver)
            if observed_config.get("sha256") != frozen_config.get("sha256"):
                raise RuntimeError("paired canary config changed after suite preflight")
            evidence = _focus_window(target_hwnd)
            evidence["identity"] = _window_identity(target_hwnd)
            evidence["screenshot"] = _capture_window(
                target_hwnd,
                driver.output_dir / "target-after-submit.png",
            )
            return evidence

        def verify_external(_client_result: dict[str, Any]) -> dict[str, Any]:
            nonlocal external_state, external_settlement, external_verdict
            external_state, external_settlement = _settled_external_state(state_url)
            oracle = external_state.get("oracle")
            if not isinstance(oracle, dict):
                external_verdict = {
                    "source": "independent_vnext_canary_browser_oracle",
                    "passed": False,
                    "errors": ["oracle response is missing"],
                }
                return external_verdict
            external_verdict = dict(oracle)
            external_verdict["source"] = (
                "independent_vnext_canary_browser_oracle"
            )
            if external_settlement.get("passed") is not True:
                external_verdict["passed"] = False
                external_verdict.setdefault("errors", []).extend(
                    external_settlement.get("errors") or []
                )
            return external_verdict

        client_result = client_harness.run_case(
            f"{spec['case']}-{runtime_version}",
            {
                "task": CANARY_TASK,
                "max_steps": FROZEN_RUNTIME_BUDGET["max_turns"],
                "timeout": FROZEN_RUNTIME_BUDGET["max_runtime_s"],
                "runtime_version": runtime_version,
            },
            output_root=case_dir / "client",
            reset_session=True,
            after_submit=handoff,
            independent_verifier=verify_external,
            expected_client_identity=expected_client_identity,
        )
        result["client_result"] = client_result
        if not external_state:
            external_state = _json_get(state_url)
        result["external_state"] = external_state
        result["external_settlement"] = external_settlement
        result["external_oracle"] = dict(
            external_verdict or external_state.get("oracle") or {}
        )
        if target_hwnd and win32gui.IsWindow(target_hwnd):
            result["screenshots"]["target_after"] = _capture_window(
                target_hwnd,
                case_dir / "target-after.png",
            )
            target_after = _window_identity(target_hwnd)
        else:
            target_after = {}
        result["target_identity_audit"] = _identity_audit(
            target_before,
            target_after,
            case_id=case_id,
        )
        result["trace_audit"] = _trace_audit(
            client_result,
            runtime_version=runtime_version,
            expected_model=str(frozen_config["model"]),
            target_identity=target_before,
            family=str(spec["family"]),
        )
        result["status"] = _status_for_result(result)
        result["passed"] = result["status"] == "passed"
    except Exception as exc:
        result["runner_error"] = f"{type(exc).__name__}: {exc}"
        result["status"] = "invalid"
        result["passed"] = False
        try:
            if state_url:
                result["external_state"] = _json_get(state_url)
                result["external_oracle"] = dict(
                    result["external_state"].get("oracle") or {}
                )
        except Exception as state_exc:
            result["oracle_collection_error"] = (
                f"{type(state_exc).__name__}: {state_exc}"
            )
    finally:
        cleanup_errors: list[str] = []
        try:
            if target_hwnd and win32gui.IsWindow(target_hwnd):
                win32gui.PostMessage(target_hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception as close_exc:
            result["target_close_error"] = f"{type(close_exc).__name__}: {close_exc}"
            cleanup_errors.append(result["target_close_error"])
        try:
            browser_cleanup = _terminate_tree(
                browser_process,
                known_identities=browser_identities,
            )
        except Exception as cleanup_exc:
            browser_cleanup = {
                "passed": False,
                "errors": [
                    f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                ],
            }
        try:
            server_cleanup = _terminate_tree(
                server_process,
                known_identities=server_identities,
            )
        except Exception as cleanup_exc:
            server_cleanup = {
                "passed": False,
                "errors": [
                    f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                ],
            }
        profile_cleanup = _remove_profile(profile_dir)
        target_still_live = bool(
            target_hwnd and win32gui.IsWindow(target_hwnd)
        )
        if target_still_live:
            cleanup_errors.append("target HWND remains live after browser cleanup")
        for evidence in (browser_cleanup, server_cleanup, profile_cleanup):
            if evidence.get("passed") is not True:
                cleanup_errors.extend(evidence.get("errors") or [])
        result["cleanup"] = {
            "passed": not cleanup_errors,
            "browser": browser_cleanup,
            "server": server_cleanup,
            "profile": profile_cleanup,
            "target_hwnd_live": target_still_live,
            "errors": cleanup_errors,
        }
        if result["cleanup"]["passed"] is not True:
            result["status"] = "invalid"
            result["passed"] = False
        result["finished_at"] = _utc_now()
        result["report"] = str(report_path)
        _write_json(report_path, result)
    return result


def run_suite() -> dict[str, Any]:
    with DesktopSuiteMutex():
        output_root = DEFAULT_OUTPUT.resolve()
        ledger_path = CAMPAIGN_LEDGER.resolve()
        if ledger_path.is_file():
            try:
                existing = json.loads(ledger_path.read_text(encoding="utf-8"))
            except Exception as exc:
                existing = {"read_error": f"{type(exc).__name__}: {exc}"}
            return {
                "format": "vnext-live-canary-paired-suite/1",
                "status": "invalid",
                "passed": False,
                "ledger": str(ledger_path),
                "existing_campaign": existing,
                "errors": [
                    "the fixed paired campaign was already claimed; reruns are forbidden"
                ],
            }

        campaign_id = (
            datetime.now().strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid.uuid4().hex
        )
        suite_dir = output_root / f"{campaign_id}-paired-suite"
        manifest_path = suite_dir / "manifest.json"
        ledger: dict[str, Any] = {
            "format": "vnext-live-canary-campaign-ledger/1",
            "campaign_id": campaign_id,
            "claimed_at": _utc_now(),
            "status": "claimed",
            "manifest": str(manifest_path),
            "matrix": [
                {
                    "case": item["case"],
                    "family": item["family"],
                    "seed": item["seed"],
                    "runtime_order": list(item["runtime_order"]),
                }
                for item in CANARY_MATRIX
            ],
        }
        # The immutable claim is created while holding the physical desktop
        # mutex and before any manifest/run artifact. A crash still consumes
        # the one allowed campaign and therefore cannot be hidden by a rerun.
        _write_json(ledger_path, ledger)

        manifest = _new_manifest()
        manifest["campaign_id"] = campaign_id
        manifest["ledger"] = str(ledger_path)
        manifest["manifest"] = str(manifest_path)
        _write_json(manifest_path, manifest)
        preflight_driver: client_harness.ElectronClientDriver | None = None
        original_config: dict[str, Any] | None = None
        try:
            manifest["status"] = "preflight"
            _write_json(manifest_path, manifest)
            preflight_driver = client_harness.ElectronClientDriver(
                suite_dir / "preflight-client"
            )
            original_config = _enable_fixed_canary(preflight_driver)
            frozen_config = _authenticated_config(preflight_driver)
            manifest["frozen_config"] = frozen_config
            manifest["client"] = {
                "pid": preflight_driver.identity.pid,
                "hwnd": preflight_driver.identity.hwnd,
                "executable": preflight_driver.identity.executable,
                "command_line": preflight_driver.identity.command_line,
                "workspace_verified": preflight_driver.identity.workspace_verified,
            }
            manifest["status"] = "running"
            _write_json(manifest_path, manifest)
            for pair_index, spec in enumerate(CANARY_MATRIX):
                pair = manifest["pairs"][pair_index]
                pair["status"] = "running"
                pair["started_at"] = _utc_now()
                _write_json(manifest_path, manifest)
                for run_index, runtime_version in enumerate(spec["runtime_order"]):
                    slot = pair["runs"][run_index]
                    slot["status"] = "running"
                    slot["started_at"] = _utc_now()
                    _write_json(manifest_path, manifest)
                    run_result: dict[str, Any] = {
                        "runtime_version": runtime_version,
                        "status": "invalid",
                        "passed": False,
                        "runner_error": "run attempt did not return a result",
                    }
                    try:
                        run_result = run_runtime_case(
                            dict(spec),
                            str(runtime_version),
                            output_root=suite_dir / "runs",
                            frozen_config=frozen_config,
                            expected_client_identity=preflight_driver.identity,
                        )
                    except Exception as exc:
                        run_result = {
                            "runtime_version": runtime_version,
                            "status": "invalid",
                            "passed": False,
                            "runner_error": f"{type(exc).__name__}: {exc}",
                            "finished_at": _utc_now(),
                        }
                    finally:
                        # The slot is always replaced exactly once. No failed
                        # member is retried and no second sample can hide it.
                        slot.update(run_result)
                        _write_json(manifest_path, manifest)
                pair["passed"] = all(
                    item.get("status") == "passed" for item in pair["runs"]
                )
                pair["status"] = "passed" if pair["passed"] else "failed"
                pair["finished_at"] = _utc_now()
                _write_json(manifest_path, manifest)
                if not pair["passed"]:
                    manifest["stopped_after_pair"] = pair["case"]
                    break
            completed_pairs = [
                pair for pair in manifest["pairs"]
                if pair.get("status") in {"passed", "failed"}
            ]
            manifest["passed"] = (
                len(completed_pairs) == len(CANARY_MATRIX)
                and all(pair.get("passed") is True for pair in completed_pairs)
            )
            manifest["status"] = "passed" if manifest["passed"] else "failed"
        except Exception as exc:
            manifest["errors"].append(f"{type(exc).__name__}: {exc}")
            manifest["passed"] = False
            manifest["status"] = "invalid"
        finally:
            if preflight_driver is not None and original_config is not None:
                try:
                    restore = _restore_canary_config(
                        preflight_driver,
                        original_config,
                    )
                except Exception as exc:
                    restore = {
                        "passed": False,
                        "errors": [f"{type(exc).__name__}: {exc}"],
                    }
                manifest["config_restore"] = restore
                if restore.get("passed") is not True:
                    manifest["errors"].append(
                        "paired canary configuration restoration failed"
                    )
                    manifest["passed"] = False
                    manifest["status"] = "invalid"
            manifest["finished_at"] = _utc_now()
            _write_json(manifest_path, manifest)
            ledger.update(
                {
                    "status": manifest["status"],
                    "passed": manifest.get("passed") is True,
                    "finished_at": manifest["finished_at"],
                }
            )
            try:
                _write_json(ledger_path, ledger)
            except Exception as exc:
                manifest["errors"].append(
                    f"campaign ledger finalization failed: {type(exc).__name__}: {exc}"
                )
                manifest["passed"] = False
                manifest["status"] = "invalid"
                _write_json(manifest_path, manifest)
        return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    return parser.parse_args()


def main() -> int:
    parse_args()
    result = run_suite()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
