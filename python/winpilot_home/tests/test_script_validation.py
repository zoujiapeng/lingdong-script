"""validate_script (pure) + GET/PUT /api/script HTTP round-trip."""
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


def test_export_script_dedupes_fast_wrapper_child_and_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(trace_mod, "SCRIPTS_DIR", tmp_path / "scripts")
    replay = {"tool": "draw_strokes", "strokes": [[[1, 2], [3, 4]]], "duration": 0.1}
    _write_trace(tmp_path / "r2.jsonl", [
        {"type": "agent_start", "task": "draw"},
        {"type": "agent_batch_action", "tool": "draw_strokes",
         "replay": {**replay, "elapsed_ms": 9}},
        {"type": "agent_tool", "tool": "draw_strokes", "replay": replay},
    ])

    path = trace_mod.export_script("r2")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["steps"] == [replay]


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
