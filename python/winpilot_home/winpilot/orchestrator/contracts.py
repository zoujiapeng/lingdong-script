"""Versioned contracts shared by the Agent, ARP runtime, and cockpit UI."""
from __future__ import annotations

from enum import Enum
import time
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict, Field


ARP_FORMAT = "arp/1"
CONDITION_OPERATORS = {
    "truthy", "falsy", "true", "false", "always_true", "always_false",
    "eq", "equals", "==", "ne", "not_equals", "!=",
    "contains", "gt", ">", "gte", ">=", "lt", "<", "lte", "<=",
}
_SPATIAL_TARGET_REQUIRED = {
    "click", "double_click", "click_element", "scroll", "move_mouse", "drag",
    "draw_strokes", "ensure_input_mode", "select_color", "mouse_down", "mouse_up",
    "coordinate_click_test", "paste_image_file",
}


class ArpNodeKind(str, Enum):
    START = "start"
    END = "end"
    AGENT_GOAL = "agent_goal"
    ACTION_BATCH = "action_batch"
    REPLAY_STEP = "replay_step"
    SKILL_REPLAY = "skill_replay"
    TASK_STAGE = "task_stage"
    CONDITION = "condition"
    DELAY = "delay"
    SET_VARIABLE = "set_variable"
    EVIDENCE_GATE = "evidence_gate"
    LEGACY_FLOW = "legacy_flow"


class ArpRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    REPLY = "reply"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


class ArpRuntimeVersion(str, Enum):
    LEGACY = "legacy"
    VNEXT = "vnext"


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
    flow_node_types: list[str] = Field(default_factory=list)


class EvidenceRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    predicate: str
    value: Any = None
    source: str
    verified: bool = False
    captured_at: float = Field(default_factory=time.time)
    action_id: str = ""
    node_id: str = ""
    run_id: str = ""
    text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceRequirement(BaseModel):
    predicate: str
    equals: Any = None
    source: str = ""
    max_age_ms: int | None = Field(default=None, ge=0, le=86_400_000)


class ArpRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph: ArpGraph
    inputs: dict[str, Any] = Field(default_factory=dict)
    runtime_version: ArpRuntimeVersion = ArpRuntimeVersion.LEGACY


def _validate_node_config(node: ArpNode) -> list[str]:
    config = node.config
    errors: list[str] = []
    prefix = f"node {node.id!r} ({node.kind.value})"
    if node.disabled:
        return errors
    if node.kind == ArpNodeKind.AGENT_GOAL:
        goal = str(config.get("goal") or config.get("task") or node.name or "").strip()
        if not goal:
            errors.append(f"{prefix} requires config.goal")
    elif node.kind == ArpNodeKind.ACTION_BATCH:
        from ..fast.action_contract import validate_batch_actions

        errors.extend(f"{prefix}: {error}" for error in validate_batch_actions(config.get("actions")))
    elif node.kind == ArpNodeKind.REPLAY_STEP:
        step = config.get("step")
        if not isinstance(step, dict):
            errors.append(f"{prefix} requires config.step object")
        else:
            # Keep WPS validation in one place so graph execution and file replay
            # accept exactly the same deterministic step language.  The pure
            # validator must not pull recorder.trace into the product closure.
            from ..recorder.wps_validate import validate_script

            script = {"format": "wps/2", "steps": [step]}
            if config.get("target_window") not in (None, "", {}):
                script["target_window"] = config["target_window"]
            error = validate_script(script)
            if error:
                errors.append(f"{prefix}: {error}")
        try:
            pace_ms = int(config.get("pace_ms") or 0)
            if pace_ms < 0 or pace_ms > 60000:
                errors.append(f"{prefix} requires config.pace_ms between 0 and 60000")
        except (TypeError, ValueError):
            errors.append(f"{prefix} requires integer config.pace_ms")
    elif node.kind == ArpNodeKind.SKILL_REPLAY:
        if not any(str(config.get(key) or "").strip() for key in ("script", "task", "memory_id")):
            errors.append(f"{prefix} requires config.script, config.task, or config.memory_id")
    elif node.kind == ArpNodeKind.TASK_STAGE:
        stage = config.get("stage")
        if not isinstance(stage, dict):
            errors.append(f"{prefix} requires config.stage object")
        else:
            try:
                from ..tasking.contracts import TaskStage

                parsed = TaskStage.model_validate(stage)
                if parsed.execution is None:
                    errors.append(f"{prefix} requires a capability selection")
                elif not parsed.execution.satisfiable:
                    missing = ", ".join(parsed.execution.missing_capabilities)
                    errors.append(f"{prefix} has missing capabilities: {missing}")
            except Exception as exc:
                errors.append(f"{prefix} has invalid typed stage: {exc}")
    elif node.kind == ArpNodeKind.CONDITION:
        if not str(config.get("source") or "").strip():
            errors.append(f"{prefix} requires config.source")
        operator = str(config.get("operator") or "truthy").casefold()
        if operator not in CONDITION_OPERATORS:
            errors.append(f"{prefix} has unsupported operator {operator!r}")
    elif node.kind == ArpNodeKind.DELAY:
        try:
            if int(config.get("duration_ms") or 0) < 0:
                errors.append(f"{prefix} requires a non-negative config.duration_ms")
        except (TypeError, ValueError):
            errors.append(f"{prefix} requires integer config.duration_ms")
    elif node.kind == ArpNodeKind.SET_VARIABLE:
        if not str(config.get("name") or "").strip():
            errors.append(f"{prefix} requires config.name")
    elif node.kind == ArpNodeKind.EVIDENCE_GATE:
        required = config.get("required")
        if not isinstance(required, list) or not any(
            isinstance(item, dict) or str(item).strip() for item in required
        ):
            errors.append(f"{prefix} requires at least one concrete evidence token")
        elif isinstance(required, list):
            for index, item in enumerate(required):
                try:
                    requirement = EvidenceRequirement.model_validate(item) if isinstance(item, dict) else EvidenceRequirement(predicate=str(item).strip())
                    if not requirement.predicate.strip():
                        raise ValueError("predicate is empty")
                except Exception as exc:
                    errors.append(f"{prefix} required[{index}] is invalid: {exc}")
    elif node.kind == ArpNodeKind.LEGACY_FLOW:
        node_type = str(config.get("node_type") or "").strip()
        errors.append(
            f"{prefix} uses retired legacy node type {node_type or '<empty>'!r}; "
            "migrate the saved flow to a canonical ARP capability before running it"
        )
    return errors


def _spatial_target_path_errors(
    start: str,
    node_map: dict[str, ArpNode],
    outgoing: dict[str, list[ArpEdge]],
) -> list[str]:
    """Reject any reachable spatial batch path without a concrete focus binding."""

    errors: set[str] = set()
    pending: list[tuple[str, bool]] = [(start, False)]
    seen: set[tuple[str, bool]] = set()
    while pending:
        node_id, target_established = pending.pop(0)
        state = (node_id, target_established)
        if state in seen:
            continue
        seen.add(state)
        node = node_map[node_id]
        established_after = target_established
        if node.kind == ArpNodeKind.ACTION_BATCH and not node.disabled:
            from ..fast.action_contract import iter_atomic_actions

            for action in iter_atomic_actions(
                "run_action_batch",
                {"actions": node.config.get("actions")},
            ):
                if action.name == "focus_window" and (
                    action.inputs.get("hwnd")
                    or str(action.inputs.get("title") or "").strip()
                ):
                    established_after = True
                elif action.name in _SPATIAL_TARGET_REQUIRED and not established_after:
                    errors.add(
                        f"action batch node {node.id!r} requires a concrete focus_window "
                        f"on every reachable path before spatial action {action.name!r}"
                    )
        pending.extend(
            (edge.target, established_after)
            for edge in outgoing.get(node_id, [])
        )
    return sorted(errors)


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
    for node in graph.nodes:
        errors.extend(_validate_node_config(node))
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
    route_keys = [(edge.source, edge.branch) for edge in graph.edges]
    duplicate_routes = sorted({key for key in route_keys if route_keys.count(key) > 1})
    for source, branch in duplicate_routes:
        errors.append(f"node {source!r} has multiple routes for branch {branch!r}")

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
        outgoing: dict[str, list[ArpEdge]] = {node_id: [] for node_id in node_map}
        for edge in graph.edges:
            outgoing[edge.source].append(edge)
        for node_id in reachable:
            node = node_map[node_id]
            if node.kind != ArpNodeKind.END and not outgoing.get(node_id):
                errors.append(f"reachable node {node_id!r} has no outgoing route")
            if node.kind == ArpNodeKind.CONDITION:
                branches = {edge.branch for edge in outgoing.get(node_id, [])}
                missing = {"yes", "no"} - branches
                if missing:
                    errors.append(
                        f"condition node {node_id!r} requires yes/no routes; missing "
                        + ", ".join(sorted(missing))
                    )
            if node.kind == ArpNodeKind.SKILL_REPLAY and not str(node.config.get("script") or "").strip():
                branches = {edge.branch for edge in outgoing.get(node_id, [])}
                if "miss" not in branches:
                    errors.append(
                        f"memory-backed skill node {node_id!r} requires a miss route for Agent fallback"
                    )
        errors.extend(_spatial_target_path_errors(starts[0], node_map, outgoing))
        if graph.policy.require_evidence_on_done:
            evidence_kinds = {
                ArpNodeKind.AGENT_GOAL,
                ArpNodeKind.ACTION_BATCH,
                ArpNodeKind.REPLAY_STEP,
                ArpNodeKind.SKILL_REPLAY,
                ArpNodeKind.TASK_STAGE,
                ArpNodeKind.EVIDENCE_GATE,
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
