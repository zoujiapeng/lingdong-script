"""Focused fake-provider tests for the two isolated vNext live model roles."""
from __future__ import annotations

import base64
import hashlib
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
from winpilot.vnext.mission import EvidencePolicy, EvidenceRef
from winpilot.vnext.actor import InvalidActorProposal
from winpilot.vnext.model_roles import (
    MAX_LIVE_MODEL_JSON_RESPONSE_CHARS,
    MAX_LIVE_MODEL_OUTPUT_TOKENS,
    MAX_LIVE_MODEL_TOTAL_TIMEOUT_S,
    VISUAL_ACTOR_SYSTEM_CONTRACT,
    VISUAL_VERIFIER_SYSTEM_CONTRACT,
    LiveModelRoleError,
    LiveVisualActor,
    LiveVisualObligationVerifier,
)
from winpilot.vnext.runtime import (
    ActorFrameView,
    ActorMissionView,
    ActorObligationView,
    ActorToolView,
    ActorTurnInput,
    RuntimeFrame,
    RuntimeVerifierTurn,
)
from winpilot.vnext.verification import (
    ObligationVerificationRequest,
    VerifierAssessment,
    forbidden_effect_condition,
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


def _verifier_turn() -> RuntimeVerifierTurn:
    frame_id = 7
    evidence = EvidenceRef(
        id="evidence-current-frame",
        mission_id="mission-secret-id",
        run_id="run-secret-id",
        obligation_id="obligation-secret-id",
        kind="current_frame",
        trust="provider_receipt",
        payload_hash="1" * 64,
        frame_id=frame_id,
    )
    request = ObligationVerificationRequest(
        mission_id=evidence.mission_id,
        run_id=evidence.run_id,
        mission_revision=4,
        obligation_id=evidence.obligation_id or "",
        claim_fingerprint="2" * 64,
        desired_state="The greeting is visible",
        acceptance_criteria=("The field contains hello",),
        forbidden_effects=("No duplicate text",),
        evidence_policy=EvidencePolicy(
            required_evidence_kinds=("current_frame",),
            preferred_evidence_kinds=(),
            allow_independent_visual_verifier=True,
            max_verification_attempts=1,
            uncertain_policy="blocked",
        ),
        evidence_refs=(evidence,),
    )
    frame = RuntimeFrame(
        frame_id=frame_id,
        epoch=3,
        hwnd=91234,
        region=(100, 200, 640, 480),
        scope="target_window",
        submitted_size=(640, 480),
        image_b64=_JPEG_B64,
        image_sha256=hashlib.sha256(
            _JPEG_B64.encode("ascii")
        ).hexdigest(),
        evidence_refs=(evidence.id,),
    )
    return RuntimeVerifierTurn(
        request=request,
        frame=frame,
        completed_action_summaries=("click", "double_click"),
    )


def _tool_response(
    name: str = "type_text",
    arguments: dict[str, Any] | str | None = None,
    *,
    content: Any = None,
    count: int = 1,
) -> dict[str, Any]:
    if arguments is None:
        arguments = {
            "text": "hello",
            "local_intent": "enter the requested greeting once",
        }
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
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": encoded,
                            },
                        }
                        for _ in range(count)
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


def _verifier_response(
    *,
    with_tool: bool = False,
    confidence: Any = None,
    verified: Any = True,
    condition_results: Any = None,
) -> dict[str, Any]:
    if condition_results is None:
        condition_results = ["satisfied", "satisfied"]
    assessment = {
        "format": "verifier-assessment/0",
        "verified": verified,
        "condition_results": condition_results,
        "confidence": confidence,
        "concise_reason": "The current frame visibly satisfies the criteria.",
    }
    message: dict[str, Any] = {
        "role": "assistant",
        "content": json.dumps(assessment, separators=(",", ":")),
    }
    if with_tool:
        message["tool_calls"] = [
            {
                "type": "function",
                "function": {"name": "click", "arguments": "{}"},
            }
        ]
    return {"choices": [{"message": message}]}


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
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


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


def test_roles_use_independent_zero_retry_clients_and_fresh_contexts() -> None:
    factory_calls, clients = _build_clients(
        _tool_response(),
        _verifier_response(),
    )
    actor_attempts = []
    verifier_attempts = []
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
        on_attempt=actor_attempts.append,
    )
    verifier = LiveVisualObligationVerifier(
        client=clients[1],
        model="vision-model",
        runtime=ModelCallRuntime(),
        on_attempt=verifier_attempts.append,
    )

    proposal = actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )
    assessment = verifier.assess(_verifier_turn())

    assert proposal == {
        "action": "type_text",
        "args": {"text": "hello"},
        "format": "actor-proposal/0",
        "kind": "desktop_action",
        "local_intent": "enter the requested greeting once",
    }
    assert isinstance(assessment, VerifierAssessment)
    assert assessment.verified is True
    assert assessment.satisfied_conditions == (
        "The field contains hello",
        forbidden_effect_condition("No duplicate text"),
    )
    assert assessment.missing_conditions == ()
    assert assessment.contradictions == ()
    assert clients[0] is not clients[1]
    assert [call["max_retries"] for call in factory_calls] == [0, 0]
    assert len(clients[0].requests) == len(clients[1].requests) == 1
    assert [attempt.kind for attempt in actor_attempts] == [
        RetryKind.INITIAL_REQUEST
    ]
    assert [attempt.kind for attempt in verifier_attempts] == [
        RetryKind.INITIAL_REQUEST
    ]

    actor_request = clients[0].requests[0]
    verifier_request = clients[1].requests[0]
    assert len(actor_request["messages"]) == 2
    assert len(verifier_request["messages"]) == 2
    assert (
        actor_request["messages"][0]["content"]
        == VISUAL_ACTOR_SYSTEM_CONTRACT
    )
    assert "Completed-action summaries are authoritative" in " ".join(
        VISUAL_ACTOR_SYSTEM_CONTRACT.split()
    )
    assert (
        verifier_request["messages"][0]["content"]
        == VISUAL_VERIFIER_SYSTEM_CONTRACT
    )
    assert (
        actor_request["messages"][0]["content"]
        != verifier_request["messages"][0]["content"]
    )
    assert actor_request["tool_choice"] == "auto"
    assert "parallel_tool_calls" not in actor_request
    assert "n" not in actor_request
    assert "max_tokens" not in actor_request
    assert actor_request["max_completion_tokens"] == 768
    assert len(actor_request["tools"]) == 7
    assert {
        tool["function"]["name"] for tool in actor_request["tools"]
    } == {
        "click",
        "double_click",
        "scroll",
        "type_text",
        "hotkey",
        "claim_obligation_done",
        "blocked",
    }
    descriptions = {
        tool["function"]["name"]: tool["function"]["description"]
        for tool in actor_request["tools"]
    }
    assert "once" in descriptions["click"]
    assert "Double-click" in descriptions["double_click"]
    assert "amount < 0 scrolls down" in descriptions["scroll"]
    assert "amount > 0 scrolls up" in descriptions["scroll"]
    assert "selected text is replaced" in descriptions["type_text"]
    assert "never enters literal text" in descriptions["hotkey"]
    assert "verifier checks it" in descriptions["claim_obligation_done"]
    assert "no supplied action can advance" in descriptions["blocked"]
    assert "response_format" not in actor_request
    assert "tools" not in verifier_request
    assert "tool_choice" not in verifier_request
    assert "parallel_tool_calls" not in verifier_request
    assert verifier_request["response_format"] == {"type": "json_object"}
    assert "max_tokens" not in verifier_request
    assert verifier_request["max_completion_tokens"] == 768
    strict_schemas = [
        tool["function"]["parameters"]
        for tool in actor_request["tools"]
    ]
    verifier_context = json.loads(
        verifier_request["messages"][1]["content"][0]["text"]
    )
    strict_schemas.append(verifier_context["output_schema"])
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
    assert actor_context_value["completed_action_summaries"] == [
        "click",
        "double_click",
    ]
    assert verifier_context["completed_action_summaries"] == [
        "click",
        "double_click",
    ]
    required_conditions = verifier_context["subject"][
        "required_conditions"
    ]
    condition_results_schema = verifier_context["output_schema"][
        "properties"
    ]["condition_results"]
    assert condition_results_schema["items"]["enum"] == [
        "satisfied",
        "missing",
        "contradicted",
    ]
    assert condition_results_schema["minItems"] == len(
        required_conditions
    )
    assert condition_results_schema["maxItems"] == len(
        required_conditions
    )
    verifier_context_text = (
        verifier_request["messages"][1]["content"][0]["text"]
    )


    for forbidden in (
        "mission-secret-id",
        "run-secret-id",
        "obligation-secret-id",
        "evidence-current-frame",
        "91234",
        "1111111111111111",
        "2222222222222222",
    ):
        assert forbidden not in actor_context
        assert forbidden not in verifier_context_text
    assert "current_frame" in verifier_context_text
    assert "provider_receipt" in verifier_context_text
    assert "required_conditions" in verifier_context_text
    assert (
        "forbidden_effect_absent:No duplicate text"
        in verifier_context_text
    )
    assert "data:image/jpeg;base64," in (
        actor_request["messages"][1]["content"][1]["image_url"]["url"]
    )
    assert "data:image/jpeg;base64," in (
        verifier_request["messages"][1]["content"][1]["image_url"]["url"]
    )


@pytest.mark.parametrize(
    "summary",
    ("secret_token", "hotkey_secret_token", "scroll_sideways"),
)
def test_model_turn_contracts_reject_arbitrary_action_summaries(
    summary: str,
) -> None:
    with pytest.raises(ValueError, match="privacy-safe"):
        replace(
            _actor_turn(),
            completed_action_summaries=(summary,),
        )
    with pytest.raises(ValueError, match="privacy-safe"):
        replace(
            _verifier_turn(),
            completed_action_summaries=(summary,),
        )


def test_consecutive_calls_always_build_fresh_two_message_contexts() -> None:
    _, clients = _build_clients(
        _tool_response(),
        _verifier_response(),
    )
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    verifier = LiveVisualObligationVerifier(
        client=clients[1],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    turn = _actor_turn()
    verifier_turn = _verifier_turn()

    actor.propose(
        turn,
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )
    clients[0].requests[0]["messages"].append(
        {"role": "assistant", "content": "private prior reasoning"}
    )
    actor.propose(
        turn,
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )

    verifier.assess(verifier_turn)
    clients[1].requests[0]["messages"].append(
        {"role": "assistant", "content": "private prior assessment"}
    )
    verifier.assess(verifier_turn)

    assert len(clients[0].requests[1]["messages"]) == 2
    assert len(clients[1].requests[1]["messages"]) == 2
    assert "private prior reasoning" not in json.dumps(
        clients[0].requests[1]["messages"]
    )
    assert "private prior assessment" not in json.dumps(
        clients[1].requests[1]["messages"]
    )
    assert clients[0].requests[0]["messages"] is not (
        clients[0].requests[1]["messages"]
    )
    assert clients[1].requests[0]["messages"] is not (
        clients[1].requests[1]["messages"]
    )


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
                "image_x": 4,
                "image_y": 5,
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
    assert actor.propose(
        turn,
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    )["action"] == "click"
    assert [
        tool["function"]["name"]
        for tool in clients[0].requests[0]["tools"]
    ] == ["click"]


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
    assert actor.propose(
        _actor_turn(),
        image_b64=_JPEG_B64,
        image_media_type="image/jpeg",
        submitted_size=(640, 480),
    ) == expected


def test_verifier_has_no_tool_permission_and_requires_its_own_schema() -> None:
    _, clients = _build_clients(
        _verifier_response(with_tool=True),
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "verified": True,
                                "satisfied_conditions": [],
                            }
                        ),
                    }
                }
            ]
        },
    )
    first = LiveVisualObligationVerifier(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    second = LiveVisualObligationVerifier(
        client=clients[1],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    with pytest.raises(LiveModelRoleError, match="no tool permission"):
        first.assess(_verifier_turn())
    with pytest.raises(LiveModelRoleError, match="invalid assessment"):
        second.assess(_verifier_turn())


@pytest.mark.parametrize("role", [None, "user"])
def test_verifier_requires_explicit_assistant_response_role(
    role: str | None,
) -> None:
    response = _verifier_response()
    message = response["choices"][0]["message"]
    if role is None:
        del message["role"]
    else:
        message["role"] = role
    _, clients = _build_clients(response)
    verifier = LiveVisualObligationVerifier(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    with pytest.raises(LiveModelRoleError, match="not assistant"):
        verifier.assess(_verifier_turn())


def test_verifier_rejects_nonnull_confidence_outside_role_schema() -> None:
    _, clients = _build_clients(_verifier_response(confidence=1.0))
    verifier = LiveVisualObligationVerifier(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )

    with pytest.raises(LiveModelRoleError, match="confidence must be null"):
        verifier.assess(_verifier_turn())
    assert len(clients[0].requests) == 1


def test_verifier_maps_ordered_statuses_to_exact_runtime_conditions() -> None:
    _, clients = _build_clients(
        _verifier_response(
            verified=False,
            condition_results=["missing", "contradicted"],
        )
    )
    verifier = LiveVisualObligationVerifier(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )

    assessment = verifier.assess(_verifier_turn())

    assert assessment.verified is False
    assert assessment.satisfied_conditions == ()
    assert assessment.missing_conditions == (
        "The field contains hello",
    )
    assert assessment.contradictions == (
        forbidden_effect_condition("No duplicate text"),
    )


@pytest.mark.parametrize(
    "response, error",
    [
        (
            _verifier_response(condition_results=["satisfied"]),
            "exactly 2 statuses",
        ),
        (
            _verifier_response(
                verified=False,
                condition_results=["satisfied", "unknown"],
            ),
            "must be satisfied, missing, or contradicted",
        ),
        (
            _verifier_response(
                verified=True,
                condition_results=["satisfied", "missing"],
            ),
            "verified must be true exactly",
        ),
        (
            _verifier_response(
                verified=False,
                condition_results=["satisfied", "satisfied"],
            ),
            "verified must be true exactly",
        ),
    ],
)
def test_verifier_fails_closed_on_invalid_ordered_condition_results(
    response: dict[str, Any],
    error: str,
) -> None:
    _, clients = _build_clients(response)
    verifier = LiveVisualObligationVerifier(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )

    with pytest.raises(LiveModelRoleError, match=error):
        verifier.assess(_verifier_turn())


@pytest.mark.parametrize("role_kind", ["actor", "verifier"])
def test_each_role_uses_call_once_transport_policy(role_kind: str) -> None:
    _, clients = _build_clients(TimeoutError("transient transport failure"))
    if role_kind == "actor":
        role = LiveVisualActor(
            client=clients[0],
            model="vision-model",
            runtime=ModelCallRuntime(sleeper=lambda _delay: None),
        )
        invoke = lambda: role.propose(
            _actor_turn(),
            image_b64=_JPEG_B64,
            image_media_type="image/jpeg",
            submitted_size=(640, 480),
        )
    else:
        role = LiveVisualObligationVerifier(
            client=clients[0],
            model="vision-model",
            runtime=ModelCallRuntime(sleeper=lambda _delay: None),
        )
        invoke = lambda: role.assess(_verifier_turn())

    with pytest.raises(ModelCallError) as caught:
        invoke()
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
    _, clients = _build_clients(_tool_response(), _verifier_response())
    with pytest.raises(LiveModelRoleError, match=error):
        LiveVisualActor(
            client=clients[0],
            model="vision-model",
            runtime=ModelCallRuntime(),
            **kwargs,
        )
    with pytest.raises(LiveModelRoleError, match=error):
        LiveVisualObligationVerifier(
            client=clients[1],
            model="vision-model",
            runtime=ModelCallRuntime(),
            **kwargs,
        )


def test_role_json_outputs_have_a_hard_character_bound() -> None:
    oversized = "{" + (
        " " * MAX_LIVE_MODEL_JSON_RESPONSE_CHARS
    ) + "}"
    _, clients = _build_clients(
        _tool_response(arguments=oversized),
        {"choices": [{"message": {"role": "assistant", "content": oversized}}]},
    )
    actor = LiveVisualActor(
        client=clients[0],
        model="vision-model",
        runtime=ModelCallRuntime(),
    )
    verifier = LiveVisualObligationVerifier(
        client=clients[1],
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
    with pytest.raises(LiveModelRoleError, match="exceeds"):
        verifier.assess(_verifier_turn())
