from __future__ import annotations

import copy
import importlib.util
import inspect
import json
import sys
from pathlib import Path

import pytest


def _load_harness():
    path = Path(__file__).resolve().parents[3] / "scripts" / "client_visual_e2e_run.py"
    spec = importlib.util.spec_from_file_location("client_visual_e2e_run", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _state(scenario: str, details: dict, *, moves: int) -> dict:
    success = {
        "case_id": "case-1",
        "scenario": scenario,
        "seed": 17,
        "type": "scenario_success",
        "trusted": True,
        "pointer_type": "mouse",
        "move_count": moves,
        "succeeded": True,
        "details": {"succeeded": True, **details},
    }
    return {
        "ready": True,
        "success": True,
        "case_id": "case-1",
        "scenario": scenario,
        "seed": 17,
        "events": [success],
        "success_event": success,
    }


def _aggregate_arm(
    runtime: str,
    *,
    pair_id: str | None = "case:seed-1",
    fingerprint: str = "same",
    oracle_pass: bool | None = None,
) -> dict:
    if oracle_pass is None:
        oracle_pass = runtime == "vnext"
    runtime_identity = {
        "contract_version": 2,
        "pid": 42,
        "repo_root": "C:/repo",
        "git": {
            "loaded_commit": "abc",
            "current_commit": "abc",
            "dirty": False,
        },
        "source": {
            "loaded_stamp": "source-1",
            "current_stamp": "source-1",
            "drifted": False,
        },
    }
    return {
        "pair_id": pair_id,
        "runtime_version": runtime,
        "passed": oracle_pass,
        "evaluation_config_fingerprint": fingerprint,
        "model_configuration_identity": {
            "provider": "provider", "model": "model",
        },
        "runtime_service_identity": {
            "before": runtime_identity,
            "after": copy.deepcopy(runtime_identity),
        },
        "client_result": {
            "trace_binding": {"passed": True, "parse_errors": []},
            "memory_isolation": {"passed": True},
        },
        "recovery_evidence": {
            "trace_complete": True,
            "turn_trace_complete": runtime == "vnext",
            "oracle_status": "conclusive",
            "external_oracle_pass": oracle_pass,
            "wall_elapsed_ms": 100 if runtime == "legacy" else 120,
            "false_done_count": 0,
        },
    }


def test_real_client_visual_matrix_is_generic_and_covers_gesture_shapes():
    harness = _load_harness()

    assert harness.SUITES["smoke"] == (
        "drag-fit", "slider", "dynamic-hold", "game-drag-v2",
    )
    assert {
        item["scenario"] for item in harness.VISUAL_CASES.values()
    } == {
        "precise_click", "scroll_reveal", "drag_fit", "slider",
        "corridor_drag", "dynamic_hold", "keyboard_entry",
        "keyboard_select_all",
    }
    assert harness.SUITES["vnext-canary"] == ("precise-click",)
    assert harness.SUITES["vnext-scroll"] == ("scroll-reveal",)
    assert harness.SUITES["vnext-keyboard"] == (
        "keyboard-entry", "keyboard-select-all",
    )
    assert "scroll-reveal" not in harness.SUITES["vnext-canary"]
    assert harness.VISUAL_CASES["precise-click"]["runtime_version"] == "vnext"
    assert harness.VISUAL_CASES["scroll-reveal"]["runtime_version"] == "vnext"
    assert harness.VISUAL_CASES["keyboard-entry"]["runtime_version"] == "vnext"
    assert harness.VISUAL_CASES["keyboard-select-all"]["runtime_version"] == "vnext"
    assert {
        harness.VISUAL_CASES[name]["expected_variant"]
        for name in ("corridor-v0", "corridor-v1", "game-drag-v2", "corridor-v3")
    } == {0, 1, 2, 3}
    assert {
        item["task"] for item in harness.VISUAL_CASES.values()
    } == {"完成当前界面上的测试。"}
    leaked_recipe = " ".join(
        item["task"] for item in harness.VISUAL_CASES.values()
    ).casefold()
    assert all(
        token not in leaked_recipe
        for token in (
            "hold", "checkpoint", "goal", "圆盘", "滑块", "拖", "按住", "释放",
        )
    )
    assert "after_submit" in inspect.signature(
        harness.client_harness.run_case,
    ).parameters
    assert "seed" in inspect.signature(harness.run_visual_case).parameters
    assert "植物大战僵尸" not in " ".join(
        item["task"] for item in harness.VISUAL_CASES.values()
    )


def test_oracle_wait_ignores_page_ready_until_success_is_persisted():
    harness = _load_harness()

    assert harness._trusted_scenario_succeeded({
        "success": False,
        "events": [{"type": "page_ready"}],
    }) is False
    assert harness._trusted_scenario_succeeded({
        "success": True,
        "events": [
            {"type": "page_ready"},
            {"type": "scenario_success", "trusted": True},
        ],
    }) is True


def test_failed_external_observation_returns_final_state_instead_of_raising(
    monkeypatch,
):
    harness = _load_harness()
    state = {
        "ready": True,
        "success": False,
        "case_id": "case-1",
        "scenario": "precise_click",
        "seed": 17,
        "events": [],
    }
    monkeypatch.setattr(
        harness,
        "_poll_json",
        lambda *_args, **_kwargs: (state, False, True, None),
    )

    observed, verdict = harness.observe_external_verdict(
        "http://example.invalid/state",
        case_id="case-1",
        scenario="precise_click",
        seed=17,
    )

    assert observed is state
    assert verdict["passed"] is False
    assert verdict["oracle_status"] == "conclusive"
    assert verdict["success_observed_before_timeout"] is False
    assert verdict["errors"][0] == (
        "trusted scenario success was not observed before timeout"
    )


def test_failed_external_observation_is_unavailable_after_transport_loss(
    monkeypatch,
):
    harness = _load_harness()
    stale_state = {
        "ready": True,
        "success": False,
        "case_id": "case-1",
        "scenario": "precise_click",
        "seed": 17,
        "events": [],
    }
    monkeypatch.setattr(
        harness,
        "_poll_json",
        lambda *_args, **_kwargs: (
            stale_state,
            False,
            False,
            "ConnectionError: unavailable",
        ),
    )

    _observed, verdict = harness.observe_external_verdict(
        "http://example.invalid/state",
        case_id="case-1",
        scenario="precise_click",
        seed=17,
    )

    assert verdict["oracle_status"] == "unavailable"
    assert verdict["oracle_read_error"] == "ConnectionError: unavailable"


def test_unavailable_oracle_cannot_retain_a_positive_verdict(monkeypatch):
    harness = _load_harness()
    monkeypatch.setattr(
        harness,
        "_poll_json",
        lambda *_args, **_kwargs: ({}, True, True, None),
    )
    monkeypatch.setattr(
        harness,
        "external_verdict",
        lambda *_args, **_kwargs: {"passed": True, "errors": []},
    )

    _state_value, verdict = harness.observe_external_verdict(
        "http://example.invalid/state",
        case_id="case-1",
        scenario="precise_click",
        seed=17,
    )

    assert verdict["oracle_status"] == "unavailable"
    assert verdict["passed"] is False


def test_visual_run_matrix_is_deterministic_paired_and_counterbalanced():
    harness = _load_harness()

    matrix = harness.build_run_matrix(
        ("precise-click",),
        seeds=(101, 202),
        paired=True,
    )

    assert matrix == harness.build_run_matrix(
        ("precise-click",),
        seeds=(101, 202),
        paired=True,
    )
    assert [item["pair_id"] for item in matrix] == [
        "precise-click:seed-101",
        "precise-click:seed-101",
        "precise-click:seed-202",
        "precise-click:seed-202",
    ]
    assert [item["runtime_version"] for item in matrix] == [
        "legacy", "vnext", "vnext", "legacy",
    ]
    assert harness._parse_seed_csv("101, 202,101") == (101, 202)


def test_visual_run_matrix_preserves_existing_single_runtime_defaults():
    harness = _load_harness()

    default = harness.build_run_matrix(("precise-click",))
    forced = harness.build_run_matrix(
        ("precise-click",),
        seeds=(99,),
        runtime_version="legacy",
    )

    assert len(default) == 1
    assert default[0]["runtime_version"] == "vnext"
    assert default[0]["seed"] == harness.VISUAL_CASES["precise-click"]["seed"]
    assert default[0]["pair_id"] is None
    assert default[0]["group_id"].startswith("precise-click:seed-")
    assert len(forced) == 1
    assert forced[0]["runtime_version"] == "legacy"
    assert forced[0]["seed"] == 99


def test_visual_run_matrix_rejects_seed_aliases_and_missing_primitive_parity():
    harness = _load_harness()

    with pytest.raises(ValueError, match="4294967295"):
        harness.build_run_matrix(("precise-click",), seeds=(-1,))
    with pytest.raises(ValueError, match="primitive parity"):
        harness.build_run_matrix(("drag-fit",), paired=True)
    with pytest.raises(ValueError, match="primitive parity"):
        harness.build_run_matrix(("keyboard-entry",), paired=True)
    with pytest.raises(ValueError, match="primitive parity"):
        harness.build_run_matrix(("keyboard-select-all",), paired=True)


def test_recovery_aggregate_marks_incomplete_pairs_without_inventing_metrics():
    harness = _load_harness()
    summary = harness.aggregate_recovery_evidence(
        [_aggregate_arm("legacy", oracle_pass=True)],
        git_identity={"revision": "abc", "dirty": False},
    )

    runtime = summary["by_runtime"]["legacy"]
    assert runtime["actor_turn_count"] == {
        "observed_count": 0, "p50": None, "p95": None,
    }
    comparison = summary["paired_comparisons"][0]
    assert comparison["arm_counts"] == {"legacy": 1, "vnext": 0}
    assert comparison["arms_present"] is False
    assert comparison["eligible_for_ab"] is False
    assert comparison["legacy_passed"] is None
    assert summary["eligible_paired_comparison_count"] == 0


def test_recovery_aggregate_does_not_create_pairs_or_fail_denominators():
    harness = _load_harness()
    results = [{
        "pair_id": None,
        "runtime_version": "vnext",
        "passed": True,
        "runner_error": "client unavailable",
        "recovery_evidence": {
            "trace_complete": False,
            "oracle_status": "unavailable",
            "external_oracle_pass": None,
            "wall_elapsed_ms": 1200,
        },
    }]

    summary = harness.aggregate_recovery_evidence(results)

    assert summary["paired_comparisons"] == []
    runtime = summary["by_runtime"]["vnext"]
    assert runtime["attempted_arm_count"] == 1
    assert runtime["runner_error_count"] == 1
    assert runtime["grounded_product_pass_count"] == 0
    assert runtime["oracle"] == {
        "conclusive_count": 0,
        "unavailable_count": 1,
        "pass_count": 0,
        "fail_count": 0,
    }


def test_recovery_aggregate_requires_exact_identity_and_unique_arms():
    harness = _load_harness()

    clean_git = {"revision": "abc", "dirty": False}
    eligible = harness.aggregate_recovery_evidence(
        [_aggregate_arm("legacy"), _aggregate_arm("vnext")],
        git_identity=clean_git,
    )["paired_comparisons"][0]
    assert eligible["eligible_for_ab"] is True
    assert eligible["wall_elapsed_delta_vnext_minus_legacy"] == 20

    mismatched = harness.aggregate_recovery_evidence(
        [
            _aggregate_arm("legacy"),
            _aggregate_arm("vnext", fingerprint="different"),
        ],
        git_identity=clean_git,
    )["paired_comparisons"][0]
    assert mismatched["eligible_for_ab"] is False
    assert mismatched["legacy_passed"] is None

    repeated = harness.aggregate_recovery_evidence(
        [
            _aggregate_arm("legacy"),
            _aggregate_arm("legacy"),
            _aggregate_arm("vnext"),
        ],
        git_identity=clean_git,
    )["paired_comparisons"][0]
    assert repeated["arm_counts"] == {"legacy": 2, "vnext": 1}
    assert repeated["eligible_for_ab"] is False

    missing_stamp = [_aggregate_arm("legacy"), _aggregate_arm("vnext")]
    for arm in missing_stamp:
        for phase in ("before", "after"):
            arm["runtime_service_identity"][phase]["source"].pop(
                "loaded_stamp",
            )
    invalid_runtime = harness.aggregate_recovery_evidence(
        missing_stamp,
        git_identity=clean_git,
    )["paired_comparisons"][0]
    assert invalid_runtime["eligible_for_ab"] is False


def test_trace_recovery_evidence_reads_only_complete_run_scoped_facts(tmp_path):
    harness = _load_harness()
    run_id = "arp-recovery"

    def turn(index, effect, streak):
        return {
            "type": "vnext_mission_runtime_turn",
            "run_id": run_id,
            "turn_index": index,
            "status": "action_recorded",
            "local_visual_change": effect,
            "frame_comparable": True,
            "no_progress_streak": streak,
        }

    events = [
        turn(0, "changed", 0),
        turn(1, "unchanged", 1),
        turn(2, "unknown", 1),
    ]
    events += [{
        "type": "arp_run_done",
        "run_id": run_id,
        "status": "done",
    }]
    trace = tmp_path / "arp-recovery.jsonl"
    trace.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )
    client_result = {
        "trace_binding": {
            "passed": True,
            "run_id": run_id,
            "trace_file": str(trace),
            "parse_errors": [],
        },
        "configuration": {"runtime_version": "vnext"},
        "task_verdict": {"oracle_status": "conclusive", "passed": False},
        "passed": False,
        "duration_seconds": 1.25,
    }

    evidence = harness.trace_recovery_evidence(client_result)

    assert evidence["actor_turn_count"] == 3
    assert evidence["turn_trace_complete"] is True
    assert evidence["effect_counts"] == {
        "changed": 1, "unchanged": 1, "unknown": 1,
    }
    assert evidence["max_unchanged_frame_streak"] == 1
    assert evidence["false_done_count"] == 1
    assert evidence["wall_elapsed_ms"] == 1250

    client_result["task_verdict"]["passed"] = True
    assert harness.trace_recovery_evidence(client_result)["false_done_count"] == 0


def test_trace_recovery_evidence_keeps_unavailable_oracle_three_state(tmp_path):
    harness = _load_harness()
    run_id = "arp-incomplete"
    trace = tmp_path / "arp-incomplete.jsonl"
    trace.write_text("\n".join(json.dumps(item) for item in (
        {
            "type": "vnext_mission_runtime_turn",
            "run_id": run_id,
            "turn_index": 0,
            "status": "action_recorded",
            "local_visual_change": "unchanged",
            "frame_comparable": False,
            "no_progress_streak": 1,
        },
        {
            "type": "arp_run_done",
            "run_id": run_id,
            "status": "done",
        },
    )) + "\n", encoding="utf-8")

    evidence = harness.trace_recovery_evidence({
        "trace_binding": {
            "passed": True,
            "run_id": run_id,
            "trace_file": str(trace),
            "parse_errors": [],
        },
        "configuration": {"runtime_version": "vnext"},
        "task_verdict": {"oracle_status": "unavailable", "passed": False},
    })

    assert evidence["external_oracle_pass"] is None
    assert evidence["false_done_count"] is None
    assert evidence["frame_incomparable_count"] == 1


def test_trace_recovery_evidence_parse_error_nulls_trace_metrics(tmp_path):
    harness = _load_harness()
    trace = tmp_path / "arp-broken.jsonl"
    trace.write_text(
        '{"type":"arp_run_done","run_id":"run-1","status":"done"}\n{',
        encoding="utf-8",
    )

    evidence = harness.trace_recovery_evidence({
        "trace_binding": {
            "passed": True,
            "run_id": "run-1",
            "trace_file": str(trace),
            "parse_errors": [],
        },
        "configuration": {"runtime_version": "vnext"},
        "task_verdict": {"oracle_status": "conclusive", "passed": False},
    })

    assert evidence["trace_complete"] is False
    assert evidence["actor_turn_count"] is None
    assert evidence["false_done_count"] is None
    assert evidence["parse_errors"]


@pytest.mark.parametrize(
    "events",
    (
        [
            {"turn_index": 0, "status": "observed"},
            {"turn_index": 2, "status": "observed"},
        ],
        [{"turn_index": 0, "status": "unknown_status"}],
        [
            {"type": "arp_run_done", "status": "failed"},
            {"turn_index": 0, "status": "observed"},
        ],
    ),
)
def test_turn_trace_rejects_gaps_unknown_status_and_post_terminal_turns(
    tmp_path,
    events,
):
    harness = _load_harness()
    run_id = "run-invalid-turns"
    normalized = []
    for event in events:
        item = dict(event)
        item.setdefault("type", "vnext_mission_runtime_turn")
        item["run_id"] = run_id
        normalized.append(item)
    if not any(item["type"] == "arp_run_done" for item in normalized):
        normalized.append({
            "type": "arp_run_done", "run_id": run_id, "status": "failed",
        })
    trace = tmp_path / "invalid-turns.jsonl"
    trace.write_text(
        "\n".join(json.dumps(item) for item in normalized) + "\n",
        encoding="utf-8",
    )

    evidence = harness.trace_recovery_evidence({
        "trace_binding": {
            "passed": True,
            "run_id": run_id,
            "trace_file": str(trace),
            "parse_errors": [],
        },
        "configuration": {"runtime_version": "vnext"},
        "task_verdict": {"oracle_status": "conclusive", "passed": False},
    })

    assert evidence["trace_complete"] is True
    assert evidence["turn_trace_complete"] is False
    assert evidence["actor_turn_count"] is None


@pytest.mark.parametrize(
    ("terminal_status", "oracle_status", "oracle_pass", "expected"),
    (
        ("done", "conclusive", False, 1),
        ("done", "conclusive", True, 0),
        ("failed", "conclusive", False, 0),
        ("done", "unavailable", False, None),
        ("done", "conclusive", None, None),
    ),
)
def test_false_done_uses_exact_trace_terminal_and_three_state_oracle(
    tmp_path,
    terminal_status,
    oracle_status,
    oracle_pass,
    expected,
):
    harness = _load_harness()
    run_id = f"run-{terminal_status}-{oracle_status}"
    trace = tmp_path / f"{run_id}.jsonl"
    trace.write_text(json.dumps({
        "type": "arp_run_done",
        "run_id": run_id,
        "status": terminal_status,
    }) + "\n", encoding="utf-8")

    evidence = harness.trace_recovery_evidence({
        "trace_binding": {
            "passed": True,
            "run_id": run_id,
            "trace_file": str(trace),
            "parse_errors": [],
        },
        "configuration": {"runtime_version": "legacy"},
        "task_verdict": {
            "oracle_status": oracle_status,
            "passed": oracle_pass,
        },
    })

    assert evidence["false_done_count"] == expected


def test_focus_preserves_visible_target_geometry_and_only_restores_minimized(
    monkeypatch,
):
    harness = _load_harness()
    hwnd = 123
    restored = []

    monkeypatch.setattr(harness.win32gui, "IsWindow", lambda value: value == hwnd)
    monkeypatch.setattr(harness.win32gui, "BringWindowToTop", lambda _value: None)
    monkeypatch.setattr(harness.win32gui, "SetForegroundWindow", lambda _value: None)
    monkeypatch.setattr(harness.win32gui, "GetForegroundWindow", lambda: hwnd)
    monkeypatch.setattr(harness.win32gui, "GetWindowText", lambda _value: "target")
    monkeypatch.setattr(
        harness.win32gui,
        "ShowWindow",
        lambda value, command: restored.append((value, command)),
    )
    monkeypatch.setattr(harness.win32api, "keybd_event", lambda *_args: None)
    monkeypatch.setattr(harness.time, "sleep", lambda _delay: None)

    monkeypatch.setattr(harness.win32gui, "IsIconic", lambda _value: False)
    result = harness._focus_window(hwnd)
    assert result["focused"] is True
    assert restored == []

    monkeypatch.setattr(harness.win32gui, "IsIconic", lambda _value: True)
    harness._focus_window(hwnd)
    assert restored == [(hwnd, harness.win32con.SW_RESTORE)]


def test_target_window_lookup_requires_exact_title_and_launched_process_owner(
    monkeypatch,
):
    harness = _load_harness()
    titles = {
        10: "WinPilot Visual Interaction Target stale",
        20: "WinPilot Visual Interaction Target",
        30: "WinPilot Visual Interaction Target",
    }
    process_ids = {10: 700, 20: 701, 30: 702}

    def enum_windows(visitor, argument):
        for hwnd in titles:
            visitor(hwnd, argument)

    monkeypatch.setattr(harness, "_process_tree_pids", lambda _root: {702})
    monkeypatch.setattr(harness.win32gui, "EnumWindows", enum_windows)
    monkeypatch.setattr(harness.win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(
        harness.win32gui,
        "GetWindowText",
        lambda hwnd: titles[hwnd],
    )
    monkeypatch.setattr(
        harness.win32process,
        "GetWindowThreadProcessId",
        lambda hwnd: (1, process_ids[hwnd]),
    )

    assert harness._find_window(
        "WinPilot Visual Interaction Target",
        owner_root_pid=999,
        timeout=0.1,
    ) == 30


def test_modifier_state_reports_observed_keys_without_mutation(monkeypatch):
    harness = _load_harness()
    calls = []

    def get_state(virtual_key):
        calls.append(virtual_key)
        return 0x8000 if virtual_key == harness.win32con.VK_LCONTROL else 0

    monkeypatch.setattr(harness.win32api, "GetAsyncKeyState", get_state)

    observed = harness._modifier_state()

    assert observed["left_ctrl"] is True
    assert sum(observed.values()) == 1
    assert set(calls) == set(harness.MODIFIER_KEYS.values())


def test_visual_target_defers_ready_geometry_until_first_painted_frame():
    source = (
        Path(__file__).resolve().parents[1]
        / "eval_targets"
        / "visual_interaction_target.html"
    ).read_text(encoding="utf-8")

    assert "let readyDetails = () => ({})" in source
    assert source.count("readyDetails=()=>") == 10
    assert "...readyDetails()" in source
    assert "document.title = 'WinPilot Visual Interaction Target'" in source
    assert "class=\"case-id\"" not in source
    assert "scenario=${scenario}" not in source
    assert "seed=${seed}" not in source
    scroll_setup = source[source.index("function setupScrollReveal()") :]
    assert "const direction=rng()<.5?'up':'down'" in scroll_setup
    assert "initialScrollTop=direction==='down'" in scroll_setup
    assert "if(!event.isTrusted)return" in scroll_setup
    assert source.count("['pointerdown','click','focus','blur']") == 2
    assert source.count("active_target_id:") == 2


def _keyboard_entry_state() -> dict:
    target_id = "entry-field-1"
    expected = "A7"

    def item(event_type, details, *, trusted=False, succeeded=False):
        return {
            "case_id": "case-1", "scenario": "keyboard_entry", "seed": 17,
            "type": event_type, "trusted": trusted, "succeeded": succeeded,
            "details": {"succeeded": succeeded, **details},
        }

    events = [item("page_ready", {
        "target_id": target_id, "expected_text": expected,
        "candidate_count": 3, "initial_value": "",
    })]
    prefix = ""
    for character in expected:
        events.append(item("trusted_beforeinput", {
            "target_id": target_id, "input_type": "insertText",
            "data": character, "is_composing": False,
            "value_before": prefix,
        }, trusted=True))
        prefix += character
        events.append(item("trusted_input", {
            "target_id": target_id, "input_type": "insertText",
            "data": character, "is_composing": False, "value": prefix,
        }, trusted=True))
    success = item("scenario_success", {
        "target_id": target_id, "expected_text": expected,
        "final_value": expected, "beforeinput_count": len(expected),
        "input_count": len(expected), "wrong_target_activity": False,
        "sequence_valid": True,
    }, trusted=True, succeeded=True)
    events.append(success)
    return {
        "ready": True, "success": True, "case_id": "case-1",
        "scenario": "keyboard_entry", "seed": 17,
        "events": events, "success_event": success,
    }


def _keyboard_select_all_state() -> dict:
    target_id = "selection-editor-2"
    original = "amber cedar"

    def item(event_type, details, *, trusted=False, succeeded=False):
        return {
            "case_id": "case-1", "scenario": "keyboard_select_all", "seed": 17,
            "type": event_type, "trusted": trusted, "succeeded": succeeded,
            "details": {"succeeded": succeeded, **details},
        }

    def key(event_type, name, code, ctrl, *, selected=False):
        return item(event_type, {
            "target_id": target_id, "key": name, "code": code,
            "ctrl_key": ctrl, "alt_key": False, "shift_key": False,
            "meta_key": False, "repeat": False,
            "selection_start": 0,
            "selection_end": len(original) if selected else 0,
            "content": original,
        }, trusted=True)

    events = [item("page_ready", {
        "target_id": target_id, "original_text": original,
        "candidate_count": 3, "selection_start": 0, "selection_end": 0,
    })]
    events.extend((
        key("trusted_keydown", "Control", "ControlLeft", True),
        key("trusted_keydown", "a", "KeyA", True),
        key("trusted_keyup", "a", "KeyA", True, selected=True),
        key("trusted_keyup", "Control", "ControlLeft", False, selected=True),
    ))
    success = item("scenario_success", {
        "target_id": target_id, "original_text": original,
        "final_text": original, "selection_start": 0,
        "selection_end": len(original), "modifier_released": True,
        "mutation_count": 0, "wrong_target_activity": False,
        "sequence_valid": True,
    }, trusted=True, succeeded=True)
    events.append(success)
    return {
        "ready": True, "success": True, "case_id": "case-1",
        "scenario": "keyboard_select_all", "seed": 17,
        "events": events, "success_event": success,
    }


def test_external_verdict_accepts_exact_trusted_keyboard_entry_chain():
    harness = _load_harness()
    verdict = harness.external_verdict(
        _keyboard_entry_state(),
        case_id="case-1", scenario="keyboard_entry", seed=17,
    )
    assert verdict["passed"] is True


def test_keyboard_focus_diagnostics_do_not_change_oracle_acceptance():
    harness = _load_harness()
    state = _keyboard_entry_state()
    ready = state["events"][0]
    state["events"][1:1] = [
        {
            "case_id": ready["case_id"],
            "scenario": ready["scenario"],
            "seed": ready["seed"],
            "type": "trusted_focus",
            "trusted": True,
            "succeeded": False,
            "details": {
                "target_id": ready["details"]["target_id"],
                "active_target_id": ready["details"]["target_id"],
                "value_length": 0,
                "selection_start": 0,
                "selection_end": 0,
            },
        }
    ]

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="keyboard_entry", seed=17,
    )

    assert verdict["passed"] is True


def test_external_observation_drains_reports_queued_after_success(monkeypatch):
    harness = _load_harness()
    initial = _keyboard_entry_state()
    settled = copy.deepcopy(initial)
    settled["events"].append(copy.deepcopy(settled["events"][1]))
    monkeypatch.setattr(
        harness,
        "_poll_json",
        lambda *_args, **_kwargs: (initial, True, True, None),
    )
    monkeypatch.setattr(
        harness,
        "_settle_external_state",
        lambda *_args, **_kwargs: (settled, True, True, None),
    )

    observed, verdict = harness.observe_external_verdict(
        "http://example.invalid/state",
        case_id="case-1", scenario="keyboard_entry", seed=17,
    )

    assert observed is settled
    assert verdict["event_stream_quiet"] is True
    assert verdict["passed"] is False
    assert "keyboard entry changed after terminal success" in verdict["errors"]


@pytest.mark.parametrize(
    "mutation",
    (
        "paste",
        "wrong_target",
        "untrusted",
        "composing",
        "append",
        "after_success",
        "causal_before_ready",
        "duplicate_ready",
    ),
)
def test_external_verdict_rejects_unsafe_keyboard_entry_chains(mutation):
    harness = _load_harness()
    state = _keyboard_entry_state()
    if mutation == "paste":
        state["events"].insert(1, {
            **copy.deepcopy(state["events"][1]), "type": "trusted_paste",
        })
    elif mutation == "wrong_target":
        state["events"][1]["details"]["target_id"] = "entry-field-0"
    elif mutation == "untrusted":
        state["events"][1]["trusted"] = False
    elif mutation == "composing":
        state["events"][1]["details"]["is_composing"] = True
    elif mutation == "append":
        state["events"][0]["details"]["initial_value"] = "old"
    elif mutation == "causal_before_ready":
        state["events"][0], state["events"][1] = (
            state["events"][1], state["events"][0],
        )
    elif mutation == "duplicate_ready":
        state["events"].insert(1, copy.deepcopy(state["events"][0]))
    else:
        state["events"].append(copy.deepcopy(state["events"][1]))

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="keyboard_entry", seed=17,
    )
    assert verdict["passed"] is False


def test_external_verdict_accepts_complete_trusted_select_all_chord():
    harness = _load_harness()
    verdict = harness.external_verdict(
        _keyboard_select_all_state(),
        case_id="case-1", scenario="keyboard_select_all", seed=17,
    )
    assert verdict["passed"] is True


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_release",
        "wrong_target",
        "repeat",
        "partial_selection",
        "content_changed",
        "clipboard",
        "untrusted",
        "causal_before_ready",
        "duplicate_ready",
        "missing_ready_selection",
        "non_integer_selection",
    ),
)
def test_external_verdict_rejects_invalid_select_all_chains(mutation):
    harness = _load_harness()
    state = _keyboard_select_all_state()
    if mutation == "missing_release":
        del state["events"][-2]
    elif mutation == "wrong_target":
        state["events"][2]["details"]["target_id"] = "selection-editor-0"
    elif mutation == "repeat":
        state["events"][2]["details"]["repeat"] = True
    elif mutation == "partial_selection":
        state["events"][3]["details"]["selection_end"] -= 1
    elif mutation == "content_changed":
        state["success_event"]["details"]["final_text"] += "!"
    elif mutation == "clipboard":
        state["events"].insert(1, {
            **copy.deepcopy(state["events"][1]), "type": "trusted_paste",
        })
    elif mutation == "causal_before_ready":
        state["events"][0], state["events"][1] = (
            state["events"][1], state["events"][0],
        )
    elif mutation == "duplicate_ready":
        state["events"].insert(1, copy.deepcopy(state["events"][0]))
    elif mutation == "missing_ready_selection":
        state["events"][0]["details"].pop("selection_start")
        state["events"][0]["details"].pop("selection_end")
    elif mutation == "non_integer_selection":
        state["events"][3]["details"]["selection_start"] = False
        state["events"][3]["details"]["selection_end"] = float(
            state["events"][3]["details"]["selection_end"],
        )
    else:
        state["events"][2]["trusted"] = False

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="keyboard_select_all", seed=17,
    )
    assert verdict["passed"] is False


@pytest.mark.parametrize(
    ("build", "scenario", "field", "value"),
    (
        (_keyboard_entry_state, "keyboard_entry", "case_id", "other-case"),
        (_keyboard_entry_state, "keyboard_entry", "scenario", "other_scenario"),
        (_keyboard_entry_state, "keyboard_entry", "seed", 18),
        (_keyboard_select_all_state, "keyboard_select_all", "case_id", "other-case"),
        (_keyboard_select_all_state, "keyboard_select_all", "scenario", "other_scenario"),
        (_keyboard_select_all_state, "keyboard_select_all", "seed", 18),
    ),
)
def test_keyboard_causal_evidence_cannot_cross_runtime_identity(
    build, scenario, field, value,
):
    harness = _load_harness()
    state = build()
    state["events"][1][field] = value

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario=scenario, seed=17,
    )

    assert verdict["passed"] is False


def _scroll_reveal_state() -> dict:
    pane = {
        "left": 100, "top": 100, "right": 500, "bottom": 350,
        "width": 400, "height": 250,
    }
    ready_target = {
        "left": 150, "top": 800, "right": 450, "bottom": 888,
        "width": 300, "height": 88,
    }
    target = {
        "left": 150, "top": 250, "right": 450, "bottom": 338,
        "width": 300, "height": 88,
    }
    details = {
        "direction": "down",
        "initial_scroll_top": 100,
        "before_scroll_top": 600,
        "after_scroll_top": 650,
        "scroll_delta": 50,
        "total_scroll_delta": 550,
        "max_scroll_top": 900,
        "wheel_delta_y": 120,
        "wheel_count": 6,
        "pane": dict(pane),
        "pane_viewport": dict(pane),
        "target": dict(target),
        "target_fully_visible": True,
    }
    state = _state("scroll_reveal", details, moves=0)
    success = state["success_event"]
    success["client_x"] = 300
    success["client_y"] = 200
    ready = {
        "case_id": "case-1",
        "scenario": "scroll_reveal",
        "seed": 17,
        "type": "page_ready",
        "trusted": False,
        "details": {
            "succeeded": False,
            "direction": "down",
            "initial_scroll_top": 100,
            "max_scroll_top": 900,
            "pane": dict(pane),
            "pane_viewport": dict(pane),
            "target": ready_target,
            "target_fully_visible": False,
        },
    }
    wheel = {
        "case_id": "case-1",
        "scenario": "scroll_reveal",
        "seed": 17,
        "type": "trusted_wheel",
        "trusted": True,
        "client_x": 300,
        "client_y": 200,
        "succeeded": False,
        "details": {"succeeded": False, **copy.deepcopy(details)},
    }
    state["events"] = [ready, wheel, success]
    return state


def test_external_verdict_accepts_trusted_scroll_reveal_transition():
    harness = _load_harness()
    state = _scroll_reveal_state()

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="scroll_reveal", seed=17,
    )

    assert verdict["passed"] is True


def test_external_verdict_accepts_upward_scroll_reveal_transition():
    harness = _load_harness()
    state = _scroll_reveal_state()
    ready_target = {
        "left": 150, "top": -350, "right": 450, "bottom": -262,
        "width": 300, "height": 88,
    }
    target = {
        "left": 150, "top": 150, "right": 450, "bottom": 238,
        "width": 300, "height": 88,
    }
    ready_details = state["events"][0]["details"]
    ready_details.update({
        "direction": "up",
        "initial_scroll_top": 800,
        "target": ready_target,
    })
    for details in (
        state["success_event"]["details"], state["events"][1]["details"],
    ):
        details.update({
            "direction": "up",
            "initial_scroll_top": 800,
            "before_scroll_top": 350,
            "after_scroll_top": 300,
            "scroll_delta": -50,
            "total_scroll_delta": -500,
            "wheel_delta_y": -120,
            "target": dict(target),
        })

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="scroll_reveal", seed=17,
    )

    assert verdict["passed"] is True


def test_external_verdict_rejects_programmatic_scroll_reveal():
    harness = _load_harness()
    state = _scroll_reveal_state()
    state["success_event"]["trusted"] = False
    state["events"][1]["trusted"] = False

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="scroll_reveal", seed=17,
    )

    assert verdict["passed"] is False
    assert "scroll reveal has no trusted wheel evidence" in verdict["errors"]


def test_external_verdict_rejects_wrong_scroll_reveal_direction():
    harness = _load_harness()
    state = _scroll_reveal_state()
    state["success_event"]["details"]["wheel_delta_y"] = -120
    state["events"][1]["details"]["wheel_delta_y"] = -120

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="scroll_reveal", seed=17,
    )

    assert verdict["passed"] is False
    assert "trusted wheel moved in the wrong reveal direction" in verdict["errors"]


def test_external_verdict_rejects_wheel_without_scrolltop_change():
    harness = _load_harness()
    state = _scroll_reveal_state()
    for details in (
        state["success_event"]["details"], state["events"][1]["details"],
    ):
        details["before_scroll_top"] = 650
        details["scroll_delta"] = 0

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="scroll_reveal", seed=17,
    )

    assert verdict["passed"] is False
    assert "trusted wheel did not change scrollTop" in verdict["errors"]


def test_external_verdict_rejects_partially_visible_scroll_target():
    harness = _load_harness()
    state = _scroll_reveal_state()
    partial = {
        "left": 150, "top": 300, "right": 450, "bottom": 388,
        "width": 300, "height": 88,
    }
    state["success_event"]["details"]["target"] = dict(partial)
    state["events"][1]["details"]["target"] = dict(partial)

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="scroll_reveal", seed=17,
    )

    assert verdict["passed"] is False
    assert "scroll target is not completely visible in the pane" in verdict["errors"]


def test_external_verdict_requires_exact_precise_click_identity_and_geometry():
    harness = _load_harness()
    target = {
        "left": 120,
        "top": 80,
        "right": 182,
        "bottom": 142,
        "width": 62,
        "height": 62,
    }
    state = _state(
        "precise_click",
        {"target_index": 7, "target": dict(target)},
        moves=0,
    )
    state["success_event"]["client_x"] = 151
    state["success_event"]["client_y"] = 111
    state["events"].insert(0, {
        "case_id": "case-1",
        "scenario": "precise_click",
        "seed": 17,
        "type": "page_ready",
        "trusted": False,
        "details": {
            "target_index": 7,
            "target": dict(target),
            "candidate_count": 12,
            "distractor_count": 11,
        },
    })

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="precise_click", seed=17,
    )

    assert verdict["passed"] is True
    state["success_event"]["details"]["target"]["left"] += 1
    failed = harness.external_verdict(
        state, case_id="case-1", scenario="precise_click", seed=17,
    )
    assert failed["passed"] is False
    assert "success target geometry does not match page-ready identity" in failed["errors"]


def test_external_verdict_rejects_precise_click_movement_and_wrong_target_identity():
    harness = _load_harness()
    target = {
        "left": 120,
        "top": 80,
        "right": 182,
        "bottom": 142,
        "width": 62,
        "height": 62,
    }
    state = _state(
        "precise_click",
        {"target_index": 12, "target": dict(target)},
        moves=1,
    )
    state["success_event"]["client_x"] = 151
    state["success_event"]["client_y"] = 111
    state["events"].insert(0, {
        "case_id": "case-1",
        "scenario": "precise_click",
        "seed": 17,
        "type": "page_ready",
        "trusted": False,
        "details": {
            "target_index": 7,
            "target": dict(target),
            "candidate_count": 12,
            "distractor_count": 11,
        },
    })

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="precise_click", seed=17,
    )

    assert verdict["passed"] is False
    assert "precise click unexpectedly recorded pointer movement" in verdict["errors"]
    assert "precise click target identity is invalid" in verdict["errors"]


def test_external_verdict_requires_complete_object_placement():
    harness = _load_harness()
    state = _state(
        "drag_fit",
        {
            "token": {"left": 30, "top": 30, "right": 60, "bottom": 60},
            "target": {"left": 20, "top": 20, "right": 70, "bottom": 70},
        },
        moves=4,
    )

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="drag_fit", seed=17,
    )

    assert verdict["passed"] is True
    state["success_event"]["details"]["token"]["right"] = 80
    failed = harness.external_verdict(
        state, case_id="case-1", scenario="drag_fit", seed=17,
    )
    assert failed["passed"] is False
    assert "object is not completely inside target" in failed["errors"]


def test_external_verdict_checks_slider_band_and_corridor_path():
    harness = _load_harness()
    slider = _state(
        "slider",
        {"center_x": 55, "goal": {"left": 45, "right": 65}},
        moves=3,
    )
    assert harness.external_verdict(
        slider, case_id="case-1", scenario="slider", seed=17,
    )["passed"] is True

    corridor = _state(
        "corridor_drag",
        {
            "path_valid": True,
            "min_clearance": 7,
            "path_points": [[1, 1], [2, 2], [3, 3], [4, 4]],
        },
        moves=8,
    )
    corridor["events"].insert(0, {
        "type": "page_ready",
        "details": {"variant": 2},
    })
    assert harness.external_verdict(
        corridor,
        case_id="case-1",
        scenario="corridor_drag",
        seed=17,
        expected_variant=2,
    )["passed"] is True

    corridor["success_event"]["details"]["path_valid"] = False
    failed = harness.external_verdict(
        corridor,
        case_id="case-1",
        scenario="corridor_drag",
        seed=17,
        expected_variant=2,
    )
    assert failed["passed"] is False
    assert "gesture left the visible corridor" in failed["errors"]


def test_corridor_variant_zero_and_zero_clearance_are_valid():
    harness = _load_harness()
    corridor = _state(
        "corridor_drag",
        {
            "path_valid": True,
            "min_clearance": 0,
            "path_points": [[1, 1], [2, 2], [3, 3], [4, 4]],
        },
        moves=8,
    )
    corridor["events"].insert(0, {
        "type": "page_ready",
        "details": {"variant": 0},
    })

    verdict = harness.external_verdict(
        corridor,
        case_id="case-1",
        scenario="corridor_drag",
        seed=17,
        expected_variant=0,
    )

    assert verdict["passed"] is True


def test_external_verdict_requires_one_cross_frame_held_pointer_gesture():
    harness = _load_harness()
    dynamic = _state(
        "dynamic_hold",
        {
            "down_count": 1,
            "checkpoint_reached": True,
            "goal_revealed_after_down": True,
            "inside_goal": True,
            "completion_event": "pointerup",
        },
        moves=3,
    )
    dynamic["events"].insert(0, {
        "type": "pointerdown",
        "trusted": True,
        "details": {"down_count": 1},
    })
    dynamic["events"].insert(1, {
        "type": "checkpoint_reached",
        "trusted": True,
        "details": {"checkpoint_reached": True},
    })
    dynamic["events"].insert(2, {
        "type": "pointerup",
        "trusted": True,
        "succeeded": True,
        "details": {
            "succeeded": True,
            "down_count": 1,
            "checkpoint_reached": True,
            "goal_revealed_after_down": True,
            "inside_goal": True,
            "completion_event": "pointerup",
        },
    })

    verdict = harness.external_verdict(
        dynamic,
        case_id="case-1",
        scenario="dynamic_hold",
        seed=17,
    )

    assert verdict["passed"] is True
    dynamic["events"].insert(0, {
        "type": "pointerdown",
        "trusted": True,
        "details": {"down_count": 2},
    })
    failed = harness.external_verdict(
        dynamic,
        case_id="case-1",
        scenario="dynamic_hold",
        seed=17,
    )
    assert failed["passed"] is False
    assert "dynamic gesture was not completed with exactly one held press" in failed["errors"]


def test_external_verdict_rejects_goal_entry_without_trusted_pointerup():
    harness = _load_harness()
    dynamic = _state(
        "dynamic_hold",
        {
            "down_count": 1,
            "checkpoint_reached": True,
            "goal_revealed_after_down": True,
            "inside_goal": True,
            "completion_event": "pointerup",
        },
        moves=5,
    )
    dynamic["events"][:0] = [
        {"type": "pointerdown", "trusted": True, "details": {"down_count": 1}},
        {
            "type": "checkpoint_reached",
            "trusted": True,
            "details": {"checkpoint_reached": True},
        },
    ]

    verdict = harness.external_verdict(
        dynamic,
        case_id="case-1",
        scenario="dynamic_hold",
        seed=17,
    )

    assert verdict["passed"] is False
    assert "dynamic gesture has no unique trusted pointerup evidence" in verdict["errors"]


def test_dynamic_target_persists_pointerup_before_success():
    target = (
        Path(__file__).resolve().parents[1]
        / "eval_targets"
        / "visual_interaction_target.html"
    ).read_text(encoding="utf-8")
    dynamic_setup = target[target.index("function setupDynamicHold()") :]

    assert dynamic_setup.index("await report('pointerup',event,details)") < dynamic_setup.index(
        "markSuccess('DYNAMIC_HOLD_SUCCEEDED'",
    )


def test_external_verdict_rejects_agent_claim_without_trusted_pointer_event():
    harness = _load_harness()
    state = _state(
        "slider",
        {"center_x": 55, "goal": {"left": 45, "right": 65}},
        moves=3,
    )
    state["success_event"]["trusted"] = False

    verdict = harness.external_verdict(
        state, case_id="case-1", scenario="slider", seed=17,
    )

    assert verdict["passed"] is False
    assert "success event is not trusted and successful" in verdict["errors"]
