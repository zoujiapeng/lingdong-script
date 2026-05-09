"""
DeepSeek LLM provider.

Uses the OpenAI-compatible HTTP API at api.deepseek.com/v1.
Supports JSON mode via response_format parameter.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests as _req

from .base import LLMProvider

logger = logging.getLogger("providers.deepseek")

DEFAULT_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


class DeepSeekProvider(LLMProvider):
    """Provider for DeepSeek and compatible OpenAI-like APIs."""

    def __init__(
        self,
        api_key: str = "",
        api_base: str = "",
        model: str = "",
    ):
        super().__init__(
            api_key=api_key,
            api_base=(api_base or DEFAULT_API_BASE).rstrip("/"),
            model=model or DEFAULT_MODEL,
        )

    def chat(self, messages: list[dict], **kwargs: Any) -> dict:
        """Send a chat completion with retry logic.

        Retries up to 3 times with exponential backoff on failure.

        Args:
            messages: Chat messages in OpenAI format.
            **kwargs: Supports temperature, max_tokens, response_format.

        Returns:
            Parsed JSON response with at least {"content": str}.
            On repeated failure, returns a fallback dict.
        """
        temperature = kwargs.get("temperature", 0.1)
        max_tokens = kwargs.get("max_tokens", 1000)
        response_format = kwargs.get("response_format", None)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format

        last_error = None
        for attempt in range(3):
            try:
                resp = _req.post(
                    f"{self.api_base}/chat/completions",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    json=payload,
                    timeout=kwargs.get("timeout", 90),
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                return {"content": raw, "raw_response": resp.json(), "model": self.model}
            except Exception as e:
                last_error = e
                logger.warning("DeepSeek API attempt %d failed: %s", attempt + 1, e)
                if attempt < 2:
                    time.sleep(1.5 ** attempt)

        logger.error("DeepSeek API failed after 3 attempts: %s", last_error)
        return {"content": "", "error": str(last_error), "model": self.model}
