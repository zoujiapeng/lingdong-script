"""OpenAI-compatible provider for the independent visual verifier role."""
from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from ...model_adapter import build_model_adapter
from ...modeling import (
    ModelCallOutcome,
    ModelCallRuntime,
    ModelRetryPolicy,
    create_openai_client,
    invoke_chat_completion,
)
from .contracts import (
    VERIFIER_ASSESSMENT_FORMAT,
    InvalidVerificationSchema,
    VerifierAssessment,
)
from .visual_model import VisualVerifierContractError, VisualVerifierModelRequest


VISUAL_VERIFIER_ROLE = "visual_verifier"
VISUAL_ASSESSMENT_TOOL = "submit_visual_assessment"
VISUAL_VERIFIER_SYSTEM_CONTRACT = (
    "Act as an independent read-only visual verifier. Judge only the supplied "
    "image against the stated conditions and call submit_visual_assessment exactly "
    "once. Use criterion text verbatim; encode an absent forbidden effect as "
    "forbidden_effect_absent:<effect>. Never propose actions or answer with text."
)


class VisualVerifierModelError(VisualVerifierContractError):
    """The provider response is not one strict, identity-free assessment."""


_ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "format": {"const": VERIFIER_ASSESSMENT_FORMAT},
        "verified": {"type": "boolean"},
        "satisfied_conditions": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
        "missing_conditions": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
        "contradictions": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
        "confidence": {
            "oneOf": [
                {"type": "number", "minimum": 0, "maximum": 1},
                {"type": "null"},
            ],
        },
        "concise_reason": {"type": "string", "minLength": 1},
    },
    "required": [
        "format",
        "verified",
        "satisfied_conditions",
        "missing_conditions",
        "contradictions",
        "confidence",
        "concise_reason",
    ],
}


def _member(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _assessment_from_response(response: Any) -> VerifierAssessment:
    choices = _member(response, "choices")
    if (
        isinstance(choices, (str, bytes, Mapping))
        or not isinstance(choices, Sequence)
        or len(choices) != 1
    ):
        raise VisualVerifierModelError(
            "visual verifier response requires exactly one choice"
        )
    message = _member(choices[0], "message")
    if message is None:
        raise VisualVerifierModelError("visual verifier response has no message")
    content = _member(message, "content")
    if content is not None and (not isinstance(content, str) or content.strip()):
        raise VisualVerifierModelError("visual verifier must not answer with text")
    calls = _member(message, "tool_calls")
    if (
        isinstance(calls, (str, bytes, Mapping))
        or not isinstance(calls, Sequence)
        or len(calls) != 1
    ):
        raise VisualVerifierModelError(
            "visual verifier must return exactly one tool call"
        )
    call = calls[0]
    if _member(call, "type") != "function":
        raise VisualVerifierModelError("visual verifier tool call must be a function")
    function = _member(call, "function")
    if _member(function, "name") != VISUAL_ASSESSMENT_TOOL:
        raise VisualVerifierModelError("visual verifier called an unknown tool")
    arguments = _member(function, "arguments")
    if type(arguments) is not str:
        raise VisualVerifierModelError("visual verifier arguments must be JSON text")
    try:
        return VerifierAssessment.from_json(arguments)
    except InvalidVerificationSchema as exc:
        raise VisualVerifierModelError(
            "visual verifier returned malformed assessment arguments"
        ) from exc


class OpenAIVisualVerifierModel:
    """One isolated, read-only assessment over one current image."""

    def __init__(
        self,
        client: Any,
        *,
        model: str,
        retry_policy: ModelRetryPolicy | None = None,
        call_runtime: ModelCallRuntime | None = None,
        decision_id_factory: Callable[[], str] | None = None,
        max_tokens: int = 900,
    ) -> None:
        self._client = client
        self._model = str(model or "").strip()
        if not self._model:
            raise VisualVerifierModelError("visual verifier model is required")
        self._retry_policy = retry_policy or ModelRetryPolicy(max_attempts=1)
        self._call_runtime = call_runtime or ModelCallRuntime()
        self._decision_id_factory = decision_id_factory or (
            lambda: f"visual-verifier-{uuid.uuid4().hex}"
        )
        self._max_tokens = max(256, min(4096, int(max_tokens)))
        self.last_outcome: ModelCallOutcome | None = None

    @classmethod
    def from_api_config(cls, api: Mapping[str, Any]) -> "OpenAIVisualVerifierModel":
        values = dict(api or {})
        base_url = str(values.get("base_url") or "").strip()
        api_key = str(values.get("api_key") or "").strip()
        model = str(values.get("model") or "").strip()
        if not (base_url and api_key and model):
            raise VisualVerifierModelError("visual verifier provider is not configured")
        try:
            request_timeout = max(
                5.0, min(180.0, float(values.get("request_timeout_s", 75) or 75))
            )
            total_timeout = max(
                10.0,
                min(300.0, float(values.get("request_total_timeout_s", 120) or 120)),
            )
            configured_retries = int(values.get("max_retries", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise VisualVerifierModelError(
                "visual verifier provider timeout/retry config is invalid"
            ) from exc
        raw_client = create_openai_client(
            base_url=base_url,
            api_key=api_key,
            timeout=request_timeout,
        )
        isolated_api = {
            "provider": values.get("provider", "auto"),
            "model": model,
            "thinking": values.get("thinking"),
        }
        if isinstance(values.get("extra_body"), Mapping):
            isolated_api["extra_body"] = dict(values["extra_body"])
        return cls(
            build_model_adapter(raw_client, isolated_api),
            model=model,
            retry_policy=ModelRetryPolicy(
                max_attempts=max(1, min(5, configured_retries + 1)),
                total_timeout_s=total_timeout,
                initial_backoff_s=1.0,
                backoff_multiplier=2.0,
                max_backoff_s=10.0,
            ),
        )

    def assess(
        self,
        request: VisualVerifierModelRequest,
        *,
        stop_event: threading.Event | None = None,
    ) -> VerifierAssessment:
        if not isinstance(request, VisualVerifierModelRequest):
            raise VisualVerifierModelError("visual verifier request has the wrong type")
        if stop_event is not None and not isinstance(stop_event, threading.Event):
            raise VisualVerifierModelError("stop_event must be threading.Event or null")
        visible_input = {
            "desired_state": request.desired_state,
            "acceptance_criteria": list(request.acceptance_criteria),
            "forbidden_effects": list(request.forbidden_effects),
            "submitted_image_size": {
                "width": request.submitted_size[0],
                "height": request.submitted_size[1],
            },
        }
        provider_request = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": VISUAL_VERIFIER_SYSTEM_CONTRACT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                visible_input,
                                ensure_ascii=False,
                                allow_nan=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{request.image_b64}"
                            },
                        },
                    ],
                },
            ],
            "tools": [{
                "type": "function",
                "function": {
                    "name": VISUAL_ASSESSMENT_TOOL,
                    "parameters": _ASSESSMENT_SCHEMA,
                },
            }],
            "tool_choice": "required",
            "temperature": 0.0,
            "max_tokens": self._max_tokens,
        }
        outcome = invoke_chat_completion(
            self._client,
            decision_id=self._decision_id_factory(),
            model_role=VISUAL_VERIFIER_ROLE,
            purpose="independent_visual_assessment",
            provider_request=provider_request,
            policy=self._retry_policy,
            stop_event=stop_event,
            runtime=self._call_runtime,
        )
        self.last_outcome = outcome
        return _assessment_from_response(outcome.response)


__all__ = [
    "VISUAL_VERIFIER_ROLE",
    "VISUAL_ASSESSMENT_TOOL",
    "VISUAL_VERIFIER_SYSTEM_CONTRACT",
    "VisualVerifierModelError",
    "OpenAIVisualVerifierModel",
]
