"""OpenAI-compatible transport construction with SDK retries disabled."""
from __future__ import annotations

from typing import Any, Callable


def create_openai_client(
    *,
    base_url: str,
    api_key: str,
    timeout: float,
    client_factory: Callable[..., Any] | None = None,
) -> Any:
    if client_factory is None:
        from openai import OpenAI

        client_factory = OpenAI
    return client_factory(
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
        max_retries=0,
    )


__all__ = ["create_openai_client"]
