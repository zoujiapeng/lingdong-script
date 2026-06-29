"""Shared types for the legacy Flow/DSL executor."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class ExecutorState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"
    FINISHED = "finished"


@dataclass
class ExecutionContext:
    variables: Dict[str, Any] = field(default_factory=dict)
    loop_count: Dict[str, int] = field(default_factory=dict)
    max_loops: int = 9999
    retry_limit: int = 3
    timeout: float = 30.0
    max_call_depth: int = 50


class ExecutionError(Exception):
    pass


class ReturnSignal(Exception):
    """Signal returned from a subroutine."""

    def __init__(self, value: Any = None):
        self.value = value
        super().__init__()


class BreakLoop(Exception):
    """Control-flow exception for breaking out of LOOP."""

    def __str__(self):
        return "BREAK 只能在 LOOP 内部使用"
