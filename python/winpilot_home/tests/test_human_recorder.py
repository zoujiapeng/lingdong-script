"""Human input recording into replay-safe .wps.json scripts."""
import json
import sys
from types import SimpleNamespace

from winpilot.recorder import human as human_mod
from winpilot.recorder.human import HumanRecorder, StepBuilder, _normalize_key_char


def test_printable_keys_coalesce_into_one_type_text():
    b = StepBuilder()
    for ch in "hi":
        b.key_down(ch)
    assert b.finish() == [{"tool": "type_text", "text_input": "hi"}]


def test_space_is_buffered_as_text():
    b = StepBuilder()
    b.key_down("a"); b.key_down("space"); b.key_down("b")
    assert b.finish() == [{"tool": "type_text", "text_input": "a b"}]


def test_special_key_flushes_then_emits_hotkey():
    b = StepBuilder()
    b.key_down("a"); b.key_down("enter")
    assert b.finish() == [
        {"tool": "type_text", "text_input": "a"},
        {"tool": "hotkey", "keys": ["enter"]},
    ]


def test_modifier_combo_emits_hotkey_and_flushes_buffer():
    b = StepBuilder()
    b.key_down("x")
    b.key_down("ctrl"); b.key_down("s"); b.key_up("ctrl")
    assert b.finish() == [
        {"tool": "type_text", "text_input": "x"},
        {"tool": "hotkey", "keys": ["ctrl", "s"]},
    ]


def test_shift_alone_is_not_a_combo():
    b = StepBuilder()
    b.key_down("shift"); b.key_down("A"); b.key_up("shift")
    assert b.finish() == [{"tool": "type_text", "text_input": "A"}]


def test_click_carries_selector():
    b = StepBuilder()
    b.mouse_press(10, 20, "left", {"text": "确定", "role": "button", "source": "uia"})
    b.mouse_release(11, 21, "left")  # within threshold → click
    assert b.finish() == [{
        "tool": "click", "coords": [10, 20], "button": "left",
        "allow_no_change": True,
        "text": "确定", "role": "button", "source": "uia",
    }]


def test_click_without_selector_has_no_text():
    b = StepBuilder()
    b.mouse_press(5, 5, "right", None)
    b.mouse_release(5, 5, "right")
    assert b.finish() == [{
        "tool": "click", "coords": [5, 5], "button": "right",
        "allow_no_change": True,
    }]


def test_drag_when_release_is_far():
    b = StepBuilder()
    b.mouse_press(0, 0, "left", None)
    b.mouse_move(50, 0)
    b.mouse_move(100, 0)
    b.mouse_release(100, 0, "left")
    steps = b.finish()
    assert len(steps) == 1
    d = steps[0]
    assert d["tool"] == "drag"
    assert d["from"] == [0, 0]
    assert d["to"] == [100, 0]
    assert [50, 0] in d["points"]


def test_typed_text_before_click_is_flushed_first():
    b = StepBuilder()
    b.key_down("h"); b.key_down("i")
    b.mouse_press(1, 2, "left", None)
    b.mouse_release(1, 2, "left")
    assert b.finish() == [
        {"tool": "type_text", "text_input": "hi"},
        {
            "tool": "click", "coords": [1, 2], "button": "left",
            "allow_no_change": True,
        },
    ]


def test_ctrl_keycode_control_character_normalizes_to_letter():
    assert _normalize_key_char("\x16") == "v"
    assert _normalize_key_char("v") == "v"


def test_text_paste_reuses_type_text_with_frozen_clipboard_content():
    target = {"title": "Editor", "process": "editor.exe"}
    b = StepBuilder()
    b.key_down("ctrl", target)
    b.key_down("v", target, pasted_text="paste now, replay later")
    b.key_up("ctrl")

    assert b.finish() == [{
        "tool": "type_text",
        "text_input": "paste now, replay later",
        "target_window": target,
    }]


def test_non_text_or_unavailable_paste_keeps_normalized_hotkey():
    b = StepBuilder()
    b.key_down("ctrl")
    b.key_down("v", pasted_text=None)
    b.key_up("ctrl")

    assert b.finish() == [{"tool": "hotkey", "keys": ["ctrl", "v"]}]


def test_printable_text_flushes_when_the_target_window_changes():
    first = {"title": "First", "process": "first.exe"}
    second = {"title": "Second", "process": "second.exe"}
    b = StepBuilder()
    b.key_down("a", first)
    b.key_down("b", second)

    assert b.finish() == [
        {"tool": "type_text", "text_input": "a", "target_window": first},
        {"tool": "type_text", "text_input": "b", "target_window": second},
    ]


def _window(hwnd, pid, title, process):
    return {"hwnd": hwnd, "pid": pid, "title": title, "process": process}


def test_recorder_snapshots_clipboard_when_paste_event_is_queued(monkeypatch):
    recorder = HumanRecorder()
    recorder._builder = StepBuilder()
    target = _window(20, 2, "Document - Editor", "editor.exe")
    clipboard = {"text": "captured before the worker runs"}
    monkeypatch.setattr(human_mod, "_key_name", lambda key: key)
    monkeypatch.setattr(recorder, "_foreground_window", lambda: target)
    monkeypatch.setattr(
        recorder,
        "_read_clipboard_text",
        lambda: clipboard["text"],
    )

    recorder._queue_key("kdown", "ctrl")
    recorder._queue_key("kdown", "v")
    clipboard["text"] = "changed after paste"
    recorder._queue_key("kup", "v")
    recorder._queue_key("kup", "ctrl")
    recorder._stop.set()
    recorder._drain()

    assert recorder._builder.finish() == [{
        "tool": "type_text",
        "text_input": "captured before the worker runs",
        "target_window": {
            "title": "Document - Editor",
            "process": "editor.exe",
        },
    }]


def test_recorder_retries_a_transient_clipboard_lock(monkeypatch):
    attempts = {"open": 0, "close": 0}

    def open_clipboard():
        attempts["open"] += 1
        if attempts["open"] < 3:
            raise OSError("clipboard is temporarily locked")

    monkeypatch.setitem(sys.modules, "win32clipboard", SimpleNamespace(
        CF_UNICODETEXT=13,
        OpenClipboard=open_clipboard,
        CloseClipboard=lambda: attempts.__setitem__("close", attempts["close"] + 1),
        IsClipboardFormatAvailable=lambda _format: True,
        GetClipboardData=lambda _format: "frozen text",
    ))
    monkeypatch.setattr(human_mod.time, "sleep", lambda _seconds: None)

    assert HumanRecorder()._read_clipboard_text() == "frozen text"
    assert attempts == {"open": 3, "close": 1}


def test_recorder_excludes_control_click_and_persists_stable_target(tmp_path, monkeypatch):
    recorder = HumanRecorder()
    recorder._builder = StepBuilder()
    recorder._name = "lesson"
    recorder._control_window = _window(10, 1, "WinPilot", "electron.exe")
    recorder._resolve_selector = lambda x, y, hwnd=None: {
        "text": "Save", "role": "button", "source": "uia",
    }
    target = _window(20, 2, "Document - Editor", "editor.exe")
    recorder._queue.put(("click", 100, 200, "left", True, target))
    recorder._queue.put(("click", 100, 200, "left", False, target))
    recorder._queue.put(("click", 10, 10, "left", True, recorder._control_window))
    recorder._queue.put(("click", 10, 10, "left", False, recorder._control_window))
    recorder._stop.set()
    recorder._drain()
    monkeypatch.setattr(human_mod, "SCRIPTS_DIR", tmp_path)

    result = recorder.stop()
    script = json.loads((tmp_path / result["file"]).read_text(encoding="utf-8"))

    assert result == {"ok": True, "file": "lesson.wps.json", "steps": 1}
    assert script["target_window"] == {
        "title": "Document - Editor",
        "process": "editor.exe",
    }
    assert "hwnd" not in script["target_window"]
    assert script["steps"] == [{
        "tool": "click",
        "coords": [100, 200],
        "button": "left",
        "allow_no_change": True,
        "text": "Save",
        "role": "button",
        "source": "uia",
        "target_window": {"title": "Document - Editor", "process": "editor.exe"},
    }]


def test_recorder_keeps_first_anchor_when_actions_cross_windows(tmp_path, monkeypatch):
    recorder = HumanRecorder()
    recorder._builder = StepBuilder()
    recorder._name = "unsafe"
    recorder._resolve_selector = lambda x, y, hwnd=None: None
    first = _window(20, 2, "First", "first.exe")
    second = _window(30, 3, "Second", "second.exe")
    for context, x in ((first, 10), (second, 20)):
        recorder._queue.put(("click", x, 10, "left", True, context))
        recorder._queue.put(("click", x, 10, "left", False, context))
    recorder._stop.set()
    recorder._drain()
    monkeypatch.setattr(human_mod, "SCRIPTS_DIR", tmp_path)

    result = recorder.stop()
    script = json.loads((tmp_path / result["file"]).read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert result["steps"] == 2
    assert script["target_window"] == {"title": "First", "process": "first.exe"}
    assert [step["target_window"] for step in script["steps"]] == [
        {"title": "First", "process": "first.exe"},
        {"title": "Second", "process": "second.exe"},
    ]


def test_recorder_cancels_drag_released_over_another_window():
    recorder = HumanRecorder()
    recorder._builder = StepBuilder()
    recorder._resolve_selector = lambda x, y, hwnd=None: None
    first = _window(20, 2, "First", "first.exe")
    second = _window(30, 3, "Second", "second.exe")
    recorder._queue.put(("click", 10, 10, "left", True, first))
    recorder._queue.put(("move", 100, 10))
    recorder._queue.put(("click", 100, 10, "left", False, second))
    recorder._stop.set()

    recorder._drain()

    assert recorder._builder.finish() == []


def test_recorder_freezes_target_metadata_until_foreground_hwnd_changes():
    recorder = HumanRecorder()
    recorder._builder = StepBuilder()
    before_enter = _window(20, 2, "Replay Target A", "pythonw.exe")
    after_enter = _window(20, 2, "Replay Target A | initial", "pythonw.exe")
    recorder._queue.put(("kdown", "a", before_enter, None))
    recorder._queue.put(("kdown", "enter", after_enter, None))
    recorder._stop.set()

    recorder._drain()

    frozen = {"title": "Replay Target A", "process": "pythonw.exe"}
    assert recorder._builder.finish() == [
        {"tool": "type_text", "text_input": "a", "target_window": frozen},
        {"tool": "hotkey", "keys": ["enter"], "target_window": frozen},
    ]


def test_recorder_refreshes_target_metadata_after_focus_leaves_and_returns():
    recorder = HumanRecorder()
    recorder._builder = StepBuilder()
    first_a = _window(20, 2, "Replay Target A", "pythonw.exe")
    changed_a = _window(20, 2, "Replay Target A | initial", "pythonw.exe")
    window_b = _window(30, 2, "Replay Target B", "pythonw.exe")
    recorder._queue.put(("kdown", "a", first_a, None))
    recorder._queue.put(("kdown", "enter", changed_a, None))
    recorder._queue.put(("kdown", "b", window_b, None))
    recorder._queue.put(("kdown", "c", changed_a, None))
    recorder._stop.set()

    recorder._drain()

    assert recorder._builder.finish() == [
        {
            "tool": "type_text",
            "text_input": "a",
            "target_window": {"title": "Replay Target A", "process": "pythonw.exe"},
        },
        {
            "tool": "hotkey",
            "keys": ["enter"],
            "target_window": {"title": "Replay Target A", "process": "pythonw.exe"},
        },
        {
            "tool": "type_text",
            "text_input": "b",
            "target_window": {"title": "Replay Target B", "process": "pythonw.exe"},
        },
        {
            "tool": "type_text",
            "text_input": "c",
            "target_window": {
                "title": "Replay Target A | initial",
                "process": "pythonw.exe",
            },
        },
    ]


def test_recorder_rejects_empty_demonstration(tmp_path, monkeypatch):
    recorder = HumanRecorder()
    recorder._builder = StepBuilder()
    recorder._name = "empty"
    recorder._stop.set()
    monkeypatch.setattr(human_mod, "SCRIPTS_DIR", tmp_path)

    result = recorder.stop()

    assert result["ok"] is False
    assert "no executable actions" in result["error"]
    assert not (tmp_path / "empty.wps.json").exists()
