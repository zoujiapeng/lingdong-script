"""Adapters from ARP node kinds to the existing WinPilot runtimes."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..agent.router import build_runner
from ..agent.runtime_profile import build_runtime_profile
from ..agent.tools import ToolSession
from ..config import SCRIPTS_DIR
from ..memory import experience
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
    recipe: dict[str, Any] | None = None
    memory_id = str(node.config.get("memory_id") or "").strip()
    task = str(node.config.get("task") or node.name or "").strip()
    if not script_name:
        try:
            script_name, recipe = experience.reusable_script_for(task, memory_id)
        except Exception as exc:
            return NodeResult(ok=False, branch="fail", error=f"skill lookup failed: {exc}")
        memory_id = str((recipe or {}).get("id") or memory_id)
    if not script_name:
        return NodeResult(
            ok=True,
            branch="miss",
            output={"memory_id": memory_id or None, "task": task, "reason": "no_verified_skill"},
            error="",
        )
    path = (SCRIPTS_DIR / script_name).resolve()
    try:
        path.relative_to(SCRIPTS_DIR.resolve())
    except ValueError:
        return NodeResult(ok=False, branch="fail", error="skill script escapes the scripts directory")
    if not path.exists():
        return NodeResult(ok=False, branch="fail", error=f"skill script not found: {script_name}")
    BUS.publish(
        "arp_child_run",
        run_id=context["run_id"],
        node_id=node.id,
        child_kind="skill_replay",
        script=script_name,
        memory_id=memory_id or None,
        task=task,
    )
    if memory_id:
        BUS.publish("memory", action="reuse", task=task, id=memory_id, run_id=context["run_id"])
        try:
            experience._store().record_attempt(
                memory_id,
                "used",
                run_id=context["run_id"],
                status="arp_skill_replay",
                detail={"task": task, "script": script_name},
            )
        except Exception:
            pass
    try:
        result = trace_mod.replay(path, stop_event=stop_event)
    except Exception as exc:
        if memory_id:
            try:
                experience._store().record_attempt(
                    memory_id,
                    "replay_failed",
                    run_id=context["run_id"],
                    status="exception",
                    success=False,
                    detail={"task": task, "script": script_name, "error": str(exc)},
                )
            except Exception:
                pass
        return NodeResult(ok=False, branch="fail", error=f"skill replay failed: {exc}")
    verified_steps = [step for step in (result.get("results") or []) if step.get("ok") is True]
    ok = bool(result.get("ok")) and bool(verified_steps)
    if result.get("ok") and not verified_steps:
        result = {**result, "ok": False, "error": "skill replay produced no verified steps"}
    if memory_id:
        try:
            experience._store().record_attempt(
                memory_id,
                "replay_ok" if ok else "replay_failed",
                run_id=context["run_id"],
                status="ok" if ok else "failed_step",
                success=ok,
                detail={"task": task, "script": script_name},
            )
        except Exception:
            pass
    output = {
        **result,
        "script": script_name,
        "memory_id": memory_id or None,
        "memory_replay_ok": ok,
    }
    return NodeResult(
        ok=ok,
        branch="next" if ok else "fail",
        output=output,
        evidence=[{
            "type": "skill_replay",
            "script": script_name,
            "memory_id": memory_id or None,
            "verified_steps": len(verified_steps),
            "ok": True,
        }] if ok else [],
        error="" if ok else str(result.get("error") or "skill replay failed"),
    )


def _legacy_actions(node_type: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if node_type == "click":
        method = str(params.get("method") or "single").casefold()
        actions.append({
            "action": "click",
            "x": params.get("x"),
            "y": params.get("y"),
            "button": method if method in {"right", "middle"} else "left",
            "double": method == "double",
        })
    elif node_type == "click_text":
        actions.append({
            "action": "click_element",
            "label": params.get("text"),
            "fallback_text": params.get("fallback_text") or [],
        })
    elif node_type == "input_text":
        if params.get("click_before", True) and params.get("x") is not None and params.get("y") is not None:
            actions.append({"action": "click", "x": params.get("x"), "y": params.get("y")})
        if params.get("clear_before"):
            actions.append({"action": "hotkey", "keys": ["ctrl", "a"]})
        actions.append({"action": "type_text", "text": params.get("text", "")})
    elif node_type == "keyboard":
        keys = [str(key) for key in (params.get("modifiers") or [])]
        keys.extend(str(key) for key in (params.get("keys") or []))
        actions.append({"action": "hotkey", "keys": list(dict.fromkeys(keys))})
    elif node_type == "focus_window":
        actions.append({
            "action": "focus_window",
            "title": params.get("title") or params.get("window_title"),
        })
    elif node_type == "launch_program":
        command = str(params.get("target") or params.get("path") or params.get("program") or "").strip()
        arguments = str(params.get("args") or "").strip()
        actions.append({"action": "launch", "command": f"{command} {arguments}".strip()})
    delay_after = params.get("delay_after")
    if isinstance(delay_after, (int, float)) and delay_after > 0:
        actions.append({"action": "wait", "duration_ms": int(float(delay_after) * 1000)})
    return actions


def _legacy_handler(node: ArpNode, context: dict[str, Any], stop_event) -> NodeResult:
    node_type = str(node.config.get("node_type") or "").strip()
    params = node.config.get("params") if isinstance(node.config.get("params"), dict) else {}
    actions = _legacy_actions(node_type, params)
    if not actions:
        return NodeResult(
            ok=False,
            branch="fail",
            error=(
                f"legacy node {node_type!r} has no native ARP adapter; "
                "run it through the Electron feature bridge or migrate it to an ARP capability"
            ),
        )
    adapted = node.model_copy(update={
        "kind": ArpNodeKind.ACTION_BATCH,
        "config": {"actions": actions, "subgoal": node.name or node_type},
    })
    return _batch_handler(adapted, context, stop_event)


def default_handlers(on_runner: RunnerCallback | None = None) -> dict[ArpNodeKind, NodeHandler]:
    return {
        ArpNodeKind.AGENT_GOAL: _agent_handler(on_runner),
        ArpNodeKind.ACTION_BATCH: _batch_handler,
        ArpNodeKind.SKILL_REPLAY: _skill_handler,
        ArpNodeKind.LEGACY_FLOW: _legacy_handler,
    }


def finalize_memory_feedback(execution: Any) -> None:
    """Score replayed memories against the final ARP outcome, not the local step."""
    successful_run = getattr(getattr(execution, "status", None), "value", "") == "done"
    positive: set[str] = set()
    negative: set[str] = set()
    for output in getattr(execution, "outputs", {}).values():
        if not isinstance(output, dict) or not isinstance(output.get("memory_replay_ok"), bool):
            continue
        memory_id = str(output.get("memory_id") or "").strip()
        if not memory_id:
            continue
        if output["memory_replay_ok"] and successful_run:
            positive.add(memory_id)
        else:
            negative.add(memory_id)
    negative -= positive
    if positive:
        experience.feedback(sorted(positive), success=True)
    if negative:
        experience.feedback(sorted(negative), success=False)
    for memory_id in sorted(positive | negative):
        BUS.publish(
            "memory",
            action="arp_feedback",
            id=memory_id,
            success=memory_id in positive,
            run_id=getattr(execution, "run_id", ""),
        )
