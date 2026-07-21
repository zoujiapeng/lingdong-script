"""The sole transport retry loop for model requests."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .contracts import ModelAttempt, ModelRequestEnvelope, ModelRetryPolicy, RetryKind


@dataclass(frozen=True, slots=True)
class ModelCallOutcome:
    response: Any
    attempts: tuple[ModelAttempt, ...]

    @property
    def request_retry_count(self) -> int:
        return max(0, len(self.attempts) - 1)


class ModelCallError(RuntimeError):
    def __init__(self, message: str, attempts: tuple[ModelAttempt, ...]) -> None:
        super().__init__(message)
        self.attempts = attempts

    @property
    def request_retry_count(self) -> int:
        return max(0, len(self.attempts) - 1)


def _status_code(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
    if value is None:
        response = getattr(exc, "response", None)
        value = getattr(response, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _retryable_transport_error(exc: Exception) -> bool:
    code = _status_code(exc)
    if code is not None:
        return code == 429 or 500 <= code <= 599
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    # Keep this allowlist explicit: matching arbitrary exception messages such
    # as ValueError("connection schema invalid") turns program defects into
    # provider retries and obscures the real failure.
    return type(exc).__name__ in {
        "APITimeoutError",
        "APIConnectionError",
        "ConnectError",
        "ConnectTimeout",
        "ReadError",
        "ReadTimeout",
        "RemoteProtocolError",
        "TimeoutException",
    }


def _notify_attempt(
    observer: Callable[[ModelAttempt], None] | None,
    attempt: ModelAttempt,
) -> None:
    if observer is None:
        return
    try:
        observer(attempt)
    except Exception:
        # Telemetry is a sidecar. An observer defect must never resend a
        # completed request or replace the provider's real exception.
        return


class ModelCallRuntime:
    """Retries only an exact frozen provider request before any consumer acts."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleeper = sleeper

    def execute(
        self,
        envelope: ModelRequestEnvelope,
        send_once: Callable[[dict[str, Any]], Any],
        *,
        policy: ModelRetryPolicy | None = None,
        stop_event: threading.Event | None = None,
        on_attempt: Callable[[ModelAttempt], None] | None = None,
    ) -> ModelCallOutcome:
        policy = policy or ModelRetryPolicy()
        started = self._clock()
        deadline = started + float(policy.total_timeout_s)
        attempts: list[ModelAttempt] = []
        last_exc: Exception | None = None
        backoff = float(policy.initial_backoff_s)

        for attempt_number in range(1, policy.max_attempts + 1):
            if stop_event is not None and stop_event.is_set():
                raise ModelCallError("model request canceled", tuple(attempts))
            if self._clock() >= deadline:
                raise ModelCallError("model request total deadline exceeded", tuple(attempts))
            attempt_started = self._clock()
            try:
                # Decode a fresh copy from the immutable canonical wire value.
                # Every request retry is therefore byte-identical in meaning,
                # even if a transport mutates the mapping it receives.
                response = send_once(envelope.provider_request())
                attempt = ModelAttempt(
                    attempt=attempt_number,
                    kind=(RetryKind.INITIAL_REQUEST if attempt_number == 1 else RetryKind.REQUEST_RETRY),
                    request_fingerprint=envelope.request_fingerprint,
                    elapsed_ms=max(0, int((self._clock() - attempt_started) * 1000)),
                    ok=True,
                )
                attempts.append(attempt)
                _notify_attempt(on_attempt, attempt)
                return ModelCallOutcome(response=response, attempts=tuple(attempts))
            except Exception as exc:
                last_exc = exc
                retryable = _retryable_transport_error(exc)
                attempt = ModelAttempt(
                    attempt=attempt_number,
                    kind=(RetryKind.INITIAL_REQUEST if attempt_number == 1 else RetryKind.REQUEST_RETRY),
                    request_fingerprint=envelope.request_fingerprint,
                    elapsed_ms=max(0, int((self._clock() - attempt_started) * 1000)),
                    ok=False,
                    error_kind=type(exc).__name__,
                    retryable=retryable,
                )
                attempts.append(attempt)
                _notify_attempt(on_attempt, attempt)
                if not retryable or attempt_number >= policy.max_attempts:
                    break
                remaining = deadline - self._clock()
                if remaining <= 0:
                    break
                delay = min(backoff, float(policy.max_backoff_s), remaining)
                if delay > 0:
                    if stop_event is not None:
                        if stop_event.wait(delay):
                            raise ModelCallError("model request canceled", tuple(attempts))
                    else:
                        self._sleeper(delay)
                backoff = min(
                    float(policy.max_backoff_s),
                    backoff * float(policy.backoff_multiplier),
                )
        raise ModelCallError(
            f"model transport failed after {len(attempts)} attempt(s): {last_exc}",
            tuple(attempts),
        )


__all__ = ["ModelCallOutcome", "ModelCallError", "ModelCallRuntime"]
