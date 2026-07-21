"""Run-scoped outbound message drafting and deterministic authorization.

Communication adapters only receive text after this module has produced a
typed draft and the StageRuntime has bound that exact text to the current run.
This prevents a missing quoted payload from degrading into an empty send while
also preventing the generator from inventing personal or time-sensitive facts.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..config import CONFIG
from ..model_adapter import build_model_adapter
from ..modeling import (
    ModelRetryPolicy,
    create_openai_client,
    invoke_chat_completion,
)


class MessageContentGenerationError(RuntimeError):
    failure_type = "message_content_generation_failed"


class MessageDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="", max_length=4000)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    grounded: bool = False
    requires_user_input: bool = False
    missing_information: list[str] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=500)
    source: str = "text_model"
    model_calls: int = 1
    model_elapsed_ms: int = 0


def _json_object(value: str) -> dict[str, Any]:
    raw = str(value or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise MessageContentGenerationError("message model returned no JSON object")
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise MessageContentGenerationError(
            f"message model returned invalid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise MessageContentGenerationError("message model returned a non-object payload")
    return payload


def generate_message_content(
    *,
    instruction: str,
    recipient: str,
    channel: str,
    stop_event: threading.Event | None = None,
) -> MessageDraft:
    """Generate the exact outbound body, or explicitly report missing context."""
    api = dict(CONFIG.get("api", default={}) or {})
    if not (
        str(api.get("base_url") or "").strip()
        and str(api.get("model") or "").strip()
        and str(api.get("api_key") or "").strip()
    ):
        raise MessageContentGenerationError("main text model is not configured")

    raw_client = create_openai_client(
        base_url=str(api["base_url"]),
        api_key=str(api["api_key"]),
        timeout=120,
    )
    adapter = build_model_adapter(raw_client, api)
    prompt = (
        "Produce the exact text body for one outbound message. The user's send "
        "instruction is authorization to draft, not permission to invent facts. "
        "A generic speech act such as a greeting may be realized as a short neutral "
        "message. Preserve any explicit facts and tone. If the requested content "
        "depends on private, current, or omitted information that is not present in "
        "the instruction, set requires_user_input=true, return text as an empty "
        "string, and list the missing information. Do not include a recipient label, "
        "quotation marks, commentary, or JSON markdown in text. Return ONLY JSON with "
        "keys text, confidence, grounded, requires_user_input, missing_information, "
        "rationale. grounded may be true only when every factual claim is supported "
        "by the instruction or is a conventional content-free greeting.\n"
        f"Instruction: {str(instruction or '').strip()}\n"
        f"Recipient: {str(recipient or '').strip()}\n"
        f"Channel: {str(channel or '').strip()}"
    )
    started = time.monotonic()
    try:
        outcome = invoke_chat_completion(
            raw_client,
            decision_id=f"message-content:{uuid.uuid4().hex}",
            model_role="message_content_generator",
            purpose="draft_grounded_outbound_message",
            provider_request=adapter.prepare_chat_completion(
                model=str(api["model"]),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=700,
                temperature=0.1,
            ),
            policy=ModelRetryPolicy(max_attempts=1),
            stop_event=stop_event,
        )
        response = outcome.response
        raw_content = response.choices[0].message.content or ""
    except MessageContentGenerationError:
        raise
    except Exception as exc:
        raise MessageContentGenerationError(
            f"message content generation failed: {exc}"
        ) from exc
    # Parsing is deliberately outside the transport Runtime. A malformed
    # successful response is not a request retry.
    payload = _json_object(raw_content)
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    try:
        return MessageDraft.model_validate({
            **payload,
            "text": str(payload.get("text") or "").strip(),
            "source": "text_model",
            "model_calls": 1,
            "model_elapsed_ms": elapsed_ms,
        })
    except Exception as exc:
        raise MessageContentGenerationError(
            f"message model payload failed validation: {exc}"
        ) from exc


def authorize_message_content(
    *,
    text: str,
    content_source: str,
    generated_draft: MessageDraft | None = None,
) -> dict[str, Any]:
    """Fail closed unless one exact, non-empty, grounded body can be sent."""
    body = str(text or "").strip()
    source = str(content_source or "none").strip().casefold()
    if source == "generate":
        if generated_draft is None:
            return {
                "ok": False,
                "failure_type": "message_draft_missing",
                "error": "generated message draft is missing from current run state",
            }
        if generated_draft.requires_user_input:
            return {
                "ok": False,
                "failure_type": "message_context_missing",
                "error": "message content requires user context before it can be sent",
                "missing_information": list(generated_draft.missing_information),
            }
        if not generated_draft.grounded or generated_draft.confidence < 0.6:
            return {
                "ok": False,
                "failure_type": "message_content_ungrounded",
                "error": "generated message content is not sufficiently grounded",
                "confidence": generated_draft.confidence,
            }
        body = generated_draft.text.strip()
    elif source != "explicit":
        return {
            "ok": False,
            "failure_type": "message_content_source_invalid",
            "error": "outbound message has no explicit or generated content source",
        }
    if not body:
        return {
            "ok": False,
            "failure_type": "empty_message_content",
            "error": "outbound message text is empty",
        }
    return {
        "ok": True,
        "text": body,
        "content_source": source,
        "confidence": (
            generated_draft.confidence if generated_draft is not None else 1.0
        ),
    }
