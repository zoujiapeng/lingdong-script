"""wps/2 Replayer integration + v1 backward-compatibility.

A spy subclass overrides only leaf-action I/O (`_run_step`) and condition
evaluation (`_check_condition`) so nothing touches the screen. ext.var_set /
ext.delay run their REAL implementations to prove variable interpolation works
end-to-end. The key invariant: a v1 flat script behaves exactly as before.
"""
import json

from winpilot.recorder import trace as trace_mod


class SpyReplayer(trace_mod.Replayer):
    """Records leaf calls; scripts condition results; no real screen I/O."""

    def __init__(self, cond_results=None):
        super().__init__()
        self.step_delay = 0
        self.leaf_calls: list[dict] = []
        self._cond = list(cond_results or [])
        self._leaf_ok = {}  # tool -> bool override

    def _run_step(self, idx, step):
        self.leaf_calls.append({"idx": idx, "tool": step.get("tool"), "step": step})
        return {"ok": self._leaf_ok.get(step.get("tool"), True)}

    def _check_condition(self, cond, timeout_s):
        return self._cond.pop(0) if self._cond else False


def _write(tmp_path, data):
    path = tmp_path / "s.wps.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# ------------------------------------------------------- v1 compatibility
def test_v1_flat_script_runs_each_leaf_in_order(tmp_path):
    script = {"format": "wps/1", "name": "x", "steps": [
        {"tool": "click", "coords": [1, 2]},
        {"tool": "type_text", "text_input": "hi"},
        {"tool": "hotkey", "keys": ["enter"]},
    ]}
    r = SpyReplayer()
    out = r.run(_write(tmp_path, script))
    assert out["ok"] is True
    assert [c["tool"] for c in r.leaf_calls] == ["click", "type_text", "hotkey"]
    assert [row["index"] for row in out["results"]] == ["1", "2", "3"]
    assert all(row["ok"] for row in out["results"])


def test_v1_leaf_failure_stops_and_reports(tmp_path):
    script = {"format": "wps/1", "steps": [
        {"tool": "click", "coords": [1, 2]},
        {"tool": "type_text", "text_input": "hi"},
        {"tool": "hotkey", "keys": ["enter"]},
    ]}
    r = SpyReplayer()
    r._leaf_ok = {"type_text": False}
    out = r.run(_write(tmp_path, script))
    assert out["ok"] is False
    # click ran ok, type_text failed, hotkey never ran
    assert [c["tool"] for c in r.leaf_calls] == ["click", "type_text"]
    assert out["results"][-1] == {"index": "2", "tool": "type_text", "ok": False}


def test_v1_no_vars_means_no_interpolation(tmp_path):
    # A literal ${...} with no vars table stays verbatim (no crash, no blank).
    script = {"format": "wps/1", "steps": [
        {"tool": "type_text", "text_input": "${notavar}"},
    ]}
    r = SpyReplayer()
    out = r.run(_write(tmp_path, script))
    assert out["ok"] is True
    assert r.leaf_calls[0]["step"]["text_input"] == "${notavar}"


# ----------------------------------------------------------- v2 control flow
def test_v2_if_true_runs_then_with_interpolation(tmp_path):
    script = {"format": "wps/2", "vars": {"who": "alice"}, "steps": [
        {"tool": "ext.var_set", "name": "greeting", "value": "hello ${who}"},
        {"tool": "ctrl.if", "cond": {"text_appears": "${who}"},
         "then": [{"tool": "type_text", "text_input": "${greeting}"}],
         "else": [{"tool": "type_text", "text_input": "no"}]},
    ]}
    r = SpyReplayer(cond_results=[True])
    out = r.run(_write(tmp_path, script))
    assert out["ok"] is True
    # only the then-branch leaf ran, and ${greeting} resolved via the var set
    # in the prior step (which itself interpolated ${who})
    assert len(r.leaf_calls) == 1
    assert r.leaf_calls[0]["step"]["text_input"] == "hello alice"


def test_v2_loop_count_paths_indices(tmp_path):
    script = {"format": "wps/2", "steps": [
        {"tool": "ctrl.loop", "loop_type": "count", "count": 2,
         "body": [{"tool": "click"}]},
    ]}
    r = SpyReplayer()
    out = r.run(_write(tmp_path, script))
    assert out["ok"] is True
    assert [row["index"] for row in out["results"]] == ["1.1.1", "1.2.1"]


def test_v2_ext_delay_records_result(tmp_path):
    script = {"format": "wps/2", "steps": [{"tool": "ext.delay", "seconds": 0}]}
    r = SpyReplayer()
    out = r.run(_write(tmp_path, script))
    assert out["ok"] is True
    assert out["results"][0]["tool"] == "ext.delay"


def test_v2_var_set_then_used_by_later_leaf(tmp_path):
    script = {"format": "wps/2", "steps": [
        {"tool": "ext.var_set", "name": "u", "value": "Bob"},
        {"tool": "type_text", "text_input": "name=${u}"},
    ]}
    r = SpyReplayer()
    out = r.run(_write(tmp_path, script))
    assert out["ok"] is True
    assert r.leaf_calls[0]["step"]["text_input"] == "name=Bob"


def test_v2_unknown_ext_step_fails_gracefully(tmp_path):
    script = {"format": "wps/2", "steps": [{"tool": "ext.bogus"}]}
    r = SpyReplayer()
    out = r.run(_write(tmp_path, script))
    assert out["ok"] is False


def test_replayer_runs_draw_strokes_leaf(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        trace_mod.executor,
        "draw_strokes",
        lambda strokes, duration_s, hwnd: calls.append((strokes, duration_s, hwnd)) or {"ok": True},
    )
    script = {"format": "wps/2", "steps": [
        {"tool": "draw_strokes", "strokes": [[[1, 2], [3, 4]]], "duration": 0.1},
    ]}
    r = trace_mod.Replayer()
    r.step_delay = 0
    out = r.run(_write(tmp_path, script))

    assert out["ok"] is True
    assert calls == [([[[1, 2], [3, 4]]], 0.1, None)]


def test_replayer_runs_detached_leaf(tmp_path, monkeypatch):
    from winpilot.action import shell

    calls = []
    monkeypatch.setattr(
        shell,
        "run_detached",
        lambda script, name="task": calls.append((script, name)) or {"ok": True, "pid": 123},
    )
    script = {"format": "wps/2", "steps": [
        {"tool": "run_detached", "script": "Start-Sleep 1", "name": "demo"},
    ]}
    r = trace_mod.Replayer()
    r.step_delay = 0
    out = r.run(_write(tmp_path, script))

    assert out["ok"] is True
    assert calls == [("Start-Sleep 1", "demo")]
