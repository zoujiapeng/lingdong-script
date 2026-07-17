"""Runtime capability profile for the single WinPilot Agent.

The product exposes one Agent.  Task classification may enable additional
runtime capabilities (for example repository/coding tools), but it must not
route the task into a separate user-facing or separately-planned sub-agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import CONFIG, PROJECT_ROOT
from .task_rules import _is_coding_task, _is_gui_only_task


def coding_workspace_dir() -> Path:
    """Configured repository workspace for Coding Runtime tools."""
    ws = CONFIG.get("coding", "workspace", default="") or ""
    return Path(ws).expanduser().resolve() if ws else PROJECT_ROOT.resolve()


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
    browser: bool = True
    safety: bool = True
    async_jobs: bool = True
    perception_daemon: bool = True
    task_contract_id: str = ""
    stage_type: str = ""
    required_capabilities: tuple[str, ...] = ()
    exposed_capabilities: tuple[str, ...] = ()
    stage_tools: tuple[str, ...] = ()
    domain_adapter: str = ""
    stage_guidance: str = ""
    model_tool_allowlist: tuple[str, ...] = ()
    inline_semantic_verification: bool = False
    experience_enabled: bool = True

    @property
    def label(self) -> str:
        if self.coding and self.desktop:
            return "coding+desktop"
        if self.coding:
            return "coding"
        if self.desktop:
            return "desktop"
        return "semantic"

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
        if self.browser:
            active.append("browser")
        if self.safety:
            active.append("safety")
        if self.async_jobs:
            active.append("async_jobs")
        if self.perception_daemon:
            active.append("perception_daemon")
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
            "browser": self.browser,
            "safety": self.safety,
            "async_jobs": self.async_jobs,
            "perception_daemon": self.perception_daemon,
            "task_contract_id": self.task_contract_id,
            "stage_type": self.stage_type,
            "required_capabilities": list(self.required_capabilities),
            "exposed_capabilities": list(self.exposed_capabilities),
            "stage_tools": list(self.stage_tools),
            "domain_adapter": self.domain_adapter,
            "stage_guidance": self.stage_guidance,
            "model_tool_allowlist": list(self.model_tool_allowlist),
            "inline_semantic_verification": self.inline_semantic_verification,
            "experience_enabled": self.experience_enabled,
        }

    def prompt_section(self) -> str:
        sections: list[str] = []
        if self.stage_guidance:
            sections.append(
                "# Typed stage execution guidance\n"
                "This is controller strategy, not part of the user's visible completion "
                "criteria. Do not require these implementation words or numbers to appear "
                "in the output.\n"
                + self.stage_guidance.strip()
            )
        if not self.coding:
            return "\n\n".join(sections)
        workspace = self.coding_workspace or coding_workspace_dir()
        sections.append(f"""
# Active runtime capability: Coding Runtime
You are still one WinPilot Agent. Do not treat coding as a separate sub-agent;
combine repository tools, shell, browser, and GUI tools whenever the task needs
that mix.

For coding/repository work, prefer deterministic repository actions over driving
an editor through the screen:
1. Inspect first with list_dir / read_file / search_text and identify the stack.
2. Edit with apply_patch for small precise replacements; use write_file only for
   new files or deliberate full rewrites. Keep one generated file/tool argument
   comfortably below the model output limit: write a concise working skeleton,
   then extend it with small apply_patch calls. Never emit pseudo <tool_call>
   markup in prose. Re-read changed files before claiming success.
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
""".strip())
        return "\n\n".join(sections)


def build_runtime_profile(
    task: str,
    *,
    task_contract=None,
    stage=None,
    stage_guidance: str = "",
    model_tool_allowlist: tuple[str, ...] | list[str] = (),
    inline_semantic_verification: bool = False,
) -> RuntimeProfile:
    """Decide which runtimes are active inside the single AgentRunner."""
    coding_enabled = bool(CONFIG.get("coding", "enabled", default=True))
    stage_execution = getattr(stage, "execution", None)
    if task_contract is not None and stage is not None:
        # A typed stage has already been classified and capability-planned.  Do
        # not let a generated stage goal re-enable Coding Runtime through an
        # unrelated substring heuristic.  Future typed coding stages can opt in
        # through explicit capabilities or their selected tools.
        required_stage_capabilities = {
            str(item).casefold()
            for item in (
                tuple(getattr(stage_execution, "required_capabilities", ()) or ())
                + tuple(getattr(stage_execution, "verifier_requirements", ()) or ())
            )
        }
        stage_capabilities = {
            str(item).casefold()
            for item in (
                tuple(getattr(stage_execution, "required_capabilities", ()) or ())
                + tuple(getattr(stage_execution, "exposed_capabilities", ()) or ())
            )
        }
        stage_tools = {
            str(item).casefold()
            for item in tuple(getattr(stage_execution, "tools", ()) or ())
        }
        desktop_required = any(
            capability.startswith((
                "app.", "window.", "controller.", "visual.", "drawing.",
                "artifact.", "interaction.", "conversation.", "recipient.",
                "communication.", "delivery.",
            ))
            for capability in required_stage_capabilities
        )
        use_coding = coding_enabled and (
            any(
                capability.startswith(("coding.", "repository.", "workspace."))
                for capability in stage_capabilities
            )
            or bool(
                stage_tools.intersection(
                    {"apply_patch", "read_file", "write_file", "search_text", "run_shell"}
                )
            )
        )
    else:
        # Compatibility path for direct AgentRunner/CLI callers that do not yet
        # provide a TaskContract.
        use_coding = coding_enabled and _is_coding_task(task) and not _is_gui_only_task(task)
        desktop_required = True
    experience_enabled = str(
        getattr(task_contract, "experience_policy", "allow") or "allow"
    ).casefold() != "disabled"
    stage_values = {
        "task_contract_id": str(getattr(task_contract, "task_id", "") or ""),
        "stage_type": str(getattr(getattr(stage, "type", None), "value", "") or ""),
        "required_capabilities": tuple(getattr(stage_execution, "required_capabilities", ()) or ()),
        "exposed_capabilities": tuple(getattr(stage_execution, "exposed_capabilities", ()) or ()),
        "stage_tools": tuple(getattr(stage_execution, "tools", ()) or ()),
        "domain_adapter": str(getattr(stage_execution, "adapter", "") or ""),
        "stage_guidance": str(stage_guidance or "").strip(),
        "model_tool_allowlist": tuple(str(item) for item in model_tool_allowlist if str(item)),
        "inline_semantic_verification": bool(inline_semantic_verification),
        "experience_enabled": experience_enabled,
    }
    if not use_coding:
        return RuntimeProfile(
            desktop=desktop_required,
            coding=False,
            replay=experience_enabled,
            **stage_values,
        )
    max_steps = int(CONFIG.get("coding", "max_steps", default=30))
    return RuntimeProfile(
        desktop=desktop_required,
        coding=True,
        replay=experience_enabled,
        coding_workspace=coding_workspace_dir(),
        max_steps=max_steps,
        **stage_values,
    )
