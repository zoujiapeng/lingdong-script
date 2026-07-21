from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest


class _ConfiguredApi:
    def get(self, *path, default=None):
        if path == ("api",):
            return {
                "base_url": "https://provider.invalid/v1",
                "api_key": "secret",
                "model": "test-model",
            }
        return default


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


def _capture_boundary(monkeypatch, module, client: _FakeClient):
    from winpilot.modeling import invoke_chat_completion as invoke

    calls: list[dict] = []

    def recorded(bound_client, **kwargs):
        calls.append(kwargs)
        return invoke(bound_client, **kwargs)

    monkeypatch.setattr(module, "CONFIG", _ConfiguredApi())
    monkeypatch.setattr(module, "create_openai_client", lambda **_kwargs: client)
    monkeypatch.setattr(module, "invoke_chat_completion", recorded)
    return calls


def test_conversation_uses_one_runtime_owned_model_attempt(monkeypatch) -> None:
    from winpilot.tasking import conversation

    client = _FakeClient("Direct answer")
    calls = _capture_boundary(monkeypatch, conversation, client)

    stopped = threading.Event()
    result = conversation.generate_conversation_response(
        "What can you do?", stop_event=stopped
    )

    assert result.text == "Direct answer"
    assert result.model_calls == 1
    assert len(client.requests) == 1
    assert len(calls) == 1
    assert calls[0]["decision_id"].startswith("conversation-response:")
    assert calls[0]["model_role"] == "conversation_responder"
    assert calls[0]["purpose"] == "generate_text_only_response"
    assert calls[0]["policy"].max_attempts == 1
    assert calls[0]["stop_event"] is stopped


def test_message_draft_uses_one_runtime_owned_model_attempt(monkeypatch) -> None:
    from winpilot.tasking import message_content

    client = _FakeClient(
        '{"text":"hello","confidence":0.9,"grounded":true,'
        '"requires_user_input":false,"missing_information":[],"rationale":"ok"}'
    )
    calls = _capture_boundary(monkeypatch, message_content, client)

    stopped = threading.Event()
    result = message_content.generate_message_content(
        instruction="say hello", recipient="Alice", channel="chat",
        stop_event=stopped,
    )

    assert result.text == "hello"
    assert result.model_calls == 1
    assert len(client.requests) == 1
    assert len(calls) == 1
    assert calls[0]["decision_id"].startswith("message-content:")
    assert calls[0]["model_role"] == "message_content_generator"
    assert calls[0]["purpose"] == "draft_grounded_outbound_message"
    assert calls[0]["policy"].max_attempts == 1
    assert calls[0]["stop_event"] is stopped


def test_message_parse_failure_is_not_a_transport_retry(monkeypatch) -> None:
    from winpilot.tasking import message_content

    client = _FakeClient("not JSON")
    calls = _capture_boundary(monkeypatch, message_content, client)

    with pytest.raises(
        message_content.MessageContentGenerationError,
        match="returned no JSON object",
    ):
        message_content.generate_message_content(
            instruction="say hello", recipient="Alice", channel="chat"
        )

    assert len(calls) == 1
    assert len(client.requests) == 1
