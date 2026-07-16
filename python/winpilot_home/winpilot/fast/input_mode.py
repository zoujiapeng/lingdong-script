"""Application-neutral input-mode inspection for deterministic GUI actions.

Continuous pointer paths are only meaningful when the target currently treats
them as freehand input.  This module describes that mechanism in terms of
accessible control semantics instead of application names, toolbar coordinates,
or task-specific symbols.  New UI families can extend the registry without
changing the interaction planner.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable


def _normalise(value: Any) -> str:
    return "".join(
        character
        for character in str(value or "").casefold()
        if character.isalnum() or "\u4e00" <= character <= "\u9fff"
    )


@dataclass(frozen=True)
class InputModeSpec:
    id: str
    aliases: tuple[str, ...]
    incompatible_aliases: tuple[str, ...] = ()
    requires_explicit_mode_when_conflicting: bool = True

    @property
    def normalised_aliases(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(_normalise(value) for value in self.aliases))

    @property
    def normalised_incompatible_aliases(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(_normalise(value) for value in self.incompatible_aliases)
        )


class InputModeRegistry:
    """Small extensible registry of interaction mechanisms, not applications."""

    _specs: dict[str, InputModeSpec] = {}

    @classmethod
    def register(cls, spec: InputModeSpec) -> None:
        cls._specs[spec.id] = spec

    @classmethod
    def get(cls, mode: str) -> InputModeSpec | None:
        return cls._specs.get(str(mode or "").strip().casefold())

    @classmethod
    def known_modes(cls) -> tuple[str, ...]:
        return tuple(sorted(cls._specs))


InputModeRegistry.register(InputModeSpec(
    id="continuous_freehand",
    aliases=(
        "brush", "brushes", "paint brush", "paintbrush", "pencil", "pen",
        "marker", "ink", "inking", "crayon", "freehand", "free hand",
        "drawing tool", "draw tool", "stylus",
        "\u753b\u7b14", "\u753b\u5237", "\u5237\u5b50", "\u94c5\u7b14", "\u94a2\u7b14", "\u9a6c\u514b\u7b14",
        "\u8bb0\u53f7\u7b14", "\u58a8\u8ff9", "\u624b\u7ed8", "\u81ea\u7531\u7ed8\u5236", "\u6d82\u9e26",
    ),
    incompatible_aliases=(
        "select", "selection", "crop", "eraser", "fill", "bucket",
        "eyedropper", "color picker", "text", "shape", "shapes", "line",
        "rectangle", "ellipse", "polygon", "zoom", "magnifier",
        "\u9009\u62e9", "\u88c1\u526a", "\u6a61\u76ae\u64e6", "\u6a61\u76ae", "\u586b\u5145", "\u989c\u8272\u9009\u62e9",
        "\u53d6\u8272", "\u6587\u672c", "\u5f62\u72b6", "\u76f4\u7ebf", "\u77e9\u5f62", "\u692d\u5706", "\u591a\u8fb9\u5f62", "\u7f29\u653e",
    ),
))


_ACTIONABLE_ROLE_TERMS = (
    "button", "radio", "menuitem", "checkbox", "tab", "listitem",
)
_ACTIVE_STATES = {"selected", "checked", "pressed", "active"}
_ACTIVE_TEXT_MARKERS = (
    "using", "currenttool", "activetool", "selectedtool",
    "\u6b63\u5728\u4f7f\u7528", "\u5f53\u524d\u5de5\u5177", "\u5df2\u9009\u62e9",
)
_NON_MODE_SUFFIXES = (
    "size", "width", "opacity", "colour", "color", "settings", "options",
    "\u5927\u5c0f", "\u5bbd\u5ea6", "\u4e0d\u900f\u660e\u5ea6", "\u989c\u8272", "\u8bbe\u7f6e", "\u9009\u9879",
)


def _matches_alias(text: str, aliases: Iterable[str]) -> tuple[bool, bool]:
    compact = _normalise(text)
    if not compact:
        return False, False
    raw = str(text or "").casefold()
    exact = False
    contained = False
    for alias in aliases:
        normalised = _normalise(alias)
        if not normalised:
            continue
        if compact == normalised:
            exact = True
            contained = True
            break
        if alias.isascii():
            if re.search(
                rf"(?<![a-z0-9]){re.escape(alias.casefold())}(?![a-z0-9])",
                raw,
            ):
                contained = True
        elif normalised in compact:
            contained = True
    return exact, contained


def _summary(element: Any) -> dict[str, Any]:
    return {
        "text": str(getattr(element, "text", "") or ""),
        "role": str(getattr(element, "role", "") or ""),
        "states": [str(value) for value in (getattr(element, "states", None) or [])],
        "source": str(getattr(element, "source", "") or ""),
        "x": int(getattr(element, "cx", 0) or 0),
        "y": int(getattr(element, "cy", 0) or 0),
    }


def inspect_input_mode(
    elements: Iterable[Any],
    mode: str,
) -> tuple[dict[str, Any], list[Any]]:
    """Return auditable mode evidence and ranked actionable candidates."""
    spec = InputModeRegistry.get(mode)
    if spec is None:
        return ({
            "ok": False,
            "requested_mode": str(mode or ""),
            "reason": "unsupported_input_mode",
            "known_modes": list(InputModeRegistry.known_modes()),
            "active_matches": [],
            "active_conflicts": [],
            "candidates": [],
        }, [])

    active_matches: list[dict[str, Any]] = []
    active_conflicts: list[dict[str, Any]] = []
    ranked: list[tuple[tuple[int, int, int, int], Any]] = []
    for element in elements:
        text = str(getattr(element, "text", "") or "").strip()
        role = str(getattr(element, "role", "") or "").casefold()
        states = {
            _normalise(value)
            for value in (getattr(element, "states", None) or [])
        }
        exact, matches = _matches_alias(text, spec.aliases)
        _conflict_exact, conflicts = _matches_alias(
            text, spec.incompatible_aliases,
        )
        actionable = any(value in role for value in _ACTIONABLE_ROLE_TERMS)
        explicitly_active = bool(states & _ACTIVE_STATES)
        compact_text = _normalise(text)
        status_active = any(marker in compact_text for marker in _ACTIVE_TEXT_MARKERS)
        summary = _summary(element)
        if matches and (explicitly_active or status_active):
            active_matches.append(summary)
        if conflicts and (explicitly_active or status_active):
            active_conflicts.append(summary)
        if not (matches and actionable):
            continue
        # Avoid treating a subordinate property such as "Brush size" as the
        # mode selector when the parent tool control is available.
        if not exact and any(
            suffix in compact_text for suffix in _NON_MODE_SUFFIXES
        ):
            continue
        source = str(getattr(element, "source", "") or "").casefold()
        source_rank = {"uia": 3, "win32": 2, "ocr": 1, "vlm": 0}.get(
            source, 0,
        )
        role_rank = 2 if any(value in role for value in ("radio", "button")) else 1
        ranked.append(((
            1 if explicitly_active else 0,
            1 if exact else 0,
            source_rank,
            role_rank,
        ), element))
    ranked.sort(key=lambda item: item[0], reverse=True)
    candidates = [element for _score, element in ranked]
    return ({
        "ok": True,
        "requested_mode": spec.id,
        "active_matches": active_matches,
        "active_conflicts": active_conflicts,
        "candidates": [_summary(element) for element in candidates[:8]],
        "explicit_mode_system": bool(
            active_matches or active_conflicts or candidates
        ),
    }, candidates)
