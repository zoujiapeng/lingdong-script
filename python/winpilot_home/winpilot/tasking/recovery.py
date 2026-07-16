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
        if failure in {
            "recipient_not_resolved", "unknown_interface", "semantic_ambiguity",
        } and attempt <= 1:
            return RecoveryDecision("llm_stage_fallback", failure, True)
        if failure in {
            "window_not_found", "focus_lost", "stale_frame",
            "stale_authoritative_frame", "target_binding_failed",
            "interaction_region_invalid", "visual_frame_lineage_invalid",
        } and attempt <= 2:
            return RecoveryDecision("rebind_and_retry", failure, True)
        if failure in {
            "interaction_action_empty", "interaction_action_failed",
            "interaction_action_no_effect",
            "local_effect_missing", "semantic_verification_failed",
        }:
            # Never blindly replay a pointer mutation whose outcome is
            # uncertain.  Recovery starts from a new authoritative frame and
            # resumes at the failed semantic turn.
            return RecoveryDecision(
                "reobserve_then_replan_current_turn", failure, attempt <= 1,
            )
        if failure == "external_change_timeout":
            return RecoveryDecision(
                "keep_session_waiting_or_cancel", failure, False,
            )
        if failure in {"preview_only", "send_action_missing", "delivery_not_visible"}:
            return RecoveryDecision("verify_or_retry_current_stage", failure, attempt <= 2)
        return RecoveryDecision("fail_stage", failure, False)
