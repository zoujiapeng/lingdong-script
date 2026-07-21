"""Projection of typed task contracts into executable ARP graphs."""
from __future__ import annotations

from ..tasking.contracts import StageType, TaskContract
from .contracts import ArpEdge, ArpGraph, ArpNode, ArpNodeKind, ArpPolicy, ArpRetryPolicy


def task_contract_to_arp(contract: TaskContract) -> ArpGraph:
    nodes = [ArpNode(id="start", kind=ArpNodeKind.START, name="开始", x=0, y=0)]
    edges: list[ArpEdge] = []
    previous = "start"
    x = 0.0
    y = 120.0
    persistent_interaction = False
    bounded_interaction = False
    interaction_visit_budget = (
        max(
            2,
            min(
                1000,
                int(contract.session.max_model_calls)
                + int(contract.session.max_replans)
                + 2,
            ),
        )
        if contract.session is not None
        else 30
    )

    for stage in contract.stages:
        stage_node = ArpNode(
            id=stage.id,
            kind=ArpNodeKind.TASK_STAGE,
            name=stage.name,
            config={
                "stage": stage.model_dump(mode="json"),
                "contract_id": contract.task_id,
                "task": contract.task,
            },
            retry=ArpRetryPolicy(
                # Stage failure recovery must reobserve/redecide explicitly;
                # re-running a whole stage can duplicate unknown mutations.
                max_attempts=1,
                delay_ms=250,
                backoff=1.5,
            ),
            timeout_s=(
                min(
                    3600.0,
                    float(contract.session.external_change_timeout_s) + 15.0,
                )
                if stage.type == StageType.WAIT_FOR_EXTERNAL_CHANGE
                else max(
                    180,
                    min(1800, int(stage.parameters.get("max_steps") or 1) * 30),
                )
                if stage.execution and stage.execution.model_role != "none"
                else 90
            ),
            x=x,
            y=y,
        )
        if stage.allow_skill_replay:
            skill_id = f"skill-{stage.id}"
            skill_node = ArpNode(
                id=skill_id,
                kind=ArpNodeKind.SKILL_REPLAY,
                name=f"复用阶段技能：{stage.name}",
                config={
                    "task": f"{stage.type.value}: {stage.name}",
                    "stage_id": stage.id,
                    "stage_type": stage.type.value,
                    "parameters": stage.parameters,
                    "provides_evidence": [
                        obligation.id for obligation in stage.obligations
                    ],
                    "resume_on_failure": stage.id,
                },
                x=x,
                y=y,
            )
            nodes.append(skill_node)
            edges.append(ArpEdge(id=f"{previous}-{skill_id}", source=previous, target=skill_id))
            edges.append(ArpEdge(id=f"{skill_id}-miss-{stage.id}", source=skill_id, target=stage.id, branch="miss", label="未匹配"))
            edges.append(ArpEdge(id=f"{skill_id}-fail-{stage.id}", source=skill_id, target=stage.id, branch="fail", label="从本阶段恢复"))
            previous_for_stage = skill_id
            y += 120.0
            stage_node.y = y
        else:
            edges.append(ArpEdge(id=f"{previous}-{stage.id}", source=previous, target=stage.id))
            previous_for_stage = ""
        nodes.append(stage_node)
        if previous_for_stage:
            # Successful replay skips only the matching stage and continues at
            # the next typed stage.  The edge is added once that next node is
            # known; retain it in metadata for the loop below.
            stage_node.config["_success_skill_node"] = previous_for_stage
        previous = stage.id
        y += 120.0

    # Stitch successful stage-skill routes to the next stage (or end).
    end_id = "end"
    for index, stage in enumerate(contract.stages):
        node = next(item for item in nodes if item.id == stage.id)
        skill_id = str(node.config.pop("_success_skill_node", ""))
        if not skill_id:
            continue
        target = contract.stages[index + 1].id if index + 1 < len(contract.stages) else end_id
        # If the next stage itself has a skill gate, enter that gate.
        if index + 1 < len(contract.stages) and contract.stages[index + 1].allow_skill_replay:
            target = f"skill-{contract.stages[index + 1].id}"
        edges.append(ArpEdge(id=f"{skill_id}-next-{target}", source=skill_id, target=target, branch="next", label="已复用"))

    nodes.append(ArpNode(id=end_id, kind=ArpNodeKind.END, name="完成", x=0, y=y))
    if contract.stages:
        persistent_interaction = bool(
            contract.session is not None
            and contract.session.lifecycle == "persistent"
            and any(
                stage.type == StageType.WAIT_FOR_EXTERNAL_CHANGE
                for stage in contract.stages
            )
        )
        bounded_interaction = bool(
            contract.session is not None
            and contract.session.lifecycle == "bounded"
            and any(
                stage.type == StageType.INFER_INTERACTION
                for stage in contract.stages
            )
            and any(
                stage.type == StageType.VERIFY_LOCAL_EFFECT
                for stage in contract.stages
            )
            and not any(
                stage.type == StageType.WAIT_FOR_EXTERNAL_CHANGE
                for stage in contract.stages
            )
        )
        if persistent_interaction or bounded_interaction:
            observe_stage = next(
                stage for stage in contract.stages
                if stage.type == StageType.OBSERVE_SCENE
            )
            infer_for_rebind = next(
                stage for stage in contract.stages
                if stage.type == StageType.INFER_INTERACTION
            )
            edges.append(ArpEdge(
                id=f"{infer_for_rebind.id}-reobserve-{observe_stage.id}",
                source=infer_for_rebind.id,
                target=observe_stage.id,
                branch="reobserve",
                label="重新观察以确认界面位移",
            ))
            # Recoverable interaction failures must re-enter through a fresh
            # authoritative observation.  This is deliberately a different
            # branch from PerformTurn's pre-mutation ``reobserve`` path: after
            # an uncertain controller outcome we must never replay or plan
            # from the old coordinates.
            for recover_stage in (
                item for item in contract.stages
                if item.type in {
                    StageType.OBSERVE_SCENE,
                    StageType.INFER_INTERACTION,
                    StageType.PERFORM_TURN,
                    StageType.VERIFY_LOCAL_EFFECT,
                }
            ):
                edges.append(ArpEdge(
                    id=f"{recover_stage.id}-recover-reobserve-{observe_stage.id}",
                    source=recover_stage.id,
                    target=observe_stage.id,
                    branch="recover_reobserve",
                    label="从新权威帧恢复当前语义回合",
                ))
        if persistent_interaction:
            wait_stage = next(
                stage for stage in contract.stages
                if stage.type == StageType.WAIT_FOR_EXTERNAL_CHANGE
            )
            infer_stage = next(
                stage for stage in contract.stages
                if stage.type == StageType.INFER_INTERACTION
            )
            perform_stage = next(
                stage for stage in contract.stages
                if stage.type == StageType.PERFORM_TURN
            )
            verify_stage = next(
                stage for stage in contract.stages
                if stage.type == StageType.VERIFY_LOCAL_EFFECT
            )
            edges.append(ArpEdge(
                id=f"{perform_stage.id}-reobserve-{infer_stage.id}",
                source=perform_stage.id,
                target=infer_stage.id,
                branch="reobserve",
                label="动作前帧已变化，重新推断",
            ))
            edges.append(ArpEdge(
                id=f"{verify_stage.id}-continue-local-{perform_stage.id}",
                source=verify_stage.id,
                target=perform_stage.id,
                branch="continue_local",
                label="local program continuation",
            ))
            # A live session advances only after the target-bound watcher has
            # proved an external ROI change.  ``complete`` remains reachable
            # only for a visibly proved semantic terminal state; a normal
            # verified turn proceeds to the existing sequential Wait stage.
            edges.append(ArpEdge(
                id=f"{wait_stage.id}-changed-{infer_stage.id}",
                source=wait_stage.id,
                target=infer_stage.id,
                branch="changed",
                label="外部变化后继续",
            ))
            edges.append(ArpEdge(
                id=f"{verify_stage.id}-continue-{infer_stage.id}",
                source=verify_stage.id,
                target=infer_stage.id,
                branch="continue",
                label="终态未证实，继续推断",
            ))
            downstream = next(
                (
                    edge for edge in edges
                    if edge.source == wait_stage.id and edge.branch == "next"
                ),
                None,
            )
            if downstream is not None:
                downstream.branch = "complete"
                downstream.label = "会话结束后继续"
                verify_complete_target = downstream.target
            else:
                edges.append(ArpEdge(
                    id=f"{wait_stage.id}-complete-{end_id}",
                    source=wait_stage.id,
                    target=end_id,
                    branch="complete",
                    label="会话结束",
                ))
                verify_complete_target = end_id
            edges.append(ArpEdge(
                id=f"{verify_stage.id}-complete-{verify_complete_target}",
                source=verify_stage.id,
                target=verify_complete_target,
                branch="complete",
                label="已验证会话终态",
            ))
            if previous != wait_stage.id:
                edges.append(ArpEdge(id=f"{previous}-end", source=previous, target=end_id))
        elif bounded_interaction:
            infer_stage = next(
                stage for stage in contract.stages
                if stage.type == StageType.INFER_INTERACTION
            )
            perform_stage = next(
                stage for stage in contract.stages
                if stage.type == StageType.PERFORM_TURN
            )
            verify_stage = next(
                stage for stage in contract.stages
                if stage.type == StageType.VERIFY_LOCAL_EFFECT
            )
            edges.append(ArpEdge(
                id=f"{perform_stage.id}-reobserve-{infer_stage.id}",
                source=perform_stage.id,
                target=infer_stage.id,
                branch="reobserve",
                label="动作前帧已变化，重新推断",
            ))
            edges.append(ArpEdge(
                id=f"{verify_stage.id}-continue-local-{perform_stage.id}",
                source=verify_stage.id,
                target=perform_stage.id,
                branch="continue_local",
                label="local program continuation",
            ))
            # The verifier, not the planner alone, selects the terminal route.
            # Partial but causally verified actions re-enter semantic inference
            # on the fresh post-action frame without re-running ObserveScene.
            edges.append(ArpEdge(
                id=f"{verify_stage.id}-continue-{infer_stage.id}",
                source=verify_stage.id,
                target=infer_stage.id,
                branch="continue",
                label="基于新帧继续",
            ))
            downstream = next(
                (
                    edge for edge in edges
                    if edge.source == verify_stage.id and edge.branch == "next"
                ),
                None,
            )
            if downstream is not None:
                downstream.branch = "complete"
                downstream.label = "交互完成后继续"
            else:
                edges.append(ArpEdge(
                    id=f"{verify_stage.id}-complete-{end_id}",
                    source=verify_stage.id,
                    target=end_id,
                    branch="complete",
                    label="已验证完成",
                ))
            if previous != verify_stage.id:
                edges.append(ArpEdge(id=f"{previous}-end", source=previous, target=end_id))
        else:
            edges.append(ArpEdge(id=f"{previous}-end", source=previous, target=end_id))
    else:
        edges.append(ArpEdge(id="start-end", source="start", target=end_id))
    return ArpGraph(
        id=contract.task_id,
        name=contract.task[:80] or "智能代理任务",
        description="由 TaskContractCompiler 生成的类型化阶段图。",
        nodes=nodes,
        edges=edges,
        variables={"task": contract.task},
        policy=ArpPolicy(
            max_node_visits=(
                interaction_visit_budget
                if persistent_interaction or bounded_interaction
                else 30
            ),
            fail_fast=True,
            require_evidence_on_done=bool(contract.obligations),
            allow_shell=False,
            allow_desktop_mutation=True,
        ),
        metadata={
            "source": "task-contract-compiler",
            "routing": "typed-stage-runtime",
            "task": contract.task,
            "experience_policy": contract.experience_policy,
            "task_contract": contract.model_dump(mode="json"),
            "satisfiability": {
                "satisfiable": contract.satisfiable,
                "missing_capabilities": contract.missing_capabilities,
            },
            "interaction_budget": (
                {
                    "requested_max_steps": contract.session.requested_max_steps,
                    "max_semantic_turns": contract.session.max_semantic_turns,
                    "max_replans": contract.session.max_replans,
                    "max_model_calls": contract.session.max_model_calls,
                    "external_change_timeout_s": contract.session.external_change_timeout_s,
                }
                if contract.session is not None
                else None
            ),
        },
    )


def legacy_goal_graph_task(graph: ArpGraph) -> tuple[str, int] | None:
    """Recognize the old frontend wrapper so the server can upgrade it."""
    if str(graph.metadata.get("source") or "") != "agent-cockpit":
        return None
    goal_nodes = [node for node in graph.nodes if node.kind == ArpNodeKind.AGENT_GOAL]
    if len(goal_nodes) != 1:
        return None
    node = goal_nodes[0]
    task = str(node.config.get("goal") or node.config.get("task") or "").strip()
    if not task:
        return None
    try:
        max_steps = int(node.config.get("max_steps") or 40)
    except (TypeError, ValueError):
        max_steps = 40
    return task, max(1, min(max_steps, 400))
