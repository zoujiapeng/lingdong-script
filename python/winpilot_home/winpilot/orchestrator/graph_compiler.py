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
                max_attempts=2 if stage.type in {
                    StageType.OPEN_APPLICATION, StageType.FOCUS_WINDOW,
                    StageType.OPEN_CONVERSATION, StageType.VERIFY_DELIVERY,
                } else 1,
                delay_ms=250,
                backoff=1.5,
            ),
            timeout_s=180 if stage.execution and stage.execution.model_role != "none" else 90,
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
            max_node_visits=30,
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
