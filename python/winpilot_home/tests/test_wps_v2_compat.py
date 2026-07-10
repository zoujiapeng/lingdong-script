"""wps/2 Replayer integration + v1 backward-compatibility.

A spy subclass overrides only leaf-action I/O (`_run_step`) and condition
evaluation (`_check_condition`) so nothing touches the screen. ext.var_set /
ext.delay run their REAL implementations to prove variable interpolation works
end-to-end. The key invariant: a v1 flat script behaves exactly as before.
"""
import json
from types import SimpleNamespace

import numpy as np

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


def test_relocate_text_uses_existing_window_anchor(monkeypatch):
    monkeypatch.setattr(trace_mod.uia, "find_by_text", lambda hwnd, text: [])
    monkeypatch.setattr(
        trace_mod.fusion,
        "observe",
        lambda hwnd, include_icons=False: SimpleNamespace(
            region=SimpleNamespace(left=0, top=0, width=800, height=600),
            dim_stats={},
            elements=[SimpleNamespace(text="OK", cx=99, cy=88)]
        ),
    )

    r = trace_mod.Replayer()
    r.hwnd = 123
    point = r._relocate({"tool": "click", "text": "OK", "coords": [1, 2]})

    assert point == (99, 88)
    assert r.hwnd == 123


def test_relocate_uses_fast_exact_uia_query_before_full_fusion(monkeypatch):
    monkeypatch.setattr(
        trace_mod.uia,
        "find_by_text",
        lambda hwnd, text: [
            SimpleNamespace(
                text="Save", cx=320, cy=240, source="uia",
                left=280, top=225, width=80, height=30,
            ),
        ],
    )
    monkeypatch.setattr(
        trace_mod.fusion,
        "observe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full fusion should not run")),
    )
    monkeypatch.setattr(
        trace_mod,
        "window_region",
        lambda hwnd: trace_mod.Region(0, 0, 800, 600),
    )

    r = trace_mod.Replayer()
    r.hwnd = 123
    point = r._relocate({"tool": "click", "text": "Save", "coords": [1, 2]})

    assert point == (320, 240)
    assert r._last_relocation["perception_method"] == "uia-name-query"


def test_relocate_refreshes_duplicate_same_bbox_after_navigation(monkeypatch):
    stale = SimpleNamespace(
        text="Export", cx=300, cy=200, source="uia",
        left=250, top=175, width=100, height=50,
    )
    current = SimpleNamespace(
        text="Export", cx=300, cy=400, source="uia",
        left=250, top=375, width=100, height=50,
    )
    responses = [[stale, stale], [stale, current]]
    monkeypatch.setattr(
        trace_mod.uia,
        "find_by_text",
        lambda hwnd, text: responses.pop(0),
    )
    monkeypatch.setattr(
        trace_mod,
        "window_region",
        lambda hwnd: trace_mod.Region(0, 0, 800, 600),
    )
    monkeypatch.setattr(
        trace_mod.Replayer,
        "_choose_visually_supported",
        lambda _self, elements, _region: (elements[-1], {"method": "test"}),
    )

    r = trace_mod.Replayer()
    r.hwnd = 123
    point = r._relocate({"tool": "click", "text": "Export", "coords": [1, 2]})

    assert point == (300, 400)
    assert r._last_relocation["perception_dims"]["uia_query"]["attempts"] == 2


def test_relocate_rejects_duplicate_selector_without_pixel_evidence(monkeypatch):
    stale = SimpleNamespace(
        text="Export", cx=300, cy=200, source="uia",
        left=250, top=175, width=100, height=50,
    )
    monkeypatch.setattr(trace_mod.uia, "find_by_text", lambda hwnd, text: [stale, stale])
    monkeypatch.setattr(
        trace_mod,
        "window_region",
        lambda hwnd: trace_mod.Region(0, 0, 800, 600),
    )
    monkeypatch.setattr(
        trace_mod.Replayer,
        "_choose_visually_supported",
        lambda *_args, **_kwargs: (None, {"confident": False}),
    )
    monkeypatch.setattr(trace_mod.ocr, "find_text", lambda *args, **kwargs: [])

    r = trace_mod.Replayer()
    r.hwnd = 123
    point = r._relocate({"tool": "click", "text": "Export", "coords": [1, 2]})

    assert point is None
    assert r._last_relocation["selector_rejected"] is True


def test_relocate_cross_checks_duplicate_uia_trees_with_physical_ocr(monkeypatch):
    observed_regions = []
    monkeypatch.setattr(trace_mod.uia, "find_by_text", lambda hwnd, text: [])
    monkeypatch.setattr(
        trace_mod.fusion,
        "observe",
        lambda hwnd, include_icons=False: SimpleNamespace(
            region=SimpleNamespace(left=0, top=0, width=800, height=600),
            elements=[
                SimpleNamespace(
                    text="Export", cx=700, cy=120, source="uia",
                    left=660, top=105, width=80, height=30,
                ),
                SimpleNamespace(
                    text="Export", cx=700, cy=420, source="uia",
                    left=660, top=405, width=80, height=30,
                ),
            ],
        ),
    )
    monkeypatch.setattr(
        trace_mod.ocr,
        "find_text",
        lambda region, text, min_conf=0.3: observed_regions.append(region) or [
            SimpleNamespace(text="Export", cx=701, cy=421, source="ocr"),
        ],
    )
    monkeypatch.setattr(
        trace_mod.Replayer,
        "_choose_visually_supported",
        lambda *_args, **_kwargs: (None, {}),
    )

    r = trace_mod.Replayer()
    r.hwnd = 123
    point = r._relocate({"tool": "click", "text": "Export", "coords": [1, 2]})

    assert point == (701, 421)
    assert r._last_relocation["source"] == "ocr"
    assert observed_regions[0].width < 800
    assert observed_regions[0].height < 600
    assert r._last_relocation["ocr_roi"] == [634, 90, 132, 360]


def test_duplicate_uia_candidates_use_local_visual_support_before_ocr(monkeypatch):
    wrong = SimpleNamespace(left=660, top=105, width=80, height=30)
    visible = SimpleNamespace(left=660, top=405, width=80, height=30)
    window = trace_mod.Region(0, 0, 800, 600)

    def fake_capture(region):
        frame = np.full((region.height, region.width, 3), 255, dtype=np.uint8)
        x1 = visible.left - region.left
        y1 = visible.top - region.top
        x2 = x1 + visible.width
        y2 = y1 + visible.height
        frame[y1:y1 + 2, x1:x2] = 80
        frame[y2 - 2:y2, x1:x2] = 80
        frame[y1:y2, x1:x1 + 2] = 80
        frame[y1:y2, x2 - 2:x2] = 80
        frame[y1 + 10:y1 + 20, x1 + 20:x1 + 60] = 30
        return frame

    monkeypatch.setattr(trace_mod, "capture", fake_capture)
    hit, metadata = trace_mod.Replayer._choose_visually_supported(
        [wrong, visible],
        window,
    )

    assert hit is visible
    assert metadata["method"] == "visual-support"
    assert metadata["confident"] is True


def test_duplicate_visible_controls_require_text_disambiguation(monkeypatch):
    first = SimpleNamespace(left=100, top=100, width=100, height=40)
    second = SimpleNamespace(left=100, top=220, width=100, height=40)
    window = trace_mod.Region(0, 0, 500, 400)

    def fake_capture(region):
        frame = np.full((region.height, region.width, 3), 255, dtype=np.uint8)
        for item in (first, second):
            x = item.left - region.left
            y = item.top - region.top
            frame[y:y + item.height, x:x + item.width] = 220
            frame[y + 8:y + 30, x + 20:x + 80] = 40
        return frame

    monkeypatch.setattr(trace_mod, "capture", fake_capture)
    hit, metadata = trace_mod.Replayer._choose_visually_supported(
        [first, second],
        window,
    )

    assert hit is None
    assert metadata["confident"] is False


def test_relocate_text_falls_back_to_recorded_coords_when_no_window(monkeypatch):
    monkeypatch.setattr(trace_mod.win32, "list_windows", lambda: [])

    r = trace_mod.Replayer()
    point = r._relocate({"tool": "click", "text": "OK", "coords": [1, 2]})

    assert point == (1, 2)


def _run_implicitly_verified_click(monkeypatch, frames):
    r = trace_mod.Replayer()
    r.hwnd = 123
    remaining = iter(frames)
    monkeypatch.setattr(r, "_capture_action_surface", lambda: next(remaining))
    monkeypatch.setattr(
        trace_mod.verify,
        "run_with_verify",
        lambda _name, action, **_kwargs: action(),
    )
    monkeypatch.setattr(
        trace_mod.executor,
        "click",
        lambda *_args, **_kwargs: (
            trace_mod.executor._sample_pressed_state() and {"ok": True}
        ) or {"ok": True},
    )
    return r._run_step("1", {"tool": "click", "coords": [10, 20]})


def test_click_without_expect_rejects_no_visible_change(monkeypatch):
    still = np.zeros((100, 100, 3), dtype=np.uint8)
    result = _run_implicitly_verified_click(monkeypatch, [still, still.copy(), still.copy()])

    assert result["ok"] is True
    assert result["verified"] is False
    assert result["screen_change_ratio"] == 0
    assert result["post_change_ratio"] == 0
    assert result["press_sample_count"] == 1


def test_click_without_expect_accepts_transient_pressed_state(monkeypatch):
    before = np.zeros((100, 100, 3), dtype=np.uint8)
    pressed = before.copy()
    pressed[20:30, 20:30] = 255
    after = before.copy()
    result = _run_implicitly_verified_click(monkeypatch, [before, pressed, after])

    assert result["verified"] is True
    assert result["screen_delta_source"] == "press"
    assert result["transient_observed"] is True
    assert result["screen_change_ratio"] == 0.01
    assert result["post_change_ratio"] == 0


def test_replayer_preflight_binds_declared_target_window(tmp_path, monkeypatch):
    monkeypatch.setattr(
        trace_mod.win32,
        "find_window",
        lambda title: {"hwnd": 456, "title": "Target App", "pid": 12} if title == "Target App" else None,
    )
    script = {
        "format": "wps/2",
        "target_window": {"title": "Target App"},
        "steps": [{"tool": "click", "coords": [1, 2]}],
    }

    r = SpyReplayer()
    out = r.run(_write(tmp_path, script))

    assert out["ok"] is True
    assert out["preflight"]["required"] is True
    assert out["preflight"]["hwnd"] == 456
    assert r.hwnd == 456
    assert [c["tool"] for c in r.leaf_calls] == ["click"]


def test_replayer_launch_binds_verified_window_for_semantic_followup(monkeypatch):
    from winpilot.recorder import trace as trace_mod

    monkeypatch.setattr(
        trace_mod.verify,
        "run_with_verify",
        lambda *args, **kwargs: {"ok": True, "verified": True, "post_state": {"foreground_hwnd": 111}},
    )
    monkeypatch.setattr(trace_mod.win32, "find_window", lambda title: {"hwnd": 456, "title": title})

    replayer = trace_mod.Replayer()
    result = replayer._run_step("1", {
        "tool": "launch",
        "command": "https://example.test",
        "expect": {"window_title": "Example"},
    })

    assert result["ok"] is True
    assert result["target_hwnd"] == 456
    assert replayer.hwnd == 456


def test_url_launch_waits_for_new_browser_surface_before_followup(monkeypatch):
    before = np.zeros((80, 120, 3), dtype=np.uint8)
    settled = []
    monkeypatch.setattr(
        trace_mod.verify,
        "run_with_verify",
        lambda *args, **kwargs: {"ok": True, "verified": True, "post_state": {}},
    )
    monkeypatch.setattr(
        trace_mod.win32,
        "find_window",
        lambda title: {"hwnd": 456, "title": title},
    )
    monkeypatch.setattr(trace_mod.executor, "focus_window", lambda _hwnd: {"ok": True})

    replayer = trace_mod.Replayer()
    replayer.hwnd = 111
    monkeypatch.setattr(replayer, "_capture_action_surface", lambda: before)
    monkeypatch.setattr(
        replayer,
        "_wait_for_url_launch_settle",
        lambda frame: settled.append(frame) or {"observed_change": True, "waited_ms": 80},
    )

    result = replayer._run_step("1", {
        "tool": "launch",
        "command": "https://example.test/app",
        "expect": {"window_title": "Example"},
    })

    assert settled == [before]
    assert result["launch_settle"]["observed_change"] is True
    assert replayer.hwnd == 456


def test_replayer_launch_accepts_declared_window_when_foreground_focus_lags(monkeypatch):
    focused = []
    monkeypatch.setattr(
        trace_mod.verify,
        "run_with_verify",
        lambda *args, **kwargs: {
            "ok": True,
            "verified": False,
            "verify_detail": "old foreground did not match",
            "post_state": {"foreground_hwnd": 111},
        },
    )
    monkeypatch.setattr(
        trace_mod.win32,
        "find_window",
        lambda title: {"hwnd": 456, "title": "WinPilot GUI Agent Target - Google Chrome"},
    )
    monkeypatch.setattr(
        trace_mod.executor,
        "focus_window",
        lambda hwnd: focused.append(hwnd) or {"ok": True, "hwnd": hwnd},
    )

    replayer = trace_mod.Replayer()
    result = replayer._run_step("1", {
        "tool": "launch",
        "command": "http://127.0.0.1:8877/gui_agent_target.html",
        "expect": {"window_title": "gui_agent_target"},
    })

    assert result["ok"] is True
    assert result["verified"] is True
    assert result["verify_method"] == "win32-title"
    assert result["target_hwnd"] == 456
    assert focused == [456]
    assert replayer.hwnd == 456


def test_replayer_launch_checks_declared_title_as_window_appearance(monkeypatch):
    captured = {}

    def run_with_verify(*args, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "verified": True, "post_state": {"foreground_hwnd": 111}}

    monkeypatch.setattr(trace_mod.verify, "run_with_verify", run_with_verify)
    monkeypatch.setattr(
        trace_mod.win32,
        "find_window",
        lambda title: {"hwnd": 456, "title": "Target App"},
    )
    monkeypatch.setattr(trace_mod.executor, "focus_window", lambda hwnd: {"ok": True})

    result = trace_mod.Replayer()._run_step("1", {
        "tool": "launch",
        "command": "https://example.test",
        "expect": {"window_title": "Target App", "text_appears": "Ready"},
    })

    assert result["ok"] is True
    assert captured["expect"] == {"window_appears": "Target App", "text_appears": "Ready"}
    assert captured["retryable"] is False


def test_in_memory_segment_surfaces_leaf_verify_failure():
    r = SpyReplayer()
    r._leaf_ok = {"hotkey": False}
    result = r.run_steps([{"tool": "hotkey", "keys": ["enter"]}], prefix="failed.")

    assert result["ok"] is False
    assert result["error"] == "hotkey failed"


def test_replayer_preflight_fails_before_actions_when_target_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod.win32, "find_window", lambda title: None)
    script = {
        "format": "wps/2",
        "target_window": "Missing App",
        "steps": [{"tool": "click", "coords": [1, 2]}],
    }

    r = SpyReplayer()
    out = r.run(_write(tmp_path, script))

    assert out["ok"] is False
    assert out["preflight"]["required"] is True
    assert r.leaf_calls == []
    assert out["results"][0]["index"] == "preflight"
    assert out["results"][0]["tool"] == "preflight.target_window"
    assert out["results"][0]["ok"] is False
    assert 'title="Missing App"' in out["results"][0]["error"]


def test_in_memory_segments_share_window_variables_and_results(monkeypatch):
    monkeypatch.setattr(
        trace_mod.win32,
        "find_window",
        lambda title: {"hwnd": 456, "title": "Target App", "pid": 12},
    )
    r = SpyReplayer()

    first = r.run_steps(
        [{"tool": "ext.var_set", "name": "greeting", "value": "hello ${user}"}],
        target_window={"title": "Target App"},
        variables={"user": "alice"},
        prefix="set.",
    )
    second = r.run_steps(
        [{"tool": "type_text", "text_input": "${greeting}"}],
        target_window={"title": "Target App"},
        prefix="type.",
    )

    assert first["ok"] is True
    assert first["preflight"]["hwnd"] == 456
    assert first["results"] == [{"index": "set.1", "tool": "ext.var_set", "ok": True}]
    assert second["ok"] is True
    assert second["preflight"]["required"] is False
    assert second["results"] == [{"index": "type.1", "tool": "type_text", "ok": True}]
    assert r.hwnd == 456
    assert r.vars["greeting"] == "hello alice"
    assert r.leaf_calls[-1]["step"]["text_input"] == "hello alice"


def test_in_memory_segment_rejects_unknown_tools_before_execution():
    r = SpyReplayer()
    result = r.run_steps([{"tool": "custom.future"}], prefix="bad.")

    assert result["ok"] is False
    assert "unknown" in result["error"].lower() or "\u672a\u77e5" in result["error"]
    assert r.leaf_calls == []


def test_in_memory_segments_rebind_when_target_window_changes(monkeypatch):
    windows = {
        "First App": {"hwnd": 111, "title": "First App", "pid": 1},
        "Second App": {"hwnd": 222, "title": "Second App", "pid": 2},
    }
    monkeypatch.setattr(trace_mod.win32, "find_window", lambda title: windows.get(title))
    r = SpyReplayer()

    first = r.run_steps(
        [{"tool": "hotkey", "keys": ["enter"]}],
        target_window="First App",
        prefix="first.",
    )
    second = r.run_steps(
        [{"tool": "hotkey", "keys": ["esc"]}],
        target_window="Second App",
        prefix="second.",
    )

    assert first["preflight"]["hwnd"] == 111
    assert second["preflight"]["hwnd"] == 222
    assert r.hwnd == 222
