"""
Agent State Machine — formal state machine for agent execution.

Replaces ad-hoc flag-based control flow with explicit states and transitions:
    IDLE → ANALYZING → PLANNING → ACTING → OBSERVING → (loop) → COMPLETED | FAILED

Designed to run alongside the existing supervisor loop in controller.py.
"""

from __future__ import annotations

import logging
import time
from enum import Enum, auto
from typing import Any, Callable

from core.interfaces import TaskState, ToolResult
from core.events import EventBus, global_bus, EVENT_AGENT_START, EVENT_AGENT_DONE, EVENT_AGENT_THINKING

logger = logging.getLogger("agent.state_machine")


class AgentState(Enum):
    """Finite states for the agent execution state machine."""
    IDLE = auto()
    ANALYZING = auto()
    PLANNING = auto()
    ACTING = auto()
    OBSERVING = auto()
    COMPLETED = auto()
    FAILED = auto()


# ── State transitions (directed graph) ──────────────────────────
# Maps: current_state → list of valid next states

VALID_TRANSITIONS: dict[AgentState, list[AgentState]] = {
    AgentState.IDLE: [AgentState.ANALYZING],
    AgentState.ANALYZING: [AgentState.PLANNING, AgentState.FAILED],
    AgentState.PLANNING: [AgentState.ACTING, AgentState.FAILED],
    AgentState.ACTING: [AgentState.OBSERVING, AgentState.FAILED],
    AgentState.OBSERVING: [AgentState.PLANNING, AgentState.COMPLETED, AgentState.FAILED],
    AgentState.COMPLETED: [AgentState.IDLE],
    AgentState.FAILED: [AgentState.IDLE],
}


# ── Callback types ──────────────────────────────────────────────

StateChangeCallback = Callable[[AgentState, AgentState], None]
"""Called as (old_state, new_state) on every state transition."""

StepActionCallback = Callable[[int, dict], ToolResult]
"""Called as (step_number, action) to execute a tool. Returns ToolResult."""


# ── State Machine ───────────────────────────────────────────────

class AgentStateMachine:
    """Formal finite state machine for agent execution.

    Manages the agent lifecycle from IDLE through ANALYZE→PLAN→ACT→OBSERVE
    loops until COMPLETED or FAILED.

    Usage:
        sm = AgentStateMachine()
        sm.on_state_change(lambda old, new: print(f"{old} → {new}"))

        # Start execution
        await sm.start("open notepad and type hello")
        while sm.state not in (AgentState.COMPLETED, AgentState.FAILED):
            sm.tick()
    """

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._state = AgentState.IDLE
        self._task = TaskState(goal="")
        self._bus = event_bus or global_bus
        self._state_change_handlers: list[StateChangeCallback] = []
        self._step_action_handler: StepActionCallback | None = None
        self._max_retries = 3
        self._max_steps = 50
        self._step_count = 0  # internal step counter (independent of TaskState.history)
        self._started_at: float = 0.0

    # ── Properties ─────────────────────────────────────────────

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def task(self) -> TaskState:
        return self._task

    @property
    def is_running(self) -> bool:
        return self._state not in (AgentState.IDLE, AgentState.COMPLETED, AgentState.FAILED)

    @property
    def elapsed(self) -> float:
        """Seconds since the agent started."""
        if self._started_at:
            return time.time() - self._started_at
        return 0.0

    # ── Configuration ──────────────────────────────────────────

    def on_state_change(self, handler: StateChangeCallback) -> None:
        """Register a handler called on every state transition."""
        self._state_change_handlers.append(handler)

    def set_step_action_handler(self, handler: StepActionCallback) -> None:
        """Register the handler that executes tool steps."""
        self._step_action_handler = handler

    def set_max_retries(self, n: int) -> None:
        self._max_retries = n

    def set_max_steps(self, n: int) -> None:
        self._max_steps = n

    # ── Lifecycle ──────────────────────────────────────────────

    def start(self, goal: str) -> None:
        """Transition from IDLE → ANALYZING to begin execution."""
        self._assert_state(AgentState.IDLE)
        self._task = TaskState(goal=goal, status="running")
        self._started_at = time.time()
        self._transition_to(AgentState.ANALYZING)
        self._bus.emit(EVENT_AGENT_START, {"goal": goal})

    def stop(self) -> None:
        """Stop execution and return to IDLE.

        Force-resets to IDLE from any state (user-initiated stop).
        """
        old_state = self._state
        self._state = AgentState.IDLE
        self._task.status = "idle"
        for handler in self._state_change_handlers:
            try:
                handler(old_state, AgentState.IDLE)
            except Exception:
                logger.exception("State change handler failed")
        self._bus.emit(EVENT_AGENT_DONE, {"status": "stopped"})

    def fail(self, reason: str) -> None:
        """Transition to FAILED state with a reason."""
        self._transition_to(AgentState.FAILED)
        self._task.status = "failed"
        self._bus.emit(EVENT_AGENT_DONE, {"status": "failed", "reason": reason})

    def complete(self) -> None:
        """Transition to COMPLETED state."""
        self._transition_to(AgentState.COMPLETED)
        self._task.status = "completed"
        self._bus.emit(EVENT_AGENT_DONE, {
            "status": "completed",
            "total_steps": self._task.total_steps,
            "elapsed": self.elapsed,
        })

    # ── Core Tick ──────────────────────────────────────────────

    def tick(self) -> AgentState:
        """Advance the state machine by one step.

        Called in a loop from the supervisor. Each call moves
        through one state transition.

        Returns:
            The current state after the tick.
        """
        if self._state == AgentState.IDLE:
            return self._state

        if self._state == AgentState.ANALYZING:
            self._on_analyzing()

        elif self._state == AgentState.PLANNING:
            self._on_planning()

        elif self._state == AgentState.ACTING:
            self._on_acting()

        elif self._state == AgentState.OBSERVING:
            self._on_observing()

        return self._state

    # ── State Handlers (overridable) ───────────────────────────

    def _on_analyzing(self) -> None:
        """ANALYZING: goal analysis phase.

        Override this to add custom goal decomposition logic.
        Default: transitions directly to PLANNING.
        """
        self._transition_to(AgentState.PLANNING)

    def _on_planning(self) -> None:
        """PLANNING: decide next action.

        Override this to call the LLM planner.
        Default: transitions to FAILED if no step handler is set.
        """
        if self._step_action_handler is None:
            self.fail("No step action handler configured")
            return
        self._transition_to(AgentState.ACTING)

    def _on_acting(self) -> None:
        """ACTING: execute the current step action.

        Override this to dispatch to specific tools.
        Default: increments internal step counter and transitions to OBSERVING.
        """
        self._step_count += 1
        self._task.currentStep = self._step_count
        self._transition_to(AgentState.OBSERVING)

    def _on_observing(self) -> None:
        """OBSERVING: collect observation after action.

        Override this to gather environment state.
        Default: transitions back to PLANNING for the next step,
        or COMPLETED if max steps reached.
        """
        if self._step_count >= self._max_steps:
            self.complete()
        else:
            self._transition_to(AgentState.PLANNING)

    # ── Transition Management ──────────────────────────────────

    def _transition_to(self, new_state: AgentState) -> None:
        """Validate and perform a state transition.

        Raises:
            ValueError: If the transition is not allowed.
        """
        allowed = VALID_TRANSITIONS.get(self._state, [])
        if new_state not in allowed:
            raise ValueError(
                f"Invalid transition: {self._state.name} → {new_state.name}. "
                f"Allowed: {[s.name for s in allowed]}"
            )

        old_state = self._state
        self._state = new_state

        # Notify handlers
        for handler in self._state_change_handlers:
            try:
                handler(old_state, new_state)
            except Exception:
                logger.exception("State change handler failed")

        logger.debug("Agent state: %s → %s", old_state.name, new_state.name)

    def _assert_state(self, *states: AgentState) -> None:
        """Assert the machine is in one of the given states."""
        if self._state not in states:
            current = self._state.name
            expected = " | ".join(s.name for s in states)
            raise RuntimeError(f"Expected state {expected}, got {current}")

    def __repr__(self) -> str:
        return (
            f"AgentStateMachine(state={self._state.name}, "
            f"goal='{self._task.goal[:40]}', "
            f"steps={self._task.total_steps})"
        )
