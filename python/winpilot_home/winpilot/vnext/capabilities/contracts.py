"""Strict P3 contracts for catalog, grants, runtime state, and projection."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from ..capability_names import canonical_capability
from ..json_contract import (
    BoundedJSONError,
    MAX_SIGNED_64,
    bounded_json_copy,
    freeze_bounded_json,
)


TOOL_SPEC_FORMAT = "tool-spec/1"
TOOL_CATALOG_FORMAT = "tool-catalog/0"
CAPABILITY_GRANT_FORMAT = "capability-grant/0"
BROKER_STATE_FORMAT = "broker-runtime-state/2"
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


class ExecutionMedium(str, Enum):
    LOCAL = "local"
    GUI = "gui"
    CONTROL = "control"
    EXTERNAL = "external"


class MutationKind(str, Enum):
    NONE = "none"
    GUI = "gui"
    EXTERNAL = "external"


class PointerDisposition(str, Enum):
    RELEASED = "released"
    HELD = "held"
    UNKNOWN = "unknown"


TOOL_PREREQUISITES = frozenset({
    "target_bound",
    "authoritative_frame",
    "current_run_artifact",
    "recipient_bound",
    "adapter_live",
    "mcp_discovered",
    "receipt_capable",
    "runtime_healthy",
})
_MAX_INTEGER = MAX_SIGNED_64
FORBIDDEN_ACTOR_TOOLS = frozenset({
    "done", "fail", "revise_plan", "complete_subgoal",
    "run_action_batch", "execute_subgoal",
})


def _text(value: object, name: str) -> str:
    if type(value) is not str:
        raise CapabilityContractError(f"{name} must be a string")
    if not value.strip():
        raise CapabilityContractError(f"{name} must be non-empty")
    return value


def _identity(value: object, name: str) -> str:
    result = _text(value, name)
    if not _ID.fullmatch(result):
        raise CapabilityContractError(f"{name} has invalid characters")
    return result


def _strings(values: object, name: str, *, sorted_output: bool = True) -> tuple[str, ...]:
    if values is None:
        return ()
    allowed_types = (list, tuple, set, frozenset) if sorted_output else (list, tuple)
    if not isinstance(values, allowed_types):
        raise CapabilityContractError(f"{name} must be an array")
    result = tuple(_text(item, f"{name}[]") for item in values)
    if len(result) != len(set(result)):
        raise CapabilityContractError(f"{name} must contain unique values")
    return tuple(sorted(result)) if sorted_output else result


def _capability(value: object, name: str) -> str:
    try:
        canonical = canonical_capability(value)
    except (TypeError, ValueError) as exc:
        raise CapabilityContractError(f"{name} is not a canonical capability") from exc
    if canonical != value:
        raise CapabilityContractError(f"{name} is not a canonical capability")
    return canonical


def _capabilities(values: object, name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise CapabilityContractError(f"{name} must be an array")
    result = tuple(_capability(item, f"{name}[]") for item in values)
    if len(result) != len(set(result)):
        raise CapabilityContractError(f"{name} must contain unique values")
    return tuple(sorted(result))


def _enum_value(value: object, enum_type: type[Enum], name: str) -> Enum:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise CapabilityContractError(
            f"{name} is not a supported literal string"
        ) from exc


def _wire_array(value: object, name: str) -> list[Any]:
    if type(value) is not list:
        raise CapabilityContractError(f"{name} must be a literal JSON array")
    return value


def _thaw_frozen_json(value: Any) -> Any:
    """Detach JSON previously frozen by ``freeze_bounded_json``."""
    if type(value) is MappingProxyType:
        return {
            key: _thaw_frozen_json(child)
            for key, child in value.items()
        }
    if type(value) is tuple:
        return [_thaw_frozen_json(child) for child in value]
    return value


def _boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise CapabilityContractError(f"{name} must be boolean")
    return value


def _strict(raw: object, fields: Iterable[str], name: str) -> dict[str, Any]:
    if type(raw) is not dict:
        raise CapabilityContractError(f"{name} must be a plain JSON object")
    try:
        value = bounded_json_copy(raw)
    except (BoundedJSONError, RecursionError) as exc:
        raise CapabilityContractError(
            f"{name} violates the bounded JSON contract: {exc}"
        ) from exc
    if type(value) is not dict:  # pragma: no cover - validator invariant
        raise CapabilityContractError(f"{name} must be a plain JSON object")
    expected = set(fields)
    keys = set(value)
    if keys != expected:
        raise CapabilityContractError(
            f"{name} fields mismatch: missing={sorted(expected - keys)!r}, "
            f"unknown={sorted(keys - expected)!r}"
        )
    return value


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    schema: Mapping[str, Any]
    route: ToolRoute | str
    mutation_kind: MutationKind | str
    execution_medium: ExecutionMedium | str | None = None
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
        if type(self.schema) is not dict:
            raise CapabilityContractError("tool.schema must be a plain JSON object")
        try:
            schema = freeze_bounded_json(self.schema)
        except (BoundedJSONError, RecursionError) as exc:
            raise CapabilityContractError(
                f"tool.schema violates the bounded JSON contract: {exc}"
            ) from exc
        if type(schema) is not MappingProxyType or schema.get("type") != "object":
            raise CapabilityContractError("tool schema must be an object JSON schema")
        object.__setattr__(self, "schema", schema)
        object.__setattr__(
            self,
            "route",
            _enum_value(self.route, ToolRoute, "tool.route"),
        )
        object.__setattr__(
            self,
            "mutation_kind",
            _enum_value(
                self.mutation_kind,
                MutationKind,
                "tool.mutation_kind",
            ),
        )
        raw_medium = self.execution_medium
        if raw_medium is None:
            if self.mutation_kind is MutationKind.GUI:
                medium = ExecutionMedium.GUI
            elif self.mutation_kind is MutationKind.EXTERNAL:
                medium = ExecutionMedium.EXTERNAL
            else:
                # Unclassified non-mutation tools are deliberately not
                # GUI-safe. A control-plane tool must opt in explicitly.
                medium = ExecutionMedium.LOCAL
        else:
            medium = _enum_value(
                raw_medium,
                ExecutionMedium,
                "tool.execution_medium",
            )
        object.__setattr__(self, "execution_medium", medium)
        if self.route is ToolRoute.VISUAL_ACTOR and self.mutation_kind is MutationKind.EXTERNAL:
            raise CapabilityContractError(
                "visual actor tools cannot perform external mutations"
            )
        if self.route is not ToolRoute.VISUAL_ACTOR and self.mutation_kind is MutationKind.GUI:
            raise CapabilityContractError(
                "provider/deterministic tools cannot mutate the GUI"
            )
        if (
            self.mutation_kind is MutationKind.GUI
            and self.execution_medium is not ExecutionMedium.GUI
        ):
            raise CapabilityContractError(
                "GUI mutation tools require the GUI execution medium"
            )
        if (
            self.execution_medium is ExecutionMedium.GUI
            and (
                self.route is not ToolRoute.VISUAL_ACTOR
                or self.mutation_kind is not MutationKind.GUI
            )
        ):
            raise CapabilityContractError(
                "GUI execution medium requires a Visual Actor GUI mutation"
            )
        if (
            self.execution_medium is ExecutionMedium.CONTROL
            and (
                self.route is not ToolRoute.VISUAL_ACTOR
                or self.mutation_kind is not MutationKind.NONE
            )
        ):
            raise CapabilityContractError(
                "control execution medium requires a non-mutating Visual Actor tool"
            )
        if (
            self.mutation_kind is MutationKind.EXTERNAL
            and self.execution_medium is not ExecutionMedium.EXTERNAL
        ):
            raise CapabilityContractError(
                "external mutation tools require the external execution medium"
            )
        if (
            self.execution_medium is ExecutionMedium.EXTERNAL
            and self.mutation_kind is not MutationKind.EXTERNAL
        ):
            raise CapabilityContractError(
                "external execution medium requires an external mutation"
            )
        object.__setattr__(
            self, "required_any_capabilities",
            _capabilities(
                self.required_any_capabilities,
                "tool.required_any_capabilities",
            ),
        )
        prerequisites = _strings(self.prerequisites, "tool.prerequisites")
        unknown = set(prerequisites) - TOOL_PREREQUISITES
        if unknown:
            raise CapabilityContractError(f"unknown prerequisites {sorted(unknown)!r}")
        object.__setattr__(self, "prerequisites", prerequisites)
        if self.route is not ToolRoute.VISUAL_ACTOR and not self.required_any_capabilities:
            raise CapabilityContractError(
                "executor tools require an accepted capability"
            )
        if self.authorization_scope is not None:
            object.__setattr__(
                self, "authorization_scope",
                _identity(self.authorization_scope, "authorization_scope"),
            )
        if self.mutation_kind is MutationKind.EXTERNAL and not self.authorization_scope:
            raise CapabilityContractError("external mutation tool requires authorization_scope")
        if (
            type(self.order) is not int
            or self.order < 0
            or self.order > _MAX_INTEGER
        ):
            raise CapabilityContractError("tool order must be non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "name": self.name,
            "schema": _thaw_frozen_json(self.schema),
            "route": self.route.value,
            "mutation_kind": self.mutation_kind.value,
            "execution_medium": self.execution_medium.value,
            "required_any_capabilities": list(self.required_any_capabilities),
            "prerequisites": list(self.prerequisites),
            "authorization_scope": self.authorization_scope,
            "order": self.order,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ToolSpec":
        fields = (
            "format", "name", "schema", "route", "mutation_kind",
            "execution_medium", "required_any_capabilities", "prerequisites",
            "authorization_scope", "order",
        )
        value = _strict(raw, fields, "ToolSpec")
        _wire_array(
            value["required_any_capabilities"],
            "ToolSpec.required_any_capabilities",
        )
        _wire_array(value["prerequisites"], "ToolSpec.prerequisites")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class ToolCatalog:
    tools: tuple[ToolSpec, ...]
    format: str = TOOL_CATALOG_FORMAT
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.format != TOOL_CATALOG_FORMAT:
            raise CapabilityContractError("unsupported tool catalog format")
        if not isinstance(self.tools, (list, tuple)):
            raise CapabilityContractError("tools must be an array")
        tools = tuple(
            item if type(item) is ToolSpec else ToolSpec.from_dict(item)
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
            {
                "format": TOOL_CATALOG_FORMAT,
                "tools": [item.to_dict() for item in tools],
            },
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
    def from_dict(cls, raw: object) -> "ToolCatalog":
        value = _strict(raw, ("format", "fingerprint", "tools"), "ToolCatalog")
        result = cls(
            format=value["format"],
            tools=_wire_array(value["tools"], "ToolCatalog.tools"),
        )
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
        object.__setattr__(
            self,
            "capability",
            _capability(self.capability, "capability"),
        )
        for name in ("mission_revision", "issued_turn"):
            value = getattr(self, name)
            if type(value) is not int or value < 0 or value > _MAX_INTEGER:
                raise CapabilityContractError(f"{name} must be non-negative integer")
        if (
            type(self.expires_after_turns) is not int
            or self.expires_after_turns < 1
            or self.expires_after_turns > _MAX_INTEGER
        ):
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
        raw = json.dumps(
            {"format": CAPABILITY_GRANT_FORMAT, **material},
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"grant-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"

    @classmethod
    def issue(cls, **values: Any) -> "CapabilityGrant":
        for name in ("mission_revision", "issued_turn"):
            value = values[name]
            if type(value) is not int or value < 0 or value > _MAX_INTEGER:
                raise CapabilityContractError(
                    f"{name} must be non-negative integer"
                )
        expires_after_turns = values["expires_after_turns"]
        if (
            type(expires_after_turns) is not int
            or expires_after_turns < 1
            or expires_after_turns > _MAX_INTEGER
        ):
            raise CapabilityContractError(
                "expires_after_turns must be positive integer"
            )
        material = {
            "mission_id": _identity(values["mission_id"], "mission_id"),
            "run_id": _identity(values["run_id"], "run_id"),
            "capability": _capability(values["capability"], "capability"),
            "obligation_id": _identity(values["obligation_id"], "obligation_id"),
            "mission_revision": values["mission_revision"],
            "issued_turn": values["issued_turn"],
            "expires_after_turns": expires_after_turns,
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
    def from_dict(cls, raw: object) -> "CapabilityGrant":
        fields = (
            "format", "grant_id", "mission_id", "run_id", "capability",
            "obligation_id", "mission_revision", "issued_turn",
            "expires_after_turns", "policy_reason",
        )
        return cls(**_strict(raw, fields, "CapabilityGrant"))


__all__ = [
    "TOOL_SPEC_FORMAT",
    "TOOL_CATALOG_FORMAT",
    "CAPABILITY_GRANT_FORMAT",
    "BROKER_STATE_FORMAT",
    "BROKER_PROJECTION_FORMAT",
    "CapabilityContractError",
    "ToolRoute",
    "ExecutionRoute",
    "ExecutionMedium",
    "MutationKind",
    "PointerDisposition",
    "TOOL_PREREQUISITES",
    "FORBIDDEN_ACTOR_TOOLS",
    "ToolSpec",
    "ToolCatalog",
    "CapabilityGrant",
]
