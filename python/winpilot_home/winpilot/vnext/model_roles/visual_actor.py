"""Restricted one-turn Visual Actor backed by the shared model call Runtime."""
from __future__ import annotations

import hashlib
import json
import threading
from copy import deepcopy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable

from ...modeling import (
    ModelAttempt,
    ModelCallRuntime,
    ModelRetryPolicy,
    invoke_chat_completion,
)
from ..actor import (
    ACTOR_PROPOSAL_FORMAT,
    InvalidActorProposal,
    actor_proposal_to_json,
    parse_actor_proposal,
)
from ..runtime.contracts import (
    APPROVED_DESKTOP_ACTIONS,
    APPROVED_RUNTIME_TOOLS,
    TASK_STATE_UPDATE_SCHEMA,
    ActorTaskStateError,
    ActorTaskStateUpdate,
    ActorToolView,
    ActorTurnDecision,
    ActorTurnInput,
)
from .common import (
    LiveModelRoleError,
    member,
    narration_content,
    require_empty_content,
    require_jpeg,
    require_positive_int,
    require_positive_timeout,
    require_text,
    response_message,
    strict_json_object,
)


VISUAL_ACTOR_MODEL_ROLE = "vnext_visual_actor"
VISUAL_ACTOR_SYSTEM_CONTRACT = """\
Use original_goal, the current authoritative JPEG, task_state, previous action,
and supplied tools to choose exactly one tool call. You may put a brief
progress note in your content text; it is shown to the user while you work.
Use coordinates in the exact submitted-image pixels. original_goal may mix
chat and task: act on the actionable task and leave chat answers for the final
claim. If the goal is visibly complete, call claim_obligation_done; when an
action cannot advance the goal, call blocked instead of repeating it. When
pointer_held is true, continue or finish that drag."""

INITIAL_ACTOR_SYSTEM_CONTRACT = """\
You are a Windows desktop agent on the user's real computer. Answer chat
questions directly in the user's language. The observe_desktop tool grants
access to the live screen; use it when you need to look at or operate the
desktop. You may chat, act, or interleave both freely: narrate what you are
doing, why, and anything you find or cannot do, in your own words, at any
time. If a message mixes chat and a task, do the task and put the short
answer into the final claim. Never invent results; verify by looking at the
screen."""

_TOOL_DESCRIPTIONS = {
    "click": "Click one point once in the current image.",
    "double_click": "Double-click one point once in the current image.",
    "drag": (
        "Drag once from one image point to another, optionally following "
        "ordered intermediate image_points; use [] for a straight path. "
        "keep_holding preserves the same press for a later visual turn."
    ),
    "scroll": (
        "Scroll at one point; amount < 0 scrolls down and amount > 0 scrolls up."
    ),
    "type_text": (
        "Enter the requested literal characters into the focused editable "
        "control; selected text is replaced."
    ),
    "hotkey": (
        "Press one key or one ordered key chord; this never enters literal "
        "text."
    ),
    "launch_application": (
        "Start one installed application or file/URL association by display "
        "name, alias, or path. This is a generic launch, not a click."
    ),
    "claim_obligation_done": (
        "Finish without further mutation when the current image shows the "
        "requested result."
    ),
    "blocked": (
        "Report a visible blocker only when no supplied action can advance the goal."
    ),
}

_MODEL_TOOL_NAMES = {
    "hotkey": "press_key",
}
_RUNTIME_TOOL_NAMES = {
    model_name: runtime_name
    for runtime_name, model_name in _MODEL_TOOL_NAMES.items()
}
_STRICT_SCHEMA_KEYS = frozenset(
    {
        "additionalProperties",
        "anyOf",
        "default",
        "description",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "properties",
        "required",
        "type",
    }
)
_LOCALLY_ENFORCED_SCHEMA_KEYS = frozenset({"not", "uniqueItems"})
_THREAD_HISTORY_MESSAGES = 12
_THREAD_HISTORY_CHARS = 16_384
_CONNECTOR_TOOL_CALL_LIMIT = 4
_CONNECTOR_SEARCH_TOOL = "search_connector_tools"
_CONNECTOR_CALL_TOOL = "call_connector_tool"
_CONNECTOR_TOOLS = frozenset({_CONNECTOR_SEARCH_TOOL, _CONNECTOR_CALL_TOOL})
_FLOW_FRAGMENT_TOOL = "propose_flow_fragment"
_STATUS_TOOL_CALL_LIMIT = 2
_STATUS_AGENT_TOOL = "get_agent_status"
_STATUS_MEMORY_TOOL = "get_memory_status"
_STATUS_CAPABILITIES_TOOL = "get_capabilities"
_STATUS_TOOLS = frozenset(
    {
        _STATUS_AGENT_TOOL,
        _STATUS_MEMORY_TOOL,
        _STATUS_CAPABILITIES_TOOL,
    }
)
_SHELL_TOOL = "run_shell"
_SHELL_TOOL_CALL_LIMIT = 4
_SHELL_MAX_COMMAND_CHARS = 4096
_SHELL_MAX_OUTPUT_CHARS = 6000
_WINDOW_TEXT_TOOL = "read_window_text"
_WINDOW_TEXT_TOOL_CALL_LIMIT = 2
_WINDOW_TEXT_MAX_CHARS = 6000


@dataclass(frozen=True, slots=True)
class _InitialExchange:
    messages: tuple[dict[str, Any], ...]
    tool_call_id: str


@dataclass(frozen=True, slots=True)
class _VisualExchange:
    messages: tuple[dict[str, Any], ...]
    tool_call_id: str
    runtime_tool_name: str


@dataclass(frozen=True, slots=True)
class VisualActorTurnAdmission:
    """One text-first Actor result before any desktop lease is acquired."""

    kind: str
    reply: str = ""


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _decision_id(turn: ActorTurnInput, image_b64: str) -> str:
    digest = hashlib.sha256()
    digest.update(turn.to_json().encode("utf-8"))
    digest.update(b"\0")
    digest.update(hashlib.sha256(image_b64.encode("ascii")).digest())
    return f"visual-actor-{digest.hexdigest()[:24]}"


def _initial_decision_id(
    goal: str,
    history: Sequence[Mapping[str, Any]],
) -> str:
    digest = hashlib.sha256()
    for message in history:
        digest.update(_canonical_json(message).encode("utf-8"))
        digest.update(b"\0")
    digest.update(goal.encode("utf-8"))
    return f"visual-actor-initial-{digest.hexdigest()[:24]}"


def _bounded_thread_messages(
    history: Sequence[Mapping[str, Any]],
    goal: str,
) -> list[dict[str, str]]:
    if isinstance(history, (str, bytes)) or not isinstance(history, Sequence):
        raise LiveModelRoleError("Actor thread history must be a sequence")
    if len(history) > _THREAD_HISTORY_MESSAGES:
        raise LiveModelRoleError("Actor thread history exceeds the bounded window")
    messages: list[dict[str, str]] = []
    for raw in history:
        if not isinstance(raw, Mapping) or set(raw) != {"role", "content"}:
            raise LiveModelRoleError(
                "Actor thread history messages require only role and content"
            )
        role = raw.get("role")
        content = raw.get("content")
        if role not in {"user", "assistant"} or type(content) is not str:
            raise LiveModelRoleError(
                "Actor thread history supports only text user and assistant messages"
            )
        if not content.strip():
            raise LiveModelRoleError("Actor thread history content must not be empty")
        messages.append({"role": role, "content": content})
    while (
        messages
        and len(goal) + sum(len(item["content"]) for item in messages)
        > _THREAD_HISTORY_CHARS
    ):
        del messages[:2]
    messages.append({"role": "user", "content": goal})
    return messages


def _strict_provider_schema(
    raw: dict[str, Any],
    *,
    path: str,
) -> dict[str, Any]:
    """Project the Broker schema into OpenAI's strict supported subset.

    ``not`` and ``uniqueItems`` are enforced again by Runtime's canonical
    Broker argument admission before mutation. They cannot be sent in a strict
    Structured Outputs schema, so only those two assertions are omitted here.
    Unknown keywords fail closed instead of reaching the provider.
    """
    unsupported = set(raw) - (
        _STRICT_SCHEMA_KEYS | _LOCALLY_ENFORCED_SCHEMA_KEYS
    )
    if unsupported:
        raise LiveModelRoleError(
            f"{path} has unsupported strict schema keywords: "
            f"{sorted(unsupported)!r}"
        )
    if "not" in raw and raw["not"] != {"const": 0}:
        raise LiveModelRoleError(
            f"{path}.not is not the locally enforced nonzero assertion"
        )
    if "uniqueItems" in raw and raw["uniqueItems"] is not True:
        raise LiveModelRoleError(
            f"{path}.uniqueItems must be true when present"
        )

    projected = {
        key: value
        for key, value in raw.items()
        if key not in _LOCALLY_ENFORCED_SCHEMA_KEYS
    }
    properties = projected.get("properties")
    if properties is not None:
        if type(properties) is not dict or any(
            type(name) is not str or type(schema) is not dict
            for name, schema in properties.items()
        ):
            raise LiveModelRoleError(
                f"{path}.properties must map names to schemas"
            )
        projected["properties"] = {
            name: _strict_provider_schema(
                schema,
                path=f"{path}.properties.{name}",
            )
            for name, schema in properties.items()
        }
    items = projected.get("items")
    if items is not None:
        if type(items) is not dict:
            raise LiveModelRoleError(f"{path}.items must be a schema")
        projected["items"] = _strict_provider_schema(
            items,
            path=f"{path}.items",
        )
    alternatives = projected.get("anyOf")
    if alternatives is not None:
        if (
            type(alternatives) is not list
            or not alternatives
            or any(type(schema) is not dict for schema in alternatives)
        ):
            raise LiveModelRoleError(
                f"{path}.anyOf must be a non-empty schema array"
            )
        projected["anyOf"] = [
            _strict_provider_schema(
                schema,
                path=f"{path}.anyOf[{index}]",
            )
            for index, schema in enumerate(alternatives)
        ]
    if projected.get("type") == "object":
        if type(properties) is not dict:
            raise LiveModelRoleError(
                f"{path} object schema requires properties"
            )
        required = projected.get("required")
        if type(required) is not list or len(required) != len(set(required)):
            raise LiveModelRoleError(
                f"{path} required must be a de-duplicated list"
            )
        missing_required = set(required) - set(properties)
        if missing_required:
            raise LiveModelRoleError(
                f"{path} required names are not properties: "
                f"{sorted(missing_required)!r}"
            )
        optional_without_default = {
            name
            for name, schema in properties.items()
            if name not in required and "default" not in schema
        }
        if optional_without_default:
            raise LiveModelRoleError(
                f"{path} optional properties need an explicit default: "
                f"{sorted(optional_without_default)!r}"
            )
        if projected.get("additionalProperties") is not False:
            raise LiveModelRoleError(
                f"{path} must forbid extras"
            )
    return projected


def _actor_parameters(tool: ActorToolView) -> dict[str, Any]:
    raw = tool.to_dict()["schema"]
    if (
        type(raw) is not dict
        or raw.get("type") != "object"
        or type(raw.get("properties")) is not dict
    ):
        raise LiveModelRoleError(
            f"Actor tool {tool.name!r} requires an object JSON schema"
        )
    parameters = dict(raw)
    properties = dict(raw["properties"])
    required = raw.get("required", [])
    if type(required) is not list or any(type(item) is not str for item in required):
        raise LiveModelRoleError(
            f"Actor tool {tool.name!r} has invalid required fields"
        )
    # Runtime keeps image_points and keep_holding optional with defaults, so
    # the model may omit them; a straight drag is exactly [] / false.
    # Older catalogs may still declare them without defaults: project the
    # same defaults here so strict providers accept an omitted field.
    if tool.name == "drag":
        optional_defaults = {
            "image_points": [],
            "keep_holding": False,
        }
        for name, default in optional_defaults.items():
            if (
                name in properties
                and name not in required
                and "default" not in properties[name]
            ):
                properties[name] = dict(properties[name])
                properties[name]["default"] = default
    if "task_state_update" in properties or "task_state_update" in required:
        raise LiveModelRoleError(
            f"Actor tool {tool.name!r} shadows task_state_update"
        )
    properties["task_state_update"] = TASK_STATE_UPDATE_SCHEMA
    required = [*required, "task_state_update"]
    if tool.requires_local_intent:
        if "local_intent" in properties or "local_intent" in required:
            raise LiveModelRoleError(
                f"Actor tool {tool.name!r} shadows local_intent"
            )
        properties["local_intent"] = {
            "type": "string",
            "minLength": 1,
            "maxLength": 16384,
        }
        required = [*required, "local_intent"]
    parameters["properties"] = properties
    parameters["required"] = required
    parameters["additionalProperties"] = False
    return _strict_provider_schema(
        parameters,
        path=f"Actor tool {tool.name!r}",
    )


def _tool_definitions(turn: ActorTurnInput) -> list[dict[str, Any]]:
    names = tuple(tool.name for tool in turn.tools)
    if not names or not set(names) <= APPROVED_RUNTIME_TOOLS:
        raise LiveModelRoleError(
            "Visual Actor tools are outside the minimal Runtime surface"
        )
    return [
        {
            "type": "function",
            "function": {
                "name": _MODEL_TOOL_NAMES.get(tool.name, tool.name),
                "description": _TOOL_DESCRIPTIONS[tool.name],
                "parameters": _actor_parameters(tool),
                "strict": True,
            },
        }
        for tool in turn.tools
    ]


def _decode_arguments(raw: Any) -> dict[str, Any]:
    return strict_json_object(raw, "Actor tool arguments")


def _flow_fragment_tool_definition(
    allowed_node_types: Sequence[str],
) -> dict[str, Any]:
    node_types = list(dict.fromkeys(allowed_node_types))
    if not node_types or any(type(item) is not str or not item for item in node_types):
        raise LiveModelRoleError("flow fragment node catalog is invalid")
    return {
        "type": "function",
        "function": {
            "name": _FLOW_FRAGMENT_TOOL,
            "description": (
                "Propose one editable, non-running flow fragment using only "
                "the supplied node catalog."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "maxLength": 1000},
                    "nodes": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 32,
                        "items": {
                            "type": "object",
                            "properties": {
                                "local_id": {"type": "string", "minLength": 1, "maxLength": 64},
                                "type": {"type": "string", "enum": node_types},
                                "label": {"type": "string", "maxLength": 120},
                                "params_json": {"type": "string", "minLength": 2, "maxLength": 4096},
                            },
                            "required": ["local_id", "type", "label", "params_json"],
                            "additionalProperties": False,
                        },
                    },
                    "edges": {
                        "type": "array",
                        "maxItems": 64,
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string", "minLength": 1, "maxLength": 64},
                                "target": {"type": "string", "minLength": 1, "maxLength": 64},
                                "source_port": {"type": "string", "minLength": 1, "maxLength": 64},
                            },
                            "required": ["source", "target", "source_port"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["summary", "nodes", "edges"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }


def _flow_fragment_from_response(
    response: Any,
    catalog_ports: Mapping[str, Sequence[str]],
    catalog_params: Mapping[str, frozenset[str]],
) -> dict[str, Any]:
    message = response_message(response)
    require_empty_content(message)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        raise LiveModelRoleError("flow fragment proposal requires exactly one tool call")
    call = calls[0]
    function = member(call, "function")
    if member(call, "type") != "function" or function is None:
        raise LiveModelRoleError("flow fragment proposal must be a function call")
    if member(function, "name") != _FLOW_FRAGMENT_TOOL:
        raise LiveModelRoleError("Actor returned an unapproved flow fragment tool")
    arguments = _decode_arguments(member(function, "arguments"))
    if set(arguments) != {"summary", "nodes", "edges"}:
        raise LiveModelRoleError("flow fragment proposal fields mismatch")
    summary = arguments["summary"]
    raw_nodes = arguments["nodes"]
    raw_edges = arguments["edges"]
    if type(summary) is not str or len(summary) > 1000:
        raise LiveModelRoleError("flow fragment summary is invalid")
    if not isinstance(raw_nodes, list) or not 1 <= len(raw_nodes) <= 32:
        raise LiveModelRoleError("flow fragment must contain 1 to 32 nodes")
    if not isinstance(raw_edges, list) or len(raw_edges) > 64:
        raise LiveModelRoleError("flow fragment contains too many edges")

    nodes: list[dict[str, Any]] = []
    ids: set[str] = set()
    node_types: dict[str, str] = {}
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, Mapping) or set(raw) != {
            "local_id", "type", "label", "params_json"
        }:
            raise LiveModelRoleError(f"flow fragment node {index + 1} fields mismatch")
        local_id = raw["local_id"]
        node_type = raw["type"]
        label = raw["label"]
        params_json = raw["params_json"]
        if type(local_id) is not str or not local_id.strip() or len(local_id) > 64:
            raise LiveModelRoleError(f"flow fragment node {index + 1} id is invalid")
        local_id = local_id.strip()
        if local_id in ids:
            raise LiveModelRoleError("flow fragment contains duplicate node ids")
        if type(node_type) is not str or node_type not in catalog_ports:
            raise LiveModelRoleError(f"flow fragment node {local_id!r} type is unavailable")
        if type(label) is not str or len(label) > 120:
            raise LiveModelRoleError(f"flow fragment node {local_id!r} label is invalid")
        if type(params_json) is not str or not 2 <= len(params_json) <= 4096:
            raise LiveModelRoleError(f"flow fragment node {local_id!r} params are invalid")
        params = strict_json_object(
            params_json,
            f"flow fragment node {local_id!r} params_json",
        )
        unknown_params = set(params) - catalog_params[node_type]
        if unknown_params:
            raise LiveModelRoleError(
                f"flow fragment node {local_id!r} uses unavailable params: "
                + ", ".join(sorted(unknown_params))
            )
        ids.add(local_id)
        node_types[local_id] = node_type
        nodes.append({
            "local_id": local_id,
            "type": node_type,
            "label": label.strip(),
            "params": params,
        })

    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    seen_branches: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_edges):
        if not isinstance(raw, Mapping) or set(raw) != {
            "source", "target", "source_port"
        }:
            raise LiveModelRoleError(f"flow fragment edge {index + 1} fields mismatch")
        source, target, source_port = (
            raw["source"], raw["target"], raw["source_port"]
        )
        if not all(type(item) is str and item.strip() for item in (source, target, source_port)):
            raise LiveModelRoleError(f"flow fragment edge {index + 1} is invalid")
        source, target, source_port = source.strip(), target.strip(), source_port.strip()
        if source not in ids or target not in ids:
            raise LiveModelRoleError("flow fragment edge references an unknown node")
        if source == target:
            raise LiveModelRoleError("flow fragment edge cannot target its source node")
        if source_port not in catalog_ports[node_types[source]]:
            raise LiveModelRoleError(
                f"flow fragment node {source!r} has no {source_port!r} output"
            )
        key = (source, target, source_port)
        if key in seen_edges:
            raise LiveModelRoleError("flow fragment contains a duplicate edge")
        branch = (source, source_port)
        if branch in seen_branches:
            raise LiveModelRoleError(
                "flow fragment output branch can target only one node"
            )
        seen_edges.add(key)
        seen_branches.add(branch)
        edges.append({"source": source, "target": target, "source_port": source_port})

    outgoing = {local_id: [] for local_id in ids}
    indegree = {local_id: 0 for local_id in ids}
    for edge in edges:
        outgoing[edge["source"]].append(edge["target"])
        indegree[edge["target"]] += 1
    roots = [local_id for local_id, degree in indegree.items() if degree == 0]
    ordered = 0
    while roots:
        current = roots.pop()
        ordered += 1
        for target in outgoing[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                roots.append(target)
    if ordered != len(nodes):
        raise LiveModelRoleError("flow fragment must not contain a directed cycle")
    if len(nodes) > 1:
        if not edges:
            raise LiveModelRoleError(
                "multi-node flow fragment requires internal edges"
            )
        neighbours = {local_id: set() for local_id in ids}
        for edge in edges:
            neighbours[edge["source"]].add(edge["target"])
            neighbours[edge["target"]].add(edge["source"])
        pending = [next(iter(ids))]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            pending.extend(neighbours[current] - visited)
        if visited != ids:
            raise LiveModelRoleError(
                "flow fragment must not contain disconnected nodes"
            )
    return {"summary": summary.strip(), "nodes": nodes, "edges": edges}


def _proposal_from_response(
    response: Any,
    allowed_names: frozenset[str],
) -> ActorTurnDecision:
    message = response_message(response)
    narration = narration_content(message)
    tool_calls = member(message, "tool_calls")
    if (
        isinstance(tool_calls, (str, bytes))
        or not isinstance(tool_calls, Sequence)
        or len(tool_calls) != 1
    ):
        raise LiveModelRoleError(
            "Visual Actor must return exactly one tool call"
        )
    call = tool_calls[0]
    call_type = member(call, "type")
    if call_type != "function":
        raise LiveModelRoleError("Visual Actor tool call must be a function")
    function = member(call, "function")
    if function is None:
        raise LiveModelRoleError("Visual Actor tool call has no function")
    name = member(function, "name")
    if type(name) is not str or name not in allowed_names:
        raise LiveModelRoleError("Visual Actor returned an unapproved tool")
    runtime_name = _RUNTIME_TOOL_NAMES.get(name, name)
    arguments = _decode_arguments(member(function, "arguments"))
    try:
        state_update = ActorTaskStateUpdate.from_dict(
            arguments.pop("task_state_update", None)
        )
    except ActorTaskStateError as exc:
        raise LiveModelRoleError(str(exc)) from exc
    if runtime_name in APPROVED_DESKTOP_ACTIONS:
        local_intent = arguments.pop("local_intent", None)
        raw_proposal = {
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "desktop_action",
            "action": runtime_name,
            "args": arguments,
            "local_intent": local_intent,
        }
    elif runtime_name == "claim_obligation_done":
        raw_proposal = {
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "claim_obligation_done",
            **arguments,
        }
    elif runtime_name == "blocked":
        raw_proposal = {
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "blocked",
            **arguments,
        }
    else:  # pragma: no cover - guarded by the exact minimal tool set
        raise LiveModelRoleError("Visual Actor returned an unsupported outcome")
    proposal = parse_actor_proposal(raw_proposal)
    return ActorTurnDecision(
        proposal=json.loads(actor_proposal_to_json(proposal)),
        task_state_update=state_update,
        narration=narration,
    )


def _history_exchange(
    response: Any,
) -> tuple[dict[str, Any], str, str] | None:
    """Keep one provider-native tool call for the next visual turn."""
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    call_id = member(call, "id")
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    arguments = member(function, "arguments") if function is not None else None
    if (
        type(call_id) is not str
        or not call_id
        or type(name) is not str
        or type(arguments) is not str
    ):
        return None
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": narration_content(message),
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments,
                },
            }
        ],
    }
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return assistant, call_id, _RUNTIME_TOOL_NAMES.get(name, name)


def _tool_format_repair(
    response: Any,
    format_error: str,
) -> tuple[dict[str, Any], str] | None:
    """Extract one failed tool call so the same model can repair it.

    The structured format error is returned to the same model before any
    physical input is executed.  A repaired proposal is parsed again and a
    second failure is terminal: the runtime never replays after input.
    """
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    call_id = member(call, "id")
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    arguments = member(function, "arguments") if function is not None else None
    if (
        type(call_id) is not str
        or not call_id
        or type(name) is not str
        or type(arguments) is not str
    ):
        return None
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments,
                },
            }
        ],
    }
    return assistant, call_id


def _connector_tool_definitions(gateway: Any) -> list[dict[str, Any]]:
    if gateway is None or getattr(gateway, "available", False) is not True:
        return []
    return [
        {
            "type": "function",
            "function": {
                "name": _CONNECTOR_SEARCH_TOOL,
                "description": "Find tools on connected services.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "connector": {"type": "string"},
                    },
                    "required": ["query", "connector"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
        {
            "type": "function",
            "function": {
                "name": _CONNECTOR_CALL_TOOL,
                "description": "Call one discovered read-only tool by exact reference.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string", "minLength": 1},
                        "arguments_json": {"type": "string", "minLength": 2},
                    },
                    "required": ["ref", "arguments_json"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    ]


def _status_tool_definitions() -> list[dict[str, Any]]:
    """Read-only Actor-owned status tools resolved without a desktop lease."""

    return [
        {
            "type": "function",
            "function": {
                "name": _STATUS_AGENT_TOOL,
                "description": (
                    "Read this Agent's own runtime status without touching "
                    "the desktop."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
        {
            "type": "function",
            "function": {
                "name": _STATUS_MEMORY_TOOL,
                "description": (
                    "Read whether long-term experience memory is enabled for "
                    "this Agent chain."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
        {
            "type": "function",
            "function": {
                "name": _STATUS_CAPABILITIES_TOOL,
                "description": (
                    "Read the generic capabilities this Agent can choose "
                    "right now."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    ]


def _shell_tool_definitions() -> list[dict[str, Any]]:
    """One generic shell tool for system query / activation, no desktop lease."""

    return [
        {
            "type": "function",
            "function": {
                "name": _SHELL_TOOL,
                "description": (
                    "Execute one PowerShell or cmd command on this computer "
                    "and return its output, exit code, and errors."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 4096,
                            "description": (
                                "Full command line; PowerShell by default. "
                                "Use ; as the separator."
                            ),
                        },
                        "shell": {
                            "type": "string",
                            "enum": ["powershell", "cmd"],
                        },
                        "timeout_s": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 120,
                        },
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    ]


def _window_text_tool_definitions() -> list[dict[str, Any]]:
    """One generic read-only window-text tool for the visual line."""

    return [
        {
            "type": "function",
            "function": {
                "name": _WINDOW_TEXT_TOOL,
                "description": (
                    "Read the visible text of the current target window via "
                    "UI Automation and return it as ordered name/value lines. "
                    "Use it to read messages, lists, or forms faster and more "
                    "exactly than OCR from the screenshot; the text comes from "
                    "the exact same window the current image shows."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    ]


def _status_exchange(
    response: Any,
) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
    """Detect one Actor-owned read-only status tool call."""
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    if name not in _STATUS_TOOLS:
        return None
    if member(call, "type") != "function":
        raise LiveModelRoleError("Actor status tool call must be a function")
    call_id = member(call, "id")
    raw_arguments = member(function, "arguments")
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError("Actor status tool call requires an id")
    arguments = _decode_arguments(raw_arguments)
    if set(arguments) != set():
        raise LiveModelRoleError(f"{name} does not accept arguments")
    content = member(message, "content")
    if content is not None and type(content) is not str:
        raise LiveModelRoleError("Actor status content must be text or null")
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw_arguments,
                },
            }
        ],
    }
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return assistant, call_id, name, arguments


def _call_status(
    provider: Any,
    name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve one Actor-owned status query from the injected Runtime facts."""

    if provider is None or not callable(getattr(provider, "resolve", None)):
        raise LiveModelRoleError("Actor status provider is unavailable")
    result = provider.resolve(name)
    if not isinstance(result, Mapping):
        raise LiveModelRoleError(
            "Actor status provider returned a non-object result"
        )
    return dict(result)


def _window_text_exchange(
    response: Any,
) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
    """Detect one Actor-owned read-only window-text tool call."""
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    if name != _WINDOW_TEXT_TOOL:
        return None
    if member(call, "type") != "function":
        raise LiveModelRoleError("Actor window-text tool call must be a function")
    call_id = member(call, "id")
    raw_arguments = member(function, "arguments")
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError("Actor window-text tool call requires an id")
    arguments = _decode_arguments(raw_arguments)
    if set(arguments) != set():
        raise LiveModelRoleError("read_window_text does not accept arguments")
    content = member(message, "content")
    if content is not None and type(content) is not str:
        raise LiveModelRoleError("Actor window-text content must be text or null")
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw_arguments,
                },
            }
        ],
    }
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return assistant, call_id, name, arguments


def _call_window_text(
    reader: Any,
    turn: ActorTurnInput,
) -> dict[str, Any]:
    """Read the bound target window's visible text through the injected reader."""
    if reader is None or not callable(reader):
        raise LiveModelRoleError("Actor window-text reader is unavailable")
    try:
        result = reader(turn)
    except Exception as exc:
        raise LiveModelRoleError(
            f"Actor window-text reader failed: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(result, Mapping):
        raise LiveModelRoleError(
            "Actor window-text reader returned a non-object result"
        )
    return dict(result)


def _connector_exchange(
    response: Any,
) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    if name not in _CONNECTOR_TOOLS:
        return None
    if member(call, "type") != "function":
        raise LiveModelRoleError("Actor connector tool call must be a function")
    call_id = member(call, "id")
    raw_arguments = member(function, "arguments")
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError("Actor connector tool call requires an id")
    arguments = _decode_arguments(raw_arguments)
    expected = (
        {"query", "connector"}
        if name == _CONNECTOR_SEARCH_TOOL
        else {"ref", "arguments_json"}
    )
    if set(arguments) != expected or not all(
        type(arguments[key]) is str for key in expected
    ):
        raise LiveModelRoleError(
            f"{name} requires exactly {sorted(expected)!r} text fields"
        )
    content = member(message, "content")
    if content is not None and type(content) is not str:
        raise LiveModelRoleError("Actor connector content must be text or null")
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw_arguments,
                },
            }
        ],
    }
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return assistant, call_id, name, arguments


def _call_connector(
    gateway: Any,
    name: str,
    arguments: Mapping[str, Any],
    *,
    timeout_s: float,
    stop_event: Any | None,
) -> dict[str, Any]:
    if gateway is None or getattr(gateway, "available", False) is not True:
        raise LiveModelRoleError("Actor connector gateway is unavailable")
    if name == _CONNECTOR_SEARCH_TOOL:
        result = gateway.search(
            query=arguments["query"],
            connector=arguments["connector"],
            timeout_s=timeout_s,
            stop_event=stop_event,
        )
    elif name == _CONNECTOR_CALL_TOOL:
        result = gateway.call(
            ref=arguments["ref"],
            arguments_json=arguments["arguments_json"],
            timeout_s=timeout_s,
            stop_event=stop_event,
        )
    else:  # pragma: no cover - exact connector tool set above
        raise LiveModelRoleError("Actor returned an unsupported connector tool")
    if not isinstance(result, Mapping):
        raise LiveModelRoleError("connector gateway returned a non-object result")
    return dict(result)


def _shell_exchange(
    response: Any,
) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
    """Detect one Actor-owned ``run_shell`` tool call (connector pattern)."""
    message = response_message(response)
    calls = member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        return None
    call = calls[0]
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    if name != _SHELL_TOOL:
        return None
    if member(call, "type") != "function":
        raise LiveModelRoleError("Actor shell tool call must be a function")
    call_id = member(call, "id")
    raw_arguments = member(function, "arguments")
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError("Actor shell tool call requires an id")
    arguments = _decode_arguments(raw_arguments)
    command = arguments.get("command")
    if type(command) is not str or not command.strip():
        raise LiveModelRoleError("run_shell requires a non-empty command")
    if len(command) > _SHELL_MAX_COMMAND_CHARS:
        raise LiveModelRoleError("run_shell command exceeds the bounded budget")
    shell_name = arguments.get("shell")
    if shell_name not in (None, "powershell", "cmd"):
        raise LiveModelRoleError("run_shell shell must be powershell or cmd")
    timeout_s = arguments.get("timeout_s")
    if timeout_s is not None and (
        type(timeout_s) is not int or not 1 <= timeout_s <= 120
    ):
        raise LiveModelRoleError("run_shell timeout_s must be 1..120")
    content = member(message, "content")
    if content is not None and type(content) is not str:
        raise LiveModelRoleError("Actor shell content must be text or null")
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": raw_arguments,
                },
            }
        ],
    }
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return assistant, call_id, name, arguments


def _call_shell(
    runner: Any,
    arguments: Mapping[str, Any],
    *,
    timeout_s: float,
    stop_event: Any | None,
) -> dict[str, Any]:
    """Execute one bounded shell command through the injected runner."""
    if runner is None or not callable(getattr(runner, "run", None)):
        raise LiveModelRoleError("Actor shell runner is unavailable")
    result = runner.run(
        command=str(arguments.get("command") or ""),
        shell=str(arguments.get("shell") or "powershell"),
        timeout_s=float(arguments.get("timeout_s") or 30),
        stop_event=stop_event,
    )
    if not isinstance(result, Mapping):
        raise LiveModelRoleError("Actor shell runner returned a non-object result")
    output = dict(result)
    for key in ("stdout", "stderr", "error"):
        value = output.get(key)
        if isinstance(value, str) and len(value) > _SHELL_MAX_OUTPUT_CHARS:
            output[key] = value[:_SHELL_MAX_OUTPUT_CHARS]
    return output


def _initial_calls_from_response(
    response: Any,
) -> list[dict[str, Any]]:
    """Return the raw tool-call list of the initial text turn."""
    message = response_message(response)
    calls = member(message, "tool_calls")
    if calls is None:
        return []
    if isinstance(calls, (str, bytes)) or not isinstance(calls, Sequence):
        raise LiveModelRoleError("Actor initial tool calls must be a sequence")
    return list(calls)


def _execute_initial_call(
    call: Mapping[str, Any],
    *,
    status_provider: Any,
    shell_runner: Any,
    connector_gateway: Any,
    window_text_reader: Any,
    turn: Any,
    timeout_s: float,
    stop_event: Any | None,
) -> dict[str, Any]:
    """Execute one non-terminal initial tool call and return its result."""
    if member(call, "type") != "function":
        raise LiveModelRoleError("Actor initial tool call must be a function")
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    arguments = (
        _decode_arguments(member(function, "arguments"))
        if function is not None
        else None
    )
    if arguments is None:
        raise LiveModelRoleError("Actor initial tool call requires arguments")
    if name in _STATUS_TOOLS:
        if status_provider is None or not callable(
            getattr(status_provider, "resolve", None)
        ):
            raise LiveModelRoleError("Actor status provider is unavailable")
        if set(arguments) != set():
            raise LiveModelRoleError(f"{name} does not accept arguments")
        return dict(status_provider.resolve(name))
    if name == _SHELL_TOOL:
        if shell_runner is None or not callable(
            getattr(shell_runner, "run", None)
        ):
            raise LiveModelRoleError("Actor shell runner is unavailable")
        result = shell_runner.run(
            command=str(arguments.get("command") or ""),
            shell=str(arguments.get("shell") or "powershell"),
            timeout_s=float(arguments.get("timeout_s") or 30),
            stop_event=stop_event,
        )
        if not isinstance(result, Mapping):
            raise LiveModelRoleError(
                "Actor shell runner returned a non-object result"
            )
        output = dict(result)
        for key in ("stdout", "stderr", "error"):
            value = output.get(key)
            if isinstance(value, str) and len(value) > _SHELL_MAX_OUTPUT_CHARS:
                output[key] = value[:_SHELL_MAX_OUTPUT_CHARS]
        return output
    if name == _WINDOW_TEXT_TOOL:
        if window_text_reader is None or not callable(window_text_reader):
            raise LiveModelRoleError("Actor window-text reader is unavailable")
        if set(arguments) != set():
            raise LiveModelRoleError("read_window_text does not accept arguments")
        result = window_text_reader(turn)
        if not isinstance(result, Mapping):
            raise LiveModelRoleError(
                "Actor window-text reader returned a non-object result"
            )
        return dict(result)
    if name is not None and name in _CONNECTOR_TOOLS:
        return _call_connector(
            connector_gateway,
            name,
            arguments,
            timeout_s=timeout_s,
            stop_event=stop_event,
        )
    raise LiveModelRoleError(
        f"Actor initial turn returned an unapproved tool: {name!r}"
    )


def _initial_admission_from_response(
    response: Any,
    messages: Sequence[Mapping[str, Any]],
) -> tuple[VisualActorTurnAdmission, _InitialExchange | None]:
    message = response_message(response)
    content = member(message, "content")
    calls = _initial_calls_from_response(response)
    if not calls:
        if type(content) is not str or not content.strip():
            raise LiveModelRoleError(
                "Actor initial turn returned neither a reply nor a desktop observation"
            )
        return VisualActorTurnAdmission(
            kind="reply",
            reply=content.strip(),
        ), None
    if content is not None and type(content) is not str:
        raise LiveModelRoleError(
            "Actor initial content must be text or null"
        )
    observe_calls = [
        call for call in calls
        if member(member(call, "function"), "name") == "observe_desktop"
    ]
    if len(observe_calls) != 1:
        raise LiveModelRoleError(
            "Actor initial turn must request at most one desktop observation"
        )
    call = observe_calls[0]
    call_id = member(call, "id")
    function = member(call, "function")
    arguments = member(function, "arguments") if function is not None else None
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError("Actor initial tool call requires an id")
    if _decode_arguments(arguments):
        raise LiveModelRoleError("observe_desktop does not accept arguments")
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "observe_desktop",
                    "arguments": arguments,
                },
            }
        ],
    }
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return (
        VisualActorTurnAdmission(kind="desktop"),
        _InitialExchange(
            messages=tuple(deepcopy([*messages, assistant])),
            tool_call_id=call_id,
        ),
    )


class FlowFragmentProposer:
    """One inert editor proposal with no desktop or conversation authority."""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        runtime: ModelCallRuntime,
        total_timeout_s: float = 120.0,
        max_output_tokens: int = 768,
        on_attempt: Callable[[ModelAttempt], None] | None = None,
        on_response: Callable[[Any], None] | None = None,
        max_attempts: int = 1,
    ) -> None:
        if client is None:
            raise LiveModelRoleError("client is required")
        if type(runtime) is not ModelCallRuntime:
            raise LiveModelRoleError("runtime must be ModelCallRuntime")
        if (
            type(max_attempts) is not int
            or not 1 <= max_attempts <= 5
        ):
            raise LiveModelRoleError("max_attempts must be in [1, 5]")
        self._max_attempts = max_attempts
        self._client = client
        self._model = require_text(model, "model")
        self._runtime = runtime
        self._total_timeout_s = require_positive_timeout(total_timeout_s)
        self._max_output_tokens = require_positive_int(
            max_output_tokens, "max_output_tokens"
        )
        if on_attempt is not None and not callable(on_attempt):
            raise LiveModelRoleError("on_attempt must be callable or null")
        if on_response is not None and not callable(on_response):
            raise LiveModelRoleError("on_response must be callable or null")
        self._on_attempt = on_attempt
        self._on_response = on_response
        self._called = False
        self._state_lock = threading.Lock()

    def propose_flow_fragment(
        self,
        request: Mapping[str, Any],
        *,
        catalog_ports: Mapping[str, Sequence[str]],
        catalog_params: Mapping[str, frozenset[str]],
        stop_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise LiveModelRoleError("flow fragment request must be an object")
        canonical_request = _canonical_json(request)
        if len(canonical_request) > _THREAD_HISTORY_CHARS:
            raise LiveModelRoleError("flow fragment request exceeds the bounded context")
        ports = {
            key: tuple(value)
            for key, value in catalog_ports.items()
            if type(key) is str and key
        }
        if len(ports) != len(catalog_ports) or any(
            not value or any(type(port) is not str or not port for port in value)
            for value in ports.values()
        ):
            raise LiveModelRoleError("flow fragment output-port catalog is invalid")
        params = {
            key: frozenset(value)
            for key, value in catalog_params.items()
            if type(key) is str and key and isinstance(value, frozenset)
        }
        if set(params) != set(ports) or any(
            any(type(name) is not str or not name for name in names)
            for names in params.values()
        ):
            raise LiveModelRoleError("flow fragment param catalog is invalid")
        with self._state_lock:
            if self._called:
                raise LiveModelRoleError("flow fragment proposer is call-once")
            self._called = True
        outcome = invoke_chat_completion(
            self._client,
            decision_id=_initial_decision_id(canonical_request, ()),
            model_role=VISUAL_ACTOR_MODEL_ROLE,
            purpose="flow_fragment_proposal",
            provider_request={
                "model": self._model,
                "messages": [{"role": "user", "content": canonical_request}],
                "tools": [_flow_fragment_tool_definition(tuple(ports))],
                "tool_choice": "auto",
                "temperature": 0.0,
                "max_completion_tokens": self._max_output_tokens,
            },
            policy=ModelRetryPolicy(
                max_attempts=self._max_attempts,
                total_timeout_s=self._total_timeout_s,
            ),
            stop_event=stop_event,
            on_attempt=self._on_attempt,
            runtime=self._runtime,
        )
        if self._on_response is not None:
            try:
                self._on_response(outcome.response)
            except Exception:
                pass
        return _flow_fragment_from_response(outcome.response, ports, params)


class LiveVisualActor:
    """Visual decisions with one bounded native exchange and no mutation authority."""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        runtime: ModelCallRuntime,
        total_timeout_s: float = 120.0,
        max_output_tokens: int = 768,
        on_attempt: Callable[[ModelAttempt], None] | None = None,
        on_response: Callable[[Any], None] | None = None,
        connector_gateway: Any | None = None,
        status_provider: Any | None = None,
        shell_runner: Any | None = None,
        window_text_reader: Any | None = None,
        max_attempts: int = 1,
    ) -> None:
        if client is None:
            raise LiveModelRoleError("client is required")
        if type(runtime) is not ModelCallRuntime:
            raise LiveModelRoleError("runtime must be ModelCallRuntime")
        self._client = client
        self._model = require_text(model, "model")
        self._runtime = runtime
        self._total_timeout_s = require_positive_timeout(total_timeout_s)
        self._max_output_tokens = require_positive_int(
            max_output_tokens, "max_output_tokens"
        )
        if on_attempt is not None and not callable(on_attempt):
            raise LiveModelRoleError("on_attempt must be callable or null")
        if on_response is not None and not callable(on_response):
            raise LiveModelRoleError("on_response must be callable or null")
        self._on_attempt = on_attempt
        self._on_response = on_response
        self._connector_gateway = (
            connector_gateway
            if getattr(connector_gateway, "available", False) is True
            else None
        )
        self._status_provider = (
            status_provider
            if callable(getattr(status_provider, "resolve", None))
            else None
        )
        self._shell_runner = (
            shell_runner
            if callable(getattr(shell_runner, "run", None))
            else None
        )
        self._window_text_reader = (
            window_text_reader if callable(window_text_reader) else None
        )
        if (
            type(max_attempts) is not int
            or not 1 <= max_attempts <= 5
        ):
            raise LiveModelRoleError("max_attempts must be in [1, 5]")
        self._max_attempts = max_attempts
        self._previous_exchange: _VisualExchange | None = None
        self._initial_exchange: _InitialExchange | None = None
        self._initial_turn_started = False
        self._initial_turn_terminal = False
        self._state_lock = threading.Lock()

    @property
    def model_call_reservation(self) -> int:
        # One terminal proposal + one format repair + a shared local-tool
        # budget (status/shell/connector) that the model spends freely.
        local_budget = (
            _STATUS_TOOL_CALL_LIMIT
            if self._status_provider is not None
            else 0
        ) + (
            _CONNECTOR_TOOL_CALL_LIMIT
            if self._connector_gateway is not None
            else 0
        ) + (
            _SHELL_TOOL_CALL_LIMIT if self._shell_runner is not None else 0
        ) + (
            _WINDOW_TEXT_TOOL_CALL_LIMIT
            if self._window_text_reader is not None
            else 0
        )
        return 1 + 1 + local_budget

    def _record_response(self, response: Any) -> None:
        if self._on_response is None:
            return
        try:
            self._on_response(response)
        except Exception:
            # Usage telemetry is a sidecar and never changes task semantics.
            return

    def _connector_call_limit(self, model_call_limit: int | None) -> int:
        if model_call_limit is None:
            model_call_limit = self.model_call_reservation
        if type(model_call_limit) is not int or not (
            1 <= model_call_limit <= self.model_call_reservation
        ):
            raise LiveModelRoleError("Actor model-call allowance is invalid")
        # One shared local-tool budget across status/shell/connector: the
        # model decides how many system queries it needs (shell may need many
        # turns to investigate), and the global model-call budget is the only
        # hard cap.  A per-family limit would silently remove tools the model
        # is still actively using.
        return max(0, model_call_limit - 2)

    def begin_turn(
        self,
        goal: str,
        *,
        history: Sequence[Mapping[str, Any]] = (),
        stop_event: threading.Event | None = None,
        _model_call_limit: int | None = None,
    ) -> VisualActorTurnAdmission:
        """Reply directly or request the first desktop observation."""

        if type(goal) is not str or not goal.strip():
            raise LiveModelRoleError("goal must not be empty")
        goal = goal.strip()
        if len(goal) > _THREAD_HISTORY_CHARS:
            raise LiveModelRoleError("goal exceeds the Actor turn character budget")
        with self._state_lock:
            if self._initial_turn_started:
                raise LiveModelRoleError("Actor initial turn is call-once")
            self._initial_turn_started = True
        system_message = {
            "role": "system",
            "content": INITIAL_ACTOR_SYSTEM_CONTRACT,
        }
        messages = _bounded_thread_messages(history, goal)
        terminal_tools = [
            {
                "type": "function",
                "function": {
                    "name": "observe_desktop",
                    "description": "Observe the desktop.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            },
        ]
        status_tools = (
            _status_tool_definitions()
            if self._status_provider is not None
            else []
        )
        shell_tools = (
            _shell_tool_definitions()
            if self._shell_runner is not None
            else []
        )
        connector_tools = _connector_tool_definitions(self._connector_gateway)
        provider_request = {
            "model": self._model,
            "messages": [system_message, *messages],
            "tools": [
                *terminal_tools,
                *status_tools,
                *shell_tools,
                *connector_tools,
            ],
            "tool_choice": "auto",
            "temperature": 0.0,
            "max_completion_tokens": self._max_output_tokens,
        }
        admission: VisualActorTurnAdmission | None = None
        exchange: _InitialExchange | None = None
        connector_limit = self._connector_call_limit(_model_call_limit)
        repair_attempted = False
        repair_budget = _model_call_limit is None or _model_call_limit >= 2
        for connector_calls in range(connector_limit + (2 if repair_budget else 1)):
            if connector_calls == connector_limit:
                provider_request["tools"] = terminal_tools
            outcome = invoke_chat_completion(
                self._client,
                decision_id=_initial_decision_id(goal, history),
                model_role=VISUAL_ACTOR_MODEL_ROLE,
                purpose="reply_observe_or_connector",
                provider_request=provider_request,
                policy=ModelRetryPolicy(
                    max_attempts=self._max_attempts,
                    total_timeout_s=self._total_timeout_s,
                ),
                stop_event=stop_event,
                on_attempt=self._on_attempt,
                runtime=self._runtime,
            )
            self._record_response(outcome.response)
            try:
                calls = _initial_calls_from_response(outcome.response)
            except LiveModelRoleError as exc:
                raise InvalidActorProposal(str(exc)) from exc
            # The model may emit several tool calls in one response (status,
            # shell, connector, then observe_desktop).  Execute every
            # non-terminal call and keep observe_desktop as the terminal
            # admission.  A bare text reply ends the initial turn.
            if not calls:
                try:
                    admission, exchange = _initial_admission_from_response(
                        outcome.response,
                        messages,
                    )
                    break
                except LiveModelRoleError as exc:
                    if (
                        repair_attempted
                        or not repair_budget
                        or connector_calls >= connector_limit + 1
                    ):
                        raise InvalidActorProposal(str(exc)) from exc
                    repair_attempted = True
                    repair = _tool_format_repair(outcome.response, str(exc))
                    if repair is None:
                        raise InvalidActorProposal(str(exc)) from exc
                    assistant, call_id = repair
                    messages.extend(
                        (
                            assistant,
                            {
                                "role": "tool",
                                "tool_call_id": call_id,
                                "content": _canonical_json(
                                    {
                                        "tool_format_error": str(exc),
                                        "action": (
                                            "return exactly one valid tool call "
                                            "with the required fields"
                                        ),
                                    }
                                ),
                            },
                        )
                    )
                    provider_request["messages"] = [system_message, *messages]
                    provider_request["tools"] = terminal_tools
                    continue
            terminal = None
            executed_any = False
            terminal_format_error: str | None = None
            message = response_message(outcome.response)
            content = member(message, "content")
            reasoning = member(message, "reasoning_content")
            assistant: dict[str, Any] = {
                "role": "assistant",
                "content": content or "",
                "tool_calls": [
                    {
                        "id": member(call, "id"),
                        "type": "function",
                        "function": {
                            "name": member(member(call, "function"), "name"),
                            "arguments": member(
                                member(call, "function"),
                                "arguments",
                            ),
                        },
                    }
                    for call in calls
                ],
            }
            if type(reasoning) is str:
                assistant["reasoning_content"] = reasoning
            pending_tool_messages: list[Mapping[str, Any]] = []
            for call in calls:
                call_function = member(call, "function")
                call_name = (
                    member(call_function, "name")
                    if call_function is not None
                    else None
                )
                if call_name == "observe_desktop":
                    terminal = call
                    continue
                try:
                    result = _execute_initial_call(
                        call,
                        status_provider=self._status_provider,
                        shell_runner=self._shell_runner,
                        connector_gateway=self._connector_gateway,
                        window_text_reader=self._window_text_reader,
                        turn=None,
                        timeout_s=min(30.0, self._total_timeout_s),
                        stop_event=stop_event,
                    )
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
                executed_any = True
                call_id = member(call, "id")
                if type(call_id) is not str or not call_id:
                    raise InvalidActorProposal(
                        "Actor initial tool call requires an id"
                    )
                pending_tool_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": _canonical_json(result),
                    }
                )
            if terminal is not None:
                terminal_id = member(terminal, "id")
                if type(terminal_id) is not str or not terminal_id:
                    terminal_format_error = (
                        "Actor initial tool call requires an id"
                    )
                else:
                    terminal_arguments = member(
                        member(terminal, "function"),
                        "arguments",
                    )
                    try:
                        if _decode_arguments(terminal_arguments):
                            terminal_format_error = (
                                "observe_desktop does not accept arguments"
                            )
                    except LiveModelRoleError as exc:
                        terminal_format_error = str(exc)
                if terminal_format_error is not None:
                    if (
                        repair_attempted
                        or not repair_budget
                        or connector_calls >= connector_limit + 1
                    ):
                        raise LiveModelRoleError(terminal_format_error)
                    repair_attempted = True
                    repair = _tool_format_repair(
                        outcome.response,
                        terminal_format_error,
                    )
                    if repair is None:
                        raise InvalidActorProposal(terminal_format_error)
                    repaired_assistant, repaired_call_id = repair
                    messages.extend(
                        (
                            repaired_assistant,
                            {
                                "role": "tool",
                                "tool_call_id": repaired_call_id,
                                "content": _canonical_json(
                                    {
                                        "tool_format_error": (
                                            terminal_format_error
                                        ),
                                        "action": (
                                            "return exactly one valid tool call "
                                            "with the required fields"
                                        ),
                                    }
                                ),
                            },
                        )
                    )
                    provider_request["messages"] = [
                        system_message,
                        *messages,
                    ]
                    provider_request["tools"] = terminal_tools
                    continue
                # Keep the full assistant tool-call list and every executed
                # result in the exchange so the provider transcript stays
                # consistent; observe_desktop is the terminal admission.  The
                # visual turn appends the synthetic observation result after
                # these messages.
                exchange = _InitialExchange(
                    messages=tuple(
                        deepcopy(
                            [*messages, assistant, *pending_tool_messages]
                        )
                    ),
                    tool_call_id=terminal_id,
                )
                admission = VisualActorTurnAdmission(kind="desktop")
                break
            if not executed_any:
                raise InvalidActorProposal(
                    "Actor initial turn returned an unapproved tool"
                )
            messages.extend((assistant, *pending_tool_messages))
            provider_request["messages"] = [system_message, *messages]
        if admission is None:
            raise LiveModelRoleError("Actor initial turn did not terminate")
        self._initial_exchange = deepcopy(exchange)
        self._initial_turn_terminal = admission.kind == "reply"
        return admission

    def propose(
        self,
        turn: ActorTurnInput,
        *,
        image_b64: str,
        image_media_type: str,
        submitted_size: tuple[int, int],
        stop_event: threading.Event | None = None,
        _model_call_limit: int | None = None,
    ) -> ActorTurnDecision:
        if self._initial_turn_terminal:
            raise LiveModelRoleError(
                "Actor cannot observe after a terminal direct reply"
            )
        if type(turn) is not ActorTurnInput:
            raise LiveModelRoleError(
                "Visual Actor requires canonical ActorTurnInput"
            )
        if not (
            turn.frame.image_available
            and turn.frame.authoritative
            and turn.frame.fresh
        ):
            raise LiveModelRoleError(
                "Visual Actor requires the current authoritative fresh frame"
            )
        image_b64, submitted_size = require_jpeg(
            image_b64, image_media_type, submitted_size
        )
        terminal_tools = _tool_definitions(turn)
        status_tools = (
            _status_tool_definitions()
            if self._status_provider is not None
            else []
        )
        shell_tools = (
            _shell_tool_definitions()
            if self._shell_runner is not None
            else []
        )
        window_text_tools = (
            _window_text_tool_definitions()
            if self._window_text_reader is not None
            else []
        )
        connector_tools = _connector_tool_definitions(self._connector_gateway)
        context = turn.to_dict()
        context.pop("tools")
        context["submitted_size"] = list(submitted_size)
        user_message = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": _canonical_json(context),
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": (
                            "data:image/jpeg;base64,"
                            f"{image_b64}"
                        )
                    },
                },
            ],
        }
        native_context: list[dict[str, Any]] = []
        initial_exchange = self._initial_exchange
        if initial_exchange is not None:
            native_context.extend(deepcopy(initial_exchange.messages))
            native_context.append(
                {
                    "role": "tool",
                    "tool_call_id": initial_exchange.tool_call_id,
                    "content": _canonical_json(
                        {"desktop_observation": "available"}
                    ),
                }
            )
        previous = self._previous_exchange
        if (
            previous is not None
            and turn.previous_action is not None
            and turn.previous_action.action == previous.runtime_tool_name
        ):
            native_context.extend(deepcopy(previous.messages))
            native_context.append(
                {
                    "role": "tool",
                    "tool_call_id": previous.tool_call_id,
                    "content": _canonical_json(
                        {
                            "previous_action": (
                                turn.previous_action.to_dict()
                            )
                        }
                    ),
                }
            )
        else:
            self._previous_exchange = None
        current_exchange = [user_message]
        provider_request = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": VISUAL_ACTOR_SYSTEM_CONTRACT,
                },
                *native_context,
                *current_exchange,
            ],
            "tools": [
                *terminal_tools,
                *status_tools,
                *shell_tools,
                *window_text_tools,
                *connector_tools,
            ],
            # MiMo's documented Chat Completions surface supports only
            # ``auto``. The role still accepts exactly one function call and
            # rejects prose, zero calls, and multiple calls without repair.
            "tool_choice": "auto",
            "temperature": 0.0,
            "max_completion_tokens": self._max_output_tokens,
        }
        outcome = None
        decision = None
        repair_attempted = False
        connector_limit = self._connector_call_limit(_model_call_limit)
        repair_budget = (
            _model_call_limit is None or _model_call_limit >= 2
        )
        window_text_calls = 0
        for connector_calls in range(connector_limit + (2 if repair_budget else 1)):
            if connector_calls == connector_limit:
                provider_request["tools"] = terminal_tools
            outcome = invoke_chat_completion(
                self._client,
                decision_id=_decision_id(turn, image_b64),
                model_role=VISUAL_ACTOR_MODEL_ROLE,
                purpose="propose_action_or_connector",
                provider_request=provider_request,
                policy=ModelRetryPolicy(
                    max_attempts=self._max_attempts,
                    total_timeout_s=self._total_timeout_s,
                ),
                stop_event=stop_event,
                on_attempt=self._on_attempt,
                runtime=self._runtime,
            )
            self._record_response(outcome.response)
            status = None
            if self._status_provider is not None:
                try:
                    status = _status_exchange(outcome.response)
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if (
                status is not None
                and connector_calls < connector_limit
            ):
                assistant, call_id, name, arguments = status
                result = _call_status(
                    self._status_provider,
                    name,
                    arguments,
                )
                connector_messages = [
                    assistant,
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": _canonical_json(result),
                    },
                ]
                current_exchange.extend(connector_messages)
                provider_request["messages"].extend(connector_messages)
                continue
            shell = None
            if self._shell_runner is not None:
                try:
                    shell = _shell_exchange(outcome.response)
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if (
                shell is not None
                and connector_calls < connector_limit
            ):
                assistant, call_id, name, arguments = shell
                result = _call_shell(
                    self._shell_runner,
                    arguments,
                    timeout_s=min(30.0, self._total_timeout_s),
                    stop_event=stop_event,
                )
                connector_messages = [
                    assistant,
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": _canonical_json(result),
                    },
                ]
                current_exchange.extend(connector_messages)
                provider_request["messages"].extend(connector_messages)
                continue
            window_text = None
            if self._window_text_reader is not None:
                try:
                    window_text = _window_text_exchange(outcome.response)
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            if (
                window_text is not None
                and connector_calls < connector_limit
                and window_text_calls < _WINDOW_TEXT_TOOL_CALL_LIMIT
            ):
                window_text_calls += 1
                assistant, call_id, name, arguments = window_text
                result = _call_window_text(
                    self._window_text_reader,
                    turn,
                )
                connector_messages = [
                    assistant,
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": _canonical_json(result),
                    },
                ]
                current_exchange.extend(connector_messages)
                provider_request["messages"].extend(connector_messages)
                continue
            try:
                connector = _connector_exchange(outcome.response)
            except LiveModelRoleError as exc:
                raise InvalidActorProposal(str(exc)) from exc
            if connector is None or connector_calls >= connector_limit:
                try:
                    decision = _proposal_from_response(
                        outcome.response,
                        frozenset(
                            _MODEL_TOOL_NAMES.get(tool.name, tool.name)
                            for tool in turn.tools
                        ),
                    )
                    break
                except InvalidActorProposal as exc:
                    if (
                        repair_attempted
                        or not repair_budget
                        or connector_calls >= connector_limit + 1
                    ):
                        raise
                    repair_attempted = True
                    repair = _tool_format_repair(
                        outcome.response,
                        str(exc),
                    )
                    if repair is None:
                        raise
                    assistant, call_id = repair
                    repair_messages = [
                        assistant,
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": _canonical_json(
                                {
                                    "tool_format_error": str(exc),
                                    "action": (
                                        "return exactly one valid tool call "
                                        "with the required fields"
                                    ),
                                }
                            ),
                        },
                    ]
                    current_exchange.extend(repair_messages)
                    provider_request["messages"].extend(repair_messages)
                    provider_request["tools"] = terminal_tools
                    continue
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
            assistant, call_id, name, arguments = connector
            result = _call_connector(
                self._connector_gateway,
                name,
                arguments,
                timeout_s=min(30.0, self._total_timeout_s),
                stop_event=stop_event,
            )
            connector_messages = [
                assistant,
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _canonical_json(result),
                },
            ]
            current_exchange.extend(connector_messages)
            provider_request["messages"].extend(connector_messages)
        if outcome is None or decision is None:
            raise LiveModelRoleError("Actor visual turn did not terminate")
        exchange = _history_exchange(outcome.response)
        self._previous_exchange = (
            None
            if exchange is None
            else _VisualExchange(
                messages=tuple(deepcopy([*current_exchange, exchange[0]])),
                tool_call_id=exchange[1],
                runtime_tool_name=exchange[2],
            )
        )
        self._initial_exchange = None
        return decision


__all__ = [
    "VISUAL_ACTOR_MODEL_ROLE",
    "VISUAL_ACTOR_SYSTEM_CONTRACT",
    "INITIAL_ACTOR_SYSTEM_CONTRACT",
    "LiveVisualActor",
    "VisualActorTurnAdmission",
]
