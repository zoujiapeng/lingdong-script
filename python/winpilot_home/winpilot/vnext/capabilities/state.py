"""Immutable runtime-state and projection contracts for the P3 Broker."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import (
    BROKER_PROJECTION_FORMAT,
    BROKER_STATE_FORMAT,
    CapabilityContractError,
    CapabilityGrant,
    ExecutionRoute,
    PointerDisposition,
    ToolRoute,
    _SHA,
    _boolean,
    _identity,
    _strict,
    _strings,
)
from .names import canonical_capabilities


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
        if self.hwnd is not None and (type(self.hwnd) is not int or self.hwnd <= 0):
            raise CapabilityContractError("target hwnd must be positive integer")
        if self.epoch is not None and (type(self.epoch) is not int or self.epoch < 0):
            raise CapabilityContractError("target epoch must be non-negative integer")
        if self.live and (
            self.binding_id is None or self.hwnd is None or self.epoch is None
        ):
            raise CapabilityContractError("live target requires binding_id, hwnd, and epoch")

    def to_dict(self) -> dict[str, Any]:
        return {"binding_id": self.binding_id, "hwnd": self.hwnd, "epoch": self.epoch, "live": self.live}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TargetState":
        return cls(**_strict(raw, ("binding_id", "hwnd", "epoch", "live"), "TargetState"))


@dataclass(frozen=True, slots=True)
class AuthoritativeFrameState:
    frame_id: int | None = None
    hwnd: int | None = None
    epoch: int | None = None
    authoritative: bool = False
    fresh: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "authoritative", _boolean(self.authoritative, "frame.authoritative")
        )
        object.__setattr__(self, "fresh", _boolean(self.fresh, "frame.fresh"))
        for name in ("frame_id", "epoch"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise CapabilityContractError(f"frame {name} must be non-negative integer")
        if self.hwnd is not None and (type(self.hwnd) is not int or self.hwnd <= 0):
            raise CapabilityContractError("frame hwnd must be positive integer")
        if (self.authoritative or self.fresh) and (
            self.frame_id is None or self.hwnd is None or self.epoch is None
        ):
            raise CapabilityContractError("authoritative/fresh frame requires complete identity")

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id, "hwnd": self.hwnd, "epoch": self.epoch,
            "authoritative": self.authoritative, "fresh": self.fresh,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AuthoritativeFrameState":
        fields = ("frame_id", "hwnd", "epoch", "authoritative", "fresh")
        return cls(**_strict(raw, fields, "AuthoritativeFrameState"))


@dataclass(frozen=True, slots=True)
class PointerLeaseState:
    disposition: PointerDisposition | str = PointerDisposition.RELEASED
    lease_id: str | None = None
    physically_owned: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "disposition", PointerDisposition(self.disposition))
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
    def from_dict(cls, raw: Mapping[str, Any]) -> "PointerLeaseState":
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
        digest = self.content_sha256.lower()
        if not _SHA.fullmatch(digest):
            raise CapabilityContractError("artifact content_sha256 is invalid")
        object.__setattr__(self, "content_sha256", digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id, "run_id": self.run_id,
            "content_sha256": self.content_sha256, "binding_name": self.binding_name,
            "sendable": self.sendable,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ArtifactState":
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
        route = ToolRoute(self.route)
        if route is ToolRoute.VISUAL_ACTOR:
            raise CapabilityContractError("executor offer cannot impersonate visual actor")
        object.__setattr__(self, "route", route)
        for name in ("available", "live", "mutates_desktop"):
            object.__setattr__(self, name, _boolean(getattr(self, name), f"offer.{name}"))
        object.__setattr__(self, "supported_capabilities", canonical_capabilities(self.supported_capabilities))
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
    def from_dict(cls, raw: Mapping[str, Any]) -> "ExecutorOffer":
        fields = (
            "executor_id", "route", "supported_capabilities", "tool_names",
            "available", "live", "mutates_desktop", "authorization_scopes", "receipt_kinds",
        )
        return cls(**_strict(raw, fields, "ExecutorOffer"))


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
    runtime_healthy: bool = True
    paused: bool = False
    canceled: bool = False
    format: str = BROKER_STATE_FORMAT

    def __post_init__(self) -> None:
        if self.format != BROKER_STATE_FORMAT or type(self.turn_index) is not int or self.turn_index < 0:
            raise CapabilityContractError("invalid broker runtime state")
        object.__setattr__(self, "available_capabilities", canonical_capabilities(self.available_capabilities))
        object.__setattr__(
            self,
            "target",
            self.target if isinstance(self.target, TargetState) else TargetState.from_dict(self.target),
        )
        object.__setattr__(
            self,
            "frame",
            self.frame
            if isinstance(self.frame, AuthoritativeFrameState)
            else AuthoritativeFrameState.from_dict(self.frame),
        )
        object.__setattr__(
            self,
            "pointer",
            self.pointer
            if isinstance(self.pointer, PointerLeaseState)
            else PointerLeaseState.from_dict(self.pointer),
        )
        object.__setattr__(self, "artifacts", tuple(
            item if isinstance(item, ArtifactState) else ArtifactState.from_dict(item)
            for item in self.artifacts
        ))
        object.__setattr__(self, "executor_offers", tuple(
            item if isinstance(item, ExecutorOffer) else ExecutorOffer.from_dict(item)
            for item in self.executor_offers
        ))
        executor_ids = tuple(item.executor_id for item in self.executor_offers)
        if len(executor_ids) != len(set(executor_ids)):
            raise CapabilityContractError("executor offer ids must be unique")
        object.__setattr__(self, "authorization_scopes", _strings(self.authorization_scopes, "authorization_scopes"))
        for name in ("runtime_healthy", "paused", "canceled"):
            object.__setattr__(self, name, _boolean(getattr(self, name), name))

    def to_dict(self) -> dict[str, Any]:
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

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "BrokerRuntimeState":
        fields = (
            "format", "turn_index", "available_capabilities", "target", "frame",
            "pointer", "artifacts", "executor_offers", "authorization_scopes",
            "runtime_healthy", "paused", "canceled",
        )
        value = _strict(raw, fields, "BrokerRuntimeState")
        value["target"] = TargetState.from_dict(value["target"])
        value["frame"] = AuthoritativeFrameState.from_dict(value["frame"])
        value["pointer"] = PointerLeaseState.from_dict(value["pointer"])
        return cls(**value)


@dataclass(frozen=True, slots=True)
class BrokerProjection:
    projection_id: str
    mission_revision: int
    obligation_id: str
    execution_route: ExecutionRoute | str
    tool_names: tuple[str, ...]
    active_grants: tuple[CapabilityGrant, ...]
    missing_capabilities: tuple[str, ...]
    projection_reasons: tuple[str, ...]
    executor_id: str | None = None
    target_binding_id: str | None = None
    pointer_lease_id: str | None = None
    format: str = BROKER_PROJECTION_FORMAT

    def __post_init__(self) -> None:
        if self.format != BROKER_PROJECTION_FORMAT:
            raise CapabilityContractError("unsupported BrokerProjection format")
        object.__setattr__(self, "projection_id", _identity(self.projection_id, "projection_id"))
        object.__setattr__(self, "obligation_id", _identity(self.obligation_id, "obligation_id"))
        if type(self.mission_revision) is not int or self.mission_revision < 0:
            raise CapabilityContractError("mission_revision must be non-negative")
        object.__setattr__(self, "execution_route", ExecutionRoute(self.execution_route))
        object.__setattr__(self, "tool_names", _strings(self.tool_names, "tool_names", sorted_output=False))
        grants = tuple(
            item if isinstance(item, CapabilityGrant) else CapabilityGrant.from_dict(item)
            for item in self.active_grants
        )
        if len({item.grant_id for item in grants}) != len(grants):
            raise CapabilityContractError("active grants must be unique")
        if any(
            item.obligation_id != self.obligation_id
            or item.mission_revision != self.mission_revision
            for item in grants
        ):
            raise CapabilityContractError(
                "active grant scope must match projection obligation and revision"
            )
        object.__setattr__(self, "active_grants", grants)
        object.__setattr__(self, "missing_capabilities", canonical_capabilities(self.missing_capabilities))
        object.__setattr__(self, "projection_reasons", _strings(self.projection_reasons, "projection_reasons", sorted_output=False))
        for name in ("executor_id", "target_binding_id", "pointer_lease_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _identity(value, name))

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format, "projection_id": self.projection_id,
            "mission_revision": self.mission_revision, "obligation_id": self.obligation_id,
            "execution_route": self.execution_route.value, "executor_id": self.executor_id,
            "target_binding_id": self.target_binding_id, "pointer_lease_id": self.pointer_lease_id,
            "tool_names": list(self.tool_names),
            "active_grants": [item.to_dict() for item in self.active_grants],
            "missing_capabilities": list(self.missing_capabilities),
            "projection_reasons": list(self.projection_reasons),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "BrokerProjection":
        fields = (
            "format", "projection_id", "mission_revision", "obligation_id",
            "execution_route", "executor_id", "target_binding_id", "pointer_lease_id",
            "tool_names", "active_grants", "missing_capabilities", "projection_reasons",
        )
        value = _strict(raw, fields, "BrokerProjection")
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
