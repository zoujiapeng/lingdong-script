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


def result_indicates_failure(result: Any) -> bool:
    """Return whether an action result explicitly reports failure.

    Legacy handlers use a mixture of booleans and ``success``/``ok``
    dictionaries.  A non-empty failure dictionary is truthy, so callers must
    not use ordinary truth testing to decide whether an action succeeded.
    """
    if result is None or result is False:
        return True
    if not isinstance(result, dict):
        return False
    if result.get("error") not in (None, "", False):
        return True
    # Status envelopes are contracts, not values to coerce by truthiness.
    # Anything except the literal JSON/Python boolean True is unknown or
    # negative and therefore cannot authorize the next action.
    for key in ("success", "ok", "found", "valid", "verified", "passed"):
        if key in result and result.get(key) is not True:
            return True
    if "result" not in result:
        return False
    nested = result.get("result")
    if nested is None or nested is False:
        return True
    return isinstance(nested, dict) and result_indicates_failure(nested)


def result_error_message(result: Any, default: str = "action reported failure") -> str:
    """Extract a useful error from a result rejected by the guard above."""
    if isinstance(result, dict):
        for key in ("error", "message", "detail", "reason"):
            value = result.get(key)
            if value:
                return str(value)
        nested = result.get("result")
        if isinstance(nested, dict):
            return result_error_message(nested, default)
    return default
