"""Restricted one-turn Visual Actor backed by the shared model call Runtime."""
from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping, Sequence
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


class LiveVisualActor:
    """One isolated visual decision with no identity or mutation authority."""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        runtime: ModelCallRuntime,
        total_timeout_s: float = 120.0,
        max_output_tokens: int = 768,
        on_attempt: Callable[[ModelAttempt], None] | None = None,
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
        self._on_attempt = on_attempt

    def propose(
        self,
        turn: ActorTurnInput,
        *,
        image_b64: str,
        image_media_type: str,
        submitted_size: tuple[int, int],
        stop_event: threading.Event | None = None,
    ) -> ActorTurnDecision:
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
        tools = _tool_definitions(turn)
        context = turn.to_dict()
        context.pop("tools")
        context["submitted_size"] = list(submitted_size)
        provider_request = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": VISUAL_ACTOR_SYSTEM_CONTRACT,
                },
                {
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
                },
            ],
            "tools": tools,
            # MiMo's documented Chat Completions surface supports only
            # ``auto``. The role still accepts exactly one function call and
            # rejects prose, zero calls, and multiple calls without repair.
            "tool_choice": "auto",
            "temperature": 0.0,
            "max_completion_tokens": self._max_output_tokens,
        }
        outcome = invoke_chat_completion(
            self._client,
            decision_id=_decision_id(turn, image_b64),
            model_role=VISUAL_ACTOR_MODEL_ROLE,
            purpose="propose_one_identity_free_action",
            provider_request=provider_request,
            policy=ModelRetryPolicy(
                max_attempts=1,
                total_timeout_s=self._total_timeout_s,
            ),
            stop_event=stop_event,
            on_attempt=self._on_attempt,
            runtime=self._runtime,
        )
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
        return decision


__all__ = [
    "VISUAL_ACTOR_MODEL_ROLE",
    "VISUAL_ACTOR_SYSTEM_CONTRACT",
    "LiveVisualActor",
]
