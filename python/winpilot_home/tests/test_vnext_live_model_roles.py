"""Focused fake-provider tests for the vNext live visual actor."""
from __future__ import annotations

import base64
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from winpilot.modeling import (
    ModelCallError,
    ModelCallRuntime,
    RetryKind,
    create_openai_client,
)
from winpilot.vnext.actor import InvalidActorProposal
from winpilot.vnext.model_roles import (
    MAX_LIVE_MODEL_JSON_RESPONSE_CHARS,
    MAX_LIVE_MODEL_OUTPUT_TOKENS,
    MAX_LIVE_MODEL_TOTAL_TIMEOUT_S,
    VISUAL_ACTOR_SYSTEM_CONTRACT,
    LiveModelRoleError,
    LiveVisualActor,
)
from winpilot.vnext.runtime import (
    ActorTaskStateUpdate,
    ActorTurnDecision,
    ActorFrameView,
    ActorMissionView,
    ActorObligationView,
    ActorPreviousActionView,
    ActorToolView,
    ActorTurnInput,
)


_JPEG_B64 = base64.b64encode(
    b"\xff\xd8\xff\xe0vnext-role-fixture\xff\xd9"
).decode("ascii")


def _point_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "image_x": {"type": "integer", "minimum": 0},
            "image_y": {"type": "integer", "minimum": 0},
        },
        "required": ["image_x", "image_y"],
    }


def _actor_turn() -> ActorTurnInput:
    action_schemas = {
        "click": _point_schema(),
        "double_click": _point_schema(),
        "drag": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "image_x1": {"type": "number", "minimum": 0},
                "image_y1": {"type": "number", "minimum": 0},
                "image_x2": {"type": "number", "minimum": 0},
                "image_y2": {"type": "number", "minimum": 0},
                "image_points": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "number", "minimum": 0},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                    "maxItems": 32,
                },
                "keep_holding": {"type": "boolean"},
            },
            "required": [
                "image_x1",
                "image_y1",
                "image_x2",
                "image_y2",
            ],
        },
        "scroll": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "image_x": {"type": "integer", "minimum": 0},
                "image_y": {"type": "integer", "minimum": 0},
                "amount": {
                    "type": "integer",
                    "minimum": -1200,
                    "maximum": 1200,
                    "not": {"const": 0},
                },
            },
            "required": ["image_x", "image_y", "amount"],
        },
        "type_text": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string", "minLength": 1},
            },
            "required": ["text"],
        },
        "hotkey": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "uniqueItems": True,
                },
            },
            "required": ["keys"],
        },
    }
    tools = [
        ActorToolView(
            name=name,
            schema=schema,
            requires_local_intent=True,
        )
        for name, schema in action_schemas.items()
    ]
    tools.extend(
        (
            ActorToolView(
                name="claim_obligation_done",
                schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "concise_claim": {
                            "type": "string",
                            "minLength": 1,
                        }
                    },
                    "required": ["concise_claim"],
                },
                requires_local_intent=False,
            ),
            ActorToolView(
                name="blocked",
                schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "blocker_kind": {
                            "type": "string",
                            "minLength": 1,
                        },
                        "observed_facts": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "minLength": 1,
                            },
                            "uniqueItems": True,
                        },
                    },
                    "required": ["blocker_kind", "observed_facts"],
                },
                requires_local_intent=False,
            ),
        )
    )
    return ActorTurnInput(
        mission=ActorMissionView(
            original_goal="Type the requested greeting",
            active_obligation=ActorObligationView(
                desired_state="The greeting is visible",
                acceptance_criteria=("The field contains hello",),
                forbidden_effects=("No duplicate text",),
                required_capabilities=("visual.interact",),
            ),
        ),
        frame=ActorFrameView(
            image_available=True,
            authoritative=True,
            fresh=True,
        ),
        tools=tuple(tools),
        completed_action_summaries=("click", "double_click"),
    )


def _tool_response(
    name: str = "type_text",
    arguments: dict[str, Any] | str | None = None,
    *,
    content: Any = None,
    count: int = 1,
    call_id: str = "call-1",
) -> dict[str, Any]:
    state_update = {"scratchpad": None}
    if arguments is None:
        arguments = {
            "text": "hello",
            "local_intent": "enter the requested greeting once",
        }
    if type(arguments) is dict:
        arguments = dict(arguments)
        arguments.setdefault("task_state_update", state_update)
    encoded = (
        arguments
        if type(arguments) is str
        else json.dumps(arguments, separators=(",", ":"))
    )
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {
                            "id": f"{call_id}-{index}",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": encoded,
                            },
                        }
                        for index in range(count)
                    ],
                }
            }
        ]
    }


def _actor_shape_response(
    *,
    role: Any = "assistant",
    include_role: bool = True,
    call_type: Any = "function",
    include_call_type: bool = True,
) -> dict[str, Any]:
    response = _tool_response()
    message = response["choices"][0]["message"]
    call = message["tool_calls"][0]
    if include_role:
        message["role"] = role
    else:
        del message["role"]
    if include_call_type:
        call["type"] = call_type
    else:
        del call["type"]
    return response


def _schema_keywords(value: Any) -> set[str]:
    if type(value) is dict:
        return set(value).union(
            *(_schema_keywords(item) for item in value.values()),
        )
    if type(value) is list:
        return set().union(
            *(_schema_keywords(item) for item in value),
        )
    return set()


class _RecordingClient:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **request: Any) -> Any:
        self.requests.append(request)
        response = self.response
        if type(response) is list:
            index = len(self.requests) - 1
            if index >= len(response):
                raise AssertionError("Unexpected extra provider request")
            response = response[index]
        if isinstance(response, BaseException):
            raise response
        return response


def _build_clients(
    *responses: Any,
) -> tuple[list[dict[str, Any]], list[_RecordingClient]]:
    factory_calls: list[dict[str, Any]] = []
    clients: list[_RecordingClient] = []

    def factory(**kwargs: Any) -> _RecordingClient:
        factory_calls.append(kwargs)
        client = _RecordingClient(responses[len(clients)])
        clients.append(client)
        return client

    for index in range(len(responses)):
        create_openai_client(
            base_url=f"https://provider-{index}.invalid/v1",
            api_key=f"secret-{index}",
            timeout=20,
            client_factory=factory,
        )
    return factory_calls, clients


def test_actor_uses_zero_retry_client_and_strict_visual_context() -> None:
    factory_calls, clients = _build_clients(_tool_response())
    actor_attempts = []
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        on_attempt=actor_attempts.append,
    )

    decision = actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert type(decision) is ActorTurnDecision
    assert dict(decision.proposal) == {
        "action": "type_text",
        "args": {"text": "hello"},
        "format": "actor-proposal/0",
        "kind": "desktop_action",
        "local_intent": "enter the requested greeting once",
    }
    assert decision.task_state_update == ActorTaskStateUpdate.from_dict(
        {"scratchpad": None}
    )
    assert [call["max_retries"] for call in factory_calls] == [0]
    assert len(clients[0].requests) == 1
    assert [attempt.kind for attempt in actor_attempts] == [
        RetryKind.INITIAL_REQUEST
    ]

    actor_request = clients[0].requests[0]
    assert len(actor_request["messages"]) == 2
    assert (
        actor_request["messages"][0]["content"]
        == VISUAL_ACTOR_SYSTEM_CONTRACT
    )
    actor_contract = " ".join(VISUAL_ACTOR_SYSTEM_CONTRACT.split())
    assert len(actor_contract.split()) < 60
    assert "current authoritative JPEG" in actor_contract
    assert "exact submitted-image pixels" in actor_contract
    assert "goal is visibly complete, call claim_obligation_done" in actor_contract
    assert "When pointer_held is true" in actor_contract
    assert actor_request["tool_choice"] == "auto"
    assert "parallel_tool_calls" not in actor_request
    assert "n" not in actor_request
    assert "max_tokens" not in actor_request
    assert actor_request["max_completion_tokens"] == 768
    assert len(actor_request["tools"]) == 8
    assert {
        tool["function"]["name"] for tool in actor_request["tools"]
    } == {
        "click",
        "double_click",
        "drag",
        "scroll",
        "type_text",
        "press_key",
        "claim_obligation_done",
        "blocked",
    }
    descriptions = {
        tool["function"]["name"]: tool["function"]["description"]
        for tool in actor_request["tools"]
    }
    assert "once" in descriptions["click"]
    assert "Double-click" in descriptions["double_click"]
    assert "ordered intermediate image_points" in descriptions["drag"]
    assert "amount < 0 scrolls down" in descriptions["scroll"]
    assert "amount > 0 scrolls up" in descriptions["scroll"]
    assert "selected text is replaced" in descriptions["type_text"]
    assert "one key or one ordered key chord" in descriptions["press_key"]
    assert "never enters literal text" in descriptions["press_key"]
    assert "without further mutation" in descriptions["claim_obligation_done"]
    assert "no supplied action can advance" in descriptions["blocked"]
    assert "response_format" not in actor_request
    strict_schemas = [
        tool["function"]["parameters"]
        for tool in actor_request["tools"]
    ]
    for schema in strict_schemas:
        assert "task_state_update" in schema["properties"]
        assert "task_state_update" in schema["required"]
    for schema in strict_schemas:
        assert {
            "allOf",
            "dependentRequired",
            "dependentSchemas",
            "if",
            "then",
            "else",
            "not",
            "uniqueItems",
        }.isdisjoint(_schema_keywords(schema))

    actor_context = actor_request["messages"][1]["content"][0]["text"]
    actor_context_value = json.loads(actor_context)
    assert "tools" not in actor_context_value
    assert actor_context_value["mission"]["original_goal"] == (
        "Type the requested greeting"
    )
    assert actor_context_value["task_state"] == {"scratchpad": ""}
    assert actor_context_value["completed_action_summaries"] == [
        "click",
        "double_click",
    ]
    assert "data:image/jpeg;base64," in (
        actor_request["messages"][1]["content"][1]["image_url"]["url"]
    )


def test_press_key_model_tool_maps_to_existing_hotkey_runtime_action() -> None:
    _, clients = _build_clients(
        _tool_response(
            name="press_key",
            arguments={
                "keys": ["9"],
                "local_intent": "press the requested visible key",
            },
        )
    )
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )

    decision = actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert dict(decision.proposal) == {
        "action": "hotkey",
        "args": {"keys": ["9"]},
        "format": "actor-proposal/0",
        "kind": "desktop_action",
        "local_intent": "press the requested visible key",
    }


@pytest.mark.parametrize(
    "previous_action",
    (
        ActorPreviousActionView(
            action="scroll",
            semantic_summary="scroll_down",
            input_completed=True,
            visible_effect="",
            local_visual_change="changed",
            frame_comparable=True,
            target_binding_valid=True,
        ),
        ActorPreviousActionView(
            action="click",
            semantic_summary="click",
            input_completed=True,
            visible_effect="",
            local_visual_change="changed",
            frame_comparable=True,
            target_binding_valid=True,
        ),
    ),
)
def test_previous_action_is_passed_without_runtime_strategy_hint(
    previous_action: ActorPreviousActionView,
) -> None:
    _, clients = _build_clients(_tool_response())
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )

    actor.propose(
        replace(_actor_turn(), previous_action=previous_action),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    context = json.loads(
        clients[0].requests[0]["messages"][1]["content"][0]["text"]
    )
    assert context["previous_action"] == previous_action.to_dict()
    assert "scroll_guard" not in context
    assert "focus_guard" not in context


@pytest.mark.parametrize(
    "summary",
    ("secret_token", "hotkey_secret_token", "scroll_sideways"),
)
def test_actor_turn_contract_rejects_arbitrary_action_summaries(
    summary: str,
) -> None:
    with pytest.raises(ValueError, match="privacy-safe"):
        replace(
            _actor_turn(),
            completed_action_summaries=(summary,),
        )


def test_consecutive_calls_keep_only_the_latest_native_tool_exchange() -> None:
    _, clients = _build_clients([
        _tool_response(call_id="first"),
        _tool_response(call_id="second"),
        _tool_response(call_id="third"),
    ])
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    turn = _actor_turn()

    actor.propose(
        turn,
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )
    clients[0].requests[0]["messages"].append(
        {"role": "assistant", "content": "private prior reasoning"}
    )
    completed = replace(
        turn,
        previous_action=ActorPreviousActionView(
            action="type_text",
            input_completed=True,
            visible_effect="changed",
        ),
    )
    actor.propose(
        completed,
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )
    actor.propose(
        completed,
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    second_messages = clients[0].requests[1]["messages"]
    assert [message["role"] for message in second_messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "user",
    ]
    assert second_messages[2]["tool_calls"][0]["id"] == "first-0"
    assert second_messages[3]["tool_call_id"] == "first-0"
    assert json.loads(second_messages[3]["content"]) == {
        "previous_action": completed.previous_action.to_dict(),
    }
    assert "private prior reasoning" not in json.dumps(
        second_messages
    )
    third_messages = clients[0].requests[2]["messages"]
    assert len(third_messages) == 5
    assert third_messages[2]["tool_calls"][0]["id"] == "second-0"
    assert "first-0" not in json.dumps(third_messages)


@pytest.mark.parametrize(
    "response, error",
    [
        (
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "done",
                            "tool_calls": [],
                        }
                    }
                ]
            },
            "only one tool call",
        ),
        (_tool_response(count=2), "exactly one tool call"),
        (_tool_response(content="explanation"), "only one tool call"),
        (_tool_response(name="observe"), "unapproved tool"),
        (_tool_response(arguments="{bad json"), "not valid JSON"),
        (
            _tool_response(
                arguments=(
                    '{"text":"hello","text":"again",'
                    '"local_intent":"type once"}'
                )
            ),
            "duplicate field",
        ),
        (
            _tool_response(
                name="click",
                arguments={
                    "image_x": 1,
                    "image_y": 2,
                    "local_intent": "click once",
                    "frame_id": 7,
                },
            ),
            "Runtime-owned identity",
        ),
        (
            _actor_shape_response(include_role=False),
            "message is not assistant",
        ),
        (
            _actor_shape_response(role="user"),
            "message is not assistant",
        ),
        (
            _actor_shape_response(include_call_type=False),
            "tool call must be a function",
        ),
        (
            _actor_shape_response(call_type="custom"),
            "tool call must be a function",
        ),
    ],
)
def test_actor_fails_closed_on_noncanonical_model_output(
    response: dict[str, Any],
    error: str,
) -> None:
    _, clients = _build_clients(response)
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    with pytest.raises(InvalidActorProposal, match=error):
        actor.propose(
            _actor_turn(),
            image_b64=_JPEG_B64,
            image_media_type="image/jpeg",
            submitted_size=(640, 480),
        )


def test_actor_permissions_are_the_exact_broker_projected_subset() -> None:
    _, clients = _build_clients(
        _tool_response(
            name="click",
            arguments={
                "image_x": 400,
                "image_y": 300,
                "local_intent": "click the visible control once",
            },
        )
    )
    full_turn = _actor_turn()
    turn = replace(full_turn, tools=(full_turn.tools[0],))
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    decision = actor.propose(
        turn,
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )
    assert decision.proposal["action"] == "click"
    assert decision.proposal["args"] == {
        "image_x": 400,
        "image_y": 300,
    }
    assert [
        tool["function"]["name"]
        for tool in clients[0].requests[0]["tools"]
    ] == ["click"]
    assert len(clients[0].requests) == 1


def test_actor_preserves_an_ordered_optional_runtime_drag_path() -> None:
    _, clients = _build_clients(
        _tool_response(
            name="drag",
            arguments={
                "image_x1": 40,
                "image_y1": 50,
                "image_x2": 500,
                "image_y2": 400,
                "image_points": [[120, 60], [250, 220], [420, 260]],
                "keep_holding": True,
                "local_intent": (
                    "drag around the visible obstacle through the opening"
                ),
            },
        )
    )
    turn = _actor_turn()
    drag = next(tool for tool in turn.tools if tool.name == "drag")
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )

    decision = actor.propose(
        replace(turn, tools=(drag,), pointer_held=True),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert decision.proposal["args"] == {
        "image_x1": 40,
        "image_y1": 50,
        "image_x2": 500,
        "image_y2": 400,
        "image_points": [[120, 60], [250, 220], [420, 260]],
        "keep_holding": True,
    }
    actor_schema = clients[0].requests[0]["tools"][0]["function"][
        "parameters"
    ]
    assert "image_points" in actor_schema["required"]
    assert "keep_holding" in actor_schema["required"]
    assert actor_schema["properties"]["image_points"]["maxItems"] == 32
    assert len(clients[0].requests) == 1


def test_actor_rejects_malformed_task_state_without_echoing_private_text() -> None:
    sentinel = "PRIVATE-STATE-MUST-NOT-ECHO"
    _, clients = _build_clients(
        _tool_response(
            arguments={
                "text": "hello",
                "local_intent": "type once",
                "task_state_update": {"unexpected": sentinel},
            },
        )
    )
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )

    with pytest.raises(
        InvalidActorProposal,
        match="exactly scratchpad",
    ) as caught:
        actor.propose(
            _actor_turn(),
            image_b64=_JPEG_B64,
            image_media_type="image/jpeg",
            submitted_size=(640, 480),
        )

    assert sentinel not in str(caught.value)


def test_actor_rejects_unknown_strict_schema_keywords_before_transport() -> None:
    _, clients = _build_clients(_tool_response())
    turn = _actor_turn()
    click = turn.tools[0]
    bad_schema = click.to_dict()["schema"]
    bad_schema["properties"]["image_x"] = {
        "allOf": [{"type": "integer"}, {"minimum": 0}],
    }
    bad_click = ActorToolView(
        name="click",
        schema=bad_schema,
        requires_local_intent=True,
    )
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )

    with pytest.raises(
        LiveModelRoleError,
        match="unsupported strict schema keywords",
    ):
        actor.propose(
            replace(turn, tools=(bad_click,)),
            image_b64=_JPEG_B64,
            image_media_type="image/jpeg",
            submitted_size=(640, 480),
        )
    assert clients[0].requests == []


@pytest.mark.parametrize(
    "name, arguments, expected",
    [
        (
            "claim_obligation_done",
            {"concise_claim": "The greeting is visibly present."},
            {
                "format": "actor-proposal/0",
                "kind": "claim_obligation_done",
                "concise_claim": "The greeting is visibly present.",
            },
        ),
        (
            "blocked",
            {
                "blocker_kind": "visible_precondition_missing",
                "observed_facts": ["The required field is not visible."],
            },
            {
                "format": "actor-proposal/0",
                "kind": "blocked",
                "blocker_kind": "visible_precondition_missing",
                "observed_facts": [
                    "The required field is not visible."
                ],
            },
        ),
    ],
)
def test_actor_maps_only_the_two_approved_runtime_outcomes(
    name: str,
    arguments: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    _, clients = _build_clients(_tool_response(name, arguments))
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    assert dict(actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    ).proposal) == expected


def test_actor_uses_call_once_transport_policy() -> None:
    _, clients = _build_clients(TimeoutError("transient transport failure"))
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(sleeper=lambda _delay: None),
    )

    with pytest.raises(ModelCallError) as caught:
        actor.propose(
            _actor_turn(),
            image_b64=_JPEG_B64,
            image_media_type="image/jpeg",
            submitted_size=(640, 480),
        )
    assert len(clients[0].requests) == 1
    assert len(caught.value.attempts) == 1
    assert caught.value.request_retry_count == 0


@pytest.mark.parametrize(
    "kwargs, error",
    [
        (
            {"total_timeout_s": MAX_LIVE_MODEL_TOTAL_TIMEOUT_S + 1},
            "total_timeout_s",
        ),
        (
            {"max_output_tokens": MAX_LIVE_MODEL_OUTPUT_TOKENS + 1},
            "max_output_tokens",
        ),
    ],
)
def test_role_configuration_budgets_have_hard_upper_bounds(
    kwargs: dict[str, Any],
    error: str,
) -> None:
    _, clients = _build_clients(_tool_response())
    with pytest.raises(LiveModelRoleError, match=error):
        LiveVisualActor(
            client=clients[0],
            model="vision-model",
            runtime=ModelCallRuntime(),
            **kwargs,
        )


def test_role_json_outputs_have_a_hard_character_bound() -> None:
    oversized = "{" + (
        " " * MAX_LIVE_MODEL_JSON_RESPONSE_CHARS
    ) + "}"
    _, clients = _build_clients(_tool_response(arguments=oversized))
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    with pytest.raises(InvalidActorProposal, match="exceeds"):
        actor.propose(
            _actor_turn(),
            image_b64=_JPEG_B64,
            image_media_type="image/jpeg",
            submitted_size=(640, 480),
        )
