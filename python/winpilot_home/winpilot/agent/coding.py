"""Compatibility shim for the removed Coding sub-agent.

Coding is no longer implemented as a separate planner/runner. Repository work
now runs through ``AgentRunner`` with Coding Runtime enabled, so GUI, shell,
files, trace, memory policy, and done verification stay unified.

``CodingRunner`` remains as a thin subclass only to avoid breaking older tests,
scripts, or imports that still reference ``winpilot.agent.coding.CodingRunner``.
New code should call ``agent.router.build_runner`` or instantiate
``AgentRunner`` directly.
"""
from __future__ import annotations

from pathlib import Path

from .loop import AgentRunner
from .runtime_profile import coding_workspace_dir


def workspace_dir() -> Path:
    """Return the Coding Runtime workspace root."""
    return coding_workspace_dir()


class CodingRunner(AgentRunner):
    """Deprecated alias for AgentRunner with Coding Runtime detection.

    This class intentionally does not override ``run`` or tool handling. The
    task text still controls whether Coding Runtime is active via RuntimeProfile.
    """

    pass
