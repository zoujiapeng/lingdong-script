"""
core — Typed interfaces and shared abstractions.

Provides the foundational type system for the entire project:
ToolResult, Observation, TaskState, EventBus, StructuredLogger.
"""
from .interfaces import ToolResult, Observation, StepRecord, TaskState
from .events import Event, EventBus
from .logging import StructuredLogger

__all__ = [
    "ToolResult", "Observation", "StepRecord", "TaskState",
    "Event", "EventBus",
    "StructuredLogger",
]
