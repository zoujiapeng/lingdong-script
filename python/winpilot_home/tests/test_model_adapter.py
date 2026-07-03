from types import SimpleNamespace
from unittest.mock import MagicMock

from winpilot.model_adapter import (
    accumulate_usage,
    build_model_adapter,
    empty_usage_total,
    model_profile_from_config,
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


def test_cost_estimation_is_provider_scoped():
    api = {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "pricing": {
            "provider": "deepseek",
            "currency": "CNY",
            "input_per_m": 2.0,
            "cache_hit_input_per_m": 0.2,
            "output_per_m": 8.0,
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
    assert step["cost_cny"] == 0.0002
    assert total["cost_estimated"] is True
    assert total["cost_cny"] == 0.0002

    mimo_api = {
        "base_url": "https://api.xiaomimimo.com/v1",
        "model": "mimo-v2.5-pro",
        "pricing": api["pricing"],
    }
    mimo_profile = model_profile_from_config(mimo_api)
    assert pricing_from_config(mimo_api, mimo_profile).enabled is False
