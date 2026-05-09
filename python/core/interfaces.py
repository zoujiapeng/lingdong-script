"""
Core type definitions for the automation platform.

Defines the contracts between subsystems:
- ToolResult: standardized return value for all tool executions
- Observation: snapshot of screen/system state at a point in time
- StepRecord: one step in execution history
- TaskState: full state of a running task
- Tool: protocol for executable automation tools
- LLMProvider: protocol for LLM backends
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


# ── Tool Result ─────────────────────────────────────────────────

@dataclass
class ToolResult:
    """Standardized return value for every tool execution.

    Fields:
        success: Whether the tool completed successfully.
        confidence: How confident the tool is in its result (0.0-1.0).
        data: Arbitrary result payload (typed per tool).
        error: Human-readable error message if failed.
        screenshot: Optional path to a screenshot taken during execution.
    """
    success: bool
    confidence: float = 1.0
    data: Any = None
    error: str | None = None
    screenshot: str | None = None

    def __bool__(self) -> bool:
        return self.success

    @classmethod
    def ok(cls, data: Any = None, confidence: float = 1.0, **kw: Any) -> ToolResult:
        return cls(success=True, data=data, confidence=confidence, **kw)

    @classmethod
    def fail(cls, error: str, **kw: Any) -> ToolResult:
        return cls(success=False, error=error, **kw)


# ── Observation ─────────────────────────────────────────────────

@dataclass
class Observation:
    """Snapshot of the environment at a moment in time.

    Collected after each tool execution (or on demand) to provide
    context for the agent's next decision.
    """
    timestamp: float = 0.0
    currentApp: str = ""
    windowTitle: str = ""
    ocrText: list[str] = field(default_factory=list)
    detectedImages: list[str] = field(default_factory=list)
    lastActionResult: ToolResult | None = None
    screenshotPath: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()

    @classmethod
    def empty(cls) -> Observation:
        return cls(timestamp=time.time())


# ── Step Record ─────────────────────────────────────────────────

@dataclass
class StepRecord:
    """Record of one executed step in an agent or workflow run.

    Used for history tracking, logging, and replay.
    """
    step: int
    tool: str
    params: dict
    result: ToolResult
    observation: Observation | None = None
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()


# ── Task State ──────────────────────────────────────────────────

TaskStatus = Literal["idle", "running", "paused", "completed", "failed"]


@dataclass
class TaskState:
    """Full state of an agent or workflow execution task."""
    goal: str
    status: TaskStatus = "idle"
    currentStep: int = 0
    retryCount: int = 0
    history: list[StepRecord] = field(default_factory=list)
    observation: Observation = field(default_factory=Observation)

    def add_step(self, record: StepRecord) -> None:
        self.history.append(record)
        self.currentStep = record.step

    @property
    def last_result(self) -> ToolResult | None:
        return self.history[-1].result if self.history else None

    @property
    def total_steps(self) -> int:
        return len(self.history)


# ── Tool Protocol ───────────────────────────────────────────────

@runtime_checkable
class Tool(Protocol):
    """Protocol for executable automation tools.

    Every tool has a name, a JSON schema describing its parameters,
    and an execute() method that returns a ToolResult.
    """
    name: str

    @property
    def schema(self) -> dict:
        """JSON Schema describing accepted parameters."""
        ...

    def execute(self, params: dict[str, Any]) -> ToolResult:
        """Execute the tool with the given parameters."""
        ...


# ── LLM Provider Protocol ───────────────────────────────────────

@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM backends (DeepSeek, OpenAI, etc.)."""

    api_key: str
    api_base: str
    model: str

    def chat(self, messages: list[dict], **kwargs: Any) -> dict:
        """Synchronous chat completion.

        Args:
            messages: List of {"role": ..., "content": ...} dicts.
            **kwargs: Additional parameters (temperature, max_tokens, etc.).

        Returns:
            Parsed response dict with at least {"content": str}.
        """
        ...

    def chat_stream(
        self, messages: list[dict], **kwargs: Any
    ) -> Any:
        """Streaming chat completion (optional).

        Returns an iterator of response chunks.
        """
        ...
