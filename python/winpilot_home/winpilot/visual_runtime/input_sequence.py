"""Forgiving model-facing grammar for generic physical input timelines."""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Final

from .keyboard_contracts import CANONICAL_HOTKEY_KEYS, canonical_hotkey_args


MAX_INPUT_SEQUENCE_EVENTS: Final = 512
MAX_POINTER_DURATION_MS: Final = 30_000
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_MOVE = re.compile(
    rf"^move\s+({_NUMBER})\s*(?:,|\s)\s*({_NUMBER})"
    rf"(?:\s+({_NUMBER})(?:\s*ms)?)?$",
    re.IGNORECASE,
)
_MOVE_RELATIVE = re.compile(
    rf"^(?:move_by|moveby|move_relative|relative_move)\s+"
    rf"({_NUMBER})\s*(?:,|\s)\s*({_NUMBER})"
    rf"(?:\s+({_NUMBER})(?:\s*ms)?)?$",
    re.IGNORECASE,
)
_BUTTONS = {
    "left": "left",
    "primary": "left",
    "right": "right",
    "secondary": "right",
    "middle": "middle",
    "x1": "x1",
    "back": "x1",
    "x2": "x2",
    "forward": "x2",
}
_COMMAND_ALIASES = {
    "down": "mouse_down",
    "mousedown": "mouse_down",
    "mouse-down": "mouse_down",
    "up": "mouse_up",
    "mouseup": "mouse_up",
    "mouse-up": "mouse_up",
    "keydown": "key_down",
    "key-down": "key_down",
    "keyup": "key_up",
    "key-up": "key_up",
    "pause": "wait",
    "sleep": "wait",
    "scroll": "wheel",
    "hscroll": "hwheel",
    "horizontal_scroll": "hwheel",
}


def _commands(value: str) -> list[str]:
    """Split commands while accepting natural comma-separated event lists.

    A comma inside ``move x,y`` is coordinate punctuation, so only a comma
    followed by a command word is treated as a timeline separator.
    """

    command_words = (
        "move|move_by|moveby|move_relative|relative_move|"
        "down|mousedown|mouse-down|mouse_down|up|mouseup|mouse-up|mouse_up|click|"
        "keydown|key-down|key_down|keyup|key-up|key_up|press|key|wheel|scroll|wait|pause|sleep"
        "|hwheel|hscroll|horizontal_scroll"
    )
    separator = rf";|\r?\n|\s+->\s+|,\s*(?=(?:{command_words})\b)"
    return [command.strip() for command in re.split(separator, value, flags=re.I) if command.strip()]


@dataclass(frozen=True, slots=True)
class InputSequenceStep:
    kind: str
    image_x: float | None = None
    image_y: float | None = None
    duration_ms: int = 0
    button: str | None = None
    key: str | None = None
    amount: int | None = None


def _duration(value: str | None, *, default: int = 0) -> int:
    if value is None:
        return default
    text = value.strip().casefold()
    if text.endswith("ms"):
        text = text[:-2]
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError("duration must be milliseconds") from exc
    if not math.isfinite(number) or not number.is_integer():
        raise ValueError("duration must be a whole number of milliseconds")
    duration = int(number)
    if not 0 <= duration <= MAX_POINTER_DURATION_MS:
        raise ValueError("duration must be in 0..30000 milliseconds")
    return duration


def _button(value: str) -> str:
    button = _BUTTONS.get(value.casefold())
    if button is None:
        raise ValueError(f"unknown mouse button {value!r}")
    return button


def _key(value: str) -> str:
    keys = canonical_hotkey_args([value])
    key = keys[0] if len(keys) == 1 else ""
    if key not in CANONICAL_HOTKEY_KEYS:
        raise ValueError(f"unknown key {value!r}")
    return key


def parse_input_sequence(value: str) -> tuple[InputSequenceStep, ...]:
    """Parse a small event grammar; it describes devices, never an app task."""

    if type(value) is not str or not value.strip():
        raise ValueError("input sequence must be a non-empty string")
    commands = _commands(value)
    steps: list[InputSequenceStep] = []
    for index, command in enumerate(commands):
        relative_move = _MOVE_RELATIVE.fullmatch(command)
        if relative_move is not None:
            dx, dy = float(relative_move.group(1)), float(relative_move.group(2))
            if not math.isfinite(dx) or not math.isfinite(dy) or (dx == 0 and dy == 0):
                raise ValueError(
                    f"sequence command {index + 1} has an invalid relative delta"
                )
            steps.append(InputSequenceStep(
                "move_relative",
                image_x=dx,
                image_y=dy,
                duration_ms=_duration(relative_move.group(3)),
            ))
            continue
        move = _MOVE.fullmatch(command)
        if move is not None:
            x, y = float(move.group(1)), float(move.group(2))
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError(f"sequence command {index + 1} has invalid coordinates")
            steps.append(InputSequenceStep(
                "move",
                image_x=x,
                image_y=y,
                duration_ms=_duration(move.group(3)),
            ))
            continue

        fields = command.split()
        if not fields:
            continue
        name = _COMMAND_ALIASES.get(fields[0].casefold(), fields[0].casefold())
        arguments = fields[1:]
        try:
            if name in {"mouse_down", "mouse_up"} and len(arguments) == 1:
                steps.append(InputSequenceStep(name, button=_button(arguments[0])))
            elif name == "click" and 1 <= len(arguments) <= 2:
                button = _button(arguments[0])
                press_ms = _duration(arguments[1] if len(arguments) == 2 else None, default=35)
                steps.append(InputSequenceStep("mouse_down", button=button))
                if press_ms:
                    steps.append(InputSequenceStep("wait", duration_ms=press_ms))
                steps.append(InputSequenceStep("mouse_up", button=button))
            elif name in {"key_down", "key_up"} and len(arguments) == 1:
                steps.append(InputSequenceStep(name, key=_key(arguments[0])))
            elif name in {"press", "key"} and 1 <= len(arguments) <= 2:
                key = _key(arguments[0])
                press_ms = _duration(arguments[1] if len(arguments) == 2 else None, default=35)
                steps.append(InputSequenceStep("key_down", key=key))
                if press_ms:
                    steps.append(InputSequenceStep("wait", duration_ms=press_ms))
                steps.append(InputSequenceStep("key_up", key=key))
            elif name in {"wheel", "hwheel"} and len(arguments) == 1:
                amount = int(arguments[0])
                if amount == 0 or abs(amount) > 1200:
                    raise ValueError(
                        "wheel amount must be a non-zero native delta in -1200..1200"
                    )
                steps.append(InputSequenceStep(name, amount=amount))
            elif name == "wait" and len(arguments) == 1:
                steps.append(InputSequenceStep("wait", duration_ms=_duration(arguments[0])))
            else:
                raise ValueError("unknown command or wrong argument count")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid sequence command {index + 1} {command!r}: {exc}") from exc
        if len(steps) > MAX_INPUT_SEQUENCE_EVENTS:
            raise ValueError("input sequence expands beyond 512 physical events")
    if not steps:
        raise ValueError("input sequence contains no physical events")
    return tuple(steps)


__all__ = [
    "InputSequenceStep",
    "MAX_INPUT_SEQUENCE_EVENTS",
    "MAX_POINTER_DURATION_MS",
    "parse_input_sequence",
]
