"""Focused fake-provider tests for the vNext live visual actor."""
from __future__ import annotations

import base64
import json
import threading
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from winpilot.modeling import (
    ModelCallError,
    ModelCallRuntime,
    RetryKind,
    create_openai_client,
)
from winpilot.vnext.actor import (
    ACTOR_PROPOSAL_FORMAT,
    InvalidActorProposal,
    actor_proposal_to_json,
    parse_actor_proposal,
)
from winpilot.vnext.model_roles import (
    FlowFragmentProposer,
    MAX_LIVE_MODEL_JSON_RESPONSE_CHARS,
    MAX_LIVE_MODEL_OUTPUT_TOKENS,
    MAX_LIVE_MODEL_TOTAL_TIMEOUT_S,
    VISUAL_ACTOR_SYSTEM_CONTRACT,
    LiveModelRoleError,
    LiveVisualActor,
)
from winpilot.vnext.runtime import (
    ActorTaskState,
    ActorTaskStateUpdate,
    ActorTurnDecision,
    ActorFrameView,
    ActorMissionView,
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
                "image_path": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {"type": "number", "minimum": 0},
                        "minItems": 2,
                        "maxItems": 2,
                },
                "minItems": 1,
            },
                "keep_holding": {"type": "boolean", "default": False},
                "held_keys": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "uniqueItems": True,
                    "default": [],
                },
            },
            "required": ["image_path"],
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
        ),
        frame=ActorFrameView(
            image_available=True,
            authoritative=True,
            fresh=True,
        ),
        tools=tuple(tools),
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
                    "connector": "github",
                    "tool": "get_issue",
                }
            ],
        }

    def call(
        self,
        *,
        connector: str,
        tool: str,
        arguments_json: str,
        timeout_s: float,
        stop_event: Any | None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "connector": connector,
                "tool": tool,
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
        history=(
            {"role": "user", "content": "Earlier request"},
            {"role": "assistant", "content": "Earlier answer"},
        ),
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
    assert len(actor_request["messages"]) == 5
    assert (
        actor_request["messages"][0]["content"]
        == VISUAL_ACTOR_SYSTEM_CONTRACT
    )
    assert actor_request["messages"][1:4] == [
        {"role": "user", "content": "Earlier request"},
        {"role": "assistant", "content": "Earlier answer"},
        {"role": "user", "content": "Type the requested greeting"},
    ]
    actor_contract = " ".join(VISUAL_ACTOR_SYSTEM_CONTRACT.split())
    assert len(actor_contract.split()) < 100
    assert "current JPEG" in actor_contract
    assert "exact submitted-image pixels" in actor_contract
    assert "Chat and act naturally" in actor_contract
    assert "runtime does not classify or route" in actor_contract
    assert "goal complete with none remaining" in actor_contract
    assert "call reply_to_user" in actor_contract
    assert "external condition no tool can change" in actor_contract
    assert "pointer_held persists" in actor_contract
    assert "only authoritative visual state" in actor_contract
    assert actor_request["tool_choice"] == "auto"
    assert "parallel_tool_calls" not in actor_request
    assert "n" not in actor_request
    assert "max_tokens" not in actor_request
    assert actor_request["max_completion_tokens"] == 4096
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
        "reply_to_user",
        "blocked",
    }
    descriptions = {
        tool["function"]["name"]: tool["function"]["description"]
        for tool in actor_request["tools"]
    }
    assert "once" in descriptions["click"]
    assert "Double-click" in descriptions["double_click"]
    assert "through ordered points text" in descriptions["drag"]
    assert "amount < 0 scrolls" in descriptions["scroll"]
    assert "120 is one ordinary notch" in descriptions["scroll"]
    assert "selected text is replaced" in descriptions["type_text"]
    assert "one key or one ordered key chord" in descriptions["press_key"]
    assert "repeat" in descriptions["press_key"]
    assert "volumedown" in descriptions["press_key"]
    assert "relative to the current system level" in descriptions["press_key"]
    assert "PrintScreen" not in descriptions["press_key"]
    assert "Answer the user" in descriptions["reply_to_user"]
    assert "external blocker" in descriptions["blocked"]
    assert "response_format" not in actor_request
    strict_schemas = [
        tool["function"]["parameters"]
        for tool in actor_request["tools"]
    ]
    for schema in strict_schemas:
        state = schema["properties"]["task_state_update"]
        assert state["default"] == {"scratchpad": None}
        assert state["properties"]["scratchpad"]["default"] is None
        assert state["properties"]["remaining_items"]["default"] is None
        assert "local_intent" not in schema["properties"]
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

    assert actor_request["messages"][4]["content"][0]["type"] == "image_url"
    actor_context = actor_request["messages"][4]["content"][1]["text"]
    actor_context_value = json.loads(actor_context)
    assert actor_context_value["submitted_image_size"] == [640, 480]
    assert "submitted_size" not in actor_context_value
    assert "tools" not in actor_context_value
    assert actor_context_value["mission"]["original_goal"] == (
        "Type the requested greeting"
    )
    assert actor_context_value["task_state"] == {
        "scratchpad": "",
        "remaining_items": [],
    }
    assert "data:image/jpeg;base64," in (
        actor_request["messages"][4]["content"][0]["image_url"]["url"]
    )


def test_visual_actor_can_request_a_fresh_desktop_without_input() -> None:
    class DetachedRunner:
        def run(self, **_kwargs: Any) -> dict[str, Any]:
            return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}

        def run_detached(self, **_kwargs: Any) -> dict[str, Any]:
            return {
                "ok": True,
                "pid": 4242,
                "status_path": "run/beacon.status.json",
            }

    _, clients = _build_clients(
        [
            _tool_response(
                name="run_detached",
                arguments={
                    "script": "blender beacon_scene.blend",
                    "name": "beacon",
                },
                call_id="launch-beacon",
            ),
            _tool_response(
                name="refresh_desktop",
                arguments={"question": "Reconsider after repeated local results"},
                call_id="refresh-after-launch",
            ),
            _tool_response(
                name="refresh_desktop",
                arguments={"question": "Is the launched window ready now?"},
                call_id="refresh-loading-window",
            ),
            _tool_response(call_id="after-refresh"),
        ]
    )
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        shell_runner=DetachedRunner(),
    )
    turn = _actor_turn()
    observe = ActorToolView(
        name="observe",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "question": {"type": "string", "minLength": 1},
            },
            "required": ["question"],
        },
        requires_local_intent=False,
    )

    decision = actor.propose(
        replace(turn, tools=(*turn.tools, observe)),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert dict(decision.proposal) == {
        "format": "actor-proposal/0",
        "kind": "observe",
        "observation_kind": "full",
        "image_region": None,
        "question": "Reconsider after repeated local results",
    }
    tool_names = {
        tool["function"]["name"]
        for tool in clients[0].requests[0]["tools"]
    }
    assert {"refresh_desktop", "run_detached"} <= tool_names

    next_decision = actor.propose(
        replace(turn, tools=(*turn.tools, observe)),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert next_decision.proposal["kind"] == "observe"
    final_decision = actor.propose(
        replace(turn, tools=(*turn.tools, observe)),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert final_decision.proposal["action"] == "type_text"
    messages = clients[0].requests[3]["messages"]
    next_messages = json.dumps(messages, ensure_ascii=False)
    assert "launch-beacon-0" in next_messages
    assert "blender beacon_scene.blend" in next_messages
    assert "run/beacon.status.json" in next_messages
    assert "refresh-after-launch-0" in next_messages
    assert "Reconsider after repeated local results" in next_messages
    assert "refresh-loading-window-0" in next_messages
    assert "Is the launched window ready now?" in next_messages
    observe_result = next(
        json.loads(message["content"])
        for message in messages
        if message.get("role") == "tool"
        and message.get("tool_call_id") == "refresh-after-launch-0"
    )
    assert observe_result == {"desktop_observation": "available"}
    second_observe_result = next(
        json.loads(message["content"])
        for message in messages
        if message.get("role") == "tool"
        and message.get("tool_call_id") == "refresh-loading-window-0"
    )
    assert second_observe_result == {"desktop_observation": "available"}
    assert next_messages.count(_JPEG_B64) == 1


def test_visual_actor_defers_refresh_shape_to_the_single_runtime_parse() -> None:
    _, clients = _build_clients(
        _tool_response(name="refresh_desktop", arguments={})
    )
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    turn = _actor_turn()
    observe = ActorToolView(
        name="observe",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "question": {"type": "string", "minLength": 1},
            },
            "required": ["question"],
        },
        requires_local_intent=False,
    )

    decision = actor.propose(
        replace(turn, tools=(*turn.tools, observe)),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    with pytest.raises(InvalidActorProposal, match="question"):
        parse_actor_proposal(decision.proposal)


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
                    "connector": "github",
                    "tool": "get_issue",
                    "arguments_json": '{"number":7}',
                    "task_state_update": {
                        "scratchpad": "Loaded issue 7 from GitHub.",
                        "remaining_items": ["Enter the issue value"],
                    },
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
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    assert gateway.calls[0] == {
        "connector": "github",
        "tool": "get_issue",
        "arguments_json": '{"number":7}',
    }
    assert actor.task_progress == {
        "scratchpad": "Loaded issue 7 from GitHub.",
        "remaining_items": ["Enter the issue value"],
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
        ),
        ActorPreviousActionView(
            action="click",
            semantic_summary="click",
            input_completed=True,
            visible_effect="",
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
        clients[0].requests[0]["messages"][2]["content"][1]["text"]
    )
    assert context["previous_action"] == previous_action.to_dict()
    assert "scroll_guard" not in context
    assert "focus_guard" not in context


def test_failed_physical_action_gets_one_actionable_model_hint() -> None:
    _, clients = _build_clients(_tool_response())
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    failed = ActorPreviousActionView(
        action="click",
        input_completed=False,
        visible_effect="",
        semantic_summary="click",
        error_kind="physical_attempt_failed",
    )

    actor.propose(
        replace(_actor_turn(), previous_action=failed),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    context = json.loads(
        clients[0].requests[0]["messages"][2]["content"][1]["text"]
    )
    assert context["previous_action"]["error_kind"] == (
        "physical_attempt_failed"
    )
    assert "change arguments or choose another tool" in context[
        "previous_action"
    ]["next_step"]


def test_consecutive_visual_calls_keep_the_run_tool_history_text_only() -> None:
    _, clients = _build_clients([
        _tool_response(call_id="first"),
        _tool_response(call_id="second"),
        _tool_response(call_id="third"),
        _tool_response(call_id="fourth"),
        _tool_response(call_id="fifth"),
        _tool_response(call_id="sixth"),
        _tool_response(call_id="seventh"),
        _tool_response(call_id="eighth"),
        _tool_response(call_id="ninth"),
        _tool_response(call_id="tenth"),
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
    second_messages = clients[0].requests[1]["messages"]
    assert [message["role"] for message in second_messages] == [
        "system", "user", "user", "assistant", "tool", "user",
    ]
    assert isinstance(second_messages[2]["content"], str)
    assert second_messages[3]["tool_calls"][0]["id"] == "first-0"
    assert json.loads(
        second_messages[4]["content"]
    )["previous_action"] == completed.previous_action.to_dict()
    context = json.loads(second_messages[5]["content"][1]["text"])
    assert context["previous_action"] == completed.previous_action.to_dict()
    assert second_messages[5]["content"][0]["image_url"]["url"] == (
        f"data:image/jpeg;base64,{_JPEG_B64}"
    )
    assert json.dumps(second_messages).count(_JPEG_B64) == 1

    actor.propose(
        completed,
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )
    third_messages = clients[0].requests[2]["messages"]
    encoded = json.dumps(third_messages)
    assert "second-0" in encoded
    assert "first-0" in encoded
    assert encoded.count(_JPEG_B64) == 1


def test_drag_retry_keeps_one_native_action_and_the_current_jpeg() -> None:
    drag_arguments = {
        "points": "20,100 100,100 100,20 180,20",
        "local_intent": "follow the visible route",
    }
    _, clients = _build_clients([
        _tool_response(name="drag", arguments=drag_arguments, call_id="first"),
        _tool_response(name="drag", arguments=drag_arguments, call_id="second"),
    ])
    turn = _actor_turn()
    drag = next(tool for tool in turn.tools if tool.name == "drag")
    observe = ActorToolView(
        name="observe",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "question": {"type": "string", "minLength": 1},
            },
            "required": ["question"],
        },
        requires_local_intent=False,
    )
    tools = (drag, observe)
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )

    actor.propose(
        replace(turn, tools=tools),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )
    after_drag = replace(
        turn,
        tools=tools,
        previous_action=ActorPreviousActionView(
            action="drag",
            input_completed=True,
            visible_effect="changed",
        ),
    )
    actor.propose(
        after_drag,
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    messages = clients[0].requests[1]["messages"]
    assert [message["role"] for message in messages] == [
        "system", "user", "user", "assistant", "tool", "user",
    ]
    assert isinstance(messages[2]["content"], str)
    assert messages[3]["tool_calls"][0]["function"] == {
        "name": "drag",
        "arguments": json.dumps(
            {"points": drag_arguments["points"]},
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
    assert json.loads(messages[4]["content"])["previous_action"] == (
        after_drag.previous_action.to_dict()
    )
    assert messages[5]["content"][0]["image_url"]["url"] == (
        f"data:image/jpeg;base64,{_JPEG_B64}"
    )


def test_rejection_feedback_discards_the_unexecuted_native_action() -> None:
    _, clients = _build_clients([
        _tool_response(call_id="rejected"),
        _tool_response(call_id="replacement"),
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
    actor.propose(
        replace(
            turn,
            previous_action=ActorPreviousActionView(
                action="type_text",
                input_completed=True,
                visible_effect="changed",
            ),
        ),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
        rejection_feedback=(
            "tool 'type_text' arguments rejected: schema_mismatch"
        ),
    )

    messages = clients[0].requests[1]["messages"]
    assert [message["role"] for message in messages] == [
        "system", "user", "user", "user",
    ]
    assert "rejected-0" not in json.dumps(messages)
    assert "schema_mismatch" in messages[2]["content"]


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
        (
            _tool_response(count=2),
            "exactly one tool call",
        ),
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
        "button": "left",
        "press_duration_ms": 35,
    }
    tools = clients[0].requests[0]["tools"]
    assert [tool["function"]["name"] for tool in tools] == ["click"]
    assert "keep_holding" not in tools[0]["function"]["parameters"]["properties"]
    assert len(clients[0].requests) == 1


def test_actor_projects_held_drag_as_one_segment_from_physical_cursor() -> None:
    _, clients = _build_clients(
        _tool_response(
            name="drag",
            arguments={
                "points": "250,220 500,400",
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
        "image_path": [[250, 220], [500, 400]],
        "keep_holding": True,
        "button": "left",
        "duration_ms": 400,
    }
    actor_schema = clients[0].requests[0]["tools"][0]["function"][
        "parameters"
    ]
    assert actor_schema["required"] == ["points"]
    assert actor_schema["properties"]["points"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 4096,
        "description": (
            "Ordered submitted-image coordinate pairs written as "
            "x1,y1 x2,y2 ..."
        ),
    }
    assert "button" not in actor_schema["properties"]
    assert actor_schema["properties"]["held_keys"]["default"] == []
    assert "duration_ms" not in actor_schema["properties"]
    assert actor_schema["properties"]["keep_holding"]["default"] is False
    assert len(clients[0].requests) == 1


def test_actor_projects_unheld_drag_path_text_as_ordered_points() -> None:
    _, clients = _build_clients(
        _tool_response(
            name="drag",
            arguments={
                "points": "120,60 250,60 250,220 500,220 500,400",
                "local_intent": "follow the visible corridor turns",
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
        replace(turn, tools=(drag,), pointer_held=False),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert decision.proposal["args"] == {
        "image_path": [
            [120, 60], [250, 60], [250, 220], [500, 220], [500, 400],
        ],
        "button": "left",
        "duration_ms": 400,
    }
    actor_schema = clients[0].requests[0]["tools"][0]["function"][
        "parameters"
    ]
    assert actor_schema["required"] == ["points"]
    assert actor_schema["properties"]["points"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 4096,
        "description": (
            "Ordered submitted-image coordinate pairs written as "
            "x1,y1 x2,y2 ..."
        ),
    }
    assert "button" not in actor_schema["properties"]
    assert actor_schema["properties"]["held_keys"]["default"] == []
    assert "duration_ms" not in actor_schema["properties"]


def test_actor_ignores_malformed_auxiliary_task_state_without_losing_action() -> None:
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

    decision = actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert decision.proposal["args"]["text"] == "hello"
    assert decision.task_state_update == ActorTaskStateUpdate()
    assert sentinel not in repr(decision)


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
            "reply_to_user",
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
                "drag",
                {
                    "points": "not coordinate pairs",
                    "local_intent": "drag once",
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
    assert "drag points" in tool_result["tool_format_error"]


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


def test_failed_tool_activity_keeps_error_and_actionable_output() -> None:
    _, clients = _build_clients([])
    activities: list[dict[str, Any]] = []
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        on_activity=activities.append,
    )

    actor._record_tool_result(
        "run_shell",
        {
            "ok": False,
            "exit_code": 23,
            "error": "command exited with code 23",
            "output": "ACCEPTANCE_CHANGE: reload manifest and retry",
            "next_step": "use the output to choose the next step",
        },
    )

    assert activities == [
        {
            "kind": "tool_result",
            "tool": "run_shell",
            "message": (
                "command exited with code 23\n"
                "ACCEPTANCE_CHANGE: reload manifest and retry\n"
                "Next: use the output to choose the next step"
            ),
            "ok": False,
        }
    ]


def test_visual_turn_can_write_file_then_launch_it_detached() -> None:
    class LocalUtilityRunner(_RecordingShellRunner):
        def __init__(self) -> None:
            super().__init__()
            self.file_calls: list[dict[str, Any]] = []
            self.detached_calls: list[dict[str, Any]] = []

        def write_file(self, **kwargs: Any) -> dict[str, Any]:
            self.file_calls.append(kwargs)
            return {"ok": True, "path": kwargs["path"], "bytes": 42}

        def run_detached(self, **kwargs: Any) -> dict[str, Any]:
            self.detached_calls.append(kwargs)
            return {"ok": True, "pid": 1234}

    source = "import tkinter as tk\nroot = tk.Tk()\nroot.mainloop()\n"
    _, clients = _build_clients([
        _tool_response(
            name="write_file",
            arguments={"path": "go_game.py", "content": source},
            call_id="write-game",
        ),
        _tool_response(
            name="run_detached",
            arguments={"script": "python .\\go_game.py", "name": "go-game"},
            call_id="launch-game",
        ),
        _tool_response(call_id="gui-call"),
    ])
    runner = LocalUtilityRunner()
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
    assert runner.file_calls == [{"path": "go_game.py", "content": source}]
    assert runner.detached_calls == [
        {"script": "python .\\go_game.py", "name": "go-game"}
    ]
    names = {
        tool["function"]["name"]
        for tool in clients[0].requests[0]["tools"]
    }
    assert {"run_shell", "write_file", "run_detached"} <= names


def test_visual_turn_marks_repeated_results_without_hiding_output() -> None:
    responses = [
        _tool_response(
            name="run_shell",
            arguments={"command": f"Get-Content pipeline.py | Select-{index}"},
            call_id=f"shell-call-{index}",
        )
        for index in range(3)
    ]
    responses.append(_tool_response(call_id="gui-after-inspection"))
    _, clients = _build_clients(responses)

    class ChangingShellRunner(_RecordingShellRunner):
        def run(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            output = "same file contents" if len(self.calls) < 3 else "changed"
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": output,
                "stderr": "",
            }

    runner = ChangingShellRunner()
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
    assert len(runner.calls) == 3
    tool_results = [
        json.loads(message["content"])
        for message in clients[0].requests[-1]["messages"]
        if message.get("role") == "tool"
    ]
    assert tool_results[0]["output"] == "same file contents"
    assert tool_results[0]["cwd"] == str(Path.cwd())
    assert tool_results[0]["model_calls_remaining"] == 6
    assert tool_results[1] == {
        "exit_code": 0,
        "completion_scope": "child_process_exit_only",
        "cwd": str(Path.cwd()),
        "physical_input_receipt": False,
        "message": (
            "Executed; this tool returned the same output as the referenced "
            "call."
        ),
        "model_calls_remaining": 5,
        "new_tool_information": False,
        "ok": True,
        "output": "same file contents",
        "repeat_count": 1,
        "result_unchanged": True,
        "same_as_tool_call_id": "shell-call-0-0",
    }
    assert tool_results[2]["output"] == "changed"
    assert tool_results[2]["model_calls_remaining"] == 4


def test_repeated_error_result_stays_explicitly_failed() -> None:
    from winpilot.vnext.model_roles.visual_actor import _repeated_tool_result

    repeated = _repeated_tool_result(
        {
            "error": "provider unavailable",
            "next_step": "choose another tool",
        },
        same_as_tool_call_id="failed-call-1",
        repeat_count=2,
    )

    assert repeated["ok"] is False
    assert repeated["error"] == "provider unavailable"
    assert repeated["next_step"] == "choose another tool"
    assert repeated["same_as_tool_call_id"] == "failed-call-1"
    assert repeated["new_tool_information"] is False


def test_tool_result_identity_ignores_only_full_output_path() -> None:
    from winpilot.vnext.model_roles.visual_actor import _project_tool_result

    seen: dict[tuple[str, str], tuple[str, int]] = {}
    first = {
        "ok": True,
        "exit_code": 0,
        "output": "same output",
        "output_meta": {"total_lines": 4},
        "foreground_window": {"hwnd": 100},
        "full_output_path": r"C:\tmp\first.log",
    }
    second = {
        **first,
        "full_output_path": r"C:\tmp\second.log",
    }

    assert _project_tool_result(
        seen,
        name="run_shell",
        call_id="first-call",
        result=first,
    ) == first
    compact = _project_tool_result(
        seen,
        name="run_shell",
        call_id="second-call",
        result=second,
    )
    assert compact["result_unchanged"] is True
    assert compact["same_as_tool_call_id"] == "first-call"
    assert compact["output"] == "same output"
    assert compact["full_output_path"] == r"C:\tmp\second.log"
    changed_window = _project_tool_result(
        seen,
        name="run_shell",
        call_id="third-call",
        result={
            **second,
            "foreground_window": {"hwnd": 200},
        },
    )
    assert changed_window["foreground_window"] == {"hwnd": 200}
    assert "result_unchanged" not in changed_window
    changed_meta = _project_tool_result(
        seen,
        name="run_shell",
        call_id="fourth-call",
        result={
            **second,
            "output_meta": {"total_lines": 5},
        },
    )
    assert changed_meta["output_meta"] == {"total_lines": 5}
    assert "result_unchanged" not in changed_meta


def test_actor_streams_public_narration_commands_results_and_task_state() -> None:
    shell_response = _tool_response(
        name="run_shell",
        arguments={
            "command": "python -m pytest -q",
            "task_state_update": {
                "scratchpad": "Phase: verify\n- run focused tests\n- revise if needed",
            },
        },
        content="I have implemented the change; now I am checking it.",
        call_id="visible-shell",
    )
    _, clients = _build_clients([
        shell_response,
        _tool_response(call_id="visible-gui"),
    ])
    runner = _RecordingShellRunner()
    activities: list[dict[str, Any]] = []
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        shell_runner=runner,
        on_activity=activities.append,
    )

    decision = actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert decision.proposal["action"] == "type_text"
    assert [item["kind"] for item in activities] == [
        "message",
        "task_state",
        "tool_start",
        "tool_result",
        "tool_start",
    ]
    assert activities[0]["message"] == (
        "I have implemented the change; now I am checking it."
    )
    assert activities[1]["task_state"].startswith("Phase: verify")
    assert activities[2] == {
        "kind": "tool_start",
        "tool": "run_shell",
        "message": "python -m pytest -q",
        "command": "python -m pytest -q",
        "arguments": {"command": "python -m pytest -q"},
    }
    assert activities[3]["ok"] is True
    assert activities[3]["message"] == "notepad running"
    tools = clients[0].requests[0]["tools"]
    shell_schema = next(
        tool["function"]["parameters"]
        for tool in tools
        if tool["function"]["name"] == "run_shell"
    )
    assert "task_state_update" not in shell_schema["properties"]
    assert shell_schema["properties"]["cwd"]["minLength"] == 1
    assert "maximum" not in shell_schema["properties"]["timeout_s"]
    shell_description = next(
        tool["function"]["description"]
        for tool in tools
        if tool["function"]["name"] == "run_shell"
    )
    assert "never provide a physical mouse or keyboard receipt" in (
        shell_description
    )
    assert "directory changes never persist" in shell_description


def test_visual_turn_can_run_shell_before_gui_action() -> None:
    _, clients = _build_clients(
        [
            _tool_response(
                name="run_shell",
                arguments={
                    "command": "tasklist",
                    "shell": "cmd",
                    "cwd": str(Path.cwd()),
                    "task_state_update": {
                        "scratchpad": "Phase: inspect processes",
                    },
                },
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
    assert decision.task_state_update.scratchpad == "Phase: inspect processes"
    assert len(runner.calls) == 1
    assert runner.calls[0]["command"] == "tasklist"
    assert runner.calls[0]["shell"] == "cmd"
    assert runner.calls[0]["cwd"] == str(Path.cwd())
    assert len(clients[0].requests) == 2


def test_visual_turn_executes_independent_query_batch_in_parallel_and_order() -> None:
    batch = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"visual-shell-{index}",
                        "type": "function",
                        "function": {
                            "name": "run_shell",
                            "arguments": json.dumps(
                                {"command": f"inspect {index}"},
                                separators=(",", ":"),
                            ),
                        },
                    }
                    for index in range(4)
                ],
            },
        }],
    }
    _, clients = _build_clients([batch, _tool_response(call_id="gui-call")])

    class ParallelShellRunner:
        def __init__(self) -> None:
            self.barrier = threading.Barrier(4)
            self.lock = threading.Lock()
            self.active = 0
            self.max_active = 0

        def run(self, **kwargs: Any) -> dict[str, Any]:
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                self.barrier.wait(timeout=1)
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": kwargs["command"],
                    "stderr": "",
                }
            finally:
                with self.lock:
                    self.active -= 1

    runner = ParallelShellRunner()
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
    assert runner.max_active == 4
    assert len(clients[0].requests) == 2
    messages = clients[0].requests[1]["messages"]
    batch_assistant = next(
        message
        for message in messages
        if len(message.get("tool_calls", ())) == 4
    )
    assert [
        call["id"] for call in batch_assistant["tool_calls"]
    ] == [f"visual-shell-{index}" for index in range(4)]
    batch_results = [
        message
        for message in messages
        if message.get("role") == "tool"
        and str(message.get("tool_call_id", "")).startswith("visual-shell-")
    ]
    assert [message["tool_call_id"] for message in batch_results] == [
        f"visual-shell-{index}" for index in range(4)
    ]
    assert [json.loads(message["content"])["output"] for message in batch_results] == [
        f"inspect {index}" for index in range(4)
    ]


def test_visual_batch_with_gui_uses_existing_single_call_failure_path() -> None:
    invalid_batch = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "would-have-run",
                        "type": "function",
                        "function": {
                            "name": "run_shell",
                            "arguments": '{"command":"Write-Output changed"}',
                        },
                    },
                    {
                        "id": "gui-mixed-in",
                        "type": "function",
                        "function": {
                            "name": "type_text",
                            "arguments": json.dumps({
                                "text": "hello",
                                "local_intent": "enter the greeting",
                            }),
                        },
                    },
                ],
            },
        }],
    }
    _, clients = _build_clients(invalid_batch)
    runner = _RecordingShellRunner()
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        shell_runner=runner,
    )

    with pytest.raises(InvalidActorProposal, match="exactly one tool call"):
        actor.propose(
            _actor_turn(),
            image_b64=_JPEG_B64,
            image_media_type="image/jpeg",
            submitted_size=(640, 480),
        )

    assert runner.calls == []
    assert len(clients[0].requests) == 1


def test_visual_turn_marks_repeated_tool_output_without_hiding_it() -> None:
    _, clients = _build_clients(
        [
            _tool_response(
                name="run_shell",
                arguments={"command": f"inspect-{index}"},
                call_id=f"visual-shell-{index}",
            )
            for index in range(3)
        ]
        + [_tool_response(call_id="gui-call")]
    )

    class ChangingShellRunner(_RecordingShellRunner):
        def run(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            output = "same output" if len(self.calls) < 3 else "changed output"
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": output,
                "stderr": "",
            }

    runner = ChangingShellRunner()
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
    assert [call["command"] for call in runner.calls] == [
        "inspect-0",
        "inspect-1",
        "inspect-2",
    ]
    tool_results = [
        json.loads(message["content"])
        for message in clients[0].requests[-1]["messages"]
        if message.get("role") == "tool"
    ]
    assert tool_results[0]["output"] == "same output"
    assert tool_results[1]["result_unchanged"] is True
    assert tool_results[1]["new_tool_information"] is False
    assert tool_results[1]["same_as_tool_call_id"] == "visual-shell-0-0"
    assert tool_results[1]["output"] == "same output"
    assert tool_results[1]["model_calls_remaining"] == 5
    assert tool_results[2]["output"] == "changed output"


def test_visual_turn_keeps_repeated_window_text_body() -> None:
    _, clients = _build_clients(
        [
            _tool_response(
                name="read_window_text",
                arguments={},
                call_id=f"window-text-{index}",
            )
            for index in range(3)
        ]
        + [_tool_response(call_id="gui-call")]
    )

    class ChangingWindowTextReader:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, _turn: ActorTurnInput) -> dict[str, Any]:
            self.calls += 1
            text = "x" * 6000 if self.calls < 3 else "changed text"
            return {
                "format": "window-text/1",
                "window_hwnd": 1,
                "element_count": 1,
                "text": text,
            }

    reader = ChangingWindowTextReader()
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        window_text_reader=reader,
    )

    actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert reader.calls == 3
    tool_results = [
        json.loads(message["content"])
        for message in clients[0].requests[-1]["messages"]
        if message.get("role") == "tool"
    ]
    assert tool_results[0]["text"] == "x" * 6000
    assert tool_results[1]["result_unchanged"] is True
    assert tool_results[1]["new_tool_information"] is False
    assert tool_results[1]["text"] == "x" * 6000
    assert tool_results[2]["text"] == "changed text"


def test_next_visual_turn_keeps_thread_context_and_gui_action() -> None:
    shell_call = _tool_response(
        name="run_shell",
        arguments={
            "command": "unique-command",
            "task_state_update": {"scratchpad": "inspect once"},
        },
        call_id="consumed-shell",
    )
    gui_call = _tool_response(
        arguments={
            "text": "hello",
            "local_intent": "enter the greeting once",
            "task_state_update": {"scratchpad": "latest state"},
        },
        content="old local narration",
        call_id="final-gui",
    )
    gui_call["choices"][0]["message"]["reasoning_content"] = (
        "private old reasoning"
    )
    _, clients = _build_clients(
        [shell_call, gui_call, _tool_response(call_id="second-gui")]
    )
    runner = _RecordingShellRunner()
    runner.result = {
        "ok": True,
        "exit_code": 0,
        "stdout": "UNIQUE-DIAGNOSTIC-BODY",
        "stderr": "",
    }
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        shell_runner=runner,
        history=(
            {
                "role": "user",
                "content": "Create the three files in the requested folder.",
            },
            {
                "role": "assistant",
                "content": (
                    r"Created C:\delivery\project.xlsx, "
                    r"C:\delivery\report.docx, and C:\delivery\briefing.pptx."
                ),
            },
        ),
    )
    turn = _actor_turn()

    actor.propose(
        turn,
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )
    previous_action = ActorPreviousActionView(
        action="type_text",
        input_completed=True,
        visible_effect="changed",
    )
    actor.propose(
        replace(
            turn,
            task_state=ActorTaskState("latest state"),
            previous_action=previous_action,
        ),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    messages = clients[0].requests[-1]["messages"]
    encoded = json.dumps(messages, ensure_ascii=False)
    assert [message["role"] for message in messages] == [
        "system", "user", "assistant", "user", "user", "assistant", "tool",
        "assistant", "tool", "user",
    ]
    assert messages[1:4] == [
        {
            "role": "user",
            "content": "Create the three files in the requested folder.",
        },
        {
            "role": "assistant",
            "content": (
                r"Created C:\delivery\project.xlsx, "
                r"C:\delivery\report.docx, and C:\delivery\briefing.pptx."
            ),
        },
        {"role": "user", "content": turn.mission.original_goal},
    ]
    assert "unique-command" in encoded
    assert "UNIQUE-DIAGNOSTIC-BODY" in encoded
    assert "consumed-shell-0" in encoded
    assert "old local narration" not in encoded
    assert "private old reasoning" not in encoded
    assert "final-gui-0" in encoded
    assert json.loads(
        messages[7]["tool_calls"][0]["function"]["arguments"]
    ) == {"text": "hello"}
    assert _JPEG_B64 in encoded
    assert encoded.count(_JPEG_B64) == 1
    current_context = json.loads(messages[-1]["content"][1]["text"])
    assert current_context["mission"]["original_goal"] == (
        turn.mission.original_goal
    )
    assert current_context["task_state"] == {
        "scratchpad": "latest state",
        "remaining_items": [],
    }
    assert current_context["previous_action"] == previous_action.to_dict()


def test_failed_shell_result_stays_in_band_with_an_actionable_next_step() -> None:
    _, clients = _build_clients(
        [
            _tool_response(
                name="run_shell",
                arguments={"command": "missing-command"},
                call_id="failed-shell",
            ),
            _tool_response(call_id="gui-call"),
        ]
    )
    runner = _RecordingShellRunner()
    runner.result = {
        "ok": False,
        "exit_code": 1,
        "output": "",
        "stdout": "",
        "stderr": "command was not found",
    }
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
    tool_messages = [
        message
        for message in clients[0].requests[1]["messages"]
        if message.get("role") == "tool"
    ]
    result = json.loads(tool_messages[-1]["content"])
    assert result["output"] == "command was not found"
    assert "stderr" not in result
    assert result["next_step"] == (
        "use the output to correct the command or choose another tool"
    )


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


def test_visual_turn_recovers_after_one_provider_rejection() -> None:
    rejection = {
        "choices": [{
            "finish_reason": "content_filter",
            "message": {
                "role": "assistant",
                "content": "provider-specific refusal prose",
                "tool_calls": [],
            },
        }],
    }
    _, clients = _build_clients([rejection, _tool_response()])
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    detail = (
        "provider rejected model response: "
        "finish_reason=content_filter"
    )

    with pytest.raises(InvalidActorProposal, match=detail):
        actor.propose(
            _actor_turn(),
            image_b64=_JPEG_B64,
            image_media_type="image/jpeg",
            submitted_size=(640, 480),
        )

    decision = actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
        rejection_feedback=detail,
    )

    assert decision.proposal["action"] == "type_text"
    assert len(clients[0].requests) == 2


def test_visual_turn_keeps_retrying_after_provider_rejections() -> None:
    first = {
        "choices": [{
            "finish_reason": "content_filter",
            "message": {
                "role": "assistant",
                "content": "first refusal text",
                "tool_calls": [],
            },
        }],
    }
    second = deepcopy(first)
    second["choices"][0]["finish_reason"] = "safety"
    second["choices"][0]["message"]["content"] = "different refusal text"
    _, clients = _build_clients([first, second])
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    detail = (
        "provider rejected model response: "
        "finish_reason=content_filter"
    )

    with pytest.raises(InvalidActorProposal, match=detail):
        actor.propose(
            _actor_turn(),
            image_b64=_JPEG_B64,
            image_media_type="image/jpeg",
            submitted_size=(640, 480),
        )

    second_detail = (
        "provider rejected model response: "
        "finish_reason=safety"
    )
    with pytest.raises(InvalidActorProposal, match=second_detail):
        actor.propose(
            _actor_turn(),
            image_b64=_JPEG_B64,
            image_media_type="image/jpeg",
            submitted_size=(640, 480),
            rejection_feedback=detail,
        )
    assert len(clients[0].requests) == 2


def test_visual_turn_keeps_model_selected_dependent_shell_chain() -> None:
    _, clients = _build_clients(
        [
            *[
                _tool_response(
                    name="run_shell",
                    arguments={"command": f"tasklist {index}"},
                    call_id=f"visual-shell-{index}",
                )
                for index in range(5)
            ],
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
    assert len(runner.calls) == 5
    assert [call["command"] for call in runner.calls] == [
        "tasklist 0",
        "tasklist 1",
        "tasklist 2",
        "tasklist 3",
        "tasklist 4",
    ]
    names = [
        tool["function"]["name"]
        for tool in clients[0].requests[5]["tools"]
    ]
    assert "run_shell" in names
    assert "type_text" in names


def test_visual_turn_executes_model_selected_shell_batch_in_parallel() -> None:
    _, clients = _build_clients(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": f"initial-shell-{index}",
                                    "type": "function",
                                    "function": {
                                        "name": "run_shell",
                                        "arguments": json.dumps(
                                            {
                                                "command": f"tasklist {index}",
                                            },
                                            separators=(",", ":"),
                                        ),
                                    },
                                }
                                for index in range(5)
                            ],
                        }
                    }
                ]
            },
            _tool_response(call_id="gui-after-batch"),
        ]
    )
    class ParallelShellRunner:
        def __init__(self) -> None:
            self.barrier = threading.Barrier(5)
            self.lock = threading.Lock()
            self.active = 0
            self.max_active = 0

        def run(self, **kwargs: Any) -> dict[str, Any]:
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                self.barrier.wait(timeout=1)
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": kwargs["command"],
                    "stderr": "",
                }
            finally:
                with self.lock:
                    self.active -= 1

    runner = ParallelShellRunner()
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
    assert runner.max_active == 5
    assert len(clients[0].requests) == 2
    tool_results = [
        json.loads(message["content"])["output"]
        for message in clients[0].requests[1]["messages"]
        if message.get("role") == "tool"
    ]
    assert tool_results == [f"tasklist {index}" for index in range(5)]
    names = [
        tool["function"]["name"]
        for tool in clients[0].requests[1]["tools"]
    ]
    assert "run_shell" in names


def test_propose_injects_rejection_feedback_into_native_context() -> None:
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
        rejection_feedback=(
            "tool arguments rejected: tool=hotkey code=schema_mismatch "
            "keyword=enum path=('keys', 0)"
        ),
    )

    messages = clients[0].requests[0]["messages"]
    assert any(
        m.get("role") == "user"
        and "tool arguments rejected" in str(m.get("content", ""))
        for m in messages
    )
    assert any(
        m.get("role") == "user"
        and "Runtime feedback from the previous attempt" in str(
            m.get("content", "")
        )
        and "return one valid tool call or an independent tool batch"
        in str(m.get("content", ""))
        for m in messages
    )


class _RecordingCurrentViewRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *, image_b64: str, decision_id: str) -> dict[str, Any]:
        self.calls.append({"image_b64": image_b64, "decision_id": decision_id})
        return {
            "format": "copy-image/1",
            "ok": True,
            "source": "current_model_view",
            "width": 640,
            "height": 480,
            "size_bytes": 1234,
            "_replay_frame": "run/view_frame_decision.jpg",
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
                {
                    "hwnd": 100,
                    "title": "QQ",
                    "class_name": "QQMainWnd",
                    "pid": 1,
                    "process_name": "QQ.exe",
                    "rect": [0, 0, 800, 600],
                    "foreground": True,
                    "visible": True,
                    "minimized": False,
                },
                {"hwnd": 200, "title": "网易云音乐", "class_name": "OrpheusBrowserHost", "pid": 2},
            ],
            "count": 2,
            "background_applications": [
                {
                    "process_name": "QQ.exe",
                    "title": "QQ",
                    "state": "background",
                }
            ],
            "background_count": 1,
        }


def test_visual_turn_can_copy_current_view_before_gui_action() -> None:
    _, clients = _build_clients(
        [
            _tool_response(
                name="copy_screen_to_clipboard",
                arguments={
                    "task_state_update": {
                        "scratchpad": "music selected; image is on clipboard",
                    },
                },
                call_id="copy-call",
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
    runner = _RecordingCurrentViewRunner()
    activities: list[dict[str, Any]] = []
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        copy_current_view_runner=runner,
        on_activity=activities.append,
    )

    decision = actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert decision.proposal["action"] == "type_text"
    assert decision.task_state_update.scratchpad == (
        "music selected; image is on clipboard"
    )
    assert runner.calls[0]["image_b64"] == _JPEG_B64
    assert runner.calls[0]["decision_id"].startswith("visual-actor-")
    assert len(clients[0].requests) == 2
    copy_result_messages = [
        m
        for m in clients[0].requests[1]["messages"]
        if m.get("role") == "tool"
    ]
    assert len(copy_result_messages) == 1
    tool_result = json.loads(copy_result_messages[0]["content"])
    assert tool_result["ok"] is True
    assert tool_result["source"] == "current_model_view"
    assert "_replay_frame" not in tool_result
    result_activity = next(
        item for item in activities
        if item.get("kind") == "tool_result"
        and item.get("tool") == "copy_current_view"
    )
    assert result_activity["arguments"] == {
        "replay_frame": "run/view_frame_decision.jpg",
    }


def test_copy_current_view_result_remains_in_this_run_without_old_image() -> None:
    replay_path = "run/view_frame_decision.jpg"
    _, clients = _build_clients([
        _tool_response(
            name="copy_screen_to_clipboard",
            arguments={},
            call_id="copy-call",
        ),
        _tool_response(call_id="first-gui"),
        _tool_response(call_id="second-gui"),
    ])
    runner = _RecordingCurrentViewRunner()
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        copy_current_view_runner=runner,
    )
    turn = _actor_turn()

    actor.propose(
        turn,
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )
    actor.propose(
        replace(
            turn,
            previous_action=ActorPreviousActionView(
                action="type_text",
                input_completed=True,
                visible_effect="changed",
            ),
        ),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    next_messages = clients[0].requests[2]["messages"]
    assert [message["role"] for message in next_messages] == [
        "system", "user", "user", "assistant", "tool", "assistant", "tool", "user",
    ]
    encoded = json.dumps(next_messages)
    assert "copy_screen_to_clipboard" in encoded
    assert "current_model_view" in encoded
    assert replay_path not in json.dumps(next_messages)
    assert encoded.count(_JPEG_B64) == 1


def test_visual_utility_history_remains_available_across_frames() -> None:
    utility_calls = [
        _tool_response(
            name="run_shell",
            arguments={"command": f"inspect-{index}"},
            call_id=f"shell-{index}",
        )
        for index in range(5)
    ]
    _, clients = _build_clients([
        *utility_calls,
        _tool_response(call_id="first-gui"),
        _tool_response(call_id="second-gui"),
    ])
    runner = _RecordingShellRunner()
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        shell_runner=runner,
    )
    turn = _actor_turn()

    actor.propose(
        turn,
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )
    actor.propose(
        replace(
            turn,
            previous_action=ActorPreviousActionView(
                action="type_text",
                input_completed=True,
                visible_effect="changed",
            ),
        ),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    encoded = json.dumps(clients[0].requests[6]["messages"])
    for index in range(5):
        assert f"inspect-{index}" in encoded
        assert f"shell-{index}-0" in encoded
    assert encoded.count(_JPEG_B64) == 1


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
    assert tool_result["windows"][0]["process_name"] == "QQ.exe"

    first_context = json.loads(
        clients[0].requests[0]["messages"][-1]["content"][1]["text"]
    )
    assert first_context["background_applications"] == [{
        "process_name": "QQ.exe",
        "title": "QQ",
        "state": "background",
    }]
    assert tool_result["windows"][0]["rect"] == [0, 0, 800, 600]
    assert tool_result["windows"][0]["foreground"] is True


def test_background_inventory_failure_never_blocks_visual_action() -> None:
    _, clients = _build_clients(_tool_response())
    calls = 0

    def unavailable_inventory() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise OSError("window enumeration unavailable")

    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        windows_reader=unavailable_inventory,
    )

    decision = actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    assert decision.proposal["action"] == "type_text"
    assert calls == 1
    context = json.loads(
        clients[0].requests[0]["messages"][-1]["content"][1]["text"]
    )
    assert "background_applications" not in context


def test_optional_desktop_utility_tools_are_absent_without_runners() -> None:
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
    assert "copy_screen_to_clipboard" not in names
    assert "capture_screen" not in names
    assert "list_windows" not in names
    assert "paste_image_file" not in names


def test_online_image_tool_has_no_path_or_recapture_surface() -> None:
    _, clients = _build_clients(_tool_response())
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        copy_current_view_runner=_RecordingCurrentViewRunner(),
    )
    actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    tools = {
        tool["function"]["name"]: tool["function"]
        for tool in clients[0].requests[0]["tools"]
    }
    assert "copy_screen_to_clipboard" in tools
    assert "capture_screen" not in tools
    assert "paste_image_file" not in tools
    assert set(tools["copy_screen_to_clipboard"]["parameters"]["properties"]) == {
        "task_state_update",
    }


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
