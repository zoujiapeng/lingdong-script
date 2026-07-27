from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from winpilot.vnext.model_roles import (
    VISUAL_ACTOR_MODEL_ROLE,
    VISUAL_VERIFIER_MODEL_ROLE,
)


WORKSPACE = Path(__file__).resolve().parents[3]


def _load(path: Path, name: str):
    unique_name = f"{name}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(unique_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def _client_harness():
    return _load(
        WORKSPACE / "scripts" / "client_e2e_run.py",
        "client_e2e_run_canary_test",
    )


def _canary_harness():
    return _load(
        WORKSPACE / "scripts" / "client_vnext_canary_e2e_run.py",
        "client_vnext_canary_e2e_run_test",
    )


def _oracle():
    return _load(
        WORKSPACE
        / "python"
        / "winpilot_home"
        / "eval_targets"
        / "vnext_canary_server.py",
        "vnext_canary_server_test",
    )


def _input_hook():
    return _load(
        WORKSPACE / "scripts" / "vnext_canary_input_hook.py",
        "vnext_canary_input_hook_test",
    )


def test_visible_client_selector_is_exact_and_read_back(monkeypatch):
    harness = _client_harness()
    state = {"label": "Legacy", "clicked": 0}

    class ValuePattern:
        @property
        def Value(self):
            return state["label"]

    class Combo:
        Name = harness.RUNTIME_SELECTOR

        def Click(self):
            state["clicked"] += 1

        def GetPattern(self, pattern_id):
            if pattern_id == harness.auto.PatternId.ValuePattern:
                return ValuePattern()
            return None

    def send_keys(keys):
        if keys == "{Home}":
            state["label"] = "Legacy"
        elif keys == "{Down}":
            state["label"] = "vNext"

    monkeypatch.setattr(harness.auto, "SendKeys", send_keys)
    monkeypatch.setattr(harness.time, "sleep", lambda _seconds: None)

    selected = harness._select_runtime_version(Combo(), "vnext")
    assert selected == {
        "runtime_version": "vnext",
        "runtime_selector_label": "vNext",
    }
    assert state["clicked"] == 1

    selected = harness._select_runtime_version(Combo(), "legacy")
    assert selected["runtime_version"] == "legacy"
    with pytest.raises(ValueError):
        harness._select_runtime_version(Combo(), "future")


def test_closed_matrix_has_two_fixed_seeds_per_family_and_no_sample_overrides():
    harness = _canary_harness()
    grouped: dict[str, list[int]] = {}
    for item in harness.CANARY_MATRIX:
        grouped.setdefault(item["family"], []).append(item["seed"])
        assert tuple(item["runtime_order"]) in {
            ("legacy", "vnext"),
            ("vnext", "legacy"),
        }
        assert sorted(item["runtime_order"]) == ["legacy", "vnext"]
    assert grouped == {
        "click_double": [4101, 4102],
        "scroll_roundtrip": [5201, 5202],
        "type_hotkey": [6301, 6302],
    }
    assert harness.FROZEN_RUNTIME_BUDGET == {
        "max_turns": 30,
        "max_runtime_s": 300,
        "max_model_calls": 60,
    }
    assert tuple(inspect.signature(harness.run_suite).parameters) == ()
    source = Path(harness.__file__).read_text(encoding="utf-8")
    for forbidden in (
        'add_argument("--seed"',
        'add_argument("--timeout"',
        'add_argument("--max-steps"',
        'add_argument("--case"',
        'add_argument("--suite"',
        'add_argument("--runtime"',
        'add_argument("--output-root"',
    ):
        assert forbidden not in source
    assert "WaitForSingleObject(handle, 0)" in source


def test_manifest_predeclares_every_unrun_member_before_execution():
    harness = _canary_harness()
    manifest = harness._new_manifest()
    assert manifest["status"] == "planned"
    assert len(manifest["pairs"]) == 6
    assert [
        run["runtime_version"]
        for pair in manifest["pairs"]
        for run in pair["runs"]
    ] == [
        runtime
        for item in harness.CANARY_MATRIX
        for runtime in item["runtime_order"]
    ]
    assert all(
        pair["status"] == "unrun"
        and all(run["status"] == "unrun" for run in pair["runs"])
        for pair in manifest["pairs"]
    )


def test_suite_attempts_both_fixed_members_then_stops_and_retains_unrun(
    monkeypatch,
    tmp_path,
):
    harness = _canary_harness()
    calls: list[tuple[str, str]] = []

    class Mutex:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class Identity:
        pid = 10
        hwnd = 20
        executable = "electron.exe"
        command_line = ["electron.exe", str(WORKSPACE)]
        workspace_verified = True

    class Driver:
        identity = Identity()

        def __init__(self, _output_dir):
            pass

    def fake_run(spec, runtime_version, **_kwargs):
        calls.append((spec["case"], runtime_version))
        passed = runtime_version == "vnext"
        return {
            "runtime_version": runtime_version,
            "status": "passed" if passed else "failed",
            "passed": passed,
            "report": f"{runtime_version}.json",
        }

    monkeypatch.setattr(harness, "DesktopSuiteMutex", Mutex)
    monkeypatch.setattr(harness.client_harness, "ElectronClientDriver", Driver)
    monkeypatch.setattr(harness, "DEFAULT_OUTPUT", tmp_path)
    monkeypatch.setattr(
        harness,
        "CAMPAIGN_LEDGER",
        tmp_path / "campaign-ledger.json",
    )
    monkeypatch.setattr(
        harness,
        "_enable_fixed_canary",
        lambda _driver: {
            "vnext": {
                "live_canary_enabled": False,
                **harness.FROZEN_RUNTIME_BUDGET,
            },
            "api": {"max_retries": 2},
        },
    )
    monkeypatch.setattr(
        harness,
        "_restore_canary_config",
        lambda _driver, _original: {"passed": True},
    )
    monkeypatch.setattr(
        harness,
        "_authenticated_config",
        lambda _driver: {"model": "model-one", "sha256": "a" * 64},
    )
    monkeypatch.setattr(harness, "run_runtime_case", fake_run)

    manifest = harness.run_suite()

    assert calls == [
        ("click-double-4101", "legacy"),
        ("click-double-4101", "vnext"),
    ]
    assert manifest["pairs"][0]["status"] == "failed"
    assert [item["status"] for item in manifest["pairs"][0]["runs"]] == [
        "failed",
        "passed",
    ]
    assert all(pair["status"] == "unrun" for pair in manifest["pairs"][1:])
    saved = json.loads(Path(manifest["manifest"]).read_text(encoding="utf-8"))
    assert saved["stopped_after_pair"] == "click-double-4101"
    assert all(pair["status"] == "unrun" for pair in saved["pairs"][1:])
    ledger = json.loads(
        (tmp_path / "campaign-ledger.json").read_text(encoding="utf-8")
    )
    assert ledger["status"] == "failed"

    refused = harness.run_suite()
    assert refused["status"] == "invalid"
    assert "already claimed" in refused["errors"][0]
    assert len(calls) == 2


def test_campaign_temporarily_freezes_retry_and_restores_exact_config(
    monkeypatch,
):
    harness = _canary_harness()
    state = {
        "api": {
            "provider": "openai",
            "base_url": "https://example.invalid/v1",
            "model": "model-one",
            "max_retries": 2,
            "done_judge_model": "",
        },
        "vlm": {
            "provider": "openai",
            "base_url": "https://example.invalid/v1",
            "model": "model-one",
        },
        "agent": {
            "escalation_model": "",
            "llm_trace": True,
        },
        "vnext": {
            "live_canary_enabled": False,
            **harness.FROZEN_RUNTIME_BUDGET,
        },
    }

    def request(_driver, *, method="GET", patch=None):
        if method == "PATCH":
            assert isinstance(patch, dict)
            for section, values in patch.items():
                state.setdefault(section, {}).update(values)
        return json.loads(json.dumps(state))

    monkeypatch.setattr(harness, "_config_request", request)
    original = harness._enable_fixed_canary(object())
    frozen = harness._authenticated_config(object())
    assert frozen["api_max_retries"] == 0
    assert state["vnext"]["live_canary_enabled"] is True
    assert state["api"]["max_retries"] == 0

    restored = harness._restore_canary_config(object(), original)
    assert restored["passed"] is True
    assert state["vnext"]["live_canary_enabled"] is False
    assert state["api"]["max_retries"] == 2


def test_same_visible_electron_identity_is_required():
    harness = _client_harness()
    expected = harness.ClientIdentity(
        pid=10,
        hwnd=20,
        executable=r"C:\Electron\electron.exe",
        command_line=["electron.exe", str(WORKSPACE)],
        workspace_verified=True,
    )
    observed = harness.ClientIdentity(
        pid=10,
        hwnd=20,
        executable=r"c:\electron\electron.exe",
        command_line=["electron.exe", str(WORKSPACE)],
        workspace_verified=True,
    )
    assert harness._client_identity_audit(observed, expected)["passed"] is True
    changed = harness.ClientIdentity(
        pid=11,
        hwnd=21,
        executable=observed.executable,
        command_line=observed.command_line,
        workspace_verified=True,
    )
    rejected = harness._client_identity_audit(changed, expected)
    assert rejected["passed"] is False
    assert len(rejected["errors"]) == 2


def test_passive_input_hook_normalizes_injected_mouse_and_keyboard_without_installing():
    hook = _input_hook()
    mouse = hook.mouse_event_from_raw(
        hook.WM_MOUSEWHEEL,
        time_ns=123_000_000,
        x=410,
        y=220,
        mouse_data=(0xFF88 << 16),
        flags=hook.LLMHF_INJECTED | hook.LLMHF_LOWER_IL_INJECTED,
        os_event_time_ms=77,
        foreground_hwnd=991,
        foreground_root_hwnd=991,
        window_from_point_hwnd=992,
    )
    assert mouse == {
        "time_ns": 123_000_000,
        "device": "mouse",
        "kind": "wheel",
        "message": hook.WM_MOUSEWHEEL,
        "mutation": True,
        "injected": True,
        "lower_integrity_injected": True,
        "screen_x": 410,
        "screen_y": 220,
        "wheel_delta": -120,
        "button": "",
        "os_event_time_ms": 77,
        "foreground_hwnd": 991,
        "foreground_root_hwnd": 991,
        "window_from_point_hwnd": 992,
    }
    key = hook.keyboard_event_from_raw(
        hook.WM_KEYUP,
        time_ns=124_000_000,
        vk_code=0x41,
        scan_code=30,
        flags=hook.LLKHF_INJECTED | hook.LLKHF_UP,
        os_event_time_ms=78,
        foreground_hwnd=991,
        foreground_root_hwnd=991,
        window_from_point_hwnd=992,
    )
    assert key["kind"] == "key_up"
    assert key["mutation"] is True
    assert key["injected"] is True
    assert key["up"] is True
    assert key["vk_code"] == 0x41

    move = hook.mouse_event_from_raw(
        hook.WM_MOUSEMOVE,
        time_ns=125_000_000,
        x=411,
        y=221,
        mouse_data=0,
        flags=0,
        os_event_time_ms=79,
        foreground_hwnd=991,
        foreground_root_hwnd=991,
        window_from_point_hwnd=992,
    )
    assert move["mutation"] is False
    assert move["injected"] is False


def test_passive_hook_source_is_read_only_and_always_chains():
    hook = _input_hook()
    source = Path(hook.__file__).read_text(encoding="utf-8")
    assert "CallNextHookEx" in source
    assert "SetWindowsHookExW" in source
    assert "PostThreadMessageW" in source
    assert "SendInput" not in source
    assert "keybd_event(" not in source
    assert "mouse_event(" not in source
    assert "return api.call_next" in source


def test_external_oracle_waits_for_late_wrong_input_and_stable_snapshot(
    monkeypatch,
):
    harness = _canary_harness()
    clock = {"now": 0.0}

    def monotonic():
        return clock["now"]

    def sleep(seconds):
        clock["now"] += seconds

    def state(_url):
        events = [{"type": "scenario_success"}]
        oracle = {"passed": True, "errors": []}
        if clock["now"] >= 0.4:
            events.append({"type": "wrong_input"})
            oracle = {"passed": False, "errors": ["wrong input"]}
        return {"events": events, "oracle": oracle}

    monkeypatch.setattr(harness.time, "monotonic", monotonic)
    monkeypatch.setattr(harness.time, "sleep", sleep)
    monkeypatch.setattr(harness, "_json_get", state)
    settled, evidence = harness._settled_external_state(
        "http://example.invalid/state",
        timeout=3,
    )
    assert evidence["passed"] is True
    assert evidence["stable_seconds"] >= 1
    assert settled["oracle"]["passed"] is False
    assert [item["type"] for item in settled["events"]] == [
        "scenario_success",
        "wrong_input",
    ]


def test_profile_cleanup_is_proven(tmp_path):
    harness = _canary_harness()
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "state").write_text("x", encoding="utf-8")
    evidence = harness._remove_profile(profile)
    assert evidence["passed"] is True
    assert profile.exists() is False


def _event_factory(case_id: str, scenario: str, seed: int, page_id: str):
    events: list[dict[str, Any]] = []

    def add(event_type: str, *, trusted: bool = True, **details: Any):
        next_sequence = len(events) + 1
        details.setdefault(
            "event",
            {
                "client_x": 240 if event_type.startswith("pointer") or event_type in {"click", "dblclick", "wheel"} else None,
                "client_y": 180 if event_type.startswith("pointer") or event_type in {"click", "dblclick", "wheel"} else None,
                "screen_x": 320 if event_type.startswith("pointer") or event_type in {"click", "dblclick", "wheel"} else None,
                "screen_y": 240 if event_type.startswith("pointer") or event_type in {"click", "dblclick", "wheel"} else None,
                "button": 0 if event_type.startswith("pointer") or event_type in {"click", "dblclick"} else None,
                "buttons": 0,
                "key": details.get("key"),
                "code": details.get("code"),
                "ctrl_key": details.get("ctrl_key") is True,
                "shift_key": False,
                "alt_key": False,
                "meta_key": False,
                "repeat": details.get("repeat") is True,
                "delta_x": details.get("delta_x"),
                "delta_y": details.get("delta_y"),
                "delta_mode": 0 if event_type == "wheel" else None,
            },
        )
        details.setdefault(
            "visible_state",
            {
                "scenario": scenario,
                "revision": next_sequence,
                "final_dom_state": details.get("final_dom_state", "waiting"),
                "value": details.get("value", details.get("final_value")),
                "scroll_top": details.get("scroll_top"),
                "counts": details.get("counts"),
            },
        )
        events.append({
            "case_id": case_id,
            "scenario": scenario,
            "seed": seed,
            "page_id": page_id,
            "event_seq": next_sequence,
            "client_wall_time_ms": 1_000_000 + next_sequence,
            "client_monotonic_ms": 1000 + next_sequence,
            "type": event_type,
            "trusted": trusted,
            "details": details,
        })
        return events[-1]

    return events, add


def _click_events():
    case_id, scenario, seed = "click-case", "click_double", 4101
    page_id = f"vnext-canary:{case_id}:{scenario}:{seed}"
    events, add = _event_factory(case_id, scenario, seed, page_id)
    add("page_ready", trusted=False, final_dom_state="waiting")
    add("pointerdown", target_id="single", detail=1)
    add("pointerup", target_id="single", detail=1)
    add("click", target_id="single", detail=1)
    add("pointerdown", target_id="double", detail=1)
    add("pointerup", target_id="double", detail=1)
    add("click", target_id="double", detail=1)
    add("pointerdown", target_id="double", detail=2)
    add("pointerup", target_id="double", detail=2)
    add("click", target_id="double", detail=2)
    add("dblclick", target_id="double", detail=2)
    add(
        "scenario_success",
        succeeded=True,
        violations=[],
        final_dom_state="complete",
        counts={"single_click": 1, "double_click_events": 2, "dblclick": 1},
    )
    return events, case_id, scenario, seed, page_id


def _scroll_events():
    case_id, scenario, seed = "scroll-case", "scroll_roundtrip", 5201
    page_id = f"vnext-canary:{case_id}:{scenario}:{seed}"
    events, add = _event_factory(case_id, scenario, seed, page_id)
    add("page_ready", trusted=False, final_dom_state="waiting")
    add("wheel", direction="down", delta_y=384, wheel_count=1)
    add("scroll_state", phase="down", scroll_top=600, max_scroll=600)
    add("scroll_checkpoint", checkpoint_count=1, scroll_top=600, max_scroll=600)
    add("wheel", direction="up", delta_y=-384, wheel_count=2)
    add("scroll_state", phase="up", scroll_top=0, max_scroll=600)
    add(
        "scenario_success",
        succeeded=True,
        violations=[],
        final_dom_state="complete",
        phase="complete",
        checkpoint_count=1,
        scroll_top=0,
        wheel_count=2,
    )
    return events, case_id, scenario, seed, page_id


def _type_events():
    case_id, scenario, seed = "type-case", "type_hotkey", 6301
    page_id = f"vnext-canary:{case_id}:{scenario}:{seed}"
    token = "CANARY-ALPHA-17"
    events, add = _event_factory(case_id, scenario, seed, page_id)
    initial = "replace-this-0"
    add(
        "page_ready",
        trusted=False,
        final_dom_state="waiting",
        expected_token=token,
        initial_value=initial,
    )
    add("pointerdown", target_id="editor", detail=1)
    add("pointerup", target_id="editor", detail=1)
    add("click", target_id="editor", detail=1, click_count=1)
    add("keydown", key="Control", code="ControlLeft", ctrl_key=True, repeat=False, chord="")
    add("keydown", key="a", code="KeyA", ctrl_key=True, repeat=False, chord="ctrl+a")
    add("keyup", key="a", code="KeyA", ctrl_key=True, repeat=False, chord="ctrl+a")
    add("keyup", key="Control", code="ControlLeft", ctrl_key=False, repeat=False, chord="")
    add("beforeinput", target_id="editor", input_type="insertText", data=token, value_before=initial)
    add("input", target_id="editor", input_type="insertText", data=token, value=token)
    add("keydown", key="Control", code="ControlLeft", ctrl_key=True, repeat=False, chord="")
    add("keydown", key="Enter", code="Enter", ctrl_key=True, repeat=False, chord="ctrl+enter")
    add(
        "scenario_success",
        succeeded=True,
        violations=[],
        final_dom_state="complete",
        final_value=token,
        expected_token=token,
        select_all_count=1,
        submit_count=1,
        click_count=1,
    )
    add("keyup", key="Enter", code="Enter", ctrl_key=True, repeat=False, chord="ctrl+enter")
    add("keyup", key="Control", code="ControlLeft", ctrl_key=False, repeat=False, chord="")
    return events, case_id, scenario, seed, page_id


@pytest.mark.parametrize(
    "factory",
    (_click_events, _scroll_events, _type_events),
    ids=("click-double", "scroll-roundtrip", "type-hotkey"),
)
def test_independent_oracle_accepts_only_complete_trusted_exact_sequences(factory):
    oracle = _oracle()
    events, case_id, scenario, seed, page_id = factory()
    verdict = oracle.validate_case_events(
        events,
        case_id=case_id,
        scenario=scenario,
        seed=seed,
        page_id=page_id,
    )
    assert verdict["passed"] is True, verdict
    assert verdict["wrong_input_count"] == 0
    assert verdict["success_event_count"] == 1
    expected_actions = {
        "click_double": ["click", "double_click"],
        "scroll_roundtrip": ["scroll", "scroll"],
        "type_hotkey": ["click", "hotkey", "type_text", "hotkey"],
    }
    observed_actions = [
        item["action"] for item in verdict["action_observations"]
    ]
    if scenario == "scroll_roundtrip":
        assert len(observed_actions) >= 2
        assert set(observed_actions) == {"scroll"}
    else:
        assert observed_actions == expected_actions[scenario]
    assert all(
        item["decidable"] is True
        and item["actual_changed"] is True
        and item["before_state_sha256"] != item["after_state_sha256"]
        for item in verdict["action_observations"]
    )

    corrupted = json.loads(json.dumps(events))
    trusted_index = next(
        index for index, item in enumerate(corrupted)
        if item["type"] not in {"page_ready"}
    )
    corrupted[trusted_index]["trusted"] = False
    rejected = oracle.validate_case_events(
        corrupted,
        case_id=case_id,
        scenario=scenario,
        seed=seed,
        page_id=page_id,
    )
    assert rejected["passed"] is False
    assert any("untrusted input event" in error for error in rejected["errors"])

    duplicate = json.loads(json.dumps(events))
    duplicate.insert(-1, {
        "case_id": case_id,
        "scenario": scenario,
        "seed": seed,
        "page_id": page_id,
        "event_seq": 0,
        "type": "wrong_input",
        "trusted": True,
        "details": {"code": "duplicate"},
    })
    for index, item in enumerate(duplicate, start=1):
        item["event_seq"] = index
    rejected = oracle.validate_case_events(
        duplicate,
        case_id=case_id,
        scenario=scenario,
        seed=seed,
        page_id=page_id,
    )
    assert rejected["passed"] is False
    assert rejected["wrong_input_count"] == 1


def test_type_oracle_rejects_correction_or_non_exact_value_progression():
    oracle = _oracle()
    events, case_id, scenario, seed, page_id = _type_events()
    input_event = next(item for item in events if item["type"] == "input")
    input_event["details"]["input_type"] = "deleteContentBackward"
    verdict = oracle.validate_case_events(
        events,
        case_id=case_id,
        scenario=scenario,
        seed=seed,
        page_id=page_id,
    )
    assert verdict["passed"] is False
    assert any(
        "deletion, correction" in error
        for error in verdict["errors"]
    )


def test_type_oracle_rejects_any_extra_trusted_key_pair():
    oracle = _oracle()
    events, case_id, scenario, seed, page_id = _type_events()
    insert_at = next(
        index for index, item in enumerate(events)
        if item["type"] == "beforeinput"
    )
    extras = [
        {
            **events[insert_at - 1],
            "type": "keydown",
            "details": {
                **events[insert_at - 1]["details"],
                "key": "ArrowRight",
                "code": "ArrowRight",
                "ctrl_key": False,
                "chord": "",
            },
        },
        {
            **events[insert_at - 1],
            "type": "keyup",
            "details": {
                **events[insert_at - 1]["details"],
                "key": "ArrowRight",
                "code": "ArrowRight",
                "ctrl_key": False,
                "chord": "",
            },
        },
    ]
    events[insert_at:insert_at] = extras
    for index, item in enumerate(events, start=1):
        item["event_seq"] = index
        item["client_wall_time_ms"] = 1_000_000 + index
        item["client_monotonic_ms"] = 1000 + index
    verdict = oracle.validate_case_events(
        events,
        case_id=case_id,
        scenario=scenario,
        seed=seed,
        page_id=page_id,
    )
    assert verdict["passed"] is False
    assert any("sequence is not exact" in error for error in verdict["errors"])


def _client_result_for_trace(path: Path, run_id: str) -> dict[str, Any]:
    return {
        "result": {
            "run_id": run_id,
            "outcome": "terminal",
            "status": "done",
            "runtime": {
                "current_run": {
                    "run_id": run_id,
                    "runtime_version": "vnext",
                    "no_legacy_fallback": True,
                },
            },
        },
        "trace_binding": {"trace_file": str(path), "passed": True},
        "execution_valid": True,
        "passed": True,
    }


def _target_identity(hwnd: int) -> dict[str, Any]:
    return {
        "hwnd": hwnd,
        "root_hwnd": hwnd,
        "title": "WinPilot vNext Canary test",
        "class_name": "Chrome_WidgetWin_1",
        "process_id": 4242,
        "executable": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "rect": [80, 60, 1180, 820],
    }


def test_trace_audit_binds_frozen_vnext_and_rejects_legacy_or_fallback(tmp_path):
    harness = _canary_harness()
    assert harness.VNEXT_MODEL_ROLES == frozenset({
        VISUAL_ACTOR_MODEL_ROLE,
        VISUAL_VERIFIER_MODEL_ROLE,
    })
    run_id = "arp-vnext-canary"
    target_hwnd = 991
    target_identity = _target_identity(target_hwnd)
    expected_target = harness._expected_target_trace(target_identity)
    mission_id = "mission-one"
    events = [
        {
            "type": "arp_run_start",
            "run_id": run_id,
            "runtime_version": "vnext",
            "graph_name": harness.CANARY_TASK,
            "task_contract": None,
        },
        {
            "type": "vnext_canary_start",
            "run_id": run_id,
            "mission_id": mission_id,
            "runtime_budget": dict(harness.FROZEN_RUNTIME_BUDGET),
            "no_legacy_fallback": True,
            "model": "model-one",
            "target": {
                "binding_id": "binding-1",
                **expected_target,
            },
        },
        {
            "type": "vnext_model_call",
            "run_id": run_id,
            "mission_id": mission_id,
            "model": "model-one",
            "model_role": VISUAL_ACTOR_MODEL_ROLE,
        },
        {
            "type": "vnext_model_call",
            "run_id": run_id,
            "mission_id": mission_id,
            "model": "model-one",
            "model_role": VISUAL_ACTOR_MODEL_ROLE,
        },
        {
            "type": "vnext_model_call",
            "run_id": run_id,
            "mission_id": mission_id,
            "model": "model-one",
            "model_role": VISUAL_ACTOR_MODEL_ROLE,
        },
        {
            "type": "vnext_model_call",
            "run_id": run_id,
            "mission_id": mission_id,
            "model": "model-one",
            "model_role": VISUAL_VERIFIER_MODEL_ROLE,
        },
        {
            "type": "vnext_raw_receipt",
            "metadata": {
                "run_id": run_id,
                "decision_id": "decision-1",
                "controller_action": "click",
                "target_hwnd": target_hwnd,
                "target_binding_id": "binding-1",
            },
            "receipt": {"outcome": "applied"},
        },
        {
            "type": "vnext_raw_receipt",
            "metadata": {
                "run_id": run_id,
                "decision_id": "decision-2",
                "controller_action": "double_click",
                "target_hwnd": target_hwnd,
                "target_binding_id": "binding-1",
            },
            "receipt": {"outcome": "applied"},
        },
        {
            "type": "vnext_canary_turn",
            "run_id": run_id,
            "mission_id": mission_id,
            "no_legacy_fallback": True,
            "turn_index": 0,
        },
        {
            "type": "vnext_canary_turn",
            "run_id": run_id,
            "mission_id": mission_id,
            "no_legacy_fallback": True,
            "turn_index": 1,
        },
        {
            "type": "vnext_canary_turn",
            "run_id": run_id,
            "mission_id": mission_id,
            "no_legacy_fallback": True,
            "turn_index": 2,
        },
        {
            "type": "vnext_canary_done",
            "run_id": run_id,
            "mission_id": mission_id,
            "runtime_version": "vnext",
            "runtime_budget": dict(harness.FROZEN_RUNTIME_BUDGET),
            "no_legacy_fallback": True,
            "status": "mission_verified",
            "target_binding_id": "binding-1",
            "turns_used": 3,
            "model_calls": 4,
        },
    ]
    trace = tmp_path / "vnext.jsonl"
    trace.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    result = _client_result_for_trace(trace, run_id)
    audit = harness._trace_audit(
        result,
        runtime_version="vnext",
        expected_model="model-one",
        target_identity=target_identity,
        family="click_double",
    )
    assert audit["passed"] is True, audit

    events[1]["target"]["process_id"] += 1
    trace.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    rejected_target = harness._trace_audit(
        result,
        runtime_version="vnext",
        expected_model="model-one",
        target_identity=target_identity,
        family="click_double",
    )
    assert rejected_target["passed"] is False
    assert any(
        "frozen target identity mismatch" in error
        for error in rejected_target["errors"]
    )
    events[1]["target"]["process_id"] -= 1

    events.append({"type": "agent_start", "run_id": run_id, "task": harness.CANARY_TASK})
    trace.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    rejected = harness._trace_audit(
        result,
        runtime_version="vnext",
        expected_model="model-one",
        target_identity=target_identity,
        family="click_double",
    )
    assert rejected["passed"] is False
    assert any("legacy/task-stage/fallback" in error for error in rejected["errors"])


def test_trace_audit_rejects_vnext_events_in_legacy_trace(tmp_path, monkeypatch):
    harness = _canary_harness()
    run_id = "legacy-child"
    forensic_root = tmp_path / "forensic"
    monkeypatch.setattr(harness.client_harness, "TRACE_ROOT", forensic_root)
    run_dir = forensic_root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "001_llm_request.json").write_text(
        json.dumps({
            "run_id": run_id,
            "model": "model-one",
            "max_attempts": 1,
        }),
        encoding="utf-8",
    )
    (run_dir / "002_llm_request_attempt.json").write_text(
        json.dumps({
            "run_id": run_id,
            "attempt": 1,
            "retry_kind": "initial_request",
        }),
        encoding="utf-8",
    )
    events = [
        {
            "type": "arp_run_start",
            "run_id": "arp-outer",
            "runtime_version": "legacy",
            "task_contract": {"task": harness.CANARY_TASK},
        },
        {
            "type": "agent_start",
            "run_id": run_id,
            "task": harness.CANARY_TASK,
            "max_steps": 30,
        },
        {"type": "agent_decision", "run_id": run_id, "model": "model-one"},
    ]
    trace = tmp_path / "legacy.jsonl"
    trace.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    result = _client_result_for_trace(trace, run_id)
    result["result"]["runtime"]["current_run"] = {
        "run_id": run_id,
        "runtime_version": "legacy",
        "no_legacy_fallback": False,
    }
    audit = harness._trace_audit(
        result,
        runtime_version="legacy",
        expected_model="model-one",
        target_identity=_target_identity(55),
        family="click_double",
    )
    assert audit["passed"] is True, audit

    events.append({"type": "vnext_model_call", "run_id": run_id, "model": "model-one"})
    trace.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    rejected = harness._trace_audit(
        result,
        runtime_version="legacy",
        expected_model="model-one",
        target_identity=_target_identity(55),
        family="click_double",
    )
    assert rejected["passed"] is False
    assert any("legacy trace contains vNext" in error for error in rejected["errors"])

    events.pop()
    (run_dir / "002_llm_request_attempt.json").write_text(
        json.dumps({
            "run_id": run_id,
            "attempt": 2,
            "retry_kind": "request_retry",
        }),
        encoding="utf-8",
    )
    trace.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    rejected_retry = harness._trace_audit(
        result,
        runtime_version="legacy",
        expected_model="model-one",
        target_identity=_target_identity(55),
        family="click_double",
    )
    assert rejected_retry["passed"] is False
    assert any(
        "provider attempt forensic contains retry" in error
        for error in rejected_retry["errors"]
    )


def test_target_is_separate_and_contains_no_forbidden_gesture_surface():
    target = (
        WORKSPACE
        / "python"
        / "winpilot_home"
        / "eval_targets"
        / "vnext_canary_target.html"
    ).read_text(encoding="utf-8")
    lowered = target.casefold()
    assert "click_double" in target
    assert "scroll_roundtrip" in target
    assert "type_hotkey" in target
    assert "event.isTrusted" in target
    assert "event_seq" in target
    assert "client_wall_time_ms" in target
    assert "client_monotonic_ms" in target
    assert "screen_x" in target
    assert "screen_y" in target
    assert "visible_state" in target
    assert "state_before" in target
    assert "state_after" in target
    for forbidden in ("setpointercapture", "drag", "held pointer", "roi", "mcp"):
        assert forbidden not in lowered
