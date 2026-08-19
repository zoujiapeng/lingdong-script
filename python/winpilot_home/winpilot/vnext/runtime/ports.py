"""Injected ports for one minimal Mission Runtime turn."""
from __future__ import annotations

from threading import Event
from typing import Any, Mapping, Protocol

from ..actor import ActorDecisionEnvelope
from .contracts import (
    ActorTurnInput,
    ControllerTransition,
    RuntimeSnapshot,
)
from .task_state import ActorTurnDecision


class ActorPort(Protocol):
    """Return one identity-free proposal from one frozen Actor turn."""

    def propose(
        self,
        turn: ActorTurnInput,
        *,
        image_b64: str,
        image_media_type: str,
        submitted_size: tuple[int, int],
        stop_event: Event | None = None,
    ) -> ActorTurnDecision | Mapping[str, Any]: ...


class RuntimeStatePort(Protocol):
    """Read an already-bound target and the existing frame authority."""

    def observe(
        self,
    ) -> RuntimeSnapshot: ...


class ControllerPort(Protocol):
    """Execute one admitted envelope through an injected desktop owner."""

    def execute(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        stop_event: Event | None = None,
    ) -> ControllerTransition: ...


__all__ = [
    "ActorPort",
    "RuntimeStatePort",
    "ControllerPort",
]
