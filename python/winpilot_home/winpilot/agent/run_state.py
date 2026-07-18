"""Small execution-state machine for the unified Agent loop.

This is deliberately state tracking, not planning. It watches GUI, file, shell,
and Coding Runtime tool flow and surfaces compact phase information so the main
model can spend less effort reconstructing whether the last action still needs
verification.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from ..fast.action_contract import BATCH_MUTATING_ACTIONS, iter_atomic_actions


ACTION_TOOLS = {
    "open_app",
    "launch",
    "click",
    "type_text",
    "hotkey",
    "scroll",
    "drag",
    "draw_strokes",
    "focus_window",
    "wait",
    "tray_click",
    "run_shell",
    "write_file",
    "apply_patch",
    "run_action_batch",
    "execute_subgoal",
    "coordinate_click_test",
}
OBSERVE_TOOLS = {
    "observe",
    "read_text",
    "list_windows",
    "scan_windows",
    "find_app",
    "find_program",
    "find_image",
    "color_probe",
    "vlm_describe",
    "screen_watch",
    "list_dir",
    "read_file",
    "search_text",
    "github_search",
    "screenshot",
    "zoom",
    "coordinate_diagnostics",
    "list_installed_apps",
    "list_scheduled_tasks",
    "search_files",
    "web_search",
}
PASSIVE_PIXEL_OBSERVE_TOOLS = {
    "screenshot",
    "zoom",
}


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


def _json_dict(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text or "")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _batch_last_action(args: dict) -> str:
    actions = tuple(iter_atomic_actions("run_action_batch", args))
    return actions[-1].name if actions else ""


def _batch_has_semantic_observe(args: dict) -> bool:
    last = _batch_last_action(args)
    return last in {"observe", "read_text", "vlm_describe"}


def _batch_has_mutation(args: dict) -> bool:
    return any(
        action.name in BATCH_MUTATING_ACTIONS
        for action in iter_atomic_actions("run_action_batch", args)
    )


def _batch_has_expect(args: dict) -> bool:
    return any(
        isinstance(action.inputs.get("expect"), dict)
        for action in iter_atomic_actions("run_action_batch", args)
    )


def _passive_observe_verifies_pending_actions(data: dict[str, Any]) -> bool:
    return data.get("verified") is True or data.get("matches") is True


@dataclass
class RunStateMachine:
    phase: str = "idle"
    last_tool: str = ""
    last_issue: str = ""
    unverified_actions: int = 0
    transition_count: int = 0
    updated_ms: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    _last_guidance_key: tuple[str, str, int] | None = None

    def _transition(self, phase: str, tool: str, issue: str = "") -> None:
        self.phase = phase
        self.last_tool = tool
        self.last_issue = issue
        self.transition_count += 1
        self.updated_ms = _now_ms()
        self.history.append({
            "phase": phase,
            "tool": tool,
            "issue": issue,
            "at_ms": self.updated_ms,
        })
        self.history = self.history[-8:]

    def before_tool(self, name: str, args: dict | None = None) -> None:
        if name in ACTION_TOOLS:
            self._transition("acting", name)
        elif name in OBSERVE_TOOLS:
            self._transition("observing", name)
        else:
            self._transition("tooling", name)

    def after_tool(
        self,
        name: str,
        args: dict | None,
        result_text: str,
        terminal: str | None = None,
    ) -> None:
        args = args or {}
        data = _json_dict(result_text)
        if terminal:
            self._transition(f"terminal:{terminal}", name)
            return

        has_expect = isinstance(args.get("expect"), dict) or (
            name == "run_action_batch" and _batch_has_expect(args)
        )
        blocked = bool(data.get("blocked"))
        ok_false = data.get("ok") is False
        verified_false = data.get("verified") is False
        if blocked or ok_false or (verified_false and has_expect):
            self.unverified_actions += 1
            issue = str(data.get("error") or data.get("verify_detail") or "tool result was not verified")[:240]
            self._transition("recovering", name, issue)
            return

        if name in OBSERVE_TOOLS:
            if self.unverified_actions > 0:
                # A queried label may have existed before the action (for
                # example the clicked button's own text). Only an observation
                # carrying an explicit verified/matches transition may clear
                # causal action debt; plain read_text merely regrounds vision.
                verified_observation = _passive_observe_verifies_pending_actions(data)
                if verified_observation:
                    self.unverified_actions = 0
                    self._transition("ready", name)
                    return
                # A fresh frame/element inventory restores visual grounding for
                # the next decision but does not prove the previous action's
                # postcondition.  Keep the debt visible for the done gate.
                self._transition(
                    "visually_grounded",
                    name,
                    (
                        f"{name} refreshed state but did not causally prove "
                        f"{self.unverified_actions} pending action(s)"
                    ),
                )
                return
            self.unverified_actions = 0
            self._transition("ready", name)
            return

        if name == "run_shell":
            # A successful read-only check is deterministic observation, not a
            # second unverified desktop mutation. Otherwise a valid Test-Path
            # after a GUI save makes the done gate impossible to satisfy.
            try:
                from .task_rules import _gui_only_shell_allowed

                read_only_verify = _gui_only_shell_allowed(
                    str(args.get("command") or "")
                )
            except Exception:
                read_only_verify = False
            if (
                read_only_verify
                and data.get("ok") is True
                and int(data.get("exit_code") or 0) == 0
            ):
                self.unverified_actions = 0
                self._transition("ready", name)
                return

        if name == "run_action_batch":
            if _passive_observe_verifies_pending_actions(data):
                self.unverified_actions = 0
                self._transition("ready", name)
            elif _batch_has_semantic_observe(args):
                # A trailing observe/read_text refreshes the real scene, but a
                # plain snapshot is not a causal postcondition. Count a new
                # batch mutation if present while preserving any prior debt;
                # an observe-only batch must not manufacture another mutation.
                if _batch_has_mutation(args):
                    self.unverified_actions += 1
                self._transition(
                    "visually_grounded",
                    name,
                    "batch refreshed state but did not explicitly verify its action",
                )
            else:
                self.unverified_actions += 1
                self._transition(
                    "need_observe",
                    name,
                    "batch executed; confirm the real UI state before claiming completion",
                )
            return

        if name in ACTION_TOOLS:
            if data.get("verified") is True:
                self.unverified_actions = 0
                self._transition("ready", name)
            else:
                self.unverified_actions += 1
                self._transition(
                    "need_observe",
                    name,
                    "action executed; verify screen/text/deterministic evidence before done",
                )
            return

        self._transition("ready", name)

    def terminal_rejected(self, name: str, issue: str = "") -> None:
        self._transition("recovering", name, issue or "terminal claim rejected by done_check")

    def accept_verification(self, name: str, reason: str) -> None:
        """Reconcile a pending mutation with stronger deterministic evidence."""
        self.unverified_actions = 0
        self._transition("ready", name, str(reason or "deterministic verification passed")[:240])

    def guidance(self) -> str:
        key = (self.phase, self.last_tool, self.unverified_actions)
        if key == self._last_guidance_key:
            return ""
        self._last_guidance_key = key
        if self.phase == "need_observe":
            return (
                f"phase=need_observe after {self.last_tool}: {self.last_issue}. "
                "Use observe/read_text/VLM or deterministic checks before done."
            )
        if self.phase == "recovering":
            return (
                f"phase=recovering after {self.last_tool}: {self.last_issue}. "
                "Do not repeat the same action blindly; re-observe or choose a different route."
            )
        if self.phase == "visually_grounded":
            return (
                f"phase=visually_grounded after {self.last_tool}: {self.last_issue}. "
                "The frame may guide the next decision, but it is not completion evidence; "
                "use a targeted predicate or the independent visual done judge."
            )
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "last_tool": self.last_tool,
            "last_issue": self.last_issue,
            "unverified_actions": self.unverified_actions,
            "transition_count": self.transition_count,
            "updated_ms": self.updated_ms,
            "history": list(self.history),
        }
