"""Runtime capability profile for the single WinPilot Agent.

The product exposes one Agent.  Task classification may enable additional
runtime capabilities (for example repository/coding tools), but it must not
route the task into a separate user-facing or separately-planned sub-agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import CONFIG
from .task_rules import _is_coding_task, _is_gui_only_task


def coding_workspace_dir() -> Path:
    """Configured repository workspace for Coding Runtime tools."""
    ws = CONFIG.get("coding", "workspace", default="") or ""
    return Path(ws).expanduser().resolve() if ws else Path.home().resolve()


@dataclass(frozen=True)
class RuntimeProfile:
    """Per-run capability switches for the unified AgentRunner."""

    desktop: bool = True
    coding: bool = False
    coding_workspace: Path | None = None
    max_steps: int | None = None
    replay: bool = True
    scheduler: bool = True
    automation_ir: bool = True
    action_queue: bool = True

    @property
    def label(self) -> str:
        return "coding+desktop" if self.coding else "desktop"

    @property
    def capabilities(self) -> tuple[str, ...]:
        active = []
        if self.desktop:
            active.append("desktop")
        if self.coding:
            active.append("coding")
        if self.replay:
            active.append("replay")
        if self.scheduler:
            active.append("scheduler")
        if self.automation_ir:
            active.append("automation_ir")
        if self.action_queue:
            active.append("desktop_action_queue")
        return tuple(active)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "capabilities": list(self.capabilities),
            "desktop": self.desktop,
            "coding": self.coding,
            "coding_workspace": str(self.coding_workspace or ""),
            "max_steps": self.max_steps,
            "replay": self.replay,
            "scheduler": self.scheduler,
            "automation_ir": self.automation_ir,
            "action_queue": self.action_queue,
        }

    def prompt_section(self) -> str:
        if not self.coding:
            return ""
        workspace = self.coding_workspace or coding_workspace_dir()
        return f"""
# Active runtime capability: Coding Runtime
You are still one WinPilot Agent. Do not treat coding as a separate sub-agent;
combine repository tools, shell, browser, and GUI tools whenever the task needs
that mix.

For coding/repository work, prefer deterministic repository actions over driving
an editor through the screen:
1. Inspect first with list_dir / read_file / search_text and identify the stack.
2. Edit with apply_patch for small precise replacements; use write_file only for
   new files or deliberate full rewrites. Re-read changed files before claiming
   success.
3. Verify with run_shell using real exit codes and output. When a command fails,
   read the referenced file/line, patch, and rerun the smallest useful check.
4. If the user asks to clone/run an open-source project without a URL, use
   github_search before cloning; do not pretend the project already exists.
5. Use GUI tools only when visible desktop/browser verification is part of the
   actual task, not as a substitute for repo inspection.

Coding workspace root: {workspace}
Safety: do not edit .env, credentials, tokens, keys, or secret/config files
unless the user explicitly requested configuration. For missing credentials,
report the required variable instead of inventing one.
""".strip()


def build_runtime_profile(task: str) -> RuntimeProfile:
    """Decide which runtimes are active inside the single AgentRunner."""
    coding_enabled = bool(CONFIG.get("coding", "enabled", default=True))
    use_coding = coding_enabled and _is_coding_task(task) and not _is_gui_only_task(task)
    if not use_coding:
        return RuntimeProfile(desktop=True, coding=False)
    max_steps = int(CONFIG.get("coding", "max_steps", default=30))
    return RuntimeProfile(
        desktop=True,
        coding=True,
        coding_workspace=coding_workspace_dir(),
        max_steps=max_steps,
    )
