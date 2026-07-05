"""Automation IR v1: shared envelope for replay, FlowScript, and skills.

The IR is intentionally small. It does not replace `.wps.json` or FlowScript
yet; it gives them one common, validated handoff shape so new automation
features do not keep inventing parallel metadata.
"""
from __future__ import annotations

import copy
import time
import uuid
from typing import Any

AUTOMATION_IR_FORMAT = "automation-ir/1"

_DESKTOP_MUTATION_TOOLS = frozenset({
    "click",
    "type_text",
    "hotkey",
    "launch",
    "focus_window",
    "scroll",
    "drag",
    "draw_strokes",
    "run_detached",
    "tray_click",
    "ext.clipboard_set",
})
_SHELL_TOOLS = frozenset({"run_shell", "ext.run_shell"})


def _clone(value: Any) -> Any:
    return copy.deepcopy(value)


def _nonempty_str(value: Any) -> str:
    return str(value or "").strip()


def _created_at(value: Any) -> int:
    try:
        return int(value or time.time())
    except (TypeError, ValueError):
        return int(time.time())


def _target_window_from_wps(script: dict[str, Any]) -> Any:
    if script.get("target_window") not in (None, "", {}):
        return _clone(script.get("target_window"))
    if script.get("target") not in (None, "", {}):
        return _clone(script.get("target"))
    conditions = script.get("conditions")
    if isinstance(conditions, dict):
        for key in ("target_window", "window_title", "window_appears", "target"):
            if conditions.get(key) not in (None, "", {}):
                return _clone(conditions.get(key))
    return None


def _walk_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in steps:
        if isinstance(step, dict):
            out.append(step)
            for key in ("then", "else", "body"):
                nested = step.get(key)
                if isinstance(nested, list):
                    out.extend(_walk_steps(nested))
    return out


def _default_safety(steps: list[dict[str, Any]]) -> dict[str, Any]:
    flat = _walk_steps(steps)
    has_desktop_mutation = any(step.get("tool") in _DESKTOP_MUTATION_TOOLS for step in flat)
    has_shell = any(step.get("tool") in _SHELL_TOOLS for step in flat)
    return {
        "requires_preflight": has_desktop_mutation or has_shell,
        "mutates_desktop": has_desktop_mutation,
        "uses_shell": has_shell,
        "desktop_action_queue_required": has_desktop_mutation,
        "shell_requires_explicit_runtime": has_shell,
    }


def automation_ir_from_wps(
    script: dict[str, Any],
    *,
    name: str | None = None,
    ir_id: str | None = None,
) -> dict[str, Any]:
    """Wrap a validated WPS script in Automation IR v1 metadata."""
    steps = _clone(script.get("steps") or [])
    raw_conditions = script.get("conditions")
    conditions: dict[str, Any] = {}
    if isinstance(raw_conditions, dict):
        conditions.update(_clone(raw_conditions))
    elif isinstance(raw_conditions, list):
        conditions["notes"] = _clone(raw_conditions)

    target_window = _target_window_from_wps(script)
    if target_window is not None:
        conditions["target_window"] = target_window

    raw_evidence = script.get("evidence")
    evidence = (
        {"required": _clone(raw_evidence)}
        if isinstance(raw_evidence, list)
        else _clone(raw_evidence) if isinstance(raw_evidence, dict)
        else {"required": []}
    )

    task = _nonempty_str(script.get("task"))
    source = {
        "kind": "wps",
        "format": script.get("format") or "wps/2",
        "source": script.get("source") or "",
        "source_run": script.get("source_run") or "",
        "task": task,
    }

    return {
        "format": AUTOMATION_IR_FORMAT,
        "id": ir_id or f"air-{uuid.uuid4().hex[:12]}",
        "name": name or _nonempty_str(script.get("name")) or task or "Automation",
        "created": _created_at(script.get("created")),
        "source": source,
        "conditions": conditions,
        "evidence": evidence,
        "steps": steps,
        "variables": _clone(script.get("vars") or script.get("variables") or {}),
        "safety": _default_safety(steps),
        "fallback": {
            "on_preflight_fail": "live_agent_takeover",
            "on_step_fail": "resume_live_from_failed_step",
        },
    }


def wps_from_automation_ir(ir: dict[str, Any]) -> dict[str, Any]:
    """Convert an Automation IR v1 document back to a `.wps.json` script."""
    err = validate_automation_ir(ir)
    if err:
        raise ValueError(err)

    source = ir.get("source") or {}
    if source.get("kind") != "wps":
        raise ValueError("only WPS-sourced Automation IR can be converted to .wps.json")
    conditions = _clone(ir.get("conditions") or {})
    script: dict[str, Any] = {
        "format": "wps/2",
        "name": _nonempty_str(ir.get("name")) or "Automation",
        "task": _nonempty_str(source.get("task")),
        "source": "automation-ir",
        "source_ir": ir.get("id"),
        "created": _created_at(ir.get("created")),
        "vars": _clone(ir.get("variables") or {}),
        "steps": _clone(ir.get("steps") or []),
    }
    target_window = conditions.get("target_window")
    if target_window not in (None, "", {}):
        script["target_window"] = target_window
    if conditions:
        script["conditions"] = conditions
    evidence = ir.get("evidence")
    if isinstance(evidence, dict) and evidence.get("required"):
        script["evidence"] = _clone(evidence["required"])
    elif isinstance(evidence, list) and evidence:
        script["evidence"] = _clone(evidence)
    return script


def validate_automation_ir(data: Any) -> str | None:
    """Return None when valid, otherwise a concise error string."""
    if not isinstance(data, dict):
        return "Automation IR must be a JSON object"
    if data.get("format") != AUTOMATION_IR_FORMAT:
        return f"unknown Automation IR format: {data.get('format')!r}"
    if not _nonempty_str(data.get("id")):
        return "Automation IR requires id"
    if not _nonempty_str(data.get("name")):
        return "Automation IR requires name"
    for key in ("source", "conditions", "evidence", "safety", "fallback"):
        if not isinstance(data.get(key), dict):
            return f"Automation IR {key} must be an object"
    if not isinstance(data.get("variables"), dict):
        return "Automation IR variables must be an object"
    steps = data.get("steps")
    if not isinstance(steps, list):
        return "Automation IR steps must be a list"
    for i, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            return f"steps[{i}] must be an object"
        if not _nonempty_str(step.get("tool")):
            return f"steps[{i}] requires tool"
    return None
