"""Versioned contracts shared by the Agent, ARP runtime, and cockpit UI."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


ARP_FORMAT = "arp/1"


class ArpNodeKind(str, Enum):
    START = "start"
    END = "end"
    AGENT_GOAL = "agent_goal"
    ACTION_BATCH = "action_batch"
    SKILL_REPLAY = "skill_replay"
    CONDITION = "condition"
    DELAY = "delay"
    SET_VARIABLE = "set_variable"
    EVIDENCE_GATE = "evidence_gate"
    HUMAN_GATE = "human_gate"
    LEGACY_FLOW = "legacy_flow"


class ArpRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


class ArpRetryPolicy(BaseModel):
    max_attempts: int = Field(default=1, ge=1, le=10)
    delay_ms: int = Field(default=0, ge=0, le=60000)
    backoff: float = Field(default=1.0, ge=1.0, le=10.0)


class ArpNode(BaseModel):
    id: str
    kind: ArpNodeKind
    name: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    retry: ArpRetryPolicy = Field(default_factory=ArpRetryPolicy)
    timeout_s: float | None = Field(default=None, gt=0, le=3600)
    disabled: bool = False
    x: float = 0
    y: float = 0


class ArpEdge(BaseModel):
    id: str
    source: str
    target: str
    branch: str = "next"
    label: str = ""


class ArpPolicy(BaseModel):
    max_node_visits: int = Field(default=20, ge=1, le=1000)
    fail_fast: bool = True
    require_evidence_on_done: bool = True
    allow_shell: bool = False
    allow_desktop_mutation: bool = True


class ArpGraph(BaseModel):
    format: str = ARP_FORMAT
    id: str
    name: str
    description: str = ""
    nodes: list[ArpNode]
    edges: list[ArpEdge]
    variables: dict[str, Any] = Field(default_factory=dict)
    policy: ArpPolicy = Field(default_factory=ArpPolicy)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphValidation(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    entry_node: str | None = None
    reachable_nodes: list[str] = Field(default_factory=list)


class CapabilitySpec(BaseModel):
    id: str
    name: str
    category: str
    description: str
    node_kind: ArpNodeKind
    latency_class: str
    deterministic: bool
    requires_llm: bool
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ArpRunRequest(BaseModel):
    graph: ArpGraph
    inputs: dict[str, Any] = Field(default_factory=dict)


def validate_graph(graph: ArpGraph) -> GraphValidation:
    errors: list[str] = []
    warnings: list[str] = []
    if graph.format != ARP_FORMAT:
        errors.append(f"unsupported graph format: {graph.format!r}")
    ids = [node.id.strip() for node in graph.nodes]
    if any(not node_id for node_id in ids):
        errors.append("every node requires a non-empty id")
    if len(ids) != len(set(ids)):
        errors.append("node ids must be unique")
    edge_ids = [edge.id.strip() for edge in graph.edges]
    if len(edge_ids) != len(set(edge_ids)):
        errors.append("edge ids must be unique")

    node_map = {node.id: node for node in graph.nodes}
    starts = [node.id for node in graph.nodes if node.kind == ArpNodeKind.START]
    ends = [node.id for node in graph.nodes if node.kind == ArpNodeKind.END]
    if len(starts) != 1:
        errors.append("graph requires exactly one start node")
    if not ends:
        errors.append("graph requires at least one end node")
    for edge in graph.edges:
        if edge.source not in node_map:
            errors.append(f"edge {edge.id!r} references missing source {edge.source!r}")
        if edge.target not in node_map:
            errors.append(f"edge {edge.id!r} references missing target {edge.target!r}")

    reachable: list[str] = []
    if len(starts) == 1 and not errors:
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_map}
        for edge in graph.edges:
            adjacency[edge.source].append(edge.target)
        pending = [starts[0]]
        seen: set[str] = set()
        while pending:
            node_id = pending.pop(0)
            if node_id in seen:
                continue
            seen.add(node_id)
            reachable.append(node_id)
            pending.extend(adjacency.get(node_id, []))
        unreachable = sorted(set(node_map) - seen)
        if unreachable:
            warnings.append("unreachable nodes: " + ", ".join(unreachable))
        if not any(end in seen for end in ends):
            errors.append("no end node is reachable from start")
        if graph.policy.require_evidence_on_done:
            evidence_kinds = {
                ArpNodeKind.AGENT_GOAL,
                ArpNodeKind.ACTION_BATCH,
                ArpNodeKind.SKILL_REPLAY,
                ArpNodeKind.EVIDENCE_GATE,
                ArpNodeKind.HUMAN_GATE,
            }
            if not any(
                node.id in seen and not node.disabled and node.kind in evidence_kinds
                for node in graph.nodes
            ):
                errors.append(
                    "policy require_evidence_on_done needs a reachable evidence-capable node"
                )

    return GraphValidation(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        entry_node=starts[0] if len(starts) == 1 else None,
        reachable_nodes=reachable,
    )
