"""StepBuilder: pure human-input → .wps.json step aggregation."""
from winpilot.recorder.human import StepBuilder


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
        "text": "确定", "role": "button", "source": "uia",
    }]


def test_click_without_selector_has_no_text():
    b = StepBuilder()
    b.mouse_press(5, 5, "right", None)
    b.mouse_release(5, 5, "right")
    assert b.finish() == [{"tool": "click", "coords": [5, 5], "button": "right"}]


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
        {"tool": "click", "coords": [1, 2], "button": "left"},
    ]
