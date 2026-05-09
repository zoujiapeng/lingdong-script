"""
Abstract base for LLM providers.

All providers implement chat() and optionally chat_stream().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """Abstract base for LLM backends.

    Subclasses must implement chat(). The constructor should accept
    api_key, api_base, and model as keyword arguments.

    Typical usage:
        provider = DeepSeekProvider(api_key="sk-...", model="deepseek-chat")
        response = provider.chat([{"role": "user", "content": "Hello"}])
    """

    def __init__(
        self,
        api_key: str = "",
        api_base: str = "",
        model: str = "",
    ):
        self.api_key = api_key
        self.api_base = api_base
        self.model = model

    @abstractmethod
    def chat(self, messages: list[dict], **kwargs: Any) -> dict:
        """Send a chat completion request and return the parsed response.

        Args:
            messages: List of {"role": ..., "content": ...} dicts.
            **kwargs: Overrides for temperature, max_tokens, etc.

        Returns:
            A dict with at minimum {"content": str, ...}.
            May include additional metadata (model, usage, etc.).
        """
        ...

    def chat_stream(self, messages: list[dict], **kwargs: Any) -> Any:
        """Optional streaming variant. Raises NotImplementedError by default."""
        raise NotImplementedError(f"{type(self).__name__} does not support streaming")
