"""Pure, versioned state contracts for the vNext mission shadow.

This module deliberately has no imports from the agent loop, model adapter,
desktop controller, perception stack, or ``task-contract/1``.  The objects are
small immutable values; the reducer is the only place that turns accepted
events into a new projection.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Iterable

from ..json_contract import (
    BoundedJSONError,
    MAX_JSON_STRING_LENGTH,
    MAX_SIGNED_64,
    MIN_SIGNED_64,
    bounded_json_copy,
    freeze_bounded_json,
)


JSONValue = Any


MISSION_DEFINITION_FORMAT = "mission-definition/0"
MISSION_EVENT_FORMAT = "mission-event/0"
MISSION_PROJECTION_FORMAT = "mission-projection/1"
MISSION_CARD_FORMAT = "mission-card/1"
ACTION_EFFECT_FORMAT = "action-effect/0"
ACTION_EFFECT_COMPARATOR_VERSION = "frame-delta/0"
ACTION_EFFECT_EVIDENCE_KIND = "deterministic_action_effect"
# ``unchanged`` is intentionally a high-precision fact: any decoded pixel
# delta is reported as ``changed``.  A non-zero threshold can turn a real
# small-control response (caret, checkbox, focus ring) into false no-progress.
ACTION_EFFECT_CHANGE_RATIO_THRESHOLD = 0.0
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_MISSING_PAYLOAD = object()
_SAFE_ACTION_SUMMARY_BY_ACTION = {
    "click": frozenset({"click"}),
    "double_click": frozenset({"double_click"}),
    "drag": frozenset({"drag"}),
    "scroll": frozenset({"scroll_down", "scroll_up"}),
    "type_text": frozenset({"type_text"}),
}
_HOTKEY_SUMMARY_MODIFIERS = ("ctrl", "alt", "shift", "win")
_HOTKEY_SUMMARY_PRIMARY_KEYS = frozenset(
    {
        "enter",
        "esc",
        "tab",
        "space",
        "backspace",
        "delete",
        "insert",
        "home",
        "end",
        "pageup",
        "pagedown",
        "up",
        "down",
        "left",
        "right",
        "capslock",
        "numlock",
        "printscreen",
        "menu",
        "plus",
        "minus",
        "comma",
        "period",
        "slash",
        *(f"f{index}" for index in range(1, 13)),
        *tuple("abcdefghijklmnopqrstuvwxyz0123456789"),
    }
)


def thaw(value: Any) -> Any:
    """Return ordinary JSON-compatible containers for serialization."""
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Freeze an internal typed mapping without erasing its value objects.

    Projection indexes intentionally contain ``EvidenceRef`` and
    ``ArtifactRef`` instances, which are not JSONValue objects.  They are
    immutable dataclass values and therefore safe to keep behind a mapping
    proxy.  JSON-bearing contract fields use ``_json_mapping`` below.
    """
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise TypeError("value must be a JSON object mapping")
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("mapping keys must be strings")
        normalized[key] = item
    return MappingProxyType(normalized)


def _json_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Freeze and validate a mapping whose values must be strict JSONValue."""
    if type(value) is not dict:
        raise BoundedJSONError("value must be a plain JSON object")
    frozen = freeze_bounded_json(value)
    if not isinstance(frozen, MappingProxyType):  # pragma: no cover - invariant
        raise BoundedJSONError("value must be a plain JSON object")
    return frozen


class MissionError(ValueError):
    """Base error for an invalid definition, event, or state transition."""


class InvalidMissionDefinition(MissionError):
    pass


class InvalidMissionEvent(MissionError):
    pass


class StaleRevisionError(MissionError):
    pass


class DuplicateEventError(MissionError):
    pass


class EvidenceScopeError(MissionError):
    pass


class IllegalTransitionError(MissionError):
    pass


def _contract_text(
    value: Any,
    name: str,
    error_type: type[MissionError],
    *,
    allow_empty: bool = False,
) -> str:
    if type(value) is not str:
        raise error_type(f"{name} must be a literal string")
    if len(value) > MAX_JSON_STRING_LENGTH:
        raise error_type(
            f"{name} exceeds the maximum string length "
            f"{MAX_JSON_STRING_LENGTH}"
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise error_type(f"{name} contains an invalid Unicode scalar") from exc
    if not allow_empty and not value.strip():
        raise error_type(f"{name} cannot be empty")
    return value


def _contract_bool(
    value: Any,
    name: str,
    error_type: type[MissionError],
) -> bool:
    if type(value) is not bool:
        raise error_type(f"{name} must be a literal boolean")
    return value


def _contract_int(
    value: Any,
    name: str,
    error_type: type[MissionError],
    *,
    minimum: int = MIN_SIGNED_64,
    maximum: int = MAX_SIGNED_64,
) -> int:
    if type(value) is not int:
        raise error_type(f"{name} must be a literal integer")
    if not minimum <= value <= maximum:
        raise error_type(f"{name} must be in range [{minimum}, {maximum}]")
    return value


def _contract_float(
    value: Any,
    name: str,
    error_type: type[MissionError],
) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise error_type(f"{name} must be a finite literal float")
    return value


def _contract_string_tuple(
    value: Any,
    name: str,
    error_type: type[MissionError],
    *,
    allow_none: bool = False,
) -> tuple[str, ...]:
    if value is None and allow_none:
        return ()
    if type(value) not in (list, tuple):
        raise error_type(f"{name} must be a string sequence")
    result = tuple(
        _contract_text(item, f"{name}[]", error_type)
        for item in value
    )
    return result


def _as_string_tuple(value: Any) -> tuple[str, ...]:
    """Strict internal event sequence parser retained for reducer helpers."""
    return _contract_string_tuple(
        value,
        "event string sequence",
        InvalidMissionEvent,
        allow_none=True,
    )


def _wire_object(
    raw: Any,
    fields: Iterable[str],
    name: str,
    error_type: type[MissionError],
) -> dict[str, Any]:
    if type(raw) is not dict:
        raise error_type(f"{name} must be a plain JSON object")
    try:
        value = bounded_json_copy(raw)
    except BoundedJSONError as exc:
        raise error_type(f"{name} violates the bounded JSON contract: {exc}") from exc
    expected = set(fields)
    actual = set(value)
    missing = expected - actual
    unknown = actual - expected
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing={sorted(missing)!r}")
        if unknown:
            details.append(f"unknown={sorted(unknown)!r}")
        raise error_type(
            f"{name} has non-canonical fields ({', '.join(details)})"
        )
    return value


def _wire_string_list(
    value: Any,
    name: str,
    error_type: type[MissionError],
) -> tuple[str, ...]:
    if type(value) is not list:
        raise error_type(f"{name} must be a JSON array of strings")
    return _contract_string_tuple(value, name, error_type)


def _wire_optional_text(
    value: Any,
    name: str,
    error_type: type[MissionError],
) -> str | None:
    if value is None:
        return None
    return _contract_text(value, name, error_type)


def _validate_contract_wire(
    wire: Any,
    name: str,
    error_type: type[MissionError],
) -> None:
    """Ensure a directly constructed contract can round-trip over its wire."""
    try:
        bounded_json_copy(wire)
    except BoundedJSONError as exc:
        raise error_type(
            f"{name} violates the bounded JSON contract: {exc}"
        ) from exc


class ObligationStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    CLAIMED = "claimed"
    VERIFIED = "verified"
    BLOCKED = "blocked"
    SUPERSEDED = "superseded"
    CANCELED = "canceled"


class MissionStatus(str, Enum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_EXTERNAL = "waiting_external"
    AWAITING_MISSION_VERIFICATION = "awaiting_mission_verification"
    BLOCKED = "blocked"
    VERIFIED = "verified"
    FAILED = "failed"
    CANCELED = "canceled"


class LocalVisualChange(str, Enum):
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    UNKNOWN = "unknown"


class ActionEffectReason(str, Enum):
    EXACT_PAYLOAD = "exact_payload"
    ALIGNED_PIXEL_DELTA = "aligned_pixel_delta"
    INPUT_NOT_EXECUTED = "input_not_executed"
    TARGET_BINDING_CHANGED = "target_binding_changed"
    FRAME_NOT_COMPARABLE = "frame_not_comparable"
    DECODE_FAILED = "decode_failed"
    DECODED_SIZE_MISMATCH = "decoded_size_mismatch"
    COMPARISON_FAILED = "comparison_failed"


DEFAULT_ALLOWED_TRUST = (
    "deterministic",
    "application_oracle",
    "provider_receipt",
    "trusted_external_harness",
    "independent_visual_model",
)


@dataclass(frozen=True, slots=True)
class EvidencePolicy:
    """Evidence requirements owned by an obligation, not by a model."""

    required_evidence_kinds: tuple[str, ...] = ()
    preferred_evidence_kinds: tuple[str, ...] = ()
    allow_independent_visual_verifier: bool = False
    max_verification_attempts: int = 1
    uncertain_policy: str = "blocked"

    _FIELDS = (
        "required_evidence_kinds",
        "preferred_evidence_kinds",
        "allow_independent_visual_verifier",
        "max_verification_attempts",
        "uncertain_policy",
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "required_evidence_kinds",
            _contract_string_tuple(
                self.required_evidence_kinds,
                "required_evidence_kinds",
                InvalidMissionDefinition,
            ),
        )
        object.__setattr__(
            self,
            "preferred_evidence_kinds",
            _contract_string_tuple(
                self.preferred_evidence_kinds,
                "preferred_evidence_kinds",
                InvalidMissionDefinition,
            ),
        )
        object.__setattr__(
            self,
            "allow_independent_visual_verifier",
            _contract_bool(
                self.allow_independent_visual_verifier,
                "allow_independent_visual_verifier",
                InvalidMissionDefinition,
            ),
        )
        object.__setattr__(
            self,
            "max_verification_attempts",
            _contract_int(
                self.max_verification_attempts,
                "max_verification_attempts",
                InvalidMissionDefinition,
                minimum=1,
            ),
        )
        policy = _contract_text(
            self.uncertain_policy,
            "uncertain_policy",
            InvalidMissionDefinition,
        )
        if policy not in {"collect_more", "ask_user", "blocked", "fail"}:
            raise InvalidMissionDefinition("invalid uncertain_policy")
        object.__setattr__(self, "uncertain_policy", policy)
        _validate_contract_wire(
            self.to_dict(),
            "EvidencePolicy",
            InvalidMissionDefinition,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_evidence_kinds": list(self.required_evidence_kinds),
            "preferred_evidence_kinds": list(self.preferred_evidence_kinds),
            "allow_independent_visual_verifier": self.allow_independent_visual_verifier,
            "max_verification_attempts": self.max_verification_attempts,
            "uncertain_policy": self.uncertain_policy,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvidencePolicy":
        value = _wire_object(
            raw,
            cls._FIELDS,
            "EvidencePolicy",
            InvalidMissionDefinition,
        )
        return cls(
            required_evidence_kinds=_wire_string_list(
                value["required_evidence_kinds"],
                "required_evidence_kinds",
                InvalidMissionDefinition,
            ),
            preferred_evidence_kinds=_wire_string_list(
                value["preferred_evidence_kinds"],
                "preferred_evidence_kinds",
                InvalidMissionDefinition,
            ),
            allow_independent_visual_verifier=_contract_bool(
                value["allow_independent_visual_verifier"],
                "allow_independent_visual_verifier",
                InvalidMissionDefinition,
            ),
            max_verification_attempts=_contract_int(
                value["max_verification_attempts"],
                "max_verification_attempts",
                InvalidMissionDefinition,
                minimum=1,
            ),
            uncertain_policy=_contract_text(
                value["uncertain_policy"],
                "uncertain_policy",
                InvalidMissionDefinition,
            ),
        )


@dataclass(frozen=True, slots=True)
class ObligationSpec:
    """A desired result state; it contains no UI route or action steps."""

    id: str
    desired_state: str
    acceptance_criteria: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    forbidden_effects: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    input_artifact_refs: tuple[str, ...] = ()
    output_binding_names: tuple[str, ...] = ()
    evidence_policy: EvidencePolicy = field(default_factory=EvidencePolicy)
    required: bool = True
    source_span: tuple[int, int] | None = None

    _FIELDS = (
        "id",
        "desired_state",
        "acceptance_criteria",
        "depends_on",
        "forbidden_effects",
        "required_capabilities",
        "input_artifact_refs",
        "output_binding_names",
        "evidence_policy",
        "required",
        "source_span",
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "id",
            _contract_text(self.id, "obligation id", InvalidMissionDefinition),
        )
        object.__setattr__(
            self,
            "desired_state",
            _contract_text(
                self.desired_state,
                f"obligation {self.id!r} desired_state",
                InvalidMissionDefinition,
            ),
        )
        for name in (
            "acceptance_criteria", "depends_on", "forbidden_effects",
            "required_capabilities", "input_artifact_refs", "output_binding_names",
        ):
            object.__setattr__(
                self,
                name,
                _contract_string_tuple(
                    getattr(self, name),
                    f"obligation {self.id!r} {name}",
                    InvalidMissionDefinition,
                ),
            )
        if not self.acceptance_criteria:
            object.__setattr__(self, "acceptance_criteria", (self.desired_state,))
        if not self.required_capabilities:
            object.__setattr__(self, "required_capabilities", ("visual.interact",))
        policy = self.evidence_policy
        if type(policy) is dict:
            policy = EvidencePolicy.from_dict(policy)
        if type(policy) is not EvidencePolicy:
            raise InvalidMissionDefinition(f"obligation {self.id!r} has invalid evidence_policy")
        object.__setattr__(self, "evidence_policy", policy)
        object.__setattr__(
            self,
            "required",
            _contract_bool(
                self.required,
                f"obligation {self.id!r} required",
                InvalidMissionDefinition,
            ),
        )
        if self.source_span is not None:
            if type(self.source_span) not in (list, tuple) or len(self.source_span) != 2:
                raise InvalidMissionDefinition(f"obligation {self.id!r} has invalid source_span")
            span = tuple(
                _contract_int(
                    item,
                    f"obligation {self.id!r} source_span[]",
                    InvalidMissionDefinition,
                    minimum=0,
                )
                for item in self.source_span
            )
            if span[1] < span[0]:
                raise InvalidMissionDefinition(f"obligation {self.id!r} has invalid source_span")
            object.__setattr__(self, "source_span", span)
        _validate_contract_wire(
            self.to_dict(),
            "ObligationSpec",
            InvalidMissionDefinition,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "desired_state": self.desired_state,
            "acceptance_criteria": list(self.acceptance_criteria),
            "depends_on": list(self.depends_on),
            "forbidden_effects": list(self.forbidden_effects),
            "required_capabilities": list(self.required_capabilities),
            "input_artifact_refs": list(self.input_artifact_refs),
            "output_binding_names": list(self.output_binding_names),
            "evidence_policy": self.evidence_policy.to_dict(),
            "required": self.required,
            "source_span": list(self.source_span) if self.source_span else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ObligationSpec":
        value = _wire_object(
            raw,
            cls._FIELDS,
            "ObligationSpec",
            InvalidMissionDefinition,
        )
        acceptance_criteria = _wire_string_list(
            value["acceptance_criteria"],
            "acceptance_criteria",
            InvalidMissionDefinition,
        )
        required_capabilities = _wire_string_list(
            value["required_capabilities"],
            "required_capabilities",
            InvalidMissionDefinition,
        )
        if not acceptance_criteria:
            raise InvalidMissionDefinition(
                "acceptance_criteria must contain at least one string"
            )
        if not required_capabilities:
            raise InvalidMissionDefinition(
                "required_capabilities must contain at least one string"
            )
        raw_span = value["source_span"]
        if raw_span is not None and (
            type(raw_span) is not list or len(raw_span) != 2
        ):
            raise InvalidMissionDefinition(
                "source_span must be null or a two-integer JSON array"
            )
        return cls(
            id=_contract_text(value["id"], "id", InvalidMissionDefinition),
            desired_state=_contract_text(
                value["desired_state"],
                "desired_state",
                InvalidMissionDefinition,
            ),
            acceptance_criteria=acceptance_criteria,
            depends_on=_wire_string_list(
                value["depends_on"], "depends_on", InvalidMissionDefinition
            ),
            forbidden_effects=_wire_string_list(
                value["forbidden_effects"],
                "forbidden_effects",
                InvalidMissionDefinition,
            ),
            required_capabilities=required_capabilities,
            input_artifact_refs=_wire_string_list(
                value["input_artifact_refs"],
                "input_artifact_refs",
                InvalidMissionDefinition,
            ),
            output_binding_names=_wire_string_list(
                value["output_binding_names"],
                "output_binding_names",
                InvalidMissionDefinition,
            ),
            evidence_policy=EvidencePolicy.from_dict(value["evidence_policy"]),
            required=_contract_bool(
                value["required"], "required", InvalidMissionDefinition
            ),
            source_span=(
                None
                if raw_span is None
                else tuple(
                    _contract_int(
                        item,
                        "source_span[]",
                        InvalidMissionDefinition,
                        minimum=0,
                    )
                    for item in raw_span
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    id: str
    mission_id: str
    run_id: str
    kind: str
    trust: str
    obligation_id: str | None = None
    payload_hash: str = ""
    frame_id: int | None = None
    action_id: str | None = None
    artifact_id: str | None = None
    created_at: float | None = None
    content_sha256: str | None = None

    _FIELDS = (
        "id",
        "mission_id",
        "run_id",
        "kind",
        "trust",
        "obligation_id",
        "payload_hash",
        "frame_id",
        "action_id",
        "artifact_id",
        "created_at",
        "content_sha256",
    )

    def __post_init__(self) -> None:
        for name in ("id", "mission_id", "run_id", "kind", "trust"):
            object.__setattr__(
                self,
                name,
                _contract_text(
                    getattr(self, name),
                    f"evidence {name}",
                    EvidenceScopeError,
                ),
            )
        object.__setattr__(
            self,
            "payload_hash",
            _contract_text(
                self.payload_hash,
                "evidence payload_hash",
                EvidenceScopeError,
            ),
        )
        if self.obligation_id is not None:
            object.__setattr__(
                self,
                "obligation_id",
                _contract_text(
                    self.obligation_id,
                    "evidence obligation_id",
                    EvidenceScopeError,
                ),
            )
        if self.action_id is not None:
            object.__setattr__(
                self,
                "action_id",
                _contract_text(
                    self.action_id,
                    "evidence action_id",
                    EvidenceScopeError,
                ),
            )
        if self.artifact_id is not None:
            object.__setattr__(
                self,
                "artifact_id",
                _contract_text(
                    self.artifact_id,
                    "evidence artifact_id",
                    EvidenceScopeError,
                ),
            )
            if self.content_sha256 is None:
                raise EvidenceScopeError(
                    "artifact evidence requires content_sha256"
                )
        if self.content_sha256 is not None:
            content_sha = _contract_text(
                self.content_sha256,
                "evidence content_sha256",
                EvidenceScopeError,
            )
            if self.artifact_id is None:
                raise EvidenceScopeError("content_sha256 requires artifact_id")
            if (
                content_sha != content_sha.lower()
                or not _SHA256_RE.fullmatch(content_sha)
            ):
                raise EvidenceScopeError(
                    "content_sha256 must be a lowercase 64-character SHA-256 hex digest"
                )
            object.__setattr__(self, "content_sha256", content_sha)
        if self.frame_id is not None:
            object.__setattr__(
                self,
                "frame_id",
                _contract_int(
                    self.frame_id,
                    "evidence frame_id",
                    EvidenceScopeError,
                    minimum=0,
                ),
            )
        if self.created_at is not None:
            object.__setattr__(
                self,
                "created_at",
                _contract_float(
                    self.created_at,
                    "evidence created_at",
                    EvidenceScopeError,
                ),
            )
        _validate_contract_wire(
            self.to_dict(),
            "EvidenceRef",
            EvidenceScopeError,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mission_id": self.mission_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "trust": self.trust,
            "obligation_id": self.obligation_id,
            "payload_hash": self.payload_hash,
            "frame_id": self.frame_id,
            "action_id": self.action_id,
            "artifact_id": self.artifact_id,
            "created_at": self.created_at,
            "content_sha256": self.content_sha256,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EvidenceRef":
        value = _wire_object(
            raw,
            cls._FIELDS,
            "EvidenceRef",
            EvidenceScopeError,
        )
        return cls(
            id=_contract_text(value["id"], "id", EvidenceScopeError),
            mission_id=_contract_text(
                value["mission_id"], "mission_id", EvidenceScopeError
            ),
            run_id=_contract_text(value["run_id"], "run_id", EvidenceScopeError),
            kind=_contract_text(value["kind"], "kind", EvidenceScopeError),
            trust=_contract_text(value["trust"], "trust", EvidenceScopeError),
            obligation_id=_wire_optional_text(
                value["obligation_id"], "obligation_id", EvidenceScopeError
            ),
            payload_hash=_contract_text(
                value["payload_hash"], "payload_hash", EvidenceScopeError
            ),
            frame_id=(
                None
                if value["frame_id"] is None
                else _contract_int(
                    value["frame_id"],
                    "frame_id",
                    EvidenceScopeError,
                    minimum=0,
                )
            ),
            action_id=_wire_optional_text(
                value["action_id"], "action_id", EvidenceScopeError
            ),
            artifact_id=_wire_optional_text(
                value["artifact_id"], "artifact_id", EvidenceScopeError
            ),
            created_at=(
                None
                if value["created_at"] is None
                else _contract_float(
                    value["created_at"], "created_at", EvidenceScopeError
                )
            ),
            content_sha256=_wire_optional_text(
                value["content_sha256"], "content_sha256", EvidenceScopeError
            ),
        )


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    media_type: str
    sha256: str
    producer_obligation_id: str
    evidence_ref: str

    _FIELDS = (
        "artifact_id",
        "media_type",
        "sha256",
        "producer_obligation_id",
        "evidence_ref",
    )

    def __post_init__(self) -> None:
        for name in ("artifact_id", "media_type", "sha256", "producer_obligation_id", "evidence_ref"):
            object.__setattr__(
                self,
                name,
                _contract_text(
                    getattr(self, name),
                    f"artifact {name}",
                    InvalidMissionEvent,
                ),
            )
        if (
            self.sha256 != self.sha256.lower()
            or not _SHA256_RE.fullmatch(self.sha256)
        ):
            raise InvalidMissionEvent(
                "artifact sha256 must be a lowercase 64-character SHA-256 hex digest"
            )
        _validate_contract_wire(
            self.to_dict(),
            "ArtifactRef",
            InvalidMissionEvent,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "producer_obligation_id": self.producer_obligation_id,
            "evidence_ref": self.evidence_ref,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ArtifactRef":
        value = _wire_object(
            raw,
            cls._FIELDS,
            "ArtifactRef",
            InvalidMissionEvent,
        )
        return cls(
            artifact_id=_contract_text(
                value["artifact_id"], "artifact_id", InvalidMissionEvent
            ),
            media_type=_contract_text(
                value["media_type"], "media_type", InvalidMissionEvent
            ),
            sha256=_contract_text(value["sha256"], "sha256", InvalidMissionEvent),
            producer_obligation_id=_contract_text(
                value["producer_obligation_id"],
                "producer_obligation_id",
                InvalidMissionEvent,
            ),
            evidence_ref=_contract_text(
                value["evidence_ref"], "evidence_ref", InvalidMissionEvent
            ),
        )


@dataclass(frozen=True, slots=True)
class TargetBinding:
    binding_id: str
    hwnd: int
    process_id: int
    executable_identity: str
    title_snapshot: str
    established_by_evidence_ref: str
    epoch: int

    _FIELDS = (
        "binding_id",
        "hwnd",
        "process_id",
        "executable_identity",
        "title_snapshot",
        "established_by_evidence_ref",
        "epoch",
    )

    def __post_init__(self) -> None:
        for name in ("binding_id", "executable_identity", "established_by_evidence_ref"):
            object.__setattr__(
                self,
                name,
                _contract_text(
                    getattr(self, name),
                    f"target {name}",
                    InvalidMissionEvent,
                ),
            )
        object.__setattr__(
            self,
            "title_snapshot",
            _contract_text(
                self.title_snapshot,
                "target title_snapshot",
                InvalidMissionEvent,
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "hwnd",
            _contract_int(
                self.hwnd, "target hwnd", InvalidMissionEvent, minimum=1
            ),
        )
        object.__setattr__(
            self,
            "process_id",
            _contract_int(
                self.process_id,
                "target process_id",
                InvalidMissionEvent,
                minimum=1,
            ),
        )
        object.__setattr__(
            self,
            "epoch",
            _contract_int(
                self.epoch, "target epoch", InvalidMissionEvent, minimum=0
            ),
        )
        _validate_contract_wire(
            self.to_dict(),
            "TargetBinding",
            InvalidMissionEvent,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "hwnd": self.hwnd,
            "process_id": self.process_id,
            "executable_identity": self.executable_identity,
            "title_snapshot": self.title_snapshot,
            "established_by_evidence_ref": self.established_by_evidence_ref,
            "epoch": self.epoch,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TargetBinding":
        value = _wire_object(
            raw,
            cls._FIELDS,
            "TargetBinding",
            InvalidMissionEvent,
        )
        return cls(
            binding_id=_contract_text(
                value["binding_id"], "binding_id", InvalidMissionEvent
            ),
            hwnd=_contract_int(
                value["hwnd"], "hwnd", InvalidMissionEvent, minimum=1
            ),
            process_id=_contract_int(
                value["process_id"],
                "process_id",
                InvalidMissionEvent,
                minimum=1,
            ),
            executable_identity=_contract_text(
                value["executable_identity"],
                "executable_identity",
                InvalidMissionEvent,
            ),
            title_snapshot=_contract_text(
                value["title_snapshot"],
                "title_snapshot",
                InvalidMissionEvent,
                allow_empty=True,
            ),
            established_by_evidence_ref=_contract_text(
                value["established_by_evidence_ref"],
                "established_by_evidence_ref",
                InvalidMissionEvent,
            ),
            epoch=_contract_int(
                value["epoch"], "epoch", InvalidMissionEvent, minimum=0
            ),
        )


@dataclass(frozen=True, slots=True)
class VerifiedFact:
    id: str
    statement: str
    evidence_refs: tuple[str, ...]
    scope: str
    valid_for_obligation_ids: tuple[str, ...] = ()
    expires_on_epoch_change: bool = True

    _FIELDS = (
        "id",
        "statement",
        "evidence_refs",
        "scope",
        "valid_for_obligation_ids",
        "expires_on_epoch_change",
    )

    def __post_init__(self) -> None:
        for name in ("id", "statement", "scope"):
            object.__setattr__(
                self,
                name,
                _contract_text(
                    getattr(self, name),
                    f"verified fact {name}",
                    InvalidMissionEvent,
                ),
            )
        evidence_refs = _contract_string_tuple(
            self.evidence_refs,
            "verified fact evidence_refs",
            InvalidMissionEvent,
        )
        if not evidence_refs:
            raise EvidenceScopeError("verified fact requires evidence_refs")
        if len(evidence_refs) != len(set(evidence_refs)):
            raise EvidenceScopeError("verified fact evidence_refs must be unique")
        valid_for = _contract_string_tuple(
            self.valid_for_obligation_ids,
            "verified fact valid_for_obligation_ids",
            InvalidMissionEvent,
        )
        if len(valid_for) != len(set(valid_for)):
            raise InvalidMissionEvent("verified fact obligation ids must be unique")
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(self, "valid_for_obligation_ids", valid_for)
        object.__setattr__(
            self,
            "expires_on_epoch_change",
            _contract_bool(
                self.expires_on_epoch_change,
                "verified fact expires_on_epoch_change",
                InvalidMissionEvent,
            ),
        )
        _validate_contract_wire(
            self.to_dict(),
            "VerifiedFact",
            InvalidMissionEvent,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "evidence_refs": list(self.evidence_refs),
            "scope": self.scope,
            "valid_for_obligation_ids": list(self.valid_for_obligation_ids),
            "expires_on_epoch_change": self.expires_on_epoch_change,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "VerifiedFact":
        value = _wire_object(
            raw,
            cls._FIELDS,
            "VerifiedFact",
            InvalidMissionEvent,
        )
        return cls(
            id=_contract_text(value["id"], "id", InvalidMissionEvent),
            statement=_contract_text(
                value["statement"], "statement", InvalidMissionEvent
            ),
            evidence_refs=_wire_string_list(
                value["evidence_refs"], "evidence_refs", InvalidMissionEvent
            ),
            scope=_contract_text(
                value["scope"], "scope", InvalidMissionEvent
            ),
            valid_for_obligation_ids=_wire_string_list(
                value["valid_for_obligation_ids"],
                "valid_for_obligation_ids",
                InvalidMissionEvent,
            ),
            expires_on_epoch_change=_contract_bool(
                value["expires_on_epoch_change"],
                "expires_on_epoch_change",
                InvalidMissionEvent,
            ),
        )


@dataclass(frozen=True, slots=True)
class ActionEffectSummary:
    """Deterministic, non-semantic comparison of two authoritative frames."""

    action_id: str
    input_completed: bool
    before_frame_id: int
    after_frame_id: int
    before_frame_evidence_ref: str
    after_frame_evidence_ref: str
    before_frame_payload_hash: str
    after_frame_payload_hash: str
    target_binding_valid: bool
    frame_comparable: bool
    local_visual_change: LocalVisualChange | str
    change_ratio: float | None
    reason: ActionEffectReason | str
    evidence_ref: str
    semantic_completion: str = "unknown"
    comparator_version: str = ACTION_EFFECT_COMPARATOR_VERSION
    format: str = ACTION_EFFECT_FORMAT

    _FIELDS = (
        "format",
        "comparator_version",
        "action_id",
        "input_completed",
        "before_frame_id",
        "after_frame_id",
        "before_frame_evidence_ref",
        "after_frame_evidence_ref",
        "before_frame_payload_hash",
        "after_frame_payload_hash",
        "target_binding_valid",
        "frame_comparable",
        "local_visual_change",
        "change_ratio",
        "reason",
        "semantic_completion",
        "evidence_ref",
        "payload_hash",
    )

    def __post_init__(self) -> None:
        _contract_text(self.format, "action effect format", InvalidMissionEvent)
        if self.format != ACTION_EFFECT_FORMAT:
            raise InvalidMissionEvent("unsupported action effect format")
        _contract_text(
            self.comparator_version,
            "action effect comparator_version",
            InvalidMissionEvent,
        )
        if self.comparator_version != ACTION_EFFECT_COMPARATOR_VERSION:
            raise InvalidMissionEvent("unsupported action effect comparator")
        for name in (
            "action_id",
            "before_frame_evidence_ref",
            "after_frame_evidence_ref",
            "evidence_ref",
        ):
            _contract_text(
                getattr(self, name),
                f"action effect {name}",
                InvalidMissionEvent,
            )
        for name in ("before_frame_payload_hash", "after_frame_payload_hash"):
            value = getattr(self, name)
            _contract_text(value, f"action effect {name}", InvalidMissionEvent)
            if not _SHA256_RE.fullmatch(value) or value != value.lower():
                raise InvalidMissionEvent(
                    f"action effect {name} must be a canonical SHA-256 digest"
                )
        _contract_bool(
            self.input_completed,
            "action effect input_completed",
            InvalidMissionEvent,
        )
        for name in ("target_binding_valid", "frame_comparable"):
            _contract_bool(
                getattr(self, name),
                f"action effect {name}",
                InvalidMissionEvent,
            )
        for name in ("before_frame_id", "after_frame_id"):
            _contract_int(
                getattr(self, name),
                f"action effect {name}",
                InvalidMissionEvent,
                minimum=1,
            )
        if self.after_frame_id <= self.before_frame_id:
            raise InvalidMissionEvent("action effect frames must be in commit order")
        if type(self.local_visual_change) is not LocalVisualChange:
            raise InvalidMissionEvent(
                "action effect local_visual_change must be a LocalVisualChange"
            )
        if type(self.reason) is not ActionEffectReason:
            raise InvalidMissionEvent(
                "action effect reason must be an ActionEffectReason"
            )
        change = self.local_visual_change
        reason = self.reason
        _contract_text(
            self.semantic_completion,
            "action effect semantic_completion",
            InvalidMissionEvent,
        )
        if self.semantic_completion != "unknown":
            raise InvalidMissionEvent("pixel comparison cannot claim semantic completion")
        ratio = self.change_ratio
        if ratio is not None:
            _contract_float(
                ratio,
                "action effect change_ratio",
                InvalidMissionEvent,
            )
            if not 0.0 <= ratio <= 1.0:
                raise InvalidMissionEvent("action effect change_ratio must be null or in [0, 1]")

        if not self.input_completed:
            coherent = bool(
                not self.frame_comparable
                and change is LocalVisualChange.UNKNOWN
                and ratio is None
                and reason is ActionEffectReason.INPUT_NOT_EXECUTED
            )
        elif not self.target_binding_valid:
            coherent = bool(
                not self.frame_comparable
                and change is LocalVisualChange.UNKNOWN
                and ratio is None
                and reason is ActionEffectReason.TARGET_BINDING_CHANGED
            )
        elif self.frame_comparable:
            expected_change = (
                LocalVisualChange.CHANGED
                if ratio is not None
                and ratio > ACTION_EFFECT_CHANGE_RATIO_THRESHOLD
                else LocalVisualChange.UNCHANGED
            )
            coherent = bool(
                ratio is not None
                and change is expected_change
                and reason in {
                    ActionEffectReason.EXACT_PAYLOAD,
                    ActionEffectReason.ALIGNED_PIXEL_DELTA,
                }
                and (
                    reason is not ActionEffectReason.EXACT_PAYLOAD
                    or (ratio == 0.0 and change is LocalVisualChange.UNCHANGED)
                )
            )
        else:
            coherent = bool(
                change is LocalVisualChange.UNKNOWN
                and ratio is None
                and reason in {
                    ActionEffectReason.FRAME_NOT_COMPARABLE,
                    ActionEffectReason.DECODE_FAILED,
                    ActionEffectReason.DECODED_SIZE_MISMATCH,
                    ActionEffectReason.COMPARISON_FAILED,
                }
            )
        if not coherent:
            raise InvalidMissionEvent("action effect fields are contradictory")

    def payload_dict(self) -> dict[str, Any]:
        """Canonical evidence material, excluding its self-referential ID."""

        return {
            "format": self.format,
            "comparator_version": self.comparator_version,
            "action_id": self.action_id,
            "input_completed": self.input_completed,
            "before_frame_id": self.before_frame_id,
            "after_frame_id": self.after_frame_id,
            "before_frame_evidence_ref": self.before_frame_evidence_ref,
            "after_frame_evidence_ref": self.after_frame_evidence_ref,
            "before_frame_payload_hash": self.before_frame_payload_hash,
            "after_frame_payload_hash": self.after_frame_payload_hash,
            "target_binding_valid": self.target_binding_valid,
            "frame_comparable": self.frame_comparable,
            "local_visual_change": self.local_visual_change.value,
            "change_ratio": self.change_ratio,
            "reason": self.reason.value,
            "semantic_completion": self.semantic_completion,
        }

    @property
    def payload_hash(self) -> str:
        encoded = json.dumps(
            self.payload_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.payload_dict(),
            "evidence_ref": self.evidence_ref,
            "payload_hash": self.payload_hash,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ActionEffectSummary":
        value = _wire_object(
            raw,
            cls._FIELDS,
            "ActionEffectSummary",
            InvalidMissionEvent,
        )
        local_visual_change_raw = _contract_text(
            value["local_visual_change"],
            "local_visual_change",
            InvalidMissionEvent,
        )
        reason_raw = _contract_text(
            value["reason"],
            "reason",
            InvalidMissionEvent,
        )
        try:
            local_visual_change = LocalVisualChange(local_visual_change_raw)
            reason = ActionEffectReason(reason_raw)
        except ValueError as exc:
            raise InvalidMissionEvent(
                "invalid action effect classification"
            ) from exc
        change_ratio = value["change_ratio"]
        if change_ratio is not None:
            change_ratio = _contract_float(
                change_ratio,
                "change_ratio",
                InvalidMissionEvent,
            )
        result = cls(
            format=_contract_text(
                value["format"], "format", InvalidMissionEvent
            ),
            comparator_version=_contract_text(
                value["comparator_version"],
                "comparator_version",
                InvalidMissionEvent,
            ),
            action_id=_contract_text(
                value["action_id"], "action_id", InvalidMissionEvent
            ),
            input_completed=_contract_bool(
                value["input_completed"],
                "input_completed",
                InvalidMissionEvent,
            ),
            before_frame_id=_contract_int(
                value["before_frame_id"],
                "before_frame_id",
                InvalidMissionEvent,
                minimum=1,
            ),
            after_frame_id=_contract_int(
                value["after_frame_id"],
                "after_frame_id",
                InvalidMissionEvent,
                minimum=1,
            ),
            before_frame_evidence_ref=_contract_text(
                value["before_frame_evidence_ref"],
                "before_frame_evidence_ref",
                InvalidMissionEvent,
            ),
            after_frame_evidence_ref=_contract_text(
                value["after_frame_evidence_ref"],
                "after_frame_evidence_ref",
                InvalidMissionEvent,
            ),
            before_frame_payload_hash=_contract_text(
                value["before_frame_payload_hash"],
                "before_frame_payload_hash",
                InvalidMissionEvent,
            ),
            after_frame_payload_hash=_contract_text(
                value["after_frame_payload_hash"],
                "after_frame_payload_hash",
                InvalidMissionEvent,
            ),
            target_binding_valid=_contract_bool(
                value["target_binding_valid"],
                "target_binding_valid",
                InvalidMissionEvent,
            ),
            frame_comparable=_contract_bool(
                value["frame_comparable"],
                "frame_comparable",
                InvalidMissionEvent,
            ),
            local_visual_change=local_visual_change,
            change_ratio=change_ratio,
            reason=reason,
            semantic_completion=_contract_text(
                value["semantic_completion"],
                "semantic_completion",
                InvalidMissionEvent,
            ),
            evidence_ref=_contract_text(
                value["evidence_ref"], "evidence_ref", InvalidMissionEvent
            ),
        )
        payload_hash = _contract_text(
            value["payload_hash"], "payload_hash", InvalidMissionEvent
        )
        if (
            not _SHA256_RE.fullmatch(payload_hash)
            or payload_hash != payload_hash.lower()
            or payload_hash != result.payload_hash
        ):
            raise InvalidMissionEvent("action effect payload_hash does not match its content")
        return result


@dataclass(frozen=True, slots=True)
class TransitionSummary:
    action: str
    input_completed: bool
    visible_effect: str = ""
    evidence_refs: tuple[str, ...] = ()
    frame_id: int | None = None
    obligation_id: str = ""
    action_id: str | None = None
    action_effect: ActionEffectSummary | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "action",
            _contract_text(self.action, "transition action", InvalidMissionEvent),
        )
        if type(self.input_completed) is not bool:
            raise InvalidMissionEvent("transition input_completed must be boolean")
        object.__setattr__(
            self,
            "visible_effect",
            _contract_text(
                self.visible_effect,
                "transition visible_effect",
                InvalidMissionEvent,
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "obligation_id",
            _contract_text(
                self.obligation_id,
                "transition obligation_id",
                InvalidMissionEvent,
            ),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _contract_string_tuple(
                self.evidence_refs,
                "transition evidence_refs",
                InvalidMissionEvent,
            ),
        )
        if self.frame_id is not None:
            object.__setattr__(
                self,
                "frame_id",
                _contract_int(
                    self.frame_id,
                    "transition frame_id",
                    InvalidMissionEvent,
                    minimum=0,
                ),
            )
        if self.action_id is not None:
            object.__setattr__(
                self,
                "action_id",
                _contract_text(
                    self.action_id,
                    "transition action_id",
                    InvalidMissionEvent,
                ),
            )
        if self.action_effect is not None:
            effect = (
                self.action_effect
                if isinstance(self.action_effect, ActionEffectSummary)
                else ActionEffectSummary.from_dict(self.action_effect)
            )
            if (
                self.action_id != effect.action_id
                or self.input_completed is not effect.input_completed
                or self.frame_id != effect.after_frame_id
                or effect.evidence_ref not in self.evidence_refs
                or effect.before_frame_evidence_ref not in self.evidence_refs
                or effect.after_frame_evidence_ref not in self.evidence_refs
            ):
                raise InvalidMissionEvent(
                    "transition does not bind its action effect identity"
                )
            object.__setattr__(self, "action_effect", effect)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "action": self.action,
            "input_completed": self.input_completed,
            "visible_effect": self.visible_effect,
            "evidence_refs": list(self.evidence_refs),
            "frame_id": self.frame_id,
            "obligation_id": self.obligation_id,
        }
        # Omitting absent additive fields preserves historical event replay
        # and claim fingerprints for pre-action-effect transitions.
        if self.action_id is not None:
            result["action_id"] = self.action_id
        if self.action_effect is not None:
            result["action_effect"] = self.action_effect.to_dict()
        return result


@dataclass(frozen=True, slots=True)
class FailureSummary:
    kind: str
    message: str = ""
    evidence_refs: tuple[str, ...] = ()
    count: int = 1
    obligation_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "kind",
            _contract_text(self.kind, "failure kind", InvalidMissionEvent),
        )
        object.__setattr__(
            self,
            "message",
            _contract_text(
                self.message,
                "failure message",
                InvalidMissionEvent,
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "obligation_id",
            _contract_text(
                self.obligation_id,
                "failure obligation_id",
                InvalidMissionEvent,
            ),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _contract_string_tuple(
                self.evidence_refs,
                "failure evidence_refs",
                InvalidMissionEvent,
            ),
        )
        object.__setattr__(
            self,
            "count",
            _contract_int(
                self.count,
                "failure count",
                InvalidMissionEvent,
                minimum=1,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "evidence_refs": list(self.evidence_refs),
            "count": self.count,
            "obligation_id": self.obligation_id,
        }


@dataclass(frozen=True, slots=True)
class MissionDefinition:
    """Immutable declaration from which every projection starts."""

    mission_id: str
    run_id: str
    original_goal: str
    obligations: tuple[ObligationSpec, ...] = ()
    goal_revision: int = 0
    source: str = "vnext"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    format: str = MISSION_DEFINITION_FORMAT
    stable_topological_order: tuple[str, ...] = ()
    # This is a lossless context policy copied from the source contract.  It
    # does not select a runtime or change any legacy behavior in the P1
    # shadow; it merely keeps the input available for a later projection.
    experience_policy: str = "allow"

    _FIELDS = (
        "format",
        "mission_id",
        "run_id",
        "original_goal",
        "goal_revision",
        "source",
        "metadata",
        "obligations",
        "stable_topological_order",
        "experience_policy",
    )

    def __post_init__(self) -> None:
        _contract_text(
            self.format,
            "MissionDefinition format",
            InvalidMissionDefinition,
        )
        if self.format != MISSION_DEFINITION_FORMAT:
            raise InvalidMissionDefinition(f"unsupported MissionDefinition format: {self.format!r}")
        object.__setattr__(
            self,
            "mission_id",
            _contract_text(
                self.mission_id, "mission_id", InvalidMissionDefinition
            ),
        )
        object.__setattr__(
            self,
            "run_id",
            _contract_text(self.run_id, "run_id", InvalidMissionDefinition),
        )
        object.__setattr__(
            self,
            "original_goal",
            _contract_text(
                self.original_goal, "original_goal", InvalidMissionDefinition
            ),
        )
        object.__setattr__(
            self,
            "source",
            _contract_text(self.source, "source", InvalidMissionDefinition),
        )
        if type(self.obligations) not in (list, tuple):
            raise InvalidMissionDefinition(
                "obligations must be an internal tuple/list of ObligationSpec values"
            )
        obligations = tuple(
            item if type(item) is ObligationSpec else ObligationSpec.from_dict(item)
            for item in self.obligations
        )
        if not obligations:
            obligations = (
                ObligationSpec(
                    id="obligation-1",
                    desired_state=self.original_goal,
                    acceptance_criteria=(self.original_goal,),
                    required_capabilities=("visual.interact",),
                ),
            )
        _validate_contract_wire(
            {"obligations": [item.to_dict() for item in obligations]},
            "MissionDefinition obligations",
            InvalidMissionDefinition,
        )
        ids = [item.id for item in obligations]
        if len(ids) != len(set(ids)):
            raise InvalidMissionDefinition("obligation ids must be unique")
        if not any(item.required for item in obligations):
            raise InvalidMissionDefinition("at least one obligation must be required")
        known = set(ids)
        for item in obligations:
            unknown = set(item.depends_on) - known
            if unknown:
                raise InvalidMissionDefinition(
                    f"obligation {item.id!r} depends on unknown ids: {sorted(unknown)}"
                )
        topological_order = _stable_topological_order(obligations)
        object.__setattr__(self, "obligations", obligations)
        try:
            object.__setattr__(self, "metadata", _json_mapping(self.metadata))
        except BoundedJSONError as exc:
            raise InvalidMissionDefinition(
                f"metadata violates the bounded JSON contract: {exc}"
            ) from exc
        object.__setattr__(
            self,
            "goal_revision",
            _contract_int(
                self.goal_revision,
                "goal_revision",
                InvalidMissionDefinition,
                minimum=0,
            ),
        )
        requested_order = _contract_string_tuple(
            self.stable_topological_order,
            "stable_topological_order",
            InvalidMissionDefinition,
        )
        if requested_order and requested_order != topological_order:
            raise InvalidMissionDefinition(
                "stable_topological_order does not match the obligation DAG"
            )
        object.__setattr__(self, "stable_topological_order", topological_order)
        policy = _contract_text(
            self.experience_policy,
            "experience_policy",
            InvalidMissionDefinition,
        )
        object.__setattr__(self, "experience_policy", policy)
        _validate_contract_wire(
            self.to_dict(),
            "MissionDefinition",
            InvalidMissionDefinition,
        )

    @classmethod
    def from_goal(
        cls,
        original_goal: str,
        *,
        mission_id: str,
        run_id: str,
        required_capabilities: Iterable[str] = ("visual.interact",),
        experience_policy: str = "allow",
    ) -> "MissionDefinition":
        capabilities = _contract_string_tuple(
            required_capabilities,
            "required_capabilities",
            InvalidMissionDefinition,
        )
        return cls(
            mission_id=mission_id,
            run_id=run_id,
            original_goal=original_goal,
            obligations=(
                ObligationSpec(
                    id="obligation-1",
                    desired_state=original_goal,
                    acceptance_criteria=(original_goal,),
                    required_capabilities=capabilities,
                ),
            ),
            experience_policy=experience_policy,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "mission_id": self.mission_id,
            "run_id": self.run_id,
            "original_goal": self.original_goal,
            "goal_revision": self.goal_revision,
            "source": self.source,
            "metadata": thaw(self.metadata),
            "obligations": [item.to_dict() for item in self.obligations],
            "stable_topological_order": list(self.stable_topological_order),
            "experience_policy": self.experience_policy,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "MissionDefinition":
        value = _wire_object(
            raw,
            cls._FIELDS,
            "MissionDefinition",
            InvalidMissionDefinition,
        )
        if type(value["obligations"]) is not list:
            raise InvalidMissionDefinition(
                "obligations must be a JSON array"
            )
        if not value["obligations"]:
            raise InvalidMissionDefinition(
                "obligations must contain at least one canonical obligation"
            )
        if type(value["metadata"]) is not dict:
            raise InvalidMissionDefinition("metadata must be a plain JSON object")
        return cls(
            mission_id=_contract_text(
                value["mission_id"], "mission_id", InvalidMissionDefinition
            ),
            run_id=_contract_text(
                value["run_id"], "run_id", InvalidMissionDefinition
            ),
            original_goal=_contract_text(
                value["original_goal"],
                "original_goal",
                InvalidMissionDefinition,
            ),
            obligations=tuple(
                ObligationSpec.from_dict(item)
                for item in value["obligations"]
            ),
            goal_revision=_contract_int(
                value["goal_revision"],
                "goal_revision",
                InvalidMissionDefinition,
                minimum=0,
            ),
            source=_contract_text(
                value["source"], "source", InvalidMissionDefinition
            ),
            metadata=value["metadata"],
            format=_contract_text(
                value["format"], "format", InvalidMissionDefinition
            ),
            stable_topological_order=_wire_string_list(
                value["stable_topological_order"],
                "stable_topological_order",
                InvalidMissionDefinition,
            ),
            experience_policy=_contract_text(
                value["experience_policy"],
                "experience_policy",
                InvalidMissionDefinition,
            ),
        )


UNIMPLEMENTED_MISSION_EVENT_KINDS = frozenset(
    {
        "approval_requested",
        "approval_decided",
        "external_change_armed",
        "external_change_recorded",
        "planner_requested",
        "planner_patch_proposed",
        "planner_patch_accepted",
        "planner_patch_rejected",
        "inspection_requested",
        "inspection_recorded",
        "capability_requested",
        "capability_granted",
        "capability_denied",
        "execution_profile_accepted",
        "execution_profile_revised",
        "user_goal_revised",
    }
)
_EVENT_PAYLOAD_FIELDS = {
    "mission_compiled": (frozenset(), frozenset()),
    "obligation_activated": (
        frozenset({"obligation_id"}),
        frozenset(),
    ),
    "artifact_bound": (
        frozenset({"artifact"}),
        frozenset({"binding_name"}),
    ),
    "target_bound": (
        frozenset({"target_binding"}),
        frozenset(),
    ),
    "target_invalidated": (
        frozenset(),
        frozenset({"reason"}),
    ),
    "proposal_suppressed": (
        frozenset(
            {
                "obligation_id",
                "action",
                "action_summary",
                "reason",
            }
        ),
        frozenset(),
    ),
    "transition_recorded": (
        frozenset({"obligation_id", "input_completed"}),
        frozenset(
            {
                "action",
                "visible_effect",
                "frame_id",
                "action_id",
                "action_effect",
                "action_summary",
            }
        ),
    ),
    "obligation_claimed": (
        frozenset({"obligation_id"}),
        frozenset(),
    ),
    "verification_recorded": (
        frozenset({"obligation_id", "verified", "claim_fingerprint"}),
        frozenset(
            {
                "verification_id",
                "satisfied_criteria",
                "checked_forbidden_effects",
                "missing_conditions",
                "contradictions",
                "facts",
                "verifier_kind",
                "reason",
                "failure_kind",
            }
        ),
    ),
    "fact_recorded": (
        frozenset({"fact"}),
        frozenset({"obligation_id"}),
    ),
    "obligation_blocked": (
        frozenset({"obligation_id"}),
        frozenset({"reason", "blocker_kind"}),
    ),
    "obligation_unblocked": (
        frozenset({"obligation_id"}),
        frozenset(),
    ),
    "mission_blocked": (
        frozenset(),
        frozenset({"reason"}),
    ),
    "mission_failed": (
        frozenset(),
        frozenset({"reason"}),
    ),
    "mission_canceled": (
        frozenset(),
        frozenset({"reason"}),
    ),
    "mission_verified": (
        frozenset(
            {
                "verified",
                "verification_id",
                "accepted_obligation_verification_ids",
                "aggregate_evidence_id",
                "aggregate_payload_hash",
            }
        ),
        frozenset(),
    ),
}
_EVENT_PAYLOAD_FIELDS.update(
    {
        event_type: (frozenset(), frozenset())
        for event_type in UNIMPLEMENTED_MISSION_EVENT_KINDS
    }
)
_EVENT_TEXT_FIELDS = frozenset(
    {
        "obligation_id",
        "binding_name",
        "reason",
        "action",
        "action_summary",
        "action_id",
        "visible_effect",
        "claim_fingerprint",
        "verification_id",
        "verifier_kind",
        "failure_kind",
        "blocker_kind",
        "aggregate_evidence_id",
        "aggregate_payload_hash",
    }
)
_EVENT_STRING_ARRAY_FIELDS = frozenset(
    {
        "satisfied_criteria",
        "checked_forbidden_effects",
        "missing_conditions",
        "contradictions",
        "accepted_obligation_verification_ids",
    }
)


def is_safe_action_summary(action: str, summary: str) -> bool:
    """Validate the canonical privacy-safe summary owned by Mission events."""

    if type(action) is not str or type(summary) is not str:
        return False
    fixed = _SAFE_ACTION_SUMMARY_BY_ACTION.get(action)
    if fixed is not None:
        return summary in fixed
    if action != "hotkey" or not summary.startswith("hotkey_"):
        return False
    keys = tuple(summary.removeprefix("hotkey_").split("_"))
    if not keys or any(not key for key in keys):
        return False
    primary = keys[-1]
    modifiers = keys[:-1]
    return (
        primary in _HOTKEY_SUMMARY_PRIMARY_KEYS
        and len(modifiers) == len(set(modifiers))
        and all(key in _HOTKEY_SUMMARY_MODIFIERS for key in modifiers)
        and tuple(
            sorted(
                modifiers,
                key=_HOTKEY_SUMMARY_MODIFIERS.index,
            )
        )
        == modifiers
    )


def _validate_event_payload(
    event_type: str,
    payload: Mapping[str, Any],
) -> None:
    """Validate the canonical, kind-specific MissionEvent payload."""
    schema = _EVENT_PAYLOAD_FIELDS.get(event_type)
    if schema is None:
        raise InvalidMissionEvent(
            f"unsupported MissionEvent kind: {event_type!r}"
        )
    required, optional = schema
    actual = set(payload)
    missing = required - actual
    unknown = actual - required - optional
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing={sorted(missing)!r}")
        if unknown:
            details.append(f"unknown={sorted(unknown)!r}")
        raise InvalidMissionEvent(
            f"{event_type} payload has non-canonical fields "
            f"({', '.join(details)})"
        )

    for field_name in actual & _EVENT_TEXT_FIELDS:
        _contract_text(
            payload[field_name],
            f"{event_type} payload {field_name!r}",
            InvalidMissionEvent,
            allow_empty=field_name in {"reason", "visible_effect"},
        )
    for field_name in actual & _EVENT_STRING_ARRAY_FIELDS:
        if type(payload[field_name]) not in (list, tuple):
            raise InvalidMissionEvent(
                f"{event_type} payload {field_name!r} "
                "must be a JSON array of strings"
            )
        _contract_string_tuple(
            payload[field_name],
            f"{event_type} payload {field_name!r}",
            InvalidMissionEvent,
        )

    if "action_summary" in payload:
        action = payload.get("action")
        summary = payload["action_summary"]
        if (
            type(action) is not str
            or type(summary) is not str
            or not is_safe_action_summary(action, summary)
        ):
            raise InvalidMissionEvent(
                "transition_recorded payload 'action_summary' must be the "
                "privacy-safe semantic summary of its action"
            )
    if (
        event_type == "proposal_suppressed"
        and payload["reason"] != "repeated_completed_action"
    ):
        raise InvalidMissionEvent(
            "proposal_suppressed payload 'reason' must be the canonical "
            "repeated_completed_action code"
        )

    if "verified" in payload:
        _contract_bool(
            payload["verified"],
            f"{event_type} payload 'verified'",
            InvalidMissionEvent,
        )
    if event_type == "verification_recorded" and type(payload["verified"]) is bool:
        common = frozenset({"obligation_id", "verified", "claim_fingerprint"})
        if payload["verified"]:
            variant_required = common | frozenset(
                {"verification_id", "satisfied_criteria"}
            )
            variant_allowed = variant_required | frozenset(
                {
                    "checked_forbidden_effects",
                    "missing_conditions",
                    "contradictions",
                    "facts",
                    "verifier_kind",
                }
            )
        else:
            variant_required = common
            variant_allowed = variant_required | frozenset(
                {
                    "missing_conditions",
                    "contradictions",
                    "verifier_kind",
                    "reason",
                    "failure_kind",
                }
            )
        variant_missing = variant_required - actual
        variant_unknown = actual - variant_allowed
        if variant_missing or variant_unknown:
            details = []
            if variant_missing:
                details.append(f"missing={sorted(variant_missing)!r}")
            if variant_unknown:
                details.append(f"unknown={sorted(variant_unknown)!r}")
            raise InvalidMissionEvent(
                "verification_recorded payload has non-canonical verdict "
                f"fields ({', '.join(details)})"
            )
    if event_type == "mission_verified" and payload["verified"] is not True:
        raise InvalidMissionEvent(
            "mission_verified payload 'verified' must be the literal boolean true"
        )
    if "input_completed" in payload:
        _contract_bool(
            payload["input_completed"],
            f"{event_type} payload 'input_completed'",
            InvalidMissionEvent,
        )
    if "frame_id" in payload and payload["frame_id"] is not None:
        _contract_int(
            payload["frame_id"],
            f"{event_type} payload 'frame_id'",
            InvalidMissionEvent,
            minimum=0,
        )
    if "action_effect" in payload:
        raw_effect = payload["action_effect"]
        if not isinstance(raw_effect, Mapping):
            raise InvalidMissionEvent(
                f"{event_type} payload 'action_effect' must be a JSON object"
            )
        ActionEffectSummary.from_dict(thaw(raw_effect))

    if "artifact" in payload:
        raw_artifact = payload["artifact"]
        if not isinstance(raw_artifact, Mapping):
            raise InvalidMissionEvent(
                f"{event_type} payload 'artifact' must be a JSON object"
            )
        ArtifactRef.from_dict(thaw(raw_artifact))
    if "target_binding" in payload:
        raw_target = payload["target_binding"]
        if not isinstance(raw_target, Mapping):
            raise InvalidMissionEvent(
                f"{event_type} payload 'target_binding' must be a JSON object"
            )
        TargetBinding.from_dict(thaw(raw_target))
    if "fact" in payload:
        raw_fact = payload["fact"]
        if not isinstance(raw_fact, Mapping):
            raise InvalidMissionEvent(
                f"{event_type} payload 'fact' must be a JSON object"
            )
        VerifiedFact.from_dict(thaw(raw_fact))
    if "facts" in payload:
        raw_facts = payload["facts"]
        if type(raw_facts) not in (list, tuple):
            raise InvalidMissionEvent(
                f"{event_type} payload 'facts' must be a JSON array"
            )
        for raw_fact in raw_facts:
            if not isinstance(raw_fact, Mapping):
                raise InvalidMissionEvent(
                    f"{event_type} payload 'facts' items must be JSON objects"
                )
            VerifiedFact.from_dict(thaw(raw_fact))


@dataclass(frozen=True, slots=True, init=False)
class MissionEvent:
    """One immutable input to the reducer.

    ``base_revision`` is a compare-and-swap token.  It prevents a late result
    from a previous frame/role from mutating a newer mission projection.
    """

    kind: str
    mission_id: str
    run_id: str
    base_revision: int
    payload: Mapping[str, Any] = field(default_factory=dict)
    event_id: str = ""
    format: str = MISSION_EVENT_FORMAT
    evidence_refs: tuple[str, ...] = ()
    created_at: float | None = None

    _FIELDS = (
        "format",
        "event_id",
        "mission_id",
        "run_id",
        "base_revision",
        "kind",
        "payload",
        "evidence_refs",
        "created_at",
    )

    def __init__(
        self,
        kind: str = "",
        mission_id: str = "",
        run_id: str = "",
        base_revision: int = 0,
        payload: Mapping[str, Any] | object = _MISSING_PAYLOAD,
        event_id: str = "",
        format: str = MISSION_EVENT_FORMAT,
        evidence_refs: Iterable[str] | None = None,
        created_at: float | None = None,
    ) -> None:
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "mission_id", mission_id)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "base_revision", base_revision)
        object.__setattr__(
            self,
            "payload",
            {} if payload is _MISSING_PAYLOAD else payload,
        )
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "format", format)
        object.__setattr__(
            self,
            "evidence_refs",
            () if evidence_refs is None else evidence_refs,
        )
        object.__setattr__(self, "created_at", created_at)
        self.__post_init__()

    def __post_init__(self) -> None:
        _contract_text(self.format, "MissionEvent format", InvalidMissionEvent)
        if self.format != MISSION_EVENT_FORMAT:
            raise InvalidMissionEvent(f"unsupported MissionEvent format: {self.format!r}")
        object.__setattr__(
            self,
            "kind",
            _contract_text(self.kind, "event kind", InvalidMissionEvent),
        )
        object.__setattr__(
            self,
            "mission_id",
            _contract_text(
                self.mission_id, "event mission_id", InvalidMissionEvent
            ),
        )
        object.__setattr__(
            self,
            "run_id",
            _contract_text(self.run_id, "event run_id", InvalidMissionEvent),
        )
        object.__setattr__(
            self,
            "base_revision",
            _contract_int(
                self.base_revision,
                "event base_revision",
                InvalidMissionEvent,
                minimum=0,
            ),
        )
        object.__setattr__(
            self,
            "event_id",
            _contract_text(
                self.event_id,
                "event event_id",
                InvalidMissionEvent,
                allow_empty=True,
            ),
        )
        if self.created_at is not None:
            object.__setattr__(
                self,
                "created_at",
                _contract_float(
                    self.created_at, "event created_at", InvalidMissionEvent
                ),
            )
        try:
            object.__setattr__(self, "payload", _json_mapping(self.payload))
        except BoundedJSONError as exc:
            raise InvalidMissionEvent(
                f"payload violates the bounded JSON contract: {exc}"
            ) from exc
        _validate_event_payload(self.kind, self.payload)
        object.__setattr__(
            self,
            "evidence_refs",
            _contract_string_tuple(
                self.evidence_refs,
                "event evidence_refs",
                InvalidMissionEvent,
            ),
        )
        if not self.event_id:
            canonical = {
                "kind": self.kind,
                "mission_id": self.mission_id,
                "run_id": self.run_id,
                "base_revision": self.base_revision,
                "payload": thaw(self.payload),
                "evidence_refs": list(self.evidence_refs),
            }
            digest = hashlib.sha256(
                json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:24]
            object.__setattr__(self, "event_id", f"event-{digest}")
        _validate_contract_wire(
            self.to_dict(),
            "MissionEvent",
            InvalidMissionEvent,
        )

    @classmethod
    def create(
        cls,
        event_type: str,
        definition: MissionDefinition,
        *,
        base_revision: int,
        payload: Mapping[str, Any] | None = None,
        event_id: str = "",
        evidence_refs: Iterable[str] | None = None,
        created_at: float | None = None,
    ) -> "MissionEvent":
        return cls(
            kind=event_type,
            mission_id=definition.mission_id,
            run_id=definition.run_id,
            base_revision=base_revision,
            payload={} if payload is None else payload,
            event_id=event_id,
            evidence_refs=evidence_refs,
            created_at=created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "event_id": self.event_id,
            "mission_id": self.mission_id,
            "run_id": self.run_id,
            "base_revision": self.base_revision,
            "kind": self.kind,
            "payload": thaw(self.payload),
            "evidence_refs": list(self.evidence_refs),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "MissionEvent":
        value = _wire_object(
            raw,
            cls._FIELDS,
            "MissionEvent",
            InvalidMissionEvent,
        )
        if type(value["payload"]) is not dict:
            raise InvalidMissionEvent("payload must be a plain JSON object")
        return cls(
            kind=_contract_text(value["kind"], "kind", InvalidMissionEvent),
            mission_id=_contract_text(
                value["mission_id"], "mission_id", InvalidMissionEvent
            ),
            run_id=_contract_text(
                value["run_id"], "run_id", InvalidMissionEvent
            ),
            base_revision=_contract_int(
                value["base_revision"],
                "base_revision",
                InvalidMissionEvent,
                minimum=0,
            ),
            payload=value["payload"],
            event_id=_contract_text(
                value["event_id"], "event_id", InvalidMissionEvent
            ),
            format=_contract_text(
                value["format"], "format", InvalidMissionEvent
            ),
            evidence_refs=_wire_string_list(
                value["evidence_refs"], "evidence_refs", InvalidMissionEvent
            ),
            created_at=(
                None
                if value["created_at"] is None
                else _contract_float(
                    value["created_at"], "created_at", InvalidMissionEvent
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ObligationProjection:
    id: str
    status: ObligationStatus
    claim_evidence_refs: tuple[str, ...] = ()
    verified_evidence_refs: tuple[str, ...] = ()
    verification_id: str | None = None
    failure_count: int = 0
    last_reason: str = ""
    claim_fingerprint: str | None = None
    verified_claim_fingerprint: str | None = None
    activation_attempt_count: int = 0
    verification_attempt_count: int = 0
    no_progress_streak: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "id",
            _contract_text(
                self.id, "obligation projection id", InvalidMissionEvent
            ),
        )
        if type(self.status) is not ObligationStatus:
            raise InvalidMissionEvent(
                "obligation projection status must be an ObligationStatus"
            )
        object.__setattr__(
            self,
            "claim_evidence_refs",
            _contract_string_tuple(
                self.claim_evidence_refs,
                "claim_evidence_refs",
                InvalidMissionEvent,
            ),
        )
        object.__setattr__(
            self,
            "verified_evidence_refs",
            _contract_string_tuple(
                self.verified_evidence_refs,
                "verified_evidence_refs",
                InvalidMissionEvent,
            ),
        )
        object.__setattr__(
            self,
            "last_reason",
            _contract_text(
                self.last_reason,
                "last_reason",
                InvalidMissionEvent,
                allow_empty=True,
            ),
        )
        if self.verification_id is not None:
            object.__setattr__(
                self,
                "verification_id",
                _contract_text(
                    self.verification_id,
                    "verification_id",
                    InvalidMissionEvent,
                ),
            )
        if self.claim_fingerprint is not None:
            object.__setattr__(
                self,
                "claim_fingerprint",
                _contract_text(
                    self.claim_fingerprint,
                    "claim_fingerprint",
                    InvalidMissionEvent,
                ),
            )
        if self.verified_claim_fingerprint is not None:
            object.__setattr__(
                self,
                "verified_claim_fingerprint",
                _contract_text(
                    self.verified_claim_fingerprint,
                    "verified_claim_fingerprint",
                    InvalidMissionEvent,
                ),
            )
        for name in (
            "failure_count",
            "activation_attempt_count",
            "verification_attempt_count",
            "no_progress_streak",
        ):
            object.__setattr__(
                self,
                name,
                _contract_int(
                    getattr(self, name),
                    name,
                    InvalidMissionEvent,
                    minimum=0,
                ),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status.value,
            "claim_evidence_refs": list(self.claim_evidence_refs),
            "verified_evidence_refs": list(self.verified_evidence_refs),
            "verification_id": self.verification_id,
            "failure_count": self.failure_count,
            "last_reason": self.last_reason,
            "claim_fingerprint": self.claim_fingerprint,
            "verified_claim_fingerprint": self.verified_claim_fingerprint,
            "activation_attempt_count": self.activation_attempt_count,
            "verification_attempt_count": self.verification_attempt_count,
            "no_progress_streak": self.no_progress_streak,
        }


@dataclass(frozen=True, slots=True)
class MissionProjection:
    """Derived mission view; it is not an independent mutable ledger."""

    definition: MissionDefinition
    revision: int
    obligation_states: tuple[ObligationProjection, ...]
    active_obligation_id: str | None
    verified_facts: tuple[VerifiedFact, ...] = ()
    artifact_bindings: Mapping[str, ArtifactRef] = field(default_factory=dict)
    target_binding: TargetBinding | None = None
    evidence_refs: Mapping[str, EvidenceRef] = field(default_factory=dict)
    accepted_event_ids: tuple[str, ...] = ()
    pending_approval: Mapping[str, Any] | None = None
    failure_budgets: Mapping[str, int] = field(default_factory=dict)
    last_transition: TransitionSummary | None = None
    failure_summary: FailureSummary | None = None
    status: MissionStatus = MissionStatus.RUNNING
    mission_verification_id: str | None = None
    last_error: str = ""
    format: str = MISSION_PROJECTION_FORMAT

    def __post_init__(self) -> None:
        _contract_text(
            self.format, "MissionProjection format", InvalidMissionEvent
        )
        if self.format != MISSION_PROJECTION_FORMAT:
            raise InvalidMissionEvent(f"unsupported MissionProjection format: {self.format!r}")
        object.__setattr__(
            self,
            "revision",
            _contract_int(
                self.revision,
                "projection revision",
                InvalidMissionEvent,
                minimum=0,
            ),
        )
        object.__setattr__(self, "obligation_states", tuple(self.obligation_states))
        ids = tuple(item.id for item in self.obligation_states)
        if len(ids) != len(set(ids)):
            raise InvalidMissionEvent("projection obligation ids must be unique")
        object.__setattr__(self, "verified_facts", tuple(self.verified_facts))
        object.__setattr__(self, "artifact_bindings", _mapping(self.artifact_bindings))
        object.__setattr__(self, "evidence_refs", _mapping(self.evidence_refs))
        object.__setattr__(self, "failure_budgets", _mapping(self.failure_budgets))
        object.__setattr__(self, "accepted_event_ids", _as_string_tuple(self.accepted_event_ids))
        if type(self.status) is not MissionStatus:
            raise InvalidMissionEvent(
                "mission projection status must be a MissionStatus"
            )
        if self.mission_verification_id is not None:
            object.__setattr__(
                self,
                "mission_verification_id",
                _contract_text(
                    self.mission_verification_id,
                    "mission_verification_id",
                    InvalidMissionEvent,
                ),
            )
        if self.pending_approval is not None:
            try:
                object.__setattr__(
                    self,
                    "pending_approval",
                    _json_mapping(self.pending_approval),
                )
            except BoundedJSONError as exc:
                raise InvalidMissionEvent(
                    "pending_approval violates the bounded JSON contract: "
                    f"{exc}"
                ) from exc

    @property
    def mission_id(self) -> str:
        return self.definition.mission_id

    @property
    def run_id(self) -> str:
        return self.definition.run_id

    @property
    def original_goal(self) -> str:
        return self.definition.original_goal

    @property
    def verified_obligation_ids(self) -> frozenset[str]:
        return frozenset(
            state.id for state in self.obligation_states
            if state.status is ObligationStatus.VERIFIED
        )

    def obligation_state(self, obligation_id: str) -> ObligationProjection:
        for state in self.obligation_states:
            if state.id == obligation_id:
                return state
        raise KeyError(obligation_id)

    def obligation_definition(self, obligation_id: str) -> ObligationSpec:
        for obligation in self.definition.obligations:
            if obligation.id == obligation_id:
                return obligation
        raise KeyError(obligation_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "definition": self.definition.to_dict(),
            "revision": self.revision,
            "obligation_states": [item.to_dict() for item in self.obligation_states],
            "active_obligation_id": self.active_obligation_id,
            "verified_facts": [item.to_dict() for item in self.verified_facts],
            "artifact_bindings": {
                key: value.to_dict() for key, value in self.artifact_bindings.items()
            },
            "target_binding": self.target_binding.to_dict() if self.target_binding else None,
            "evidence_refs": {
                key: value.to_dict() for key, value in self.evidence_refs.items()
            },
            "accepted_event_ids": list(self.accepted_event_ids),
            "pending_approval": thaw(self.pending_approval),
            "failure_budgets": thaw(self.failure_budgets),
            "last_transition": self.last_transition.to_dict() if self.last_transition else None,
            "failure_summary": self.failure_summary.to_dict() if self.failure_summary else None,
            "status": self.status.value,
            "mission_verification_id": self.mission_verification_id,
            "last_error": self.last_error,
        }


@dataclass(frozen=True, slots=True)
class ActiveObligationCard:
    id: str
    desired_state: str
    acceptance_criteria: tuple[str, ...]
    forbidden_effects: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    depends_on: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "desired_state": self.desired_state,
            "acceptance_criteria": list(self.acceptance_criteria),
            "forbidden_effects": list(self.forbidden_effects),
            "required_capabilities": list(self.required_capabilities),
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True, slots=True)
class MissionCard:
    """Minimal per-turn actor view projected from MissionProjection."""

    mission_id: str
    mission_revision: int
    original_goal: str
    active_obligation: ActiveObligationCard | None
    verified_facts: tuple[VerifiedFact, ...] = ()
    artifact_bindings: tuple[ArtifactRef, ...] = ()
    target_binding: TargetBinding | None = None
    last_transition: TransitionSummary | None = None
    failure_summary: FailureSummary | None = None
    no_progress_streak: int = 0
    format: str = MISSION_CARD_FORMAT

    def __post_init__(self) -> None:
        if self.format != MISSION_CARD_FORMAT:
            raise InvalidMissionEvent(f"unsupported MissionCard format: {self.format!r}")
        object.__setattr__(self, "verified_facts", tuple(self.verified_facts))
        object.__setattr__(self, "artifact_bindings", tuple(self.artifact_bindings))
        if type(self.no_progress_streak) is not int or self.no_progress_streak < 0:
            raise InvalidMissionEvent("MissionCard no_progress_streak must be non-negative")

    @property
    def active_obligation_id(self) -> str | None:
        return self.active_obligation.id if self.active_obligation else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "mission_id": self.mission_id,
            "mission_revision": self.mission_revision,
            "original_goal": self.original_goal,
            "active_obligation": self.active_obligation.to_dict() if self.active_obligation else None,
            "verified_facts": [item.to_dict() for item in self.verified_facts],
            "artifact_bindings": [item.to_dict() for item in self.artifact_bindings],
            "target_binding": self.target_binding.to_dict() if self.target_binding else None,
            "last_transition": self.last_transition.to_dict() if self.last_transition else None,
            "failure_summary": self.failure_summary.to_dict() if self.failure_summary else None,
            "no_progress_streak": self.no_progress_streak,
        }

    @classmethod
    def from_projection(cls, projection: MissionProjection) -> "MissionCard":
        active = None
        relevant_facts: list[VerifiedFact] = []
        relevant_artifacts: list[ArtifactRef] = []
        if projection.active_obligation_id:
            definition = projection.obligation_definition(projection.active_obligation_id)
            active = ActiveObligationCard(
                id=definition.id,
                desired_state=definition.desired_state,
                acceptance_criteria=definition.acceptance_criteria,
                forbidden_effects=definition.forbidden_effects,
                required_capabilities=definition.required_capabilities,
                depends_on=definition.depends_on,
            )
            active_id = definition.id
            for fact in projection.verified_facts:
                evidence_related = any(
                    projection.evidence_refs.get(ref_id)
                    and projection.evidence_refs[ref_id].obligation_id == active_id
                    for ref_id in fact.evidence_refs
                )
                if (
                    active_id in fact.valid_for_obligation_ids
                    or (not fact.valid_for_obligation_ids and fact.scope in {"mission", active_id})
                    or evidence_related
                ):
                    relevant_facts.append(fact)
            needed = set(definition.input_artifact_refs) | set(definition.output_binding_names)
            seen_artifacts: set[str] = set()
            for name, artifact in projection.artifact_bindings.items():
                if (
                    name in needed
                    or artifact.artifact_id in needed
                    or artifact.producer_obligation_id == active_id
                ) and artifact.artifact_id not in seen_artifacts:
                    seen_artifacts.add(artifact.artifact_id)
                    relevant_artifacts.append(artifact)
        active_id = projection.active_obligation_id
        transition = projection.last_transition
        if transition is not None and transition.obligation_id != active_id:
            transition = None
        failure = projection.failure_summary
        if failure is not None and failure.obligation_id != active_id:
            failure = None
        no_progress_streak = (
            projection.obligation_state(active_id).no_progress_streak
            if active_id is not None
            else 0
        )
        return cls(
            mission_id=projection.mission_id,
            mission_revision=projection.revision,
            original_goal=projection.original_goal,
            active_obligation=active,
            verified_facts=tuple(relevant_facts),
            artifact_bindings=tuple(relevant_artifacts),
            target_binding=projection.target_binding,
            last_transition=transition,
            failure_summary=failure,
            no_progress_streak=no_progress_streak,
        )


@dataclass(frozen=True, slots=True)
class EvidenceScope:
    """Immutable evidence index constrained to one mission/run."""

    mission_id: str
    run_id: str
    refs: Mapping[str, EvidenceRef] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized: dict[str, EvidenceRef] = {}
        source = self.refs.values() if isinstance(self.refs, Mapping) else self.refs
        for ref in source:
            item = ref if isinstance(ref, EvidenceRef) else EvidenceRef.from_dict(ref)
            if item.id in normalized:
                raise EvidenceScopeError(f"duplicate evidence id: {item.id}")
            if item.mission_id != self.mission_id or item.run_id != self.run_id:
                raise EvidenceScopeError(
                    f"evidence {item.id!r} is outside mission/run scope"
                )
            normalized[item.id] = item
        object.__setattr__(self, "refs", MappingProxyType(normalized))

    @classmethod
    def from_refs(
        cls,
        mission_id: str,
        run_id: str,
        refs: "EvidenceScope | Iterable[EvidenceRef] | None" = None,
    ) -> "EvidenceScope":
        if isinstance(refs, EvidenceScope):
            if refs.mission_id != mission_id or refs.run_id != run_id:
                raise EvidenceScopeError("evidence scope does not match definition")
            return refs
        return cls(mission_id=mission_id, run_id=run_id, refs=tuple(refs or ()))

    @classmethod
    def from_source(
        cls,
        mission_id: str,
        run_id: str,
        source: Any = None,
    ) -> "EvidenceScope":
        """Read the current external registry snapshot for one apply call."""
        if isinstance(source, EvidenceScope):
            if source.mission_id != mission_id or source.run_id != run_id:
                raise EvidenceScopeError("evidence scope does not match definition")
            return source
        current = source
        if callable(current):
            current = current()
        elif current is not None and hasattr(current, "snapshot"):
            current = current.snapshot()
        elif current is not None and hasattr(current, "evidence_scope"):
            current = current.evidence_scope
        elif current is not None and hasattr(current, "refs"):
            current = current.refs
        if isinstance(current, Mapping):
            current = current.values()
        return cls.from_refs(mission_id, run_id, current)

    def require(self, ids: Iterable[str], *, obligation_id: str | None = None) -> tuple[EvidenceRef, ...]:
        normalized = _as_string_tuple(ids)
        if not normalized:
            raise EvidenceScopeError("at least one evidence reference is required")
        found: list[EvidenceRef] = []
        for evidence_id in normalized:
            ref = self.refs.get(evidence_id)
            if ref is None:
                raise EvidenceScopeError(f"evidence ref is not in run scope: {evidence_id}")
            if (
                obligation_id
                and ref.obligation_id not in (None, "", obligation_id)
            ):
                raise EvidenceScopeError(
                    f"evidence {evidence_id!r} belongs to another obligation"
                )
            found.append(ref)
        return tuple(found)


def _stable_topological_order(
    obligations: tuple[ObligationSpec, ...],
) -> tuple[str, ...]:
    """Return a deterministic dependency-first order.

    Declaration order is the tie breaker.  This is a pure serialization aid;
    it does not encode GUI steps or choose a route for the actor.
    """
    graph = {item.id: item.depends_on for item in obligations}
    ordered: list[str] = []
    colors = {node: 0 for node in graph}
    for root in graph:
        if colors[root] == 2:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        while stack:
            node, dependency_index = stack[-1]
            if colors[node] == 0:
                colors[node] = 1
            dependencies = graph[node]
            if dependency_index < len(dependencies):
                dependency = dependencies[dependency_index]
                stack[-1] = (node, dependency_index + 1)
                if colors[dependency] == 1:
                    raise InvalidMissionDefinition(
                        "obligation dependency graph contains a cycle"
                    )
                if colors[dependency] == 0:
                    stack.append((dependency, 0))
                continue
            stack.pop()
            if colors[node] != 2:
                colors[node] = 2
                ordered.append(node)
    return tuple(ordered)


__all__ = [
    "MISSION_DEFINITION_FORMAT", "MISSION_EVENT_FORMAT", "MISSION_PROJECTION_FORMAT",
    "MISSION_CARD_FORMAT", "ACTION_EFFECT_FORMAT", "ACTION_EFFECT_COMPARATOR_VERSION",
    "ACTION_EFFECT_EVIDENCE_KIND", "ACTION_EFFECT_CHANGE_RATIO_THRESHOLD",
    "MissionError", "InvalidMissionDefinition",
    "InvalidMissionEvent", "StaleRevisionError", "DuplicateEventError",
    "EvidenceScopeError", "IllegalTransitionError", "ObligationStatus", "MissionStatus",
    "LocalVisualChange", "ActionEffectReason", "ActionEffectSummary",
    "JSONValue", "EvidencePolicy", "ObligationSpec", "EvidenceRef", "ArtifactRef",
    "TargetBinding", "VerifiedFact", "TransitionSummary", "FailureSummary",
    "MissionDefinition", "MissionEvent", "ObligationProjection", "MissionProjection",
    "ActiveObligationCard", "MissionCard", "EvidenceScope",
    "is_safe_action_summary", "thaw",
]
