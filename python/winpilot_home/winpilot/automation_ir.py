"""Automation IR v1: canonical handoff for replay, FlowScript, and skills.

Legacy WPS and FlowScript documents remain import/export formats. New saved
automations and skills use this validated envelope as their runtime boundary.
"""
from __future__ import annotations

import copy
import hashlib
import json
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
_SHELL_TOOLS = frozenset({"run_shell", "run_detached", "ext.run_shell"})


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
    export_manifest = script.get("export_manifest")
    if isinstance(export_manifest, dict):
        source["export_manifest"] = _clone(export_manifest)
    source_runs = script.get("source_runs")
    if isinstance(source_runs, list):
        source["source_runs"] = _clone(source_runs)

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
            "on_preflight_fail": "stop",
            "on_step_fail": "stop",
        },
        "runtime": {"kind": "wps_replay"},
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
        "safety": _clone(ir.get("safety") or {}),
        "fallback": _clone(ir.get("fallback") or {}),
    }
    target_window = conditions.get("target_window")
    if target_window not in (None, "", {}):
        script["target_window"] = target_window
    if conditions:
        script["conditions"] = conditions
    export_manifest = source.get("export_manifest")
    if isinstance(export_manifest, dict):
        script["export_manifest"] = _clone(export_manifest)
    source_runs = source.get("source_runs")
    if isinstance(source_runs, list):
        script["source_runs"] = _clone(source_runs)
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
    source = data.get("source") or {}
    if not _nonempty_str(source.get("kind")):
        return "Automation IR source.kind is required"
    source_kind = _nonempty_str(source.get("kind"))
    if source_kind not in {"wps", "flowscript"}:
        return f"unsupported Automation IR source.kind: {source_kind!r}"
    if source_kind == "flowscript":
        node_ids: list[str] = []
        for index, step in enumerate(steps, start=1):
            node_id = _nonempty_str(step.get("node_id"))
            if not node_id:
                return f"steps[{index}] requires node_id for flowscript source"
            node_ids.append(node_id)
            if not isinstance(step.get("params"), dict):
                return f"steps[{index}].params must be an object"
            outgoing = step.get("outgoing")
            if outgoing is not None and not isinstance(outgoing, list):
                return f"steps[{index}].outgoing must be a list"
        if len(node_ids) != len(set(node_ids)):
            return "flowscript Automation IR node_id values must be unique"
        known = set(node_ids)
        for index, step in enumerate(steps, start=1):
            for edge_index, edge in enumerate(step.get("outgoing") or [], start=1):
                if not isinstance(edge, dict):
                    return f"steps[{index}].outgoing[{edge_index}] must be an object"
                target = _nonempty_str(edge.get("to_node"))
                if not target or target not in known:
                    return (
                        f"steps[{index}].outgoing[{edge_index}] references "
                        f"unknown to_node {target!r}"
                    )
    runtime = data.get("runtime")
    if runtime is not None:
        if not isinstance(runtime, dict):
            return "Automation IR runtime must be an object"
        kind = _nonempty_str(runtime.get("kind"))
        if kind not in {"wps_replay", "arp_graph"}:
            return f"unsupported Automation IR runtime kind: {kind or '<empty>'}"
    return None


def automation_runtime_kind(ir: dict[str, Any]) -> str:
    runtime = ir.get("runtime")
    if isinstance(runtime, dict):
        kind = _nonempty_str(runtime.get("kind"))
        if kind:
            return kind
    source = ir.get("source")
    if isinstance(source, dict) and source.get("kind") == "wps":
        return "wps_replay"
    return ""


def automation_projection_fingerprint(
    steps: list[dict[str, Any]],
    variables: dict[str, Any],
    graph: dict[str, Any] | None = None,
) -> str:
    content: dict[str, Any] = {"steps": steps, "variables": variables}
    if graph is not None:
        content["graph"] = graph
    payload = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _legacy_projection_fingerprint(
    steps: list[dict[str, Any]],
    variables: dict[str, Any],
    graph: dict[str, Any] | None = None,
) -> str:
    content: dict[str, Any] = {"steps": steps, "variables": variables}
    if graph is not None:
        content["graph"] = graph
    payload = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    value = 0x811C9DC5
    for byte in payload:
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return f"fnv1a32:{value:08x}"


def arp_graph_from_automation_ir(
    ir: dict[str, Any],
    *,
    require_valid: bool = True,
):
    """Return a validated ArpGraph from the canonical runtime projection."""
    error = validate_automation_ir(ir)
    if error:
        raise ValueError(error)
    runtime = ir.get("runtime")
    if not isinstance(runtime, dict) or runtime.get("kind") != "arp_graph":
        raise ValueError(
            "Automation IR has no canonical ARP runtime graph; "
            "reopen the source in Studio and save it again"
        )
    raw_graph = runtime.get("graph")
    if not isinstance(raw_graph, dict):
        raise ValueError("Automation IR arp_graph runtime requires graph object")
    expected = _nonempty_str(runtime.get("projection_fingerprint"))
    if not expected:
        raise ValueError("Automation IR arp_graph runtime requires projection_fingerprint")
    fingerprint_args = (
        ir.get("steps") or [],
        ir.get("variables") or {},
        raw_graph,
    )
    actual = (
        _legacy_projection_fingerprint(*fingerprint_args)
        if expected.startswith("fnv1a32:")
        else automation_projection_fingerprint(*fingerprint_args)
    )
    if expected != actual:
        raise ValueError(
            "Automation IR runtime projection is stale: "
            f"expected {expected}, calculated {actual}"
        )
    from .orchestrator.contracts import ArpGraph, validate_graph

    try:
        graph = ArpGraph.model_validate(raw_graph)
    except Exception as exc:
        raise ValueError(f"Automation IR ARP graph parse failed: {exc}") from exc
    if require_valid:
        validation = validate_graph(graph)
        if not validation.valid:
            raise ValueError("; ".join(validation.errors))
    return graph


def validate_automation_runtime(ir: dict[str, Any]) -> str | None:
    """Validate the executable projection, not just the IR envelope."""
    error = validate_automation_ir(ir)
    if error:
        return error
    kind = automation_runtime_kind(ir)
    if kind == "wps_replay":
        try:
            expected_safety = _default_safety(ir.get("steps") or [])
            safety = ir.get("safety") or {}
            for field, required in expected_safety.items():
                if required is True and safety.get(field) is not True:
                    return f"Automation IR safety.{field} must be true for its WPS steps"
            script = wps_from_automation_ir(ir)
            from .recorder.trace import validate_script

            return validate_script(script)
        except ValueError as exc:
            return str(exc)
    if kind == "arp_graph":
        try:
            arp_graph_from_automation_ir(ir)
        except ValueError as exc:
            return str(exc)
        return None
    return "Automation IR requires a supported runtime.kind"
