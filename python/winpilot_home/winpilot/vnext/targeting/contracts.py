"""Strict, immutable contracts for read-only desktop target discovery."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
import os
import re
from typing import Any, Iterable

from ...process_identity import (
    ProcessIdentityError,
    canonical_process_identity,
    process_identity_for,
)


TARGET_DISCOVERY_FORMAT = "target-discovery/0"
TARGET_CANDIDATE_FORMAT = "target-candidate/0"
_REF = re.compile(r"target-[0-9a-f]{24}")
_TOKEN = re.compile(r"[a-z][a-z0-9_]{0,63}")
MAX_TARGET_CANDIDATES = 16
MAX_TARGET_QUERY_CHARS = 256
MAX_TARGET_TEXT_CHARS = 512


class TargetContractError(ValueError):
    pass


class TargetActivation(str, Enum):
    SWITCH_WINDOW = "switch_window"
    OPEN_APP = "open_app"


class TargetState(str, Enum):
    VISIBLE = "visible"
    RECOVERABLE = "recoverable"
    INSTALLED = "installed"


def _text(value: object, name: str, *, maximum: int = MAX_TARGET_TEXT_CHARS) -> str:
    if type(value) is not str or not value.strip():
        raise TargetContractError(f"{name} must be a non-empty string")
    result = value.strip()
    if len(result) > maximum:
        raise TargetContractError(f"{name} is too long")
    return result


def _tokens(values: Iterable[object], name: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TargetContractError(f"{name} must be a tuple")
    result = tuple(_text(value, f"{name}[]", maximum=64) for value in values)
    if not result or len(result) != len(set(result)):
        raise TargetContractError(f"{name} must contain unique values")
    if any(not _TOKEN.fullmatch(value) for value in result):
        raise TargetContractError(f"{name} contains a non-canonical token")
    return tuple(sorted(result))


def candidate_ref_for(*, activation: TargetActivation | str, identity: str) -> str:
    try:
        route = TargetActivation(activation).value
    except ValueError as exc:
        raise TargetContractError("unsupported target activation") from exc
    material = json.dumps(
        {"activation": route, "identity": _text(identity, "identity", maximum=2048)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "target-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _launch_parts(*, kind: object, target: object) -> tuple[str, str, str]:
    launch_kind = _text(kind, "launch_kind", maximum=16).casefold()
    if launch_kind not in {"appid", "exe", "lnk"}:
        raise TargetContractError("unsupported launch_kind")
    launch_target = _text(
        os.path.expandvars(
            _text(target, "launch_target", maximum=2048).strip('"')
        ),
        "launch_target",
        maximum=2048,
    )
    if launch_kind != "appid":
        launch_target = os.path.normpath(launch_target)
    normalized = (
        launch_target.casefold()
        if launch_kind == "appid"
        else os.path.normcase(launch_target).casefold()
    )
    return launch_kind, launch_target, f"{launch_kind}:{normalized}"


def launch_identity_for(*, kind: object, target: object) -> str:
    return _launch_parts(kind=kind, target=target)[2]


def _window_ref_identity(
    *, hwnd: int, process_id: int, process_identity: str,
) -> str:
    return f"{hwnd}|{process_id}|{process_identity}"


@dataclass(frozen=True, slots=True)
class TargetCandidate:
    """Runtime-private candidate; ``actor_view`` redacts executable identities."""

    candidate_ref: str
    activation: TargetActivation | str
    state: TargetState | str
    label: str
    provenance: tuple[str, ...]
    window_title: str = ""
    hwnd: int | None = None
    process_id: int | None = None
    process_identity: str | None = None
    launch_kind: str | None = None
    launch_target: str | None = None
    format: str = TARGET_CANDIDATE_FORMAT

    def __post_init__(self) -> None:
        if self.format != TARGET_CANDIDATE_FORMAT:
            raise TargetContractError("unsupported TargetCandidate format")
        try:
            activation = TargetActivation(self.activation)
            state = TargetState(self.state)
        except ValueError as exc:
            raise TargetContractError("unsupported target candidate kind") from exc
        ref = _text(self.candidate_ref, "candidate_ref", maximum=31)
        if not _REF.fullmatch(ref):
            raise TargetContractError("candidate_ref is invalid")
        label = _text(self.label, "label")
        provenance = _tokens(self.provenance, "provenance")
        if type(self.window_title) is not str:
            raise TargetContractError("window_title must be a string")
        title = self.window_title.strip()
        if len(title) > MAX_TARGET_TEXT_CHARS:
            raise TargetContractError("window_title is too long")
        if activation is TargetActivation.SWITCH_WINDOW:
            if state not in {TargetState.VISIBLE, TargetState.RECOVERABLE}:
                raise TargetContractError("switch_window candidate has invalid state")
            if (
                type(self.hwnd) is not int or self.hwnd <= 0
                or type(self.process_id) is not int or self.process_id <= 0
                or type(self.process_identity) is not str
            ):
                raise TargetContractError("switch_window requires stable window/process identity")
            try:
                process_identity = canonical_process_identity(self.process_identity)
            except ProcessIdentityError as exc:
                raise TargetContractError(
                    "switch_window requires stable window/process identity"
                ) from exc
            if self.launch_kind is not None or self.launch_target is not None:
                raise TargetContractError("switch_window cannot contain launch identity")
            ref_identity = _window_ref_identity(
                hwnd=self.hwnd,
                process_id=self.process_id,
                process_identity=process_identity,
            )
        else:
            if state is not TargetState.INSTALLED:
                raise TargetContractError("open_app candidate must be installed")
            if self.hwnd is not None or self.process_id is not None or self.process_identity is not None:
                raise TargetContractError("open_app cannot contain a window identity")
            kind, target, ref_identity = _launch_parts(
                kind=self.launch_kind, target=self.launch_target,
            )

        if ref != candidate_ref_for(activation=activation, identity=ref_identity):
            raise TargetContractError("candidate_ref does not identify the candidate")

        object.__setattr__(self, "activation", activation)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "candidate_ref", ref)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "window_title", title)
        object.__setattr__(self, "provenance", provenance)
        if activation is TargetActivation.SWITCH_WINDOW:
            object.__setattr__(self, "process_identity", process_identity)
        else:
            object.__setattr__(self, "launch_kind", kind)
            object.__setattr__(self, "launch_target", target)

    def actor_view(self) -> dict[str, Any]:
        """Return only semantic selection facts, never HWND/PID/path/AppID."""

        return {
            "candidate_ref": self.candidate_ref,
            "activation": self.activation.value,
            "state": self.state.value,
            "label": self.label,
            "window_title": self.window_title or None,
            "provenance": list(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class TargetDiscoverySnapshot:
    query: str
    candidates: tuple[TargetCandidate, ...]
    captured_at: float
    notices: tuple[str, ...] = ()
    format: str = TARGET_DISCOVERY_FORMAT

    def __post_init__(self) -> None:
        if self.format != TARGET_DISCOVERY_FORMAT:
            raise TargetContractError("unsupported TargetDiscoverySnapshot format")
        query = _text(self.query, "query", maximum=MAX_TARGET_QUERY_CHARS)
        if (
            isinstance(self.candidates, (str, bytes))
            or not isinstance(self.candidates, tuple)
            or len(self.candidates) > MAX_TARGET_CANDIDATES
            or any(not isinstance(item, TargetCandidate) for item in self.candidates)
        ):
            raise TargetContractError("candidates must be a bounded TargetCandidate tuple")
        refs = tuple(item.candidate_ref for item in self.candidates)
        if len(refs) != len(set(refs)):
            raise TargetContractError("candidate_ref values must be unique")
        if type(self.captured_at) not in (int, float) or not math.isfinite(
            float(self.captured_at)
        ) or float(self.captured_at) < 0:
            raise TargetContractError("captured_at must be finite and non-negative")
        if not isinstance(self.notices, tuple) or any(
            type(item) is not str for item in self.notices
        ):
            raise TargetContractError("notices must be a tuple of strings")
        notices = tuple(item.strip() for item in self.notices)
        if any(not item or len(item) > MAX_TARGET_TEXT_CHARS for item in notices):
            raise TargetContractError("notices must contain bounded non-empty strings")
        if any(not _TOKEN.fullmatch(item) for item in notices):
            raise TargetContractError("notices must contain canonical runtime tokens")
        if len(notices) != len(set(notices)):
            raise TargetContractError("notices must be unique")
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "captured_at", float(self.captured_at))
        object.__setattr__(self, "notices", notices)

    def candidate(self, candidate_ref: str) -> TargetCandidate:
        matches = tuple(
            item for item in self.candidates if item.candidate_ref == candidate_ref
        )
        if len(matches) != 1:
            raise TargetContractError("candidate_ref is absent or ambiguous")
        return matches[0]

    def actor_view(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "query": self.query,
            "candidates": [item.actor_view() for item in self.candidates],
            "notices": list(self.notices),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.actor_view(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )


__all__ = [
    "MAX_TARGET_CANDIDATES", "MAX_TARGET_QUERY_CHARS",
    "TARGET_CANDIDATE_FORMAT", "TARGET_DISCOVERY_FORMAT",
    "TargetActivation", "TargetCandidate", "TargetContractError",
    "TargetDiscoverySnapshot", "TargetState", "candidate_ref_for",
    "launch_identity_for", "process_identity_for",
]
