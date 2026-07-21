"""OpenAI-compatible, tool-free provider for the read-only Task Profiler."""
from __future__ import annotations

import json
import threading
import uuid
from typing import Any, Callable, Mapping

from ...model_adapter import build_model_adapter
from ...modeling import (
    ModelCallOutcome,
    ModelCallRuntime,
    ModelRequestEnvelope,
    ModelRetryPolicy,
    create_openai_client,
)
from .contracts import InvalidProfileSchema
from .profiler import TASK_PROFILER_ROLE, TaskProfilerRequest


class OpenAITaskProfilerModel:
    """One isolated semantic proposal over the unified request runtime."""

    def __init__(
        self,
        client: Any,
        *,
        model: str,
        retry_policy: ModelRetryPolicy | None = None,
        call_runtime: ModelCallRuntime | None = None,
        decision_id_factory: Callable[[], str] | None = None,
        max_tokens: int = 1800,
    ) -> None:
        self._client = client
        self._model = str(model or "").strip()
        if not self._model:
            raise InvalidProfileSchema("Task Profiler model is required")
        self._retry_policy = retry_policy or ModelRetryPolicy(max_attempts=1)
        self._call_runtime = call_runtime or ModelCallRuntime()
        self._decision_id_factory = decision_id_factory or (
            lambda: f"task-profiler-{uuid.uuid4().hex}"
        )
        self._max_tokens = max(256, min(4096, int(max_tokens)))
        self.last_outcome: ModelCallOutcome | None = None

    @classmethod
    def from_api_config(cls, api: Mapping[str, Any]) -> "OpenAITaskProfilerModel":
        values = dict(api or {})
        base_url = str(values.get("base_url") or "").strip()
        api_key = str(values.get("api_key") or "").strip()
        model = str(values.get("model") or "").strip()
        if not (base_url and api_key and model):
            raise InvalidProfileSchema("Task Profiler provider is not configured")
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
            raise InvalidProfileSchema("Task Profiler provider timeout/retry config is invalid") from exc
        raw_client = create_openai_client(
            base_url=base_url,
            api_key=api_key,
            timeout=request_timeout,
        )
        isolated_api = {
            "provider": values.get("provider", "auto"),
            "model": model,
            "thinking": False,
        }
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

    def propose(
        self,
        request: TaskProfilerRequest,
        *,
        stop_event: threading.Event | None = None,
    ) -> Mapping[str, Any]:
        if not isinstance(request, TaskProfilerRequest):
            raise InvalidProfileSchema("Task Profiler request has the wrong type")
        payload = request.model_payload()
        user_payload = {
            "input": payload["input"],
            "response_schema": payload["response_schema"],
        }
        params: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system_contract},
                {
                    "role": "user",
                    "content": json.dumps(
                        user_payload,
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "temperature": 0.0,
            "max_tokens": self._max_tokens,
        }
        prepare = getattr(self._client, "prepare_chat_completion", None)
        send_prepared = getattr(self._client, "send_prepared_chat_completion", None)
        if callable(prepare) and callable(send_prepared):
            provider_request = prepare(**params)
            send_once = send_prepared
        else:
            provider_request = params

            def send_once(value: dict[str, Any]):
                return self._client.chat.completions.create(**value)

        envelope = ModelRequestEnvelope.bind(
            decision_id=self._decision_id_factory(),
            model_role=TASK_PROFILER_ROLE,
            purpose="task_profile_proposal",
            provider_request=provider_request,
        )
        outcome = self._call_runtime.execute(
            envelope,
            send_once,
            policy=self._retry_policy,
            stop_event=stop_event,
        )
        self.last_outcome = outcome
        try:
            raw = outcome.response.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise InvalidProfileSchema("Task Profiler provider response has no content") from exc
        if not isinstance(raw, str) or not raw.strip():
            raise InvalidProfileSchema("Task Profiler provider returned empty content")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InvalidProfileSchema("Task Profiler provider returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise InvalidProfileSchema("Task Profiler provider output must be an object")
        return value


__all__ = ["OpenAITaskProfilerModel"]
