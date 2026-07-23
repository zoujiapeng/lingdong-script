"""Opt-in real-client Windows E2E matrix for general visual interactions.

The test enters through the same ``require_gui=True`` compile request and ARP
run request as the Electron client.  Every case is one opaque
``ExecuteDesktopTask``: the model receives the unchanged root goal and live
pixels, then owns its visual strategy.  The test does not mock the model,
screenshot pipeline, Controller, browser, or pointer input.  A localhost
evaluation target records trusted browser pointer events as independent ground
truth, while the WinPilot trace proves the fresh-frame/action/root-goal chain.

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
CORRIDOR_TASK = (
    "In the current visible window, drag the blue disk through the visible "
    "corridor into the green GOAL without leaving the corridor."
)
VISUAL_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario": "drag_fit",
        "task": (
            "In the current visible window, drag the blue MOVE ME circle completely "
            "inside the green DROP HERE target."
        ),
        "expected_actions": {"drag"},
    },
    {
        "scenario": "precise_click",
        "task": (
            "In the current visible window, click the only purple hexagon with a "
            "white center and ignore every distractor."
        ),
        "expected_actions": {"click"},
    },
    {
        "scenario": "ordered_clicks",
        "task": (
            "In the current visible window, click the numbered markers 1, then 2, "
            "then 3 in ascending order. Ignore the gray letter distractors."
        ),
        "expected_actions": {"click"},
    },
    {
        "scenario": "slider",
        "task": "In the current visible window, drag the orange knob into the striped green GOAL band.",
        "expected_actions": {"drag", "mouse_down", "move_mouse", "mouse_up"},
    },
    # Mulberry32 seeds below deterministically cover all four route
    # orientations.  Random-only coverage repeatedly hid mirror/reversal bugs.
    *(
        {
            "id": f"corridor_drag_v{variant}",
            "scenario": "corridor_drag",
            "seed": seed,
            "expected_variant": variant,
            "task": CORRIDOR_TASK,
            "expected_actions": {
                "drag", "mouse_down", "move_mouse", "mouse_up",
            },
        }
        for variant, seed in enumerate((7, 12, 1, 4))
    ),
    {
        "scenario": "shared_turn",
        "task": "跟我玩这个棋局，现在到你了。",
        "expected_actions": {"draw_strokes"},
    },
)


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


def _assert_model_owned_visual_evidence(
    events: list[dict[str, Any]],
    snapshot: dict[str, Any],
    *,
    task: str,
    target_hwnd: int,
) -> dict[str, Any]:
    """Prove one root-goal -> fresh pixels -> action -> fresh pixels chain.

    These are architecture assertions, not a scenario-specific plan.  The
    browser oracle below decides whether the model's chosen strategy actually
    solved the case; this helper only proves that the real native visual loop
    owned that strategy and that completion used causal evidence.
    """

    starts = [
        item for item in events
        if item.get("type") == "agent_start"
        and item.get("stage_type") == "ExecuteDesktopTask"
    ]
    assert starts, "the ExecuteDesktopTask child Agent never started"
    start = starts[-1]
    child_run_id = str(start.get("run_id") or "")
    assert child_run_id, start
    assert start.get("task") == task, start
    runtime = start.get("runtime") or {}
    assert runtime.get("stage_type") == "ExecuteDesktopTask", runtime
    assert runtime.get("adaptive_visual_interaction") is True, runtime
    assert runtime.get("perception_daemon") is False, runtime
    assert runtime.get("experience_enabled") is False, runtime

    child_events = [
        item for item in events
        if str(item.get("run_id") or "") == child_run_id
    ]
    indexed = list(enumerate(child_events))
    decisions = [
        item for item in child_events
        if item.get("type") == "agent_decision" and item.get("ok") is True
    ]
    assert decisions, "the model never made a live visual decision"

    frame_events = [
        (index, item) for index, item in indexed
        if item.get("type") == "visual_feedback"
        and int(item.get("frame_id") or 0) > 0
    ]
    assert frame_events, "no authoritative native visual frame was traced"
    frame_ids = [int(item.get("frame_id") or 0) for _, item in frame_events]
    assert frame_ids == sorted(frame_ids), frame_ids
    assert any(item.get("phase") == "initial" for _, item in frame_events), frame_events
    assert all(int(item.get("hwnd") or 0) == target_hwnd for _, item in frame_events), frame_events
    assert all(
        item.get("capture_scope") in {"target_window", "full_desktop"}
        and item.get("capture_scope_reason")
        for _, item in frame_events
    ), frame_events

    successful_actions = [
        (index, item) for index, item in indexed
        if item.get("type") == "agent_batch_action"
        and item.get("ok") is True
    ]
    assert successful_actions, "no native pointer action reached the Controller"
    for _index, action in successful_actions:
        envelope = action.get("action_envelope") or {}
        target = envelope.get("target") or {}
        assert int(target.get("hwnd") or 0) == target_hwnd, target
        assert int(target.get("frame_id") or 0) > 0, envelope
        region = target.get("region") or {}
        assert int(region.get("width") or 0) > 0, target
        assert int(region.get("height") or 0) > 0, target

    # At least one executed action must be followed by a strictly newer
    # authoritative post-action frame.  A successful input event or a pixel
    # delta alone is never treated as task completion.
    causal_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for action_index, action in successful_actions:
        action_frame_id = int(
            ((action.get("action_envelope") or {}).get("target") or {}).get(
                "frame_id",
            ) or 0
        )
        post_frame = next((
            frame for frame_index, frame in frame_events
            if frame_index > action_index
            and frame.get("phase") == "post_action"
            and int(frame.get("frame_id") or 0) > action_frame_id
        ), None)
        if post_frame is not None:
            causal_pairs.append((action, post_frame))
    assert causal_pairs, "native input was not followed by a fresh post-action frame"

    passed_done_checks = [
        (index, item) for index, item in indexed
        if item.get("type") == "done_check" and item.get("passed") is True
    ]
    assert passed_done_checks, "root-goal completion was never visually accepted"
    final_done_index, _final_done = passed_done_checks[-1]
    assert any(
        frame_index < final_done_index and frame.get("phase") == "post_action"
        for frame_index, frame in frame_events
    ), "completion was accepted without a preceding fresh post-action frame"

    stage_trace = snapshot.get("stage_trace") or []
    assert len(stage_trace) == 1, stage_trace
    stage_record = stage_trace[0]
    assert stage_record.get("stage_type") == "ExecuteDesktopTask", stage_record
    assert stage_record.get("status") == "done", stage_record
    assert int(stage_record.get("model_calls") or 0) >= 1, stage_record
    assert not (stage_record.get("interaction_state") or {}), stage_record

    evidence_snapshot = snapshot.get("contract_evidence") or {}
    root_evidence = next((
        item for item in evidence_snapshot.get("records") or []
        if item.get("predicate") == "desktop.full_goal_verified"
        and item.get("verified") is True
        and item.get("value") == task
    ), None)
    assert root_evidence is not None, evidence_snapshot
    evidence_metadata = root_evidence.get("metadata") or {}
    assert evidence_metadata.get("child_run_id") == child_run_id, root_evidence
    assert evidence_metadata.get("agent_done_verified") is True, root_evidence
    assert evidence_metadata.get("runtime_stage_type") == "ExecuteDesktopTask", root_evidence

    return {
        "child_run_id": child_run_id,
        "model_decision_count": len(decisions),
        "authoritative_frame_ids": frame_ids,
        "successful_action_count": len(successful_actions),
        "causal_action_frame_pairs": [
            {
                "action": str(
                    (action.get("action_envelope") or {}).get("type")
                    or action.get("tool")
                    or ""
                ),
                "source_frame_id": int(
                    ((action.get("action_envelope") or {}).get("target") or {}).get(
                        "frame_id",
                    ) or 0
                ),
                "post_action_frame_id": int(frame.get("frame_id") or 0),
            }
            for action, frame in causal_pairs
        ],
        "root_goal_evidence_id": str(root_evidence.get("id") or ""),
    }


def _assert_external_scenario(
    state: dict[str, Any],
    *,
    case_id: str,
    scenario: str,
    seed: int,
) -> None:
    """Validate trusted browser evidence, never the agent's success claim."""
    assert state.get("success") is True, state
    assert state.get("case_id") == case_id, state
    assert state.get("scenario") == scenario, state
    assert int(state.get("seed") or 0) == seed, state
    event = state.get("success_event") or {}
    assert event.get("type") == "scenario_success", event
    assert event.get("trusted") is True, event
    assert event.get("succeeded") is True, event
    details = event.get("details") or {}
    assert details.get("succeeded") is True, details

    if scenario == "drag_fit":
        token, target = details.get("token") or {}, details.get("target") or {}
        assert int(event.get("move_count") or 0) >= 2, event
        assert token["left"] >= target["left"], details
        assert token["top"] >= target["top"], details
        assert token["right"] <= target["right"], details
        assert token["bottom"] <= target["bottom"], details
    elif scenario == "precise_click":
        assert int(details.get("target_index")) >= 0, details
        assert int(event.get("move_count") or 0) == 0, event
    elif scenario == "ordered_clicks":
        assert details.get("required_sequence") == [1, 2, 3], details
        assert details.get("click_sequence") == [1, 2, 3], details
        assert int(event.get("move_count") or 0) == 0, event
        assert len(details.get("targets") or []) == 3, details
    elif scenario == "slider":
        goal = details.get("goal") or {}
        center = int(details.get("center_x"))
        assert int(event.get("move_count") or 0) >= 2, event
        assert goal["left"] <= center <= goal["right"], details
    elif scenario == "corridor_drag":
        assert int(event.get("move_count") or 0) >= 3, event
        assert details.get("path_valid") is True, details
        assert int(details.get("min_clearance") or -1) >= 0, details
        assert len(details.get("path_points") or []) >= 3, details
    elif scenario == "shared_turn":
        assert int(details.get("valid_stroke_count") or 0) >= 2, details
        assert int(details.get("selected_cell")) >= 0, details
        assert int(details.get("selected_cell")) != int(details.get("user_cell")), details
    else:  # pragma: no cover - the matrix is closed above
        raise AssertionError(f"unknown visual scenario {scenario!r}")


@pytest.mark.parametrize(
    "scenario_case",
    VISUAL_SCENARIOS,
    ids=[item.get("id") or item["scenario"] for item in VISUAL_SCENARIOS],
)
def test_real_frontend_api_visual_scenario(scenario_case: dict[str, Any]) -> None:
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
    scenario = str(scenario_case["scenario"])
    replay_seed = os.environ.get("WINPILOT_VISUAL_E2E_SEED", "").strip()
    seed = (
        int(replay_seed, 0)
        if replay_seed
        else (
            int(scenario_case["seed"])
            if "seed" in scenario_case
            else int(uuid.uuid4().hex[:8], 16)
        )
    )
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
    run_id = ""
    started_at = time.time()
    try:
        _wait_json(f"{target_base}/state", lambda value: value.get("ready") is True)
        state_url = f"{target_base}/state?case={case_id}"
        url = (
            f"{target_base}/visual_interaction_target.html?case={case_id}"
            f"&scenario={scenario}&seed={seed}"
        )
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
            state_url,
            lambda value: any(
                item.get("type") == "page_ready"
                and item.get("scenario") == scenario
                and int(((item.get("details") or {}).get("viewport") or {}).get("width") or 0) >= 760
                and int(((item.get("details") or {}).get("viewport") or {}).get("height") or 0) >= 560
                and int(((item.get("details") or {}).get("arena") or {}).get("width") or 0) >= 700
                and int(((item.get("details") or {}).get("arena") or {}).get("height") or 0) >= 300
                for item in value.get("events") or []
            ),
            timeout_s=10.0,
        )
        assert ready_state.get("success") is False
        if "expected_variant" in scenario_case:
            ready_event = next(
                item for item in reversed(ready_state.get("events") or [])
                if item.get("type") == "page_ready"
                and item.get("case_id") == case_id
            )
            assert int((ready_event.get("details") or {}).get("variant")) == int(
                scenario_case["expected_variant"]
            )

        task = str(scenario_case["task"])
        compiled = _request("POST", f"{base}/api/v2/task-contracts/compile", {
            "task": task,
            "max_steps": 12,
            "cold_start": True,
            "require_gui": True,
        })
        contract = compiled["contract"]
        graph = compiled["graph"]
        assert compiled.get("needs_input") is False, compiled
        assert compiled.get("clarification_questions") == [], compiled
        assert contract.get("satisfiable") is True, contract.get("missing_capabilities")
        assert contract.get("task") == task, contract
        assert contract.get("experience_policy") == "disabled", contract
        assert not contract.get("session"), contract.get("session")
        stage_types = [stage.get("type") for stage in contract.get("stages") or []]
        assert stage_types == ["ExecuteDesktopTask"], stage_types
        stage = (contract.get("stages") or [])[0]
        params = stage.get("parameters") or {}
        assert params.get("goal") == task, params
        assert params.get("original_task") == task, params
        assert params.get("preserve_original_intent") is True, params
        assert params.get("verification_policy") == "full_goal_coverage", params
        assert stage.get("allow_skill_replay") is False, stage
        assert {
            "interaction_mode", "primary_action_modality", "planned_actions",
            "surface_output_mode", "surface_output_subject",
            "surface_output_quality", "subject", "quality", "lifecycle",
            "forbid_open_only", "minimum_effect_categories",
        }.isdisjoint(params), params
        node_kinds = [node.get("kind") for node in graph.get("nodes") or []]
        assert node_kinds.count("task_stage") == 1, node_kinds
        assert "agent_goal" not in node_kinds
        assert "skill_replay" not in node_kinds
        task_node = next(
            node for node in graph.get("nodes") or []
            if node.get("kind") == "task_stage"
        )
        assert (
            ((task_node.get("config") or {}).get("stage") or {}).get("type")
            == "ExecuteDesktopTask"
        ), task_node
        assert graph.get("metadata", {}).get("experience_policy") == "disabled", graph
        assert (graph.get("policy") or {}).get("allow_shell") is False

        # Reproduce the native client's handoff/capture/start sequence.  The
        # opt-in test already yielded focus to its target above, so it captures
        # that foreground HWND through the same endpoint used by the client.
        assert _find_window(title, timeout_s=3.0) == browser_hwnd
        desktop_context = _request(
            "GET",
            f"{base}/api/v2/desktop-context?exclude_process_id=0",
        )
        target_window = desktop_context.get("target") or {}
        assert target_window.get("format") == "desktop-target/2", target_window
        assert target_window.get("usable") is True, target_window
        assert target_window.get("foreground_verified") is True, target_window
        assert int(target_window.get("hwnd") or 0) == browser_hwnd, target_window
        assert int(target_window.get("root_hwnd") or 0) > 0, target_window
        assert int(target_window.get("process_id") or 0) > 0, target_window
        process_identity = str(target_window.get("process_identity") or "")
        assert (
            process_identity.startswith("process-")
            and len(process_identity) == 40
            and all(character in "0123456789abcdef" for character in process_identity[8:])
        ), target_window
        submitted_at = int(time.time() * 1000)
        execution_context = {
            "format": "execution-context/1",
            "entrypoint": "native_agent_cockpit",
            "task_id": contract.get("task_id"),
            "submitted_at": submitted_at,
            "desktop_handoff": {
                "required": True,
                "strategy": "foreground_after_control_surface_yield",
                "yielded": True,
                "control_surface_pid": 0,
                "prepared_at": submitted_at,
                "reason": "real E2E target already owns foreground",
            },
            "target_window": target_window,
        }
        started = _request("POST", f"{base}/api/v2/arp/runs", {
            "graph": graph,
            "inputs": {
                "e2e_case_id": case_id,
                "execution_context": execution_context,
            },
        })
        run_id = str(started["run_id"])
        timeout_s = float(os.environ.get("WINPILOT_REAL_VISUAL_TIMEOUT_S", "240"))
        snapshot = _poll_run(base, run_id, timeout_s)
        try:
            target_state = _wait_json(
                state_url,
                lambda value: value.get("success") is True,
                timeout_s=5.0,
            )
        except AssertionError:
            target_state = _request("GET", state_url)
        events = _request("GET", f"{base}/api/trace/{run_id}")

        # Persist the trace before assertions so a real failure remains
        # inspectable instead of disappearing behind the first pytest error.
        stage_trace = snapshot.get("stage_trace") or []
        model_calls = sum(int(item.get("model_calls") or 0) for item in stage_trace)
        model_ms = sum(int(item.get("model_elapsed_ms") or 0) for item in stage_trace)
        controller_ms = sum(int(item.get("controller_elapsed_ms") or 0) for item in stage_trace)
        capture_ms = sum(int(item.get("capture_elapsed_ms") or 0) for item in stage_trace)
        recoveries = [
            item for item in events if item.get("type") == "agent_recovery"
        ]
        report = {
            "kind": "real_client_windows_visual_interaction_e2e",
            "validation_scope": "terminal_single_execute_desktop_task",
            "entrypoint": {
                "compile_api": "/api/v2/task-contracts/compile",
                "run_api": "/api/v2/arp/runs",
                "require_gui": True,
                "cold_start": True,
                "execution_context": execution_context,
            },
            "run_id": run_id,
            "case_id": case_id,
            "scenario": scenario,
            "matrix_case": scenario_case.get("id") or scenario,
            "seed": seed,
            "task": task,
            "status": snapshot.get("status"),
            "outcome": str(snapshot.get("status") or ""),
            "error": snapshot.get("error"),
            "duration_s": round(time.time() - started_at, 3),
            "model_calls": model_calls,
            "model_elapsed_ms": model_ms,
            "controller_elapsed_ms": controller_ms,
            "capture_elapsed_ms": capture_ms,
            "recovery_count": len(recoveries),
            "recoveries": recoveries,
            "stage_trace": stage_trace,
            "contract_evidence": snapshot.get("contract_evidence") or {},
            "external_ground_truth": target_state,
            "successful_action_types": _trace_action_types(events),
            "runtime": status.get("runtime"),
        }
        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        (REPORT_ROOT / f"{run_id}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        assert snapshot.get("status") == "done", snapshot
        _assert_external_scenario(
            target_state, case_id=case_id, scenario=scenario, seed=seed,
        )
        action_types = set(_trace_action_types(events))
        assert action_types & set(scenario_case["expected_actions"]), action_types
        if scenario == "ordered_clicks":
            successful_actions = _trace_action_types(events)
            assert successful_actions.count("click") >= 3, successful_actions

        visual_evidence = _assert_model_owned_visual_evidence(
            events,
            snapshot,
            task=task,
            target_hwnd=browser_hwnd,
        )
        report["visual_evidence"] = visual_evidence
        (REPORT_ROOT / f"{run_id}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    finally:
        # Explicitly clean up only resources created by this opt-in test.
        if run_id:
            try:
                current = _request(
                    "GET", f"{base}/api/v2/arp/runs/{run_id}", timeout=5.0,
                )
                current_run = (
                    current.get("run") if isinstance(current, dict) else {}
                ) or {}
                if str(current_run.get("status") or "") not in TERMINAL:
                    _request(
                        "POST",
                        f"{base}/api/v2/arp/runs/{run_id}/cancel",
                        {},
                        timeout=5.0,
                    )
            except Exception:
                pass
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
