"""Strict P3 contracts for catalog, grants, runtime state, and projection."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from ..capability_names import canonical_capabilities, canonical_capability


TOOL_SPEC_FORMAT = "tool-spec/1"
TOOL_CATALOG_FORMAT = "tool-catalog/1"
CAPABILITY_GRANT_FORMAT = "capability-grant/0"
BROKER_STATE_FORMAT = "broker-runtime-state/1"
BROKER_PROJECTION_FORMAT = "broker-projection/1"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}")
_TOOL = re.compile(r"[a-z][a-z0-9_]{1,63}")
_SHA = re.compile(r"[0-9a-f]{64}")


class CapabilityContractError(ValueError):
    pass


class ToolRoute(str, Enum):
    VISUAL_ACTOR = "visual_actor"
    DETERMINISTIC = "deterministic"
    PROVIDER = "provider"


class ExecutionRoute(str, Enum):
    VISUAL_ACTOR = "visual_actor"
    DETERMINISTIC = "deterministic"
    PROVIDER = "provider"
    BLOCKED = "blocked"


class MutationKind(str, Enum):
    NONE = "none"
    GUI = "gui"
    EXTERNAL = "external"


class PointerDisposition(str, Enum):
    RELEASED = "released"
    HELD = "held"
    UNKNOWN = "unknown"


TOOL_PREREQUISITES = frozenset({
    "target_unbound",
    "target_bound",
    "authoritative_frame",
    "authoritative_full_desktop_frame",
    "target_candidates_available",
    "pointer_released",
    "pointer_held",
    "current_run_artifact",
    "recipient_bound",
    "adapter_live",
    "mcp_discovered",
    "receipt_capable",
    "runtime_healthy",
})
FORBIDDEN_ACTOR_TOOLS = frozenset({
    "done", "fail", "revise_plan", "complete_subgoal",
    "run_action_batch", "execute_subgoal",
})


def _text(value: object, name: str) -> str:
    if type(value) is not str:
        raise CapabilityContractError(f"{name} must be a string")
    result = value.strip()
    if not result:
        raise CapabilityContractError(f"{name} must be non-empty")
    return result


def _identity(value: object, name: str) -> str:
    result = _text(value, name)
    if not _ID.fullmatch(result):
        raise CapabilityContractError(f"{name} has invalid characters")
    return result


def _strings(values: object, name: str, *, sorted_output: bool = True) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        raise CapabilityContractError(f"{name} must be an array")
    try:
        result = tuple(_text(item, f"{name}[]") for item in values)
    except TypeError as exc:
        raise CapabilityContractError(f"{name} must be an array") from exc
    if len(result) != len(set(result)):
        raise CapabilityContractError(f"{name} must contain unique values")
    return tuple(sorted(result)) if sorted_output else result


def _json_value(
    value: Any,
    path: str = "value",
    *,
    _ancestors: set[int] | None = None,
) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CapabilityContractError(f"{path} contains non-finite number")
        return value
    if isinstance(value, (list, tuple, Mapping)):
        ancestors = _ancestors if _ancestors is not None else set()
        marker = id(value)
        if marker in ancestors:
            raise CapabilityContractError(f"{path} contains a cyclic JSON value")
        ancestors.add(marker)
        try:
            if isinstance(value, (list, tuple)):
                return [
                    _json_value(item, f"{path}[]", _ancestors=ancestors)
                    for item in value
                ]
            if any(not isinstance(key, str) for key in value):
                raise CapabilityContractError(f"{path} keys must be strings")
            return {
                key: _json_value(item, f"{path}.{key}", _ancestors=ancestors)
                for key, item in value.items()
            }
        finally:
            ancestors.remove(marker)
    raise CapabilityContractError(f"{path} contains non-JSON value")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    return value


def _boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise CapabilityContractError(f"{name} must be boolean")
    return value


def _strict(raw: Mapping[str, Any], fields: Iterable[str], name: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise CapabilityContractError(f"{name} must be an object")
    expected = set(fields)
    keys = set(raw)
    if any(not isinstance(key, str) for key in keys):
        raise CapabilityContractError(f"{name} field names must be strings")
    if keys != expected:
        raise CapabilityContractError(
            f"{name} fields mismatch: missing={sorted(expected - keys)!r}, "
            f"unknown={sorted(keys - expected)!r}"
        )
    return dict(raw)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    schema: Mapping[str, Any]
    route: ToolRoute | str
    mutation_kind: MutationKind | str
    required_any_capabilities: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    authorization_scope: str | None = None
    order: int = 100
    format: str = TOOL_SPEC_FORMAT

    def __post_init__(self) -> None:
        if self.format != TOOL_SPEC_FORMAT:
            raise CapabilityContractError("unsupported tool spec format")
        name = _text(self.name, "tool.name")
        if not _TOOL.fullmatch(name) or name in FORBIDDEN_ACTOR_TOOLS:
            raise CapabilityContractError(f"invalid or forbidden tool name {name!r}")
        object.__setattr__(self, "name", name)
        schema = _json_value(self.schema, "tool.schema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise CapabilityContractError("tool schema must be an object JSON schema")
        object.__setattr__(self, "schema", _freeze_json(schema))
        object.__setattr__(self, "route", ToolRoute(self.route))
        object.__setattr__(self, "mutation_kind", MutationKind(self.mutation_kind))
        if self.route is ToolRoute.VISUAL_ACTOR and self.mutation_kind is MutationKind.EXTERNAL:
            raise CapabilityContractError(
                "visual actor tools cannot perform external mutations"
            )
        if self.route is not ToolRoute.VISUAL_ACTOR and self.mutation_kind is MutationKind.GUI:
            raise CapabilityContractError(
                "provider/deterministic tools cannot mutate the GUI"
            )
        object.__setattr__(
            self, "required_any_capabilities",
            canonical_capabilities(self.required_any_capabilities),
        )
        prerequisites = _strings(self.prerequisites, "tool.prerequisites")
        unknown = set(prerequisites) - TOOL_PREREQUISITES
        if unknown:
            raise CapabilityContractError(f"unknown prerequisites {sorted(unknown)!r}")
        object.__setattr__(self, "prerequisites", prerequisites)
        if self.authorization_scope is not None:
            object.__setattr__(
                self, "authorization_scope",
                _identity(self.authorization_scope, "authorization_scope"),
            )
        if self.mutation_kind is MutationKind.EXTERNAL and not self.authorization_scope:
            raise CapabilityContractError("external mutation tool requires authorization_scope")
        if type(self.order) is not int or self.order < 0:
            raise CapabilityContractError("tool order must be non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "name": self.name,
            "schema": _json_value(self.schema),
            "route": self.route.value,
            "mutation_kind": self.mutation_kind.value,
            "required_any_capabilities": list(self.required_any_capabilities),
            "prerequisites": list(self.prerequisites),
            "authorization_scope": self.authorization_scope,
            "order": self.order,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ToolSpec":
        fields = (
            "format", "name", "schema", "route", "mutation_kind",
            "required_any_capabilities", "prerequisites", "authorization_scope", "order",
        )
        return cls(**_strict(raw, fields, "ToolSpec"))


@dataclass(frozen=True, slots=True)
class ToolCatalog:
    tools: tuple[ToolSpec, ...]
    format: str = TOOL_CATALOG_FORMAT
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.format != TOOL_CATALOG_FORMAT:
            raise CapabilityContractError("unsupported tool catalog format")
        tools = tuple(
            item if isinstance(item, ToolSpec) else ToolSpec.from_dict(item)
            for item in self.tools
        )
        if not tools:
            raise CapabilityContractError("tool catalog cannot be empty")
        names = [item.name for item in tools]
        if len(names) != len(set(names)):
            raise CapabilityContractError("tool names must be unique")
        tools = tuple(sorted(tools, key=lambda item: (item.order, item.name)))
        object.__setattr__(self, "tools", tools)
        canonical = json.dumps(
            [item.to_dict() for item in tools],
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        object.__setattr__(self, "fingerprint", hashlib.sha256(canonical.encode()).hexdigest())

    def get(self, name: str) -> ToolSpec:
        for tool in self.tools:
            if tool.name == name:
                return tool
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "fingerprint": self.fingerprint,
            "tools": [item.to_dict() for item in self.tools],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ToolCatalog":
        value = _strict(raw, ("format", "fingerprint", "tools"), "ToolCatalog")
        result = cls(format=value["format"], tools=tuple(value["tools"]))
        if value["fingerprint"] != result.fingerprint:
            raise CapabilityContractError("tool catalog fingerprint mismatch")
        return result


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    grant_id: str
    mission_id: str
    run_id: str
    capability: str
    obligation_id: str
    mission_revision: int
    issued_turn: int
    expires_after_turns: int
    policy_reason: str
    format: str = CAPABILITY_GRANT_FORMAT

    def __post_init__(self) -> None:
        if self.format != CAPABILITY_GRANT_FORMAT:
            raise CapabilityContractError("unsupported capability grant format")
        for name in ("grant_id", "mission_id", "run_id", "obligation_id"):
            object.__setattr__(self, name, _identity(getattr(self, name), name))
        object.__setattr__(self, "capability", canonical_capability(self.capability))
        for name in ("mission_revision", "issued_turn"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise CapabilityContractError(f"{name} must be non-negative integer")
        if type(self.expires_after_turns) is not int or self.expires_after_turns < 1:
            raise CapabilityContractError("expires_after_turns must be positive integer")
        object.__setattr__(self, "policy_reason", _text(self.policy_reason, "policy_reason"))
        if self.grant_id != self._expected_id(
            mission_id=self.mission_id,
            run_id=self.run_id,
            capability=self.capability,
            obligation_id=self.obligation_id,
            mission_revision=self.mission_revision,
            issued_turn=self.issued_turn,
            expires_after_turns=self.expires_after_turns,
            policy_reason=self.policy_reason,
        ):
            raise CapabilityContractError("capability grant_id does not match its scope")

    @staticmethod
    def _expected_id(**material: Any) -> str:
        raw = json.dumps(material, sort_keys=True, separators=(",", ":"))
        return f"grant-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"

    @classmethod
    def issue(cls, **values: Any) -> "CapabilityGrant":
        material = {
            "mission_id": _identity(values["mission_id"], "mission_id"),
            "run_id": _identity(values["run_id"], "run_id"),
            "capability": canonical_capability(values["capability"]),
            "obligation_id": _identity(values["obligation_id"], "obligation_id"),
            "mission_revision": values["mission_revision"],
            "issued_turn": values["issued_turn"],
            "expires_after_turns": values["expires_after_turns"],
            "policy_reason": _text(values["policy_reason"], "policy_reason"),
        }
        return cls(grant_id=cls._expected_id(**material), **material)

    def valid_for(
        self, *, mission_id: str, run_id: str, obligation_id: str,
        mission_revision: int, turn_index: int,
    ) -> bool:
        return bool(
            self.mission_id == mission_id
            and self.run_id == run_id
            and self.obligation_id == obligation_id
            and self.mission_revision == mission_revision
            and self.issued_turn <= turn_index < self.issued_turn + self.expires_after_turns
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format, "grant_id": self.grant_id,
            "mission_id": self.mission_id, "run_id": self.run_id,
            "capability": self.capability, "obligation_id": self.obligation_id,
            "mission_revision": self.mission_revision, "issued_turn": self.issued_turn,
            "expires_after_turns": self.expires_after_turns,
            "policy_reason": self.policy_reason,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CapabilityGrant":
        fields = (
            "format", "grant_id", "mission_id", "run_id", "capability",
            "obligation_id", "mission_revision", "issued_turn",
            "expires_after_turns", "policy_reason",
        )
        return cls(**_strict(raw, fields, "CapabilityGrant"))


# Compatibility re-export: state.py is the owner, while existing imports from
# capabilities.contracts continue to resolve during the migration.
from .state import (
    ArtifactState,
    AuthoritativeFrameState,
    BrokerProjection,
    BrokerRuntimeState,
    ExecutorOffer,
    PointerLeaseState,
    TargetState,
)


__all__ = [
    "TOOL_SPEC_FORMAT",
    "TOOL_CATALOG_FORMAT",
    "CAPABILITY_GRANT_FORMAT",
    "BROKER_STATE_FORMAT",
    "BROKER_PROJECTION_FORMAT",
    "CapabilityContractError",
    "ToolRoute",
    "ExecutionRoute",
    "MutationKind",
    "PointerDisposition",
    "TOOL_PREREQUISITES",
    "FORBIDDEN_ACTOR_TOOLS",
    "ToolSpec",
    "ToolCatalog",
    "CapabilityGrant",
    "ArtifactState",
    "AuthoritativeFrameState",
    "BrokerProjection",
    "BrokerRuntimeState",
    "ExecutorOffer",
    "PointerLeaseState",
    "TargetState",
]
