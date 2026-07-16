"""Runner construction for the single WinPilot Agent.

Older builds routed repository/coding tasks into ``CodingRunner``. That split
created two planners, two prompts, and two tool loops. Coding is now an optional
runtime capability inside ``AgentRunner``; the router only constructs the single
runner so all tasks share Trace/BUS/done-gate/memory semantics.
"""
from __future__ import annotations

import threading
from typing import Any

from ..utils.log import logger
from .loop import AgentRunner
from .runtime_profile import build_runtime_profile


def build_runner(
    task: str,
    stop_event: threading.Event | None = None,
    session_context: list | None = None,
    max_steps_override: int | None = None,
    allow_shell_tools: bool | None = None,
    runtime_profile=None,
    task_intent: Any | None = None,
    communication_context: dict[str, Any] | None = None,
) -> AgentRunner:
    """Return the unified AgentRunner for every task.

    Capability selection happens inside the runner via RuntimeProfile. For
    example, a repository task enables Coding Runtime tools without becoming a
    separate coding sub-agent.
    """
    profile = runtime_profile or build_runtime_profile(task)
    logger.info("构建统一 AgentRunner: runtime=%s task=%s", profile.label, task[:60])
    return AgentRunner(
        task,
        stop_event=stop_event,
        session_context=session_context,
        max_steps_override=max_steps_override,
        runtime_profile=profile,
        allow_shell_tools=allow_shell_tools,
        task_intent=task_intent,
        communication_context=communication_context,
    )
