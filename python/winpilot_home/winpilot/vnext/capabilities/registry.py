"""Trusted local owners for broad vNext capability availability.

This registry describes implementations that are present in the runtime. It
does not claim that a task succeeded, select a GUI route, or accept model input.
Provider, adapter and MCP owners must register separately when they are live.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contracts import CapabilityContractError, _identity
from ..capability_names import canonical_capabilities


@dataclass(frozen=True, slots=True)
class CapabilityOwner:
    owner_id: str
    capabilities: tuple[str, ...]
    live: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_id", _identity(self.owner_id, "owner_id"))
        object.__setattr__(
            self, "capabilities", canonical_capabilities(self.capabilities)
        )
        if type(self.live) is not bool:
            raise CapabilityContractError("capability owner live must be boolean")


class RuntimeCapabilityRegistry:
    """Immutable owner registry; snapshots are deterministic and auditable."""

    def __init__(self, owners: Iterable[CapabilityOwner]) -> None:
        normalized = tuple(owners)
        owner_ids = tuple(item.owner_id for item in normalized)
        if len(owner_ids) != len(set(owner_ids)):
            raise CapabilityContractError("capability owner ids must be unique")
        self._owners = tuple(sorted(normalized, key=lambda item: item.owner_id))

    @property
    def owners(self) -> tuple[CapabilityOwner, ...]:
        return self._owners

    def snapshot(self) -> tuple[str, ...]:
        return tuple(sorted({
            capability
            for owner in self._owners if owner.live
            for capability in owner.capabilities
        }))


def legacy_local_capability_registry() -> RuntimeCapabilityRegistry:
    """Capabilities backed by current local owners, independent of task text."""
    return RuntimeCapabilityRegistry((
        CapabilityOwner(
            "frame-controller-raw-device",
            (
                "application.resolve",
                "external.wait",
                "pointer.dynamic",
                "visual.draw",
                "visual.interact",
                "visual.read_text",
            ),
        ),
        CapabilityOwner("artifact-store", ("artifact.capture", "artifact.read")),
        CapabilityOwner(
            "legacy-gui-communication",
            (
                "communication.bind_recipient",
                "communication.send_artifact",
                "communication.send_text",
                "communication.verify_delivery",
            ),
        ),
    ))


__all__ = [
    "CapabilityOwner",
    "RuntimeCapabilityRegistry",
    "legacy_local_capability_registry",
]
