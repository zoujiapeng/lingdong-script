from __future__ import annotations

from copy import deepcopy

import pytest

from winpilot.modeling.provider_protocols import (
    ProviderHTTPError,
    ProviderProtocolAdapter,
    ProviderProtocolError,
    build_provider_request,
    normalize_provider_response,
    provider_endpoint,
)
from winpilot.modeling.chat import invoke_chat_completion
from winpilot.modeling.contracts import ModelRetryPolicy
from winpilot.modeling.runtime import ModelCallError, ModelCallRuntime

_IMAGE = "data:image/png;base64,iVBORw0KGgo="
_TOOL = {
    "type": "function",
    "function": {
        "name": "inspect",
        "description": "Inspect visible state",
        "parameters": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
        },
        "strict": True,
    },
}


def _common_request() -> dict:
    return {
        "model": "model-x",
        "max_completion_tokens": 128,
        "messages": [
            {"role": "system", "content": "Act on the visible state."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Inspect this."},
                    {
                        "type": "image_url",
                        "image_url": {"url": _IMAGE, "detail": "high"},
                    },
                ],
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "inspect",
                            "arguments": '{"target":"window"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": '{"ok":true}'},
        ],
        "tools": [_TOOL],
    }


def test_responses_request_translates_multimodal_tools_and_results():
    body = build_provider_request("responses", _common_request())

    assert body["model"] == "model-x"
    assert body["max_output_tokens"] == 128
    assert body["input"][0] == {
        "type": "message",
        "role": "system",
        "content": "Act on the visible state.",
    }
    assert body["input"][1]["content"] == [
        {"type": "input_text", "text": "Inspect this."},
        {"type": "input_image", "image_url": _IMAGE, "detail": "high"},
    ]
    assert body["input"][2] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "inspect",
        "arguments": '{"target":"window"}',
    }
    assert body["input"][3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"ok":true}',
    }
    assert body["tools"] == [
        {
            "type": "function",
            "name": "inspect",
            "description": "Inspect visible state",
            "parameters": _TOOL["function"]["parameters"],
            "strict": True,
        }
    ]
    assert body["include"] == ["reasoning.encrypted_content"]


def test_anthropic_request_translates_system_images_and_tool_blocks():
    body = build_provider_request("anthropic", _common_request())

    assert body["system"] == [{"type": "text", "text": "Act on the visible state."}]
    assert body["max_tokens"] == 128
    assert body["messages"][0] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "Inspect this."},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "iVBORw0KGgo=",
                },
            },
        ],
    }
    assert body["messages"][1]["content"] == [
        {
            "type": "tool_use",
            "id": "call_1",
            "name": "inspect",
            "input": {"target": "window"},
        }
    ]
    assert body["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "call_1", "content": '{"ok":true}'}
    ]
    assert body["tools"] == [
        {
            "name": "inspect",
            "description": "Inspect visible state",
            "input_schema": _TOOL["function"]["parameters"],
            "strict": True,
        }
    ]


def test_gemini_request_translates_parts_and_function_round_trip():
    body = build_provider_request("gemini", _common_request())

    assert body["systemInstruction"] == {
        "parts": [{"text": "Act on the visible state."}]
    }
    assert body["generationConfig"] == {"maxOutputTokens": 128}
    assert body["contents"][0] == {
        "role": "user",
        "parts": [
            {"text": "Inspect this."},
            {"inlineData": {"mimeType": "image/png", "data": "iVBORw0KGgo="}},
        ],
    }
    assert body["contents"][1] == {
        "role": "model",
        "parts": [
            {
                "functionCall": {
                    "id": "call_1",
                    "name": "inspect",
                    "args": {"target": "window"},
                }
            }
        ],
    }
    assert body["contents"][2] == {
        "role": "user",
        "parts": [
            {
                "functionResponse": {
                    "id": "call_1",
                    "name": "inspect",
                    "response": {"ok": True},
                }
            }
        ],
    }
    declaration = body["tools"][0]["functionDeclarations"][0]
    assert declaration["parametersJsonSchema"] == _TOOL["function"]["parameters"]


def test_provider_tool_choice_shapes_follow_each_native_protocol():
    request = _common_request()
    request["tool_choice"] = {
        "type": "function",
        "function": {"name": "inspect"},
    }

    responses = build_provider_request("responses", request)
    assert responses["tool_choice"] == {"type": "function", "name": "inspect"}

    anthropic = build_provider_request("anthropic", request)
    assert anthropic["tool_choice"] == {"type": "tool", "name": "inspect"}

    gemini = build_provider_request("gemini", request)
    assert gemini["toolConfig"] == {
        "functionCallingConfig": {
            "mode": "ANY",
            "allowedFunctionNames": ["inspect"],
        }
    }


def test_responses_round_trip_replays_every_native_output_item_untouched():
    native_output = [
        {
            "type": "reasoning",
            "id": "rs_1",
            "status": "completed",
            "summary": [{"type": "summary_text", "text": "inspect first"}],
            "encrypted_content": "enc",
        },
        {
            "type": "message",
            "id": "msg_1",
            "status": "completed",
            "role": "assistant",
            "phase": "commentary",
            "content": [{"type": "output_text", "text": "Checking."}],
        },
        {
            "type": "function_call",
            "id": "fc_1",
            "status": "completed",
            "call_id": "call_r",
            "name": "inspect",
            "arguments": '{"target":"screen"}',
        },
    ]
    response, replay = normalize_provider_response(
        "responses",
        {
            "id": "resp_1",
            "status": "completed",
            "model": "gpt-x",
            "output": native_output,
            "usage": {"input_tokens": 10, "output_tokens": 2},
        },
        model="fallback",
    )
    assistant = response["choices"][0]["message"]

    body = build_provider_request(
        "responses",
        {
            "model": "gpt-x",
            "messages": [
                {"role": "user", "content": "Inspect."},
                assistant,
                {"role": "tool", "tool_call_id": "call_r", "content": "ok"},
            ],
            "tools": [_TOOL],
        },
        replay=replay,
    )

    assert body["input"][1:4] == native_output
    assert body["input"][4] == {
        "type": "function_call_output",
        "call_id": "call_r",
        "output": "ok",
    }
    assert replay["call_r"]["responses_output"] == native_output


def test_gemini_round_trip_preserves_full_content_and_absent_native_call_id():
    native_content = {
        "role": "model",
        "parts": [
            {"text": "private thought", "thought": True},
            {
                "functionCall": {"name": "inspect", "args": {}},
                "thoughtSignature": "signature",
            },
            {"text": "after-call marker"},
        ],
    }
    response, replay = normalize_provider_response(
        "gemini",
        {
            "responseId": "gem_1",
            "modelVersion": "gemini-x",
            "candidates": [{"finishReason": "STOP", "content": native_content}],
        },
        model="fallback",
    )
    assistant = response["choices"][0]["message"]
    canonical_call_id = assistant["tool_calls"][0]["id"]
    assert canonical_call_id.startswith("call_gemini_")
    assert replay[canonical_call_id]["gemini_native_call_id"] is None

    body = build_provider_request(
        "gemini",
        {
            "model": "gemini-x",
            "messages": [
                {"role": "user", "content": "Inspect."},
                assistant,
                {
                    "role": "tool",
                    "tool_call_id": canonical_call_id,
                    "content": '{"ok":true}',
                },
            ],
            "tools": [_TOOL],
        },
        replay=replay,
    )

    assert body["contents"][1] == native_content
    function_response = body["contents"][2]["parts"][0]["functionResponse"]
    assert function_response == {
        "name": "inspect",
        "response": {"ok": True},
    }


def test_gemini_synthetic_ids_do_not_collide_across_responses():
    payload = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"functionCall": {"name": "inspect", "args": {}}}],
                }
            }
        ]
    }
    first, _ = normalize_provider_response("gemini", payload, model="gemini-x")
    second, _ = normalize_provider_response("gemini", payload, model="gemini-x")
    assert (
        first["choices"][0]["message"]["tool_calls"][0]["id"]
        != second["choices"][0]["message"]["tool_calls"][0]["id"]
    )


def test_anthropic_signed_thinking_is_replayed_before_the_tool_use():
    response, replay = normalize_provider_response(
        "anthropic",
        {
            "id": "msg_1",
            "model": "claude-x",
            "stop_reason": "tool_use",
            "content": [
                {"type": "thinking", "thinking": "inspect first", "signature": "sig-a"},
                {"type": "tool_use", "id": "call_a", "name": "inspect", "input": {}},
            ],
        },
        model="fallback",
    )
    assistant = response["choices"][0]["message"]
    body = build_provider_request(
        "anthropic",
        {
            "model": "claude-x",
            "max_tokens": 64,
            "messages": [
                {"role": "user", "content": "Inspect."},
                assistant,
                {"role": "tool", "tool_call_id": "call_a", "content": "ok"},
            ],
            "tools": [_TOOL],
        },
        replay=replay,
    )
    assert body["messages"][1]["content"][:2] == [
        {"type": "thinking", "thinking": "inspect first", "signature": "sig-a"},
        {"type": "tool_use", "id": "call_a", "name": "inspect", "input": {}},
    ]


def test_gemini_usage_counts_thoughts_as_completion_and_preserves_total():
    response, _ = normalize_provider_response(
        "gemini",
        {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "done"}]}}],
            "usageMetadata": {
                "promptTokenCount": 10,
                "cachedContentTokenCount": 6,
                "candidatesTokenCount": 2,
                "thoughtsTokenCount": 5,
                "totalTokenCount": 20,
            },
        },
        model="gemini-x",
    )
    assert response["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 7,
        "total_tokens": 20,
        "reasoning_tokens": 5,
        "prompt_tokens_details": {"cached_tokens": 6},
        "completion_tokens_details": {"reasoning_tokens": 5},
    }


@pytest.mark.parametrize(
    "payload",
    [
        {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [],
        },
        {"status": "failed", "output": []},
        {"error": {"message": "bad request"}},
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "refusal", "refusal": "cannot comply"}],
                }
            ],
        },
    ],
)
def test_responses_non_success_states_never_become_empty_stop(payload):
    with pytest.raises(ProviderProtocolError):
        normalize_provider_response("responses", payload, model="gpt-x")


@pytest.mark.parametrize(
    ("family", "payload"),
    [
        (
            "responses",
            {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "",
                        "name": "inspect",
                        "arguments": "{}",
                    }
                ],
            },
        ),
        (
            "responses",
            {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "inspect",
                        "arguments": "[]",
                    }
                ],
            },
        ),
        (
            "anthropic",
            {
                "content": [
                    {"type": "tool_use", "id": "call_1", "name": "", "input": {}}
                ]
            },
        ),
        (
            "anthropic",
            {
                "content": [
                    {"type": "tool_use", "id": "call_1", "name": "inspect", "input": []}
                ]
            },
        ),
        (
            "gemini",
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {"functionCall": {"name": "inspect", "args": []}}
                            ],
                        }
                    }
                ]
            },
        ),
    ],
)
def test_provider_tool_calls_require_valid_ids_names_and_object_args(family, payload):
    with pytest.raises(ProviderProtocolError):
        normalize_provider_response(family, payload, model="model-x")


class _FakeTransport:
    def __init__(self, response: dict):
        self.response = response
        self.calls = []

    def post_json(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        return deepcopy(self.response)


@pytest.mark.parametrize(
    ("family", "payload", "url", "auth_header"),
    [
        (
            "responses",
            {
                "id": "resp_1",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                ],
            },
            "https://provider.test/v1/responses",
            "authorization",
        ),
        (
            "anthropic",
            {
                "id": "msg_1",
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn",
            },
            "https://provider.test/v1/messages",
            "x-api-key",
        ),
        (
            "gemini",
            {
                "responseId": "gem_1",
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": "done"}],
                        }
                    }
                ],
            },
            "https://provider.test/v1beta/models/model-x:generateContent",
            "x-goog-api-key",
        ),
    ],
)
def test_adapter_uses_one_injected_http_boundary(family, payload, url, auth_header):
    transport = _FakeTransport(payload)
    adapter = ProviderProtocolAdapter(
        family=family,
        base_url="https://provider.test",
        api_key="secret",
        model="model-x",
        timeout=12,
        transport=transport,
    )
    request = {"messages": [{"role": "user", "content": "hello"}]}
    if family == "anthropic":
        request["max_tokens"] = 32

    result = adapter.send_prepared_chat_completion(
        adapter.prepare_chat_completion(**request)
    )

    assert result["choices"][0]["message"]["content"] == "done"
    assert len(transport.calls) == 1
    assert transport.calls[0]["url"] == url
    assert transport.calls[0]["headers"][auth_header]
    assert transport.calls[0]["timeout"] == 12


def test_adapter_replays_gemini_thought_signature_on_next_turn():
    transport = _FakeTransport(
        {
            "responseId": "gem_1",
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {
                                    "id": "call_g",
                                    "name": "inspect",
                                    "args": {},
                                },
                                "thoughtSignature": "signature",
                            }
                        ],
                    }
                }
            ],
        }
    )
    adapter = ProviderProtocolAdapter(
        family="gemini",
        base_url="https://provider.test/v1beta",
        api_key="secret",
        model="model-x",
        timeout=12,
        transport=transport,
    )
    first = adapter.send_prepared_chat_completion(
        adapter.prepare_chat_completion(
            messages=[{"role": "user", "content": "inspect"}],
            tools=[_TOOL],
        )
    )
    assistant = first["choices"][0]["message"]

    next_body = adapter.prepare_chat_completion(
        messages=[
            {"role": "user", "content": "inspect"},
            assistant,
            {"role": "tool", "tool_call_id": "call_g", "content": "ok"},
        ],
        tools=[_TOOL],
    )

    assert next_body["contents"][1]["parts"][0]["thoughtSignature"] == "signature"


def test_adapter_honors_frozen_per_request_timeout_without_sending_it_as_json():
    transport = _FakeTransport(
        {"id": "msg_1", "content": [{"type": "text", "text": "done"}]}
    )
    adapter = ProviderProtocolAdapter(
        family="anthropic",
        base_url="https://provider.test",
        api_key="secret",
        model="model-x",
        timeout=12,
        transport=transport,
    )

    adapter.send_prepared_chat_completion(
        adapter.prepare_chat_completion(
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=32,
            timeout=3.5,
        )
    )

    assert transport.calls[0]["timeout"] == 3.5
    assert "timeout" not in transport.calls[0]["body"]


class _FlakyTransport(_FakeTransport):
    def post_json(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        if len(self.calls) == 1:
            raise ProviderHTTPError(429, {"error": {"message": "busy"}})
        return deepcopy(self.response)


def test_native_adapter_stays_inside_the_existing_exact_request_retry_owner():
    transport = _FlakyTransport(
        {
            "id": "resp_1",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "done"}],
                }
            ],
        }
    )
    adapter = ProviderProtocolAdapter(
        family="responses",
        base_url="https://provider.test/v1",
        api_key="secret",
        model="model-x",
        timeout=12,
        transport=transport,
    )

    outcome = invoke_chat_completion(
        adapter,
        decision_id="decision-1",
        model_role="visual_actor",
        purpose="test native provider boundary",
        provider_request={
            "model": "model-x",
            "messages": [{"role": "user", "content": "hello"}],
        },
        policy=ModelRetryPolicy(max_attempts=2, initial_backoff_s=0),
        runtime=ModelCallRuntime(sleeper=lambda _seconds: None),
    )

    assert outcome.response["choices"][0]["message"]["content"] == "done"
    assert len(outcome.attempts) == 2
    assert (
        outcome.attempts[0].request_fingerprint
        == outcome.attempts[1].request_fingerprint
    )
    assert transport.calls[0]["body"] == transport.calls[1]["body"]


def test_endpoint_builder_preserves_query_and_adds_default_api_version():
    assert provider_endpoint("responses", "https://api.test?tenant=one", "model") == (
        "https://api.test/v1/responses?tenant=one"
    )
    assert provider_endpoint("anthropic", "https://api.test", "model") == (
        "https://api.test/v1/messages"
    )
    assert provider_endpoint("gemini", "https://api.test", "model/one") == (
        "https://api.test/v1beta/models/model%2Fone:generateContent"
    )


def test_invalid_native_requests_fail_before_http():
    request = _common_request()
    request["messages"][1]["content"][1]["image_url"][
        "url"
    ] = "https://example.test/image.png"
    with pytest.raises(ProviderProtocolError, match="base64 data URL"):
        build_provider_request("gemini", request)

    request = _common_request()
    request["stream"] = True
    with pytest.raises(ProviderProtocolError, match="non-streaming"):
        build_provider_request("responses", request)


def test_anthropic_system_is_text_only_and_images_use_supported_media_types():
    system_image = _common_request()
    system_image["messages"][0]["content"] = [
        {"type": "image_url", "image_url": {"url": _IMAGE}}
    ]
    with pytest.raises(ProviderProtocolError, match="text-only"):
        build_provider_request("anthropic", system_image)

    svg_image = _common_request()
    svg_image["messages"][1]["content"] = [
        {
            "type": "image_url",
            "image_url": {"url": "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="},
        }
    ]
    with pytest.raises(ProviderProtocolError, match="does not support"):
        build_provider_request("anthropic", svg_image)


def test_invalid_transport_configuration_is_not_retried():
    transport = _FakeTransport({})
    adapter = ProviderProtocolAdapter(
        family="responses",
        base_url="not-an-absolute-url",
        api_key="secret",
        model="model-x",
        timeout=12,
        transport=transport,
    )

    with pytest.raises(ModelCallError) as caught:
        invoke_chat_completion(
            adapter,
            decision_id="decision-invalid-config",
            model_role="visual_actor",
            purpose="verify invalid provider config",
            provider_request={
                "model": "model-x",
                "messages": [{"role": "user", "content": "hello"}],
            },
            policy=ModelRetryPolicy(max_attempts=3, initial_backoff_s=0),
        )

    assert len(caught.value.attempts) == 1
    assert caught.value.attempts[0].retryable is False
    assert transport.calls == []


def test_http_error_exposes_structured_status_and_body():
    error = ProviderHTTPError(429, {"error": {"message": "busy"}})
    assert error.status_code == 429
    assert error.body == {"error": {"message": "busy"}}
