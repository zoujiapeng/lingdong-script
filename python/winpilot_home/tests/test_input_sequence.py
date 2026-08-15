from __future__ import annotations

import pytest

from winpilot.visual_runtime.input_sequence import parse_input_sequence


def test_parser_accepts_human_friendly_mixed_device_timeline() -> None:
    steps = parse_input_sequence(
        "move 10,20 duration_ms=25; mouse_down left; key_down Control_L; "
        "move_by 5,-4 duration_ms=10; key_up ctrl\nmouse_up left; wheel -360; "
        "hwheel 120; press f24 0ms"
    )

    assert [step.kind for step in steps] == [
        "move",
        "mouse_down",
        "key_down",
        "move_relative",
        "key_up",
        "mouse_up",
        "wheel",
        "hwheel",
        "key_down",
        "key_up",
    ]
    assert (steps[0].image_x, steps[0].image_y, steps[0].duration_ms) == (
        10.0,
        20.0,
        25,
    )
    assert steps[1].button == "left"
    assert steps[2].key == "ctrl"
    assert (
        steps[3].image_x,
        steps[3].image_y,
        steps[3].duration_ms,
    ) == (5.0, -4.0, 10)
    assert steps[6].amount == -360
    assert steps[7].amount == 120
    assert steps[8].key == "f24"


def test_pointer_buttons_accept_inline_coordinates_and_default_left() -> None:
    steps = parse_input_sequence(
        "mouse_down left 10,20; mouse_up 30 40; mouse_down; mouse_up left"
    )

    assert [step.kind for step in steps] == [
        "move",
        "mouse_down",
        "move",
        "mouse_up",
        "mouse_down",
        "mouse_up",
    ]
    assert (steps[0].image_x, steps[0].image_y) == (10.0, 20.0)
    assert (steps[2].image_x, steps[2].image_y) == (30.0, 40.0)
    assert [step.button for step in steps if step.button] == ["left"] * 4


@pytest.mark.parametrize(
    ("sequence", "field", "value"),
    (
        ("move_by 0,0", "kind", "move_relative"),
        ("mouse_down unknown", "button", "unknown"),
        ("key_down not-a-key", "key", "not-a-key"),
        ("wheel 0", "amount", 0),
        ("wheel 1201", "amount", 1201),
        ("wait 1.5", "duration_ms", 2),
    ),
)
def test_parser_leaves_device_validation_to_the_physical_event_contract(
    sequence: str,
    field: str,
    value: object,
) -> None:
    step = parse_input_sequence(sequence)[0]

    assert getattr(step, field) == value


def test_parser_accepts_observed_click_and_two_word_variants() -> None:
    steps = parse_input_sequence(
        "click 550,255; key down c; key up c; "
        "mouse down left 10 20; mouse up 30,40; wait duration_ms=1.5"
    )

    assert [step.kind for step in steps] == [
        "move",
        "mouse_down",
        "wait",
        "mouse_up",
        "key_down",
        "key_up",
        "move",
        "mouse_down",
        "move",
        "mouse_up",
        "wait",
    ]
    assert (steps[0].image_x, steps[0].image_y) == (550.0, 255.0)
    assert steps[2].duration_ms == 35
    assert steps[4].key == steps[5].key == "c"
    assert steps[-1].duration_ms == 2


@pytest.mark.parametrize(
    "sequence",
    (
        "move 1",
        "mouse_down left 1",
        "click left 1 2 3 4",
        "unknown_command 1",
    ),
)
def test_parser_rejects_only_text_that_cannot_form_an_event(sequence: str) -> None:
    with pytest.raises(ValueError):
        parse_input_sequence(sequence)
