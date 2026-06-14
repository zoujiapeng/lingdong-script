"""Runner router: pick the coding sub-agent or the GUI agent for a task.

Single source of truth used by both entry points (Electron IPC adapter and the
FastAPI server) so routing stays consistent. Coding tasks (detected by the
coding-project playbook keywords) go to CodingRunner when coding is enabled;
everything else to the GUI AgentRunner.
"""
from __future__ import annotations

import threading

from ..config import CONFIG
from ..utils.log import logger
from .loop import AgentRunner, _is_coding_task


def build_runner(task: str, stop_event: threading.Event | None = None,
                 session_context: list | None = None):
    """Return a runner (AgentRunner | CodingRunner) for the task. Both share the
    run() / run_id / status / result_text / interject interface."""
    if CONFIG.get("coding", "enabled", default=True) and _is_coding_task(task):
        try:
            from .coding import CodingRunner
            logger.info("路由到编程子agent: %s", task[:60])
            return CodingRunner(task, stop_event=stop_event, session_context=session_context)
        except Exception as exc:  # never block on a coding-agent import problem
            logger.warning("编程子agent 不可用，回退到 GUI agent: %s", exc)
    return AgentRunner(task, stop_event=stop_event, session_context=session_context)
