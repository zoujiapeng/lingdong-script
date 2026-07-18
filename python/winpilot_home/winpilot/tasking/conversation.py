"""Single-turn conversational responses that must not enter the GUI loop."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..config import CONFIG
from ..model_adapter import build_model_adapter


class ConversationResponseError(RuntimeError):
    failure_type = "conversation_response_failed"


@dataclass(frozen=True)
class ConversationResponse:
    text: str
    model_calls: int
    model_elapsed_ms: int


def generate_conversation_response(
    task: str,
    session_context: list[dict[str, Any]] | None = None,
) -> ConversationResponse:
    api = dict(CONFIG.get("api", default={}) or {})
    if not (
        str(api.get("base_url") or "").strip()
        and str(api.get("model") or "").strip()
        and str(api.get("api_key") or "").strip()
    ):
        raise ConversationResponseError("main text model is not configured")

    from openai import OpenAI

    client = build_model_adapter(
        OpenAI(
            base_url=str(api["base_url"]),
            api_key=str(api["api_key"]),
            timeout=120,
        ),
        api,
    )
    recent = []
    for item in (session_context or [])[-3:]:
        if not isinstance(item, dict):
            continue
        recent.append(
            f"- task={str(item.get('task') or '')[:180]!r}; "
            f"status={str(item.get('status') or '')!r}; "
            f"result={str(item.get('result') or '')[:240]!r}"
        )
    context = "\n".join(recent) or "- no prior task context"
    prompt = (
        "Respond directly to the user's conversational/meta question in the same language. "
        "This route produces text only: do not claim to have opened, clicked, demonstrated, "
        "sent, or changed anything. If the user refers to a previous failure, explain that "
        "specific outcome using the supplied session context. Be concise and honest about "
        "supported versus unavailable capabilities. Return only the response text, with no "
        "JSON and no tool call.\n"
        f"Recent session context:\n{context}\n"
        f"User: {str(task or '').strip()}"
    )
    started = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=str(api["model"]),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=700,
            temperature=0.2,
        )
        text = str(response.choices[0].message.content or "").strip()
    except Exception as exc:
        raise ConversationResponseError(
            f"conversation response generation failed: {exc}"
        ) from exc
    if not text:
        raise ConversationResponseError("conversation model returned an empty response")
    return ConversationResponse(
        text=text,
        model_calls=1,
        model_elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
    )
