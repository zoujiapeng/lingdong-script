"""One OpenAI-compatible chat-completion transport boundary."""
from __future__ import annotations

import threading
from inspect import getattr_static
from typing import Any, Callable, Mapping

from .contracts import ModelAttempt, ModelRequestEnvelope, ModelRetryPolicy
from .runtime import ModelCallOutcome, ModelCallRuntime


def invoke_chat_completion(
    client: Any,
    *,
    decision_id: str,
    model_role: str,
    purpose: str,
    provider_request: Mapping[str, Any],
    policy: ModelRetryPolicy | None = None,
    stop_event: threading.Event | None = None,
    on_attempt: Callable[[ModelAttempt], None] | None = None,
    runtime: ModelCallRuntime | None = None,
) -> ModelCallOutcome:
    """Freeze once, retry only transport, and leave parsing to the caller."""
    # ``MagicMock`` and some dynamic SDK facades fabricate arbitrary attributes
    # through ``__getattr__``.  Only treat methods that are actually declared on
    # the instance/class as the adapter protocol, otherwise a plain client can
    # be mistaken for an adapter and produce a non-JSON mock request.
    has_prepare = callable(
        getattr_static(client, "prepare_chat_completion", None)
    )
    has_send_prepared = callable(
        getattr_static(client, "send_prepared_chat_completion", None)
    )
    prepare_request = (
        getattr(client, "prepare_chat_completion") if has_prepare else None
    )
    send_prepared = (
        getattr(client, "send_prepared_chat_completion")
        if has_send_prepared
        else None
    )
    if callable(prepare_request) and callable(send_prepared):
        exact_provider_request = prepare_request(**dict(provider_request))
        send_once = send_prepared
    else:
        exact_provider_request = provider_request

        def send_once(request: dict[str, Any]) -> Any:
            return client.chat.completions.create(**request)

    envelope = ModelRequestEnvelope.bind(
        decision_id=decision_id,
        model_role=model_role,
        purpose=purpose,
        provider_request=exact_provider_request,
    )
    call_runtime = runtime or ModelCallRuntime()
    return call_runtime.execute(
        envelope,
        send_once,
        policy=policy,
        stop_event=stop_event,
        on_attempt=on_attempt,
    )


__all__ = ["invoke_chat_completion"]
