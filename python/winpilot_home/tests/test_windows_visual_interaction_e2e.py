"""Opt-in, real Windows E2E for a generic visual drag interaction.

The test uses the same compile and ARP run HTTP endpoints as the native
frontend.  It does not mock the model, screenshot pipeline, Controller,
browser, or pointer input.  A localhost evaluation target records trusted
browser pointer events as external ground truth.

It is intentionally excluded from normal test runs.  Set
``WINPILOT_REAL_VISUAL_E2E=1`` only on an unlocked interactive Windows desktop.
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


pytestmark = [pytest.mark.real, pytest.mark.slow]

ROOT = Path(__file__).resolve().parents[1]
TARGET_SERVER = ROOT / "eval_targets" / "visual_interaction_server.py"
REPORT_ROOT = ROOT.parents[1] / ".e2e" / "real_visual_interaction"
TERMINAL = {"done", "failed", "canceled"}


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("WINPILOT_E2E_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request(method: str, url: str, payload: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, method=method, headers=_headers())
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"{method} {url} returned HTTP {exc.code}: {detail}") from exc


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_json(url: str, predicate, timeout_s: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            value = _request("GET", url, timeout=2.0)
            if isinstance(value, dict):
                last = value
                if predicate(last):
                    return last
        except Exception:
            pass
        time.sleep(0.15)
    raise AssertionError(f"timed out waiting for {url}; last={last}")


def _browser_executable() -> str:
    explicit = os.environ.get("WINPILOT_REAL_VISUAL_BROWSER", "").strip()
    candidates = [
        explicit,
        shutil.which("msedge") or "",
        shutil.which("chrome") or "",
        str(Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe"),
        str(Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe"),
        str(Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    pytest.skip("Edge or Chrome is required (or set WINPILOT_REAL_VISUAL_BROWSER)")


def _find_window(title_fragment: str, timeout_s: float = 20.0) -> int:
    try:
        import win32con
        import win32gui
    except ImportError:
        pytest.skip("pywin32 is required for the real Windows visual E2E")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        matches: list[int] = []

        def visit(hwnd: int, _unused: Any) -> None:
            if win32gui.IsWindowVisible(hwnd) and title_fragment in win32gui.GetWindowText(hwnd):
                matches.append(hwnd)

        win32gui.EnumWindows(visit, None)
        if matches:
            hwnd = int(matches[-1])
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(hwnd)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
            time.sleep(0.35)
            if int(win32gui.GetForegroundWindow() or 0) == hwnd:
                return hwnd
            # Windows may reject SetForegroundWindow when pytest was launched
            # from a background terminal. Exercise the same generic ALT-unlock
            # focus path used by the real Controller before declaring the E2E
            # environment unavailable.
            try:
                from winpilot.action.executor import _raw_focus_window

                focused = _raw_focus_window(hwnd)
                if focused.get("ok") is True and int(win32gui.GetForegroundWindow() or 0) == hwnd:
                    return hwnd
            except Exception:
                pass
        time.sleep(0.2)
    raise AssertionError(f"visible foreground window containing {title_fragment!r} was not found")


def _close_window(hwnd: int) -> None:
    if not hwnd:
        return
    try:
        import win32con
        import win32gui

        if win32gui.IsWindow(hwnd):
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    except Exception:
        pass


def _poll_run(base: str, run_id: str, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        payload = _request("GET", f"{base}/api/v2/arp/runs/{run_id}")
        last = payload.get("run") if isinstance(payload, dict) else {}
        if str(last.get("status") or "") in TERMINAL:
            return last
        time.sleep(0.5)
    try:
        _request("POST", f"{base}/api/v2/arp/runs/{run_id}/cancel", {})
    finally:
        raise AssertionError(f"ARP run {run_id} did not finish within {timeout_s}s; last={last}")


def _assert_runtime_is_current(status: dict[str, Any]) -> None:
    runtime = status.get("runtime") or {}
    assert int(runtime.get("contract_version") or 0) == 2, runtime
    assert Path(str(runtime.get("repo_root") or "")).resolve() == ROOT.parents[1].resolve(), runtime
    source = runtime.get("source") or {}
    assert source.get("loaded_stamp"), source
    assert source.get("loaded_stamp") == source.get("current_stamp"), source
    assert source.get("drifted") is False, source
    git = runtime.get("git") or {}
    loaded = str(git.get("loaded_commit") or "")
    current = str(git.get("current_commit") or "")
    assert not loaded or not current or loaded == current, git
    assert status.get("vlm_configured") is True, status.get("vlm_reason")


def _trace_action_types(events: list[dict[str, Any]]) -> list[str]:
    action_types: list[str] = []
    for event in events:
        if event.get("type") != "agent_batch_action" or event.get("ok") is not True:
            continue
        envelope = event.get("action_envelope") or {}
        name = str(envelope.get("type") or event.get("tool") or "")
        if name:
            action_types.append(name)
    return action_types


def test_real_frontend_api_generic_visual_drag() -> None:
    if sys.platform != "win32":
        pytest.skip("real visual interaction E2E requires Windows")
    if os.environ.get("WINPILOT_REAL_VISUAL_E2E") != "1":
        pytest.skip("set WINPILOT_REAL_VISUAL_E2E=1 to authorize real mouse input")
    # A locked desktop produces misleading screenshots and synthetic failures.
    assert ctypes.windll.user32.GetForegroundWindow(), "interactive Windows desktop is not available"

    base = os.environ.get("WINPILOT_E2E_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
    status = _request("GET", f"{base}/api/status")
    _assert_runtime_is_current(status)
    assert status.get("running") is False, "real visual E2E requires an idle interactive runtime"

    port = _free_port()
    case_id = uuid.uuid4().hex[:10]
    target_base = f"http://127.0.0.1:{port}"
    target_process = subprocess.Popen(
        [sys.executable, "-u", str(TARGET_SERVER), str(port)],
        cwd=str(TARGET_SERVER.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    browser_hwnd = 0
    browser_process: subprocess.Popen | None = None
    profile_dir: Path | None = None
    started_at = time.time()
    try:
        _wait_json(f"{target_base}/state", lambda value: value.get("ready") is True)
        url = f"{target_base}/visual_interaction_target.html?case={case_id}"
        # A unique profile prevents an already-running browser from silently
        # ignoring launch flags or reusing a stale per-site zoom/extension
        # state. The evaluation target must be visually deterministic before
        # it can judge the agent, while the agent still explores it cold.
        profile_dir = Path(tempfile.mkdtemp(prefix=f"winpilot-visual-{case_id}-"))
        browser_process = subprocess.Popen(
            [
                _browser_executable(),
                f"--app={url}",
                "--new-window",
                "--window-size=1100,760",
                "--window-position=80,60",
                "--force-device-scale-factor=1",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
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
        browser_hwnd = _find_window(title)
        ready_state = _wait_json(
            f"{target_base}/state",
            lambda value: any(
                item.get("type") == "page_ready"
                and int(item.get("viewport_width") or 0) >= 760
                and int(item.get("viewport_height") or 0) >= 560
                and 0 <= int(item.get("token_left") or -1)
                and int(item.get("token_right") or 0) > int(item.get("token_left") or 0)
                and int(item.get("token_right") or 0) <= int(item.get("viewport_width") or 0)
                and 0 <= int(item.get("target_left") or -1)
                and int(item.get("target_right") or 0) > int(item.get("target_left") or 0)
                and int(item.get("target_right") or 0) <= int(item.get("viewport_width") or 0)
                and 0 <= int(item.get("token_top") or -1)
                and int(item.get("token_bottom") or 0) <= int(item.get("viewport_height") or 0)
                and 0 <= int(item.get("target_top") or -1)
                and int(item.get("target_bottom") or 0) <= int(item.get("viewport_height") or 0)
                for item in value.get("events") or []
            ),
            timeout_s=10.0,
        )
        assert ready_state.get("success") is False

        task = (
            "In the current visible window, drag the blue circle labeled MOVE ME completely "
            "into the green outlined DROP HERE target. Finish only after the page visibly "
            "shows DRAG_SUCCEEDED."
        )
        compiled = _request("POST", f"{base}/api/v2/task-contracts/compile", {
            "task": task,
            "max_steps": 12,
            "cold_start": True,
        })
        contract = compiled["contract"]
        graph = compiled["graph"]
        interaction = (contract.get("intent") or {}).get("interaction") or {}
        assert interaction.get("active") is True, interaction
        assert interaction.get("mode") == "direct_visual_manipulation", interaction
        assert interaction.get("session_mode") == "bounded", interaction
        assert contract.get("satisfiable") is True, contract.get("missing_capabilities")
        stage_types = [stage.get("type") for stage in contract.get("stages") or []]
        assert stage_types == [
            "ObserveScene", "InferInteraction", "PerformTurn", "VerifyLocalEffect",
        ], stage_types
        node_kinds = [node.get("kind") for node in graph.get("nodes") or []]
        assert "task_stage" in node_kinds
        assert "agent_goal" not in node_kinds
        assert "skill_replay" not in node_kinds
        assert (graph.get("policy") or {}).get("allow_shell") is False

        # Reassert foreground immediately before the frontend-equivalent run request.
        assert _find_window(title, timeout_s=3.0) == browser_hwnd
        started = _request("POST", f"{base}/api/v2/arp/runs", {
            "graph": graph,
            "inputs": {"e2e_case_id": case_id},
        })
        run_id = str(started["run_id"])
        timeout_s = float(os.environ.get("WINPILOT_REAL_VISUAL_TIMEOUT_S", "240"))
        snapshot = _poll_run(base, run_id, timeout_s)
        try:
            target_state = _wait_json(
                f"{target_base}/state",
                lambda value: value.get("success") is True,
                timeout_s=5.0,
            )
        except AssertionError:
            target_state = _request("GET", f"{target_base}/state")
        events = _request("GET", f"{base}/api/trace/{run_id}")

        # Persist the trace before assertions so a real failure remains
        # inspectable instead of disappearing behind the first pytest error.
        interaction_state = snapshot.get("interaction_state") or {}
        stage_trace = snapshot.get("stage_trace") or []
        recovery_records = [
            item for item in stage_trace if item.get("status") == "recovering"
        ]
        model_calls = sum(int(item.get("model_calls") or 0) for item in stage_trace)
        model_ms = sum(int(item.get("model_elapsed_ms") or 0) for item in stage_trace)
        controller_ms = sum(int(item.get("controller_elapsed_ms") or 0) for item in stage_trace)
        capture_ms = sum(int(item.get("capture_elapsed_ms") or 0) for item in stage_trace)
        report = {
            "kind": "real_windows_visual_interaction_e2e",
            "run_id": run_id,
            "case_id": case_id,
            "task": task,
            "status": snapshot.get("status"),
            "error": snapshot.get("error"),
            "failed_stage": snapshot.get("failed_stage"),
            "duration_s": round(time.time() - started_at, 3),
            "model_calls": model_calls,
            "model_elapsed_ms": model_ms,
            "controller_elapsed_ms": controller_ms,
            "capture_elapsed_ms": capture_ms,
            "replan_count": int(interaction_state.get("replan_count") or 0),
            "recovery_count": len(recovery_records),
            "recoveries": recovery_records,
            "budget": interaction_state.get("budget") or {},
            "stage_trace": stage_trace,
            "interaction_state": interaction_state,
            "external_ground_truth": target_state,
            "successful_action_types": _trace_action_types(events),
            "runtime": status.get("runtime"),
        }
        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        (REPORT_ROOT / f"{run_id}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        assert snapshot.get("status") == "done", snapshot
        assert target_state.get("case_id") == case_id, target_state
        trusted = [item for item in target_state.get("events") or [] if item.get("trusted") is True]
        press = next(item for item in trusted if item.get("type") == "pointerdown")
        assert any(item.get("type") == "pointermove" for item in trusted), trusted
        release = next(
            item for item in reversed(trusted)
            if item.get("type") == "pointerup" and item.get("succeeded") is True
        )
        assert int(release.get("move_count") or 0) >= 2, release
        assert abs(int(release["screen_x"]) - int(press["screen_x"])) >= 250, (press, release)
        assert release["token_left"] >= release["target_left"], release
        assert release["token_top"] >= release["target_top"], release
        assert release["token_right"] <= release["target_right"], release
        assert release["token_bottom"] <= release["target_bottom"], release
        assert "drag" in _trace_action_types(events), _trace_action_types(events)

        interaction_state = snapshot.get("interaction_state") or {}
        assert int((interaction_state.get("target_binding") or {}).get("hwnd") or 0) == browser_hwnd
        assert (interaction_state.get("last_mutating_plan") or {}).get("actions"), interaction_state
        assert (interaction_state.get("last_executed_action") or {}).get("deferred") is not True
        assert (
            interaction_state.get("last_local_verification")
            or interaction_state.get("local_verification")
            or {}
        ).get("verified") is True
        assert not interaction_state.get("failure_stage"), interaction_state

        stage_trace = snapshot.get("stage_trace") or []
        traced_types = [item.get("stage_type") for item in stage_trace]
        # Safe stale-frame recovery may repeat Infer/Perform, so require the
        # contract stages as an ordered subsequence rather than pretending a
        # real E2E can never take an explicit recovery edge.
        cursor = 0
        for expected in stage_types:
            cursor = traced_types.index(expected, cursor) + 1
        assert traced_types[0] == "ObserveScene", traced_types
        assert traced_types[-1] == "VerifyLocalEffect", traced_types
        assert all(
            item.get("status") in {"done", "recovering"}
            for item in stage_trace
        ), stage_trace
        recovery_records = [
            item for item in stage_trace if item.get("status") == "recovering"
        ]
        assert all(
            item.get("failure_type")
            and (item.get("recovery") or {}).get("action")
            for item in recovery_records
        ), recovery_records
        assert stage_trace[-1].get("status") == "done", stage_trace[-1]
        assert all(item.get("satisfiable") is not False for item in stage_trace), stage_trace
        assert all(not (item.get("missing_capabilities") or []) for item in stage_trace), stage_trace
        model_calls = sum(int(item.get("model_calls") or 0) for item in stage_trace)
        model_ms = sum(int(item.get("model_elapsed_ms") or 0) for item in stage_trace)
        controller_ms = sum(int(item.get("controller_elapsed_ms") or 0) for item in stage_trace)
        capture_ms = sum(int(item.get("capture_elapsed_ms") or 0) for item in stage_trace)
        assert 1 <= model_calls <= 3, model_calls
        assert controller_ms >= 0

    finally:
        _close_window(browser_hwnd)
        if browser_process is not None and browser_process.poll() is None:
            browser_process.terminate()
            try:
                browser_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                browser_process.kill()
                browser_process.wait(timeout=5)
        target_process.terminate()
        try:
            target_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            target_process.kill()
            target_process.wait(timeout=5)
        if profile_dir is not None:
            shutil.rmtree(profile_dir, ignore_errors=True)
