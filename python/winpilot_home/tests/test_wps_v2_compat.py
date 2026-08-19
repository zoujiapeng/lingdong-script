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
        self.leaf_calls.append({"idx": idx, "tool": step.get("tool"), "step": step, "hwnd": self.hwnd})
        return {"ok": self._leaf_ok.get(step.get("tool"), True)}

    def _check_condition(self, cond, timeout_s):
        return self._cond.pop(0) if self._cond else False


def _write(tmp_path, data):
    path = tmp_path / "s.wps.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _stub_window_inventory(monkeypatch, windows, processes):
    monkeypatch.setattr(trace_mod.win32, "list_windows", lambda: [dict(win) for win in windows])
    monkeypatch.setattr(
        trace_mod.win32,
        "_process_name",
        lambda pid: processes.get(pid, ""),
        raising=False,
    )
    monkeypatch.setattr(
        trace_mod.win32,
        "title_contains",
        lambda title, expected: expected.casefold() in title.casefold(),
    )


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
         "body": [{"tool": "click", "coords": [1, 2]}]},
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


def test_v2_ext_delay_cancellation_is_prompt_and_not_success(tmp_path):
    class CancelOnWait:
        def __init__(self):
            self.wait_calls = []
            self._set = False

        def is_set(self):
            return self._set

        def wait(self, timeout=None):
            self.wait_calls.append(timeout)
            self._set = True
            return True

    stop_event = CancelOnWait()
    script = {"format": "wps/2", "steps": [{"tool": "ext.delay", "seconds": 10}]}
    r = trace_mod.Replayer(stop_event)
    r.step_delay = 0
    out = r.run(_write(tmp_path, script))

    assert stop_event.wait_calls == [10.0]
    assert stop_event.is_set()
    assert out["ok"] is False
    assert out["results"][0]["ok"] is False
    assert out["results"][0]["stopped"] is True


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


def test_replayer_executes_recorded_agent_utility_and_device_actions(monkeypatch):
    from winpilot.action import clipboard_ops, shell
    from winpilot.perception import apps

    calls = []
    monkeypatch.setattr(
        apps, "launch_application",
        lambda app, arguments="": calls.append(("launch", app, arguments)) or {"ok": True},
    )
    monkeypatch.setattr(
        trace_mod.executor, "move_mouse",
        lambda x, y, hwnd=None: calls.append(("move", x, y, hwnd)) or {"ok": True},
    )
    monkeypatch.setattr(
        trace_mod.executor, "execute_admitted_physical_sequence",
        lambda events, hwnd=None, stop_event=None: (
            calls.append(("sequence", events, hwnd, stop_event))
            or SimpleNamespace(facts={"ok": True, "event_count": len(events)})
        ),
    )
    monkeypatch.setattr(
        shell, "run",
        lambda command, **kwargs: calls.append(("shell", command, kwargs)) or {"ok": True},
    )
    monkeypatch.setattr(
        shell, "write_file",
        lambda path, content: calls.append(("write", path, content)) or {"ok": True},
    )
    monkeypatch.setattr(
        clipboard_ops, "capture_screen_backend",
        lambda **kwargs: calls.append(("capture", kwargs)) or {"ok": True},
    )
    monkeypatch.setattr(
        clipboard_ops, "paste_image_backend",
        lambda **kwargs: calls.append(("paste", kwargs)) or {"ok": True},
    )
    monkeypatch.setattr(
        clipboard_ops, "copy_image_file_backend",
        lambda **kwargs: calls.append(("copy", kwargs)) or {"ok": True},
    )
    replay = trace_mod.Replayer()
    replay.hwnd = 123

    results = [
        replay._run_step("1", {"tool": "launch_application", "app": "notepad",
                               "arguments": "note.txt"}),
        replay._run_step("2", {"tool": "move_mouse", "coords": [11, 12]}),
        replay._run_step("3", {"tool": "input_sequence", "events": [
            {"kind": "key_down", "key": "shift"},
            {"kind": "key_up", "key": "shift"},
        ]}),
        replay._run_step("4", {"tool": "run_shell", "command": "Get-Date",
                               "cwd": "C:/tmp"}),
        replay._run_step("5", {"tool": "write_file", "path": "C:/tmp/a.txt",
                               "content": "hello"}),
        replay._run_step("6", {"tool": "capture_screen", "path": "C:/tmp/a.jpg",
                               "window": True}),
        replay._run_step("7", {"tool": "paste_image_file", "path": "C:/tmp/a.jpg"}),
        replay._run_step("8", {
            "tool": "copy_current_view",
            "reference_frame": {"path": "C:/tmp/view.jpg"},
        }),
    ]

    assert all(result["ok"] is True for result in results)
    assert [call[0] for call in calls] == [
        "launch", "move", "sequence", "shell", "write", "capture", "paste", "copy",
    ]
    assert [event.kind.value for event in calls[2][1]] == ["key_down", "key_up"]
    assert calls[3][2]["cwd"] == "C:/tmp"


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


def test_relocate_uses_nested_visual_anchor_label_before_recorded_coords(monkeypatch):
    queries = []
    monkeypatch.setattr(
        trace_mod.uia,
        "find_by_text",
        lambda hwnd, text: queries.append((hwnd, text)) or [
            SimpleNamespace(
                text=text, cx=410, cy=260, source="uia",
                left=370, top=245, width=80, height=30,
            ),
        ],
    )
    monkeypatch.setattr(
        trace_mod,
        "window_region",
        lambda hwnd: trace_mod.Region(0, 0, 800, 600),
    )

    r = trace_mod.Replayer()
    r.hwnd = 123
    point = r._relocate({
        "tool": "click",
        "coords": [1, 2],
        "anchor": {"label": "Moving target", "role": "button"},
    })

    assert point == (410, 260)
    assert queries == [(123, "Moving target")]
    assert r._last_relocation["source"] == "uia"


def test_replay_preserves_recorded_visual_servo_before_physical_click(monkeypatch):
    clicks = []
    moves = []
    r = trace_mod.Replayer()
    r.hwnd = 123
    monkeypatch.setattr(
        trace_mod,
        "window_region",
        lambda hwnd: trace_mod.Region(0, 0, 800, 600),
    )
    monkeypatch.setattr(
        trace_mod.executor,
        "move_mouse",
        lambda x, y: moves.append((x, y)),
    )
    monkeypatch.setattr(
        r,
        "_relocate",
        lambda step, anchor_key="": (
            r._last_relocation.update({
                "source": "uia",
                "point": [100, 120],
                "bbox": [70, 105, 60, 30],
            })
            or (100, 120)
        ),
    )
    from winpilot.fast import visual_servo as visual_servo_mod

    monkeypatch.setattr(
        visual_servo_mod,
        "track_visual_target",
        lambda initial, labels, **kwargs: (
            trace_mod.UIElement(
                elem_id=initial.elem_id,
                source="servo-template",
                role="button",
                text="Moving target",
                cx=180,
                cy=140,
                left=150,                top=125,
                width=60,
                height=30,
                confidence=0.95,
            ),
            {"enabled": True, "confirmed": True, "correction_count": 1},
        ),
    )
    monkeypatch.setattr(
        trace_mod.verify,
        "run_with_verify",
        lambda _name, action, **_kwargs: action() | {"verified": True},
    )
    monkeypatch.setattr(
        trace_mod.executor,
        "click",
        lambda x, y, **kwargs: clicks.append((x, y, kwargs)) or {"ok": True},
    )

    result = r._run_step("2", {
        "tool": "click",
        "coords": [1, 2],
        "anchor": {"label": "Moving target", "role": "button"},
        "resolved_element": {"source": "servo-template", "role": "button"},
        "expect": {"text_appears": "VISUAL_SERVO_OK"},
    })

    assert result["ok"] is True
    assert result["visual_servo"]["confirmed"] is True
    assert result["resolved"]["point"] == [180, 140]
    assert moves == [(788, 588)]
    assert result["visual_servo"]["arm_point"] == {"x": 788, "y": 588}
    assert clicks == [(180, 140, {
        "button": "left", "double": False, "hwnd": 123, "prepositioned": True,
    })]


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


def test_click_without_expect_succeeds_without_pixel_change_probe(monkeypatch):
    calls = []
    replay = trace_mod.Replayer()
    replay.hwnd = 123
    monkeypatch.setattr(
        replay,
        "_capture_action_surface",
        lambda: (_ for _ in ()).throw(AssertionError("click must not capture pixel proof")),
    )
    monkeypatch.setattr(trace_mod.verify, "post_state_summary", lambda: {})
    monkeypatch.setattr(
        trace_mod.executor,
        "click",
        lambda *_args, **_kwargs: calls.append(1) or {"ok": True},
    )

    result = replay._run_step("1", {"tool": "click", "coords": [10, 20]})

    assert calls == [1]
    assert result["ok"] is True
    assert result["attempts"] == 1
    assert "verified" not in result


def test_click_failed_expect_does_not_repeat_physical_action(monkeypatch):
    calls = []
    replay = trace_mod.Replayer()
    replay.hwnd = 123
    monkeypatch.setattr(trace_mod.verify, "post_state_summary", lambda: {})
    monkeypatch.setattr(
        trace_mod.verify,
        "check",
        lambda *_args, **_kwargs: trace_mod.verify.VerifyOutcome(False, "not found", "uia"),
    )
    monkeypatch.setattr(
        trace_mod.executor,
        "click",
        lambda *_args, **_kwargs: calls.append(1) or {"ok": True},
    )

    result = replay._run_step("1", {
        "tool": "click",
        "coords": [10, 20],
        "expect": {"text_appears": "Never"},
    })

    assert calls == [1]
    assert result["attempts"] == 1
    assert result["verified"] is False


def test_draw_failed_expect_does_not_repeat_physical_action(monkeypatch):
    calls = []
    replay = trace_mod.Replayer()
    replay.hwnd = 123
    monkeypatch.setattr(trace_mod.verify, "post_state_summary", lambda: {})
    monkeypatch.setattr(
        trace_mod.verify,
        "check",
        lambda *_args, **_kwargs: trace_mod.verify.VerifyOutcome(False, "not found", "uia"),
    )
    monkeypatch.setattr(
        trace_mod.executor,
        "draw_strokes",
        lambda *_args, **_kwargs: calls.append(1) or {"ok": True},
    )

    result = replay._run_step("1", {
        "tool": "draw_strokes",
        "strokes": [[[1, 2], [3, 4]]],
        "expect": {"text_appears": "Never"},
    })

    assert calls == [1]
    assert result["attempts"] == 1
    assert result["verified"] is False


def test_replayer_preflight_binds_declared_target_window(tmp_path, monkeypatch):
    focused = []
    monkeypatch.setattr(
        trace_mod.win32,
        "find_window",
        lambda title: {"hwnd": 456, "title": "Target App", "pid": 12} if title == "Target App" else None,
    )
    monkeypatch.setattr(
        trace_mod.executor, "focus_window",
        lambda hwnd: focused.append(hwnd) or {"ok": True, "hwnd": hwnd},
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
    assert focused == [456]
    assert [c["tool"] for c in r.leaf_calls] == ["click"]


def test_replayer_rebinds_and_focuses_each_explicit_step_target(tmp_path, monkeypatch):
    windows = {
        "First": {"hwnd": 111, "title": "First", "pid": 1},
        "Second": {"hwnd": 222, "title": "Second", "pid": 2},
    }
    focused = []
    r = SpyReplayer()
    monkeypatch.setattr(r, "_find_target_window", lambda hint: windows.get(hint.get("title")))
    monkeypatch.setattr(
        trace_mod.executor, "focus_window",
        lambda hwnd: focused.append(hwnd) or {"ok": True, "hwnd": hwnd},
    )
    target = lambda title, process: {"title": title, "process": process}
    script = {
        "format": "wps/2",
        "target_window": target("First", "first.exe"),
        "steps": [
            {"tool": "hotkey", "keys": ["enter"], "target_window": target("First", "first.exe")},
            {"tool": "hotkey", "keys": ["tab"], "target_window": target("Second", "second.exe")},
            {"tool": "hotkey", "keys": ["esc"], "target_window": target("First", "first.exe")},
        ],
    }

    out = r.run(_write(tmp_path, script))

    assert out["ok"] is True
    assert [call["hwnd"] for call in r.leaf_calls] == [111, 222, 111]
    assert focused == [111, 111, 222, 111]


def test_replayer_keeps_same_live_process_bound_across_complete_title_drift(tmp_path, monkeypatch):
    windows = [{"hwnd": 111, "title": "Document - Editor", "pid": 1}]
    _stub_window_inventory(monkeypatch, windows, {1: "editor.exe"})
    focused = []
    monkeypatch.setattr(
        trace_mod.executor,
        "focus_window",
        lambda hwnd: focused.append(hwnd) or {"ok": True, "hwnd": hwnd},
    )
    target = {"title": "Document - Editor", "process": "editor.exe"}
    script = {
        "format": "wps/2",
        "target_window": target,
        "steps": [
            {"tool": "hotkey", "keys": ["enter"], "target_window": target},
            {"tool": "hotkey", "keys": ["esc"], "target_window": target},
        ],
    }
    r = SpyReplayer()
    run_step = r._run_step

    def mutate_title_after_first_action(idx, step):
        result = run_step(idx, step)
        if idx == "1":
            windows[0]["title"] = "A completely different title"
        return result

    monkeypatch.setattr(r, "_run_step", mutate_title_after_first_action)

    out = r.run(_write(tmp_path, script))

    assert out["ok"] is True
    assert [call["hwnd"] for call in r.leaf_calls] == [111, 111]
    assert focused == [111, 111, 111]


def test_replayer_rejects_title_drift_when_bound_hwnd_process_changes(monkeypatch):
    windows = [{"hwnd": 111, "title": "Document - Editor", "pid": 1}]
    processes = {1: "editor.exe"}
    _stub_window_inventory(monkeypatch, windows, processes)
    monkeypatch.setattr(trace_mod.executor, "focus_window", lambda hwnd: {"ok": True, "hwnd": hwnd})
    target = {"title": "Document - Editor", "process": "editor.exe"}
    r = SpyReplayer()

    first = r.run_steps([{"tool": "hotkey", "keys": ["enter"]}], target_window=target)
    windows[0]["title"] = "A completely different title"
    processes[1] = "other.exe"
    second = r.run_steps([{"tool": "hotkey", "keys": ["esc"]}], target_window=target)

    assert first["ok"] is True
    assert second["ok"] is False
    assert "target window not found" in second["error"]
    assert [call["tool"] for call in r.leaf_calls] == ["hotkey"]


def test_replayer_does_not_accept_title_drift_without_process_identity(monkeypatch):
    windows = [{"hwnd": 111, "title": "Document - Editor", "pid": 1}]
    _stub_window_inventory(monkeypatch, windows, {})
    monkeypatch.setattr(
        trace_mod.win32,
        "find_window",
        lambda title: next(
            (dict(win) for win in windows if title.casefold() in win["title"].casefold()),
            None,
        ),
    )
    monkeypatch.setattr(trace_mod.executor, "focus_window", lambda hwnd: {"ok": True, "hwnd": hwnd})
    r = SpyReplayer()

    first = r.run_steps([{"tool": "hotkey", "keys": ["enter"]}], target_window="Document - Editor")
    windows[0]["title"] = "A completely different title"
    second = r.run_steps([{"tool": "hotkey", "keys": ["esc"]}], target_window="Document - Editor")

    assert first["ok"] is True
    assert second["ok"] is False
    assert [call["tool"] for call in r.leaf_calls] == ["hotkey"]


def test_replayer_fully_resolves_changed_signature_instead_of_reusing_bound_hwnd(monkeypatch):
    windows = [
        {"hwnd": 111, "title": "First", "pid": 1},
        {"hwnd": 222, "title": "Second", "pid": 2},
    ]
    _stub_window_inventory(monkeypatch, windows, {1: "editor.exe", 2: "editor.exe"})
    monkeypatch.setattr(trace_mod.executor, "focus_window", lambda hwnd: {"ok": True, "hwnd": hwnd})
    r = SpyReplayer()

    first = r.run_steps(
        [{"tool": "hotkey", "keys": ["enter"]}],
        target_window={"title": "First", "process": "editor.exe"},
    )
    second = r.run_steps(
        [{"tool": "hotkey", "keys": ["esc"]}],
        target_window={"title": "Second", "process": "editor.exe"},
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert [call["hwnd"] for call in r.leaf_calls] == [111, 222]


def test_replayer_fully_resolves_same_signature_when_bound_hwnd_disappears(monkeypatch):
    windows = [{"hwnd": 111, "title": "Document - Editor", "pid": 1}]
    _stub_window_inventory(monkeypatch, windows, {1: "editor.exe", 2: "editor.exe"})
    monkeypatch.setattr(trace_mod.executor, "focus_window", lambda hwnd: {"ok": True, "hwnd": hwnd})
    target = {"title": "Document - Editor", "process": "editor.exe"}
    r = SpyReplayer()

    first = r.run_steps([{"tool": "hotkey", "keys": ["enter"]}], target_window=target)
    windows[:] = [{"hwnd": 222, "title": "Document - Editor", "pid": 2}]
    second = r.run_steps([{"tool": "hotkey", "keys": ["esc"]}], target_window=target)
    windows.clear()
    third = r.run_steps([{"tool": "hotkey", "keys": ["tab"]}], target_window=target)

    assert first["ok"] is True
    assert second["ok"] is True
    assert third["ok"] is False
    assert [call["hwnd"] for call in r.leaf_calls] == [111, 222]


def test_replayer_fails_closed_before_a_missing_step_target(tmp_path, monkeypatch):
    r = SpyReplayer()
    monkeypatch.setattr(
        r, "_find_target_window",
        lambda hint: {"hwnd": 111, "title": "First", "pid": 1}
        if hint.get("title") == "First" else None,
    )
    monkeypatch.setattr(trace_mod.executor, "focus_window", lambda hwnd: {"ok": True, "hwnd": hwnd})
    script = {
        "format": "wps/2",
        "target_window": "First",
        "steps": [
            {"tool": "hotkey", "keys": ["enter"], "target_window": "First"},
            {"tool": "hotkey", "keys": ["tab"], "target_window": "Missing"},
        ],
    }

    out = r.run(_write(tmp_path, script))

    assert out["ok"] is False
    assert [call["hwnd"] for call in r.leaf_calls] == [111]
    assert out["results"][-1]["index"] == "2"
    assert "target window not found" in out["results"][-1]["error"]


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
    monkeypatch.setattr(trace_mod.executor, "focus_window", lambda hwnd: {"ok": True, "hwnd": hwnd})
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
    monkeypatch.setattr(trace_mod.executor, "focus_window", lambda hwnd: {"ok": True, "hwnd": hwnd})
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


def test_in_memory_segments_revalidate_same_target_before_each_action(monkeypatch):
    resolved = iter([
        {"hwnd": 111, "title": "Target App", "pid": 1},
        {"hwnd": 222, "title": "Target App", "pid": 2},
    ])
    focused = []
    r = SpyReplayer()
    monkeypatch.setattr(r, "_find_target_window", lambda hint: next(resolved))
    monkeypatch.setattr(
        trace_mod.executor, "focus_window",
        lambda hwnd: focused.append(hwnd) or {"ok": True, "hwnd": hwnd},
    )

    first = r.run_steps(
        [{"tool": "hotkey", "keys": ["enter"]}],
        target_window={"title": "Target App", "process": "target.exe"},
    )
    second = r.run_steps(
        [{"tool": "hotkey", "keys": ["esc"]}],
        target_window={"title": "Target App", "process": "target.exe"},
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert [call["hwnd"] for call in r.leaf_calls[-2:]] == [111, 222]
    assert focused == [111, 222]


def test_replay_rejects_malformed_declared_postflight_evidence():
    result = SpyReplayer().run_script({
        "format": "wps/2",
        "evidence": {"required": "must exist"},
        "steps": [{"tool": "ext.delay", "seconds": 0}],
    })

    assert result["ok"] is False
    assert result["postflight"]["required"] is True
    assert "must be an array" in result["postflight"]["error"]


def test_automation_ir_desktop_replay_allows_human_screen_coordinates(tmp_path):
    from winpilot.automation_ir import automation_ir_from_wps

    ir = automation_ir_from_wps({
        "format": "wps/2",
        "name": "human screen coordinates",
        "steps": [{"tool": "click", "coords": [10, 20]}],
    })
    replayer = SpyReplayer()
    result = replayer.run(_write(tmp_path, ir))

    assert result["ok"] is True
    assert result["preflight"]["required"] is False
    assert [call["tool"] for call in replayer.leaf_calls] == ["click"]


def test_replay_preflight_enforces_structured_conditions(tmp_path):
    marker = tmp_path / "ready.txt"
    marker.write_text("ready", encoding="utf-8")
    script = {
        "format": "wps/2",
        "conditions": {"checks": [{"file_exists": str(marker)}]},
        "steps": [{"tool": "ext.delay", "seconds": 0}],
    }

    result = SpyReplayer().run(_write(tmp_path, script))

    assert result["ok"] is True
    assert result["preflight"]["required"] is True
    assert result["preflight"]["checks"][0]["ok"] is True


def test_replay_postflight_enforces_typed_evidence(tmp_path):
    marker = tmp_path / "result.txt"
    marker.write_text("done", encoding="utf-8")
    script = {
        "format": "wps/2",
        "evidence": {"required": [{"predicate": "file.exists", "value": str(marker)}]},
        "steps": [{"tool": "ext.delay", "seconds": 0}],
    }

    result = SpyReplayer().run(_write(tmp_path, script))
    marker.unlink()
    missing = SpyReplayer().run(_write(tmp_path, script))

    assert result["ok"] is True
    assert result["postflight"]["required"] is True
    assert result["postflight"]["checks"][0]["ok"] is True
    assert missing["ok"] is False
    assert "evidence requirement failed" in missing["postflight"]["error"]
