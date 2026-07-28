"""Canonical, reasoning-free records for the vNext Runtime evidence plane."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from ..json_contract import (
    BoundedJSONError,
    MAX_SIGNED_64,
    bounded_json_copy,
    freeze_bounded_json,
)
from ..mission import ActionEffectSummary, EvidenceRef


ACTOR_DECISION_RECORD_FORMAT = "actor-decision-record/0"
ADMISSION_RECORD_FORMAT = "runtime-admission-record/0"
ACTION_EXECUTION_BINDING_FORMAT = "action-execution-binding/0"
FRAME_ARTIFACT_REFERENCE_FORMAT = "frame-artifact-reference/0"
RUNTIME_EVIDENCE_REPORT_FORMAT = "runtime-evidence-report/0"

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_ACTION = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DESKTOP_ACTIONS = frozenset(
    {"click", "double_click", "scroll", "type_text", "hotkey"}
)
_RAW_ACTIONS = _DESKTOP_ACTIONS - {"double_click"}
_RAW_FIELDS = (
    "action",
    "outcome",
    "input_completed",
    "backend_result",
    "error_kind",
)
_TRANSITION_FIELDS = (
    "format",
    "outcome",
    "action_id",
    "action",
    "input_completed",
    "visible_effect",
    "post_frame_evidence",
    "post_frame_id",
    "action_effect",
    "action_effect_evidence",
)
_MAX_URI_LENGTH = 4096


class RuntimeEvidenceContractError(ValueError):
    """One evidence-plane value is not canonical."""


class FrameArtifactRole(str, Enum):
    BEFORE = "before"
    AFTER = "after"


class RuntimeEvidenceReportStatus(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    VERIFICATION_FAILED = "verification_failed"


def _identity(value: Any, name: str) -> str:
    if type(value) is not str or not _ID.fullmatch(value):
        raise RuntimeEvidenceContractError(f"{name} is not a canonical identity")
    return value


def _action(value: Any, name: str = "action") -> str:
    if type(value) is not str or not _ACTION.fullmatch(value):
        raise RuntimeEvidenceContractError(f"{name} is not a canonical action")
    return value


def _sha256(value: Any, name: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise RuntimeEvidenceContractError(
            f"{name} must be a lowercase SHA-256 digest"
        )
    return value


def _timestamp_ns(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SIGNED_64:
        raise RuntimeEvidenceContractError(
            f"{name} must be a non-negative signed-64 integer"
        )
    return value


def _positive_integer(value: Any, name: str) -> int:
    if type(value) is not int or not 0 < value <= MAX_SIGNED_64:
        raise RuntimeEvidenceContractError(
            f"{name} must be a positive signed-64 integer"
        )
    return value


def _optional_boolean(value: Any, name: str) -> bool | None:
    if value is not None and type(value) is not bool:
        raise RuntimeEvidenceContractError(f"{name} must be boolean or null")
    return value


def _strict_mapping(raw: Any, fields: Iterable[str], name: str) -> dict[str, Any]:
    if type(raw) is not dict:
        raise RuntimeEvidenceContractError(f"{name} must be a plain JSON object")
    try:
        value = bounded_json_copy(raw)
    except (BoundedJSONError, RecursionError) as exc:
        raise RuntimeEvidenceContractError(
            f"{name} violates the bounded JSON contract: {exc}"
        ) from exc
    expected = set(fields)
    keys = set(value)
    if keys != expected:
        raise RuntimeEvidenceContractError(
            f"{name} fields mismatch: missing={sorted(expected - keys)!r}, "
            f"unknown={sorted(keys - expected)!r}"
        )
    return value


def _frozen_object(value: Any, name: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise RuntimeEvidenceContractError(f"{name} must be a plain JSON object")
    try:
        frozen = freeze_bounded_json(value)
    except (BoundedJSONError, RecursionError) as exc:
        raise RuntimeEvidenceContractError(
            f"{name} violates the bounded JSON contract: {exc}"
        ) from exc
    if type(frozen) is not MappingProxyType:
        raise RuntimeEvidenceContractError(f"{name} must be a plain JSON object")
    return frozen


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    return value


def canonical_sha256(value: Any) -> str:
    """Hash one bounded literal JSON value with the shared canonical form."""

    try:
        copied = bounded_json_copy(_thaw(value))
    except (BoundedJSONError, RecursionError) as exc:
        raise RuntimeEvidenceContractError(
            f"value violates the bounded JSON contract: {exc}"
        ) from exc
    canonical = json.dumps(
        copied,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_frame_identity(value: Any, name: str) -> Mapping[str, Any]:
    item = _strict_mapping(
        value,
        (
            "frame_id",
            "epoch",
            "hwnd",
            "region",
            "scope",
            "submitted_size",
            "image_sha256",
            "media_type",
        ),
        name,
    )
    _positive_integer(item["frame_id"], f"{name}.frame_id")
    _timestamp_ns(item["epoch"], f"{name}.epoch")
    _positive_integer(item["hwnd"], f"{name}.hwnd")
    region = item["region"]
    if (
        type(region) is not list
        or len(region) != 4
        or any(type(part) is not int for part in region)
        or region[2] <= 0
        or region[3] <= 0
    ):
        raise RuntimeEvidenceContractError(
            f"{name}.region must be four canonical integers"
        )
    size = item["submitted_size"]
    if (
        type(size) is not list
        or len(size) != 2
        or any(type(part) is not int or part <= 0 for part in size)
    ):
        raise RuntimeEvidenceContractError(
            f"{name}.submitted_size must be two positive integers"
        )
    if _identity(item["scope"], f"{name}.scope") != "target_window":
        raise RuntimeEvidenceContractError(f"{name}.scope must be target_window")
    _sha256(item["image_sha256"], f"{name}.image_sha256")
    if item["media_type"] != "image/jpeg":
        raise RuntimeEvidenceContractError(f"{name}.media_type must be image/jpeg")
    return _frozen_object(item, name)


def _validate_target_identity(value: Any, name: str) -> Mapping[str, Any]:
    item = _strict_mapping(
        value,
        ("binding_id", "hwnd", "process_id", "executable_identity", "epoch"),
        name,
    )
    _identity(item["binding_id"], f"{name}.binding_id")
    _positive_integer(item["hwnd"], f"{name}.hwnd")
    _positive_integer(item["process_id"], f"{name}.process_id")
    if type(item["executable_identity"]) is not str or not item["executable_identity"]:
        raise RuntimeEvidenceContractError(
            f"{name}.executable_identity must be non-empty"
        )
    _timestamp_ns(item["epoch"], f"{name}.epoch")
    return _frozen_object(item, name)


def _validate_backend_result(value: Any, expected: bool | None) -> None:
    backend = _strict_mapping(
        value, ("input_completed", "facts"), "raw receipt backend_result"
    )
    if _optional_boolean(
        backend["input_completed"], "backend_result.input_completed"
    ) is not expected:
        raise RuntimeEvidenceContractError(
            "backend_result contradicts raw receipt outcome"
        )
    facts = backend["facts"]
    if type(facts) is not dict or any(
        type(key) is not str
        or not key
        or (
            fact is not None
            and type(fact) not in {bool, int, float, str}
        )
        or (type(fact) is float and not math.isfinite(fact))
        for key, fact in facts.items()
    ):
        raise RuntimeEvidenceContractError(
            "backend_result.facts must contain JSON scalars"
        )


def canonical_raw_action_receipt(value: Any) -> Mapping[str, Any]:
    """Validate and freeze an exact RawActionReceipt wire snapshot."""

    item = _strict_mapping(_thaw(value), _RAW_FIELDS, "raw_action_receipt")
    action = _action(item["action"], "raw_action_receipt.action")
    if action not in _RAW_ACTIONS:
        raise RuntimeEvidenceContractError("raw receipt action is not admitted")
    outcome = item["outcome"]
    completion = {
        "applied": True,
        "not_applied": False,
        "not_started": False,
        "outcome_unknown": None,
    }
    if outcome not in completion:
        raise RuntimeEvidenceContractError("raw receipt outcome is not canonical")
    expected = completion[outcome]
    if _optional_boolean(
        item["input_completed"], "raw_action_receipt.input_completed"
    ) is not expected:
        raise RuntimeEvidenceContractError(
            "raw receipt outcome contradicts input_completed"
        )
    backend = item["backend_result"]
    error = item["error_kind"]
    if outcome in {"applied", "not_applied"}:
        _validate_backend_result(backend, expected)
        if error is not None:
            raise RuntimeEvidenceContractError(
                "certain raw receipt cannot contain error_kind"
            )
    else:
        if outcome == "not_started" and backend is not None:
            raise RuntimeEvidenceContractError(
                "not-started raw receipt cannot contain backend_result"
            )
        if backend is not None:
            _validate_backend_result(backend, expected)
        if type(error) is not str or not error:
            raise RuntimeEvidenceContractError(
                "uncertain/not-started receipt requires error_kind"
            )
    return _frozen_object(item, "raw_action_receipt")


def _validate_evidence_ref(value: Any, name: str) -> dict[str, Any]:
    try:
        return EvidenceRef.from_dict(value).to_dict()
    except Exception as exc:
        raise RuntimeEvidenceContractError(
            f"{name} is not a canonical EvidenceRef"
        ) from exc


def canonical_controller_transition(value: Any) -> Mapping[str, Any]:
    """Validate and freeze an exact ControllerTransition wire snapshot."""

    item = _strict_mapping(_thaw(value), _TRANSITION_FIELDS, "controller_transition")
    if item["format"] != "controller-transition/0":
        raise RuntimeEvidenceContractError("transition format is not canonical")
    action_id = _identity(item["action_id"], "controller_transition.action_id")
    action = _action(item["action"], "controller_transition.action")
    if action not in _DESKTOP_ACTIONS:
        raise RuntimeEvidenceContractError("transition action is not admitted")
    outcome = item["outcome"]
    if outcome not in {"completed", "rejected", "unknown"}:
        raise RuntimeEvidenceContractError("transition outcome is not canonical")
    completed = _optional_boolean(
        item["input_completed"], "controller_transition.input_completed"
    )
    if type(item["visible_effect"]) is not str:
        raise RuntimeEvidenceContractError("visible_effect must be a string")
    post_id = item["post_frame_id"]
    if post_id is not None:
        _positive_integer(post_id, "controller_transition.post_frame_id")
    evidence = item["post_frame_evidence"]
    if type(evidence) is not list:
        raise RuntimeEvidenceContractError("post_frame_evidence must be an array")
    refs = [
        _validate_evidence_ref(ref, "post_frame_evidence[]") for ref in evidence
    ]
    ids = [ref["id"] for ref in refs]
    if ids != sorted(set(ids)):
        raise RuntimeEvidenceContractError(
            "post_frame_evidence ids must be unique and sorted"
        )
    if any(
        ref["action_id"] != action_id
        or ref["frame_id"] != post_id
        or ref["kind"] != "post_action_frame"
        or ref["trust"] != "deterministic"
        for ref in refs
    ):
        raise RuntimeEvidenceContractError(
            "post_frame_evidence changed action/frame identity"
        )
    if outcome == "completed" and (
        completed is not True or post_id is None or not refs
    ):
        raise RuntimeEvidenceContractError(
            "completed transition requires input, post frame, and evidence"
        )
    if outcome == "rejected" and (
        completed is not False or (post_id is None) != (not refs)
    ):
        raise RuntimeEvidenceContractError(
            "rejected transition fields are contradictory"
        )
    effect = item["action_effect"]
    effect_ref = item["action_effect_evidence"]
    if (effect is None) != (effect_ref is None):
        raise RuntimeEvidenceContractError(
            "action_effect and action_effect_evidence must be paired"
        )
    if effect is not None:
        if type(effect) is not dict or type(effect_ref) is not dict:
            raise RuntimeEvidenceContractError("action effect must be an object")
        try:
            summary = ActionEffectSummary.from_dict(effect)
            canonical_effect_ref = EvidenceRef.from_dict(effect_ref)
        except Exception as exc:
            raise RuntimeEvidenceContractError(
                "action effect is not canonical"
            ) from exc
        if outcome != "completed" or (
            summary.action_id != action_id
            or summary.after_frame_id != post_id
            or canonical_effect_ref.id != summary.evidence_ref
            or canonical_effect_ref.payload_hash != summary.payload_hash
            or canonical_effect_ref.kind != "deterministic_action_effect"
            or canonical_effect_ref.trust != "deterministic"
        ):
            raise RuntimeEvidenceContractError(
                "action effect changed transition identity"
            )
        item["action_effect"] = summary.to_dict()
        item["action_effect_evidence"] = canonical_effect_ref.to_dict()
    return _frozen_object(item, "controller_transition")


@dataclass(frozen=True, slots=True)
class ActorDecisionRecord:
    decision_id: str
    mission_id: str
    obligation_id: str
    proposal_kind: str
    action: str
    canonical_arguments_sha256: str
    tool_schema_sha256: str
    broker_projection_sha256: str
    format: str = ACTOR_DECISION_RECORD_FORMAT

    def __post_init__(self) -> None:
        if self.format != ACTOR_DECISION_RECORD_FORMAT:
            raise RuntimeEvidenceContractError("unsupported ActorDecisionRecord format")
        for name in ("decision_id", "mission_id", "obligation_id"):
            object.__setattr__(self, name, _identity(getattr(self, name), name))
        if self.proposal_kind != "desktop_action":
            raise RuntimeEvidenceContractError(
                "evidence plane accepts only desktop_action decisions"
            )
        if _action(self.action) not in _DESKTOP_ACTIONS:
            raise RuntimeEvidenceContractError("decision action is not admitted")
        for name in (
            "canonical_arguments_sha256",
            "tool_schema_sha256",
            "broker_projection_sha256",
        ):
            object.__setattr__(self, name, _sha256(getattr(self, name), name))

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "decision_id": self.decision_id,
            "mission_id": self.mission_id,
            "obligation_id": self.obligation_id,
            "proposal_kind": self.proposal_kind,
            "action": self.action,
            "canonical_arguments_sha256": self.canonical_arguments_sha256,
            "tool_schema_sha256": self.tool_schema_sha256,
            "broker_projection_sha256": self.broker_projection_sha256,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ActorDecisionRecord":
        return cls(**_strict_mapping(raw, cls.__dataclass_fields__, cls.__name__))


@dataclass(frozen=True, slots=True)
class AdmissionRecord:
    decision_id: str
    frame_identity: Mapping[str, Any]
    target_identity: Mapping[str, Any]
    mutation_permit_id: str
    controller_route: str
    accepted_timestamp_ns: int
    format: str = ADMISSION_RECORD_FORMAT

    def __post_init__(self) -> None:
        if self.format != ADMISSION_RECORD_FORMAT:
            raise RuntimeEvidenceContractError("unsupported AdmissionRecord format")
        object.__setattr__(
            self, "decision_id", _identity(self.decision_id, "decision_id")
        )
        frame = _validate_frame_identity(_thaw(self.frame_identity), "frame_identity")
        target = _validate_target_identity(
            _thaw(self.target_identity), "target_identity"
        )
        if frame["hwnd"] != target["hwnd"] or frame["epoch"] != target["epoch"]:
            raise RuntimeEvidenceContractError(
                "admission frame and target identities disagree"
            )
        object.__setattr__(self, "frame_identity", frame)
        object.__setattr__(self, "target_identity", target)
        object.__setattr__(
            self,
            "mutation_permit_id",
            _identity(self.mutation_permit_id, "mutation_permit_id"),
        )
        if self.controller_route != "vnext_atomic_desktop_adapter":
            raise RuntimeEvidenceContractError("controller_route is not canonical")
        object.__setattr__(
            self,
            "accepted_timestamp_ns",
            _timestamp_ns(self.accepted_timestamp_ns, "accepted_timestamp_ns"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "decision_id": self.decision_id,
            "frame_identity": _thaw(self.frame_identity),
            "target_identity": _thaw(self.target_identity),
            "mutation_permit_id": self.mutation_permit_id,
            "controller_route": self.controller_route,
            "accepted_timestamp_ns": self.accepted_timestamp_ns,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "AdmissionRecord":
        return cls(**_strict_mapping(raw, cls.__dataclass_fields__, cls.__name__))


@dataclass(frozen=True, slots=True)
class ActionExecutionBinding:
    action_id: str
    decision_id: str
    action: str
    actor_envelope_sha256: str
    raw_action_receipt: Mapping[str, Any]
    raw_action_receipt_sha256: str
    controller_transition: Mapping[str, Any]
    controller_transition_sha256: str
    physical_attempt_count: int
    attempt_reservation_sha256: str | None
    before_frame_identity: Mapping[str, Any]
    after_frame_identity: Mapping[str, Any] | None = None
    before_artifact_reference_sha256: str | None = None
    after_artifact_reference_sha256: str | None = None
    format: str = ACTION_EXECUTION_BINDING_FORMAT

    def __post_init__(self) -> None:
        if self.format != ACTION_EXECUTION_BINDING_FORMAT:
            raise RuntimeEvidenceContractError(
                "unsupported ActionExecutionBinding format"
            )
        action_id = _identity(self.action_id, "action_id")
        decision_id = _identity(self.decision_id, "decision_id")
        if action_id != decision_id:
            raise RuntimeEvidenceContractError("action_id must equal decision_id")
        action = _action(self.action)
        if action not in _DESKTOP_ACTIONS:
            raise RuntimeEvidenceContractError("binding action is not admitted")
        raw = canonical_raw_action_receipt(self.raw_action_receipt)
        transition = canonical_controller_transition(self.controller_transition)
        if _sha256(
            self.raw_action_receipt_sha256, "raw_action_receipt_sha256"
        ) != canonical_sha256(_thaw(raw)):
            raise RuntimeEvidenceContractError("raw receipt hash is not canonical")
        if _sha256(
            self.controller_transition_sha256, "controller_transition_sha256"
        ) != canonical_sha256(_thaw(transition)):
            raise RuntimeEvidenceContractError("transition hash is not canonical")
        expected_raw = "click" if action == "double_click" else action
        if (
            raw["action"] != expected_raw
            or transition["action"] != action
            or transition["action_id"] != action_id
            or raw["input_completed"] is not transition["input_completed"]
        ):
            raise RuntimeEvidenceContractError(
                "envelope, receipt, and transition facts disagree"
            )
        if type(self.physical_attempt_count) is not int or not (
            0 <= self.physical_attempt_count <= 1
        ):
            raise RuntimeEvidenceContractError(
                "physical_attempt_count must be zero or one"
            )
        if (
            self.physical_attempt_count == 0
            and raw["outcome"] != "not_started"
        ):
            raise RuntimeEvidenceContractError(
                "unreserved input cannot claim a physical attempt result"
            )
        if (self.physical_attempt_count == 1) != (
            self.attempt_reservation_sha256 is not None
        ):
            raise RuntimeEvidenceContractError(
                "physical attempt and reservation hash must be paired"
            )
        if self.attempt_reservation_sha256 is not None:
            object.__setattr__(
                self,
                "attempt_reservation_sha256",
                _sha256(
                    self.attempt_reservation_sha256,
                    "attempt_reservation_sha256",
                ),
            )
        before = _validate_frame_identity(
            _thaw(self.before_frame_identity), "before_frame_identity"
        )
        after = (
            None
            if self.after_frame_identity is None
            else _validate_frame_identity(
                _thaw(self.after_frame_identity), "after_frame_identity"
            )
        )
        post_id = transition["post_frame_id"]
        if after is not None and (
            after["frame_id"] != post_id
            or after["frame_id"] != before["frame_id"] + 1
            or any(
                after[field] != before[field]
                for field in (
                    "epoch",
                    "hwnd",
                    "region",
                    "scope",
                    "submitted_size",
                    "media_type",
                )
            )
        ):
            raise RuntimeEvidenceContractError(
                "before and after frame identities are not one target commit"
            )
        if transition["outcome"] == "completed" and (
            self.physical_attempt_count != 1
            or raw["outcome"] != "applied"
            or after is None
        ):
            raise RuntimeEvidenceContractError(
                "completed binding lacks one proven attempt and after frame"
            )
        if after is not None:
            after_payload_hash = canonical_sha256(_thaw(after))
            if any(
                ref["payload_hash"] != after_payload_hash
                for ref in transition["post_frame_evidence"]
            ):
                raise RuntimeEvidenceContractError(
                    "post-frame evidence payload hash changed"
                )
            effect = transition["action_effect"]
            if effect is not None and (
                effect["before_frame_id"] != before["frame_id"]
                or effect["before_frame_payload_hash"]
                != canonical_sha256(_thaw(before))
                or effect["after_frame_payload_hash"]
                != after_payload_hash
            ):
                raise RuntimeEvidenceContractError(
                    "action effect changed bound frame identity"
                )
        if post_id is None and after is not None:
            raise RuntimeEvidenceContractError(
                "after frame identity requires transition post_frame_id"
            )
        object.__setattr__(self, "action_id", action_id)
        object.__setattr__(self, "decision_id", decision_id)
        object.__setattr__(self, "action", action)
        object.__setattr__(
            self,
            "actor_envelope_sha256",
            _sha256(self.actor_envelope_sha256, "actor_envelope_sha256"),
        )
        object.__setattr__(self, "raw_action_receipt", raw)
        object.__setattr__(self, "controller_transition", transition)
        object.__setattr__(self, "before_frame_identity", before)
        object.__setattr__(self, "after_frame_identity", after)
        for name in (
            "before_artifact_reference_sha256",
            "after_artifact_reference_sha256",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _sha256(value, name))

    @property
    def before_frame_id(self) -> int:
        return int(self.before_frame_identity["frame_id"])

    @property
    def after_frame_id(self) -> int | None:
        return (
            None
            if self.after_frame_identity is None
            else int(self.after_frame_identity["frame_id"])
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "action_id": self.action_id,
            "decision_id": self.decision_id,
            "action": self.action,
            "actor_envelope_sha256": self.actor_envelope_sha256,
            "raw_action_receipt": _thaw(self.raw_action_receipt),
            "raw_action_receipt_sha256": self.raw_action_receipt_sha256,
            "controller_transition": _thaw(self.controller_transition),
            "controller_transition_sha256": self.controller_transition_sha256,
            "physical_attempt_count": self.physical_attempt_count,
            "attempt_reservation_sha256": (
                self.attempt_reservation_sha256
            ),
            "before_frame_identity": _thaw(self.before_frame_identity),
            "after_frame_identity": _thaw(self.after_frame_identity),
            "before_artifact_reference_sha256": (
                self.before_artifact_reference_sha256
            ),
            "after_artifact_reference_sha256": (
                self.after_artifact_reference_sha256
            ),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ActionExecutionBinding":
        return cls(**_strict_mapping(raw, cls.__dataclass_fields__, cls.__name__))


@dataclass(frozen=True, slots=True)
class FrameArtifactReference:
    frame_id: int
    artifact_uri: str
    sha256: str
    capture_timestamp_ns: int
    role: FrameArtifactRole | str
    format: str = FRAME_ARTIFACT_REFERENCE_FORMAT

    def __post_init__(self) -> None:
        if self.format != FRAME_ARTIFACT_REFERENCE_FORMAT:
            raise RuntimeEvidenceContractError(
                "unsupported FrameArtifactReference format"
            )
        object.__setattr__(
            self, "frame_id", _positive_integer(self.frame_id, "frame_id")
        )
        uri = self.artifact_uri
        if (
            type(uri) is not str
            or not uri.strip()
            or len(uri) > _MAX_URI_LENGTH
            or uri.lstrip().lower().startswith("data:")
        ):
            raise RuntimeEvidenceContractError(
                "artifact_uri must be a bounded external URI or path"
            )
        object.__setattr__(self, "sha256", _sha256(self.sha256, "sha256"))
        object.__setattr__(
            self,
            "capture_timestamp_ns",
            _timestamp_ns(self.capture_timestamp_ns, "capture_timestamp_ns"),
        )
        try:
            object.__setattr__(self, "role", FrameArtifactRole(self.role))
        except Exception as exc:
            raise RuntimeEvidenceContractError("role must be before or after") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "frame_id": self.frame_id,
            "artifact_uri": self.artifact_uri,
            "sha256": self.sha256,
            "capture_timestamp_ns": self.capture_timestamp_ns,
            "role": self.role.value,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "FrameArtifactReference":
        return cls(**_strict_mapping(raw, cls.__dataclass_fields__, cls.__name__))


def _record_tuple(
    values: Iterable[Any], expected_type: type, name: str
) -> tuple[Any, ...]:
    if isinstance(values, (str, bytes)):
        raise RuntimeEvidenceContractError(f"{name} must be an array")
    result = tuple(values)
    if any(type(item) is not expected_type for item in result):
        raise RuntimeEvidenceContractError(
            f"{name} contains a non-canonical record"
        )
    return result


def _unique_strings(values: Iterable[str], name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise RuntimeEvidenceContractError(f"{name} must be an array")
    result = tuple(_identity(value, f"{name}[]") for value in values)
    if result != tuple(sorted(set(result))):
        raise RuntimeEvidenceContractError(f"{name} must be unique and sorted")
    return result


@dataclass(frozen=True, slots=True)
class RuntimeEvidenceReport:
    mission_id: str
    run_id: str
    status: RuntimeEvidenceReportStatus | str
    actor_decisions: tuple[ActorDecisionRecord, ...]
    admissions: tuple[AdmissionRecord, ...]
    action_bindings: tuple[ActionExecutionBinding, ...]
    frame_artifacts: tuple[FrameArtifactReference, ...]
    missing_evidence: tuple[str, ...] = ()
    verification_failures: tuple[str, ...] = ()
    format: str = RUNTIME_EVIDENCE_REPORT_FORMAT

    def __post_init__(self) -> None:
        if self.format != RUNTIME_EVIDENCE_REPORT_FORMAT:
            raise RuntimeEvidenceContractError(
                "unsupported RuntimeEvidenceReport format"
            )
        mission_id = _identity(self.mission_id, "mission_id")
        object.__setattr__(self, "mission_id", mission_id)
        object.__setattr__(self, "run_id", _identity(self.run_id, "run_id"))
        try:
            claimed_status = RuntimeEvidenceReportStatus(self.status)
        except Exception as exc:
            raise RuntimeEvidenceContractError(
                "invalid Runtime evidence report status"
            ) from exc
        decisions = _record_tuple(
            self.actor_decisions, ActorDecisionRecord, "actor_decisions"
        )
        admissions = _record_tuple(
            self.admissions, AdmissionRecord, "admissions"
        )
        bindings = _record_tuple(
            self.action_bindings, ActionExecutionBinding, "action_bindings"
        )
        artifacts = _record_tuple(
            self.frame_artifacts, FrameArtifactReference, "frame_artifacts"
        )
        for values, key, name in (
            (decisions, lambda item: item.decision_id, "actor_decisions"),
            (admissions, lambda item: item.decision_id, "admissions"),
            (bindings, lambda item: item.action_id, "action_bindings"),
            (
                artifacts,
                lambda item: (item.frame_id, item.role.value),
                "frame_artifacts",
            ),
        ):
            keys = [key(item) for item in values]
            if keys != sorted(set(keys)):
                raise RuntimeEvidenceContractError(
                    f"{name} must be unique and sorted"
                )
        decision_by_id = {item.decision_id: item for item in decisions}
        admission_by_id = {item.decision_id: item for item in admissions}
        binding_by_id = {item.decision_id: item for item in bindings}
        artifact_by_key = {
            (item.role, item.frame_id): item for item in artifacts
        }
        if any(item.mission_id != mission_id for item in decisions):
            raise RuntimeEvidenceContractError(
                "report contains a decision from another mission"
            )
        if not set(admission_by_id).issubset(decision_by_id) or not set(
            binding_by_id
        ).issubset(decision_by_id):
            raise RuntimeEvidenceContractError(
                "report contains orphan admission/action binding"
            )
        missing = set(_unique_strings(self.missing_evidence, "missing_evidence"))
        failures = set(
            _unique_strings(
                self.verification_failures, "verification_failures"
            )
        )
        if not decisions:
            missing.add("actor_decision")
        used_artifacts: set[tuple[FrameArtifactRole, int]] = set()
        for decision_id, decision in decision_by_id.items():
            admission = admission_by_id.get(decision_id)
            binding = binding_by_id.get(decision_id)
            if admission is None:
                missing.add(f"{decision_id}_admission")
            if binding is None:
                missing.add(f"{decision_id}_action_binding")
                continue
            if binding.action != decision.action:
                raise RuntimeEvidenceContractError(
                    "decision and action binding disagree"
                )
            refs = list(
                binding.controller_transition["post_frame_evidence"]
            )
            effect_ref = binding.controller_transition[
                "action_effect_evidence"
            ]
            if effect_ref is not None:
                refs.append(effect_ref)
            if any(
                ref["mission_id"] != mission_id
                or ref["run_id"] != self.run_id
                or ref["obligation_id"] != decision.obligation_id
                or ref["action_id"] != decision_id
                for ref in refs
            ):
                raise RuntimeEvidenceContractError(
                    "transition evidence is outside report scope"
                )
            if admission is None:
                continue
            if binding.before_frame_identity != admission.frame_identity:
                raise RuntimeEvidenceContractError(
                    "admission and before frame identity disagree"
                )
            expected_reservation = canonical_sha256(
                {
                    "action_id": decision_id,
                    "actor_envelope_sha256": (
                        binding.actor_envelope_sha256
                    ),
                    "admission_sha256": canonical_sha256(
                        admission.to_dict()
                    ),
                }
            )
            if (
                binding.physical_attempt_count == 1
                and binding.attempt_reservation_sha256
                != expected_reservation
            ):
                raise RuntimeEvidenceContractError(
                    "physical attempt reservation binding changed"
                )
            for role, frame_id, ref_hash in (
                (
                    FrameArtifactRole.BEFORE,
                    binding.before_frame_id,
                    binding.before_artifact_reference_sha256,
                ),
                (
                    FrameArtifactRole.AFTER,
                    binding.after_frame_id,
                    binding.after_artifact_reference_sha256,
                ),
            ):
                token = f"{decision_id}_{role.value}_frame"
                if frame_id is None:
                    missing.add(f"{token}_identity")
                    continue
                reference = artifact_by_key.get((role, frame_id))
                if reference is None:
                    missing.add(f"{token}_artifact")
                    continue
                used_artifacts.add((role, frame_id))
                expected_ref_hash = canonical_sha256(reference.to_dict())
                if ref_hash is None:
                    missing.add(f"{token}_artifact_binding")
                elif ref_hash != expected_ref_hash:
                    failures.add(f"{token}_artifact_reference_mismatch")
                if (
                    role is FrameArtifactRole.BEFORE
                    and reference.capture_timestamp_ns
                    > admission.accepted_timestamp_ns
                ) or (
                    role is FrameArtifactRole.AFTER
                    and reference.capture_timestamp_ns
                    < admission.accepted_timestamp_ns
                ):
                    failures.add(f"{token}_capture_order_mismatch")
        if set(artifact_by_key) != used_artifacts:
            raise RuntimeEvidenceContractError(
                "report contains an orphan frame artifact"
            )
        missing_items = tuple(sorted(missing))
        failure_items = tuple(sorted(failures))
        derived_status = (
            RuntimeEvidenceReportStatus.INCOMPLETE
            if missing_items
            else RuntimeEvidenceReportStatus.VERIFICATION_FAILED
            if failure_items
            else RuntimeEvidenceReportStatus.COMPLETE
        )
        if claimed_status is not derived_status:
            raise RuntimeEvidenceContractError(
                "report status contradicts independently derived completeness"
            )
        object.__setattr__(self, "status", derived_status)
        object.__setattr__(self, "actor_decisions", decisions)
        object.__setattr__(self, "admissions", admissions)
        object.__setattr__(self, "action_bindings", bindings)
        object.__setattr__(self, "frame_artifacts", artifacts)
        object.__setattr__(self, "missing_evidence", missing_items)
        object.__setattr__(self, "verification_failures", failure_items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "mission_id": self.mission_id,
            "run_id": self.run_id,
            "status": self.status.value,
            "actor_decisions": [item.to_dict() for item in self.actor_decisions],
            "admissions": [item.to_dict() for item in self.admissions],
            "action_bindings": [item.to_dict() for item in self.action_bindings],
            "frame_artifacts": [item.to_dict() for item in self.frame_artifacts],
            "missing_evidence": list(self.missing_evidence),
            "verification_failures": list(self.verification_failures),
        }

    @classmethod
    def from_dict(
        cls,
        raw: Any,
    ) -> "RuntimeEvidenceReport":
        value = _strict_mapping(raw, cls.__dataclass_fields__, cls.__name__)
        for name, record_type in (
            ("actor_decisions", ActorDecisionRecord),
            ("admissions", AdmissionRecord),
            ("action_bindings", ActionExecutionBinding),
            ("frame_artifacts", FrameArtifactReference),
        ):
            if type(value[name]) is not list:
                raise RuntimeEvidenceContractError(f"{name} must be an array")
            value[name] = tuple(record_type.from_dict(item) for item in value[name])
        for name in ("missing_evidence", "verification_failures"):
            if type(value[name]) is not list:
                raise RuntimeEvidenceContractError(f"{name} must be an array")
            value[name] = tuple(value[name])
        if value["status"] == RuntimeEvidenceReportStatus.COMPLETE.value:
            raise RuntimeEvidenceContractError(
                "serialized complete reports cannot self-authenticate"
            )
        return cls(**value)


__all__ = [
    "ACTION_EXECUTION_BINDING_FORMAT",
    "ACTOR_DECISION_RECORD_FORMAT",
    "ADMISSION_RECORD_FORMAT",
    "FRAME_ARTIFACT_REFERENCE_FORMAT",
    "RUNTIME_EVIDENCE_REPORT_FORMAT",
    "ActionExecutionBinding",
    "ActorDecisionRecord",
    "AdmissionRecord",
    "FrameArtifactReference",
    "FrameArtifactRole",
    "RuntimeEvidenceContractError",
    "RuntimeEvidenceReport",
    "RuntimeEvidenceReportStatus",
    "canonical_controller_transition",
    "canonical_raw_action_receipt",
    "canonical_sha256",
]
