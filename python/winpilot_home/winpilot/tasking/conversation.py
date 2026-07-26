"""Single-turn conversational responses that must not enter the GUI loop."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from ..config import CONFIG
from ..model_adapter import build_model_adapter
from ..modeling import (
    ModelRetryPolicy,
    create_openai_client,
    invoke_chat_completion,
)


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
    *,
    stop_event: threading.Event | None = None,
) -> ConversationResponse:
    api = dict(CONFIG.get("api", default={}) or {})
    if not (
        str(api.get("base_url") or "").strip()
        and str(api.get("model") or "").strip()
        and str(api.get("api_key") or "").strip()
    ):
        raise ConversationResponseError("main text model is not configured")

    raw_client = create_openai_client(
        base_url=str(api["base_url"]),
        api_key=str(api["api_key"]),
        timeout=120,
    )
    adapter = build_model_adapter(raw_client, api)
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
        outcome = invoke_chat_completion(
            raw_client,
            decision_id=f"conversation-response:{uuid.uuid4().hex}",
            model_role="conversation_responder",
            purpose="generate_text_only_response",
            provider_request=adapter.prepare_chat_completion(
                model=str(api["model"]),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=700,
                temperature=0.2,
            ),
            policy=ModelRetryPolicy(max_attempts=1),
            stop_event=stop_event,
        )
        response = outcome.response
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
