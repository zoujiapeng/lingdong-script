"""Provider-boundary tests for the production-injectable Visual Actor."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from winpilot.modeling import ModelCallRuntime, ModelRetryPolicy
from winpilot.vnext.actor.model import (
    VISUAL_ACTOR_ROLE,
    VISUAL_ACTOR_SYSTEM_CONTRACT,
    OpenAIVisualActor,
    VisualActorModelError,
    VisualActorResponseError,
)
from winpilot.vnext.actor.proposals import (
    ACTOR_PROPOSAL_FORMAT,
    DesktopActionProposal,
    InvalidActorProposal,
    parse_actor_proposal,
)
from winpilot.vnext.actor.views import (
    ACTOR_FRAME_VIEW_FORMAT,
    ACTOR_MISSION_VIEW_FORMAT,
    ACTOR_TOOL_VIEW_FORMAT,
    ACTOR_TURN_INPUT_FORMAT,
    ActorFrameView,
    ActorMissionView,
    ActorObligationView,
    ActorToolView,
    ActorTurnInput,
)
from winpilot.vnext.capabilities.live_catalog import build_live_catalog


class _ChatClient:
    def __init__(self, create) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _tool_call(name: str, arguments: object) -> SimpleNamespace:
    encoded = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return SimpleNamespace(
        type="function",
        function=SimpleNamespace(name=name, arguments=encoded),
    )


def _response(
    *calls: SimpleNamespace,
    content: object = None,
    choices: int = 1,
) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=list(calls))
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message) for _ in range(choices)],
    )


def _turn(*tool_names: str) -> ActorTurnInput:
    catalog = build_live_catalog()

    def schema(name: str):
        if name == "observe":
            return {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "question": {"type": "string", "minLength": 1},
                },
            }
        return catalog.get(name).schema

    return ActorTurnInput(
        format=ACTOR_TURN_INPUT_FORMAT,
        mission=ActorMissionView(
            format=ACTOR_MISSION_VIEW_FORMAT,
            original_goal="Complete the currently visible task.",
            active_obligation=ActorObligationView(
                desired_state="The requested visible state exists",
                acceptance_criteria=("The visible result matches the request",),
                forbidden_effects=("Do not alter unrelated content",),
                required_capabilities=("visual.interact",),
            ),
            verified_facts=(),
            artifacts=(),
            target_title="Visible Surface",
            last_transition=None,
            failure_summary=None,
        ),
        frame=ActorFrameView(
            format=ACTOR_FRAME_VIEW_FORMAT,
            image_available=True,
            authoritative=True,
            fresh=True,
            coordinate_space="submitted_image_pixels",
        ),
        tools=tuple(
            ActorToolView(
                format=ACTOR_TOOL_VIEW_FORMAT,
                name=name,
                schema=schema(name),
            )
            for name in tool_names
        ),
    )


def _actor(create, **kwargs) -> OpenAIVisualActor:
    return OpenAIVisualActor(
        _ChatClient(create),
        model="vision-model",
        decision_id_factory=lambda: "visual-decision-1",
        **kwargs,
    )


def test_visual_actor_uses_one_isolated_tool_call_and_returns_strict_proposal(
    monkeypatch,
) -> None:
    from winpilot.vnext.actor import model as actor_model

    requests = []
    boundary = []
    real_invoke = actor_model.invoke_chat_completion

    def create(**request):
        requests.append(request)
        return _response(_tool_call("click", {"image_x": 123, "image_y": 456}))

    def recorded(client, **kwargs):
        boundary.append(kwargs)
        return real_invoke(client, **kwargs)

    monkeypatch.setattr(actor_model, "invoke_chat_completion", recorded)
    raw = _actor(create).propose(
        _turn("click"),
        image_b64="AAAA",
        submitted_size=(800, 600),
    )
    proposal = parse_actor_proposal(raw)

    assert isinstance(proposal, DesktopActionProposal)
    assert proposal.format == ACTOR_PROPOSAL_FORMAT
    assert proposal.action == "click"
    assert dict(proposal.args) == {"image_x": 123, "image_y": 456}
    assert len(requests) == 1
    assert boundary[0]["model_role"] == VISUAL_ACTOR_ROLE
    assert boundary[0]["purpose"] == "visual_actor_proposal"
    assert requests[0]["tool_choice"] == "required"
    assert [item["function"]["name"] for item in requests[0]["tools"]] == ["click"]
    assert requests[0]["tools"][0]["function"]["parameters"] == {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "image_x": {"type": "number", "minimum": 0},
            "image_y": {"type": "number", "minimum": 0},
        },
        "required": ["image_x", "image_y"],
    }
    assert requests[0]["messages"][0] == {
        "role": "system", "content": VISUAL_ACTOR_SYSTEM_CONTRACT,
    }
    user_content = requests[0]["messages"][1]["content"]
    assert len(user_content) == 2
    assert user_content[1]["image_url"]["url"] == "data:image/jpeg;base64,AAAA"
    payload = json.loads(user_content[0]["text"])
    assert payload["submitted_image_size"] == {"width": 800, "height": 600}
    expected_turn = json.loads(_turn("click").to_json())
    assert payload["mission"] == expected_turn["mission"]
    assert payload["frame"] == expected_turn["frame"]
    assert "tools" not in payload


def test_visual_actor_transport_retry_reuses_exact_frozen_image_request() -> None:
    requests = []

    def create(**request):
        requests.append(request)
        if len(requests) == 1:
            request["messages"][0]["content"] = "transport mutation"
            raise TimeoutError("temporary timeout")
        return _response(_tool_call("double_click", {"image_x": 4, "image_y": 5}))

    actor = _actor(
        create,
        retry_policy=ModelRetryPolicy(max_attempts=2, initial_backoff_s=0),
        call_runtime=ModelCallRuntime(sleeper=lambda _delay: None),
    )
    proposal = parse_actor_proposal(actor.propose(
        _turn("double_click"),
        image_b64="BBBB",
        submitted_size=(20, 10),
    ))

    assert proposal.action == "double_click"
    assert len(requests) == 2
    assert requests[1]["messages"][0]["content"] == VISUAL_ACTOR_SYSTEM_CONTRACT
    assert requests[0]["messages"][1] == requests[1]["messages"][1]
    assert actor.last_outcome is not None
    assert actor.last_outcome.request_retry_count == 1


def test_catalog_claim_description_is_projected_once_to_provider_function() -> None:
    requests = []
    turn = _turn("claim_obligation_done")

    def create(**request):
        requests.append(request)
        return _response(_tool_call(
            "claim_obligation_done",
            {"concise_claim": "The requested state is now visible"},
        ))

    proposal = parse_actor_proposal(_actor(create).propose(
        turn,
        image_b64="AAAA",
        submitted_size=(100, 100),
    ))

    expected = build_live_catalog().get("claim_obligation_done").schema["description"]
    serialized = json.loads(turn.to_json())
    assert serialized["tools"][0]["schema"]["description"] == expected
    assert (
        ActorTurnInput.from_dict(serialized).tools[0].input_schema["description"]
        == expected
    )
    function = requests[0]["tools"][0]["function"]
    assert proposal.kind == "claim_obligation_done"
    assert function["description"] == expected
    assert "description" not in function["parameters"]
    assert requests[0]["messages"][0]["content"] == VISUAL_ACTOR_SYSTEM_CONTRACT
    assert expected not in VISUAL_ACTOR_SYSTEM_CONTRACT


@pytest.mark.parametrize(
    "invalid_description",
    (None, "", "   ", " leading", "trailing ", 7),
)
def test_visual_actor_rejects_invalid_catalog_description_before_provider_call(
    invalid_description,
) -> None:
    raw = json.loads(_turn("click").to_json())
    raw["tools"][0]["schema"]["description"] = invalid_description
    turn = ActorTurnInput.from_dict(raw)
    provider_called = False

    def create(**_request):
        nonlocal provider_called
        provider_called = True
        return _response(_tool_call("click", {"image_x": 1, "image_y": 2}))

    with pytest.raises(VisualActorModelError, match="description"):
        _actor(create).propose(
            turn,
            image_b64="AAAA",
            submitted_size=(100, 100),
        )
    assert provider_called is False


def test_explicit_scroll_contract_reaches_the_provider_tool_schema() -> None:
    requests = []

    def create(**request):
        requests.append(request)
        return _response(_tool_call(
            "scroll", {
                "image_x": 40,
                "image_y": 50,
                "direction": "up",
                "notches": 3,
            },
        ))

    proposal = parse_actor_proposal(_actor(create).propose(
        _turn("scroll"),
        image_b64="AAAA",
        submitted_size=(100, 100),
    ))

    assert proposal.action == "scroll"
    properties = requests[0]["tools"][0]["function"]["parameters"][
        "properties"
    ]
    assert properties["direction"]["enum"] == ["up", "down"]
    assert properties["notches"]["minimum"] == 1
    assert properties["notches"]["maximum"] == 12


def test_visual_actor_rejects_legacy_signed_scroll_amount() -> None:
    actor = _actor(lambda **_request: _response(_tool_call(
        "scroll", {"image_x": 40, "image_y": 50, "amount": 3},
    )))

    with pytest.raises(VisualActorModelError, match="Broker schema"):
        actor.propose(
            _turn("scroll"),
            image_b64="AAAA",
            submitted_size=(100, 100),
        )


@pytest.mark.parametrize(
    "response,error",
    (
        (_response(content="I would click it."), "text"),
        (_response(), "exactly one tool call"),
        (
            _response(
                _tool_call("click", {"image_x": 1, "image_y": 2}),
                _tool_call("click", {"image_x": 3, "image_y": 4}),
            ),
            "exactly one tool call",
        ),
        (_response(_tool_call("click", "not-json")), "invalid JSON"),
        (_response(_tool_call("click", [])), "one object"),
        (
            _response(_tool_call("click", {
                "image_x": 1, "image_y": 2, "frame_id": 9,
            })),
            "Broker schema",
        ),
        (_response(_tool_call("click", {"image_x": True, "image_y": 2})), "Broker schema"),
    ),
)
def test_visual_actor_rejects_text_multiple_malformed_and_identity_overreach(
    response,
    error,
) -> None:
    with pytest.raises(VisualActorModelError, match=error):
        _actor(lambda **_request: response).propose(
            _turn("click"),
            image_b64="AAAA",
            submitted_size=(100, 100),
        )


def test_provider_response_errors_are_recoverable_but_invariants_are_not() -> None:
    assert issubclass(VisualActorResponseError, InvalidActorProposal)
    assert not issubclass(VisualActorModelError, InvalidActorProposal)


def test_visual_actor_rejects_tool_outside_exact_broker_projection() -> None:
    actor = _actor(lambda **_request: _response(
        _tool_call("click", {"image_x": 1, "image_y": 2})
    ))
    with pytest.raises(VisualActorModelError, match="outside Broker projection"):
        actor.propose(
            _turn("observe"),
            image_b64="AAAA",
            submitted_size=(100, 100),
        )


@pytest.mark.parametrize(
    "name,arguments,kind",
    (
        (
            "observe",
            {},
            "observe",
        ),
        (
            "claim_obligation_done",
            {"concise_claim": "The requested state is now visible"},
            "claim_obligation_done",
        ),
        (
            "blocked",
            {
                "blocker_kind": "visual_uncertainty",
                "observed_facts": ["Two visible candidates remain indistinguishable"],
            },
            "blocked",
        ),
    ),
)
def test_visual_actor_maps_each_non_mutating_live_tool_to_one_strict_proposal(
    name,
    arguments,
    kind,
) -> None:
    actor = _actor(lambda **_request: _response(_tool_call(name, arguments)))
    proposal = parse_actor_proposal(actor.propose(
        _turn(name),
        image_b64="AAAA",
        submitted_size=(100, 100),
    ))
    assert proposal.kind == kind
    if name == "observe":
        assert proposal.observation_kind == "full"
        assert proposal.image_region is None
        assert proposal.question == "refresh the current authoritative frame"


def test_visual_actor_rejects_multiple_choices_and_non_current_images() -> None:
    actor = _actor(lambda **_request: _response(
        _tool_call("click", {"image_x": 1, "image_y": 2}), choices=2,
    ))
    with pytest.raises(VisualActorModelError, match="exactly one choice"):
        actor.propose(_turn("click"), image_b64="AAAA", submitted_size=(10, 10))

    turn = _turn("click")
    stale = ActorTurnInput.from_dict({
        **json.loads(turn.to_json()),
        "frame": {**json.loads(turn.frame.to_json()), "fresh": False},
    })
    with pytest.raises(VisualActorModelError, match="current authoritative image"):
        actor.propose(stale, image_b64="AAAA", submitted_size=(10, 10))
    with pytest.raises(VisualActorModelError, match="image is missing"):
        actor.propose(turn, image_b64="", submitted_size=(10, 10))
    with pytest.raises(VisualActorModelError, match="submitted_size"):
        actor.propose(turn, image_b64="AAAA", submitted_size=(10, 0))


def test_visual_actor_system_contract_is_short_general_and_rule_free() -> None:
    assert len(VISUAL_ACTOR_SYSTEM_CONTRACT) < 320
    lowered = VISUAL_ACTOR_SYSTEM_CONTRACT.lower()
    for forbidden in (
        "qq", "paint", "netease", "douyin", "benchmark", "test case", "button",
    ):
        assert forbidden not in lowered


def test_visual_actor_api_factory_keeps_sdk_implicit_retries_disabled(
    monkeypatch,
) -> None:
    from winpilot.modeling.openai_transport import create_openai_client as real_create
    from winpilot.vnext.actor import model as actor_model

    captured = {}

    class RawClient:
        chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: None))

    def factory(**kwargs):
        captured.update(kwargs)
        return RawClient()

    def create(**kwargs):
        return real_create(**kwargs, client_factory=factory)

    monkeypatch.setattr(actor_model, "create_openai_client", create)
    actor = OpenAIVisualActor.from_api_config({
        "base_url": "https://provider.invalid/v1",
        "api_key": "secret",
        "model": "vision-model",
        "max_retries": 2,
    })

    assert isinstance(actor, OpenAIVisualActor)
    assert captured["max_retries"] == 0
