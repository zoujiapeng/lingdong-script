"""Mechanical call-once shell around one Runtime mutation permit."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from ..actor import ActorDecisionEnvelope
from .contracts import RuntimeCommitIdentity
from .mutation_permits import MutationPermitAuthority


TransitionT = TypeVar("TransitionT")


@dataclass(frozen=True, slots=True)
class PermitAttemptResult(Generic[TransitionT]):
    """Call and cleanup facts only; this value never interprets mutation."""

    permit_id: str
    execute_call_count: int
    transition: TransitionT | None
    controller_error: Exception | None
    pending_permit_revoked: bool
    active_admission_closed: bool


def call_once_with_permit(
    *,
    authority: MutationPermitAuthority,
    envelope: ActorDecisionEnvelope,
    turn_index: int,
    commit_identity: RuntimeCommitIdentity,
    still_current: Callable[[], bool],
    execute: Callable[[ActorDecisionEnvelope], TransitionT],
) -> PermitAttemptResult[TransitionT]:
    """Issue once, invoke exactly once, and retire all permit state.

    The Controller consumes the pending permit at its commit boundary.
    Controller exceptions become non-replayable facts.  ``BaseException``
    still propagates after the same deterministic cleanup.
    """

    if type(authority) is not MutationPermitAuthority:
        raise TypeError("authority must be MutationPermitAuthority")
    if type(envelope) is not ActorDecisionEnvelope:
        raise TypeError("envelope must be ActorDecisionEnvelope")
    if type(commit_identity) is not RuntimeCommitIdentity:
        raise TypeError("commit_identity must be RuntimeCommitIdentity")
    if not callable(execute):
        raise TypeError("execute must be callable")
    permit_id = authority.issue(
        envelope,
        turn_index=turn_index,
        commit_identity=commit_identity,
        still_current=still_current,
    )
    transition: TransitionT | None = None
    controller_error: Exception | None = None
    call_count = 0
    pending_revoked = False
    active_closed = False
    try:
        try:
            call_count += 1
            transition = execute(envelope)
        except Exception as exc:
            controller_error = exc
    finally:
        try:
            pending_revoked = authority.revoke(envelope.decision_id)
        finally:
            active_closed = authority.finish_admission(
                envelope.decision_id
            )
    return PermitAttemptResult(
        permit_id=permit_id,
        execute_call_count=call_count,
        transition=transition,
        controller_error=controller_error,
        pending_permit_revoked=pending_revoked,
        active_admission_closed=active_closed,
    )


__all__ = ["PermitAttemptResult", "call_once_with_permit"]
