"""Minimal model-facing grammar for a physical input timeline."""
from __future__ import annotations

from dataclasses import dataclass
import re

from .keyboard_contracts import canonical_hotkey_args


@dataclass(frozen=True, slots=True)
class InputSequenceStep:
    kind: str
    image_x: float | None = None
    image_y: float | None = None
    duration_ms: int = 0
    button: str | None = None
    key: str | None = None
    amount: int | None = None
    text: str | None = None


def _commands(value: str) -> list[str]:
    """Split the timeline without treating quoted text as punctuation."""

    commands: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char in ";\r\n":
            command = value[start:index].strip()
            if command:
                commands.append(command)
            start = index + 1
    if quote is not None:
        raise ValueError("input sequence contains an unterminated quoted string")
    command = value[start:].strip()
    if command:
        commands.append(command)
    return commands


def _literal_text(command: str) -> str | None:
    match = re.fullmatch(r"(?:type|type_text)\s+(.+)", command, flags=re.I | re.S)
    if match is None:
        return None
    value = match.group(1).strip()
    if value[:1] not in {"'", '"'}:
        return value
    quote = value[0]
    if len(value) < 2 or value[-1] != quote:
        raise ValueError("type_text requires one quoted string")
    body = value[1:-1]
    text: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == quote:
            raise ValueError("type_text requires one quoted string")
        if (
            char == "\\"
            and index + 1 < len(body)
            and body[index + 1] in {"\\", quote}
        ):
            index += 1
            char = body[index]
        text.append(char)
        index += 1
    return "".join(text)


def _fields(command: str) -> list[str]:
    command = re.sub(r"\bduration_ms\s*=\s*", "", command, flags=re.I)
    return command.replace(",", " ").split()


def _milliseconds(value: str) -> int:
    return int(round(float(value.casefold().removesuffix("ms"))))


def _point(values: list[str]) -> tuple[float, float]:
    if len(values) != 2:
        raise ValueError("pointer coordinates require x and y")
    return float(values[0]), float(values[1])


def _pointer(values: list[str]) -> tuple[str, tuple[float, float] | None]:
    values = list(values)
    if len(values) in {0, 2}:
        button = "left"
    elif len(values) in {1, 3}:
        button = values.pop(0).casefold()
    else:
        raise ValueError("pointer event requires an optional button and x,y")
    return button, _point(values) if values else None


def parse_input_sequence(value: str) -> tuple[InputSequenceStep, ...]:
    """Translate text into events; the physical event contract validates them."""

    if type(value) is not str or not value.strip():
        raise ValueError("input sequence must be a non-empty string")
    commands = _commands(value)
    steps: list[InputSequenceStep] = []
    for index, command in enumerate(commands, start=1):
        try:
            text = _literal_text(command)
            if text is not None:
                steps.append(InputSequenceStep("type_text", text=text))
                continue
            fields = _fields(command)
            if len(fields) > 1 and fields[0].casefold() in {"key", "mouse"}:
                name = f"{fields[0]}_{fields[1]}".casefold()
                arguments = fields[2:]
            else:
                name = fields[0].casefold()
                arguments = fields[1:]

            if name in {"move", "move_by"}:
                if len(arguments) not in {2, 3}:
                    raise ValueError("move requires x, y, and optional duration")
                x, y = _point(arguments[:2])
                steps.append(InputSequenceStep(
                    "move_relative" if name == "move_by" else "move",
                    image_x=x,
                    image_y=y,
                    duration_ms=(
                        _milliseconds(arguments[2]) if len(arguments) == 3 else 0
                    ),
                ))
            elif name in {"mouse_down", "mouse_up", "click"}:
                button, point = _pointer(arguments)
                if point is not None:
                    steps.append(InputSequenceStep(
                        "move", image_x=point[0], image_y=point[1],
                    ))
                if name == "click":
                    steps.extend((
                        InputSequenceStep("mouse_down", button=button),
                        InputSequenceStep("wait", duration_ms=35),
                        InputSequenceStep("mouse_up", button=button),
                    ))
                else:
                    steps.append(InputSequenceStep(name, button=button))
            elif name in {"key_down", "key_up"}:
                if len(arguments) != 1:
                    raise ValueError("key event requires one key")
                key = canonical_hotkey_args([arguments[0]])[0]
                steps.append(InputSequenceStep(name, key=key))
            elif name == "press":
                if len(arguments) not in {1, 2}:
                    raise ValueError("press requires one key and optional duration")
                key = canonical_hotkey_args([arguments[0]])[0]
                duration = _milliseconds(arguments[1]) if len(arguments) == 2 else 35
                steps.append(InputSequenceStep("key_down", key=key))
                if duration:
                    steps.append(InputSequenceStep("wait", duration_ms=duration))
                steps.append(InputSequenceStep("key_up", key=key))
            elif name in {"wheel", "hwheel"}:
                if len(arguments) != 1:
                    raise ValueError("wheel requires one amount")
                steps.append(InputSequenceStep(
                    name, amount=int(round(float(arguments[0]))),
                ))
            elif name == "wait":
                if len(arguments) != 1:
                    raise ValueError("wait requires one duration")
                steps.append(InputSequenceStep(
                    "wait", duration_ms=_milliseconds(arguments[0]),
                ))
            else:
                raise ValueError("unknown input command")
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid input command {index} {command!r}: {exc}") from exc
    if not steps:
        raise ValueError("input sequence contains no physical events")
    return tuple(steps)


__all__ = ["InputSequenceStep", "parse_input_sequence"]
