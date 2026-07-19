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

_REPLAY_SHELL_TOOLS = {"run_shell", "ext.run_shell", "run_detached"}
_REPLAY_DESKTOP_TOOLS = {
    "click", "type_text", "hotkey", "launch", "focus_window", "scroll",
    "drag", "draw_strokes", "run_detached", "tray_click", "ext.clipboard_set",
}


def _publish_anchor_metrics(result: dict[str, Any], context: dict[str, Any], node: ArpNode) -> None:
    metrics = result.get("anchor_cache")
    if not isinstance(metrics, dict) or not metrics.get("enabled"):
        return
    BUS.publish(
        "replay_anchor_cache",
        run_id=context["run_id"],
        node_id=node.id,
        **metrics,
    )


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
        graph = context.get("graph") if isinstance(context.get("graph"), dict) else {}
        policy = graph.get("policy") if isinstance(graph.get("policy"), dict) else {}
        profile = build_runtime_profile(goal)
        runner = build_runner(
            goal,
            stop_event=stop_event,
            max_steps_override=max_steps_override,
            # ARP's allow_shell flag is a replay safety boundary. It must not
            # silently remove the Coding Runtime's own deterministic shell
            # capability from a mixed coding+desktop goal created by the Agent
            # frontend. Desktop-only goals still keep the conservative policy.
            allow_shell_tools=bool(policy.get("allow_shell", False) or profile.coding),
        )
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
        evidence: list[dict[str, Any]] = []
        if ok and bool(getattr(runner, "_done_verified", False)):
            evidence.append({
                "predicate": "agent.done",
                "value": goal,
                "source": "done_gate",
                "verified": True,
                "action_id": runner.run_id,
            })
            for item in (getattr(runner, "_visible_evidence_ledger", []) or [])[-20:]:
                target = str(item.get("target") or "").strip()
                if not target:
                    continue
                evidence.append({
                    "predicate": "visible.text",
                    "value": target,
                    "source": str(item.get("source") or "agent_observe"),
                    "verified": True,
                    "text": str(item.get("snippet") or "")[:500],
                    "action_id": runner.run_id,
                })
        return NodeResult(
            ok=ok,
            branch="next" if ok else "fail",
            output={
                "child_run_id": runner.run_id,
                "status": runner.status,
                "result": result,
                "usage": dict(runner.usage_total),
            },
            evidence=evidence,
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
            evidence.append({
                "predicate": "controller.batch",
                "value": task,
                "source": "fast_gui_controller",
                "verified": True,
            })
        return NodeResult(
            ok=ok,
            branch="next" if ok else "fail",
            output=data,
            evidence=evidence,
            error="" if ok else str(data.get("error") or result.text),
        )
    finally:
        session.close()


def _walk_replay_tools(step: dict[str, Any]) -> set[str]:
    tools = {str(step.get("tool") or "")}
    for key in ("then", "else", "body"):
        nested = step.get(key)
        if isinstance(nested, list):
            for child in nested:
                if isinstance(child, dict):
                    tools.update(_walk_replay_tools(child))
    return tools


def _replay_step_handler(node: ArpNode, context: dict[str, Any], stop_event) -> NodeResult:
    step = node.config.get("step")
    if not isinstance(step, dict):
        return NodeResult(ok=False, branch="fail", error="replay_step requires config.step")

    tools = _walk_replay_tools(step)
    policy = ((context.get("graph") or {}).get("policy") or {})
    if tools & _REPLAY_SHELL_TOOLS and not policy.get("allow_shell", False):
        return NodeResult(ok=False, branch="fail", error="replay_step shell execution is disabled by graph policy")
    if tools & _REPLAY_DESKTOP_TOOLS and not policy.get("allow_desktop_mutation", True):
        return NodeResult(ok=False, branch="fail", error="replay_step desktop mutation is disabled by graph policy")

    runtime_state = context.get("runtime_state")
    if not isinstance(runtime_state, dict):
        return NodeResult(ok=False, branch="fail", error="replay_step requires an ARP runtime session")
    sessions = runtime_state.setdefault("replay_sessions", {})
    session_id = str(node.config.get("session") or "default").strip() or "default"
    replayer = sessions.get(session_id)
    if not isinstance(replayer, trace_mod.Replayer):
        replayer = trace_mod.Replayer(stop_event)
        sessions[session_id] = replayer
    graph_id = str(((context.get("graph") or {}).get("id") or "graph")).strip() or "graph"
    configure_cache = getattr(replayer, "configure_anchor_cache", None)
    if callable(configure_cache):
        configure_cache(f"arp:{graph_id}:{session_id}")

    try:
        pace_ms = max(0, int(node.config.get("pace_ms") or 0))
    except (TypeError, ValueError):
        pace_ms = 0
    replayer.step_delay = pace_ms / 1000.0
    variables = context.get("variables") if isinstance(context.get("variables"), dict) else {}
    seeded_variables = {str(key): str(value) for key, value in variables.items()}
    try:
        result = replayer.run_steps(
            [step],
            target_window=node.config.get("target_window"),
            variables=variables,
            prefix=f"{node.id}.",
        )
    except Exception as exc:
        return NodeResult(ok=False, branch="fail", error=f"replay step failed: {exc}")
    _publish_anchor_metrics(result, context, node)

    # Preserve native ARP value types unless the WPS segment actually changed
    # or introduced that variable.
    for key, value in replayer.vars.items():
        if key not in seeded_variables or value != seeded_variables[key]:
            variables[key] = value
    verified_steps = [item for item in result.get("results", []) if item.get("ok") is True]
    ok = bool(result.get("ok")) and bool(verified_steps)
    if result.get("ok") and not verified_steps:
        result = {**result, "ok": False, "error": "replay step produced no verified action"}
    output = {
        **result,
        "session": session_id,
        "tool": step.get("tool"),
        "verified_steps": len(verified_steps),
    }
    return NodeResult(
        ok=ok,
        branch="next" if ok else "fail",
        output=output,
        evidence=[{
            "predicate": "replay.step",
            "value": step.get("tool"),
            "source": "wps_replayer",
            "verified": True,
            "metadata": {
                "session": session_id,
                "verified_steps": len(verified_steps),
            },
        }] if ok else [],
        error="" if ok else str(result.get("error") or "replay step failed"),
    )


def _skill_handler(node: ArpNode, context: dict[str, Any], stop_event) -> NodeResult:
    script_name = str(node.config.get("script") or "").strip()
    recipe: dict[str, Any] | None = None
    memory_id = str(node.config.get("memory_id") or "").strip()
    task = str(node.config.get("task") or node.name or "").strip()
    if not script_name:
        try:
            stage_type = str(node.config.get("stage_type") or "")
            if stage_type:
                script_name, recipe = experience.reusable_stage_script_for(
                    stage_type,
                    dict(node.config.get("parameters") or {}),
                    memory_id,
                    app_version=str(node.config.get("app_version") or ""),
                )
            else:
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
            try:
                experience.update(memory_id, {"failure_reason": str(exc)})
            except Exception:
                pass
        return NodeResult(ok=False, branch="fail", error=f"skill replay failed: {exc}")
    _publish_anchor_metrics(result, context, node)
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
        try:
            experience.update(memory_id, {
                "failure_reason": "" if ok else str(result.get("error") or "stage replay validation failed"),
            })
        except Exception:
            pass
    output = {
        **result,
        "script": script_name,
        "memory_id": memory_id or None,
        "memory_replay_ok": ok,
    }
    evidence = [{
        "predicate": "skill.replay",
        "value": script_name,
        "source": "memory_skill_replayer",
        "verified": True,
        "metadata": {
            "memory_id": memory_id or None,
            "verified_steps": len(verified_steps),
            "stage_id": str(node.config.get("stage_id") or ""),
            "stage_type": str(node.config.get("stage_type") or ""),
        },
    }] if ok else []
    if ok:
        provided = [str(item) for item in (node.config.get("provides_evidence") or []) if str(item).strip()]
        if provided:
            from ..tasking.evidence import EvidenceRegistry

            registry = context["runtime_state"].get("evidence_registry")
            if not isinstance(registry, EvidenceRegistry):
                registry = EvidenceRegistry(context["run_id"])
                context["runtime_state"]["evidence_registry"] = registry
            for predicate in provided:
                record = registry.record(
                    predicate,
                    provider="verified_stage_skill",
                    verified=True,
                    stage_id=str(node.config.get("stage_id") or ""),
                    metadata={
                        "memory_id": memory_id or None,
                        "script": script_name,
                        "verified_steps": len(verified_steps),
                        "parameters": node.config.get("parameters") or {},
                    },
                )
                evidence.append(record)
    return NodeResult(
        ok=ok,
        branch="next" if ok else "fail",
        output=output,
        evidence=evidence,
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
    from ..tasking.stage_runtime import task_stage_handler

    return {
        ArpNodeKind.AGENT_GOAL: _agent_handler(on_runner),
        ArpNodeKind.ACTION_BATCH: _batch_handler,
        ArpNodeKind.REPLAY_STEP: _replay_step_handler,
        ArpNodeKind.SKILL_REPLAY: _skill_handler,
        ArpNodeKind.TASK_STAGE: task_stage_handler,
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
