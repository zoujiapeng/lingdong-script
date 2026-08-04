"""OpenAI-compatible transport construction with SDK retries disabled."""
from __future__ import annotations

import os
from typing import Any, Callable

# Some OpenAI-compatible relays front their API with a browser-only
# Cloudflare signature check (error 1010/403 for library UAs).  Presenting a
# normal browser user agent keeps the same wire protocol while satisfying the
# relay's transport gate; it never changes request semantics.
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

import logging

logging.getLogger("winpilot.transport").warning(
    "openai_transport loaded from %s", __file__
)

# Relay endpoints behind Cloudflare are frequently unreachable on direct
# routes from this network while a local proxy (e.g. Clash on 127.0.0.1:7897)
# succeeds.  When WINPILOT_HTTP_PROXY / WINPILOT_HTTPS_PROXY (or the standard
# HTTP_PROXY / HTTPS_PROXY) are set, route the OpenAI-compatible transport
# through that proxy so relay providers can be used from restricted networks.
# Direct providers (MiMo/DeepSeek) simply leave the variables unset.
def _proxy_env() -> dict[str, str]:
    result: dict[str, str] = {}
    for env_key, proxy_key in (
        ("WINPILOT_HTTP_PROXY", "http"),
        ("HTTP_PROXY", "http"),
        ("WINPILOT_HTTPS_PROXY", "https"),
        ("HTTPS_PROXY", "https"),
    ):
        value = os.environ.get(env_key)
        if value and proxy_key not in result:
            result[proxy_key] = value
    return result


def _proxy_from_config(api: dict[str, Any] | None) -> str | None:
    """Read the proxy URL from the api config (``proxy`` field).

    Electron-spawned Python inherits only a filtered environment, so an
    env-only proxy (HTTPS_PROXY) can silently vanish inside the hosted
    server.  ``api.proxy`` in config.json is the reliable channel.
    """
    if not isinstance(api, dict):
        return None
    value = api.get("proxy")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def create_openai_client(
    *,
    base_url: str,
    api_key: str,
    timeout: float,
    client_factory: Callable[..., Any] | None = None,
    api_config: dict[str, Any] | None = None,
) -> Any:
    if client_factory is None:
        from openai import OpenAI

        client_factory = OpenAI
    kwargs: dict[str, Any] = {
        "base_url": base_url,
        "api_key": api_key,
        "timeout": timeout,
        "max_retries": 0,
        "default_headers": {"User-Agent": _DEFAULT_USER_AGENT},
    }
    proxies = _proxy_env()
    proxy_url = _proxy_from_config(api_config)
    if proxies:
        proxy_url = proxy_url or (proxies.get("https") or proxies.get("http"))
    if proxy_url:
        import httpx

        kwargs["http_client"] = httpx.Client(
            proxy=proxy_url,
            timeout=timeout,
            headers={"User-Agent": _DEFAULT_USER_AGENT},
        )
    return client_factory(**kwargs)


__all__ = ["create_openai_client"]
