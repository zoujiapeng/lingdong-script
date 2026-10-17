"""Thin model/provider adapter for WinPilot's OpenAI-compatible chat path.

WinPilot currently talks to Chat Completions-compatible endpoints directly.
This module keeps that path intact while centralizing provider inference,
model capability checks, provider-specific request extras, and usage/cost
normalization. It is intentionally not a proxy service and does not touch the
GUI controller.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any


_THINKING_DISABLED_BY_DEFAULT = {"reasonix"}
# Reasoning models that emit a long ``reasoning_content`` (counted against the
# completion budget) before any tool call.  With a bounded ``max_tokens`` the
# reasoning alone can consume the whole budget and the turn finishes with
# ``finish_reason=length`` and zero tool calls, so the Agent never acts.  These
# providers therefore default to non-reasoning unless the operator explicitly
# enables thinking.  This mirrors the VLM workaround in ``perception/vlm.py``.
_THINKING_OVERSPEND_PROVIDERS = {"mimo"}
_VISION_MODEL_MARKERS = ("omni", "-vl", "vision", "gpt-4o")
_DEEPSEEK_VISION_MODELS = {"deepseek-v4-flash-vision-exp", "deepseek-flash"}
_NATIVE_PROTOCOL_ALIASES = {
    "openai_responses": "openai_responses",
    "responses": "openai_responses",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "gemini": "gemini",
    "google": "gemini",
}
_CHAT_PROTOCOL_ALIASES = {
    "chat",
    "chat_completions",
    "openai_chat",
    "openai_compatible",
}


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _nested(obj: Any, *path: str) -> Any:
    node = obj
    for key in path:
        node = _get(node, key, None)
        if node is None:
            return None
    return node


def _to_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        if value is not None:
            return _to_int(value)
    return None


def _api_dict(api: Any) -> dict[str, Any]:
    if isinstance(api, dict):
        return api
    return {"model": api} if api else {}


def infer_provider(api: dict[str, Any] | Any) -> str:
    """Infer provider family from explicit config, base URL, or model name."""
    api = _api_dict(api)
    explicit = _lower(api.get("provider") or "auto")
    if explicit and explicit != "auto":
        return explicit

    model = _lower(api.get("model"))
    base_url = _lower(api.get("base_url"))
    haystack = f"{base_url} {model}"
    if "reasonix" in haystack:
        return "reasonix"
    if "xiaomimimo" in haystack or "mimo" in haystack:
        return "mimo"
    if "deepseek" in haystack:
        return "deepseek"
    if "anthropic" in haystack or "claude" in model:
        return "anthropic"
    if "generativelanguage.googleapis.com" in base_url or "gemini" in model:
        return "gemini"
    if "openai" in haystack:
        return "openai"
    return "openai_compatible"


def native_provider_protocol(api: dict[str, Any] | Any) -> str | None:
    """Return an explicitly selected native wire protocol, if any.

    Model names and base URLs are deliberately not used here: aggregators can
    expose Claude or Gemini models through an OpenAI-compatible endpoint.  A
    native protocol is selected only by ``api.protocol``/``api.api_style``;
    ``api.provider`` remains metadata because aggregators may use a provider
    family while still exposing an OpenAI-compatible wire contract.
    """
    api = _api_dict(api)
    raw_protocol = api.get("protocol") or api.get("api_style")
    if raw_protocol is not None:
        key = _lower(raw_protocol).replace("-", "_")
        if key in _CHAT_PROTOCOL_ALIASES:
            return None
        native = _NATIVE_PROTOCOL_ALIASES.get(key)
        if native is None:
            raise ValueError(f"unsupported model protocol {raw_protocol!r}")
        return native

    return None


def model_supports_vision(
    model: str,
    provider: str = "",
    explicit: bool | None = None,
) -> bool:
    """Return true only for model names that explicitly advertise vision."""
    if explicit is not None:
        return explicit
    name = _lower(model)
    family = _lower(provider)
    # MiMo v2.5's public OpenAI-compatible endpoint accepts image_url content
    # even though the model name does not contain an omni/vision marker. This
    # was verified against the live endpoint; ASR/TTS variants and v2.5-pro
    # are intentionally not inferred from that result.
    if name == "mimo-v2.5":
        return True
    # DeepSeek exposes vision on one explicit experimental model. Other
    # DeepSeek V4 aliases still reject image input, so keep this family
    # fail-closed instead of relying on the generic ``vision`` name marker.
    if family == "deepseek":
        return name in _DEEPSEEK_VISION_MODELS
    if family in {"anthropic", "claude"} and re.match(
        r"^claude-(?:(?:opus|sonnet|haiku)-)?[345](?:[.-]|$)", name
    ):
        return True
    if family in {"gemini", "google"} and re.match(
        r"^gemini-(?:1\.5|[2-9](?:\.\d+)?)(?:-|$)", name
    ):
        return True
    return any(marker in name for marker in _VISION_MODEL_MARKERS)


@dataclass(frozen=True)
class ModelProfile:
    provider: str
    base_url: str
    model: str
    supports_vision: bool

    @property
    def disables_thinking_by_default(self) -> bool:
        return self.provider in _THINKING_DISABLED_BY_DEFAULT


def model_profile_from_config(api: dict[str, Any] | Any) -> ModelProfile:
    api = _api_dict(api)
    provider = infer_provider(api)
    model = str(api.get("model") or "")
    explicit_vision = api.get("supports_vision")
    if not isinstance(explicit_vision, bool):
        explicit_vision = None
    return ModelProfile(
        provider=provider,
        base_url=str(api.get("base_url") or ""),
        model=model,
        supports_vision=model_supports_vision(model, provider, explicit_vision),
    )


def request_extra_body(api: dict[str, Any]) -> dict[str, Any]:
    """Return only provider extras the operator supplied explicitly.

    ``False``/missing leaves a provider at its native default, ``True``
    explicitly enables reasoning, and an explicit object still passes through
    unchanged.  One exception: providers in ``_THINKING_OVERSPEND_PROVIDERS``
    default to reasoning *off* when the operator left ``thinking`` unset,
    because their reasoning otherwise consumes the entire completion budget and
    the turn emits no tool call.  Operators can still force it on with
    ``thinking: true`` or an explicit object.
    """
    body: dict[str, Any] = {}
    thinking = api.get("thinking", None)
    if thinking is True:
        body["thinking"] = {"type": "enabled"}
    elif isinstance(thinking, dict):
        body["thinking"] = thinking
    elif thinking in (False, None) and _lower(api.get("provider")) in (
        _THINKING_OVERSPEND_PROVIDERS
    ):
        body["thinking"] = {"type": "disabled"}

    extra = api.get("extra_body")
    if isinstance(extra, dict):
        body.update(extra)
    return body


def _contains_visual_input(messages: Any) -> bool:
    """Whether one Chat Completions request contains an actual image."""

    if not isinstance(messages, (list, tuple)):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, (list, tuple)):
            continue
        for item in content:
            if (
                isinstance(item, dict)
                and item.get("type") in {"image_url", "input_image"}
            ):
                return True
    return False


class _CompletionsFacade:
    def __init__(self, adapter: "OpenAICompatibleAdapter") -> None:
        self._adapter = adapter

    def create(self, **kwargs: Any) -> Any:
        return self._adapter.create_chat_completion(**kwargs)


class OpenAICompatibleAdapter:
    """Small facade that preserves ``client.chat.completions.create`` shape."""

    def __init__(self, client: Any, api: dict[str, Any]) -> None:
        self.client = client
        self.api = dict(api or {})
        self.profile = model_profile_from_config(self.api)
        self.chat = SimpleNamespace(completions=_CompletionsFacade(self))

    def prepare_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        """Build the exact provider request before Runtime freezes it."""
        params = dict(kwargs)
        params.setdefault("model", self.profile.model)
        extra = request_extra_body(self.api)
        if (
            "thinking" not in extra
            and self.profile.disables_thinking_by_default
            and _contains_visual_input(params.get("messages"))
        ):
            # These providers default to a fast non-reasoning path.  A visual
            # Actor request must not silently lose the model's native spatial
            # reasoning merely because config.api.thinking is false/missing.
            extra["thinking"] = {"type": "enabled"}
        if extra:
            existing = params.get("extra_body")
            params["extra_body"] = {
                **extra,
                **(existing if isinstance(existing, dict) else {}),
            }
        return params

    def send_prepared_chat_completion(self, params: dict[str, Any]) -> Any:
        return self.client.chat.completions.create(**params)

    def create_chat_completion(self, **kwargs: Any) -> Any:
        return self.send_prepared_chat_completion(
            self.prepare_chat_completion(**kwargs)
        )


def build_model_adapter(client: Any, api: dict[str, Any]) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(client, api)


@dataclass(frozen=True)
class UsagePricing:
    provider: str = ""
    currency: str = "CNY"
    input_per_m: float | None = None
    cache_hit_input_per_m: float | None = None
    cache_creation_input_per_m: float | None = None
    output_per_m: float | None = None

    @property
    def enabled(self) -> bool:
        return self.input_per_m is not None and self.output_per_m is not None


@dataclass(frozen=True)
class NormalizedUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0

    def empty(self) -> bool:
        return (
            self.prompt_tokens == 0
            and self.completion_tokens == 0
            and self.cache_hit_tokens == 0
            and self.cache_creation_tokens == 0
        )


def _usage_obj(resp_or_usage: Any) -> Any:
    if isinstance(resp_or_usage, dict) and "usage" in resp_or_usage:
        return resp_or_usage.get("usage")
    return getattr(resp_or_usage, "usage", resp_or_usage)


def normalize_usage(resp_or_usage: Any) -> NormalizedUsage:
    """Normalize common OpenAI-compatible and Anthropic-style usage fields."""
    usage = _usage_obj(resp_or_usage)
    if not usage:
        return NormalizedUsage()

    prompt_raw = _get(usage, "prompt_tokens", None)
    input_raw = _get(usage, "input_tokens", None)
    completion = _first_int(
        _get(usage, "completion_tokens", None),
        _get(usage, "output_tokens", None),
    ) or 0

    deepseek_cache_hit = _get(usage, "prompt_cache_hit_tokens", None)
    deepseek_cache_miss = _get(usage, "prompt_cache_miss_tokens", None)
    direct_cache_read = _get(usage, "cache_read_input_tokens", None)
    nested_input_cache = _nested(usage, "input_tokens_details", "cached_tokens")
    nested_prompt_cache = _nested(usage, "prompt_tokens_details", "cached_tokens")
    cache_hit = _first_int(
        deepseek_cache_hit,
        direct_cache_read,
        nested_input_cache,
        nested_prompt_cache,
    ) or 0
    cache_creation = _first_int(
        _get(usage, "cache_creation_input_tokens", None),
        _get(usage, "cache_write_tokens", None),
        _nested(usage, "input_tokens_details", "cache_write_tokens"),
        _nested(usage, "prompt_tokens_details", "cache_write_tokens"),
    ) or 0

    if prompt_raw is not None:
        prompt = _to_int(prompt_raw)
    elif input_raw is not None:
        input_tokens = _to_int(input_raw)
        # Anthropic-style direct cache fields usually exclude cache reads from
        # input_tokens; OpenAI nested details include them in input_tokens.
        if direct_cache_read is not None and nested_input_cache is None and nested_prompt_cache is None:
            prompt = input_tokens + cache_hit + cache_creation
        else:
            prompt = input_tokens
    elif deepseek_cache_hit is not None or deepseek_cache_miss is not None:
        prompt = cache_hit + _to_int(deepseek_cache_miss) + cache_creation
    else:
        prompt = 0

    if deepseek_cache_miss is not None:
        cache_miss = _to_int(deepseek_cache_miss)
    elif direct_cache_read is not None and prompt_raw is None and input_raw is not None:
        cache_miss = _to_int(input_raw)
    elif cache_hit or cache_creation:
        cache_miss = max(0, prompt - cache_hit - cache_creation)
    else:
        cache_miss = prompt

    reasoning = _first_int(
        _get(usage, "reasoning_tokens", None),
        _nested(usage, "completion_tokens_details", "reasoning_tokens"),
        _nested(usage, "output_tokens_details", "reasoning_tokens"),
    ) or 0
    total = _first_int(_get(usage, "total_tokens", None)) or (prompt + completion)
    return NormalizedUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cache_hit_tokens=cache_hit,
        cache_miss_tokens=cache_miss,
        cache_creation_tokens=cache_creation,
        reasoning_tokens=reasoning,
        total_tokens=total,
    )


def pricing_from_config(api: dict[str, Any], profile: ModelProfile) -> UsagePricing:
    raw = api.get("pricing")
    if not isinstance(raw, dict):
        raw = {}

    if not raw and profile.provider == "deepseek":
        # DeepSeek V4-Flash public CNY rates checked 2026-07-14.
        # https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
        raw = {
            "provider": "deepseek",
            "currency": "CNY",
            "input_per_m": 1.0,
            "cache_hit_input_per_m": 0.02,
            "output_per_m": 2.0,
        }

    provider_filter = _lower(raw.get("provider") or "")
    if provider_filter and provider_filter != "auto" and provider_filter != profile.provider:
        return UsagePricing(provider=provider_filter, currency=str(raw.get("currency") or "CNY"))

    input_per_m = _to_float(
        raw.get("cache_miss_input_per_m", raw.get("input_per_m"))
    )
    cache_hit_per_m = _to_float(raw.get("cache_hit_input_per_m"))
    cache_creation_per_m = _to_float(raw.get("cache_creation_input_per_m", input_per_m))
    output_per_m = _to_float(raw.get("output_per_m"))
    return UsagePricing(
        provider=provider_filter or profile.provider,
        currency=str(raw.get("currency") or "CNY"),
        input_per_m=input_per_m,
        cache_hit_input_per_m=cache_hit_per_m,
        cache_creation_input_per_m=cache_creation_per_m,
        output_per_m=output_per_m,
    )


def estimate_cost(usage: NormalizedUsage, pricing: UsagePricing) -> float | None:
    if not pricing.enabled:
        return None
    cache_hit_rate = (
        pricing.cache_hit_input_per_m
        if pricing.cache_hit_input_per_m is not None
        else pricing.input_per_m
    )
    cache_creation_rate = (
        pricing.cache_creation_input_per_m
        if pricing.cache_creation_input_per_m is not None
        else pricing.input_per_m
    )
    assert pricing.input_per_m is not None
    assert pricing.output_per_m is not None
    return (
        usage.cache_miss_tokens / 1_000_000 * pricing.input_per_m
        + usage.cache_hit_tokens / 1_000_000 * (cache_hit_rate or 0.0)
        + usage.cache_creation_tokens / 1_000_000 * (cache_creation_rate or 0.0)
        + usage.completion_tokens / 1_000_000 * pricing.output_per_m
    )


def empty_usage_total() -> dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "cache_creation_tokens": 0,
        "reasoning_tokens": 0,
        "cost_cny": 0.0,
        "cost_estimated": False,
        "cost_currency": "CNY",
    }


def usage_event_dict(
    usage: NormalizedUsage,
    *,
    cost: float | None,
    pricing: UsagePricing,
    profile: ModelProfile,
) -> dict[str, Any]:
    return {
        "provider": profile.provider,
        "model": profile.model,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "cache_hit_tokens": usage.cache_hit_tokens,
        "cache_miss_tokens": usage.cache_miss_tokens,
        "cache_creation_tokens": usage.cache_creation_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "cost_cny": round(cost, 4) if cost is not None and pricing.currency.upper() == "CNY" else None,
        "cost_estimated": cost is not None,
        "cost_currency": pricing.currency,
    }


def accumulate_usage(
    total: dict[str, Any],
    resp: Any,
    *,
    profile: ModelProfile,
    api: dict[str, Any],
) -> dict[str, Any] | None:
    usage = normalize_usage(resp)
    if usage.empty():
        return None
    pricing = pricing_from_config(api, profile)
    cost = estimate_cost(usage, pricing)
    step = usage_event_dict(usage, cost=cost, pricing=pricing, profile=profile)

    for key in (
        "prompt_tokens",
        "completion_tokens",
        "cache_hit_tokens",
        "cache_miss_tokens",
        "cache_creation_tokens",
        "reasoning_tokens",
    ):
        total[key] = int(total.get(key) or 0) + int(step.get(key) or 0)

    total["provider"] = profile.provider
    total["model"] = profile.model
    total["cost_currency"] = pricing.currency
    if cost is not None and pricing.currency.upper() == "CNY":
        total["cost_cny"] = round(float(total.get("cost_cny") or 0.0) + cost, 4)
        total["cost_estimated"] = True
    else:
        total.setdefault("cost_cny", 0.0)
        total.setdefault("cost_estimated", False)
    return step
