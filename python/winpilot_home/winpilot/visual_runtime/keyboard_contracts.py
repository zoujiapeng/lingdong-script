"""Pure, owner-neutral contracts shared by keyboard schema and execution."""
from __future__ import annotations

from dataclasses import dataclass


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
