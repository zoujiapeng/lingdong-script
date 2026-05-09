"""
Event system — typed pub/sub event bus.

Provides decoupled communication between subsystems:
- Agent → UI: progress events, step updates
- Executor → Logger: execution events
- Tool → Observer: post-execution observations
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("core.events")


# ── Event Types ─────────────────────────────────────────────────

# Standard event type constants for discoverability
EVENT_AGENT_START = "agent:start"
EVENT_AGENT_STOP = "agent:stop"
EVENT_AGENT_DONE = "agent:done"
EVENT_AGENT_THINKING = "agent:thinking"

EVENT_STEP_START = "step:start"
EVENT_STEP_COMPLETE = "step:complete"
EVENT_STEP_FAIL = "step:fail"
EVENT_STEP_SKIP = "step:skip"

EVENT_TOOL_CALL = "tool:call"
EVENT_TOOL_RESULT = "tool:result"

EVENT_OBSERVATION = "observation:collected"

EVENT_ERROR = "error"
EVENT_LOG = "log"
EVENT_WARN = "warn"


# ── Event Data ──────────────────────────────────────────────────

@dataclass
class Event:
    """A typed event carrying structured data.

    Attributes:
        type: Event type string (use EVENT_* constants).
        data: Arbitrary payload (typed per event type).
        timestamp: When the event was created.
    """
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()


# ── Event Bus ───────────────────────────────────────────────────

Handler = Callable[[Event], None]


class EventBus:
    """Typed pub/sub event bus.

    Usage:
        bus = EventBus()
        bus.subscribe("step:complete", my_handler)
        bus.emit(Event("step:complete", {"step": 1, "status": "ok"}))
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = {}

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """Register a handler for a specific event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        """Remove a previously registered handler."""
        handlers = self._subscribers.get(event_type)
        if handlers:
            try:
                handlers.remove(handler)
            except ValueError:
                pass

    def emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Emit an event to all subscribers of that type.

        Args:
            event_type: The event type string.
            data: Optional payload dict.
        """
        event = Event(type=event_type, data=data or {})
        handlers = self._subscribers.get(event_type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception("Event handler %s failed for %s", handler, event_type)

    def emit_event(self, event: Event) -> None:
        """Emit a pre-constructed Event object."""
        handlers = self._subscribers.get(event.type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception("Event handler %s failed for %s", handler, event.type)

    def clear(self) -> None:
        """Remove all subscribers."""
        self._subscribers.clear()

    @property
    def subscriber_count(self) -> int:
        return sum(len(h) for h in self._subscribers.values())


# Module-level singleton for convenience
global_bus = EventBus()
