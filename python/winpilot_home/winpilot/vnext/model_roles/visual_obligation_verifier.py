"""Independent read-only Visual ObligationVerifier model role."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import threading
from typing import Any, Callable

from ...modeling import (
    ModelAttempt,
    ModelCallRuntime,
    ModelRetryPolicy,
    invoke_chat_completion,
)
from ..runtime.contracts import RuntimeVerifierTurn
from ..verification import (
    VERIFIER_ASSESSMENT_FORMAT,
    VerifierAssessment,
)
from .common import (
    LiveModelRoleError,
    member,
    require_jpeg,
    require_no_tool_calls,
    require_positive_int,
    require_positive_timeout,
    require_text,
    response_message,
    strict_json_object,
)


VISUAL_VERIFIER_MODEL_ROLE = "vnext_visual_obligation_verifier"
VISUAL_VERIFIER_KIND = "independent_visual_model"
VISUAL_VERIFIER_SYSTEM_CONTRACT = """\
You are the independent read-only vNext Visual ObligationVerifier. Assess only
the Runtime-bound subject, current frame, and bound evidence in this request.
You have no tools and no mutation permission. Return only the strict assessment
JSON object. For each required condition, return one condition_results status
in the same order. Do not provide hidden reasoning, new identities, actions,
plans, or instructions."""

_ASSESSMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "format": {
            "type": "string",
            "enum": [VERIFIER_ASSESSMENT_FORMAT],
        },
        "verified": {"type": "boolean"},
        "condition_results": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "satisfied",
                    "missing",
                    "contradicted",
                ],
            },
        },
        "confidence": {
            # JSON cannot preserve whether lexical 1 was intended as a Python
            # float. The canonical wire contract rejects integers, and this
            # canary does not consume confidence, so require null.
            "type": "null",
        },
        "concise_reason": {
            "type": "string",
            "minLength": 1,
        },
    },
    "required": [
        "format",
        "verified",
        "condition_results",
        "confidence",
        "concise_reason",
    ],
}

_ASSESSMENT_FIELDS = frozenset(_ASSESSMENT_SCHEMA["required"])
_CONDITION_STATUSES = frozenset(
    ("satisfied", "missing", "contradicted")
)


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _decision_id(turn: RuntimeVerifierTurn) -> str:
    material = {
        "format": turn.format,
        "request": turn.request.to_dict(),
        "frame": turn.frame.identity.to_dict(),
        "frame_evidence_refs": list(turn.frame.evidence_refs),
        "completed_action_summaries": list(
            turn.completed_action_summaries
        ),
    }
    digest = hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()
    return f"visual-verifier-{digest[:24]}"


def _assessment_from_response(
    response: Any,
    *,
    required_conditions: tuple[str, ...],
) -> VerifierAssessment:
    message = response_message(response)
    require_no_tool_calls(message)
    content = member(message, "content")
    if type(content) is not str or not content.strip():
        raise LiveModelRoleError(
            "Visual ObligationVerifier must return strict JSON text"
        )
    try:
        raw = strict_json_object(
            content,
            "Visual ObligationVerifier assessment",
        )
        fields = frozenset(raw)
        if fields != _ASSESSMENT_FIELDS:
            raise ValueError(
                "fields must be exactly "
                f"{sorted(_ASSESSMENT_FIELDS)!r}"
            )
        if raw["format"] != VERIFIER_ASSESSMENT_FORMAT:
            raise ValueError(
                "format must be "
                f"{VERIFIER_ASSESSMENT_FORMAT!r}"
            )
        verified = raw["verified"]
        if type(verified) is not bool:
            raise ValueError("verified must be a boolean")
        condition_results = raw["condition_results"]
        if type(condition_results) is not list:
            raise ValueError("condition_results must be an array")
        if len(condition_results) != len(required_conditions):
            raise ValueError(
                "condition_results must contain exactly "
                f"{len(required_conditions)} statuses"
            )
        for index, status in enumerate(condition_results):
            if (
                type(status) is not str
                or status not in _CONDITION_STATUSES
            ):
                raise ValueError(
                    "condition_results"
                    f"[{index}] must be satisfied, missing, "
                    "or contradicted"
                )
        all_satisfied = all(
            status == "satisfied" for status in condition_results
        )
        if verified is not all_satisfied:
            raise ValueError(
                "verified must be true exactly when every condition "
                "is satisfied"
            )
        if raw["confidence"] is not None:
            raise ValueError(
                "Visual ObligationVerifier confidence must be null"
            )
        concise_reason = raw["concise_reason"]
        if (
            type(concise_reason) is not str
            or not concise_reason.strip()
        ):
            raise ValueError(
                "concise_reason must be a non-empty string"
            )
        return VerifierAssessment.from_dict(
            {
                "format": VERIFIER_ASSESSMENT_FORMAT,
                "verified": verified,
                "satisfied_conditions": [
                    condition
                    for condition, status in zip(
                        required_conditions,
                        condition_results,
                        strict=True,
                    )
                    if status == "satisfied"
                ],
                "missing_conditions": [
                    condition
                    for condition, status in zip(
                        required_conditions,
                        condition_results,
                        strict=True,
                    )
                    if status == "missing"
                ],
                "contradictions": [
                    condition
                    for condition, status in zip(
                        required_conditions,
                        condition_results,
                        strict=True,
                    )
                    if status == "contradicted"
                ],
                "confidence": None,
                "concise_reason": concise_reason,
            }
        )
    except Exception as exc:
        raise LiveModelRoleError(
            f"Visual ObligationVerifier returned invalid assessment: {exc}"
        ) from exc


class LiveVisualObligationVerifier:
    """A separate model context with no tool or mutation surface."""

    verifier_kind = VISUAL_VERIFIER_KIND

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        runtime: ModelCallRuntime,
        total_timeout_s: float = 120.0,
        max_output_tokens: int = 768,
        on_attempt: Callable[[ModelAttempt], None] | None = None,
    ) -> None:
        if client is None:
            raise LiveModelRoleError("client is required")
        if type(runtime) is not ModelCallRuntime:
            raise LiveModelRoleError("runtime must be ModelCallRuntime")
        self._client = client
        self._model = require_text(model, "model")
        self._runtime = runtime
        self._total_timeout_s = require_positive_timeout(total_timeout_s)
        self._max_output_tokens = require_positive_int(
            max_output_tokens, "max_output_tokens"
        )
        if on_attempt is not None and not callable(on_attempt):
            raise LiveModelRoleError("on_attempt must be callable or null")
        self._on_attempt = on_attempt

    def assess(
        self,
        turn: RuntimeVerifierTurn,
        *,
        stop_event: threading.Event | None = None,
    ) -> VerifierAssessment:
        if type(turn) is not RuntimeVerifierTurn:
            raise LiveModelRoleError(
                "Visual ObligationVerifier requires RuntimeVerifierTurn"
            )
        image_b64, submitted_size = require_jpeg(
            turn.frame.image_b64,
            turn.frame.media_type,
            turn.frame.submitted_size,
        )
        assessment_schema = deepcopy(_ASSESSMENT_SCHEMA)
        required_conditions = list(turn.request.required_conditions)
        condition_results_schema = assessment_schema["properties"][
            "condition_results"
        ]
        condition_results_schema["minItems"] = len(
            required_conditions
        )
        condition_results_schema["maxItems"] = len(
            required_conditions
        )
        context = {
            "format": turn.format,
            "output_schema": assessment_schema,
            "subject": {
                "desired_state": turn.request.desired_state,
                "acceptance_criteria": list(
                    turn.request.acceptance_criteria
                ),
                "forbidden_effects": list(
                    turn.request.forbidden_effects
                ),
                "evidence_policy": (
                    turn.request.evidence_policy.to_dict()
                ),
                "required_conditions": list(
                    required_conditions
                ),
            },
            "frame": {
                "authoritative_current_jpeg": True,
                "submitted_size": list(submitted_size),
            },
            "completed_action_summaries": list(
                turn.completed_action_summaries
            ),
            "evidence": [
                {
                    "kind": ref.kind,
                    "trust": ref.trust,
                    "belongs_to_current_frame": (
                        ref.frame_id in (None, turn.frame.frame_id)
                    ),
                }
                for ref in turn.request.evidence_refs
            ],
        }
        provider_request = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": VISUAL_VERIFIER_SYSTEM_CONTRACT,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": _canonical_json(context),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    "data:image/jpeg;base64,"
                                    f"{image_b64}"
                                )
                            },
                        },
                    ],
                },
            ],
            "response_format": {
                # Portable intersection of the OpenAI and current MiMo Chat
                # Completions surfaces. Exact schema enforcement remains local
                # and call-once; invalid output is never repaired or retried.
                "type": "json_object",
            },
            "temperature": 0.0,
            "max_completion_tokens": self._max_output_tokens,
        }
        outcome = invoke_chat_completion(
            self._client,
            decision_id=_decision_id(turn),
            model_role=VISUAL_VERIFIER_MODEL_ROLE,
            purpose="assess_runtime_bound_visual_obligation",
            provider_request=provider_request,
            policy=ModelRetryPolicy(
                max_attempts=1,
                total_timeout_s=self._total_timeout_s,
            ),
            stop_event=stop_event,
            on_attempt=self._on_attempt,
            runtime=self._runtime,
        )
        return _assessment_from_response(
            outcome.response,
            required_conditions=tuple(required_conditions),
        )


__all__ = [
    "VISUAL_VERIFIER_KIND",
    "VISUAL_VERIFIER_MODEL_ROLE",
    "VISUAL_VERIFIER_SYSTEM_CONTRACT",
    "LiveVisualObligationVerifier",
]
