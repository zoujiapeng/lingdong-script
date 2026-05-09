"""
OpenAI LLM provider.

Uses the OpenAI Python SDK or direct HTTP API.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .base import LLMProvider

logger = logging.getLogger("providers.openai")

DEFAULT_API_BASE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o"


class OpenAIProvider(LLMProvider):
    """Provider for OpenAI models (GPT-4o, GPT-4, etc.)."""

    def __init__(
        self,
        api_key: str = "",
        api_base: str = "",
        model: str = "",
    ):
        super().__init__(
            api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
            api_base=(api_base or os.environ.get("OPENAI_API_BASE", "") or DEFAULT_API_BASE).rstrip("/"),
            model=model or DEFAULT_MODEL,
        )

    def chat(self, messages: list[dict], **kwargs: Any) -> dict:
        """Send a chat completion via the OpenAI-compatible HTTP API.

        Falls back to direct HTTP if the openai SDK is not installed.

        Args:
            messages: Chat messages in OpenAI format.
            **kwargs: Supports temperature, max_tokens, response_format.

        Returns:
            Parsed response dict with at least {"content": str}.
        """
        temperature = kwargs.get("temperature", 0.1)
        max_tokens = kwargs.get("max_tokens", 1000)
        response_format = kwargs.get("response_format", None)

        # Try the openai SDK first
        try:
            import openai as _openai

            client = _openai.OpenAI(api_key=self.api_key, base_url=self.api_base)
            params = {
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if response_format:
                params["response_format"] = response_format

            resp = client.chat.completions.create(**params)
            content = resp.choices[0].message.content or ""
            return {
                "content": content,
                "raw_response": resp.model_dump() if hasattr(resp, "model_dump") else {},
                "model": self.model,
            }
        except ImportError:
            logger.debug("openai SDK not installed, falling back to direct HTTP")
        except Exception as e:
            logger.warning("OpenAI SDK call failed, falling back to HTTP: %s", e)

        # Fallback: direct HTTP
        return self._chat_http(messages, temperature, max_tokens, response_format)

    def _chat_http(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        response_format: Any = None,
    ) -> dict:
        """Direct HTTP fallback without the openai SDK."""
        import json
        import re

        import requests as _req

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
                    timeout=90,
                )
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"].strip()
                # Strip markdown code fences if present
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                return {"content": raw, "raw_response": resp.json(), "model": self.model}
            except Exception as e:
                last_error = e
                logger.warning("OpenAI HTTP attempt %d failed: %s", attempt + 1, e)
                if attempt < 2:
                    import time
                    time.sleep(1.5 ** attempt)

        logger.error("OpenAI HTTP failed after 3 attempts: %s", last_error)
        return {"content": "", "error": str(last_error), "model": self.model}
