"""Single owner for provider transport calls and request retry semantics."""

from .contracts import (
    MODEL_REQUEST_FORMAT,
    FollowupKind,
    InvalidModelRequest,
    ModelAttempt,
    ModelRequestEnvelope,
    ModelRetryPolicy,
    RetryKind,
    classify_followup,
)
from .openai_transport import create_openai_client
from .provider_protocols import (
    ProviderHTTPError,
    ProviderProtocolAdapter,
    ProviderProtocolError,
)
from .runtime import ModelCallError, ModelCallOutcome, ModelCallRuntime
from .chat import invoke_chat_completion

__all__ = [
    "MODEL_REQUEST_FORMAT",
    "RetryKind",
    "FollowupKind",
    "InvalidModelRequest",
    "ModelRetryPolicy",
    "ModelRequestEnvelope",
    "ModelAttempt",
    "classify_followup",
    "create_openai_client",
    "ProviderProtocolError",
    "ProviderHTTPError",
    "ProviderProtocolAdapter",
    "ModelCallError",
    "ModelCallOutcome",
    "ModelCallRuntime",
    "invoke_chat_completion",
]
