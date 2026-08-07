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
from winpilot.vnext.actor import InvalidActorProposal, actor_proposal_to_json
from winpilot.vnext.model_roles import (
    FlowFragmentProposer,
    INITIAL_ACTOR_SYSTEM_CONTRACT,
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


def _connector_response(
    name: str,
    arguments: dict[str, str],
    *,
    call_id: str,
) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(
                                    arguments,
                                    separators=(",", ":"),
                                ),
                            },
                        }
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


class _RecordingConnectorGateway:
    available = True

    def __init__(self) -> None:
        self.searches: list[dict[str, str]] = []
        self.calls: list[dict[str, Any]] = []

    def search(
        self,
        *,
        query: str,
        connector: str = "",
        timeout_s: float,
        stop_event: Any | None,
    ) -> dict[str, Any]:
        self.searches.append({"query": query, "connector": connector})
        return {
            "ok": True,
            "tools": [
                {
                    "ref": "connector-ref",
                    "connector": "github",
                    "name": "get_issue",
                }
            ],
        }

    def call(
        self,
        *,
        ref: str,
        arguments_json: str,
        timeout_s: float,
        stop_event: Any | None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "ref": ref,
                "arguments_json": arguments_json,
            }
        )
        return {"ok": True, "content": "issue-value"}


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
    assert len(actor_contract.split()) < 100
    assert "current authoritative JPEG" in actor_contract
    assert "exact submitted-image pixels" in actor_contract
    assert "goal is visibly complete, call claim_obligation_done" in actor_contract
    assert "may mix chat and task" in actor_contract
    assert "call blocked instead of repeating it" in actor_contract
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
        assert "task_state_update" not in schema["required"]
        assert schema["properties"]["task_state_update"]["default"] == {
            "scratchpad": None
        }
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


def test_initial_actor_turn_replies_with_native_history_and_short_system_contract() -> None:
    reply = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "remembered-value",
                    "tool_calls": [],
                }
            }
        ]
    }
    _, clients = _build_clients(reply)
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    history = (
        {"role": "user", "content": "Remember this value."},
        {"role": "assistant", "content": "remembered-value"},
    )

    admission = actor.begin_turn(
        "What was the value?",
        history=history,
    )

    assert admission.kind == "reply"
    assert admission.reply == "remembered-value"
    request = clients[0].requests[0]
    assert request["messages"] == [
        {"role": "system", "content": INITIAL_ACTOR_SYSTEM_CONTRACT},
        *history,
        {"role": "user", "content": "What was the value?"},
    ]
    assert request["messages"][0]["role"] == "system"
    initial_contract = " ".join(INITIAL_ACTOR_SYSTEM_CONTRACT.split())
    assert len(initial_contract.split()) < 120
    assert "live screen" in initial_contract
    assert "mixes chat and a task" in initial_contract
    # The initial contract must not teach keyword routing: no verb list is
    # mapped to observe_desktop.
    assert "Call observe_desktop for any request" not in initial_contract
    assert [tool["function"]["name"] for tool in request["tools"]] == [
        "observe_desktop"
    ]
    assert request["tools"][0]["function"]["description"] == (
        "Observe the desktop."
    )
    with pytest.raises(
        LiveModelRoleError,
        match="terminal direct reply",
    ):
        actor.propose(
            _actor_turn(),
            image_b64=_JPEG_B64,
            image_media_type="image/jpeg",
            submitted_size=(640, 480),
        )


def test_initial_observe_with_content_continues_on_the_same_actor() -> None:
    observe = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "I will inspect the desktop.",
                    "reasoning_content": "provider-native-reasoning",
                    "tool_calls": [
                        {
                            "id": "observe-call",
                            "type": "function",
                            "function": {
                                "name": "observe_desktop",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            }
        ]
    }
    _, clients = _build_clients(
        [observe, _tool_response(call_id="visual-call")]
    )
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )

    admission = actor.begin_turn(
        "Complete the visible task",
        history=(
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": "Earlier answer"},
        ),
    )
    decision = actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert admission.kind == "desktop"
    assert admission.reply == ""
    assert isinstance(decision, ActorTurnDecision)
    assert len(clients[0].requests) == 2
    visual_messages = clients[0].requests[1]["messages"]
    assert visual_messages[0]["role"] == "system"
    assert visual_messages[1:4] == [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
        {"role": "user", "content": "Complete the visible task"},
    ]
    assert visual_messages[4]["content"] == "I will inspect the desktop."
    assert visual_messages[4]["reasoning_content"] == (
        "provider-native-reasoning"
    )
    assert visual_messages[5] == {
        "role": "tool",
        "tool_call_id": "observe-call",
        "content": '{"desktop_observation":"available"}',
    }
    assert visual_messages[6]["content"][1]["type"] == "image_url"


def test_initial_actor_lazily_calls_a_read_only_connector_then_replies() -> None:
    reply = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "The issue contains issue-value.",
                    "tool_calls": [],
                }
            }
        ]
    }
    _, clients = _build_clients(
        [
            _connector_response(
                "search_connector_tools",
                {"query": "issue", "connector": "github"},
                call_id="search-1",
            ),
            _connector_response(
                "call_connector_tool",
                {
                    "ref": "connector-ref",
                    "arguments_json": '{"number":7}',
                },
                call_id="call-1",
            ),
            reply,
        ]
    )
    gateway = _RecordingConnectorGateway()
    responses = []
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        connector_gateway=gateway,
        on_response=responses.append,
    )

    admission = actor.begin_turn("Read issue 7 from GitHub")

    assert admission.kind == "reply"
    assert admission.reply == "The issue contains issue-value."
    assert gateway.searches == [{"query": "issue", "connector": "github"}]
    assert gateway.calls == [
        {
            "ref": "connector-ref",
            "arguments_json": '{"number":7}',
        }
    ]
    assert len(responses) == 3
    assert actor.model_call_reservation == 6
    assert len(clients[0].requests) == 3
    assert [
        tool["function"]["name"]
        for tool in clients[0].requests[0]["tools"]
    ] == [
        "observe_desktop",
        "search_connector_tools",
        "call_connector_tool",
    ]
    assert [message["role"] for message in clients[0].requests[2]["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]


def test_visual_actor_can_use_a_connector_before_one_gui_action() -> None:
    _, clients = _build_clients(
        [
            _connector_response(
                "search_connector_tools",
                {"query": "issue", "connector": "github"},
                call_id="visual-search",
            ),
            _connector_response(
                "call_connector_tool",
                {
                    "ref": "connector-ref",
                    "arguments_json": '{"number":7}',
                },
                call_id="visual-call",
            ),
            _tool_response(
                arguments={
                    "text": "issue-value",
                    "local_intent": "enter the connected value once",
                },
                call_id="gui-call",
            ),
        ]
    )
    gateway = _RecordingConnectorGateway()
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        connector_gateway=gateway,
    )

    decision = actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert decision.proposal["action"] == "type_text"
    assert decision.proposal["args"] == {"text": "issue-value"}
    assert len(clients[0].requests) == 3
    assert [message["role"] for message in clients[0].requests[2]["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    assert gateway.calls[0]["ref"] == "connector-ref"


def test_last_allowed_model_call_does_not_offer_nonterminal_connector_tools() -> None:
    _, clients = _build_clients(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "budget-safe reply",
                        "tool_calls": [],
                    }
                }
            ]
        }
    )
    gateway = _RecordingConnectorGateway()
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        connector_gateway=gateway,
    )

    admission = actor.begin_turn(
        "Answer without another tool round",
        _model_call_limit=1,
    )

    assert admission.reply == "budget-safe reply"
    assert len(clients[0].requests) == 1
    assert [
        tool["function"]["name"]
        for tool in clients[0].requests[0]["tools"]
    ] == ["observe_desktop"]
    assert gateway.searches == []


def test_initial_actor_turn_is_call_once_and_accepts_a_long_goal() -> None:
    reply = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "ok",
                    "tool_calls": [],
                }
            }
        ]
    }
    _, clients = _build_clients(reply)
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )

    assert actor.begin_turn("x" * 1024).reply == "ok"
    with pytest.raises(LiveModelRoleError, match="call-once"):
        actor.begin_turn("another turn")


def test_initial_actor_turn_drops_oldest_pairs_to_fit_long_history() -> None:
    reply = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "recent-context",
                    "tool_calls": [],
                }
            }
        ]
    }
    _, clients = _build_clients(reply)
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    history = tuple(
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"{index:02d}-" + ("x" * 1_997),
        }
        for index in range(12)
    )

    assert actor.begin_turn("recall the latest context", history=history).reply == (
        "recent-context"
    )

    messages = clients[0].requests[0]["messages"]
    assert messages[0] == {
        "role": "system",
        "content": INITIAL_ACTOR_SYSTEM_CONTRACT,
    }
    assert messages[1:-1] == list(history[4:])
    assert messages[-1] == {
        "role": "user",
        "content": "recall the latest context",
    }


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
            semantic_summary="scroll",
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


def test_consecutive_calls_keep_a_bounded_native_tool_exchange_history() -> None:
    _, clients = _build_clients([
        _tool_response(call_id="first"),
        _tool_response(call_id="second"),
        _tool_response(call_id="third"),
        _tool_response(call_id="fourth"),
        _tool_response(call_id="fifth"),
        _tool_response(call_id="sixth"),
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
    assert len(third_messages) == 8
    assert third_messages[2]["tool_calls"][0]["id"] == "first-0"
    assert third_messages[3]["tool_call_id"] == "first-0"
    assert third_messages[5]["tool_calls"][0]["id"] == "second-0"
    assert third_messages[6]["tool_call_id"] == "second-0"
    assert json.loads(third_messages[6]["content"]) == {
        "previous_action": completed.previous_action.to_dict(),
    }

    actor.propose(
        completed,
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )
    fourth_messages = clients[0].requests[3]["messages"]
    assert len(fourth_messages) == 11
    assert fourth_messages[5]["tool_calls"][0]["id"] == "second-0"
    assert fourth_messages[8]["tool_calls"][0]["id"] == "third-0"
    assert "first-0" in json.dumps(fourth_messages)

    actor.propose(
        completed,
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )
    fifth_messages = clients[0].requests[4]["messages"]
    assert len(fifth_messages) == 14
    assert "first-0" in json.dumps(fifth_messages)
    assert fifth_messages[2]["tool_calls"][0]["id"] == "first-0"
    assert fifth_messages[5]["tool_calls"][0]["id"] == "second-0"
    assert fifth_messages[8]["tool_calls"][0]["id"] == "third-0"
    assert fifth_messages[11]["tool_calls"][0]["id"] == "fourth-0"

    actor.propose(
        completed,
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )
    sixth_messages = clients[0].requests[5]["messages"]
    assert len(sixth_messages) == 14
    assert "first-0" not in json.dumps(sixth_messages)
    assert sixth_messages[2]["tool_calls"][0]["id"] == "second-0"
    assert sixth_messages[5]["tool_calls"][0]["id"] == "third-0"
    assert sixth_messages[8]["tool_calls"][0]["id"] == "fourth-0"
    assert sixth_messages[11]["tool_calls"][0]["id"] == "fifth-0"


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
            "exactly one tool call",
        ),
        (_tool_response(count=2), "exactly one tool call"),
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


def test_actor_preserves_narration_alongside_one_tool_call() -> None:
    """The Actor may narrate progress in content while still acting."""
    _, clients = _build_clients(
        _tool_response(
            name="click",
            arguments={
                "image_x": 400,
                "image_y": 300,
                "local_intent": "click the canvas once",
            },
            content="我找到了画板，现在在画布上落笔。",
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
    assert decision.narration == "我找到了画板，现在在画布上落笔。"
    assert decision.proposal["kind"] == "desktop_action"
    assert decision.proposal["action"] == "click"


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
    assert "image_points" not in actor_schema["required"]
    assert "keep_holding" not in actor_schema["required"]
    assert actor_schema["properties"]["image_points"]["default"] == []
    assert actor_schema["properties"]["keep_holding"]["default"] is False
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


def test_actor_proposes_inert_flow_fragment_without_default_prompt_or_desktop_tools() -> None:
    response = _tool_response(
        "propose_flow_fragment",
        json.dumps(
            {
                "summary": "click then wait",
                "nodes": [
                    {"local_id": "click", "type": "click", "label": "点击", "params_json": "{\"x\":420,\"y\":300}"},
                    {"local_id": "wait", "type": "wait", "label": "等待", "params_json": "{\"wait_type\":\"time\",\"duration\":1}"},
                ],
                "edges": [{"source": "click", "target": "wait", "source_port": "output"}],
            },
            separators=(",", ":"),
        ),
    )
    _, clients = _build_clients(response)
    actor = FlowFragmentProposer(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )

    fragment = actor.propose_flow_fragment(
        {"request": "click then wait", "document": {"node_count": 2}},
        catalog_ports={"click": ("output",), "wait": ("output",)},
        catalog_params={
            "click": frozenset({"x", "y"}),
            "wait": frozenset({"wait_type", "duration"}),
        },
    )

    assert fragment["summary"] == "click then wait"
    assert fragment["nodes"][0]["params"] == {"x": 420, "y": 300}
    request = clients[0].requests[0]
    assert request["messages"] == [{
        "role": "user",
        "content": '{"document":{"node_count":2},"request":"click then wait"}',
    }]
    assert request["tool_choice"] == "auto"
    assert len(clients[0].requests) == 1
    assert [tool["function"]["name"] for tool in request["tools"]] == [
        "propose_flow_fragment"
    ]
    node_schema = request["tools"][0]["function"]["parameters"]["properties"]["nodes"]["items"]
    assert node_schema["properties"]["params_json"]["type"] == "string"
    assert "observe_desktop" not in json.dumps(request)


def test_actor_rejects_flow_fragment_edge_outside_catalog_port() -> None:
    response = _tool_response(
        "propose_flow_fragment",
        json.dumps(
            {
                "summary": "bad edge",
                "nodes": [
                    {"local_id": "click", "type": "click", "label": "", "params_json": "{}"},
                    {"local_id": "wait", "type": "wait", "label": "", "params_json": "{}"},
                ],
                "edges": [{"source": "click", "target": "wait", "source_port": "fail"}],
            },
            separators=(",", ":"),
        ),
    )
    _, clients = _build_clients(response)
    actor = FlowFragmentProposer(client=clients[0], model="vision-model", runtime=ModelCallRuntime())

    with pytest.raises(LiveModelRoleError, match="has no 'fail' output"):
        actor.propose_flow_fragment(
            {"request": "bad"},
            catalog_ports={"click": ("output",), "wait": ("output",)},
            catalog_params={"click": frozenset(), "wait": frozenset()},
        )


@pytest.mark.parametrize(
    ("params_json", "match"),
    [
        ('{"x":NaN}', "non-JSON number"),
        ('{"x":1,"x":2}', "duplicate field"),
    ],
)
def test_actor_rejects_non_strict_flow_fragment_params_json(
    params_json: str,
    match: str,
) -> None:
    response = _tool_response(
        "propose_flow_fragment",
        json.dumps({
            "summary": "bad params",
            "nodes": [{
                "local_id": "click",
                "type": "click",
                "label": "",
                "params_json": params_json,
            }],
            "edges": [],
        }),
    )
    _, clients = _build_clients(response)
    actor = FlowFragmentProposer(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )

    with pytest.raises(LiveModelRoleError, match=match):
        actor.propose_flow_fragment(
            {"request": "bad"},
            catalog_ports={"click": ("output",)},
            catalog_params={"click": frozenset({"x"})},
        )


def test_actor_rejects_flow_fragment_directed_cycle() -> None:
    response = _tool_response(
        "propose_flow_fragment",
        json.dumps(
            {
                "summary": "cycle",
                "nodes": [
                    {"local_id": "a", "type": "click", "label": "", "params_json": "{}"},
                    {"local_id": "b", "type": "wait", "label": "", "params_json": "{}"},
                ],
                "edges": [
                    {"source": "a", "target": "b", "source_port": "output"},
                    {"source": "b", "target": "a", "source_port": "output"},
                ],
            },
            separators=(",", ":"),
        ),
    )
    _, clients = _build_clients(response)
    actor = FlowFragmentProposer(client=clients[0], model="vision-model", runtime=ModelCallRuntime())

    with pytest.raises(LiveModelRoleError, match="directed cycle"):
        actor.propose_flow_fragment(
            {"request": "cycle"},
            catalog_ports={"click": ("output",), "wait": ("output",)},
            catalog_params={"click": frozenset(), "wait": frozenset()},
        )


@pytest.mark.parametrize(
    ("nodes", "edges", "match"),
    [
        (
            [
                {"local_id": "a", "type": "click", "label": "", "params_json": "{}"},
                {"local_id": "b", "type": "wait", "label": "", "params_json": "{}"},
                {"local_id": "c", "type": "wait", "label": "", "params_json": "{}"},
            ],
            [
                {"source": "a", "target": "b", "source_port": "output"},
                {"source": "a", "target": "c", "source_port": "output"},
            ],
            "only one node",
        ),
        (
            [
                {"local_id": "a", "type": "click", "label": "", "params_json": "{}"},
                {"local_id": "b", "type": "wait", "label": "", "params_json": "{}"},
            ],
            [],
            "requires internal edges",
        ),
        (
            [{"local_id": "a", "type": "click", "label": "", "params_json": "{\"offset_x\":9}"}],
            [],
            "unavailable params",
        ),
    ],
)
def test_actor_rejects_fragment_semantics_the_renderer_would_reject(
    nodes: list[dict[str, str]],
    edges: list[dict[str, str]],
    match: str,
) -> None:
    response = _tool_response(
        "propose_flow_fragment",
        json.dumps(
            {"summary": "bad", "nodes": nodes, "edges": edges},
            separators=(",", ":"),
        ),
    )
    _, clients = _build_clients(response)
    actor = FlowFragmentProposer(client=clients[0], model="vision-model", runtime=ModelCallRuntime())

    with pytest.raises(LiveModelRoleError, match=match):
        actor.propose_flow_fragment(
            {"request": "bad"},
            catalog_ports={"click": ("output",), "wait": ("output",)},
            catalog_params={"click": frozenset(), "wait": frozenset()},
        )


class _RecordingStatusProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def resolve(self, name: str) -> dict[str, Any]:
        self.calls.append(name)
        if name == "get_agent_status":
            return {"format": "agent-status/1", "model": "vision-model"}
        if name == "get_memory_status":
            return {
                "format": "memory-status/1",
                "long_term_experience_memory": "disabled",
                "experience_policy": "disabled",
            }
        if name == "get_capabilities":
            return {
                "format": "capabilities/1",
                "direct_reply": True,
                "observe_desktop": True,
            }
        raise ValueError(name)


def _status_response(
    name: str,
    *,
    call_id: str,
) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            }
        ]
    }


def test_status_tool_tolerates_surplus_fields_like_owned_tools() -> None:
    reply = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "status read",
                    "tool_calls": [],
                }
            }
        ]
    }
    status = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "status-surplus-1",
                            "type": "function",
                            "function": {
                                "name": "get_memory_status",
                                "arguments": (
                                    '{"task_state_update": '
                                    '{"scratchpad": "keep me"}}'
                                ),
                            },
                        }
                    ],
                }
            }
        ]
    }
    _, clients = _build_clients([status, reply])
    provider = _RecordingStatusProvider()
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        status_provider=provider,
    )

    admission = actor.begin_turn("check memory")

    assert admission.kind == "reply"
    assert provider.calls == ["get_memory_status"]


def test_connector_tool_tolerates_surplus_fields_like_owned_tools() -> None:
    reply = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "fetched",
                    "tool_calls": [],
                }
            }
        ]
    }
    _, clients = _build_clients(
        [
            _connector_response(
                "search_connector_tools",
                {
                    "query": "issue",
                    "connector": "github",
                    "task_state_update": {"scratchpad": "keep"},
                },
                call_id="search-surplus-1",
            ),
            reply,
        ]
    )
    gateway = _RecordingConnectorGateway()
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        connector_gateway=gateway,
    )

    admission = actor.begin_turn("search github issues")

    assert admission.kind == "reply"
    assert gateway.searches == [{"query": "issue", "connector": "github"}]


def test_initial_actor_resolves_own_status_without_a_desktop_lease() -> None:
    reply = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "memory is disabled",
                    "tool_calls": [],
                }
            }
        ]
    }
    _, clients = _build_clients(
        [
            _status_response("get_memory_status", call_id="status-1"),
            reply,
        ]
    )
    provider = _RecordingStatusProvider()
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        status_provider=provider,
    )

    admission = actor.begin_turn("Is long-term memory enabled?")

    assert admission.kind == "reply"
    assert admission.reply == "memory is disabled"
    assert provider.calls == ["get_memory_status"]
    assert len(clients[0].requests) == 2
    assert [
        tool["function"]["name"]
        for tool in clients[0].requests[0]["tools"]
    ] == [
        "observe_desktop",
        "get_agent_status",
        "get_memory_status",
        "get_capabilities",
    ]
    assert [message["role"] for message in clients[0].requests[1]["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    tool_result = json.loads(clients[0].requests[1]["messages"][3]["content"])
    assert tool_result["experience_policy"] == "disabled"


def test_status_tools_are_never_offered_on_the_last_allowed_model_call() -> None:
    reply = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "budget-safe reply",
                    "tool_calls": [],
                }
            }
        ]
    }
    _, clients = _build_clients(reply)
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        status_provider=_RecordingStatusProvider(),
    )

    admission = actor.begin_turn(
        "Answer without another tool round",
        _model_call_limit=2,
    )

    assert admission.reply == "budget-safe reply"
    assert len(clients[0].requests) == 1
    assert [
        tool["function"]["name"]
        for tool in clients[0].requests[0]["tools"]
    ] == ["observe_desktop"]
    assert actor.model_call_reservation == 4


def test_visual_actor_repairs_one_invalid_tool_call_before_physical_input() -> None:
    repaired = _tool_response(
        "type_text",
        {
            "text": "hello",
            "local_intent": "enter the requested greeting once",
        },
        call_id="repaired-call",
    )
    _, clients = _build_clients(
        [
            _tool_response(
                "type_text",
                # Malformed task_state_update; strict schema now allows an
                # omitted local_intent, so the repair must be provoked by
                # the state contract instead.
                {
                    "text": "hello",
                    "local_intent": "enter the requested greeting once",
                    "task_state_update": {"scratchpad": 12345},
                },
                call_id="broken-call",
            ),
            repaired,
        ]
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

    assert isinstance(decision, ActorTurnDecision)
    assert decision.proposal["args"]["text"] == "hello"
    assert len(clients[0].requests) == 2
    repair_messages = clients[0].requests[1]["messages"]
    assert repair_messages[-2]["tool_calls"][0]["id"] == "broken-call-0"
    tool_result = json.loads(repair_messages[-1]["content"])
    assert "tool_format_error" in tool_result
    assert "scratchpad" in tool_result["tool_format_error"]

def test_initial_observe_with_arguments_is_repaired_once_then_terminates() -> None:
    bad_observe = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "observe-with-args",
                            "type": "function",
                            "function": {
                                "name": "observe_desktop",
                                "arguments": '{"app":"notepad"}',
                            },
                        }
                    ],
                }
            }
        ]
    }
    good_observe = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "observe-fixed",
                            "type": "function",
                            "function": {
                                "name": "observe_desktop",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            }
        ]
    }
    _, clients = _build_clients([bad_observe, good_observe])
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )

    admission = actor.begin_turn("Open notepad and wait")

    assert admission.kind == "desktop"
    assert len(clients[0].requests) == 2
    repair_messages = clients[0].requests[1]["messages"]
    assert repair_messages[-2]["tool_calls"][0]["id"] == "observe-with-args"
    tool_result = json.loads(repair_messages[-1]["content"])
    assert tool_result["tool_format_error"] == (
        "observe_desktop does not accept arguments"
    )


def test_initial_observe_second_format_failure_is_terminal() -> None:
    bad_observe = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "observe-with-args",
                            "type": "function",
                            "function": {
                                "name": "observe_desktop",
                                "arguments": '{"app":"notepad"}',
                            },
                        }
                    ],
                }
            }
        ]
    }
    _, clients = _build_clients([bad_observe, bad_observe])
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )

    with pytest.raises(
        LiveModelRoleError,
        match="observe_desktop does not accept arguments",
    ):
        actor.begin_turn("Open notepad and wait")

class _RecordingShellRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result: dict[str, Any] = {
            "ok": True,
            "exit_code": 0,
            "stdout": "notepad running",
            "stderr": "",
        }

    def run(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return dict(self.result)


def test_initial_turn_can_run_shell_before_observe() -> None:
    shell_response = _tool_response(
        name="run_shell",
        arguments={"command": "Get-Process notepad"},
        call_id="shell-call",
    )
    observe = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "observe-call",
                            "type": "function",
                            "function": {
                                "name": "observe_desktop",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            }
        ]
    }
    _, clients = _build_clients([shell_response, observe])
    runner = _RecordingShellRunner()
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        shell_runner=runner,
    )

    admission = actor.begin_turn("Check if notepad is running")

    assert admission.kind == "desktop"
    assert len(runner.calls) == 1
    assert runner.calls[0]["command"] == "Get-Process notepad"
    assert runner.calls[0]["shell"] == "powershell"
    assert len(clients[0].requests) == 2
    shell_result_messages = [
        m
        for m in clients[0].requests[1]["messages"]
        if m.get("role") == "tool"
    ]
    assert len(shell_result_messages) == 1
    tool_result = json.loads(shell_result_messages[0]["content"])
    assert tool_result["stdout"] == "notepad running"


def test_visual_turn_can_run_shell_before_gui_action() -> None:
    _, clients = _build_clients(
        [
            _tool_response(
                name="run_shell",
                arguments={"command": "tasklist", "shell": "cmd"},
                call_id="visual-shell",
            ),
            _tool_response(
                arguments={
                    "text": "hello",
                    "local_intent": "enter the requested text once",
                },
                call_id="gui-call",
            ),
        ]
    )
    runner = _RecordingShellRunner()
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        shell_runner=runner,
    )

    decision = actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert decision.proposal["action"] == "type_text"
    assert len(runner.calls) == 1
    assert runner.calls[0]["command"] == "tasklist"
    assert runner.calls[0]["shell"] == "cmd"
    assert len(clients[0].requests) == 2


def test_shell_tool_is_absent_without_runner() -> None:
    _, clients = _build_clients(_tool_response())
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )
    names = [
        tool["function"]["name"]
        for tool in clients[0].requests[0]["tools"]
    ]
    assert "run_shell" not in names


class _RecordingCaptureRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *, path: str, window: bool) -> dict[str, Any]:
        self.calls.append({"path": path, "window": window})
        return {
            "format": "capture-screen/1",
            "ok": True,
            "path": path,
            "width": 1920,
            "height": 1080,
            "size_bytes": 1234,
            "scope": "window" if window else "screen",
        }


class _RecordingWindowsReader:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> dict[str, Any]:
        self.calls += 1
        return {
            "format": "windows-list/1",
            "ok": True,
            "windows": [
                {"hwnd": 100, "title": "QQ", "class_name": "QQMainWnd", "pid": 1},
                {"hwnd": 200, "title": "网易云音乐", "class_name": "OrpheusBrowserHost", "pid": 2},
            ],
            "count": 2,
        }


class _RecordingPasteRunner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, *, path: str) -> dict[str, Any]:
        self.calls.append(path)
        return {
            "format": "paste-image/1",
            "ok": True,
            "path": path,
            "width": 1920,
            "height": 1080,
            "via": "clipboard_dib",
        }


def test_visual_turn_can_capture_screen_before_gui_action() -> None:
    _, clients = _build_clients(
        [
            _tool_response(
                name="capture_screen",
                arguments={
                    "path": r"C:\tmp\shot.png",
                    "window": True,
                },
                call_id="capture-call",
            ),
            _tool_response(
                arguments={
                    "text": "hello",
                    "local_intent": "enter the requested text once",
                },
                call_id="gui-call",
            ),
        ]
    )
    runner = _RecordingCaptureRunner()
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        capture_runner=runner,
    )

    decision = actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert decision.proposal["action"] == "type_text"
    assert runner.calls == [{"path": r"C:\tmp\shot.png", "window": True}]
    assert len(clients[0].requests) == 2
    capture_result_messages = [
        m
        for m in clients[0].requests[1]["messages"]
        if m.get("role") == "tool"
    ]
    assert len(capture_result_messages) == 1
    tool_result = json.loads(capture_result_messages[0]["content"])
    assert tool_result["ok"] is True
    assert tool_result["path"] == r"C:\tmp\shot.png"


def test_visual_turn_can_list_windows_before_gui_action() -> None:
    _, clients = _build_clients(
        [
            _tool_response(
                name="list_windows",
                arguments={},
                call_id="windows-call",
            ),
            _tool_response(
                arguments={
                    "text": "hello",
                    "local_intent": "enter the requested text once",
                },
                call_id="gui-call",
            ),
        ]
    )
    reader = _RecordingWindowsReader()
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        windows_reader=reader,
    )

    decision = actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert decision.proposal["action"] == "type_text"
    assert reader.calls == 1
    assert len(clients[0].requests) == 2
    result_messages = [
        m
        for m in clients[0].requests[1]["messages"]
        if m.get("role") == "tool"
    ]
    tool_result = json.loads(result_messages[0]["content"])
    assert tool_result["count"] == 2
    assert tool_result["windows"][0]["title"] == "QQ"


def test_visual_turn_can_paste_image_before_gui_action() -> None:
    _, clients = _build_clients(
        [
            _tool_response(
                name="paste_image_file",
                arguments={"path": r"C:\tmp\shot.png"},
                call_id="paste-call",
            ),
            _tool_response(
                arguments={
                    "text": "hello",
                    "local_intent": "enter the requested text once",
                },
                call_id="gui-call",
            ),
        ]
    )
    runner = _RecordingPasteRunner()
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        paste_runner=runner,
    )

    decision = actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert decision.proposal["action"] == "type_text"
    assert runner.calls == [r"C:\tmp\shot.png"]
    assert len(clients[0].requests) == 2
    result_messages = [
        m
        for m in clients[0].requests[1]["messages"]
        if m.get("role") == "tool"
    ]
    tool_result = json.loads(result_messages[0]["content"])
    assert tool_result["ok"] is True


def test_capture_window_tools_are_absent_without_runners() -> None:
    _, clients = _build_clients(_tool_response())
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )
    names = [
        tool["function"]["name"]
        for tool in clients[0].requests[0]["tools"]
    ]
    assert "capture_screen" not in names
    assert "list_windows" not in names
    assert "paste_image_file" not in names


def test_capture_screen_accepts_omitted_optional_window_field() -> None:
    """The tool schema declares ``window`` optional with default false; the
    runtime validator must not reject a call that omits it (a regression that
    burned 41 turns in a real QQ screenshot run)."""
    _, clients = _build_clients(
        [
            _tool_response(
                name="capture_screen",
                arguments={"path": r"C:\tmp\shot.png"},
                call_id="capture-call",
            ),
            _tool_response(
                arguments={
                    "text": "hello",
                    "local_intent": "enter the requested text once",
                },
                call_id="gui-call",
            ),
        ]
    )
    runner = _RecordingCaptureRunner()
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        capture_runner=runner,
    )

    decision = actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert decision.proposal["action"] == "type_text"
    assert runner.calls == [{"path": r"C:\tmp\shot.png", "window": False}]
    assert len(clients[0].requests) == 2


def test_read_window_text_ignores_extra_arguments() -> None:
    """A no-arg read tool must not reject the model for passing extra keys."""
    _, clients = _build_clients(
        [
            _tool_response(
                name="read_window_text",
                arguments={
                    "unexpected_field": 1,
                    "task_state_update": {"scratchpad": "checking the window"},
                },
            ),
            _tool_response(
                name="click",
                arguments={
                    "image_x": 400,
                    "image_y": 300,
                },
            ),
        ]
    )
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        window_text_reader=lambda _turn: {
            "format": "window-text/1",
            "window_hwnd": 1,
            "element_count": 0,
            "text": "",
        },
    )

    decision = actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert decision.proposal is not None
    assert decision.proposal["kind"] == "desktop_action"
    assert decision.proposal["action"] == "click"
    assert len(clients[0].requests) == 2


def test_owned_utility_tool_tolerates_unknown_fields() -> None:
    """Utility tools ignore surplus keys instead of rejecting the call."""
    from winpilot.vnext.model_roles.visual_actor import _owned_tool_exchange

    response = _tool_response(
        name="list_windows",
        arguments={
            "unexpected": 1,
            "task_state_update": {"scratchpad": "w"},
        },
    )
    exchange = _owned_tool_exchange(
        response,
        "list_windows",
        expected_arguments={},
    )
    assert exchange is not None
    assert exchange[3] == {}
