"""Failure-class recovery decisions for typed stages."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    reason: str
    retryable: bool


class RecoveryPolicy:
    """Keep recovery scoped to the failed stage instead of restarting a task."""

    def decide(self, stage_type: str, failure_type: str, attempt: int = 1) -> RecoveryDecision:
        failure = str(failure_type or "unknown").casefold()
        if failure in {"capability_missing", "contract_unsatisfied"}:
            return RecoveryDecision("stop_before_execution", failure, False)
        if failure in {"recipient_not_resolved", "unknown_interface", "semantic_ambiguity"} and attempt <= 1:
            return RecoveryDecision("llm_stage_fallback", failure, True)
        if failure in {"window_not_found", "focus_lost", "stale_frame"} and attempt <= 2:
            return RecoveryDecision("rebind_and_retry", failure, True)
        if failure in {"preview_only", "send_action_missing", "delivery_not_visible"}:
            return RecoveryDecision("verify_or_retry_current_stage", failure, attempt <= 2)
        return RecoveryDecision("fail_stage", failure, False)
