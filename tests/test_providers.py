"""
LLM provider tests — DeepSeekProvider and OpenAIProvider with mocked HTTP.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from unittest.mock import MagicMock, patch

import pytest

from providers.base import LLMProvider
from providers.deepseek import DeepSeekProvider, DEFAULT_API_BASE, DEFAULT_MODEL
from providers.openai import OpenAIProvider


# ── Fake Response Helper ───────────────────────────────────────────

def _fake_response(content: str, status_code: int = 200):
    """Build a mock requests.Response with OpenAI-compatible JSON body."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "model": "test-model",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    mock_resp.raise_for_status = MagicMock()
    if status_code >= 400:
        mock_resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return mock_resp


class TestDeepSeekProvider:
    """DeepSeekProvider construction, config, and chat with mocked HTTP."""

    def setup_method(self):
        self.provider = DeepSeekProvider(
            api_key="sk-test",
            api_base="https://test.deepseek.com/v1",
            model="test-model",
        )

    def test_defaults(self):
        p = DeepSeekProvider()
        assert p.api_base == DEFAULT_API_BASE
        assert p.model == DEFAULT_MODEL
        assert p.api_key == ""

    def test_custom_config(self):
        assert self.provider.api_key == "sk-test"
        assert self.provider.api_base == "https://test.deepseek.com/v1"
        assert self.provider.model == "test-model"

    def test_does_not_append_extra_slash(self):
        p = DeepSeekProvider(api_base="https://x.com/v1/")
        assert p.api_base == "https://x.com/v1"

    @patch("providers.deepseek._req.post")
    def test_chat_success(self, mock_post):
        mock_post.return_value = _fake_response('{"result": "ok"}')

        result = self.provider.chat(
            [{"role": "user", "content": "hello"}],
            temperature=0.5,
            max_tokens=100,
        )

        assert result["content"] == '{"result": "ok"}'
        assert result["model"] == "test-model"
        assert "raw_response" in result
        mock_post.assert_called_once()

        # Verify payload sent to API
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["model"] == "test-model"
        assert call_kwargs["json"]["temperature"] == 0.5
        assert call_kwargs["json"]["max_tokens"] == 100

    @patch("providers.deepseek._req.post")
    def test_chat_with_json_mode(self, mock_post):
        mock_post.return_value = _fake_response('{"ok": true}')

        result = self.provider.chat(
            [{"role": "user", "content": "say json"}],
            response_format={"type": "json_object"},
        )

        assert result["content"] == '{"ok": true}'
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["response_format"] == {"type": "json_object"}

    @patch("providers.deepseek._req.post")
    def test_chat_strips_code_fences(self, mock_post):
        mock_post.return_value = _fake_response("```json\n{\"key\": \"value\"}\n```")

        result = self.provider.chat([{"role": "user", "content": "hi"}])
        assert result["content"] == '{"key": "value"}'

    @patch("providers.deepseek._req.post")
    def test_retry_on_failure_then_succeed(self, mock_post):
        """First two attempts fail, third succeeds."""
        mock_post.side_effect = [
            Exception("Network error"),
            Exception("Timeout"),
            _fake_response('{"ok": true}'),
        ]

        result = self.provider.chat([{"role": "user", "content": "hi"}])
        assert result["content"] == '{"ok": true}'
        assert mock_post.call_count == 3

    @patch("providers.deepseek._req.post")
    def test_all_retries_fail_returns_fallback(self, mock_post):
        mock_post.side_effect = Exception("Always fails")

        result = self.provider.chat([{"role": "user", "content": "hi"}])
        assert result["content"] == ""
        assert "error" in result
        assert mock_post.call_count == 3

    def test_chat_stream_raises(self):
        with pytest.raises(NotImplementedError):
            self.provider.chat_stream([])


class TestOpenAIProvider:
    """OpenAIProvider construction, config, and chat with mocked HTTP."""

    def setup_method(self):
        self.provider = OpenAIProvider(
            api_key="sk-openai-test",
            api_base="https://test.openai.com/v1",
            model="gpt-4o-test",
        )

    def test_defaults(self):
        # Ensure env vars don't interfere
        with patch.dict(os.environ, {}, clear=True):
            p = OpenAIProvider()
            assert p.api_key == ""
            # Without env, should use default base
            assert p.model == "gpt-4o"

    def test_custom_config(self):
        assert self.provider.api_key == "sk-openai-test"
        assert self.provider.api_base == "https://test.openai.com/v1"
        assert self.provider.model == "gpt-4o-test"

    def test_uses_env_api_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env-key"}):
            p = OpenAIProvider()
            assert p.api_key == "sk-env-key"

    @patch("requests.post")
    def test_http_fallback_success(self, mock_post):
        """When SDK is not installed, falls back to direct HTTP."""
        mock_post.return_value = _fake_response('{"answer": 42}')

        result = self.provider.chat(
            [{"role": "user", "content": "what is the meaning?"}],
            temperature=0.7,
        )

        assert result["content"] == '{"answer": 42}'
        assert result["model"] == "gpt-4o-test"
        mock_post.assert_called_once()

        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["model"] == "gpt-4o-test"
        assert call_kwargs["json"]["temperature"] == 0.7

    @patch("requests.post")
    def test_http_fallback_retry_then_succeed(self, mock_post):
        mock_post.side_effect = [
            Exception("Server error"),
            _fake_response('{"ok": true}'),
        ]

        result = self.provider.chat([{"role": "user", "content": "hi"}])
        assert result["content"] == '{"ok": true}'
        assert mock_post.call_count == 2

    @patch("requests.post")
    def test_http_fallback_all_fail(self, mock_post):
        mock_post.side_effect = Exception("Always fails")

        result = self.provider.chat([{"role": "user", "content": "hi"}])
        assert result["content"] == ""
        assert "error" in result

    def test_chat_stream_raises(self):
        with pytest.raises(NotImplementedError):
            self.provider.chat_stream([])


class TestLLMProviderProtocol:
    """Structural subtyping via the LLMProvider protocol."""

    def test_deepseek_is_provider(self):
        from core.interfaces import LLMProvider as LLMProtocol
        provider = DeepSeekProvider(api_key="sk-test")
        # runtime_checkable protocol
        assert isinstance(provider, LLMProtocol)

    def test_openai_is_provider(self):
        from core.interfaces import LLMProvider as LLMProtocol
        provider = OpenAIProvider(api_key="sk-test")
        assert isinstance(provider, LLMProtocol)

    def test_provider_abc_enforces_abstract(self):
        """Cannot instantiate LLMProvider ABC directly."""
        with pytest.raises(TypeError):
            LLMProvider()  # abstract

    def test_custom_provider_matches_protocol(self):
        """A duck-typed provider should pass the protocol check."""
        from core.interfaces import LLMProvider as LLMProtocol

        class MyProvider:
            api_key = "key"
            api_base = "base"
            model = "m"
            def chat(self, messages, **kwargs): return {"content": "hi"}
            def chat_stream(self, messages, **kwargs): return iter([])

        assert isinstance(MyProvider(), LLMProtocol)
