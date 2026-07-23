"""Injected P4 ports; no port owns mission truth or a second GUI executor."""
from __future__ import annotations

from threading import Event
from typing import Mapping, Protocol, Any

from ..actor import ActorDecisionEnvelope, ActorTurnInput, ImageRegion
from ..mission import MissionProjection
from ..targeting import TargetDiscoverySnapshot
from .contracts import ControllerTransition, RuntimeSnapshot


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
        envelope: ActorDecisionEnvelope,
        *,
        stop_event: Event | None = None,
    ) -> ControllerTransition: ...


class PointerCleanupPort(Protocol):
    def release_pointer(self, lease_id: str, *, reason: str) -> bool: ...


__all__ = [
    "ActorPort",
    "RuntimeStatePort",
    "TargetDiscoveryPort",
    "ControllerPort",
    "PointerCleanupPort",
]
