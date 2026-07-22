"""Focused tests for the independent OpenAI visual-verifier provider."""
from __future__ import annotations

import base64
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from winpilot.modeling import ModelCallError, ModelCallRuntime, ModelRetryPolicy
from winpilot.vnext.verification.contracts import (
    VerifierAssessment,
    forbidden_effect_condition,
)
from winpilot.vnext.verification.model_provider import (
    VISUAL_ASSESSMENT_TOOL,
    VISUAL_VERIFIER_ROLE,
    VISUAL_VERIFIER_SYSTEM_CONTRACT,
    OpenAIVisualVerifierModel,
    VisualVerifierModelError,
)
from winpilot.vnext.verification.visual_model import VisualVerifierModelRequest


CRITERION = "the requested visual result is present"
FORBIDDEN = "unrelated visible content was changed"


class _ChatClient:
    def __init__(self, create) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _request() -> VisualVerifierModelRequest:
    image_b64 = base64.b64encode(b"current-authoritative-image").decode("ascii")
    return VisualVerifierModelRequest(
        desired_state="the requested visual state exists",
        acceptance_criteria=(CRITERION,),
        forbidden_effects=(FORBIDDEN,),
        image_b64=image_b64,
        image_sha256=hashlib.sha256(image_b64.encode("ascii")).hexdigest(),
        submitted_size=(800, 600),
    )


def _assessment(**overrides) -> dict:
    value = {
        "satisfied_condition_indices": [0, 1],
        "missing_condition_indices": [],
        "contradictions": [],
        "concise_reason": "the image visibly satisfies every condition",
    }
    value.update(overrides)
    return value


def _tool_call(
    arguments: object,
    *,
    name: str = VISUAL_ASSESSMENT_TOOL,
    call_type: str = "function",
) -> SimpleNamespace:
    encoded = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return SimpleNamespace(
        type=call_type,
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


def _model(create, **kwargs) -> OpenAIVisualVerifierModel:
    return OpenAIVisualVerifierModel(
        _ChatClient(create),
        model="vision-model",
        decision_id_factory=lambda: "verifier-decision-local",
        **kwargs,
    )


def test_provider_uses_isolated_identity_free_image_request_and_one_tool(
    monkeypatch,
) -> None:
    from winpilot.vnext.verification import model_provider

    provider_requests = []
    boundary = []
    real_invoke = model_provider.invoke_chat_completion

    def create(**request):
        provider_requests.append(request)
        return _response(_tool_call(_assessment()))

    def recorded(client, **kwargs):
        boundary.append(kwargs)
        return real_invoke(client, **kwargs)

    monkeypatch.setattr(model_provider, "invoke_chat_completion", recorded)
    assessment = _model(create).assess(_request())

    assert isinstance(assessment, VerifierAssessment)
    assert assessment.verified is True
    assert assessment.satisfied_conditions == (
        CRITERION,
        forbidden_effect_condition(FORBIDDEN),
    )
    assert assessment.confidence is None
    assert boundary[0]["model_role"] == VISUAL_VERIFIER_ROLE
    assert boundary[0]["purpose"] == "independent_visual_assessment"
    assert len(provider_requests) == 1
    sent = provider_requests[0]
    assert sent["tool_choice"] == "required"
    assert [tool["function"]["name"] for tool in sent["tools"]] == [
        VISUAL_ASSESSMENT_TOOL
    ]
    schema = sent["tools"][0]["function"]["parameters"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "satisfied_condition_indices",
        "missing_condition_indices",
        "contradictions",
        "concise_reason",
    }
    assert schema["properties"]["satisfied_condition_indices"]["items"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 1,
    }
    assert sent["messages"][0] == {
        "role": "system",
        "content": VISUAL_VERIFIER_SYSTEM_CONTRACT,
    }
    content = sent["messages"][1]["content"]
    payload = json.loads(content[0]["text"])
    assert set(payload) == {
        "desired_state",
        "required_conditions",
        "submitted_image_size",
    }
    assert payload["required_conditions"] == [
        {
            "condition_index": 0,
            "kind": "acceptance",
            "condition": CRITERION,
        },
        {
            "condition_index": 1,
            "kind": "forbidden_effect_absent",
            "condition": FORBIDDEN,
        },
    ]
    request = _request()
    assert content[1]["image_url"]["url"] == (
        f"data:image/jpeg;base64,{request.image_b64}"
    )
    serialized = json.dumps(sent, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "mission_id",
        "run_id",
        "mission_revision",
        "obligation_id",
        "frame_id",
        "evidence_id",
        "evidence_refs",
        "claim_fingerprint",
        "image_sha256",
        "verifier-decision-local",
    ):
        assert forbidden not in serialized
    for mutation_tool in ("click", "type_text", "scroll", "drag", "done"):
        assert mutation_tool not in {tool["function"]["name"] for tool in sent["tools"]}


@pytest.mark.parametrize(
    "response,error",
    (
        (_response(content="It looks correct."), "text"),
        (_response(), "exactly one tool call"),
        (
            _response(_tool_call(_assessment()), _tool_call(_assessment())),
            "exactly one tool call",
        ),
        (
            _response(_tool_call(_assessment(), name="click")),
            "unknown tool",
        ),
        (_response(_tool_call("not-json")), "malformed assessment"),
        (_response(_tool_call([])), "non-canonical assessment fields"),
        (
            _response(_tool_call({**_assessment(), "mission_id": "invented"})),
            "non-canonical assessment fields",
        ),
        (
            _response(_tool_call(_assessment(
                missing_condition_indices=[0],
            ))),
            "classifications overlap",
        ),
        (
            _response(_tool_call(_assessment(
                satisfied_condition_indices=[0],
            ))),
            "classify every required condition",
        ),
        (
            _response(_tool_call(_assessment(
                satisfied_condition_indices=[0, "1"],
            ))),
            "array of integers",
        ),
        (
            _response(_tool_call(_assessment(
                satisfied_condition_indices=[0, 0, 1],
            ))),
            "must not contain duplicates",
        ),
        (
            _response(_tool_call(_assessment(
                contradictions=[1],
            ))),
            "contradictions must be",
        ),
        (
            _response(_tool_call(_assessment(
                contradictions=["conflict", " conflict"],
            ))),
            "canonical assessment",
        ),
        (
            _response(_tool_call(_assessment(
                concise_reason=" ",
            ))),
            "concise_reason",
        ),
        (
            _response(_tool_call({**_assessment(), "confidence": "1"})),
            "non-canonical assessment fields",
        ),
        (
            _response(_tool_call(_assessment(), call_type="provider")),
            "must be a function",
        ),
    ),
)
def test_provider_rejects_text_multiple_malformed_and_identity_overreach(
    response,
    error,
) -> None:
    with pytest.raises(VisualVerifierModelError, match=error):
        _model(lambda **_request: response).assess(_request())


def test_provider_rejects_multiple_choices() -> None:
    provider = _model(lambda **_request: _response(
        _tool_call(_assessment()), choices=2,
    ))
    with pytest.raises(VisualVerifierModelError, match="exactly one choice"):
        provider.assess(_request())


def test_provider_maps_index_classification_to_exact_canonical_conditions() -> None:
    provider = _model(lambda **_request: _response(_tool_call(_assessment(
        satisfied_condition_indices=[1],
        missing_condition_indices=[0],
        concise_reason="the requested result is not yet visible",
    ))))

    assessment = provider.assess(_request())

    assert assessment.verified is False
    assert assessment.satisfied_conditions == (
        forbidden_effect_condition(FORBIDDEN),
    )
    assert assessment.missing_conditions == (CRITERION,)
    assert assessment.contradictions == ()
    assert assessment.confidence is None


def test_transport_retry_reuses_exact_frozen_image_request() -> None:
    calls = []

    def create(**request):
        calls.append(request)
        if len(calls) == 1:
            request["messages"][1]["content"][1]["image_url"]["url"] = "mutated"
            raise TimeoutError("temporary provider timeout")
        return _response(_tool_call(_assessment()))

    provider = _model(
        create,
        retry_policy=ModelRetryPolicy(max_attempts=2, initial_backoff_s=0),
        call_runtime=ModelCallRuntime(sleeper=lambda _delay: None),
    )
    assessment = provider.assess(_request())

    expected_url = f"data:image/jpeg;base64,{_request().image_b64}"
    assert assessment.verified is True
    assert len(calls) == 2
    assert calls[1]["messages"][1]["content"][1]["image_url"]["url"] == expected_url
    assert provider.last_outcome is not None
    assert provider.last_outcome.request_retry_count == 1


def test_cancellation_before_or_during_transport_discards_response() -> None:
    stopped = threading.Event()
    stopped.set()
    calls = []
    provider = _model(lambda **request: calls.append(request))
    with pytest.raises(ModelCallError, match="canceled"):
        provider.assess(_request(), stop_event=stopped)
    assert calls == []
    assert provider.last_outcome is None

    late_stop = threading.Event()

    def create(**_request):
        late_stop.set()
        return _response(_tool_call(_assessment()))

    with pytest.raises(ModelCallError, match="abandoned"):
        _model(create).assess(_request(), stop_event=late_stop)


@pytest.mark.parametrize(
    "config",
    (
        {},
        {"base_url": "x", "api_key": "y"},
        {
            "base_url": "https://provider.invalid/v1",
            "api_key": "secret",
            "model": "vision-model",
            "request_timeout_s": "invalid",
        },
    ),
)
def test_provider_configuration_fails_closed(config) -> None:
    with pytest.raises(VisualVerifierModelError):
        OpenAIVisualVerifierModel.from_api_config(config)
    with pytest.raises(VisualVerifierModelError):
        OpenAIVisualVerifierModel(_ChatClient(lambda **_request: None), model="")


def test_api_factory_keeps_sdk_implicit_retries_disabled(monkeypatch) -> None:
    from winpilot.modeling.openai_transport import create_openai_client as real_create
    from winpilot.vnext.verification import model_provider

    captured = {}

    class RawClient:
        chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: None))

    def factory(**kwargs):
        captured.update(kwargs)
        return RawClient()

    def create(**kwargs):
        return real_create(**kwargs, client_factory=factory)

    monkeypatch.setattr(model_provider, "create_openai_client", create)
    provider = OpenAIVisualVerifierModel.from_api_config({
        "base_url": "https://provider.invalid/v1",
        "api_key": "secret",
        "model": "vision-model",
        "max_retries": 2,
    })

    assert isinstance(provider, OpenAIVisualVerifierModel)
    assert captured["max_retries"] == 0


def test_system_contract_is_short_general_and_has_no_gui_rules() -> None:
    assert len(VISUAL_VERIFIER_SYSTEM_CONTRACT) < 360
    lowered = VISUAL_VERIFIER_SYSTEM_CONTRACT.lower()
    for forbidden in (
        "qq",
        "paint",
        "netease",
        "douyin",
        "benchmark",
        "test case",
        "button",
        "coordinate",
    ):
        assert forbidden not in lowered
