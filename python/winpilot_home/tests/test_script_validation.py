"""validate_script (pure) + GET/PUT /api/script HTTP round-trip."""
import base64
import json

import pytest

from winpilot.recorder import trace as trace_mod


# ----------------------------------------------------------- validate_script
def test_structured_evidence_validation_rejects_unknown_fields():
    assert trace_mod.validate_evidence_requirements([
        {"predicate": "visible.text", "value": "done", "typo": True},
    ]) == "evidence requirement 1 has unsupported fields"
    assert trace_mod.validate_evidence_requirements([
        {"text_appears": "done", "timeout_s": 4},
    ]) is None
    assert trace_mod.validate_evidence_requirements([
        {"text_appears": "done", "timeout_s": "later"},
    ]) == "evidence requirement 1 has invalid timeout_s"
    assert trace_mod.evidence_expectation(
        {"text_appears": "done", "timeout_s": 4},
    ) == {"text_appears": "done", "timeout_s": 4}


def test_invalid_canonical_script_does_not_hide_valid_wps_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "SCRIPTS_DIR", tmp_path)
    (tmp_path / "lesson.automation.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "lesson.wps.json").write_text(
        json.dumps({"format": "wps/2", "steps": [{"tool": "ext.delay", "seconds": 0}]}),
        encoding="utf-8",
    )

    scripts = {item["file"]: item for item in trace_mod.list_scripts()}

    assert scripts["lesson.automation.json"]["valid"] is False
    assert scripts["lesson.wps.json"]["valid"] is True


def test_valid_v1_script():
    assert trace_mod.validate_script(
        {"format": "wps/1", "steps": [{"tool": "click", "coords": [1, 2]}]}) is None


def test_valid_v2_nested_script():
    script = {
        "format": "wps/2",
        "vars": {"a": "1"},
        "steps": [
            {"tool": "ctrl.if",
             "cond": {"always": True},
             "then": [{"tool": "click", "coords": [1, 2]}],
             "else": [{"tool": "ext.delay", "seconds": 1}]},
            {"tool": "ctrl.loop",
             "body": [{"tool": "type_text", "text_input": "hello"}]},
        ],
    }
    assert trace_mod.validate_script(script) is None


def test_valid_agent_replay_only_tools():
    script = {"format": "wps/2", "steps": [
        {"tool": "draw_strokes", "strokes": [[[1, 2], [3, 4]]], "duration": 0.1},
        {"tool": "run_detached", "script": "Start-Sleep 1", "name": "demo"},
        {"tool": "copy_current_view", "reference_frame": {"pending_path": "run/view.jpg"}},
    ]}
    assert trace_mod.validate_script(script) is None


def test_reject_bad_format():
    assert "format" in trace_mod.validate_script({"format": "x", "steps": []})


def test_reject_non_dict():
    assert trace_mod.validate_script([1, 2]) is not None


def test_reject_steps_not_list():
    assert "steps" in trace_mod.validate_script({"format": "wps/2", "steps": {}})


def test_reject_unknown_tool():
    err = trace_mod.validate_script(
        {"format": "wps/2", "steps": [{"tool": "evil.exec"}]})
    assert "evil.exec" in err


def test_reject_unknown_tool_nested_in_branch():
    script = {"format": "wps/2", "steps": [
        {"tool": "ctrl.if", "cond": {"always": True}, "then": [{"tool": "nope"}]},
    ]}
    err = trace_mod.validate_script(script)
    assert "nope" in err and "then" in err


def test_reject_if_without_cond():
    err = trace_mod.validate_script(
        {"format": "wps/2", "steps": [{"tool": "ctrl.if", "then": [{"tool": "click", "coords": [1, 2]}]}]})
    assert "ctrl.if requires cond" in err


def test_allow_if_with_explicit_always_cond():
    assert trace_mod.validate_script(
        {"format": "wps/2", "steps": [{
            "tool": "ctrl.if",
            "cond": {"always": True},
            "then": [{"tool": "ext.delay", "seconds": 0}],
        }]}) is None


def test_reject_script_without_any_executable_leaf():
    assert "executable leaf" in trace_mod.validate_script(
        {"format": "wps/2", "steps": []})
    assert "executable leaf" in trace_mod.validate_script({
        "format": "wps/2",
        "steps": [{
            "tool": "ctrl.if",
            "cond": {"always": True},
            "then": [],
            "else": [{"tool": "ctrl.loop", "count": 2, "body": []}],
        }],
    })
    assert "executable leaf" in trace_mod.validate_script({
        "format": "wps/2",
        "steps": [{
            "tool": "ctrl.loop",
            "loop_type": "count",
            "count": 0,
            "body": [{"tool": "ext.delay", "seconds": 0}],
        }],
    })


def test_reject_missing_required_leaf_fields():
    assert "click requires" in trace_mod.validate_script(
        {"format": "wps/2", "steps": [{"tool": "click"}]})
    assert "text_input" in trace_mod.validate_script(
        {"format": "wps/2", "steps": [{"tool": "type_text"}]})
    assert "draw_strokes" in trace_mod.validate_script(
        {"format": "wps/2", "steps": [{"tool": "draw_strokes", "strokes": [[[1, 2]]]}]})


def test_reject_bad_loop_type():
    err = trace_mod.validate_script(
        {"format": "wps/2", "steps": [{"tool": "ctrl.loop", "loop_type": "forever", "body": []}]})
    assert "loop_type" in err


def test_reject_unbounded_conditional_loop_without_cond():
    err = trace_mod.validate_script(
        {"format": "wps/2", "steps": [{"tool": "ctrl.loop", "loop_type": "until", "body": []}]})
    assert "requires cond" in err
    err = trace_mod.validate_script(
        {"format": "wps/2", "steps": [{"tool": "ctrl.loop", "loop_type": "while", "cond": {}, "body": []}]})
    assert "cond must be non-empty" in err


def test_reject_bad_loop_count_and_max_iters():
    err = trace_mod.validate_script(
        {"format": "wps/2", "steps": [{"tool": "ctrl.loop", "loop_type": "count", "count": "many", "body": []}]})
    assert "count" in err
    err = trace_mod.validate_script(
        {"format": "wps/2", "steps": [{"tool": "ctrl.loop", "max_iters": "many", "body": []}]})
    assert "max_iters" in err


def test_reject_bad_vars():
    assert "vars" in trace_mod.validate_script(
        {"format": "wps/2", "steps": [], "vars": "nope"})


def test_reject_empty_declared_target_window():
    err = trace_mod.validate_script(
        {"format": "wps/2", "target_window": {}, "steps": []})
    assert "target_window requires" in err


def test_reject_empty_step_target_window_at_any_nesting_depth():
    for steps in (
        [{"tool": "click", "coords": [1, 2], "target_window": {}}],
        [{"tool": "ctrl.if", "cond": {"always": True}, "then": [
            {"tool": "hotkey", "keys": ["enter"], "target_window": ""},
        ]}],
    ):
        err = trace_mod.validate_script({"format": "wps/2", "steps": steps})
        assert ".target_window requires" in err


# ------------------------------------------------------------- export_script
def _write_trace(path, events):
    with open(path, "w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"action": "click", "physical_arguments": {
                "kind": "pointer_click", "x": 1, "y": 2,
            }},
            {"tool": "click", "coords": [1, 2]},
        ),
        (
            {"action": "double_click", "physical_arguments": {
                "kind": "pointer_click", "x": 3, "y": 4,
            }},
            {"tool": "click", "coords": [3, 4], "double": True},
        ),
        (
            {"action": "scroll", "physical_arguments": {
                "kind": "wheel", "x": 5, "y": 6, "signed_wheel_amount": -3,
            }},
            {"tool": "scroll", "coords": [5, 6], "amount": -3},
        ),
        (
            {"action": "drag", "physical_arguments": {
                "kind": "pointer_drag", "x1": 1, "y1": 2, "x2": 7, "y2": 8,
                "points": [[3, 4], [5, 6]],
            }},
            {"tool": "drag", "from": [1, 2], "to": [7, 8],
             "points": [[3, 4], [5, 6]]},
        ),
        (
            {"action": "type_text", "physical_arguments": {
                "kind": "type_text", "text": "hello",
            }},
            {"tool": "type_text", "text_input": "hello"},
        ),
        (
            {"action": "hotkey", "physical_arguments": {
                "kind": "hotkey", "modifiers": ["ctrl", "shift"], "key": "s",
            }},
            {"tool": "hotkey", "keys": ["ctrl", "shift", "s"]},
        ),
        (
            {"action": "move_mouse", "physical_arguments": {
                "kind": "pointer_move", "x": 11, "y": 12, "duration_ms": 30,
            }},
            {"tool": "move_mouse", "coords": [11, 12], "duration_ms": 30},
        ),
        (
            {"action": "input_sequence", "physical_arguments": {
                "kind": "input_sequence",
                "events": [{"kind": "key_down", "key": "shift"}],
            }},
            {"tool": "input_sequence", "events": [
                {"kind": "key_down", "key": "shift"},
            ]},
        ),
        (
            {"action": "launch_application", "physical_arguments": {
                "kind": "launch_application", "app": "notepad", "arguments": "a.txt",
            }},
            {"tool": "launch_application", "app": "notepad", "arguments": "a.txt"},
        ),
    ],
)
def test_vnext_physical_attempt_maps_to_plain_wps(payload, expected):
    assert trace_mod._physical_attempt_to_wps(payload) == expected


def test_redacted_literal_sequence_is_not_exported_as_a_false_replay():
    attempt = {
        "action": "input_sequence",
        "physical_arguments": {
            "kind": "input_sequence",
            "events": [
                {"kind": "type_text", "codepoint_count": 16},
                {"kind": "key_down", "key": "enter"},
            ],
        },
    }

    assert trace_mod._physical_attempt_to_wps(attempt) is None

    attempt["physical_arguments"]["events"][0]["text"] = "deepseek harness"
    assert trace_mod._physical_attempt_to_wps(attempt) == {
        "tool": "input_sequence",
        "events": [
            {
                "kind": "type_text",
                "codepoint_count": 16,
                "text": "deepseek harness",
            },
            {"kind": "key_down", "key": "enter"},
        ],
    }


def test_vnext_trace_steps_keeps_visible_activity_as_note():
    decision_id = "decision-readable"
    events = [
        {
            "type": "live_agent_activity",
            "kind": "message",
            "content": "The result is visible. Click the Save button.",
        },
        {
            "type": "vnext_physical_attempt",
            "run_id": "run-readable",
            "decision_id": decision_id,
            "action": "click",
            "physical_arguments": {
                "kind": "pointer_click", "x": 10, "y": 20,
            },
            "target_identity": {},
            "timestamp_ns": 1,
        },
        {
            "type": "vnext_raw_receipt",
            "run_id": "run-readable",
            "decision_id": decision_id,
            "input_completed": True,
            "controller_action": "click",
            "raw_action": "click",
        },
    ]

    steps, reasons, is_vnext = trace_mod._vnext_trace_steps(
        "run-readable", events,
    )

    assert is_vnext is True
    assert steps == [{
        "tool": "click",
        "coords": [10, 20],
        "note": "The result is visible. Click the Save button.",
    }]
    assert reasons == []


def test_vnext_drag_preserves_the_real_drag_and_hold_shape():
    attempt = {
        "action": "drag",
        "physical_arguments": {
            "kind": "pointer_drag",
            "x1": 1,
            "y1": 2,
            "x2": 7,
            "y2": 8,
            "started_held": False,
            "keep_holding": False,
        },
    }

    assert trace_mod._physical_attempt_to_wps(attempt) == {
        "tool": "drag",
        "from": [1, 2],
        "to": [7, 8],
        "started_held": False,
        "keep_holding": False,
    }
    for field in ("started_held", "keep_holding"):
        held = json.loads(json.dumps(attempt))
        held["physical_arguments"][field] = True
        projected = trace_mod._physical_attempt_to_wps(held)
        assert projected["tool"] == "drag"
        assert projected[field] is True


def test_vnext_trace_orders_local_utilities_with_physical_actions():
    events = [
        {"type": "live_agent_activity", "kind": "tool_start", "tool": "run_shell",
         "arguments": {"command": "Get-Date", "shell": "powershell"}, "ts": 1},
        {"type": "vnext_physical_attempt", "run_id": "run-all", "decision_id": "d1",
         "action": "launch_application", "timestamp_ns": 2_000_000_000,
         "physical_arguments": {"kind": "launch_application", "app": "notepad",
                                "arguments": ""}, "target_identity": {}},
        {"type": "vnext_raw_receipt", "run_id": "run-all", "decision_id": "d1",
         "input_completed": True, "controller_action": "launch_application",
         "raw_action": "launch_application"},
        {"type": "live_agent_activity", "kind": "tool_result",
         "tool": "copy_current_view",
         "arguments": {"replay_frame": "run-all/view_frame_d1.jpg"}, "ts": 2.5},
        {"type": "live_agent_activity", "kind": "tool_start", "tool": "capture_screen",
         "arguments": {"path": "C:/tmp/frame.png", "window": False}, "ts": 3},
    ]

    steps, _reasons, is_vnext = trace_mod._vnext_trace_steps("run-all", events)

    assert is_vnext is True
    assert [step["tool"] for step in steps] == [
        "run_shell", "launch_application", "copy_current_view", "capture_screen",
    ]
    assert steps[0]["command"] == "Get-Date"
    assert steps[2]["reference_frame"] == {
        "pending_path": "run-all/view_frame_d1.jpg",
    }
    assert steps[3]["path"] == "C:/tmp/frame.png"


def test_trace_list_counts_physical_and_utility_replay_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    _write_trace(tmp_path / "run-count.jsonl", [
        {"type": "vnext_raw_receipt", "decision_id": "d1", "input_completed": True},
        {"type": "live_agent_activity", "kind": "tool_start", "tool": "run_shell",
         "arguments": {"command": "Get-Date"}},
        {"type": "live_agent_activity", "kind": "tool_start", "tool": "capture_screen",
         "arguments": {"path": "C:/tmp/a.png"}},
        {"type": "live_agent_activity", "kind": "tool_result",
         "tool": "copy_current_view",
         "arguments": {"replay_frame": "run-count/view_frame_d1.jpg"}},
    ])

    assert trace_mod.list_traces()[0]["steps"] == 4


def test_export_script_includes_batch_child_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(trace_mod, "SCRIPTS_DIR", tmp_path / "scripts")
    _write_trace(tmp_path / "r1.jsonl", [
        {"type": "agent_start", "task": "batch"},
        {"type": "agent_batch_action", "tool": "click",
         "replay": {"tool": "click", "coords": [7, 8], "elapsed_ms": 5}},
        {"type": "agent_tool", "tool": "run_action_batch"},
    ])

    path = trace_mod.export_script("r1")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["format"] == "wps/2"
    assert data["steps"] == [{"tool": "click", "coords": [7, 8]}]
    assert trace_mod.validate_script(data) is None


def test_export_attaches_exact_model_click_frame_and_consumes_cache(
    tmp_path,
    monkeypatch,
):
    traces = tmp_path / "traces"
    scripts = tmp_path / "scripts"
    run_dir = traces / "run-frame"
    run_dir.mkdir(parents=True)
    frame = run_dir / "click_frame_d1.jpg"
    frame.write_bytes(b"\xff\xd8exact-model-frame\xff\xd9")
    view_frame = run_dir / "view_frame_d2.jpg"
    view_frame.write_bytes(b"\xff\xd8exact-copied-view\xff\xd9")
    (run_dir / trace_mod._REPLAY_FRAME_MANIFEST).write_text(
        json.dumps({"run_id": "run-frame", "thread_id": "thread-1", "created": 1}),
        encoding="utf-8",
    )
    monkeypatch.setattr(trace_mod, "TRACES_DIR", traces)
    monkeypatch.setattr(trace_mod, "SCRIPTS_DIR", scripts)
    _write_trace(traces / "run-frame.jsonl", [
        {"type": "vnext_physical_attempt", "run_id": "run-frame",
         "decision_id": "d1", "action": "click", "timestamp_ns": 1,
         "physical_arguments": {"kind": "pointer_click", "x": 600, "y": 300},
         "submitted_image_size": [1120, 630], "frame_region": [0, 0, 1920, 1080],
         "image_coordinates": {"image_x": 350, "image_y": 175},
         "frame_id": 9, "replay_frame": "run-frame/click_frame_d1.jpg",
         "target_identity": {}},
        {"type": "vnext_raw_receipt", "run_id": "run-frame", "decision_id": "d1",
         "input_completed": True, "controller_action": "click", "raw_action": "click"},
        {"type": "live_agent_activity", "kind": "tool_result",
         "tool": "copy_current_view", "ts": 2,
         "arguments": {"replay_frame": "run-frame/view_frame_d2.jpg"}},
    ])

    path = trace_mod.export_script("run-frame", name="click-sample")
    data = json.loads(path.read_text(encoding="utf-8"))
    reference = data["steps"][0]["reference_frame"]
    asset = scripts / reference["asset"]
    view_reference = data["steps"][1]["reference_frame"]
    view_asset = scripts / view_reference["asset"]

    assert reference["target"] == [350, 175]
    assert reference["image_size"] == [1120, 630]
    assert reference["screen_region"] == [0, 0, 1920, 1080]
    assert reference["path"] == str(asset.resolve())
    assert asset.read_bytes() == b"\xff\xd8exact-model-frame\xff\xd9"
    assert data["steps"][1]["tool"] == "copy_current_view"
    assert view_reference["path"] == str(view_asset.resolve())
    assert view_asset.name == "view-002.jpg"
    assert view_asset.read_bytes() == b"\xff\xd8exact-copied-view\xff\xd9"
    assert not frame.exists()
    assert not view_frame.exists()
    assert (run_dir / trace_mod._REPLAY_FRAME_MANIFEST).exists()
    ir = json.loads((scripts / "click-sample.automation.json").read_text(encoding="utf-8"))
    assert ir["steps"][0]["reference_frame"] == reference
    assert ir["steps"][1]["reference_frame"] == view_reference


def test_pending_click_frames_keep_only_two_latest_agent_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    for created, run_id in enumerate(("run-old", "run-middle", "run-new"), start=1):
        run_dir = tmp_path / run_id
        run_dir.mkdir()
        (run_dir / trace_mod._REPLAY_FRAME_MANIFEST).write_text(
            json.dumps({"run_id": run_id, "thread_id": run_id, "created": created}),
            encoding="utf-8",
        )
        (run_dir / f"{trace_mod._REPLAY_FRAME_PREFIX}d.jpg").write_bytes(b"frame")
        (run_dir / f"{trace_mod._REPLAY_VIEW_FRAME_PREFIX}d.jpg").write_bytes(b"view")

    trace_mod.TraceRecorder._prune_replay_frames()

    assert not (tmp_path / "run-old" / f"{trace_mod._REPLAY_FRAME_PREFIX}d.jpg").exists()
    assert not (tmp_path / "run-old" / f"{trace_mod._REPLAY_VIEW_FRAME_PREFIX}d.jpg").exists()
    assert not (tmp_path / "run-old" / trace_mod._REPLAY_FRAME_MANIFEST).exists()
    assert (tmp_path / "run-middle" / f"{trace_mod._REPLAY_FRAME_PREFIX}d.jpg").exists()
    assert (tmp_path / "run-middle" / f"{trace_mod._REPLAY_VIEW_FRAME_PREFIX}d.jpg").exists()
    assert (tmp_path / "run-new" / f"{trace_mod._REPLAY_FRAME_PREFIX}d.jpg").exists()
    assert (tmp_path / "run-new" / f"{trace_mod._REPLAY_VIEW_FRAME_PREFIX}d.jpg").exists()


def test_exact_click_frame_cache_is_opt_in_and_requires_open_trace(tmp_path, monkeypatch):
    from winpilot.utils import replay_frames

    monkeypatch.setattr(replay_frames, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(
        replay_frames.CONFIG,
        "get",
        lambda *path, default=None: False,
    )
    jpeg = base64.b64encode(b"\xff\xd8model-view\xff\xd9").decode("ascii")
    (tmp_path / "run-open").mkdir()

    assert replay_frames.save_pending_click_frame(
        run_id="run-open", decision_id="click / 1", image_b64=jpeg,
    ) is None
    (tmp_path / "run-open.jsonl").touch()
    assert replay_frames.save_pending_click_frame(
        run_id="run-open", decision_id="click / 1", image_b64=jpeg,
    ) is None
    assert not (tmp_path / "run-open/click_frame_click_1.jpg").exists()
    copied = replay_frames.save_pending_view_frame(
        run_id="run-open", decision_id="copy / 1", image_b64=jpeg,
    )
    assert copied == "run-open/view_frame_copy_1.jpg"
    assert (tmp_path / copied).read_bytes() == b"\xff\xd8model-view\xff\xd9"

    monkeypatch.setattr(
        replay_frames.CONFIG,
        "get",
        lambda *path, default=None: True,
    )
    relative = replay_frames.save_pending_click_frame(
        run_id="run-open", decision_id="click / 1", image_b64=jpeg,
    )

    assert relative == "run-open/click_frame_click_1.jpg"
    assert (tmp_path / relative).read_bytes() == b"\xff\xd8model-view\xff\xd9"


def test_export_script_drops_only_explicit_fast_wrapper_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(trace_mod, "SCRIPTS_DIR", tmp_path / "scripts")
    replay = {"tool": "draw_strokes", "strokes": [[[1, 2], [3, 4]]], "duration": 0.1}
    _write_trace(tmp_path / "r2.jsonl", [
        {"type": "agent_start", "task": "draw"},
        {"type": "agent_batch_action", "tool": "draw_strokes",
         "replay": {**replay, "elapsed_ms": 9}},
        {"type": "agent_tool", "tool": "draw_strokes", "replay": replay,
         "result_meta": {"wrapped_by_fast_controller": True}},
    ])

    path = trace_mod.export_script("r2")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["steps"] == [replay]


def test_export_script_keeps_independently_executed_identical_actions(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(trace_mod, "SCRIPTS_DIR", tmp_path / "scripts")
    replay = {"tool": "hotkey", "keys": ["ctrl", "s"]}
    _write_trace(tmp_path / "repeated.jsonl", [
        {"type": "agent_start", "task": "repeat"},
        {"type": "agent_tool", "action_id": "first", "tool": "hotkey", "replay": replay},
        {"type": "agent_tool", "action_id": "second", "tool": "hotkey", "replay": replay},
        {"type": "trace_integrity", "complete": True, "dropped_events": 0,
         "write_failures": 0, "accepted_events": 3},
    ])

    data = trace_mod.build_script("repeated")

    assert data["steps"] == [replay, replay]
    assert data["export_manifest"]["complete"] is True


@pytest.mark.parametrize(
    ("integrity", "trailing", "expected_reason"),
    [
        (
            {"complete": True, "dropped_events": 0, "write_failures": 0},
            [],
            "trace_integrity_accepted_events_missing_or_invalid",
        ),
        (
            {"complete": True, "dropped_events": 0, "write_failures": 0,
             "accepted_events": 9},
            [],
            "trace_integrity_event_count_mismatch:9:2",
        ),
        (
            {"complete": True, "dropped_events": 0, "write_failures": 0,
             "accepted_events": 2},
            [{"type": "note", "value": "unfinished append"}],
            "trace_events_after_integrity:1",
        ),
    ],
)
def test_export_manifest_fails_closed_for_unsealed_trace_segment(
    tmp_path,
    monkeypatch,
    integrity,
    trailing,
    expected_reason,
):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    _write_trace(tmp_path / "integrity.jsonl", [
        {"type": "agent_start", "task": "integrity"},
        {"type": "agent_tool", "action_id": "save", "tool": "hotkey",
         "replay": {"tool": "hotkey", "keys": ["ctrl", "s"]}},
        {"type": "trace_integrity", **integrity},
        *trailing,
    ])

    manifest = trace_mod.build_script("integrity")["export_manifest"]

    assert manifest["complete"] is False
    assert manifest["partial"] is True
    assert expected_reason in manifest["reasons"]


def test_export_manifest_validates_every_append_segment(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    _write_trace(tmp_path / "appended.jsonl", [
        {"type": "agent_start", "task": "first segment"},
        {"type": "agent_tool", "action_id": "first", "tool": "hotkey",
         "replay": {"tool": "hotkey", "keys": ["ctrl", "s"]}},
        {"type": "trace_integrity", "complete": True, "dropped_events": 0,
         "write_failures": 0, "accepted_events": 2},
        {"type": "agent_tool", "action_id": "second", "tool": "hotkey",
         "replay": {"tool": "hotkey", "keys": ["ctrl", "n"]}},
        {"type": "trace_integrity", "complete": True, "dropped_events": 0,
         "write_failures": 0, "accepted_events": 1},
    ])

    data = trace_mod.build_script("appended")

    assert data["steps"] == [
        {"tool": "hotkey", "keys": ["ctrl", "s"]},
        {"tool": "hotkey", "keys": ["ctrl", "n"]},
    ]
    assert data["export_manifest"]["complete"] is True


def test_export_manifest_keeps_first_append_failure_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    _write_trace(tmp_path / "appended-failure.jsonl", [
        {"type": "agent_start", "task": "first segment"},
        {"type": "agent_tool", "action_id": "first", "tool": "hotkey",
         "replay": {"tool": "hotkey", "keys": ["ctrl", "s"]}},
        {"type": "trace_integrity", "complete": False, "dropped_events": 1,
         "write_failures": 0, "accepted_events": 2},
        {"type": "agent_tool", "action_id": "second", "tool": "hotkey",
         "replay": {"tool": "hotkey", "keys": ["ctrl", "n"]}},
        {"type": "trace_integrity", "complete": True, "dropped_events": 0,
         "write_failures": 0, "accepted_events": 1},
    ])

    manifest = trace_mod.build_script("appended-failure")["export_manifest"]

    assert manifest["complete"] is False
    assert manifest["dropped_events"] == 1
    assert "trace_integrity_failed" in manifest["reasons"]
    assert "trace_queue_dropped:1" in manifest["reasons"]


def test_legacy_export_reports_missing_leaf_replay_but_ignores_reads_and_wrappers(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    _write_trace(tmp_path / "legacy-omission.jsonl", [
        {"type": "agent_start", "task": "legacy omission"},
        {"type": "agent_tool", "action_id": "read", "tool": "read_text"},
        {"type": "agent_batch_action", "action_id": "observe", "tool": "observe",
         "replay": {"elapsed_ms": 1}},
        {"type": "agent_tool", "action_id": "shell-read", "tool": "run_shell"},
        {"type": "agent_tool", "action_id": "batch", "tool": "run_action_batch"},
        {"type": "agent_tool", "action_id": "subgoal", "tool": "execute_subgoal",
         "replay": {"tool": "execute_subgoal", "actions": []}},
        {"type": "agent_tool", "action_id": "missing-click", "tool": "click"},
        {"type": "agent_tool", "action_id": "saved", "tool": "hotkey",
         "replay": {"tool": "hotkey", "keys": ["ctrl", "s"]}},
        {"type": "trace_integrity", "complete": True, "dropped_events": 0,
         "write_failures": 0, "accepted_events": 8},
    ])

    data = trace_mod.build_script("legacy-omission")

    assert data["steps"] == [{"tool": "hotkey", "keys": ["ctrl", "s"]}]
    assert data["export_manifest"]["reasons"] == [
        "missing_replay_for_action:click:missing-click",
    ]
    assert data["export_manifest"]["partial"] is True


def test_export_script_skips_non_executable_replay_records(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(trace_mod, "SCRIPTS_DIR", tmp_path / "scripts")
    _write_trace(tmp_path / "r3.jsonl", [
        {"type": "agent_start", "task": "subgoal"},
        {"type": "agent_tool", "tool": "launch",
         "replay": {"tool": "launch", "command": "notepad"}},
        {"type": "agent_batch_action", "tool": "click_element",
         "replay": {"tool": "click", "coords": [7, 8], "text": "OK"}},
        {"type": "agent_batch_action", "tool": "observe",
         "replay": {"elapsed_ms": 123}},
        {"type": "agent_tool", "tool": "execute_subgoal",
         "replay": {"tool": "execute_subgoal", "actions": []}},
    ])

    path = trace_mod.export_script("r3")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["steps"] == [
        {"tool": "launch", "command": "notepad"},
        {"tool": "click", "coords": [7, 8], "text": "OK"},
    ]
    assert trace_mod.validate_script(data) is None


def test_export_script_adds_stable_target_window_from_launch_expect(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(trace_mod, "SCRIPTS_DIR", tmp_path / "scripts")
    _write_trace(tmp_path / "r4.jsonl", [
        {"type": "agent_start", "task": "targeted"},
        {
            "type": "agent_tool",
            "tool": "launch",
            "args": {"expect": {"window_title": "Target App"}},
            "replay": {"tool": "launch", "command": "target.exe"},
        },
        {
            "type": "agent_tool",
            "tool": "click",
            "replay": {"tool": "click", "coords": [7, 8]},
        },
    ])

    path = trace_mod.export_script("r4")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["target_window"] == {"title": "Target App"}
    assert trace_mod.validate_script(data) is None


def test_export_script_uses_perception_target_window_without_persisting_hwnd(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(trace_mod, "SCRIPTS_DIR", tmp_path / "scripts")
    _write_trace(tmp_path / "r5.jsonl", [
        {"type": "agent_start", "task": "targeted"},
        {
            "type": "agent_tool",
            "tool": "execute_subgoal",
            "args": {"target_window": {"hwnd": 123456}},
            "result_meta": {
                "evidence_packet": {
                    "perception_reducer": {
                        "target_window": {
                            "title": "WinPilot Target - Chrome",
                            "process": "chrome.exe",
                            "hwnd": 123456,
                        }
                    }
                }
            },
            "replay": {"tool": "click", "coords": [7, 8], "text": "OK"},
        },
    ])

    path = trace_mod.export_script("r5")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["target_window"] == {
        "title": "WinPilot Target - Chrome",
        "process": "chrome.exe",
    }
    assert "hwnd" not in data["target_window"]
    assert trace_mod.validate_script(data) is None


def test_read_trace_does_not_silently_truncate_long_conversation(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    events = [{"type": "note", "index": index} for index in range(2005)]
    _write_trace(tmp_path / "long.jsonl", events)

    loaded = trace_mod.read_trace("long")

    assert len(loaded) == 2005
    assert loaded[-1]["index"] == 2004


def test_bounded_trace_read_always_reports_integrity(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    _write_trace(tmp_path / "bounded.jsonl", [
        {"type": "note", "index": index} for index in range(5)
    ])

    loaded = trace_mod.read_trace("bounded", limit=2, include_integrity=True)

    assert [event["index"] for event in loaded[:-1]] == [0, 1]
    assert loaded[-1] == {
        "type": "trace_read_integrity",
        "run_id": "bounded",
        "complete": False,
        "total_lines": 5,
        "returned_events": 2,
        "malformed_lines": 0,
        "truncated": True,
    }


def test_thread_export_concatenates_runs_and_preserves_equal_actions(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(trace_mod, "SCRIPTS_DIR", tmp_path / "scripts")
    thread_id = "thread-conversation"
    first_target = {"title": "First Window", "process": "first.exe"}
    second_target = {"title": "Second Window", "process": "second.exe"}

    def run_events(ts, run_id, turn_id, target):
        return [
            {"type": "agent_tool", "ts": ts, "action_id": f"{run_id}-action",
             "tool": "hotkey", "target_window": target,
             "replay": {"tool": "hotkey", "keys": ["ctrl", "s"]}},
            {"type": "arp_run_done", "ts": ts + 1, "runtime_snapshot": {
                "thread_id": thread_id,
                "turn_id": turn_id,
                "goal_admission": {"goal": f"goal-{run_id}"},
            }},
            {"type": "trace_integrity", "complete": True, "dropped_events": 0,
             "write_failures": 0, "accepted_events": 2},
        ]

    _write_trace(tmp_path / "run-later.jsonl", run_events(20, "run-later", "turn-2", second_target))
    _write_trace(tmp_path / "run-first.jsonl", run_events(10, "run-first", "turn-1", first_target))
    _write_trace(tmp_path / "run-older-than-ui-history.jsonl", run_events(
        5,
        "run-older-than-ui-history",
        "turn-0",
        first_target,
    ))

    _write_trace(tmp_path / "unrelated.jsonl", [{"broken": "not this thread"}])
    data = trace_mod.build_thread_script(
        thread_id,
        run_ids=["run-later", "run-first"],
    )

    assert data["source_runs"] == [
        "run-older-than-ui-history",
        "run-first",
        "run-later",
    ]
    assert [step["target_window"] for step in data["steps"]] == [
        first_target,
        first_target,
        second_target,
    ]
    assert [step["keys"] for step in data["steps"]] == [
        ["ctrl", "s"],
        ["ctrl", "s"],
        ["ctrl", "s"],
    ]
    assert data["export_manifest"]["complete"] is True


def _complete_trace(events):
    return [
        *events,
        {
            "type": "trace_integrity",
            "complete": True,
            "dropped_events": 0,
            "write_failures": 0,
            "accepted_events": len(events),
        },
    ]



def test_thread_export_rejects_one_trace_owned_by_multiple_threads(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    mixed = [
        {"type": "agent_tool", "ts": 1, "tool": "hotkey",
         "replay": {"tool": "hotkey", "keys": ["ctrl", "a"]}},
        {"type": "arp_run_done", "thread_id": "thread-a", "turn_id": "turn-a"},
        {"type": "trace_integrity", "complete": True, "dropped_events": 0,
         "write_failures": 0, "accepted_events": 2},
        {"type": "agent_tool", "ts": 2, "tool": "hotkey",
         "replay": {"tool": "hotkey", "keys": ["ctrl", "b"]}},
        {"type": "arp_run_done", "runtime_snapshot": {
            "thread_id": "thread-b", "turn_id": "turn-b",
        }},
        {"type": "trace_integrity", "complete": True, "dropped_events": 0,
         "write_failures": 0, "accepted_events": 2},
    ]
    valid = _complete_trace([
        {"type": "agent_tool", "ts": 3, "tool": "hotkey",
         "replay": {"tool": "hotkey", "keys": ["ctrl", "s"]}},
        {"type": "arp_run_done", "runtime_snapshot": {
            "thread_id": "thread-b", "turn_id": "turn-valid",
        }},
    ])
    _write_trace(tmp_path / "mixed.jsonl", mixed)
    _write_trace(tmp_path / "valid.jsonl", valid)

    data = trace_mod.build_thread_script(
        "thread-b",
        run_ids=["mixed", "valid"],
    )

    assert data["source_runs"] == ["valid"]
    assert data["steps"] == [{"tool": "hotkey", "keys": ["ctrl", "s"]}]
    assert data["export_manifest"]["reasons"] == [
        "trace_thread_id_mixed:mixed:thread-a|thread-b",
    ]


def test_trace_task_reads_vnext_arp_run_start_goal_admission():
    assert trace_mod._trace_task([
        {
            "type": "arp_run_start",
            "runtime_version": "vnext",
            "goal_admission": {"goal": "真实 vNext 原始目标"},
        },
    ], "run-fallback") == "真实 vNext 原始目标"


def test_explicit_thread_export_accounts_for_every_requested_run(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    thread_id = "thread-accounting"
    complete = {
        "type": "trace_integrity",
        "complete": True,
        "dropped_events": 0,
        "write_failures": 0,
        "accepted_events": 3,
    }
    _write_trace(tmp_path / "run-valid.jsonl", [
        {
            "type": "agent_start",
            "ts": 10,
            "task": "保存当前文档",
        },
        {
            "type": "desktop_lease_requested",
            "thread_id": thread_id,
            "turn_id": "turn-valid",
        },
        {
            "type": "agent_tool",
            "action_id": "action-valid",
            "tool": "hotkey",
            "replay": {"tool": "hotkey", "keys": ["ctrl", "s"]},
        },
        complete,
    ])
    _write_trace(tmp_path / "run-no-thread.jsonl", [
        {
            "type": "agent_tool",
            "action_id": "action-no-thread",
            "tool": "hotkey",
            "replay": {"tool": "hotkey", "keys": ["ctrl", "n"]},
        },
        complete,
    ])
    _write_trace(tmp_path / "run-wrong-thread.jsonl", [
        {
            "type": "desktop_lease_requested",
            "thread_id": "thread-other",
            "turn_id": "turn-other",
        },
        {
            "type": "agent_tool",
            "action_id": "action-wrong-thread",
            "tool": "hotkey",
            "replay": {"tool": "hotkey", "keys": ["ctrl", "o"]},
        },
        complete,
    ])

    data = trace_mod.build_thread_script(
        thread_id,
        run_ids=[
            "run-valid",
            "run-missing",
            "run-no-thread",
            "run-wrong-thread",
        ],
    )

    assert data["task"] == "保存当前文档"
    assert data["source_runs"] == ["run-valid"]
    assert data["steps"] == [{"tool": "hotkey", "keys": ["ctrl", "s"]}]
    assert data["export_manifest"] == {
        "format": "agent-replay-export/1",
        "scope": "thread",
        "coverage": "ordinary_arp_actions",
        "thread_id": thread_id,
        "complete": False,
        "partial": True,
        "reasons": [
            "trace_missing:run-missing",
            "trace_thread_id_missing:run-no-thread",
            "trace_thread_id_mismatch:run-wrong-thread:thread-other",
        ],
        "source_runs": ["run-valid"],
        "run_count": 1,
    }


def test_thread_export_marks_unreadable_owner_line_partial(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    healthy = [
        {
            "type": "desktop_lease_requested",
            "thread_id": "thread-target",
            "turn_id": "turn-healthy",
        },
        {
            "type": "agent_tool",
            "tool": "hotkey",
            "replay": {"tool": "hotkey", "keys": ["ctrl", "s"]},
        },
    ]
    _write_trace(tmp_path / "healthy.jsonl", _complete_trace(healthy))
    (tmp_path / "unreadable.jsonl").write_text(
        '{"type":"desktop_lease_requested","thread_id":"thread-target"\n'
        '{"type":"agent_tool","tool":"hotkey",'
        '"replay":{"tool":"hotkey","keys":["ctrl","n"]}}\n'
        '{"type":"trace_integrity","complete":true,"dropped_events":0,'
        '"write_failures":0,"accepted_events":1}\n',
        encoding="utf-8",
    )

    data = trace_mod.build_thread_script("thread-target")

    assert data["steps"] == [{"tool": "hotkey", "keys": ["ctrl", "s"]}]
    assert data["export_manifest"]["partial"] is True
    assert (
        "trace_thread_identity_unverifiable:unreadable"
        in data["export_manifest"]["reasons"]
    )


def test_trace_read_preserves_valid_events_around_invalid_utf8(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    path = tmp_path / "invalid-utf8.jsonl"
    path.write_bytes(
        b'{"type":"agent_start","task":"ok"}\n'
        b'{"type":"note","value":"\xe4\xb8"}\n'
        b'{"type":"agent_tool","tool":"hotkey",'
        b'"replay":{"tool":"hotkey","keys":["ctrl","s"]}}\n'
    )

    events, integrity = trace_mod._read_trace_records("invalid-utf8")

    assert [event["type"] for event in events] == ["agent_start", "agent_tool"]
    assert integrity["malformed_lines"] == 1


@pytest.mark.parametrize("unsafe", [
    r"..\escape",
    "../escape",
    "CON",
    "con.txt",
    "PRN",
    "COM1",
    "lpt9.log",
    "CON .txt",
    "COM¹.log",
    "CONOUT$",
    "trailing. ",
])
def test_all_trace_exports_reject_unsafe_windows_leaf_names(
    tmp_path,
    monkeypatch,
    unsafe,
):
    traces = tmp_path / "traces"
    scripts = tmp_path / "scripts"
    traces.mkdir()
    monkeypatch.setattr(trace_mod, "TRACES_DIR", traces)
    monkeypatch.setattr(trace_mod, "SCRIPTS_DIR", scripts)
    thread_id = "thread-safe"
    _write_trace(traces / "run-safe.jsonl", [
        {"type": "agent_tool", "tool": "hotkey",
         "replay": {"tool": "hotkey", "keys": ["ctrl", "s"]}},
        {"type": "arp_run_done", "runtime_snapshot": {"thread_id": thread_id}},
        {"type": "trace_integrity", "complete": True,
         "dropped_events": 0, "write_failures": 0, "accepted_events": 2},
    ])

    for export in (
        lambda: trace_mod.export_script("run-safe", name=unsafe),
        lambda: trace_mod.export_automation_ir("run-safe", name=unsafe),
        lambda: trace_mod.export_thread_script(thread_id, name=unsafe),
        lambda: trace_mod.export_thread_automation_ir(thread_id, name=unsafe),
    ):
        with pytest.raises(ValueError, match="Windows-safe"):
            export()
    minimal = {
        "format": "wps/2",
        "name": "safe",
        "steps": [{"tool": "hotkey", "keys": ["ctrl", "s"]}],
    }
    with pytest.raises(ValueError, match="Windows-safe"):
        trace_mod.write_automation_ir(
            minimal, name=unsafe, basename="safe", directory=scripts,
        )
    with pytest.raises(ValueError, match="Windows-safe"):
        trace_mod.write_automation_ir(
            minimal, name=None, basename=unsafe, directory=scripts,
        )
    assert not scripts.exists()


def test_thread_export_preserves_safe_chinese_name_with_space(tmp_path, monkeypatch):
    traces = tmp_path / "traces"
    scripts = tmp_path / "scripts"
    traces.mkdir()
    monkeypatch.setattr(trace_mod, "TRACES_DIR", traces)
    monkeypatch.setattr(trace_mod, "SCRIPTS_DIR", scripts)
    thread_id = "thread-safe-name"
    _write_trace(traces / "run-safe-name.jsonl", [
        {"type": "agent_tool", "tool": "hotkey",
         "replay": {"tool": "hotkey", "keys": ["ctrl", "s"]}},
        {"type": "arp_run_done", "runtime_snapshot": {"thread_id": thread_id}},
        {"type": "trace_integrity", "complete": True,
         "dropped_events": 0, "write_failures": 0, "accepted_events": 2},
    ])

    path = trace_mod.export_thread_script(thread_id, name="中文 流程")

    assert path == scripts / "中文 流程.wps.json"
    assert path.is_file()
    assert (scripts / "中文 流程.automation.json").is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "中文 流程"


def test_trace_exports_never_replace_existing_wps_or_ir_siblings(tmp_path, monkeypatch):
    traces = tmp_path / "traces"
    scripts = tmp_path / "scripts"
    traces.mkdir()
    monkeypatch.setattr(trace_mod, "TRACES_DIR", traces)
    monkeypatch.setattr(trace_mod, "SCRIPTS_DIR", scripts)
    _write_trace(traces / "run-collision.jsonl", [
        {"type": "agent_start", "task": "collision"},
        {"type": "agent_tool", "action_id": "save", "tool": "hotkey",
         "replay": {"tool": "hotkey", "keys": ["ctrl", "s"]}},
        {"type": "trace_integrity", "complete": True, "dropped_events": 0,
         "write_failures": 0, "accepted_events": 2},
    ])

    first = trace_mod.export_script("run-collision", name="same-name")
    first_wps = first.read_bytes()
    first_ir_path = scripts / "same-name.automation.json"
    first_ir = first_ir_path.read_bytes()
    second = trace_mod.export_script("run-collision", name="same-name")
    ir_only = trace_mod.export_automation_ir("run-collision", name="same-name")

    assert first == scripts / "same-name.wps.json"
    assert second == scripts / "same-name-2.wps.json"
    assert (scripts / "same-name-2.automation.json").is_file()
    assert ir_only == scripts / "same-name-3.automation.json"
    assert not (scripts / "same-name-3.wps.json").exists()
    assert first.read_bytes() == first_wps
    assert first_ir_path.read_bytes() == first_ir


def test_clipboard_get_fails_closed_when_open_stays_busy(monkeypatch):
    import sys
    from types import SimpleNamespace

    calls = {"open": 0}

    def fail_open():
        calls["open"] += 1
        raise OSError("clipboard busy")

    monkeypatch.setitem(sys.modules, "win32clipboard", SimpleNamespace(
        CF_UNICODETEXT=13,
        OpenClipboard=fail_open,
    ))

    result = trace_mod._clipboard_get(attempts=3, retry_delay_s=0)

    assert result["ok"] is False
    assert "clipboard busy" in result["error"]
    assert calls["open"] == 3


def test_clipboard_get_distinguishes_missing_text_format_from_failure(monkeypatch):
    import sys
    from types import SimpleNamespace

    calls = {"close": 0}
    monkeypatch.setitem(sys.modules, "win32clipboard", SimpleNamespace(
        CF_UNICODETEXT=13,
        OpenClipboard=lambda: None,
        IsClipboardFormatAvailable=lambda _format: False,
        GetClipboardData=lambda _format: (_ for _ in ()).throw(
            AssertionError("GetClipboardData must not be called")
        ),
        CloseClipboard=lambda: calls.__setitem__("close", calls["close"] + 1),
    ))

    assert trace_mod._clipboard_get(attempts=1, retry_delay_s=0) == {
        "ok": True,
        "text": "",
    }
    assert calls["close"] == 1


def test_clipboard_get_fails_closed_when_data_read_fails(monkeypatch):
    import sys
    from types import SimpleNamespace

    calls = {"close": 0}
    monkeypatch.setitem(sys.modules, "win32clipboard", SimpleNamespace(
        CF_UNICODETEXT=13,
        OpenClipboard=lambda: None,
        IsClipboardFormatAvailable=lambda _format: True,
        GetClipboardData=lambda _format: (_ for _ in ()).throw(
            OSError("clipboard read failed")
        ),
        CloseClipboard=lambda: calls.__setitem__("close", calls["close"] + 1),
    ))

    result = trace_mod._clipboard_get(attempts=3, retry_delay_s=0)

    assert result["ok"] is False
    assert calls["close"] == 3


def test_clipboard_get_retries_transient_busy_then_returns_text(monkeypatch):
    import sys
    from types import SimpleNamespace

    calls = {"open": 0, "close": 0}

    def open_clipboard():
        calls["open"] += 1
        if calls["open"] < 3:
            raise OSError("clipboard busy")

    monkeypatch.setitem(sys.modules, "win32clipboard", SimpleNamespace(
        CF_UNICODETEXT=13,
        OpenClipboard=open_clipboard,
        IsClipboardFormatAvailable=lambda _format: True,
        GetClipboardData=lambda _format: "clipboard nonce",
        CloseClipboard=lambda: calls.__setitem__("close", calls["close"] + 1),
    ))

    assert trace_mod._clipboard_get(attempts=3, retry_delay_s=0) == {
        "ok": True,
        "text": "clipboard nonce",
    }
    assert calls == {"open": 3, "close": 1}


# ------------------------------------------------------------- HTTP endpoints
@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from winpilot import server
    monkeypatch.setattr(server, "SCRIPTS_DIR", tmp_path)
    return TestClient(server.app)


def test_put_then_get_roundtrip(client):
    script = {"format": "wps/2", "name": "t",
              "steps": [{"tool": "click", "coords": [1, 2]}]}
    put = client.put("/api/script/demo.wps.json", json={"script": script})
    assert put.status_code == 200 and put.json()["ok"] is True
    got = client.get("/api/script/demo.wps.json")
    assert got.status_code == 200
    assert got.json()["steps"][0]["tool"] == "click"


def test_put_rejects_invalid_script(client):
    bad = {"format": "wps/2", "steps": [{"tool": "rm -rf"}]}
    resp = client.put("/api/script/bad.wps.json", json={"script": bad})
    assert resp.status_code == 400


def test_get_missing_script_404(client):
    assert client.get("/api/script/none.wps.json").status_code == 404


def test_reject_non_wps_filename(client):
    assert client.get("/api/script/passwd.txt").status_code == 400


def test_path_traversal_blocked(client):
    # ..%2f style traversal must not escape SCRIPTS_DIR
    resp = client.get("/api/script/..%2f..%2fconfig.wps.json")
    assert resp.status_code in (400, 404)


def test_replayer_leaf_without_explicit_ok_fails_and_records_detail(monkeypatch):
    replayer = trace_mod.Replayer()
    replayer.step_delay = 0
    monkeypatch.setattr(
        replayer,
        "_run_step",
        lambda _idx, _step: {"error": "missing ok", "attempts": 2},
    )

    assert replayer._exec_steps([{"tool": "click", "coords": [1, 2]}]) is False
    assert replayer._results == [{
        "index": "1",
        "tool": "click",
        "ok": False,
        "error": "missing ok",
        "attempts": 2,
    }]


def test_replayer_does_not_guess_first_window_for_semantic_relocation(monkeypatch):
    replayer = trace_mod.Replayer()
    monkeypatch.setattr(
        trace_mod.win32,
        "list_windows",
        lambda: [{"hwnd": 123, "title": "Wrong"}],
    )

    assert replayer._observe_hwnd() is None
    assert replayer.hwnd is None
