"""Immutable runtime-state and projection contracts for the P3 Broker."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from .contracts import (
    BROKER_PROJECTION_FORMAT,
    BROKER_STATE_FORMAT,
    CapabilityContractError,
    CapabilityGrant,
    ExecutionRoute,
    PointerDisposition,
    ToolRoute,
    FORBIDDEN_ACTOR_TOOLS,
    _MAX_INTEGER,
    _SHA,
    _TOOL,
    _boolean,
    _capabilities,
    _enum_value,
    _identity,
    _strict,
    _strings,
    _wire_array,
)


@dataclass(frozen=True, slots=True)
class TargetState:
    binding_id: str | None = None
    hwnd: int | None = None
    epoch: int | None = None
    live: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "live", _boolean(self.live, "target.live"))
        if self.binding_id is not None:
            object.__setattr__(self, "binding_id", _identity(self.binding_id, "binding_id"))
        if self.hwnd is not None and (
            type(self.hwnd) is not int
            or self.hwnd <= 0
            or self.hwnd > _MAX_INTEGER
        ):
            raise CapabilityContractError("target hwnd must be positive integer")
        if self.epoch is not None and (
            type(self.epoch) is not int
            or self.epoch < 0
            or self.epoch > _MAX_INTEGER
        ):
            raise CapabilityContractError("target epoch must be non-negative integer")
        if self.live and (
            self.binding_id is None or self.hwnd is None or self.epoch is None
        ):
            raise CapabilityContractError("live target requires binding_id, hwnd, and epoch")

    def to_dict(self) -> dict[str, Any]:
        return {"binding_id": self.binding_id, "hwnd": self.hwnd, "epoch": self.epoch, "live": self.live}

    @classmethod
    def from_dict(cls, raw: object) -> "TargetState":
        return cls(**_strict(raw, ("binding_id", "hwnd", "epoch", "live"), "TargetState"))


@dataclass(frozen=True, slots=True)
class AuthoritativeFrameState:
    frame_id: int | None = None
    hwnd: int | None = None
    epoch: int | None = None
    authoritative: bool = False
    fresh: bool = False
    scope: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "authoritative", _boolean(self.authoritative, "frame.authoritative")
        )
        object.__setattr__(self, "fresh", _boolean(self.fresh, "frame.fresh"))
        for name in ("frame_id", "epoch"):
            value = getattr(self, name)
            minimum = 1 if name == "frame_id" else 0
            if value is not None and (
                type(value) is not int
                or value < minimum
                or value > _MAX_INTEGER
            ):
                qualifier = "positive" if minimum else "non-negative"
                raise CapabilityContractError(
                    f"frame {name} must be {qualifier} integer"
                )
        if self.hwnd is not None and (
            type(self.hwnd) is not int
            or self.hwnd <= 0
            or self.hwnd > _MAX_INTEGER
        ):
            raise CapabilityContractError("frame hwnd must be positive integer")
        if self.scope is not None:
            object.__setattr__(self, "scope", _identity(self.scope, "frame.scope"))
        if (self.authoritative or self.fresh) and (
            self.frame_id is None
            or self.hwnd is None
            or self.epoch is None
            or self.scope is None
        ):
            raise CapabilityContractError("authoritative/fresh frame requires complete identity")

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id, "hwnd": self.hwnd, "epoch": self.epoch,
            "authoritative": self.authoritative, "fresh": self.fresh,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "AuthoritativeFrameState":
        fields = ("frame_id", "hwnd", "epoch", "authoritative", "fresh", "scope")
        return cls(**_strict(raw, fields, "AuthoritativeFrameState"))


@dataclass(frozen=True, slots=True)
class PointerLeaseState:
    disposition: PointerDisposition | str = PointerDisposition.UNKNOWN
    lease_id: str | None = None
    physically_owned: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "disposition",
            _enum_value(
                self.disposition,
                PointerDisposition,
                "pointer.disposition",
            ),
        )
        object.__setattr__(
            self,
            "physically_owned",
            _boolean(self.physically_owned, "pointer.physically_owned"),
        )
        if self.lease_id is not None:
            object.__setattr__(self, "lease_id", _identity(self.lease_id, "lease_id"))
        if self.disposition is PointerDisposition.HELD and not self.lease_id:
            raise CapabilityContractError("held pointer requires lease_id")
        if self.disposition is PointerDisposition.RELEASED and self.lease_id is not None:
            raise CapabilityContractError("released pointer cannot retain lease_id")
        if self.physically_owned and self.disposition is not PointerDisposition.HELD:
            raise CapabilityContractError("only a held pointer may be physically owned")

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "lease_id": self.lease_id,
            "physically_owned": self.physically_owned,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "PointerLeaseState":
        fields = ("disposition", "lease_id", "physically_owned")
        return cls(**_strict(raw, fields, "PointerLeaseState"))


@dataclass(frozen=True, slots=True)
class ArtifactState:
    artifact_id: str
    run_id: str
    content_sha256: str
    binding_name: str
    sendable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "sendable", _boolean(self.sendable, "artifact.sendable"))
        for name in ("artifact_id", "run_id", "binding_name"):
            object.__setattr__(self, name, _identity(getattr(self, name), name))
        if type(self.content_sha256) is not str:
            raise CapabilityContractError("artifact content_sha256 must be a string")
        if not _SHA.fullmatch(self.content_sha256):
            raise CapabilityContractError("artifact content_sha256 is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id, "run_id": self.run_id,
            "content_sha256": self.content_sha256, "binding_name": self.binding_name,
            "sendable": self.sendable,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ArtifactState":
        fields = ("artifact_id", "run_id", "content_sha256", "binding_name", "sendable")
        return cls(**_strict(raw, fields, "ArtifactState"))


@dataclass(frozen=True, slots=True)
class ExecutorOffer:
    executor_id: str
    route: ToolRoute | str
    supported_capabilities: tuple[str, ...]
    tool_names: tuple[str, ...]
    available: bool
    live: bool
    mutates_desktop: bool = False
    authorization_scopes: tuple[str, ...] = ()
    receipt_kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "executor_id", _identity(self.executor_id, "executor_id"))
        route = _enum_value(self.route, ToolRoute, "offer.route")
        if route is ToolRoute.VISUAL_ACTOR:
            raise CapabilityContractError("executor offer cannot impersonate visual actor")
        object.__setattr__(self, "route", route)
        for name in ("available", "live", "mutates_desktop"):
            object.__setattr__(self, name, _boolean(getattr(self, name), f"offer.{name}"))
        object.__setattr__(
            self,
            "supported_capabilities",
            _capabilities(
                self.supported_capabilities,
                "offer.supported_capabilities",
            ),
        )
        object.__setattr__(self, "tool_names", _strings(self.tool_names, "offer.tool_names"))
        object.__setattr__(self, "authorization_scopes", _strings(self.authorization_scopes, "offer.authorization_scopes"))
        object.__setattr__(self, "receipt_kinds", _strings(self.receipt_kinds, "offer.receipt_kinds"))
        if self.mutates_desktop:
            raise CapabilityContractError("provider/deterministic offer cannot mutate desktop")

    def to_dict(self) -> dict[str, Any]:
        return {
            "executor_id": self.executor_id, "route": self.route.value,
            "supported_capabilities": list(self.supported_capabilities),
            "tool_names": list(self.tool_names), "available": self.available,
            "live": self.live, "mutates_desktop": self.mutates_desktop,
            "authorization_scopes": list(self.authorization_scopes),
            "receipt_kinds": list(self.receipt_kinds),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ExecutorOffer":
        fields = (
            "executor_id", "route", "supported_capabilities", "tool_names",
            "available", "live", "mutates_desktop", "authorization_scopes", "receipt_kinds",
        )
        value = _strict(raw, fields, "ExecutorOffer")
        for name in (
            "supported_capabilities",
            "tool_names",
            "authorization_scopes",
            "receipt_kinds",
        ):
            _wire_array(value[name], f"ExecutorOffer.{name}")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class BrokerRuntimeState:
    turn_index: int
    available_capabilities: tuple[str, ...]
    target: TargetState = field(default_factory=TargetState)
    frame: AuthoritativeFrameState = field(default_factory=AuthoritativeFrameState)
    pointer: PointerLeaseState = field(default_factory=PointerLeaseState)
    artifacts: tuple[ArtifactState, ...] = ()
    executor_offers: tuple[ExecutorOffer, ...] = ()
    authorization_scopes: tuple[str, ...] = ()
    runtime_healthy: bool = False
    paused: bool = False
    canceled: bool = False
    format: str = BROKER_STATE_FORMAT
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            self.format != BROKER_STATE_FORMAT
            or type(self.turn_index) is not int
            or self.turn_index < 0
            or self.turn_index > _MAX_INTEGER
        ):
            raise CapabilityContractError("invalid broker runtime state")
        object.__setattr__(
            self,
            "available_capabilities",
            _capabilities(
                self.available_capabilities,
                "available_capabilities",
            ),
        )
        object.__setattr__(
            self,
            "target",
            self.target
            if type(self.target) is TargetState
            else TargetState.from_dict(self.target),
        )
        object.__setattr__(
            self,
            "frame",
            self.frame
            if type(self.frame) is AuthoritativeFrameState
            else AuthoritativeFrameState.from_dict(self.frame),
        )
        object.__setattr__(
            self,
            "pointer",
            self.pointer
            if type(self.pointer) is PointerLeaseState
            else PointerLeaseState.from_dict(self.pointer),
        )
        if not isinstance(self.artifacts, (list, tuple)):
            raise CapabilityContractError("artifacts must be an array")
        artifacts = tuple(
            item if type(item) is ArtifactState else ArtifactState.from_dict(item)
            for item in self.artifacts
        )
        artifact_keys = tuple(item.binding_name for item in artifacts)
        if len(artifact_keys) != len(set(artifact_keys)):
            raise CapabilityContractError("artifact binding names must be unique")
        object.__setattr__(
            self,
            "artifacts",
            tuple(sorted(artifacts, key=lambda item: (item.binding_name, item.artifact_id))),
        )
        if not isinstance(self.executor_offers, (list, tuple)):
            raise CapabilityContractError("executor_offers must be an array")
        executor_offers = tuple(
            item
            if type(item) is ExecutorOffer
            else ExecutorOffer.from_dict(item)
            for item in self.executor_offers
        )
        executor_ids = tuple(item.executor_id for item in executor_offers)
        if len(executor_ids) != len(set(executor_ids)):
            raise CapabilityContractError("executor offer ids must be unique")
        object.__setattr__(
            self,
            "executor_offers",
            tuple(sorted(executor_offers, key=lambda item: item.executor_id)),
        )
        object.__setattr__(self, "authorization_scopes", _strings(self.authorization_scopes, "authorization_scopes"))
        for name in ("runtime_healthy", "paused", "canceled"):
            object.__setattr__(self, name, _boolean(getattr(self, name), name))
        canonical = json.dumps(
            self._content_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        object.__setattr__(
            self,
            "fingerprint",
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "format": self.format, "turn_index": self.turn_index,
            "available_capabilities": list(self.available_capabilities),
            "target": self.target.to_dict(), "frame": self.frame.to_dict(),
            "pointer": self.pointer.to_dict(),
            "artifacts": [item.to_dict() for item in self.artifacts],
            "executor_offers": [item.to_dict() for item in self.executor_offers],
            "authorization_scopes": list(self.authorization_scopes),
            "runtime_healthy": self.runtime_healthy, "paused": self.paused,
            "canceled": self.canceled,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "fingerprint": self.fingerprint}

    @classmethod
    def from_dict(cls, raw: object) -> "BrokerRuntimeState":
        fields = (
            "format", "turn_index", "available_capabilities",
            "target", "frame", "pointer", "artifacts",
            "executor_offers", "authorization_scopes", "runtime_healthy",
            "paused", "canceled", "fingerprint",
        )
        value = _strict(raw, fields, "BrokerRuntimeState")
        fingerprint = value.pop("fingerprint")
        for name in (
            "available_capabilities",
            "artifacts",
            "executor_offers",
            "authorization_scopes",
        ):
            _wire_array(value[name], f"BrokerRuntimeState.{name}")
        value["target"] = TargetState.from_dict(value["target"])
        value["frame"] = AuthoritativeFrameState.from_dict(value["frame"])
        value["pointer"] = PointerLeaseState.from_dict(value["pointer"])
        result = cls(**value)
        if fingerprint != result.fingerprint:
            raise CapabilityContractError("broker runtime state fingerprint mismatch")
        return result


def _broker_projection_id(
    *,
    mission_id: str,
    run_id: str,
    catalog_fingerprint: str,
    profile_id: str,
    state_fingerprint: str,
    mission_revision: int,
    obligation_id: str,
    turn_index: int,
    execution_route: ExecutionRoute,
    executor_id: str | None,
    target_binding_id: str | None,
    frame_id: int | None,
    frame_scope: str | None,
    pointer_lease_id: str | None,
    tool_names: tuple[str, ...],
    active_grants: tuple[CapabilityGrant, ...],
    missing_capabilities: tuple[str, ...],
    projection_reasons: tuple[str, ...],
) -> str:
    material = {
        "format": BROKER_PROJECTION_FORMAT,
        "mission_id": mission_id,
        "run_id": run_id,
        "catalog_fingerprint": catalog_fingerprint,
        "profile_id": profile_id,
        "state_fingerprint": state_fingerprint,
        "mission_revision": mission_revision,
        "obligation_id": obligation_id,
        "turn_index": turn_index,
        "execution_route": execution_route.value,
        "executor_id": executor_id,
        "target_binding_id": target_binding_id,
        "frame_id": frame_id,
        "frame_scope": frame_scope,
        "pointer_lease_id": pointer_lease_id,
        "tool_names": list(tool_names),
        "active_grant_ids": [item.grant_id for item in active_grants],
        "missing_capabilities": list(missing_capabilities),
        "projection_reasons": list(projection_reasons),
    }
    canonical = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "projection-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class BrokerProjection:
    projection_id: str
    mission_id: str
    run_id: str
    catalog_fingerprint: str
    profile_id: str
    state_fingerprint: str
    mission_revision: int
    obligation_id: str
    turn_index: int
    execution_route: ExecutionRoute | str
    tool_names: tuple[str, ...]
    active_grants: tuple[CapabilityGrant, ...]
    missing_capabilities: tuple[str, ...]
    projection_reasons: tuple[str, ...]
    executor_id: str | None = None
    target_binding_id: str | None = None
    frame_id: int | None = None
    frame_scope: str | None = None
    pointer_lease_id: str | None = None
    format: str = BROKER_PROJECTION_FORMAT

    def __post_init__(self) -> None:
        if self.format != BROKER_PROJECTION_FORMAT:
            raise CapabilityContractError("unsupported BrokerProjection format")
        object.__setattr__(self, "projection_id", _identity(self.projection_id, "projection_id"))
        object.__setattr__(self, "mission_id", _identity(self.mission_id, "mission_id"))
        object.__setattr__(self, "run_id", _identity(self.run_id, "run_id"))
        if type(self.catalog_fingerprint) is not str:
            raise CapabilityContractError("catalog_fingerprint must be a string")
        if not _SHA.fullmatch(self.catalog_fingerprint):
            raise CapabilityContractError("catalog_fingerprint must be SHA-256")
        object.__setattr__(self, "profile_id", _identity(self.profile_id, "profile_id"))
        if type(self.state_fingerprint) is not str:
            raise CapabilityContractError("state_fingerprint must be a string")
        if not _SHA.fullmatch(self.state_fingerprint):
            raise CapabilityContractError("state_fingerprint must be SHA-256")
        object.__setattr__(self, "obligation_id", _identity(self.obligation_id, "obligation_id"))
        if (
            type(self.mission_revision) is not int
            or self.mission_revision < 0
            or self.mission_revision > _MAX_INTEGER
        ):
            raise CapabilityContractError("mission_revision must be non-negative")
        if (
            type(self.turn_index) is not int
            or self.turn_index < 0
            or self.turn_index > _MAX_INTEGER
        ):
            raise CapabilityContractError("turn_index must be non-negative")
        object.__setattr__(
            self,
            "execution_route",
            _enum_value(
                self.execution_route,
                ExecutionRoute,
                "execution_route",
            ),
        )
        tool_names = _strings(
            self.tool_names, "tool_names", sorted_output=False
        )
        if any(
            not _TOOL.fullmatch(name) or name in FORBIDDEN_ACTOR_TOOLS
            for name in tool_names
        ):
            raise CapabilityContractError(
                "tool_names contains an invalid or forbidden tool name"
            )
        object.__setattr__(self, "tool_names", tool_names)
        if not isinstance(self.active_grants, (list, tuple)):
            raise CapabilityContractError("active_grants must be an array")
        grants = tuple(
            item
            if type(item) is CapabilityGrant
            else CapabilityGrant.from_dict(item)
            for item in self.active_grants
        )
        if len({item.grant_id for item in grants}) != len(grants):
            raise CapabilityContractError("active grants must be unique")
        if any(
            not item.valid_for(
                mission_id=self.mission_id,
                run_id=self.run_id,
                obligation_id=self.obligation_id,
                mission_revision=self.mission_revision,
                turn_index=self.turn_index,
            )
            for item in grants
        ):
            raise CapabilityContractError(
                "active grant scope and lifetime must match projection"
            )
        grants = tuple(sorted(grants, key=lambda item: item.grant_id))
        object.__setattr__(self, "active_grants", grants)
        object.__setattr__(
            self,
            "missing_capabilities",
            _capabilities(
                self.missing_capabilities,
                "missing_capabilities",
            ),
        )
        object.__setattr__(self, "projection_reasons", _strings(self.projection_reasons, "projection_reasons", sorted_output=False))
        for name in (
            "executor_id",
            "target_binding_id",
            "frame_scope",
            "pointer_lease_id",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _identity(value, name))
        if self.frame_id is not None and (
            type(self.frame_id) is not int
            or self.frame_id <= 0
            or self.frame_id > _MAX_INTEGER
        ):
            raise CapabilityContractError("frame_id must be a positive integer")
        if (self.frame_id is None) != (self.frame_scope is None):
            raise CapabilityContractError(
                "frame_id and frame_scope must be present together"
            )
        if self.frame_id is not None and self.target_binding_id is None:
            raise CapabilityContractError(
                "frame identity requires a target binding"
            )
        executor_route = self.execution_route in {
            ExecutionRoute.DETERMINISTIC,
            ExecutionRoute.PROVIDER,
        }
        if executor_route != (self.executor_id is not None):
            raise CapabilityContractError(
                "executor route and executor_id must be present together"
            )
        if self.execution_route is not ExecutionRoute.BLOCKED and not self.tool_names:
            raise CapabilityContractError(
                "non-blocked projection requires at least one tool"
            )
        expected_projection_id = _broker_projection_id(
            mission_id=self.mission_id,
            run_id=self.run_id,
            catalog_fingerprint=self.catalog_fingerprint,
            profile_id=self.profile_id,
            state_fingerprint=self.state_fingerprint,
            mission_revision=self.mission_revision,
            obligation_id=self.obligation_id,
            turn_index=self.turn_index,
            execution_route=self.execution_route,
            executor_id=self.executor_id,
            target_binding_id=self.target_binding_id,
            frame_id=self.frame_id,
            frame_scope=self.frame_scope,
            pointer_lease_id=self.pointer_lease_id,
            tool_names=self.tool_names,
            active_grants=self.active_grants,
            missing_capabilities=self.missing_capabilities,
            projection_reasons=self.projection_reasons,
        )
        if self.projection_id != expected_projection_id:
            raise CapabilityContractError(
                "projection_id does not identify the canonical projection"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format, "projection_id": self.projection_id,
            "mission_id": self.mission_id, "run_id": self.run_id,
            "catalog_fingerprint": self.catalog_fingerprint,
            "profile_id": self.profile_id,
            "state_fingerprint": self.state_fingerprint,
            "mission_revision": self.mission_revision, "obligation_id": self.obligation_id,
            "turn_index": self.turn_index,
            "execution_route": self.execution_route.value, "executor_id": self.executor_id,
            "target_binding_id": self.target_binding_id,
            "frame_id": self.frame_id, "frame_scope": self.frame_scope,
            "pointer_lease_id": self.pointer_lease_id,
            "tool_names": list(self.tool_names),
            "active_grants": [item.to_dict() for item in self.active_grants],
            "missing_capabilities": list(self.missing_capabilities),
            "projection_reasons": list(self.projection_reasons),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "BrokerProjection":
        fields = (
            "format", "projection_id", "mission_id", "run_id",
            "catalog_fingerprint", "profile_id", "mission_revision", "obligation_id",
            "state_fingerprint",
            "turn_index", "execution_route", "executor_id", "target_binding_id",
            "frame_id", "frame_scope", "pointer_lease_id",
            "tool_names", "active_grants", "missing_capabilities", "projection_reasons",
        )
        value = _strict(raw, fields, "BrokerProjection")
        for name in (
            "tool_names",
            "active_grants",
            "missing_capabilities",
            "projection_reasons",
        ):
            _wire_array(value[name], f"BrokerProjection.{name}")
        value["active_grants"] = tuple(
            CapabilityGrant.from_dict(item) for item in value["active_grants"]
        )
        return cls(**value)


__all__ = [
    "TargetState",
    "AuthoritativeFrameState",
    "PointerLeaseState",
    "ArtifactState",
    "ExecutorOffer",
    "BrokerRuntimeState",
    "BrokerProjection",
]
