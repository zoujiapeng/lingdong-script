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

from ...process_identity import ProcessIdentityError, canonical_process_identity


JSONValue = Any


MISSION_DEFINITION_FORMAT = "mission-definition/0"
MISSION_EVENT_FORMAT = "mission-event/1"
MISSION_PROJECTION_FORMAT = "mission-projection/2"
MISSION_CARD_FORMAT = "mission-card/2"
ACTION_EFFECT_FORMAT = "action-effect/0"
ACTION_EFFECT_COMPARATOR_VERSION = "frame-delta/0"
ACTION_EFFECT_EVIDENCE_KIND = "deterministic_action_effect"
TARGET_BINDING_IDENTITY_FORMAT = "target-binding-identity/1"
TARGET_BINDING_EVIDENCE_FORMAT = "target-binding-evidence/1"
TARGET_BINDING_EVIDENCE_KIND = "authoritative_target_binding"
# ``unchanged`` is intentionally a high-precision fact: any decoded pixel
# delta is reported as ``changed``.  A non-zero threshold can turn a real
# small-control response (caret, checkbox, focus ring) into false no-progress.
ACTION_EFFECT_CHANGE_RATIO_THRESHOLD = 0.0
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_CANONICAL_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TARGET_BINDING_ID_RE = re.compile(r"^target-[0-9a-f]{24}$")


def _as_tuple(value: Iterable[Any] | None) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (set, frozenset)):
        raise TypeError("unordered sets are not valid contract sequences")
    return tuple(value)


def _as_string_tuple(value: Iterable[Any] | None) -> tuple[str, ...]:
    return tuple(str(item) for item in _as_tuple(value) if str(item).strip())


def _freeze(value: Any) -> Any:
    """Recursively freeze a strict JSONValue payload."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            frozen[key] = _freeze(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple, set, frozenset)):
        if isinstance(value, (set, frozenset)):
            raise TypeError("JSON arrays cannot be sets")
        return tuple(_freeze(item) for item in value)
    raise TypeError(f"value of type {type(value).__name__} is not JSONValue")


def thaw(value: Any) -> Any:
    """Return ordinary JSON-compatible containers for serialization."""
    if isinstance(value, Mapping):
        return {str(key): thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
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


def _json_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Freeze and validate a mapping whose values must be strict JSONValue."""
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise TypeError("value must be a JSON object mapping")
    frozen: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("JSON object keys must be strings")
        frozen[key] = _freeze(item)
    return MappingProxyType(frozen)


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "required_evidence_kinds", _as_string_tuple(self.required_evidence_kinds))
        object.__setattr__(self, "preferred_evidence_kinds", _as_string_tuple(self.preferred_evidence_kinds))
        object.__setattr__(self, "allow_independent_visual_verifier", bool(self.allow_independent_visual_verifier))
        if int(self.max_verification_attempts) < 1:
            raise InvalidMissionDefinition("max_verification_attempts must be positive")
        object.__setattr__(self, "max_verification_attempts", int(self.max_verification_attempts))
        if str(self.uncertain_policy) not in {"collect_more", "ask_user", "blocked", "fail"}:
            raise InvalidMissionDefinition("invalid uncertain_policy")
        object.__setattr__(self, "uncertain_policy", str(self.uncertain_policy))

    @property
    def allowed_trust(self) -> tuple[str, ...]:
        """Compatibility view; trust is a runtime evidence invariant."""
        return DEFAULT_ALLOWED_TRUST

    @property
    def minimum_evidence(self) -> int:
        return 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_evidence_kinds": list(self.required_evidence_kinds),
            "preferred_evidence_kinds": list(self.preferred_evidence_kinds),
            "allow_independent_visual_verifier": self.allow_independent_visual_verifier,
            "max_verification_attempts": self.max_verification_attempts,
            "uncertain_policy": self.uncertain_policy,
        }


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

    def __post_init__(self) -> None:
        if not str(self.id).strip():
            raise InvalidMissionDefinition("obligation id cannot be empty")
        if not str(self.desired_state).strip():
            raise InvalidMissionDefinition(f"obligation {self.id!r} has no desired_state")
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "desired_state", str(self.desired_state))
        for name in (
            "acceptance_criteria", "depends_on", "forbidden_effects",
            "required_capabilities", "input_artifact_refs", "output_binding_names",
        ):
            object.__setattr__(self, name, _as_string_tuple(getattr(self, name)))
        if not self.acceptance_criteria:
            object.__setattr__(self, "acceptance_criteria", (self.desired_state,))
        if not self.required_capabilities:
            object.__setattr__(self, "required_capabilities", ("visual.interact",))
        policy = self.evidence_policy
        if isinstance(policy, Mapping):
            policy = EvidencePolicy(**dict(policy))
        if not isinstance(policy, EvidencePolicy):
            raise InvalidMissionDefinition(f"obligation {self.id!r} has invalid evidence_policy")
        object.__setattr__(self, "evidence_policy", policy)
        if self.source_span is not None:
            span = tuple(int(item) for item in self.source_span)
            if len(span) != 2 or span[0] < 0 or span[1] < span[0]:
                raise InvalidMissionDefinition(f"obligation {self.id!r} has invalid source_span")
            object.__setattr__(self, "source_span", span)

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
        if not isinstance(raw, Mapping):
            raise InvalidMissionDefinition("obligation must be a JSON object")
        return cls(
            id=str(raw.get("id") or ""),
            desired_state=str(raw.get("desired_state") or raw.get("description") or ""),
            acceptance_criteria=raw.get("acceptance_criteria") or raw.get("criteria") or (),
            depends_on=raw.get("depends_on") or (),
            forbidden_effects=raw.get("forbidden_effects") or (),
            required_capabilities=raw.get("required_capabilities") or (),
            input_artifact_refs=raw.get("input_artifact_refs") or (),
            output_binding_names=raw.get("output_binding_names") or (),
            evidence_policy=raw.get("evidence_policy") or {},
            required=bool(raw.get("required", True)),
            source_span=tuple(raw["source_span"]) if raw.get("source_span") else None,
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

    def __post_init__(self) -> None:
        for name in ("id", "mission_id", "run_id", "kind", "trust"):
            if not str(getattr(self, name) or "").strip():
                raise EvidenceScopeError(f"evidence {name} cannot be empty")
            object.__setattr__(self, name, str(getattr(self, name)))
        if not str(self.payload_hash or "").strip():
            raise EvidenceScopeError("evidence payload_hash cannot be empty")
        object.__setattr__(self, "payload_hash", str(self.payload_hash))
        if self.obligation_id is not None:
            value = str(self.obligation_id).strip()
            object.__setattr__(self, "obligation_id", value or None)
        if self.action_id is not None:
            value = str(self.action_id).strip()
            object.__setattr__(self, "action_id", value or None)
        if self.artifact_id is not None:
            value = str(self.artifact_id).strip()
            if not value:
                object.__setattr__(self, "artifact_id", None)
            else:
                object.__setattr__(self, "artifact_id", value)
                if self.content_sha256 is None:
                    raise EvidenceScopeError(
                        "artifact evidence requires content_sha256"
                    )
        if self.content_sha256 is not None:
            content_sha = str(self.content_sha256).strip().lower()
            if self.artifact_id is None:
                raise EvidenceScopeError("content_sha256 requires artifact_id")
            if not _SHA256_RE.fullmatch(content_sha):
                raise EvidenceScopeError("content_sha256 must be a 64-character SHA-256 hex digest")
            object.__setattr__(self, "content_sha256", content_sha)
        if (
            self.frame_id is not None
            and (type(self.frame_id) is not int or self.frame_id <= 0)
        ):
            raise EvidenceScopeError(
                "evidence frame_id must be a positive integer or null"
            )
        if self.created_at is not None and not math.isfinite(float(self.created_at)):
            raise EvidenceScopeError("evidence created_at must be finite")

    @property
    def evidence_id(self) -> str:
        return self.id

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
        return cls(
            id=str(raw.get("id") or raw.get("evidence_id") or ""),
            mission_id=str(raw.get("mission_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            kind=str(raw.get("kind") or ""),
            trust=str(raw.get("trust") or ""),
            obligation_id=(str(raw["obligation_id"]) if raw.get("obligation_id") is not None else None),
            payload_hash=str(raw.get("payload_hash") or ""),
            frame_id=raw.get("frame_id"),
            action_id=(str(raw["action_id"]) if raw.get("action_id") is not None else None),
            artifact_id=(str(raw["artifact_id"]) if raw.get("artifact_id") is not None else None),
            created_at=(float(raw["created_at"]) if raw.get("created_at") is not None else None),
            content_sha256=(str(raw["content_sha256"]) if raw.get("content_sha256") is not None else None),
        )


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    media_type: str
    sha256: str
    producer_obligation_id: str
    evidence_ref: str

    def __post_init__(self) -> None:
        for name in ("artifact_id", "media_type", "sha256", "producer_obligation_id", "evidence_ref"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise InvalidMissionEvent(f"artifact {name} cannot be empty")
            object.__setattr__(self, name, value)
        digest = str(self.sha256).lower()
        if not _SHA256_RE.fullmatch(digest):
            raise InvalidMissionEvent("artifact sha256 must be a 64-character SHA-256 hex digest")
        object.__setattr__(self, "sha256", digest)

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
        return cls(
            artifact_id=str(raw.get("artifact_id") or ""),
            media_type=str(raw.get("media_type") or "application/octet-stream"),
            sha256=str(raw.get("sha256") or ""),
            producer_obligation_id=str(raw.get("producer_obligation_id") or ""),
            evidence_ref=str(raw.get("evidence_ref") or ""),
        )


def target_binding_identity_sha256(
    *,
    hwnd: int,
    root_hwnd: int,
    process_id: int,
    process_identity: str,
    epoch: int,
) -> str:
    """Hash the complete stable window/process identity used by a binding."""

    for name, value in (
        ("hwnd", hwnd),
        ("root_hwnd", root_hwnd),
        ("process_id", process_id),
    ):
        if type(value) is not int or value <= 0:
            raise InvalidMissionEvent(f"target {name} must be a positive integer")
    if type(epoch) is not int or epoch < 0:
        raise InvalidMissionEvent("target epoch must be a non-negative integer")
    try:
        canonical_process = canonical_process_identity(process_identity)
    except ProcessIdentityError as exc:
        raise InvalidMissionEvent(
            "target canonical process identity is required"
        ) from exc
    return _canonical_sha256({
        "format": TARGET_BINDING_IDENTITY_FORMAT,
        "hwnd": hwnd,
        "root_hwnd": root_hwnd,
        "process_id": process_id,
        "process_identity": canonical_process,
        "epoch": epoch,
    })


def target_binding_id_for(
    *,
    mission_id: str,
    run_id: str,
    canonical_identity_sha256: str,
    acquisition_action_id: str,
) -> str:
    """Derive one run- and acquisition-scoped binding identity."""

    values: dict[str, str] = {}
    for name, value in (
        ("mission_id", mission_id),
        ("run_id", run_id),
        ("acquisition_action_id", acquisition_action_id),
    ):
        if type(value) is not str or not value.strip():
            raise InvalidMissionEvent(f"target {name} cannot be empty")
        values[name] = value.strip()
    if (
        type(canonical_identity_sha256) is not str
        or not _CANONICAL_SHA256_RE.fullmatch(canonical_identity_sha256)
    ):
        raise InvalidMissionEvent(
            "target canonical_identity_sha256 must be a lowercase SHA-256 digest"
        )
    digest = _canonical_sha256({
        "format": "target-binding-id/1",
        **values,
        "canonical_identity_sha256": canonical_identity_sha256,
    })
    return f"target-{digest[:24]}"


@dataclass(frozen=True, slots=True)
class TargetBinding:
    binding_id: str
    hwnd: int
    root_hwnd: int
    process_id: int
    process_identity: str
    title_snapshot: str
    epoch: int
    canonical_identity_sha256: str
    acquisition_action_id: str
    established_by_evidence_ref: str

    def __post_init__(self) -> None:
        for name in (
            "binding_id",
            "process_identity",
            "acquisition_action_id",
            "established_by_evidence_ref",
        ):
            raw = getattr(self, name)
            if type(raw) is not str or not raw.strip():
                raise InvalidMissionEvent(f"target {name} cannot be empty")
            value = raw.strip()
            if len(value) > 2048:
                raise InvalidMissionEvent(f"target {name} is too long")
            object.__setattr__(self, name, value)
        if not _TARGET_BINDING_ID_RE.fullmatch(self.binding_id):
            raise InvalidMissionEvent("target binding_id is not canonical")
        if type(self.title_snapshot) is not str:
            raise InvalidMissionEvent("target title_snapshot must be a string")
        if len(self.title_snapshot) > 4096:
            raise InvalidMissionEvent("target title_snapshot is too long")
        for name in ("hwnd", "root_hwnd", "process_id"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise InvalidMissionEvent(
                    f"target {name} must be a positive integer"
                )
        if type(self.epoch) is not int or self.epoch < 0:
            raise InvalidMissionEvent(
                "target epoch must be a non-negative integer"
            )
        if (
            type(self.canonical_identity_sha256) is not str
            or not _CANONICAL_SHA256_RE.fullmatch(
                self.canonical_identity_sha256
            )
        ):
            raise InvalidMissionEvent(
                "target canonical_identity_sha256 must be a lowercase SHA-256 digest"
            )
        expected_identity = target_binding_identity_sha256(
            hwnd=self.hwnd,
            root_hwnd=self.root_hwnd,
            process_id=self.process_id,
            process_identity=self.process_identity,
            epoch=self.epoch,
        )
        if self.canonical_identity_sha256 != expected_identity:
            raise InvalidMissionEvent(
                "target canonical identity hash does not match its identity fields"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "hwnd": self.hwnd,
            "root_hwnd": self.root_hwnd,
            "process_id": self.process_id,
            "process_identity": self.process_identity,
            "title_snapshot": self.title_snapshot,
            "epoch": self.epoch,
            "canonical_identity_sha256": self.canonical_identity_sha256,
            "acquisition_action_id": self.acquisition_action_id,
            "established_by_evidence_ref": self.established_by_evidence_ref,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TargetBinding":
        if not isinstance(raw, Mapping):
            raise InvalidMissionEvent("target binding must be a JSON object")
        fields = {
            "binding_id",
            "hwnd",
            "root_hwnd",
            "process_id",
            "process_identity",
            "title_snapshot",
            "epoch",
            "canonical_identity_sha256",
            "acquisition_action_id",
            "established_by_evidence_ref",
        }
        missing, extra = fields - set(raw), set(raw) - fields
        if missing or extra:
            raise InvalidMissionEvent(
                "invalid target binding fields; "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
        return cls(
            binding_id=raw["binding_id"],
            hwnd=raw["hwnd"],
            root_hwnd=raw["root_hwnd"],
            process_id=raw["process_id"],
            process_identity=raw["process_identity"],
            title_snapshot=raw["title_snapshot"],
            epoch=raw["epoch"],
            canonical_identity_sha256=raw["canonical_identity_sha256"],
            acquisition_action_id=raw["acquisition_action_id"],
            established_by_evidence_ref=raw["established_by_evidence_ref"],
        )


def target_binding_evidence_payload_hash(
    *, mission_id: str, run_id: str, binding: TargetBinding,
) -> str:
    """Bind one deterministic target attestation to its mission/run/action."""

    if not isinstance(binding, TargetBinding):
        raise InvalidMissionEvent("binding must be TargetBinding")
    expected_binding_id = target_binding_id_for(
        mission_id=mission_id,
        run_id=run_id,
        canonical_identity_sha256=binding.canonical_identity_sha256,
        acquisition_action_id=binding.acquisition_action_id,
    )
    if binding.binding_id != expected_binding_id:
        raise InvalidMissionEvent(
            "target binding_id does not match mission/run/acquisition identity"
        )
    return _canonical_sha256({
        "format": TARGET_BINDING_EVIDENCE_FORMAT,
        "mission_id": mission_id.strip(),
        "run_id": run_id.strip(),
        "binding_id": binding.binding_id,
        "canonical_identity_sha256": binding.canonical_identity_sha256,
        "acquisition_action_id": binding.acquisition_action_id,
        "title_snapshot": binding.title_snapshot,
    })


def target_binding_attestation(
    *,
    mission_id: str,
    run_id: str,
    obligation_id: str,
    evidence_id: str,
    acquisition_action_id: str,
    hwnd: int,
    root_hwnd: int,
    process_id: int,
    process_identity: str,
    title_snapshot: str,
    epoch: int,
    frame_id: int | None = None,
    created_at: float | None = None,
) -> tuple[TargetBinding, EvidenceRef]:
    """Build the one canonical binding/evidence pair accepted by the reducer."""

    identity = target_binding_identity_sha256(
        hwnd=hwnd,
        root_hwnd=root_hwnd,
        process_id=process_id,
        process_identity=process_identity,
        epoch=epoch,
    )
    binding = TargetBinding(
        binding_id=target_binding_id_for(
            mission_id=mission_id,
            run_id=run_id,
            canonical_identity_sha256=identity,
            acquisition_action_id=acquisition_action_id,
        ),
        hwnd=hwnd,
        root_hwnd=root_hwnd,
        process_id=process_id,
        process_identity=process_identity,
        title_snapshot=title_snapshot,
        epoch=epoch,
        canonical_identity_sha256=identity,
        acquisition_action_id=acquisition_action_id,
        established_by_evidence_ref=evidence_id,
    )
    evidence = EvidenceRef(
        id=evidence_id,
        mission_id=mission_id,
        run_id=run_id,
        obligation_id=obligation_id,
        kind=TARGET_BINDING_EVIDENCE_KIND,
        trust="deterministic",
        payload_hash=target_binding_evidence_payload_hash(
            mission_id=mission_id,
            run_id=run_id,
            binding=binding,
        ),
        frame_id=frame_id,
        action_id=acquisition_action_id,
        created_at=created_at,
    )
    return binding, evidence


@dataclass(frozen=True, slots=True)
class VerifiedFact:
    id: str
    statement: str
    evidence_refs: tuple[str, ...]
    scope: str
    valid_for_obligation_ids: tuple[str, ...] = ()
    expires_on_epoch_change: bool = True

    def __post_init__(self) -> None:
        for name in ("id", "statement", "scope"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise InvalidMissionEvent(f"verified fact {name} cannot be empty")
            object.__setattr__(self, name, value)
        evidence_refs = _as_string_tuple(self.evidence_refs)
        if not evidence_refs:
            raise EvidenceScopeError("verified fact requires evidence_refs")
        if len(evidence_refs) != len(set(evidence_refs)):
            raise EvidenceScopeError("verified fact evidence_refs must be unique")
        valid_for = _as_string_tuple(self.valid_for_obligation_ids)
        if len(valid_for) != len(set(valid_for)):
            raise InvalidMissionEvent("verified fact obligation ids must be unique")
        object.__setattr__(self, "evidence_refs", evidence_refs)
        object.__setattr__(self, "valid_for_obligation_ids", valid_for)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "evidence_refs": list(self.evidence_refs),
            "scope": self.scope,
            "valid_for_obligation_ids": list(self.valid_for_obligation_ids),
            "expires_on_epoch_change": self.expires_on_epoch_change,
        }


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

    def __post_init__(self) -> None:
        if self.format != ACTION_EFFECT_FORMAT:
            raise InvalidMissionEvent("unsupported action effect format")
        if self.comparator_version != ACTION_EFFECT_COMPARATOR_VERSION:
            raise InvalidMissionEvent("unsupported action effect comparator")
        for name in (
            "action_id",
            "before_frame_evidence_ref",
            "after_frame_evidence_ref",
            "evidence_ref",
        ):
            raw_value = getattr(self, name)
            if type(raw_value) is not str or not raw_value.strip():
                raise InvalidMissionEvent(f"action effect {name} cannot be empty")
            object.__setattr__(self, name, raw_value.strip())
        for name in ("before_frame_payload_hash", "after_frame_payload_hash"):
            value = getattr(self, name)
            if type(value) is not str or not _SHA256_RE.fullmatch(value):
                raise InvalidMissionEvent(
                    f"action effect {name} must be a SHA-256 digest"
                )
            object.__setattr__(self, name, value.lower())
        if type(self.input_completed) is not bool:
            raise InvalidMissionEvent("action effect input_completed must be boolean")
        for name in ("target_binding_valid", "frame_comparable"):
            if type(getattr(self, name)) is not bool:
                raise InvalidMissionEvent(f"action effect {name} must be boolean")
        for name in ("before_frame_id", "after_frame_id"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise InvalidMissionEvent(f"action effect {name} must be positive")
        if self.after_frame_id <= self.before_frame_id:
            raise InvalidMissionEvent("action effect frames must be in commit order")
        try:
            change = LocalVisualChange(self.local_visual_change)
            reason = ActionEffectReason(self.reason)
        except ValueError as exc:
            raise InvalidMissionEvent("invalid action effect classification") from exc
        object.__setattr__(self, "local_visual_change", change)
        object.__setattr__(self, "reason", reason)
        if self.semantic_completion != "unknown":
            raise InvalidMissionEvent("pixel comparison cannot claim semantic completion")
        ratio = self.change_ratio
        if ratio is not None:
            if (
                isinstance(ratio, bool)
                or not isinstance(ratio, (int, float))
                or not math.isfinite(float(ratio))
                or not 0.0 <= float(ratio) <= 1.0
            ):
                raise InvalidMissionEvent("action effect change_ratio must be null or in [0, 1]")
            ratio = float(ratio)
            object.__setattr__(self, "change_ratio", ratio)

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
        if not isinstance(raw, Mapping):
            raise InvalidMissionEvent("action effect must be an object")
        expected = {
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
        }
        if set(raw) != expected:
            raise InvalidMissionEvent("action effect fields do not match the canonical schema")
        result = cls(
            format=raw["format"],
            comparator_version=raw["comparator_version"],
            action_id=raw["action_id"],
            input_completed=raw["input_completed"],
            before_frame_id=raw["before_frame_id"],
            after_frame_id=raw["after_frame_id"],
            before_frame_evidence_ref=raw["before_frame_evidence_ref"],
            after_frame_evidence_ref=raw["after_frame_evidence_ref"],
            before_frame_payload_hash=raw["before_frame_payload_hash"],
            after_frame_payload_hash=raw["after_frame_payload_hash"],
            target_binding_valid=raw["target_binding_valid"],
            frame_comparable=raw["frame_comparable"],
            local_visual_change=raw["local_visual_change"],
            change_ratio=raw["change_ratio"],
            reason=raw["reason"],
            semantic_completion=raw["semantic_completion"],
            evidence_ref=raw["evidence_ref"],
        )
        if raw["payload_hash"] != result.payload_hash:
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
        object.__setattr__(self, "action", str(self.action or "unknown"))
        if type(self.input_completed) is not bool:
            raise InvalidMissionEvent("transition input_completed must be boolean")
        object.__setattr__(self, "obligation_id", str(self.obligation_id or "").strip())
        if not self.obligation_id:
            raise InvalidMissionEvent("transition summary must bind an obligation")
        if self.action_id is not None:
            action_id = str(self.action_id or "").strip()
            if not action_id:
                raise InvalidMissionEvent("transition action_id cannot be empty")
            object.__setattr__(self, "action_id", action_id)
        object.__setattr__(self, "evidence_refs", _as_string_tuple(self.evidence_refs))
        if self.frame_id is not None and (
            type(self.frame_id) is not int or self.frame_id <= 0
        ):
            raise InvalidMissionEvent(
                "transition frame_id must be a positive integer or null"
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
        object.__setattr__(self, "kind", str(self.kind or "failure"))
        object.__setattr__(self, "obligation_id", str(self.obligation_id or "").strip())
        if not self.obligation_id:
            raise InvalidMissionEvent("failure summary must bind an obligation")
        object.__setattr__(self, "evidence_refs", _as_string_tuple(self.evidence_refs))
        if int(self.count) < 1:
            raise InvalidMissionEvent("failure count must be positive")
        object.__setattr__(self, "count", int(self.count))

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

    def __post_init__(self) -> None:
        if self.format != MISSION_DEFINITION_FORMAT:
            raise InvalidMissionDefinition(f"unsupported MissionDefinition format: {self.format!r}")
        if not str(self.mission_id).strip() or not str(self.run_id).strip():
            raise InvalidMissionDefinition("mission_id and run_id are required")
        if not str(self.original_goal).strip():
            raise InvalidMissionDefinition("original_goal cannot be empty")
        object.__setattr__(self, "mission_id", str(self.mission_id))
        object.__setattr__(self, "run_id", str(self.run_id))
        object.__setattr__(self, "original_goal", str(self.original_goal))
        obligations = tuple(
            item if isinstance(item, ObligationSpec) else ObligationSpec.from_dict(item)
            for item in _as_tuple(self.obligations)
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
        _assert_acyclic(obligations)
        object.__setattr__(self, "obligations", obligations)
        try:
            object.__setattr__(self, "metadata", _json_mapping(self.metadata))
        except (TypeError, ValueError) as exc:
            raise InvalidMissionDefinition("metadata must be JSONValue") from exc
        if int(self.goal_revision) < 0:
            raise InvalidMissionDefinition("goal_revision cannot be negative")
        topological_order = _stable_topological_order(obligations)
        requested_order = _as_string_tuple(self.stable_topological_order)
        if requested_order and requested_order != topological_order:
            raise InvalidMissionDefinition(
                "stable_topological_order does not match the obligation DAG"
            )
        object.__setattr__(self, "stable_topological_order", topological_order)
        policy = str(self.experience_policy or "allow")
        object.__setattr__(self, "experience_policy", policy)

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
        return cls(
            mission_id=mission_id,
            run_id=run_id,
            original_goal=original_goal,
            obligations=(
                ObligationSpec(
                    id="obligation-1",
                    desired_state=original_goal,
                    acceptance_criteria=(original_goal,),
                    required_capabilities=tuple(required_capabilities),
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
        if not isinstance(raw, Mapping):
            raise InvalidMissionDefinition("MissionDefinition must be a JSON object")
        return cls(
            mission_id=str(raw.get("mission_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            original_goal=str(raw.get("original_goal") or ""),
            obligations=tuple(
                ObligationSpec.from_dict(item)
                for item in (raw.get("obligations") or ())
            ),
            goal_revision=int(raw.get("goal_revision") or 0),
            source=str(raw.get("source") or "vnext"),
            metadata=raw.get("metadata") or {},
            format=str(raw.get("format") or ""),
            stable_topological_order=raw.get("stable_topological_order") or (),
            experience_policy=str(raw.get("experience_policy") or "allow"),
        )


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

    def __init__(
        self,
        kind: str | None = None,
        mission_id: str = "",
        run_id: str = "",
        base_revision: int = 0,
        payload: Mapping[str, Any] | None = None,
        event_id: str = "",
        format: str = MISSION_EVENT_FORMAT,
        evidence_refs: Iterable[str] | None = None,
        created_at: float | None = None,
    ) -> None:
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "mission_id", mission_id)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "base_revision", base_revision)
        object.__setattr__(self, "payload", {} if payload is None else payload)
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "format", format)
        if evidence_refs is None:
            normalized_refs: tuple[Any, ...] = ()
        elif isinstance(evidence_refs, (list, tuple)):
            normalized_refs = tuple(evidence_refs)
        else:
            raise InvalidMissionEvent(
                "evidence_refs must be an ordered sequence of strings"
            )
        object.__setattr__(self, "evidence_refs", normalized_refs)
        object.__setattr__(self, "created_at", created_at)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.format != MISSION_EVENT_FORMAT:
            raise InvalidMissionEvent(f"unsupported MissionEvent format: {self.format!r}")
        if type(self.kind) is not str or not self.kind.strip():
            raise InvalidMissionEvent("event kind cannot be empty")
        if (
            type(self.mission_id) is not str
            or not self.mission_id.strip()
            or type(self.run_id) is not str
            or not self.run_id.strip()
        ):
            raise InvalidMissionEvent("event mission_id and run_id are required")
        if type(self.event_id) is not str:
            raise InvalidMissionEvent("event_id must be a string")
        if type(self.base_revision) is not int or self.base_revision < 0:
            raise InvalidMissionEvent(
                "base_revision must be a non-negative integer"
            )
        if self.created_at is not None:
            if (
                type(self.created_at) not in (int, float)
                or not math.isfinite(float(self.created_at))
            ):
                raise InvalidMissionEvent("created_at must be a finite number or null")
            object.__setattr__(self, "created_at", float(self.created_at))
        try:
            object.__setattr__(self, "payload", _json_mapping(self.payload))
        except (TypeError, ValueError) as exc:
            raise InvalidMissionEvent("payload must contain only JSONValue") from exc
        if any(
            type(ref) is not str or not ref.strip()
            for ref in self.evidence_refs
        ):
            raise InvalidMissionEvent(
                "evidence_refs must contain only non-empty strings"
            )
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
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

    @property
    def event_type(self) -> str:
        return self.kind

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
            payload=payload or {},
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
        if not isinstance(raw, Mapping):
            raise InvalidMissionEvent("MissionEvent must be a JSON object")
        fields = {
            "format", "event_id", "mission_id", "run_id", "base_revision",
            "kind", "payload", "evidence_refs", "created_at",
        }
        if set(raw) != fields or any(type(key) is not str for key in raw):
            raise InvalidMissionEvent(
                "MissionEvent fields do not match the canonical schema"
            )
        if not isinstance(raw.get("payload"), Mapping):
            raise InvalidMissionEvent("MissionEvent payload must be a JSON object")
        if not isinstance(raw.get("evidence_refs"), list):
            raise InvalidMissionEvent(
                "MissionEvent evidence_refs must be a JSON array"
            )
        if type(raw.get("event_id")) is not str or not raw.get("event_id"):
            raise InvalidMissionEvent(
                "MissionEvent event_id must be a non-empty string"
            )
        return cls(
            kind=raw.get("kind"),
            mission_id=raw.get("mission_id"),
            run_id=raw.get("run_id"),
            base_revision=raw.get("base_revision"),
            payload=raw.get("payload") or {},
            event_id=raw.get("event_id"),
            format=raw.get("format"),
            evidence_refs=raw.get("evidence_refs"),
            created_at=raw.get("created_at"),
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
        object.__setattr__(self, "status", ObligationStatus(self.status))
        object.__setattr__(self, "claim_evidence_refs", _as_string_tuple(self.claim_evidence_refs))
        object.__setattr__(self, "verified_evidence_refs", _as_string_tuple(self.verified_evidence_refs))
        if self.verification_id is not None:
            value = str(self.verification_id).strip()
            if not value:
                raise InvalidMissionEvent("verification_id cannot be empty")
            object.__setattr__(self, "verification_id", value)
        if self.claim_fingerprint is not None:
            value = str(self.claim_fingerprint).strip()
            if not value:
                raise InvalidMissionEvent("claim_fingerprint cannot be empty")
            object.__setattr__(self, "claim_fingerprint", value)
        if self.verified_claim_fingerprint is not None:
            value = str(self.verified_claim_fingerprint).strip()
            if not value:
                raise InvalidMissionEvent("verified_claim_fingerprint cannot be empty")
            object.__setattr__(self, "verified_claim_fingerprint", value)
        for name in (
            "failure_count",
            "activation_attempt_count",
            "verification_attempt_count",
            "no_progress_streak",
        ):
            value = int(getattr(self, name))
            if value < 0:
                raise InvalidMissionEvent(f"{name} cannot be negative")
            object.__setattr__(self, name, value)

    @property
    def accepted_verification_id(self) -> str | None:
        """Canonical read name for the accepted obligation verifier identity."""
        return self.verification_id

    @property
    def activation_count(self) -> int:
        """Compatibility read alias; the serialized field is explicit."""
        return self.activation_attempt_count

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
    target_rebind_after_frame_id: int | None = None
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
        if self.format != MISSION_PROJECTION_FORMAT:
            raise InvalidMissionEvent(f"unsupported MissionProjection format: {self.format!r}")
        if int(self.revision) < 0:
            raise InvalidMissionEvent("projection revision cannot be negative")
        if (
            self.target_rebind_after_frame_id is not None
            and (
                type(self.target_rebind_after_frame_id) is not int
                or self.target_rebind_after_frame_id <= 0
            )
        ):
            raise InvalidMissionEvent(
                "target_rebind_after_frame_id must be a positive integer or null"
            )
        if (
            self.target_binding is not None
            and self.target_rebind_after_frame_id is not None
        ):
            raise InvalidMissionEvent(
                "bound target cannot also require a newer rebind frame"
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
        object.__setattr__(self, "status", MissionStatus(self.status))
        if self.mission_verification_id is not None:
            value = str(self.mission_verification_id).strip()
            if not value:
                raise InvalidMissionEvent("mission_verification_id cannot be empty")
            object.__setattr__(self, "mission_verification_id", value)
        if self.pending_approval is not None:
            object.__setattr__(self, "pending_approval", _json_mapping(self.pending_approval))

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

    @property
    def obligations(self) -> tuple[ObligationSpec, ...]:
        """Compatibility view of immutable definitions, never mutable status."""
        return self.definition.obligations

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
            "target_rebind_after_frame_id": self.target_rebind_after_frame_id,
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


def _assert_acyclic(obligations: tuple[ObligationSpec, ...]) -> None:
    graph = {item.id: set(item.depends_on) for item in obligations}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise InvalidMissionDefinition("obligation dependency graph contains a cycle")
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


def _stable_topological_order(
    obligations: tuple[ObligationSpec, ...],
) -> tuple[str, ...]:
    """Return a deterministic dependency-first order.

    Declaration order is the tie breaker.  This is a pure serialization aid;
    it does not encode GUI steps or choose a route for the actor.
    """
    graph = {item.id: item.depends_on for item in obligations}
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visited:
            return
        for dependency in graph[node]:
            visit(dependency)
        visited.add(node)
        ordered.append(node)

    for node in graph:
        visit(node)
    return tuple(ordered)


# The architecture documents use MissionState for the long-lived view; P1's
# implementation calls the same derived value MissionProjection.  This alias
# prevents callers from accidentally creating a second state structure.
MissionState = MissionProjection

# Compatibility import for the pre-audit name.  There is one canonical class
# and one serialized schema: ``ObligationSpec``.
ObligationDefinition = ObligationSpec


__all__ = [
    "MISSION_DEFINITION_FORMAT", "MISSION_EVENT_FORMAT", "MISSION_PROJECTION_FORMAT",
    "MISSION_CARD_FORMAT", "ACTION_EFFECT_FORMAT", "ACTION_EFFECT_COMPARATOR_VERSION",
    "ACTION_EFFECT_EVIDENCE_KIND", "ACTION_EFFECT_CHANGE_RATIO_THRESHOLD",
    "TARGET_BINDING_IDENTITY_FORMAT", "TARGET_BINDING_EVIDENCE_FORMAT",
    "TARGET_BINDING_EVIDENCE_KIND",
    "MissionError", "InvalidMissionDefinition",
    "InvalidMissionEvent", "StaleRevisionError", "DuplicateEventError",
    "EvidenceScopeError", "IllegalTransitionError", "ObligationStatus", "MissionStatus",
    "LocalVisualChange", "ActionEffectReason", "ActionEffectSummary",
    "JSONValue", "EvidencePolicy", "ObligationSpec", "ObligationDefinition", "EvidenceRef", "ArtifactRef",
    "TargetBinding", "target_binding_identity_sha256", "target_binding_id_for",
    "target_binding_evidence_payload_hash", "target_binding_attestation",
    "VerifiedFact", "TransitionSummary", "FailureSummary",
    "MissionDefinition", "MissionEvent", "ObligationProjection", "MissionProjection", "MissionState",
    "ActiveObligationCard", "MissionCard", "EvidenceScope", "thaw",
]
