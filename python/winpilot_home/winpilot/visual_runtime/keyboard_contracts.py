"""Pure, owner-neutral contracts shared by keyboard schema and execution."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


MAX_TYPE_TEXT_CODEPOINTS = 4096
CANONICAL_HOTKEY_MODIFIERS = ("ctrl", "alt", "shift", "win")
CANONICAL_HOTKEY_KEYS = frozenset({
    *CANONICAL_HOTKEY_MODIFIERS,
    "enter", "esc", "tab", "space", "backspace", "delete", "insert",
    "home", "end", "pageup", "pagedown", "up", "down", "left", "right",
    "capslock", "numlock", "printscreen", "menu",
    "plus", "minus", "comma", "period", "slash",
    *(f"f{index}" for index in range(1, 13)),
    *tuple("abcdefghijklmnopqrstuvwxyz0123456789"),
})

_MODIFIER_ALIASES = {
    "control": "ctrl",
    "ctl": "ctrl",
    "option": "alt",
    "command": "win",
    "cmd": "win",
    "super": "win",
    "windows": "win",
}


def normalize_hotkey_args(raw: Any) -> list[str]:
    """Normalize model-produced hotkey arguments to a key-name list.

    The canonical layer absorbs representation differences (array-looking
    strings, ``Ctrl+C`` / comma-separated forms, nested arrays) so a schema
    format quirk does not burn an Actor turn on a format-repair round.
    Unknown names pass through unchanged; the HotkeyChord contract still
    rejects them mechanically.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        parsed: Any = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            if "[" in text and "]" in text:
                bracketed = text[text.find("["):text.rfind("]") + 1]
                try:
                    parsed = json.loads(bracketed)
                except json.JSONDecodeError:
                    parsed = None
        if isinstance(parsed, (list, tuple)):
            return [str(key) for key in parsed]
        if isinstance(parsed, str):
            text = parsed.strip()
        if "+" in text:
            parts = [part.strip() for part in text.split("+")]
            return [part for part in parts if part]
        if "," in text:
            parts = [part.strip(" \t\r\n'\"") for part in text.split(",")]
            return [part for part in parts if part]
        return [text] if text else []
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for item in raw:
            if isinstance(item, str) and item.strip().startswith("["):
                nested = normalize_hotkey_args(item)
                if len(nested) > 1:
                    out.extend(nested)
                    continue
            out.append(str(item))
        return out
    return [str(raw)]


def canonical_hotkey_args(keys: list[str]) -> list[str]:
    """Fold modifier aliases and order chord modifiers first.

    Preserves relative ordering of ordinary keys; a chord with no modifier
    keeps the submitted order.
    """
    folded = [
        _MODIFIER_ALIASES.get(key.casefold(), key.casefold())
        for key in keys
    ]
    modifiers = sorted(
        (key for key in folded if key in CANONICAL_HOTKEY_MODIFIERS),
        key=CANONICAL_HOTKEY_MODIFIERS.index,
    )
    ordinary = [key for key in folded if key not in CANONICAL_HOTKEY_MODIFIERS]
    return [*modifiers, *ordinary] if modifiers else folded


@dataclass(frozen=True, slots=True)
class HotkeyChord:
    """One key, or canonical modifiers followed by one primary key.

    A standalone modifier is a complete key press.  This matters for keys such
    as ``win`` whose ordinary GUI meaning is produced by pressing and releasing
    that key without a second key.
    """

    keys: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.keys) is not tuple:
            raise TypeError("hotkey keys must be a tuple of canonical names")
        keys = self.keys
        if (
            not 1 <= len(keys) <= len(CANONICAL_HOTKEY_MODIFIERS) + 1
            or any(type(key) is not str or key not in CANONICAL_HOTKEY_KEYS for key in keys)
            or len(keys) != len(set(keys))
            or (
                len(keys) > 1
                and keys[-1] in CANONICAL_HOTKEY_MODIFIERS
            )
        ):
            raise ValueError(
                "hotkey keys must be one key or unique canonical names ending "
                "in a primary key"
            )
        modifiers = keys[:-1]
        if (
            any(key not in CANONICAL_HOTKEY_MODIFIERS for key in modifiers)
            or tuple(sorted(
                modifiers, key=CANONICAL_HOTKEY_MODIFIERS.index,
            )) != modifiers
        ):
            raise ValueError(
                "hotkey chord must be ordered modifiers followed by one key"
            )


__all__ = [
    "CANONICAL_HOTKEY_KEYS",
    "CANONICAL_HOTKEY_MODIFIERS",
    "HotkeyChord",
    "MAX_TYPE_TEXT_CODEPOINTS",
]
