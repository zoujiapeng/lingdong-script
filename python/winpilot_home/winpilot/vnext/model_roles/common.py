"""Shared fail-closed helpers for the two isolated live model roles."""
from __future__ import annotations

import base64
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any


MAX_LIVE_MODEL_TOTAL_TIMEOUT_S = 600.0
MAX_LIVE_MODEL_OUTPUT_TOKENS = 16_384
MAX_LIVE_MODEL_JSON_RESPONSE_CHARS = 131_072
MAX_LIVE_MODEL_NAME_CHARS = 256


class LiveModelRoleError(ValueError):
    """A live role request or response violated its bounded contract."""


def require_text(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise LiveModelRoleError(f"{name} must be a non-empty string")
    if len(value.strip()) > MAX_LIVE_MODEL_NAME_CHARS:
        raise LiveModelRoleError(
            f"{name} exceeds {MAX_LIVE_MODEL_NAME_CHARS} characters"
        )
    return value.strip()


def require_positive_int(value: Any, name: str) -> int:
    if (
        type(value) is not int
        or not 1 <= value <= MAX_LIVE_MODEL_OUTPUT_TOKENS
    ):
        raise LiveModelRoleError(
            f"{name} must be in [1, {MAX_LIVE_MODEL_OUTPUT_TOKENS}]"
        )
    return value


def require_positive_timeout(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 < float(value) <= MAX_LIVE_MODEL_TOTAL_TIMEOUT_S
    ):
        raise LiveModelRoleError(
            "total_timeout_s must be in "
            f"(0, {MAX_LIVE_MODEL_TOTAL_TIMEOUT_S:g}]"
        )
    return float(value)


def require_jpeg(
    image_b64: Any,
    image_media_type: Any,
    submitted_size: Any,
) -> tuple[str, tuple[int, int]]:
    if image_media_type != "image/jpeg":
        raise LiveModelRoleError("live visual roles accept image/jpeg only")
    if (
        type(submitted_size) is not tuple
        or len(submitted_size) != 2
        or any(type(value) is not int or value <= 0 for value in submitted_size)
    ):
        raise LiveModelRoleError(
            "submitted_size must be a positive integer pair"
        )
    if type(image_b64) is not str or not image_b64:
        raise LiveModelRoleError("image_b64 must be a non-empty string")
    try:
        raw = base64.b64decode(image_b64, validate=True)
    except Exception as exc:
        raise LiveModelRoleError("image_b64 must be strict base64") from exc
    if len(raw) < 4 or raw[:3] != b"\xff\xd8\xff" or raw[-2:] != b"\xff\xd9":
        raise LiveModelRoleError("image_b64 must contain one complete JPEG")
    return image_b64, submitted_size


def member(value: Any, name: str, *, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def strict_json_object(raw: Any, name: str) -> dict[str, Any]:
    if type(raw) is not str:
        raise LiveModelRoleError(f"{name} must be JSON text")
    if len(raw) > MAX_LIVE_MODEL_JSON_RESPONSE_CHARS:
        raise LiveModelRoleError(
            f"{name} exceeds {MAX_LIVE_MODEL_JSON_RESPONSE_CHARS} characters"
        )

    def reject_constant(value: str) -> None:
        raise LiveModelRoleError(
            f"{name} contains non-JSON number {value}"
        )

    def reject_duplicate_pairs(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LiveModelRoleError(
                    f"{name} contains duplicate field {key!r}"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            raw,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_pairs,
        )
    except LiveModelRoleError:
        raise
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError) as exc:
        raise LiveModelRoleError(f"{name} is not valid JSON") from exc
    if type(value) is not dict:
        raise LiveModelRoleError(f"{name} must be a JSON object")
    return value


def response_message(response: Any) -> Any:
    choices = member(response, "choices")
    if (
        isinstance(choices, (str, bytes))
        or not isinstance(choices, Sequence)
        or len(choices) != 1
    ):
        raise LiveModelRoleError(
            "model response must contain exactly one choice"
        )
    message = member(choices[0], "message")
    if message is None:
        raise LiveModelRoleError("model response choice has no message")
    role = member(message, "role")
    if role != "assistant":
        raise LiveModelRoleError("model response message is not assistant")
    return message


def require_empty_content(message: Any) -> None:
    content = member(message, "content")
    if content is not None and (
        type(content) is not str or bool(content.strip())
    ):
        raise LiveModelRoleError(
            "Visual Actor must return only one tool call"
        )


def narration_content(message: Any) -> str:
    """Extract the Actor's brief progress note alongside its tool call.

    The model may narrate what it is doing in the content text while it
    works; the runtime keeps the latest note and surfaces it to the user
    instead of a rigid system string when the run cannot finish.
    """
    content = member(message, "content")
    if content is None:
        return ""
    if type(content) is not str:
        raise LiveModelRoleError("Actor content must be text or null")
    return content.strip()[:_NARRATION_MAX_CHARS]


_NARRATION_MAX_CHARS = 4000


__all__ = [
    "MAX_LIVE_MODEL_JSON_RESPONSE_CHARS",
    "MAX_LIVE_MODEL_NAME_CHARS",
    "MAX_LIVE_MODEL_OUTPUT_TOKENS",
    "MAX_LIVE_MODEL_TOTAL_TIMEOUT_S",
    "LiveModelRoleError",
    "member",
    "narration_content",
    "require_empty_content",
    "require_jpeg",
    "require_positive_int",
    "require_positive_timeout",
    "require_text",
    "response_message",
    "strict_json_object",
]
