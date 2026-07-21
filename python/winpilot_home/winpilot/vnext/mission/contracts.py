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


JSONValue = Any


MISSION_DEFINITION_FORMAT = "mission-definition/0"
MISSION_EVENT_FORMAT = "mission-event/0"
MISSION_PROJECTION_FORMAT = "mission-projection/0"
MISSION_CARD_FORMAT = "mission-card/0"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


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
            frame_id=(int(raw["frame_id"]) if raw.get("frame_id") is not None else None),
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


@dataclass(frozen=True, slots=True)
class TargetBinding:
    binding_id: str
    hwnd: int
    process_id: int
    executable_identity: str
    title_snapshot: str
    established_by_evidence_ref: str
    epoch: int

    def __post_init__(self) -> None:
        for name in ("binding_id", "executable_identity", "established_by_evidence_ref"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise InvalidMissionEvent(f"target {name} cannot be empty")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "title_snapshot", str(self.title_snapshot or ""))
        object.__setattr__(self, "hwnd", int(self.hwnd))
        object.__setattr__(self, "process_id", int(self.process_id))
        object.__setattr__(self, "epoch", int(self.epoch))
        if int(self.hwnd) <= 0:
            raise InvalidMissionEvent("target hwnd must be positive")
        if int(self.process_id) < 0 or int(self.epoch) < 0:
            raise InvalidMissionEvent("target process_id/epoch cannot be negative")

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
        return cls(
            binding_id=str(raw.get("binding_id") or ""),
            hwnd=int(raw.get("hwnd") or 0),
            process_id=int(raw.get("process_id") or 0),
            executable_identity=str(raw.get("executable_identity") or ""),
            title_snapshot=str(raw.get("title_snapshot") or ""),
            established_by_evidence_ref=str(raw.get("established_by_evidence_ref") or ""),
            epoch=int(raw.get("epoch") or 0),
        )


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
class TransitionSummary:
    action: str
    input_completed: bool
    visible_effect: str = ""
    evidence_refs: tuple[str, ...] = ()
    frame_id: int | None = None
    obligation_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", str(self.action or "unknown"))
        object.__setattr__(self, "obligation_id", str(self.obligation_id or "").strip())
        if not self.obligation_id:
            raise InvalidMissionEvent("transition summary must bind an obligation")
        object.__setattr__(self, "evidence_refs", _as_string_tuple(self.evidence_refs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "input_completed": self.input_completed,
            "visible_effect": self.visible_effect,
            "evidence_refs": list(self.evidence_refs),
            "frame_id": self.frame_id,
            "obligation_id": self.obligation_id,
        }


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
        format: str | None = MISSION_EVENT_FORMAT,
        evidence_refs: Iterable[str] | None = None,
        created_at: float | None = None,
        *,
        type: str | None = None,
    ) -> None:
        # ``type`` is accepted only as a legacy input alias.  The canonical
        # field and wire representation remain ``kind``.
        if type and kind and str(type) != str(kind):
            raise InvalidMissionEvent("type and kind disagree")
        resolved_kind = str(kind or type or "")
        object.__setattr__(self, "kind", resolved_kind)
        object.__setattr__(self, "mission_id", str(mission_id))
        object.__setattr__(self, "run_id", str(run_id))
        object.__setattr__(self, "base_revision", int(base_revision))
        object.__setattr__(self, "payload", payload or {})
        object.__setattr__(self, "event_id", str(event_id or ""))
        object.__setattr__(self, "format", MISSION_EVENT_FORMAT if format is None else str(format))
        object.__setattr__(self, "evidence_refs", _as_string_tuple(evidence_refs))
        object.__setattr__(self, "created_at", None if created_at is None else float(created_at))
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.format != MISSION_EVENT_FORMAT:
            raise InvalidMissionEvent(f"unsupported MissionEvent format: {self.format!r}")
        if not str(self.kind).strip():
            raise InvalidMissionEvent("event kind cannot be empty")
        if not str(self.mission_id).strip() or not str(self.run_id).strip():
            raise InvalidMissionEvent("event mission_id and run_id are required")
        if int(self.base_revision) < 0:
            raise InvalidMissionEvent("base_revision cannot be negative")
        if self.created_at is not None and not math.isfinite(float(self.created_at)):
            raise InvalidMissionEvent("created_at must be finite")
        object.__setattr__(self, "kind", str(self.kind))
        object.__setattr__(self, "mission_id", str(self.mission_id))
        object.__setattr__(self, "run_id", str(self.run_id))
        try:
            object.__setattr__(self, "payload", _json_mapping(self.payload))
        except (TypeError, ValueError) as exc:
            raise InvalidMissionEvent("payload must contain only JSONValue") from exc
        object.__setattr__(self, "evidence_refs", _as_string_tuple(self.evidence_refs))
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
    def type(self) -> str:
        """Legacy read alias; it is not a second serialized field."""
        return self.kind

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
        raw_kind = raw.get("kind")
        raw_type = raw.get("type")
        if raw_kind is not None and raw_type is not None and str(raw_kind) != str(raw_type):
            raise InvalidMissionEvent("type and kind disagree")
        return cls(
            kind=str(raw_kind or raw_type or ""),
            mission_id=str(raw.get("mission_id") or ""),
            run_id=str(raw.get("run_id") or ""),
            base_revision=int(raw.get("base_revision", raw.get("revision", 0))),
            payload=raw.get("payload") or {},
            event_id=str(raw.get("event_id") or ""),
            format=str(raw.get("format") or ""),
            evidence_refs=raw.get("evidence_refs") or (),
            created_at=(float(raw["created_at"]) if raw.get("created_at") is not None else None),
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
        for name in ("failure_count", "activation_attempt_count", "verification_attempt_count"):
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
        if self.format != MISSION_PROJECTION_FORMAT:
            raise InvalidMissionEvent(f"unsupported MissionProjection format: {self.format!r}")
        if int(self.revision) < 0:
            raise InvalidMissionEvent("projection revision cannot be negative")
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
    format: str = MISSION_CARD_FORMAT

    def __post_init__(self) -> None:
        if self.format != MISSION_CARD_FORMAT:
            raise InvalidMissionEvent(f"unsupported MissionCard format: {self.format!r}")
        object.__setattr__(self, "verified_facts", tuple(self.verified_facts))
        object.__setattr__(self, "artifact_bindings", tuple(self.artifact_bindings))

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
    "MISSION_CARD_FORMAT", "MissionError", "InvalidMissionDefinition",
    "InvalidMissionEvent", "StaleRevisionError", "DuplicateEventError",
    "EvidenceScopeError", "IllegalTransitionError", "ObligationStatus", "MissionStatus",
    "JSONValue", "EvidencePolicy", "ObligationSpec", "ObligationDefinition", "EvidenceRef", "ArtifactRef",
    "TargetBinding", "VerifiedFact", "TransitionSummary", "FailureSummary",
    "MissionDefinition", "MissionEvent", "ObligationProjection", "MissionProjection", "MissionState",
    "ActiveObligationCard", "MissionCard", "EvidenceScope", "thaw",
]
