from __future__ import annotations

import pytest

from winpilot.visual_runtime.input_sequence import parse_input_sequence


def test_parser_accepts_human_friendly_mixed_device_timeline() -> None:
    steps = parse_input_sequence(
        "move 10,20 25ms, mouse_down primary -> key_down Control_L; "
        "move_by 5,-4 10; key_up ctrl\nmouse_up left; wheel -360; "
        "hscroll 120; press f24 0ms"
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
    assert (steps[3].image_x, steps[3].image_y) == (5.0, -4.0)
    assert steps[6].amount == -360
    assert steps[7].amount == 120
    assert steps[8].key == "f24"


@pytest.mark.parametrize(
    "sequence",
    (
        "move 1",
        "move_by 0,0",
        "mouse_down unknown",
        "key_down not-a-key",
        "wheel 0",
        "wheel 1201",
        "wait 1.5",
    ),
)
def test_parser_rejects_only_mechanically_unusable_commands(sequence: str) -> None:
    with pytest.raises(ValueError):
        parse_input_sequence(sequence)
