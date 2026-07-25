"""Fail-closed policy for retrying a desktop mutation."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MutationOutcome(str, Enum):
    NOT_STARTED = "not_started"
    NOT_APPLIED = "not_applied"
    APPLIED = "applied"
    OUTCOME_UNKNOWN = "outcome_unknown"


class ReplayDisposition(str, Enum):
    NEW_DECISION = "new_decision"
    REPLAY_EXPLICIT_IDEMPOTENT = "replay_explicit_idempotent"
    REOBSERVE = "reobserve"
    RECONCILE_OR_BLOCK = "reconcile_or_block"


@dataclass(frozen=True, slots=True)
class ActionReplayDecision:
    allowed: bool
    disposition: ReplayDisposition
    reason: str


def decide_action_replay(
    outcome: MutationOutcome | str,
    *,
    explicitly_idempotent: bool = False,
    replay_authorized: bool = False,
) -> ActionReplayDecision:
    if not isinstance(outcome, MutationOutcome):
        try:
            outcome = MutationOutcome(outcome)
        except (TypeError, ValueError):
            outcome = MutationOutcome.OUTCOME_UNKNOWN
    if outcome is MutationOutcome.NOT_STARTED:
        return ActionReplayDecision(False, ReplayDisposition.NEW_DECISION, "mutation_not_started")
    if outcome is MutationOutcome.NOT_APPLIED:
        if explicitly_idempotent is True and replay_authorized is True:
            return ActionReplayDecision(
                True,
                ReplayDisposition.REPLAY_EXPLICIT_IDEMPOTENT,
                "deterministically_not_applied_and_explicitly_idempotent",
            )
        return ActionReplayDecision(
            False,
            ReplayDisposition.REOBSERVE,
            "not_applied_without_explicit_idempotent_replay_authority",
        )
    if outcome is MutationOutcome.APPLIED:
        return ActionReplayDecision(False, ReplayDisposition.REOBSERVE, "mutation_already_applied")
    return ActionReplayDecision(
        False,
        ReplayDisposition.RECONCILE_OR_BLOCK,
        "mutation_outcome_unknown_never_auto_replay",
    )


__all__ = [
    "MutationOutcome",
    "ReplayDisposition",
    "ActionReplayDecision",
    "decide_action_replay",
]
