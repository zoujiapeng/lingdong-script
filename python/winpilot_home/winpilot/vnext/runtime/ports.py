"""Injected P4 ports; no port owns mission truth or a second GUI executor."""
from __future__ import annotations

from threading import Event
from typing import Any, Mapping, Protocol, TypeAlias

from ..actor import ActorDecisionEnvelope, ActorTurnInput, ImageRegion
from ..mission import MissionProjection
from ..targeting import TargetDiscoverySnapshot
from .contracts import ControllerTransition, RuntimeSnapshot
from .target_activation import (
    TargetActivationEnvelope,
    TargetActivationTransition,
)


ControllerEnvelope: TypeAlias = (
    ActorDecisionEnvelope | TargetActivationEnvelope
)
ControllerResult: TypeAlias = (
    ControllerTransition | TargetActivationTransition
)


class ActorPort(Protocol):
    def propose(
        self,
        turn: ActorTurnInput,
        *,
        image_b64: str,
        submitted_size: tuple[int, int],
        stop_event: Event | None = None,
    ) -> Mapping[str, Any]: ...


class RuntimeStatePort(Protocol):
    def observe(
        self,
        projection: MissionProjection,
        *,
        turn_index: int,
        image_region: ImageRegion | None = None,
    ) -> RuntimeSnapshot: ...

    def current(
        self,
        projection: MissionProjection,
        *,
        turn_index: int,
    ) -> RuntimeSnapshot: ...


class TargetDiscoveryPort(Protocol):
    """Read-only target facts; it never selects or activates a candidate."""

    def discover(
        self,
        query: str,
        *,
        stop_event: Event | None = None,
    ) -> TargetDiscoverySnapshot: ...


class ControllerPort(Protocol):
    def execute(
        self,
        envelope: ControllerEnvelope,
        *,
        stop_event: Event | None = None,
    ) -> ControllerResult: ...


class PointerCleanupPort(Protocol):
    def release_pointer(self, lease_id: str, *, reason: str) -> bool: ...


__all__ = [
    "ActorPort",
    "ControllerEnvelope",
    "RuntimeStatePort",
    "TargetDiscoveryPort",
    "ControllerPort",
    "ControllerResult",
    "PointerCleanupPort",
]
