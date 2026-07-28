"""Canonical, reasoning-free records for the vNext Runtime evidence plane."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from ..capability_names import canonical_capability
from ..json_contract import (
    BoundedJSONError,
    MAX_SIGNED_64,
    bounded_json_copy,
    freeze_bounded_json,
)
from ..mission import ActionEffectSummary, EvidenceRef


ACTOR_DECISION_RECORD_FORMAT = "actor-decision-record/1"
ADMISSION_RECORD_FORMAT = "runtime-admission-record/1"
PHYSICAL_ATTEMPT_RECORD_FORMAT = "physical-attempt-record/1"
ACTION_EXECUTION_BINDING_FORMAT = "action-execution-binding/1"
FRAME_ARTIFACT_REFERENCE_FORMAT = "frame-artifact-reference/1"
RUNTIME_EVIDENCE_REPORT_FORMAT = "runtime-evidence-report/1"

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_ACTION = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DESKTOP_ACTIONS = frozenset(
    {"click", "double_click", "scroll", "type_text", "hotkey"}
)
_RUNTIME_ACTOR_TOOLS = _DESKTOP_ACTIONS | {
    "claim_obligation_done",
    "blocked",
}
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
_ACTOR_ENVELOPE_FIELDS = (
    "format",
    "decision_id",
    "mission_id",
    "run_id",
    "mission_revision",
    "mission_projection_sha256",
    "accepted_mission_event_ids",
    "obligation_id",
    "turn_index",
    "catalog_fingerprint",
    "profile_id",
    "state_fingerprint",
    "frame_id",
    "frame_epoch",
    "frame_hwnd",
    "frame_scope",
    "frame_region",
    "submitted_image_size",
    "frame_image_sha256",
    "target_binding_id",
    "pointer_disposition",
    "pointer_lease_id",
    "broker_projection_id",
    "capability_grant_ids",
    "allowed_evidence_scope",
    "proposal",
)
_DESKTOP_PROPOSAL_FIELDS = (
    "format",
    "kind",
    "action",
    "args",
    "local_intent",
)
_BROKER_PROJECTION_FIELDS = (
    "format",
    "projection_id",
    "mission_id",
    "run_id",
    "catalog_fingerprint",
    "profile_id",
    "state_fingerprint",
    "mission_revision",
    "obligation_id",
    "turn_index",
    "execution_route",
    "executor_id",
    "target_binding_id",
    "frame_id",
    "frame_scope",
    "pointer_lease_id",
    "tool_names",
    "active_grants",
    "missing_capabilities",
    "projection_reasons",
)
_CAPABILITY_GRANT_FIELDS = (
    "format",
    "grant_id",
    "mission_id",
    "run_id",
    "capability",
    "obligation_id",
    "mission_revision",
    "issued_turn",
    "expires_after_turns",
    "policy_reason",
)
_NON_EXECUTION_RECORD_FIELDS = frozenset(
    {
        "prompt",
        "prompts",
        "system_prompt",
        "user_prompt",
        "reasoning",
        "chain_of_thought",
        "cot",
        "model_response",
        "raw_model_response",
        "provider_response",
        "raw_provider_response",
        "provider_internal",
        "provider_metadata",
        "hidden_fields",
    }
)


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


def _nonnegative_integer(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SIGNED_64:
        raise RuntimeEvidenceContractError(
            f"{name} must be a non-negative signed-64 integer"
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


def _reject_non_execution_fields(value: Any, name: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str:
                raise RuntimeEvidenceContractError(
                    f"{name} contains a non-string field"
                )
            normalized = key.strip().lower().replace("-", "_")
            if normalized in _NON_EXECUTION_RECORD_FIELDS:
                raise RuntimeEvidenceContractError(
                    f"{name} contains forbidden non-execution field {key!r}"
                )
            _reject_non_execution_fields(item, f"{name}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_non_execution_fields(item, f"{name}[{index}]")


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


def _identity_array(value: Any, name: str, *, ordered: bool = False) -> list[str]:
    if type(value) is not list:
        raise RuntimeEvidenceContractError(f"{name} must be an array")
    result = [_identity(item, f"{name}[]") for item in value]
    if len(result) != len(set(result)):
        raise RuntimeEvidenceContractError(f"{name} must not contain duplicates")
    if not ordered and result != sorted(result):
        raise RuntimeEvidenceContractError(f"{name} must be sorted")
    return result


def _validate_submitted_arguments(
    action: str,
    value: Any,
    name: str = "canonical_arguments",
) -> Mapping[str, Any]:
    fields = {
        "click": ("image_x", "image_y"),
        "double_click": ("image_x", "image_y"),
        "scroll": ("image_x", "image_y", "amount"),
        "type_text": ("text",),
        "hotkey": ("keys",),
    }[action]
    item = _strict_mapping(value, fields, name)
    if action in {"click", "double_click", "scroll"}:
        for coordinate in ("image_x", "image_y"):
            number = item[coordinate]
            if (
                type(number) not in {int, float}
                or not math.isfinite(float(number))
                or float(number) < 0
            ):
                raise RuntimeEvidenceContractError(
                    f"{name}.{coordinate} must be a finite submitted-image coordinate"
                )
    if action == "scroll":
        amount = item["amount"]
        if type(amount) is not int or amount == 0 or abs(amount) > 1200:
            raise RuntimeEvidenceContractError(
                f"{name}.amount must be a non-zero integer in -1200..1200"
            )
    elif action == "type_text":
        text = item["text"]
        if type(text) is not str or not 1 <= len(text) <= 4096:
            raise RuntimeEvidenceContractError(
                f"{name}.text must be a bounded non-empty string"
            )
    elif action == "hotkey":
        keys = item["keys"]
        if (
            type(keys) is not list
            or not 1 <= len(keys) <= 5
            or any(type(key) is not str or not key for key in keys)
            or len(keys) != len(set(keys))
        ):
            raise RuntimeEvidenceContractError(
                f"{name}.keys must be one canonical hotkey chord"
            )
    return _frozen_object(item, name)


def _validate_actor_envelope(
    value: Any,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    envelope = _strict_mapping(
        value,
        _ACTOR_ENVELOPE_FIELDS,
        "actor_envelope",
    )
    if envelope["format"] != "actor-envelope/1":
        raise RuntimeEvidenceContractError("actor_envelope format is not canonical")
    for name in (
        "decision_id",
        "mission_id",
        "run_id",
        "obligation_id",
        "profile_id",
        "target_binding_id",
        "broker_projection_id",
    ):
        _identity(envelope[name], f"actor_envelope.{name}")
    _nonnegative_integer(
        envelope["mission_revision"],
        "actor_envelope.mission_revision",
    )
    _nonnegative_integer(envelope["turn_index"], "actor_envelope.turn_index")
    for name in (
        "mission_projection_sha256",
        "catalog_fingerprint",
        "state_fingerprint",
        "frame_image_sha256",
    ):
        _sha256(envelope[name], f"actor_envelope.{name}")
    _identity_array(
        envelope["accepted_mission_event_ids"],
        "actor_envelope.accepted_mission_event_ids",
        ordered=True,
    )
    grant_ids = _identity_array(
        envelope["capability_grant_ids"],
        "actor_envelope.capability_grant_ids",
    )
    _identity_array(
        envelope["allowed_evidence_scope"],
        "actor_envelope.allowed_evidence_scope",
    )
    if (
        envelope["frame_scope"] != "target_window"
        or envelope["pointer_disposition"] != "released"
        or envelope["pointer_lease_id"] is not None
    ):
        raise RuntimeEvidenceContractError(
            "actor_envelope changed the bounded live-frame contract"
        )
    _validate_frame_identity(
        {
            "frame_id": envelope["frame_id"],
            "epoch": envelope["frame_epoch"],
            "hwnd": envelope["frame_hwnd"],
            "region": envelope["frame_region"],
            "scope": envelope["frame_scope"],
            "submitted_size": envelope["submitted_image_size"],
            "image_sha256": envelope["frame_image_sha256"],
            "media_type": "image/jpeg",
        },
        "actor_envelope.frame_identity",
    )
    proposal = _strict_mapping(
        envelope["proposal"],
        _DESKTOP_PROPOSAL_FIELDS,
        "actor_envelope.proposal",
    )
    if (
        proposal["format"] != "actor-proposal/0"
        or proposal["kind"] != "desktop_action"
        or type(proposal["local_intent"]) is not str
        or not proposal["local_intent"]
    ):
        raise RuntimeEvidenceContractError(
            "actor_envelope proposal is not one bounded desktop action"
        )
    action = _action(proposal["action"], "actor_envelope.proposal.action")
    if action not in _DESKTOP_ACTIONS:
        raise RuntimeEvidenceContractError("actor_envelope action is not admitted")
    arguments = _validate_submitted_arguments(
        action,
        proposal["args"],
        "actor_envelope.proposal.args",
    )
    return (
        _frozen_object(envelope, "actor_envelope"),
        _frozen_object(proposal, "actor_envelope.proposal"),
        arguments,
    )


def _validate_capability_grant(
    value: Any,
    *,
    envelope: Mapping[str, Any],
) -> dict[str, Any]:
    item = _strict_mapping(
        value,
        _CAPABILITY_GRANT_FIELDS,
        "broker_projection.active_grants[]",
    )
    if item["format"] != "capability-grant/0":
        raise RuntimeEvidenceContractError(
            "active grant format is not canonical"
        )
    for name in (
        "grant_id",
        "mission_id",
        "run_id",
        "obligation_id",
    ):
        _identity(item[name], f"active_grant.{name}")
    try:
        capability = canonical_capability(item["capability"])
    except (TypeError, ValueError) as exc:
        raise RuntimeEvidenceContractError(
            "active grant capability is not canonical"
        ) from exc
    if capability != item["capability"]:
        raise RuntimeEvidenceContractError(
            "active grant capability is not canonical"
        )
    for name in ("mission_revision", "issued_turn"):
        _nonnegative_integer(item[name], f"active_grant.{name}")
    expires = item["expires_after_turns"]
    if type(expires) is not int or not 1 <= expires <= MAX_SIGNED_64:
        raise RuntimeEvidenceContractError(
            "active_grant.expires_after_turns must be positive"
        )
    reason = item["policy_reason"]
    if type(reason) is not str or not reason.strip():
        raise RuntimeEvidenceContractError(
            "active_grant.policy_reason must be non-empty"
        )
    material = {
        "format": "capability-grant/0",
        "mission_id": item["mission_id"],
        "run_id": item["run_id"],
        "capability": capability,
        "obligation_id": item["obligation_id"],
        "mission_revision": item["mission_revision"],
        "issued_turn": item["issued_turn"],
        "expires_after_turns": expires,
        "policy_reason": reason,
    }
    expected_id = "grant-" + hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()[:24]
    if item["grant_id"] != expected_id:
        raise RuntimeEvidenceContractError(
            "active grant identity changed from its canonical scope"
        )
    if (
        item["mission_id"] != envelope["mission_id"]
        or item["run_id"] != envelope["run_id"]
        or item["obligation_id"] != envelope["obligation_id"]
        or item["mission_revision"] != envelope["mission_revision"]
        or not (
            item["issued_turn"]
            <= envelope["turn_index"]
            < item["issued_turn"] + expires
        )
    ):
        raise RuntimeEvidenceContractError(
            "active grant is outside the Actor decision lifetime"
        )
    return item


def _validate_broker_projection(
    value: Any,
    *,
    envelope: Mapping[str, Any],
) -> Mapping[str, Any]:
    projection = _strict_mapping(
        value,
        _BROKER_PROJECTION_FIELDS,
        "broker_projection",
    )
    if projection["format"] != "broker-projection/1":
        raise RuntimeEvidenceContractError(
            "broker_projection format is not canonical"
        )
    for name in (
        "projection_id",
        "mission_id",
        "run_id",
        "profile_id",
        "obligation_id",
    ):
        _identity(projection[name], f"broker_projection.{name}")
    for name in ("catalog_fingerprint", "state_fingerprint"):
        _sha256(projection[name], f"broker_projection.{name}")
    _nonnegative_integer(
        projection["mission_revision"],
        "broker_projection.mission_revision",
    )
    _nonnegative_integer(
        projection["turn_index"],
        "broker_projection.turn_index",
    )
    tool_names = projection["tool_names"]
    if (
        type(tool_names) is not list
        or not tool_names
        or any(
            _action(item, "broker_projection.tool_names[]")
            not in _RUNTIME_ACTOR_TOOLS
            for item in tool_names
        )
        or len(tool_names) != len(set(tool_names))
    ):
        raise RuntimeEvidenceContractError(
            "broker_projection.tool_names is not the bounded Actor surface"
        )
    active_grants = projection["active_grants"]
    if type(active_grants) is not list:
        raise RuntimeEvidenceContractError(
            "broker_projection.active_grants must be an array"
        )
    active_grants = [
        _validate_capability_grant(item, envelope=envelope)
        for item in active_grants
    ]
    grant_ids = [item["grant_id"] for item in active_grants]
    if grant_ids != sorted(set(grant_ids)):
        raise RuntimeEvidenceContractError(
            "broker_projection.active_grants must be unique and sorted"
        )
    projection["active_grants"] = active_grants
    missing_capabilities = projection["missing_capabilities"]
    if type(missing_capabilities) is not list:
        raise RuntimeEvidenceContractError(
            "broker_projection.missing_capabilities must be an array"
        )
    try:
        canonical_missing = [
            canonical_capability(item) for item in missing_capabilities
        ]
    except (TypeError, ValueError) as exc:
        raise RuntimeEvidenceContractError(
            "broker_projection.missing_capabilities is not canonical"
        ) from exc
    if canonical_missing != sorted(set(canonical_missing)):
        raise RuntimeEvidenceContractError(
            "broker_projection.missing_capabilities must be unique and sorted"
        )
    reasons = projection["projection_reasons"]
    if (
        type(reasons) is not list
        or any(type(item) is not str or not item.strip() for item in reasons)
        or len(reasons) != len(set(reasons))
    ):
        raise RuntimeEvidenceContractError(
            "broker_projection.projection_reasons is not canonical"
        )
    if (
        projection["execution_route"] != "visual_actor"
        or projection["executor_id"] is not None
    ):
        raise RuntimeEvidenceContractError(
            "broker_projection is not the Visual Actor route"
        )
    for name in (
        "target_binding_id",
        "frame_scope",
    ):
        if projection[name] is None:
            raise RuntimeEvidenceContractError(
                f"broker_projection.{name} must be present"
            )
    expected = {
        "projection_id": envelope["broker_projection_id"],
        "mission_id": envelope["mission_id"],
        "run_id": envelope["run_id"],
        "catalog_fingerprint": envelope["catalog_fingerprint"],
        "profile_id": envelope["profile_id"],
        "state_fingerprint": envelope["state_fingerprint"],
        "mission_revision": envelope["mission_revision"],
        "obligation_id": envelope["obligation_id"],
        "turn_index": envelope["turn_index"],
        "target_binding_id": envelope["target_binding_id"],
        "frame_id": envelope["frame_id"],
        "frame_scope": envelope["frame_scope"],
        "pointer_lease_id": envelope["pointer_lease_id"],
    }
    if any(projection[name] != expected_value for name, expected_value in expected.items()):
        raise RuntimeEvidenceContractError(
            "broker_projection changed Actor envelope identity"
        )
    if tuple(grant_ids) != tuple(envelope["capability_grant_ids"]):
        raise RuntimeEvidenceContractError(
            "broker_projection active grants changed Actor envelope scope"
        )
    projection_material = {
        "format": "broker-projection/1",
        "mission_id": projection["mission_id"],
        "run_id": projection["run_id"],
        "catalog_fingerprint": projection["catalog_fingerprint"],
        "profile_id": projection["profile_id"],
        "state_fingerprint": projection["state_fingerprint"],
        "mission_revision": projection["mission_revision"],
        "obligation_id": projection["obligation_id"],
        "turn_index": projection["turn_index"],
        "execution_route": projection["execution_route"],
        "executor_id": projection["executor_id"],
        "target_binding_id": projection["target_binding_id"],
        "frame_id": projection["frame_id"],
        "frame_scope": projection["frame_scope"],
        "pointer_lease_id": projection["pointer_lease_id"],
        "tool_names": tool_names,
        "active_grant_ids": grant_ids,
        "missing_capabilities": canonical_missing,
        "projection_reasons": reasons,
    }
    expected_projection_id = "projection-" + hashlib.sha256(
        json.dumps(
            projection_material,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()[:24]
    if projection["projection_id"] != expected_projection_id:
        raise RuntimeEvidenceContractError(
            "broker_projection id does not identify its canonical content"
        )
    return _frozen_object(projection, "broker_projection")


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
    run_id: str
    mission_revision: int
    obligation_id: str
    turn_index: int
    proposal_kind: str
    action: str
    canonical_arguments: Mapping[str, Any] = field(repr=False)
    canonical_arguments_sha256: str
    actor_envelope: Mapping[str, Any] = field(repr=False)
    actor_envelope_sha256: str
    broker_projection: Mapping[str, Any] = field(repr=False)
    broker_projection_sha256: str
    tool_schema: Mapping[str, Any] = field(repr=False)
    tool_schema_sha256: str
    format: str = ACTOR_DECISION_RECORD_FORMAT

    def __post_init__(self) -> None:
        if self.format != ACTOR_DECISION_RECORD_FORMAT:
            raise RuntimeEvidenceContractError("unsupported ActorDecisionRecord format")
        for name in ("decision_id", "mission_id", "run_id", "obligation_id"):
            object.__setattr__(self, name, _identity(getattr(self, name), name))
        object.__setattr__(
            self,
            "mission_revision",
            _nonnegative_integer(self.mission_revision, "mission_revision"),
        )
        object.__setattr__(
            self,
            "turn_index",
            _nonnegative_integer(self.turn_index, "turn_index"),
        )
        if self.proposal_kind != "desktop_action":
            raise RuntimeEvidenceContractError(
                "evidence plane accepts only desktop_action decisions"
            )
        action = _action(self.action)
        if action not in _DESKTOP_ACTIONS:
            raise RuntimeEvidenceContractError("decision action is not admitted")
        arguments = _validate_submitted_arguments(
            action,
            _thaw(self.canonical_arguments),
        )
        envelope, proposal, envelope_arguments = _validate_actor_envelope(
            _thaw(self.actor_envelope)
        )
        projection = _validate_broker_projection(
            _thaw(self.broker_projection),
            envelope=envelope,
        )
        schema = _frozen_object(_thaw(self.tool_schema), "tool_schema")
        _reject_non_execution_fields(envelope, "actor_envelope")
        _reject_non_execution_fields(projection, "broker_projection")
        _reject_non_execution_fields(schema, "tool_schema")
        if schema.get("type") != "object":
            raise RuntimeEvidenceContractError(
                "tool_schema must be an object JSON schema"
            )
        expected = {
            "decision_id": envelope["decision_id"],
            "mission_id": envelope["mission_id"],
            "run_id": envelope["run_id"],
            "mission_revision": envelope["mission_revision"],
            "obligation_id": envelope["obligation_id"],
            "turn_index": envelope["turn_index"],
            "proposal_kind": proposal["kind"],
            "action": proposal["action"],
        }
        if any(getattr(self, name) != value for name, value in expected.items()):
            raise RuntimeEvidenceContractError(
                "ActorDecisionRecord identity changed from actor_envelope"
            )
        if arguments != envelope_arguments:
            raise RuntimeEvidenceContractError(
                "ActorDecisionRecord arguments changed from actor_envelope"
            )
        hashes = {
            "canonical_arguments_sha256": canonical_sha256(_thaw(arguments)),
            "actor_envelope_sha256": canonical_sha256(_thaw(envelope)),
            "broker_projection_sha256": canonical_sha256(_thaw(projection)),
            "tool_schema_sha256": canonical_sha256(_thaw(schema)),
        }
        for name in (
            "canonical_arguments_sha256",
            "actor_envelope_sha256",
            "broker_projection_sha256",
            "tool_schema_sha256",
        ):
            digest = _sha256(getattr(self, name), name)
            if digest != hashes[name]:
                raise RuntimeEvidenceContractError(
                    f"{name} does not match its exact canonical payload"
                )
        if action not in projection["tool_names"]:
            raise RuntimeEvidenceContractError(
                "selected action is absent from BrokerProjection"
            )
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "canonical_arguments", arguments)
        object.__setattr__(self, "actor_envelope", envelope)
        object.__setattr__(self, "broker_projection", projection)
        object.__setattr__(self, "tool_schema", schema)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "decision_id": self.decision_id,
            "mission_id": self.mission_id,
            "run_id": self.run_id,
            "mission_revision": self.mission_revision,
            "obligation_id": self.obligation_id,
            "turn_index": self.turn_index,
            "proposal_kind": self.proposal_kind,
            "action": self.action,
            "canonical_arguments": _thaw(self.canonical_arguments),
            "canonical_arguments_sha256": self.canonical_arguments_sha256,
            "actor_envelope": _thaw(self.actor_envelope),
            "actor_envelope_sha256": self.actor_envelope_sha256,
            "broker_projection": _thaw(self.broker_projection),
            "broker_projection_sha256": self.broker_projection_sha256,
            "tool_schema": _thaw(self.tool_schema),
            "tool_schema_sha256": self.tool_schema_sha256,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ActorDecisionRecord":
        return cls(**_strict_mapping(raw, cls.__dataclass_fields__, cls.__name__))


@dataclass(frozen=True, slots=True)
class AdmissionRecord:
    decision_id: str
    actor_decision_record_sha256: str
    actor_envelope_sha256: str
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
        object.__setattr__(
            self,
            "actor_decision_record_sha256",
            _sha256(
                self.actor_decision_record_sha256,
                "actor_decision_record_sha256",
            ),
        )
        object.__setattr__(
            self,
            "actor_envelope_sha256",
            _sha256(self.actor_envelope_sha256, "actor_envelope_sha256"),
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
            "actor_decision_record_sha256": (
                self.actor_decision_record_sha256
            ),
            "actor_envelope_sha256": self.actor_envelope_sha256,
            "frame_identity": _thaw(self.frame_identity),
            "target_identity": _thaw(self.target_identity),
            "mutation_permit_id": self.mutation_permit_id,
            "controller_route": self.controller_route,
            "accepted_timestamp_ns": self.accepted_timestamp_ns,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "AdmissionRecord":
        return cls(**_strict_mapping(raw, cls.__dataclass_fields__, cls.__name__))


def _validate_physical_arguments(
    action: str,
    value: Any,
) -> Mapping[str, Any]:
    fields = {
        "click": ("kind", "x", "y", "button", "click_count"),
        "double_click": ("kind", "x", "y", "button", "click_count"),
        "scroll": (
            "kind",
            "x",
            "y",
            "signed_wheel_amount",
            "event_wheel_delta",
        ),
        "type_text": ("kind", "text"),
        "hotkey": ("kind", "modifiers", "key"),
    }[action]
    item = _strict_mapping(value, fields, "physical_arguments")
    expected_kind = {
        "click": "pointer_click",
        "double_click": "pointer_click",
        "scroll": "wheel",
        "type_text": "type_text",
        "hotkey": "hotkey",
    }[action]
    if item["kind"] != expected_kind:
        raise RuntimeEvidenceContractError(
            "physical_arguments kind changed from action"
        )
    if action in {"click", "double_click", "scroll"}:
        for name in ("x", "y"):
            if type(item[name]) is not int:
                raise RuntimeEvidenceContractError(
                    f"physical_arguments.{name} must be an integer"
                )
    if action in {"click", "double_click"}:
        expected_count = 2 if action == "double_click" else 1
        if (
            item["button"] != "left"
            or type(item["click_count"]) is not int
            or item["click_count"] != expected_count
        ):
            raise RuntimeEvidenceContractError(
                "physical click payload is not the admitted left click count"
            )
    elif action == "scroll":
        amount = item["signed_wheel_amount"]
        delta = item["event_wheel_delta"]
        if (
            type(amount) is not int
            or amount == 0
            or abs(amount) > 1200
            or type(delta) is not int
            or delta != amount * 120
        ):
            raise RuntimeEvidenceContractError(
                "physical wheel payload is not canonical"
            )
    elif action == "type_text":
        text = item["text"]
        if type(text) is not str or not 1 <= len(text) <= 4096:
            raise RuntimeEvidenceContractError(
                "physical type_text payload is not canonical"
            )
    else:
        modifiers = item["modifiers"]
        key = item["key"]
        if (
            type(modifiers) is not list
            or any(type(value) is not str for value in modifiers)
            or len(modifiers) != len(set(modifiers))
            or type(key) is not str
            or not key
            or key in modifiers
        ):
            raise RuntimeEvidenceContractError(
                "physical hotkey payload is not canonical"
            )
    return _frozen_object(item, "physical_arguments")


@dataclass(frozen=True, slots=True)
class PhysicalAttemptRecord:
    mission_id: str
    run_id: str
    obligation_id: str
    decision_id: str
    action_id: str
    action: str
    attempt_index: int
    submitted_arguments: Mapping[str, Any] = field(repr=False)
    physical_arguments: Mapping[str, Any] = field(repr=False)
    raw_device_route: str
    mechanism: str
    target_identity: Mapping[str, Any]
    frame_identity: Mapping[str, Any]
    mutation_permit_id: str
    actor_decision_record_sha256: str
    admission_record_sha256: str
    timestamp_ns: int
    reservation_sha256: str
    format: str = PHYSICAL_ATTEMPT_RECORD_FORMAT

    def __post_init__(self) -> None:
        if self.format != PHYSICAL_ATTEMPT_RECORD_FORMAT:
            raise RuntimeEvidenceContractError(
                "unsupported PhysicalAttemptRecord format"
            )
        for name in (
            "mission_id",
            "run_id",
            "obligation_id",
            "decision_id",
            "action_id",
            "mutation_permit_id",
        ):
            object.__setattr__(self, name, _identity(getattr(self, name), name))
        if self.action_id != self.decision_id:
            raise RuntimeEvidenceContractError(
                "physical action_id must equal decision_id"
            )
        action = _action(self.action)
        if action not in _DESKTOP_ACTIONS:
            raise RuntimeEvidenceContractError("physical action is not admitted")
        if type(self.attempt_index) is not int or self.attempt_index != 1:
            raise RuntimeEvidenceContractError("attempt_index must be exactly one")
        submitted = _validate_submitted_arguments(
            action,
            _thaw(self.submitted_arguments),
            "submitted_arguments",
        )
        physical = _validate_physical_arguments(
            action,
            _thaw(self.physical_arguments),
        )
        if action == "type_text" and physical["text"] != submitted["text"]:
            raise RuntimeEvidenceContractError(
                "physical type_text changed submitted text"
            )
        if action == "hotkey" and (
            *physical["modifiers"],
            physical["key"],
        ) != tuple(submitted["keys"]):
            raise RuntimeEvidenceContractError(
                "physical hotkey changed submitted chord"
            )
        if action == "scroll" and (
            physical["signed_wheel_amount"] != submitted["amount"]
        ):
            raise RuntimeEvidenceContractError(
                "physical wheel amount changed submitted amount"
            )
        route = self.raw_device_route
        expected_route = {
            "click": "raw_device.admitted_physical_click/0",
            "double_click": "raw_device.admitted_physical_click/0",
            "scroll": "raw_device.admitted_physical_scroll/0",
            "type_text": "raw_device.admitted_physical_type_text/0",
            "hotkey": "raw_device.admitted_physical_hotkey/0",
        }[action]
        if route != expected_route or self.mechanism != "physical_sendinput":
            raise RuntimeEvidenceContractError(
                "physical attempt route/mechanism is not canonical"
            )
        target = _validate_target_identity(
            _thaw(self.target_identity),
            "target_identity",
        )
        frame = _validate_frame_identity(
            _thaw(self.frame_identity),
            "frame_identity",
        )
        if frame["hwnd"] != target["hwnd"] or frame["epoch"] != target["epoch"]:
            raise RuntimeEvidenceContractError(
                "physical attempt frame and target identities disagree"
            )
        for name in (
            "actor_decision_record_sha256",
            "admission_record_sha256",
        ):
            object.__setattr__(self, name, _sha256(getattr(self, name), name))
        object.__setattr__(
            self,
            "timestamp_ns",
            _timestamp_ns(self.timestamp_ns, "timestamp_ns"),
        )
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "submitted_arguments", submitted)
        object.__setattr__(self, "physical_arguments", physical)
        object.__setattr__(self, "target_identity", target)
        object.__setattr__(self, "frame_identity", frame)
        claimed = _sha256(self.reservation_sha256, "reservation_sha256")
        if claimed != canonical_sha256(self.reservation_material()):
            raise RuntimeEvidenceContractError(
                "reservation_sha256 does not bind the physical attempt"
            )

    def reservation_material(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "mission_id": self.mission_id,
            "run_id": self.run_id,
            "obligation_id": self.obligation_id,
            "decision_id": self.decision_id,
            "action_id": self.action_id,
            "action": self.action,
            "attempt_index": self.attempt_index,
            "submitted_arguments": _thaw(self.submitted_arguments),
            "physical_arguments": _thaw(self.physical_arguments),
            "raw_device_route": self.raw_device_route,
            "mechanism": self.mechanism,
            "target_identity": _thaw(self.target_identity),
            "frame_identity": _thaw(self.frame_identity),
            "mutation_permit_id": self.mutation_permit_id,
            "actor_decision_record_sha256": (
                self.actor_decision_record_sha256
            ),
            "admission_record_sha256": self.admission_record_sha256,
            "timestamp_ns": self.timestamp_ns,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.reservation_material(),
            "reservation_sha256": self.reservation_sha256,
        }

    @classmethod
    def create(cls, **values: Any) -> "PhysicalAttemptRecord":
        material = {
            "format": PHYSICAL_ATTEMPT_RECORD_FORMAT,
            **values,
        }
        return cls(
            **material,
            reservation_sha256=canonical_sha256(material),
        )

    @classmethod
    def from_dict(cls, raw: Any) -> "PhysicalAttemptRecord":
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
    physical_attempt_record_sha256: str | None
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
        if (self.physical_attempt_count == 1) != (
            self.physical_attempt_record_sha256 is not None
        ):
            raise RuntimeEvidenceContractError(
                "physical attempt and attempt-record hash must be paired"
            )
        if self.physical_attempt_record_sha256 is not None:
            object.__setattr__(
                self,
                "physical_attempt_record_sha256",
                _sha256(
                    self.physical_attempt_record_sha256,
                    "physical_attempt_record_sha256",
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
            after["frame_id"] != before["frame_id"] + 1
            or (post_id is not None and after["frame_id"] != post_id)
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
            "physical_attempt_record_sha256": (
                self.physical_attempt_record_sha256
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
    mission_id: str
    run_id: str
    decision_id: str
    action_id: str
    frame_id: int
    frame_identity: Mapping[str, Any]
    artifact_uri: str
    raw_jpeg_sha256: str
    runtime_frame_image_sha256: str
    capture_timestamp_ns: int
    role: FrameArtifactRole | str
    format: str = FRAME_ARTIFACT_REFERENCE_FORMAT

    def __post_init__(self) -> None:
        if self.format != FRAME_ARTIFACT_REFERENCE_FORMAT:
            raise RuntimeEvidenceContractError(
                "unsupported FrameArtifactReference format"
            )
        for name in (
            "mission_id",
            "run_id",
            "decision_id",
            "action_id",
        ):
            object.__setattr__(self, name, _identity(getattr(self, name), name))
        if self.action_id != self.decision_id:
            raise RuntimeEvidenceContractError(
                "frame artifact action_id must equal decision_id"
            )
        object.__setattr__(
            self, "frame_id", _positive_integer(self.frame_id, "frame_id")
        )
        identity = _validate_frame_identity(
            _thaw(self.frame_identity),
            "frame_identity",
        )
        if identity["frame_id"] != self.frame_id:
            raise RuntimeEvidenceContractError(
                "frame artifact frame_id changed from frame identity"
            )
        object.__setattr__(self, "frame_identity", identity)
        uri = self.artifact_uri
        path = PurePosixPath(uri) if type(uri) is str else None
        if (
            type(uri) is not str
            or not uri.strip()
            or len(uri) > _MAX_URI_LENGTH
            or path is None
            or path.is_absolute()
            or "\\" in uri
            or ":" in uri
            or any(part in {"", ".", ".."} for part in path.parts)
            or not uri.startswith("artifacts/")
            or path.suffix.lower() != ".jpg"
        ):
            raise RuntimeEvidenceContractError(
                "artifact_uri must be a safe relative run JPEG path"
            )
        object.__setattr__(
            self,
            "raw_jpeg_sha256",
            _sha256(self.raw_jpeg_sha256, "raw_jpeg_sha256"),
        )
        runtime_hash = _sha256(
            self.runtime_frame_image_sha256,
            "runtime_frame_image_sha256",
        )
        if runtime_hash != identity["image_sha256"]:
            raise RuntimeEvidenceContractError(
                "artifact RuntimeFrame hash changed from frame identity"
            )
        object.__setattr__(
            self,
            "runtime_frame_image_sha256",
            runtime_hash,
        )
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
            "mission_id": self.mission_id,
            "run_id": self.run_id,
            "decision_id": self.decision_id,
            "action_id": self.action_id,
            "frame_id": self.frame_id,
            "frame_identity": _thaw(self.frame_identity),
            "artifact_uri": self.artifact_uri,
            "raw_jpeg_sha256": self.raw_jpeg_sha256,
            "runtime_frame_image_sha256": (
                self.runtime_frame_image_sha256
            ),
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
    physical_attempts: tuple[PhysicalAttemptRecord, ...]
    action_bindings: tuple[ActionExecutionBinding, ...]
    frame_artifacts: tuple[FrameArtifactReference, ...]
    journal_head_sha256: str | None
    journal_event_count: int
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
        attempts = _record_tuple(
            self.physical_attempts,
            PhysicalAttemptRecord,
            "physical_attempts",
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
            (attempts, lambda item: item.decision_id, "physical_attempts"),
            (bindings, lambda item: item.action_id, "action_bindings"),
            (
                artifacts,
                lambda item: (item.decision_id, item.role.value),
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
        attempt_by_id = {item.decision_id: item for item in attempts}
        binding_by_id = {item.decision_id: item for item in bindings}
        artifact_by_key = {
            (item.decision_id, item.role): item for item in artifacts
        }
        if any(
            item.mission_id != mission_id or item.run_id != self.run_id
            for item in decisions
        ):
            raise RuntimeEvidenceContractError(
                "report contains a decision from another mission/run"
            )
        if (
            not set(admission_by_id).issubset(decision_by_id)
            or not set(attempt_by_id).issubset(decision_by_id)
            or not set(binding_by_id).issubset(decision_by_id)
            or any(
                decision_id not in decision_by_id
                for decision_id, _role in artifact_by_key
            )
        ):
            raise RuntimeEvidenceContractError(
                "report contains an orphan evidence record"
            )
        missing = set(_unique_strings(self.missing_evidence, "missing_evidence"))
        failures = set(
            _unique_strings(
                self.verification_failures, "verification_failures"
            )
        )
        if not decisions:
            missing.add("actor_decision")
        if self.journal_head_sha256 is None:
            journal_head = None
            missing.add("durable_journal")
        else:
            journal_head = _sha256(
                self.journal_head_sha256,
                "journal_head_sha256",
            )
        journal_count = _nonnegative_integer(
            self.journal_event_count,
            "journal_event_count",
        )
        if journal_head is None and journal_count != 0:
            raise RuntimeEvidenceContractError(
                "journal event count requires a journal head"
            )
        for decision_id, decision in decision_by_id.items():
            admission = admission_by_id.get(decision_id)
            attempt = attempt_by_id.get(decision_id)
            binding = binding_by_id.get(decision_id)
            if admission is None:
                missing.add(f"{decision_id}_admission")
            if attempt is None:
                missing.add(f"{decision_id}_physical_attempt")
            if admission is not None and (
                admission.actor_decision_record_sha256
                != canonical_sha256(decision.to_dict())
                or admission.actor_envelope_sha256
                != decision.actor_envelope_sha256
            ):
                raise RuntimeEvidenceContractError(
                    "admission changed Actor decision identity"
                )
            if attempt is not None:
                if (
                    attempt.mission_id != mission_id
                    or attempt.run_id != self.run_id
                    or attempt.obligation_id != decision.obligation_id
                    or attempt.decision_id != decision_id
                    or attempt.action_id != decision_id
                    or attempt.action != decision.action
                    or attempt.submitted_arguments
                    != decision.canonical_arguments
                    or attempt.actor_decision_record_sha256
                    != canonical_sha256(decision.to_dict())
                ):
                    raise RuntimeEvidenceContractError(
                        "physical attempt is outside decision scope"
                    )
                if admission is not None and (
                    attempt.admission_record_sha256
                    != canonical_sha256(admission.to_dict())
                    or attempt.frame_identity != admission.frame_identity
                    or attempt.target_identity != admission.target_identity
                    or attempt.mutation_permit_id
                    != admission.mutation_permit_id
                ):
                    raise RuntimeEvidenceContractError(
                        "physical attempt changed admission identity"
                    )
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
            if attempt is not None:
                if (
                    binding.attempt_reservation_sha256
                    != attempt.reservation_sha256
                    or binding.physical_attempt_record_sha256
                    != canonical_sha256(attempt.to_dict())
                ):
                    raise RuntimeEvidenceContractError(
                        "physical attempt binding changed"
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
                reference = artifact_by_key.get((decision_id, role))
                if reference is None:
                    missing.add(f"{token}_artifact")
                    continue
                expected_identity = (
                    binding.before_frame_identity
                    if role is FrameArtifactRole.BEFORE
                    else binding.after_frame_identity
                )
                if (
                    reference.mission_id != mission_id
                    or reference.run_id != self.run_id
                    or reference.decision_id != decision_id
                    or reference.action_id != binding.action_id
                    or reference.frame_id != frame_id
                    or reference.frame_identity != expected_identity
                ):
                    failures.add(f"{token}_artifact_scope_mismatch")
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
        missing_items = tuple(sorted(missing))
        failure_items = tuple(sorted(failures))
        derived_status = (
            RuntimeEvidenceReportStatus.VERIFICATION_FAILED
            if failure_items
            else RuntimeEvidenceReportStatus.INCOMPLETE
            if missing_items
            else RuntimeEvidenceReportStatus.COMPLETE
        )
        if claimed_status is not derived_status:
            raise RuntimeEvidenceContractError(
                "report status contradicts independently derived completeness"
            )
        object.__setattr__(self, "status", derived_status)
        object.__setattr__(self, "actor_decisions", decisions)
        object.__setattr__(self, "admissions", admissions)
        object.__setattr__(self, "physical_attempts", attempts)
        object.__setattr__(self, "action_bindings", bindings)
        object.__setattr__(self, "frame_artifacts", artifacts)
        object.__setattr__(self, "journal_head_sha256", journal_head)
        object.__setattr__(self, "journal_event_count", journal_count)
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
            "physical_attempts": [
                item.to_dict() for item in self.physical_attempts
            ],
            "action_bindings": [item.to_dict() for item in self.action_bindings],
            "frame_artifacts": [item.to_dict() for item in self.frame_artifacts],
            "journal_head_sha256": self.journal_head_sha256,
            "journal_event_count": self.journal_event_count,
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
            ("physical_attempts", PhysicalAttemptRecord),
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


def runtime_evidence_report_sha256(
    report: RuntimeEvidenceReport,
) -> str:
    """Hash one fully validated report without re-bounding its aggregate size.

    Every record in ``RuntimeEvidenceReport`` has already crossed the bounded
    JSON contract independently.  The complete report may legitimately exceed
    that per-value node limit after a long run, so stream the same canonical
    JSON bytes instead of copying and re-validating the aggregate.
    """

    if type(report) is not RuntimeEvidenceReport:
        raise TypeError("report must be a RuntimeEvidenceReport")
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    for chunk in encoder.iterencode(report.to_dict()):
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


__all__ = [
    "ACTION_EXECUTION_BINDING_FORMAT",
    "ACTOR_DECISION_RECORD_FORMAT",
    "ADMISSION_RECORD_FORMAT",
    "FRAME_ARTIFACT_REFERENCE_FORMAT",
    "PHYSICAL_ATTEMPT_RECORD_FORMAT",
    "RUNTIME_EVIDENCE_REPORT_FORMAT",
    "ActionExecutionBinding",
    "ActorDecisionRecord",
    "AdmissionRecord",
    "FrameArtifactReference",
    "FrameArtifactRole",
    "PhysicalAttemptRecord",
    "RuntimeEvidenceContractError",
    "RuntimeEvidenceReport",
    "RuntimeEvidenceReportStatus",
    "canonical_controller_transition",
    "canonical_raw_action_receipt",
    "canonical_sha256",
    "runtime_evidence_report_sha256",
]
