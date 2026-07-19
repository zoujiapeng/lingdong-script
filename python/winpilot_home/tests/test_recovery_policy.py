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


def test_execute_desktop_task_failure_does_not_enter_stage_recovery_policy(
    monkeypatch,
):
    import threading

    from winpilot.orchestrator.contracts import ArpNode, ArpNodeKind
    from winpilot.orchestrator.runtime import NodeResult
    from winpilot.tasking.contracts import StageType, TaskStage
    from winpilot.tasking.stage_runtime import StageRuntime

    stage = TaskStage(
        id="execute-desktop",
        type=StageType.EXECUTE_DESKTOP_TASK,
        name="Execute desktop task",
    )
    node = ArpNode(
        id=stage.id,
        kind=ArpNodeKind.TASK_STAGE,
        config={"stage": stage.model_dump(mode="json")},
    )

    class _Evidence:
        @staticmethod
        def records():
            return []

    for failure_mode in ("exception", "result"):
        runtime = StageRuntime()
        monkeypatch.setattr(runtime, "_contract", lambda _context: object())
        monkeypatch.setattr(runtime, "_evidence", lambda _context: _Evidence())
        monkeypatch.setattr(
            runtime.capabilities,
            "assert_satisfiable",
            lambda _contract: None,
        )
        monkeypatch.setattr(
            runtime.recovery,
            "decide",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("semantic stages must not invoke StageRuntime recovery")
            ),
        )

        if failure_mode == "exception":
            def fail_semantic_stage(*_args, **_kwargs):
                error = RuntimeError("semantic child failed")
                error.failure_type = "semantic_child_failed"
                error.stage_details = {"model_elapsed_ms": 7}
                raise error

            monkeypatch.setattr(runtime, "_execute", fail_semantic_stage)
        else:
            monkeypatch.setattr(
                runtime,
                "_execute",
                lambda *_args, **_kwargs: NodeResult(
                    ok=False,
                    branch="fail",
                    output={"failure_type": "semantic_child_failed"},
                    error="semantic child failed",
                ),
            )

        context = {"run_id": f"semantic-{failure_mode}", "runtime_state": {}}
        result = runtime.execute(node, context, threading.Event())

        assert result.ok is False
        assert "recovery" not in result.output
        assert "recovery_attempt" not in result.output
        assert "stage_recovery_attempts" not in context["runtime_state"]
