"""Adapters from ARP node kinds to the existing WinPilot runtimes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..agent.router import build_runner
from ..agent.runtime_profile import build_runtime_profile
from ..agent.tools import ToolSession
from ..config import SCRIPTS_DIR
from ..recorder import trace as trace_mod
from ..utils.log import BUS
from .contracts import ArpNode, ArpNodeKind
from .runtime import NodeHandler, NodeResult


RunnerCallback = Callable[[Any], None]


def _json_result(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except (TypeError, json.JSONDecodeError):
        return {"ok": False, "text": text}
    return value if isinstance(value, dict) else {"ok": False, "value": value}


def _agent_handler(on_runner: RunnerCallback | None = None) -> NodeHandler:
    def run(node: ArpNode, context: dict[str, Any], stop_event) -> NodeResult:
        goal = str(node.config.get("goal") or node.config.get("task") or node.name or "").strip()
        if not goal:
            return NodeResult(ok=False, error="agent_goal requires config.goal")
        max_steps = node.config.get("max_steps")
        try:
            max_steps_override = int(max_steps) if max_steps else None
        except (TypeError, ValueError):
            max_steps_override = None
        runner = build_runner(goal, stop_event=stop_event, max_steps_override=max_steps_override)
        if on_runner:
            on_runner(runner)
        BUS.publish(
            "arp_child_run",
            run_id=context["run_id"],
            node_id=node.id,
            child_run_id=runner.run_id,
            child_kind="agent",
            task=goal,
        )
        result = runner.run()
        ok = runner.status == "done"
        return NodeResult(
            ok=ok,
            branch="next" if ok else "fail",
            output={
                "child_run_id": runner.run_id,
                "status": runner.status,
                "result": result,
                "usage": dict(runner.usage_total),
            },
            evidence=[runner.result_text] if ok and runner.result_text else [],
            error="" if ok else runner.result_text or f"agent ended with {runner.status}",
        )

    return run


def _batch_handler(node: ArpNode, context: dict[str, Any], stop_event) -> NodeResult:
    actions = node.config.get("actions")
    if not isinstance(actions, list) or not actions:
        return NodeResult(ok=False, error="action_batch requires config.actions")
    task = str(node.config.get("subgoal") or node.name or "ARP GUI action batch")
    session = ToolSession(stop_event=stop_event, task=task, runtime_profile=build_runtime_profile(task))
    session.run_id = context["run_id"]
    try:
        result = session.dispatch(
            "run_action_batch",
            {
                "actions": actions,
                "subgoal": task,
                "stop_on_error": bool(node.config.get("stop_on_error", True)),
                **({"sync": node.config["sync"]} if "sync" in node.config else {}),
            },
        )
        data = _json_result(result.text)
        ok = bool(data.get("ok")) and not data.get("failed_at")
        verified = bool(data.get("verified")) or bool(
            isinstance(data.get("controller_state"), dict)
            and data["controller_state"].get("verified")
        )
        evidence = []
        if verified:
            evidence.append({"type": "controller", "subgoal": task, "verified": True})
        return NodeResult(
            ok=ok,
            branch="next" if ok else "fail",
            output=data,
            evidence=evidence,
            error="" if ok else str(data.get("error") or result.text),
        )
    finally:
        session.close()


def _skill_handler(node: ArpNode, context: dict[str, Any], stop_event) -> NodeResult:
    script_name = str(node.config.get("script") or "").strip()
    if not script_name:
        return NodeResult(ok=False, error="skill_replay requires config.script")
    path = (SCRIPTS_DIR / script_name).resolve()
    try:
        path.relative_to(SCRIPTS_DIR.resolve())
    except ValueError:
        return NodeResult(ok=False, error="skill script escapes the scripts directory")
    if not path.exists():
        return NodeResult(ok=False, error=f"skill script not found: {script_name}")
    BUS.publish(
        "arp_child_run",
        run_id=context["run_id"],
        node_id=node.id,
        child_kind="skill_replay",
        script=script_name,
    )
    result = trace_mod.replay(path, stop_event=stop_event)
    ok = bool(result.get("ok"))
    return NodeResult(
        ok=ok,
        branch="next" if ok else "fail",
        output=result,
        evidence=[{"type": "skill_replay", "script": script_name, "ok": True}] if ok else [],
        error="" if ok else str(result.get("error") or "skill replay failed"),
    )


def _legacy_handler(node: ArpNode, context: dict[str, Any], stop_event) -> NodeResult:
    node_type = str(node.config.get("node_type") or "").strip()
    params = node.config.get("params") if isinstance(node.config.get("params"), dict) else {}
    action: dict[str, Any] | None = None
    if node_type == "click":
        action = {"action": "click", "x": params.get("x"), "y": params.get("y")}
    elif node_type == "click_text":
        action = {"action": "click_element", "label": params.get("text")}
    elif node_type == "input_text":
        action = {"action": "type_text", "text": params.get("text", "")}
        if params.get("x") is not None and params.get("y") is not None:
            action.update({"x": params.get("x"), "y": params.get("y")})
    elif node_type == "keyboard":
        action = {"action": "hotkey", "keys": params.get("keys") or []}
    elif node_type == "focus_window":
        action = {"action": "focus_window", "title": params.get("title") or params.get("window_title")}
    elif node_type == "launch_program":
        action = {"action": "launch", "command": params.get("path") or params.get("program")}
    if action is None:
        return NodeResult(
            ok=False,
            error=(
                f"legacy node {node_type!r} has no native ARP adapter; "
                "run it through the Electron feature bridge or migrate it to an ARP capability"
            ),
        )
    adapted = node.model_copy(update={
        "kind": ArpNodeKind.ACTION_BATCH,
        "config": {"actions": [action], "subgoal": node.name or node_type},
    })
    return _batch_handler(adapted, context, stop_event)


def default_handlers(on_runner: RunnerCallback | None = None) -> dict[ArpNodeKind, NodeHandler]:
    return {
        ArpNodeKind.AGENT_GOAL: _agent_handler(on_runner),
        ArpNodeKind.ACTION_BATCH: _batch_handler,
        ArpNodeKind.SKILL_REPLAY: _skill_handler,
        ArpNodeKind.LEGACY_FLOW: _legacy_handler,
    }
