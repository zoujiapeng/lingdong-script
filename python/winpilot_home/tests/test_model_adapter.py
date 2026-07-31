from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from winpilot.model_adapter import (
    accumulate_usage,
    build_model_adapter,
    empty_usage_total,
    model_profile_from_config,
    native_provider_protocol,
    normalize_usage,
    pricing_from_config,
)


def _usage(**kwargs):
    return SimpleNamespace(**kwargs)


def test_profile_infers_provider_and_vision_capability():
    deepseek = model_profile_from_config({
        "provider": "auto",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
    })
    assert deepseek.provider == "deepseek"
    assert deepseek.supports_vision is False

    mimo_text = model_profile_from_config({
        "base_url": "https://api.xiaomimimo.com/v1",
        "model": "mimo-v2.5-pro",
    })
    assert mimo_text.provider == "mimo"
    assert mimo_text.supports_vision is False

    mimo_v25 = model_profile_from_config({
        "base_url": "https://api.xiaomimimo.com/v1",
        "model": "mimo-v2.5",
    })
    assert mimo_v25.provider == "mimo"
    assert mimo_v25.supports_vision is True

    mimo_vision = model_profile_from_config({
        "base_url": "https://api.xiaomimimo.com/v1",
        "model": "mimo-v2-omni",
    })
    assert mimo_vision.provider == "mimo"
    assert mimo_vision.supports_vision is True

    reasonix = model_profile_from_config({
        "base_url": "https://api.reasonix.example/v1",
        "model": "reasonix-coder",
    })
    assert reasonix.provider == "reasonix"


def test_native_provider_vision_capability_is_explicit_or_conservative():
    claude = model_profile_from_config({"model": "claude-sonnet-4-20250514"})
    assert claude.provider == "anthropic"
    assert claude.supports_vision is True

    gemini = model_profile_from_config({"model": "gemini-2.5-pro"})
    assert gemini.provider == "gemini"
    assert gemini.supports_vision is True

    unknown = model_profile_from_config({
        "provider": "anthropic",
        "model": "private-text-model",
    })
    assert unknown.supports_vision is False

    forced_off = model_profile_from_config({
        "provider": "gemini",
        "model": "gemini-2.5-pro",
        "supports_vision": False,
    })
    assert forced_off.supports_vision is False

    forced_on = model_profile_from_config({
        "provider": "custom",
        "model": "private-model",
        "supports_vision": True,
    })
    assert forced_on.supports_vision is True


def test_native_protocol_selection_is_explicit_and_aggregator_safe():
    assert native_provider_protocol({
        "base_url": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-sonnet-4",
    }) is None
    assert native_provider_protocol({
        "provider": "auto",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-2.5-pro",
    }) is None
    assert native_provider_protocol({"provider": "anthropic"}) is None
    assert native_provider_protocol({
        "provider": "anthropic",
        "protocol": "anthropic",
    }) == "anthropic"
    assert native_provider_protocol({"api_style": "google"}) == "gemini"
    assert native_provider_protocol({
        "provider": "anthropic",
        "protocol": "openai-compatible",
    }) is None


def test_native_protocol_rejects_unknown_explicit_protocol():
    with pytest.raises(ValueError, match="unsupported model protocol"):
        native_provider_protocol({"protocol": "mystery-wire"})


def test_normalize_usage_accepts_deepseek_cache_fields():
    usage = normalize_usage(_usage(
        prompt_tokens=100,
        completion_tokens=20,
        prompt_cache_hit_tokens=80,
        prompt_cache_miss_tokens=20,
    ))

    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 20
    assert usage.cache_hit_tokens == 80
    assert usage.cache_miss_tokens == 20


def test_normalize_usage_accepts_chat_and_responses_cached_token_details():
    chat = normalize_usage({
        "prompt_tokens": 100,
        "completion_tokens": 5,
        "prompt_tokens_details": {"cached_tokens": 30},
    })
    assert chat.prompt_tokens == 100
    assert chat.cache_hit_tokens == 30
    assert chat.cache_miss_tokens == 70

    responses = normalize_usage({
        "input_tokens": 120,
        "output_tokens": 6,
        "input_tokens_details": {"cached_tokens": 100},
    })
    assert responses.prompt_tokens == 120
    assert responses.cache_hit_tokens == 100
    assert responses.cache_miss_tokens == 20


def test_normalize_usage_accepts_openai_cache_write_token_details():
    chat = normalize_usage({
        "prompt_tokens": 100,
        "completion_tokens": 5,
        "prompt_tokens_details": {
            "cached_tokens": 30,
            "cache_write_tokens": 40,
        },
    })
    assert chat.cache_hit_tokens == 30
    assert chat.cache_creation_tokens == 40
    assert chat.cache_miss_tokens == 30

    responses = normalize_usage({
        "input_tokens": 120,
        "output_tokens": 6,
        "input_tokens_details": {
            "cached_tokens": 60,
            "cache_write_tokens": 20,
        },
    })
    assert responses.cache_hit_tokens == 60
    assert responses.cache_creation_tokens == 20
    assert responses.cache_miss_tokens == 40

    direct = normalize_usage({
        "prompt_tokens": 50,
        "completion_tokens": 2,
        "cache_write_tokens": 10,
    })
    assert direct.cache_creation_tokens == 10
    assert direct.cache_miss_tokens == 40


def test_normalize_usage_accepts_anthropic_style_cache_read_fields():
    usage = normalize_usage({
        "input_tokens": 20,
        "output_tokens": 5,
        "cache_read_input_tokens": 80,
    })

    assert usage.prompt_tokens == 100
    assert usage.cache_hit_tokens == 80
    assert usage.cache_miss_tokens == 20


def test_adapter_disables_provider_thinking_for_mimo_and_reasonix_only():
    raw = MagicMock()
    adapter = build_model_adapter(raw, {
        "base_url": "https://api.xiaomimimo.com/v1",
        "model": "mimo-v2.5-pro",
    })

    adapter.chat.completions.create(model="mimo-v2.5-pro", messages=[])

    kwargs = raw.chat.completions.create.call_args.kwargs
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}

    raw = MagicMock()
    adapter = build_model_adapter(raw, {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
    })

    adapter.chat.completions.create(model="deepseek-v4-flash", messages=[])

    kwargs = raw.chat.completions.create.call_args.kwargs
    assert "extra_body" not in kwargs


def test_main_agent_adapter_keeps_configured_thinking_until_request_routing(monkeypatch):
    from winpilot.agent import loop

    values = {
        ("api",): {
            "provider": "mimo",
            "base_url": "https://api.xiaomimimo.com/v1",
            "model": "mimo-v2.5",
            "thinking": False,
        },
        ("controller", "native_visual_thinking"): True,
    }
    monkeypatch.setattr(
        loop.CONFIG,
        "get",
        lambda *path, default=None: values.get(tuple(path), default),
    )

    assert loop._main_agent_api_settings()["thinking"] is False

    values[("api",)] = {
        **values[("api",)],
        "model": "mimo-v2.5-pro",
    }
    assert loop._main_agent_api_settings()["thinking"] is False

    values[("api",)] = {
        **values[("api",)],
        "model": "mimo-v2.5",
    }
    values[("controller", "native_visual_thinking")] = False
    assert loop._main_agent_api_settings()["thinking"] is False


def test_native_visual_request_enables_provider_thinking_at_request_boundary(monkeypatch):
    from winpilot.agent import loop

    api = {
        "provider": "mimo",
        "base_url": "https://api.xiaomimimo.com/v1",
        "model": "mimo-v2.5",
        "thinking": False,
        "max_tokens": 4096,
        "temperature": 0.3,
    }
    monkeypatch.setattr(
        loop.CONFIG,
        "get",
        lambda *path, default=None: (
            api if path == ("api",)
            else True if path == ("controller", "native_visual_thinking")
            else default
        ),
    )
    monkeypatch.setattr(loop, "_main_model_has_vision", lambda: True)

    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[])

    runner = loop.AgentRunner("operate the current visible application")
    runner._visual_closed_loop_active = True
    runner.messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": runner.task},
    ]
    runner._trace_debug = lambda *_args, **_kwargs: None
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )

    assert runner._chat(client, []) is not None
    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}
    assert captured["max_completion_tokens"] == 4096
    assert "max_tokens" not in captured
    assert "temperature" not in captured
    assert runner._last_llm_thinking_enabled is True


def test_native_visual_request_skips_text_only_escalation_model(monkeypatch):
    from winpilot.agent import loop

    api = {
        "provider": "mimo",
        "base_url": "https://api.xiaomimimo.com/v1",
        "model": "mimo-v2.5",
        "thinking": False,
        "max_tokens": 4096,
        "temperature": 0.3,
    }

    def configured(*path, default=None):
        if path == ("api",):
            return api
        if path == ("agent", "escalation_model"):
            return "mimo-v2.5-pro"
        if path == ("controller", "native_visual_thinking"):
            return True
        return default

    monkeypatch.setattr(loop.CONFIG, "get", configured)
    monkeypatch.setattr(loop, "_main_model_has_vision", lambda: True)
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[])

    runner = loop.AgentRunner("operate the current visible application")
    runner._visual_closed_loop_active = True
    runner._escalate_next = True
    runner.messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": [
            {"type": "text", "text": "authoritative frame"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,frame"}},
        ]},
    ]
    runner._trace_debug = lambda *_args, **_kwargs: None
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    )

    assert runner._chat(client, []) is not None
    assert captured["model"] == "mimo-v2.5"
    assert runner._last_llm_model == "mimo-v2.5"
    assert runner._escalate_next is False


def test_thinking_tool_history_can_preserve_complete_reasoning_content():
    from winpilot.agent.loop import _sanitize_assistant

    call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="click", arguments='{"frame_x":500,"frame_y":500}'),
    )
    message = SimpleNamespace(
        content="",
        tool_calls=[call],
        reasoning_content="complete provider reasoning",
    )

    preserved = _sanitize_assistant(message, preserve_reasoning=True)
    stripped = _sanitize_assistant(message)

    assert preserved["reasoning_content"] == "complete provider reasoning"
    assert "reasoning_content" not in stripped


def test_cost_estimation_is_provider_scoped():
    api = {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "pricing": {
            "provider": "deepseek",
            "currency": "CNY",
            "input_per_m": 1.0,
            "cache_hit_input_per_m": 0.02,
            "output_per_m": 2.0,
        },
    }
    profile = model_profile_from_config(api)
    total = empty_usage_total()

    step = accumulate_usage(
        total,
        SimpleNamespace(usage=_usage(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_cache_hit_tokens=80,
            prompt_cache_miss_tokens=20,
        )),
        profile=profile,
        api=api,
    )

    assert step is not None
    assert step["cost_estimated"] is True
    assert step["cost_cny"] == 0.0001
    assert total["cost_estimated"] is True
    assert total["cost_cny"] == 0.0001

    default_pricing = pricing_from_config({}, profile)
    assert default_pricing.input_per_m == 1.0
    assert default_pricing.cache_hit_input_per_m == 0.02
    assert default_pricing.output_per_m == 2.0

    mimo_api = {
        "base_url": "https://api.xiaomimimo.com/v1",
        "model": "mimo-v2.5-pro",
        "pricing": api["pricing"],
    }
    mimo_profile = model_profile_from_config(mimo_api)
    assert pricing_from_config(mimo_api, mimo_profile).enabled is False
