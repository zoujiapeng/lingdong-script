"""Read-only P5a verification ports."""
from __future__ import annotations

from threading import Event
from typing import Protocol

from .contracts import ObligationVerificationRequest, VerifierAssessment


class ObligationVerifierPort(Protocol):
    """Assess one frozen obligation without mutating Mission or the desktop."""

    def verify(
        self,
        request: ObligationVerificationRequest,
        *,
        stop_event: Event | None = None,
    ) -> VerifierAssessment: ...


__all__ = ["ObligationVerifierPort"]
