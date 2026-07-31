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
and supplied tools to choose exactly one tool call. Return no prose. Use
coordinates in the exact submitted-image pixels. If the goal is visibly
complete, call claim_obligation_done. When pointer_held is true, continue or
finish that drag."""

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
        if (
            type(required) is not list
            or len(required) != len(set(required))
            or set(required) != set(properties)
            or projected.get("additionalProperties") is not False
        ):
            raise LiveModelRoleError(
                f"{path} must require every property and forbid extras"
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
    # Runtime keeps image_points optional so old four-coordinate proposals and
    # journals remain valid. Strict provider schemas require every property,
    # so the model-facing projection represents a straight drag as [].
    if tool.name == "drag":
        for optional_name in ("image_points", "keep_holding"):
            if optional_name in properties and optional_name not in required:
                required = [*required, optional_name]
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


def _proposal_from_response(
    response: Any,
    allowed_names: frozenset[str],
) -> ActorTurnDecision:
    message = response_message(response)
    require_empty_content(message)
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
    reasoning = member(message, "reasoning_content")
    if type(reasoning) is str:
        assistant["reasoning_content"] = reasoning
    return assistant, call_id, _RUNTIME_TOOL_NAMES.get(name, name)


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


def _initial_admission_from_response(
    response: Any,
    messages: Sequence[Mapping[str, Any]],
) -> tuple[VisualActorTurnAdmission, _InitialExchange | None]:
    message = response_message(response)
    content = member(message, "content")
    tool_calls = member(message, "tool_calls")
    calls = [] if tool_calls is None else tool_calls
    if isinstance(calls, (str, bytes)) or not isinstance(calls, Sequence):
        raise LiveModelRoleError("Actor initial tool calls must be a sequence")
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
    if len(calls) != 1:
        raise LiveModelRoleError(
            "Actor initial turn must request at most one desktop observation"
        )
    call = calls[0]
    if member(call, "type") != "function":
        raise LiveModelRoleError("Actor initial tool call must be a function")
    call_id = member(call, "id")
    function = member(call, "function")
    name = member(function, "name") if function is not None else None
    arguments = member(function, "arguments") if function is not None else None
    if type(call_id) is not str or not call_id:
        raise LiveModelRoleError("Actor initial tool call requires an id")
    if name != "observe_desktop":
        raise LiveModelRoleError("Actor initial turn returned an unapproved tool")
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
        self._previous_exchange: _VisualExchange | None = None
        self._initial_exchange: _InitialExchange | None = None
        self._initial_turn_started = False
        self._initial_turn_terminal = False
        self._state_lock = threading.Lock()

    @property
    def model_call_reservation(self) -> int:
        return (
            _CONNECTOR_TOOL_CALL_LIMIT + 1
            if self._connector_gateway is not None
            else 1
        )

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
        return min(_CONNECTOR_TOOL_CALL_LIMIT, model_call_limit - 1)

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
        connector_tools = _connector_tool_definitions(self._connector_gateway)
        provider_request = {
            "model": self._model,
            "messages": messages,
            "tools": [*terminal_tools, *connector_tools],
            "tool_choice": "auto",
            "temperature": 0.0,
            "max_completion_tokens": self._max_output_tokens,
        }
        admission: VisualActorTurnAdmission | None = None
        exchange: _InitialExchange | None = None
        connector_limit = self._connector_call_limit(_model_call_limit)
        for connector_calls in range(connector_limit + 1):
            if connector_calls == connector_limit:
                provider_request["tools"] = terminal_tools
            outcome = invoke_chat_completion(
                self._client,
                decision_id=_initial_decision_id(goal, history),
                model_role=VISUAL_ACTOR_MODEL_ROLE,
                purpose="reply_observe_or_connector",
                provider_request=provider_request,
                policy=ModelRetryPolicy(
                    max_attempts=1,
                    total_timeout_s=self._total_timeout_s,
                ),
                stop_event=stop_event,
                on_attempt=self._on_attempt,
                runtime=self._runtime,
            )
            self._record_response(outcome.response)
            connector = _connector_exchange(outcome.response)
            if connector is None or connector_calls >= connector_limit:
                admission, exchange = _initial_admission_from_response(
                    outcome.response,
                    messages,
                )
                break
            assistant, call_id, name, arguments = connector
            result = _call_connector(
                self._connector_gateway,
                name,
                arguments,
                timeout_s=min(30.0, self._total_timeout_s),
                stop_event=stop_event,
            )
            messages.extend(
                (
                    assistant,
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": _canonical_json(result),
                    },
                )
            )
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
            and turn.previous_action.input_completed
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
            "tools": [*terminal_tools, *connector_tools],
            # MiMo's documented Chat Completions surface supports only
            # ``auto``. The role still accepts exactly one function call and
            # rejects prose, zero calls, and multiple calls without repair.
            "tool_choice": "auto",
            "temperature": 0.0,
            "max_completion_tokens": self._max_output_tokens,
        }
        outcome = None
        decision = None
        connector_limit = self._connector_call_limit(_model_call_limit)
        for connector_calls in range(connector_limit + 1):
            if connector_calls == connector_limit:
                provider_request["tools"] = terminal_tools
            outcome = invoke_chat_completion(
                self._client,
                decision_id=_decision_id(turn, image_b64),
                model_role=VISUAL_ACTOR_MODEL_ROLE,
                purpose="propose_action_or_connector",
                provider_request=provider_request,
                policy=ModelRetryPolicy(
                    max_attempts=1,
                    total_timeout_s=self._total_timeout_s,
                ),
                stop_event=stop_event,
                on_attempt=self._on_attempt,
                runtime=self._runtime,
            )
            self._record_response(outcome.response)
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
                except InvalidActorProposal:
                    raise
                except LiveModelRoleError as exc:
                    raise InvalidActorProposal(str(exc)) from exc
                break
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
    "LiveVisualActor",
    "VisualActorTurnAdmission",
]
