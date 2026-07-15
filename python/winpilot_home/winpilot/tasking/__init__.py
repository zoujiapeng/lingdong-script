"""Typed task contracts and the deterministic stage runtime.

This package is deliberately independent from ``AgentRunner``.  The runner is
one possible stage executor; it is no longer the place where task routing,
artifact identity, communication policy, and completion semantics are invented.
"""

from .compiler import TaskContractCompiler
from .contracts import StageType, TaskContract, TaskIntent, TaskStage
from .intent import TaskIntentParser

__all__ = (
    "StageType",
    "TaskContract",
    "TaskContractCompiler",
    "TaskIntent",
    "TaskIntentParser",
    "TaskStage",
)
