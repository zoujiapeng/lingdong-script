"""Unit tests for hotkey argument normalization in the keyboard contracts."""
from __future__ import annotations

from winpilot.visual_runtime.keyboard_contracts import (
    HotkeyChord,
    canonical_hotkey_args,
    normalize_hotkey_args,
)


def test_normalize_accepts_array() -> None:
    assert normalize_hotkey_args(["ctrl", "s"]) == ["ctrl", "s"]


def test_normalize_accepts_plus_separated_string() -> None:
    assert normalize_hotkey_args("Ctrl+S") == ["Ctrl", "S"]


def test_normalize_accepts_comma_separated_string() -> None:
    assert normalize_hotkey_args("ctrl, alt, delete") == ["ctrl", "alt", "delete"]


def test_normalize_accepts_json_array_string() -> None:
    assert normalize_hotkey_args('["ctrl","s"]') == ["ctrl", "s"]


def test_normalize_accepts_bracketed_snippet_with_extra_text() -> None:
    assert normalize_hotkey_args('prefix ["ctrl","s"] suffix') == ["ctrl", "s"]


def test_normalize_none_returns_empty() -> None:
    assert normalize_hotkey_args(None) == []


def test_normalize_passthrough_unknown_names() -> None:
    assert normalize_hotkey_args(["bogus_key"]) == ["bogus_key"]


def test_canonical_folds_aliases() -> None:
    assert canonical_hotkey_args(["control", "option", "command", "x"]) == [
        "ctrl", "alt", "win", "x",
    ]


def test_canonical_orders_modifiers_first_in_canonical_order() -> None:
    assert canonical_hotkey_args(["s", "shift", "ctrl"]) == ["ctrl", "shift", "s"]


def test_canonical_keeps_modifierless_order() -> None:
    assert canonical_hotkey_args(["f5"]) == ["f5"]


def test_canonicalized_chord_passes_hotkey_contract() -> None:
    chord = HotkeyChord(tuple(canonical_hotkey_args(normalize_hotkey_args("shift+cmd+s"))))
    assert chord.keys == ("shift", "win", "s")
