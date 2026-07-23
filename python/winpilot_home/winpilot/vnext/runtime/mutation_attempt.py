"""One-shot cleanup shell around a Runtime mutation permit."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from .mutation_permits import MutationEnvelope, MutationPermitAuthority


TransitionT = TypeVar("TransitionT")


@dataclass(frozen=True, slots=True)
class PermitAttemptResult(Generic[TransitionT]):
    """Mechanical call and cleanup facts; never a mutation interpretation."""

    permit_id: str
    execute_returned: bool
    transition: TransitionT | None
    controller_error: Exception | None
    pending_permit_revoked: bool
    active_admission_closed: bool


def call_once_with_permit(
    *,
    authority: MutationPermitAuthority,
    envelope: MutationEnvelope,
    turn_index: int,
    still_current: Callable[[], bool],
    execute: Callable[[MutationEnvelope], TransitionT],
) -> PermitAttemptResult[TransitionT]:
    """Issue once, call once, and retire every remaining permit state.

    Permit issuance errors propagate before ``execute``.  Ordinary Controller
    exceptions are returned as local facts; BaseException still propagates
    after the same cleanup.  This function never retries or interprets output.
    """

    if not isinstance(authority, MutationPermitAuthority):
        raise TypeError("authority must be MutationPermitAuthority")
    if not callable(execute):
        raise TypeError("execute must be callable")
    permit_id = authority.issue(
        envelope,
        turn_index=turn_index,
        still_current=still_current,
    )
    transition: TransitionT | None = None
    controller_error: Exception | None = None
    execute_returned = False
    pending_revoked = False
    active_closed = False
    try:
        try:
            transition = execute(envelope)
            execute_returned = True
        except Exception as exc:
            controller_error = exc
    finally:
        try:
            pending_revoked = authority.revoke(envelope.decision_id)
        finally:
            active_closed = authority.finish_admission(envelope.decision_id)
    return PermitAttemptResult(
        permit_id=permit_id,
        execute_returned=execute_returned,
        transition=transition,
        controller_error=controller_error,
        pending_permit_revoked=pending_revoked,
        active_admission_closed=active_closed,
    )


__all__ = ["PermitAttemptResult", "call_once_with_permit"]
