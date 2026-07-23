"""Strict, immutable contracts for read-only desktop target discovery."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
import os
import re
from typing import Iterable

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
DISCOVER_TARGET_TOOL_NAME = "discover_target"
ACTIVATE_TARGET_TOOL_NAME = "activate_target"
_CANDIDATE_FINGERPRINT = re.compile(r"target-candidates-[0-9a-f]{64}")
_TARGET_CANDIDATE_FIELDS = (
    "format",
    "candidate_ref",
    "activation",
    "state",
    "label",
    "provenance",
    "window_title",
    "hwnd",
    "process_id",
    "process_identity",
    "launch_kind",
    "launch_target",
)


class TargetContractError(ValueError):
    pass


class TargetActivation(str, Enum):
    SWITCH_WINDOW = "switch_window"
    OPEN_APP = "open_app"


class TargetState(str, Enum):
    VISIBLE = "visible"
    RECOVERABLE = "recoverable"
    INSTALLED = "installed"


def discover_target_tool_schema() -> dict[str, object]:
    """Canonical Actor schema for one read-only target query."""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_TARGET_QUERY_CHARS,
                "pattern": r"[\s\S]*\S[\s\S]*",
            },
        },
        "required": ["query"],
    }


def activate_target_tool_schema() -> dict[str, object]:
    """Canonical Actor schema for choosing one current private candidate."""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "candidate_ordinal": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_TARGET_CANDIDATES,
            },
        },
        "required": ["candidate_ordinal"],
    }


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

    def to_dict(self) -> dict[str, object]:
        """Serialize the one canonical Runtime-private candidate schema."""

        return {
            "format": self.format,
            "candidate_ref": self.candidate_ref,
            "activation": self.activation.value,
            "state": self.state.value,
            "label": self.label,
            "provenance": list(self.provenance),
            "window_title": self.window_title,
            "hwnd": self.hwnd,
            "process_id": self.process_id,
            "process_identity": self.process_identity,
            "launch_kind": self.launch_kind,
            "launch_target": self.launch_target,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "TargetCandidate":
        """Parse strict JSON wire data without creating a second schema owner."""

        if type(raw) is not dict:
            raise TargetContractError("TargetCandidate must be a JSON object")
        missing = set(_TARGET_CANDIDATE_FIELDS) - set(raw)
        extra = set(raw) - set(_TARGET_CANDIDATE_FIELDS)
        if missing or extra:
            raise TargetContractError(
                "invalid TargetCandidate fields; "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
        if raw["format"] != TARGET_CANDIDATE_FORMAT:
            raise TargetContractError("unsupported TargetCandidate format")
        provenance = raw["provenance"]
        if type(provenance) is not list:
            raise TargetContractError("TargetCandidate provenance must be a JSON array")
        return cls(
            format=raw["format"],
            candidate_ref=raw["candidate_ref"],
            activation=raw["activation"],
            state=raw["state"],
            label=raw["label"],
            provenance=tuple(provenance),
            window_title=raw["window_title"],
            hwnd=raw["hwnd"],
            process_id=raw["process_id"],
            process_identity=raw["process_identity"],
            launch_kind=raw["launch_kind"],
            launch_target=raw["launch_target"],
        )


def candidate_fingerprint_for(candidates: tuple[TargetCandidate, ...]) -> str:
    """Hash the exact ordered private mapping and its model-visible semantics."""

    if (
        not isinstance(candidates, tuple)
        or len(candidates) > MAX_TARGET_CANDIDATES
        or any(not isinstance(item, TargetCandidate) for item in candidates)
    ):
        raise TargetContractError("candidate fingerprint requires a bounded tuple")
    material = [
        {
            "candidate_ref": item.candidate_ref,
            "activation": item.activation.value,
            "state": item.state.value,
            "label": item.label,
            "window_title": item.window_title,
        }
        for item in candidates
    ]
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "target-candidates-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def canonical_candidate_fingerprint(value: object) -> str:
    if type(value) is not str or not _CANDIDATE_FINGERPRINT.fullmatch(value):
        raise TargetContractError("candidate fingerprint is invalid")
    return value

@dataclass(frozen=True, slots=True)
class TargetDiscoverySnapshot:
    query: str
    candidates: tuple[TargetCandidate, ...]
    captured_at: float
    notices: tuple[str, ...] = ()
    format: str = TARGET_DISCOVERY_FORMAT
    candidate_fingerprint: str = field(init=False)

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
        if (
            not isinstance(self.notices, tuple)
            or len(self.notices) > MAX_TARGET_CANDIDATES
            or any(
                type(item) is not str for item in self.notices
            )
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
        fingerprint = candidate_fingerprint_for(self.candidates)
        object.__setattr__(
            self, "candidate_fingerprint", canonical_candidate_fingerprint(fingerprint),
        )

__all__ = [
    "ACTIVATE_TARGET_TOOL_NAME", "DISCOVER_TARGET_TOOL_NAME",
    "MAX_TARGET_CANDIDATES", "MAX_TARGET_QUERY_CHARS",
    "TARGET_CANDIDATE_FORMAT", "TARGET_DISCOVERY_FORMAT",
    "TargetActivation", "TargetCandidate", "TargetContractError",
    "TargetDiscoverySnapshot", "TargetState", "activate_target_tool_schema",
    "candidate_fingerprint_for", "canonical_candidate_fingerprint",
    "candidate_ref_for", "discover_target_tool_schema",
    "launch_identity_for", "process_identity_for",
]
