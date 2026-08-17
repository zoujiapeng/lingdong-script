"""Kept shared modules of the legacy tasking package.

The task-contract compiler, StageRuntime and interaction chains were removed
with the legacy Agent line.  Only the modules still owned by vNext/Studio
remain here: run artifact storage, typed evidence records, and the desktop
workspace context captured for the vNext lease handoff.
"""

from . import artifacts, evidence, execution_context  # noqa: F401

__all__ = ("artifacts", "evidence", "execution_context")
