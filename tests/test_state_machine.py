"""
AgentStateMachine tests — all valid/invalid transitions, lifecycle, error handling.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import pytest
from agent.state_machine import AgentState, AgentStateMachine, VALID_TRANSITIONS


# Helper: a step handler that always succeeds
_OK_HANDLER = lambda s, a: type("R", (), {"success": True})()


def _sm_with_handler() -> AgentStateMachine:
    """Create a state machine with a step handler configured."""
    sm = AgentStateMachine()
    sm.set_step_action_handler(_OK_HANDLER)
    return sm


class TestValidTransitions:
    """Every allowed state transition defined in VALID_TRANSITIONS."""

    def test_idle_to_analyzing(self):
        sm = AgentStateMachine()
        sm.start("test")
        assert sm.state == AgentState.ANALYZING

    def test_analyzing_to_planning(self):
        sm = AgentStateMachine()
        sm.start("test")
        sm.tick()
        assert sm.state == AgentState.PLANNING

    def test_planning_to_acting(self):
        sm = _sm_with_handler()
        sm.start("test")
        sm.tick()  # analyzing → planning
        sm.tick()  # planning → acting
        assert sm.state == AgentState.ACTING

    def test_planning_to_failed_without_handler(self):
        sm = AgentStateMachine()
        sm.start("test")
        sm.tick()  # analyzing → planning
        sm.tick()  # planning → fail (no handler)
        assert sm.state == AgentState.FAILED

    def test_acting_to_observing(self):
        sm = _sm_with_handler()
        sm.start("test")
        sm.tick()  # analyzing → planning
        sm.tick()  # planning → acting
        sm.tick()  # acting → observing
        assert sm.state == AgentState.OBSERVING

    def test_observing_to_planning(self):
        sm = _sm_with_handler()
        sm.start("test")
        for _ in range(4):
            sm.tick()
        assert sm.state == AgentState.PLANNING  # loop back

    def test_completed_to_idle(self):
        sm = _sm_with_handler()
        sm.start("test")  # ANALYZING
        sm.tick()  # → PLANNING
        sm.tick()  # → ACTING
        sm.tick()  # → OBSERVING
        assert sm.state == AgentState.OBSERVING
        sm.complete()
        assert sm.state == AgentState.COMPLETED
        sm.stop()
        assert sm.state == AgentState.IDLE

    def test_failed_to_idle(self):
        sm = _sm_with_handler()
        sm.start("test")
        sm.fail("oops")
        assert sm.state == AgentState.FAILED
        sm.stop()
        assert sm.state == AgentState.IDLE


class TestInvalidTransitions:
    """Transitions not in VALID_TRANSITIONS should raise ValueError."""

    def test_cannot_start_from_analyzing(self):
        sm = AgentStateMachine()
        sm.start("test")
        with pytest.raises(RuntimeError, match="Expected state IDLE"):
            sm.start("again")

    def test_cannot_complete_from_idle(self):
        sm = AgentStateMachine()
        with pytest.raises(ValueError):
            sm._transition_to(AgentState.COMPLETED)

    def test_cannot_analyze_from_acting(self):
        sm = _sm_with_handler()
        sm.start("test")  # ANALYZING
        sm.tick()  # → PLANNING
        sm.tick()  # → ACTING
        with pytest.raises(ValueError):
            sm._transition_to(AgentState.ANALYZING)

    def test_cannot_skip_analyzing(self):
        sm = AgentStateMachine()
        with pytest.raises(ValueError):
            sm._transition_to(AgentState.PLANNING)  # IDLE→PLANNING invalid


class TestLifecycle:
    """Full lifecycle: start → execution loop → complete/fail."""

    def test_start_sets_task_goal(self):
        sm = AgentStateMachine()
        sm.start("open notepad")
        assert sm.task.goal == "open notepad"
        assert sm.task.status == "running"

    def test_is_running_during_execution(self):
        sm = _sm_with_handler()
        assert sm.is_running is False
        sm.start("test")
        assert sm.is_running is True
        sm.tick(); sm.tick(); sm.tick()  # → OBSERVING
        sm.complete()
        assert sm.is_running is False

    def test_complete_sets_status(self):
        sm = _sm_with_handler()
        sm.start("test")
        sm.tick(); sm.tick(); sm.tick()  # → OBSERVING
        sm.complete()
        assert sm.state == AgentState.COMPLETED
        assert sm.task.status == "completed"

    def test_fail_sets_status_and_reason(self):
        sm = _sm_with_handler()
        sm.start("test")
        sm.fail("target not found")
        assert sm.state == AgentState.FAILED
        assert sm.task.status == "failed"

    def test_full_loop_then_complete(self):
        sm = _sm_with_handler()
        sm.set_max_steps(3)
        sm.start("test")

        steps_run = 0
        while sm.state not in (AgentState.COMPLETED, AgentState.FAILED):
            sm.tick()
            steps_run += 1

        assert sm.state == AgentState.COMPLETED
        assert sm._step_count == 3
        assert steps_run > 0

    def test_full_loop_then_complete_with_max_steps(self):
        sm = _sm_with_handler()
        sm.set_max_steps(2)
        sm.start("test")

        for _ in range(10):
            sm.tick()
            if sm.state == AgentState.COMPLETED:
                break

        assert sm.state == AgentState.COMPLETED
        assert sm._step_count >= 2  # max_steps reached in OBSERVING

    def test_elapsed_time(self):
        sm = _sm_with_handler()
        assert sm.elapsed == 0.0
        sm.start("test")
        time.sleep(0.01)
        assert sm.elapsed > 0.0
        elapsed = sm.elapsed
        sm.tick(); sm.tick(); sm.tick()  # → OBSERVING
        sm.complete()
        assert sm.elapsed >= elapsed  # doesn't reset on complete

    def test_stop_resets_to_idle(self):
        sm = _sm_with_handler()
        sm.start("test")
        sm.stop()
        assert sm.state == AgentState.IDLE
        assert sm.task.status == "idle"

    def test_tick_from_idle_returns_idle(self):
        sm = AgentStateMachine()
        result = sm.tick()
        assert result == AgentState.IDLE
        assert sm.state == AgentState.IDLE


class TestStateChangeHandlers:
    """State change callback registration and invocation."""

    def test_handler_called_on_transition(self):
        sm = AgentStateMachine()
        changes: list[tuple] = []
        sm.on_state_change(lambda old, new: changes.append((old, new)))
        sm.start("test")
        assert len(changes) == 1
        assert changes[0] == (AgentState.IDLE, AgentState.ANALYZING)

    def test_handler_called_multiple_times(self):
        sm = _sm_with_handler()
        changes: list[tuple] = []
        sm.on_state_change(lambda old, new: changes.append((old, new)))
        sm.start("test")
        sm.tick()
        sm.tick()
        assert len(changes) >= 3

    def test_multiple_handlers(self):
        sm = AgentStateMachine()
        results: list[str] = []
        sm.on_state_change(lambda o, n: results.append("a"))
        sm.on_state_change(lambda o, n: results.append("b"))
        sm.start("test")
        assert "a" in results
        assert "b" in results

    def test_handler_exception_does_not_crash(self):
        sm = AgentStateMachine()
        def broken(o, n):
            raise RuntimeError("handler failed")
        sm.on_state_change(broken)
        sm.on_state_change(lambda o, n: None)  # second handler runs
        sm.start("test")  # should not raise


class TestStepActionHandler:
    """Step action handler lifecycle."""

    def test_with_handler_transitions_from_planning_to_acting(self):
        sm = _sm_with_handler()
        sm.start("test")
        sm.tick()  # analyzing → planning
        sm.tick()  # planning → acting
        assert sm.state == AgentState.ACTING

    def test_no_handler_goes_to_failed(self):
        sm = AgentStateMachine()
        sm.start("test")
        sm.tick()  # analyzing → planning
        sm.tick()  # planning → failed (no handler)
        assert sm.state == AgentState.FAILED


class TestConfig:
    """Configuration setters."""

    def test_set_max_retries(self):
        sm = AgentStateMachine()
        sm.set_max_retries(5)
        assert sm._max_retries == 5

    def test_set_max_steps(self):
        sm = AgentStateMachine()
        sm.set_max_steps(10)
        assert sm._max_steps == 10


class TestTransitionTable:
    """VALID_TRANSITIONS table consistency."""

    def test_all_states_have_entry(self):
        for state in AgentState:
            assert state in VALID_TRANSITIONS, f"{state} missing from VALID_TRANSITIONS"

    def test_no_self_loops_in_table(self):
        for state, next_states in VALID_TRANSITIONS.items():
            assert state not in next_states, f"{state} has self-loop"

    def test_all_targets_are_valid_states(self):
        valid = set(AgentState)
        for state, next_states in VALID_TRANSITIONS.items():
            for ns in next_states:
                assert ns in valid, f"{state}→{ns}: {ns} not a real state"
