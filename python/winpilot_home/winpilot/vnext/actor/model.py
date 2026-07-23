"""OpenAI-compatible, production-injectable Visual Actor model boundary."""
from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ...model_adapter import build_model_adapter
from ...modeling import (
    ModelCallOutcome,
    ModelCallRuntime,
    ModelRetryPolicy,
    create_openai_client,
    invoke_chat_completion,
)
from ..targeting.contracts import DISCOVER_TARGET_TOOL_NAME
from .action_history import (
    ActionHistoryProjectionError,
    project_action_history_args,
)
from .proposals import (
    ACTOR_PROPOSAL_FORMAT,
    ActorContractError,
    InvalidActorProposal,
    actor_proposal_to_json,
    parse_actor_proposal,
)
from .views import MAX_ACTOR_PREVIOUS_INTENT_CHARS, ActorToolView, ActorTurnInput


VISUAL_ACTOR_ROLE = "visual_actor"
VISUAL_ACTOR_SYSTEM_CONTRACT = (
    "Act as the only Visual Actor. Read the current image and local mission state, "
    "then call exactly one provided tool for the next action. Use submitted-image "
    "pixel coordinates only. Never answer with text or invent runtime identity."
)
_LOCAL_INTENT_FIELD = "local_intent"
_LOCAL_INTENT_DESCRIPTION = (
    "Briefly state the direct visible effect intended by this one action "
    "without copying argument values."
)
_PREVIOUS_ACTION_CALL_ID = "previous_visual_action"


class VisualActorModelError(ActorContractError):
    """The Actor boundary itself is misconfigured or internally inconsistent."""


class VisualActorResponseError(InvalidActorProposal, VisualActorModelError):
    """One provider response cannot become an identity-free ActorProposal."""


def _member(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _one_choice(response: Any) -> Any:
    choices = _member(response, "choices")
    if (
        isinstance(choices, (str, bytes, Mapping))
        or not isinstance(choices, Sequence)
        or len(choices) != 1
    ):
        raise VisualActorResponseError("Visual Actor response requires exactly one choice")
    return choices[0]


def _one_tool_call(response: Any) -> tuple[str, Mapping[str, Any]]:
    message = _member(_one_choice(response), "message")
    if message is None:
        raise VisualActorResponseError("Visual Actor response has no message")
    content = _member(message, "content")
    if content is not None and (not isinstance(content, str) or content.strip()):
        raise VisualActorResponseError("Visual Actor must not answer with text")
    calls = _member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes, Mapping))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        raise VisualActorResponseError("Visual Actor must return exactly one tool call")
    call = calls[0]
    if _member(call, "type") != "function":
        raise VisualActorResponseError("Visual Actor tool call must have type 'function'")
    function = _member(call, "function")
    name = _member(function, "name")
    raw_arguments = _member(function, "arguments")
    if type(name) is not str or not name.strip():
        raise VisualActorResponseError("Visual Actor tool call has no valid name")
    if type(raw_arguments) is not str:
        raise VisualActorResponseError("Visual Actor tool arguments must be JSON text")
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise VisualActorResponseError("Visual Actor tool arguments are invalid JSON") from exc
    if not isinstance(arguments, dict):
        raise VisualActorResponseError("Visual Actor tool arguments must be one object")
    return name.strip(), arguments


def _plain_schema(tool: ActorToolView) -> dict[str, Any]:
    raw = tool.model_dump(mode="json", by_alias=True)["schema"]
    if not isinstance(raw, dict):  # pragma: no cover - ActorToolView already proves this
        raise VisualActorModelError("Broker tool schema is not an object")
    return raw


def _without_local_annotations(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Remove local schema-node annotations without changing property names."""

    result = dict(schema)
    result.pop("historyVisible", None)
    for keyword in (
        "properties", "patternProperties", "dependentSchemas", "$defs", "definitions",
    ):
        children = result.get(keyword)
        if isinstance(children, Mapping):
            result[keyword] = {
                name: (
                    _without_local_annotations(child)
                    if isinstance(child, Mapping)
                    else child
                )
                for name, child in children.items()
            }
    items = result.get("items")
    if isinstance(items, Mapping):
        result["items"] = _without_local_annotations(items)
    additional = result.get("additionalProperties")
    if isinstance(additional, Mapping):
        result["additionalProperties"] = _without_local_annotations(additional)
    for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
        children = result.get(keyword)
        if isinstance(children, list):
            result[keyword] = [
                _without_local_annotations(child)
                if isinstance(child, Mapping)
                else child
                for child in children
            ]
    for keyword in (
        "not", "if", "then", "else", "contains", "propertyNames",
        "unevaluatedProperties", "unevaluatedItems", "additionalItems", "contentSchema",
    ):
        child = result.get(keyword)
        if isinstance(child, Mapping):
            result[keyword] = _without_local_annotations(child)
    return result


def _provider_parameter_schema(tool: ActorToolView) -> dict[str, Any]:
    schema = _without_local_annotations(_plain_schema(tool))
    if not tool.requires_local_intent:
        return schema
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise VisualActorModelError(
            f"GUI tool {tool.name!r} requires canonical object properties and required"
        )
    if _LOCAL_INTENT_FIELD in properties or _LOCAL_INTENT_FIELD in required:
        raise VisualActorModelError(
            f"GUI tool {tool.name!r} collides with Actor local-intent envelope"
        )
    schema["properties"] = {
        **properties,
        _LOCAL_INTENT_FIELD: {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_ACTOR_PREVIOUS_INTENT_CHARS,
            "description": _LOCAL_INTENT_DESCRIPTION,
        },
    }
    schema["required"] = [*required, _LOCAL_INTENT_FIELD]
    return schema


def _validated_arguments(
    tool: ActorToolView, arguments: Mapping[str, Any],
) -> tuple[dict[str, Any], str | None]:
    schema = _provider_parameter_schema(tool)
    try:
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(dict(arguments)),
            key=lambda item: (
                tuple(str(part) for part in item.absolute_path),
                tuple(str(part) for part in item.absolute_schema_path),
            ),
        )
    except SchemaError as exc:
        raise VisualActorModelError("Broker supplied an invalid tool schema") from exc
    if errors:
        raise VisualActorResponseError(
            f"Visual Actor arguments do not match Broker schema for {tool.name!r}"
        )
    execution_arguments = dict(arguments)
    local_intent = None
    if tool.requires_local_intent:
        local_intent = str(execution_arguments.pop(_LOCAL_INTENT_FIELD)).strip()
    return execution_arguments, local_intent


def _proposal_for(
    tool: ActorToolView,
    arguments: Mapping[str, Any],
    *,
    local_intent: str | None,
) -> dict[str, Any]:
    name = tool.name
    if name == "observe":
        raw = {
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "observe",
            "observation_kind": "full",
            "image_region": None,
            "question": str(
                arguments.get("question")
                or "refresh the current authoritative frame"
            ),
        }
    elif name == DISCOVER_TARGET_TOOL_NAME:
        raw = {
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "discover_target",
            **dict(arguments),
        }
    elif name == "claim_obligation_done":
        raw = {
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "claim_obligation_done",
            **dict(arguments),
        }
    elif name == "blocked":
        raw = {
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "blocked",
            **dict(arguments),
        }
    else:
        if local_intent is None:
            raise VisualActorResponseError(
                f"GUI action {name!r} has no bounded local intent"
            )
        raw = {
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "desktop_action",
            "action": name,
            "args": dict(arguments),
            "local_intent": local_intent,
        }
    try:
        proposal = parse_actor_proposal(raw)
        return json.loads(actor_proposal_to_json(proposal))
    except InvalidActorProposal as exc:
        raise VisualActorResponseError(str(exc)) from exc


def _provider_tools(turn: ActorTurnInput) -> list[dict[str, Any]]:
    result = []
    missing_description = object()
    for tool in turn.tools:
        parameters = _provider_parameter_schema(tool)
        description = parameters.pop("description", missing_description)
        if description is not missing_description and (
            type(description) is not str
            or not description.strip()
            or description != description.strip()
        ):
            raise VisualActorModelError(
                f"Broker tool description for {tool.name!r} must be canonical non-empty text"
            )
        function = {
            "name": tool.name,
            "parameters": parameters,
        }
        if description is not missing_description:
            function["description"] = description
        result.append({
            "type": "function",
            "function": function,
        })
    return result


def _previous_action_messages(turn: ActorTurnInput) -> list[dict[str, Any]]:
    previous = turn.previous_action
    transition = turn.mission.last_transition
    if (
        previous is None
        or transition is None
        or not previous.input_completed
        or not previous.post_action_frame_reconciled
        or previous.target_binding_valid is not True
        or (
            previous.action,
            previous.input_completed,
            previous.visible_effect,
            previous.local_visual_change,
            previous.frame_comparable,
            previous.target_binding_valid,
        ) != (
            transition.action,
            transition.input_completed,
            transition.visible_effect,
            transition.local_visual_change,
            transition.frame_comparable,
            transition.target_binding_valid,
        )
    ):
        return []
    matching_tools = tuple(tool for tool in turn.tools if tool.name == previous.action)
    if len(matching_tools) != 1:
        return []
    try:
        tool = matching_tools[0]
        call_arguments = dict(project_action_history_args(
            previous.arguments, tool.input_schema,
        ))
        call_arguments[_LOCAL_INTENT_FIELD] = previous.local_intent
        _validated_arguments(tool, call_arguments)
    except (ActionHistoryProjectionError, VisualActorModelError):
        return []
    receipt_payload = {
        "input_completed": previous.input_completed,
        "post_action_frame_reconciled": previous.post_action_frame_reconciled,
        "visible_effect": previous.visible_effect,
        "temporal_full_frame_change": previous.local_visual_change,
        "frame_comparable": previous.frame_comparable,
        "target_binding_valid": previous.target_binding_valid,
        "semantic_completion": "unknown",
        "causal_attribution": "unknown",
    }
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": _PREVIOUS_ACTION_CALL_ID,
                "type": "function",
                "function": {
                    "name": previous.action,
                    "arguments": json.dumps(
                        call_arguments,
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            }],
        },
        {
            "role": "tool",
            "tool_call_id": _PREVIOUS_ACTION_CALL_ID,
            "content": json.dumps(
                receipt_payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    ]


class OpenAIVisualActor:
    """One isolated model decision over ActorTurnInput and one current image."""

    def __init__(
        self,
        client: Any,
        *,
        model: str,
        retry_policy: ModelRetryPolicy | None = None,
        call_runtime: ModelCallRuntime | None = None,
        decision_id_factory: Callable[[], str] | None = None,
        max_tokens: int = 900,
    ) -> None:
        self._client = client
        self._model = str(model or "").strip()
        if not self._model:
            raise VisualActorModelError("Visual Actor model is required")
        self._retry_policy = retry_policy or ModelRetryPolicy(max_attempts=1)
        self._call_runtime = call_runtime or ModelCallRuntime()
        self._decision_id_factory = decision_id_factory or (
            lambda: f"visual-actor-{uuid.uuid4().hex}"
        )
        self._max_tokens = max(256, min(4096, int(max_tokens)))
        self.last_outcome: ModelCallOutcome | None = None

    @classmethod
    def from_api_config(cls, api: Mapping[str, Any]) -> "OpenAIVisualActor":
        values = dict(api or {})
        base_url = str(values.get("base_url") or "").strip()
        api_key = str(values.get("api_key") or "").strip()
        model = str(values.get("model") or "").strip()
        if not (base_url and api_key and model):
            raise VisualActorModelError("Visual Actor provider is not configured")
        try:
            request_timeout = max(
                5.0, min(180.0, float(values.get("request_timeout_s", 75) or 75))
            )
            total_timeout = max(
                10.0,
                min(300.0, float(values.get("request_total_timeout_s", 120) or 120)),
            )
            configured_retries = int(values.get("max_retries", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise VisualActorModelError(
                "Visual Actor provider timeout/retry config is invalid"
            ) from exc
        raw_client = create_openai_client(
            base_url=base_url,
            api_key=api_key,
            timeout=request_timeout,
        )
        isolated_api = {
            "provider": values.get("provider", "auto"),
            "model": model,
            "thinking": values.get("thinking"),
        }
        if isinstance(values.get("extra_body"), Mapping):
            isolated_api["extra_body"] = dict(values["extra_body"])
        return cls(
            build_model_adapter(raw_client, isolated_api),
            model=model,
            retry_policy=ModelRetryPolicy(
                max_attempts=max(1, min(5, configured_retries + 1)),
                total_timeout_s=total_timeout,
                initial_backoff_s=1.0,
                backoff_multiplier=2.0,
                max_backoff_s=10.0,
            ),
        )

    def propose(
        self,
        turn: ActorTurnInput,
        *,
        image_b64: str,
        submitted_size: tuple[int, int],
        stop_event: threading.Event | None = None,
    ) -> Mapping[str, Any]:
        if not isinstance(turn, ActorTurnInput):
            raise VisualActorModelError("Visual Actor turn has the wrong type")
        if not (
            turn.frame.image_available
            and turn.frame.authoritative
            and turn.frame.fresh
        ):
            raise VisualActorModelError("Visual Actor requires one current authoritative image")
        if type(image_b64) is not str or not image_b64.strip():
            raise VisualActorModelError("Visual Actor image is missing")
        if (
            not isinstance(submitted_size, tuple)
            or len(submitted_size) != 2
            or any(type(value) is not int or value <= 0 for value in submitted_size)
        ):
            raise VisualActorModelError("submitted_size must contain two positive integers")

        tools = _provider_tools(turn)
        projected = {tool.name: tool for tool in turn.tools}
        # Tool schemas already travel through the provider's function surface.
        # Do not duplicate them in message text: both projections still come
        # from the same ActorTurnInput, while the model sees one smaller schema
        # copy and no legacy history.
        turn_payload = json.loads(turn.to_json())
        previous_action_messages = _previous_action_messages(turn)
        mission_payload = turn_payload["mission"]
        if previous_action_messages:
            mission_payload["last_transition"] = None
        user_payload = {
            "submitted_image_size": {
                "width": submitted_size[0],
                "height": submitted_size[1],
            },
            "mission": mission_payload,
            "frame": turn_payload["frame"],
        }
        if turn_payload["target_discovery_feedback"] is not None:
            user_payload["target_discovery_feedback"] = (
                turn_payload["target_discovery_feedback"]
            )
        if turn_payload["target_choices"]:
            user_payload["target_choices"] = turn_payload["target_choices"]
        request = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": VISUAL_ACTOR_SYSTEM_CONTRACT},
                *previous_action_messages,
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                user_payload,
                                ensure_ascii=False,
                                allow_nan=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64.strip()}"
                            },
                        },
                    ],
                },
            ],
            "tools": tools,
            "tool_choice": "required",
            "temperature": 0.0,
            "max_tokens": self._max_tokens,
        }
        outcome = invoke_chat_completion(
            self._client,
            decision_id=self._decision_id_factory(),
            model_role=VISUAL_ACTOR_ROLE,
            purpose="visual_actor_proposal",
            provider_request=request,
            policy=self._retry_policy,
            stop_event=stop_event,
            runtime=self._call_runtime,
        )
        self.last_outcome = outcome
        name, arguments = _one_tool_call(outcome.response)
        tool = projected.get(name)
        if tool is None:
            raise VisualActorResponseError(
                f"Visual Actor called tool outside Broker projection: {name!r}"
            )
        execution_arguments, local_intent = _validated_arguments(tool, arguments)
        return _proposal_for(
            tool,
            execution_arguments,
            local_intent=local_intent,
        )


__all__ = [
    "VISUAL_ACTOR_ROLE",
    "VISUAL_ACTOR_SYSTEM_CONTRACT",
    "VisualActorModelError", "VisualActorResponseError",
    "OpenAIVisualActor",
]
