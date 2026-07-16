"""Failure-specific recovery must resume the failed typed stage safely."""
from __future__ import annotations


def test_pointer_ownership_loss_rebinds_instead_of_replaying_old_geometry():
    from winpilot.tasking.recovery import RecoveryPolicy

    decision = RecoveryPolicy().decide(
        "PerformTurn", "POINTER_OWNERSHIP_LOST", attempt=1,
    )

    assert decision.action == "rebind_and_retry"
    assert decision.retryable is True


def test_repeated_pointer_ownership_loss_stops_after_bounded_recovery():
    from winpilot.tasking.recovery import RecoveryPolicy

    decision = RecoveryPolicy().decide(
        "PerformTurn", "pointer_ownership_lost", attempt=3,
    )

    assert decision.action == "fail_stage"
    assert decision.retryable is False


def test_semantic_failure_after_mutation_never_replays_pointer_input():
    from winpilot.tasking.recovery import RecoveryPolicy

    decision = RecoveryPolicy().decide(
        "VerifyLocalEffect", "semantic_verification_failed", attempt=1,
    )

    assert decision.action == "stop_after_uncertain_mutation"
    assert decision.retryable is False
