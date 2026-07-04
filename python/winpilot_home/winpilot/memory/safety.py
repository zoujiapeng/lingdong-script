"""Safety helpers for memory persistence."""
from __future__ import annotations

import re
from typing import Any

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|authorization)\b\s*[:=]\s*([^\s,;]+)"
)
_OPENAI_STYLE_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")


def _known_secret_values() -> list[str]:
    try:
        from ..config import CONFIG

        snapshot = CONFIG.snapshot()
    except Exception:
        return []
    secrets: list[str] = []

    def walk(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, str(child_key))
        elif isinstance(value, str) and len(value) >= 8:
            lowered = key.lower()
            if any(marker in lowered for marker in ("key", "secret", "token", "password")):
                secrets.append(value)

    walk(snapshot)
    return secrets


def sanitize_memory_text(text: Any) -> str:
    """Redact obvious secrets before text is written to long-term memory."""
    out = str(text or "")
    out = _BEARER_TOKEN.sub("Bearer [REDACTED]", out)
    out = _OPENAI_STYLE_KEY.sub("[REDACTED_API_KEY]", out)
    out = _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}=[REDACTED]", out)
    for secret in _known_secret_values():
        if secret and secret in out:
            out = out.replace(secret, "[REDACTED_SECRET]")
    return out


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("api_key", "secret", "token", "password", "passwd")):
                out[key] = "[REDACTED]"
            else:
                out[key] = sanitize_payload(child)
        return out
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return sanitize_memory_text(value)
    return value
