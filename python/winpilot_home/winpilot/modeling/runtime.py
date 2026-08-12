"""The sole transport retry loop for model requests."""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from .contracts import ModelAttempt, ModelRequestEnvelope, ModelRetryPolicy, RetryKind


_TRANSIENT_MULTIMODAL_REJECTION_MESSAGES = frozenset(
    {
        "multimodal data is corrupted or cannot be processed.",
    }
)


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


def _is_transient_multimodal_rejection(exc: Exception) -> bool:
    """Recognize one provider-side decoder failure from structured error data.

    The same locally complete JPEG request can occasionally receive this 400
    after identical-shape multimodal requests succeeded.  Treat only the exact
    structured provider response as transient; exception-message matching
    would make unrelated programming and request-schema errors retryable.
    """

    if _status_code(exc) != 400:
        return False
    body = getattr(exc, "body", None)
    if not isinstance(body, Mapping):
        return False
    error = body.get("error", body)
    if not isinstance(error, Mapping):
        return False
    error_type = str(error.get("type") or "").strip().casefold()
    if error_type != "badrequesterror":
        return False
    code = str(error.get("code") or "").strip()
    if code and code != "400":
        return False
    message = " ".join(str(error.get("message") or "").split()).casefold()
    return message in _TRANSIENT_MULTIMODAL_REJECTION_MESSAGES


def _retryable_transport_error(exc: Exception) -> bool:
    code = _status_code(exc)
    if code is not None:
        return (
            code in {408, 409, 429}
            or 500 <= code <= 599
            or _is_transient_multimodal_rejection(exc)
        )
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


class _ModelResponseAbandoned(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _send_before_deadline(
    send_once: Callable[[dict[str, Any]], Any],
    request: dict[str, Any],
    *,
    clock: Callable[[], float],
    deadline: float,
    stop_event: threading.Event | None,
) -> Any:
    """Keep a blocked synchronous transport from owning the run deadline."""

    completed = threading.Event()
    result: list[tuple[bool, Any]] = []

    def send() -> None:
        try:
            value = send_once(request)
        except BaseException as exc:
            result.append((False, exc))
        else:
            result.append((True, value))
        finally:
            completed.set()

    threading.Thread(
        target=send,
        name="winpilot-model-transport",
        daemon=True,
    ).start()
    while not completed.is_set():
        if stop_event is not None and stop_event.is_set():
            raise _ModelResponseAbandoned("ModelResponseCanceled")
        remaining = deadline - clock()
        if remaining <= 0:
            raise _ModelResponseAbandoned("ModelResponseDeadlineExceeded")
        completed.wait(min(0.05, remaining))

    succeeded, value = result[0]
    if not succeeded:
        raise value
    return value


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
                response = _send_before_deadline(
                    send_once,
                    envelope.provider_request(),
                    clock=self._clock,
                    deadline=deadline,
                    stop_event=stop_event,
                )
            except _ModelResponseAbandoned as abandoned:
                attempt = ModelAttempt(
                    attempt=attempt_number,
                    kind=(
                        RetryKind.INITIAL_REQUEST
                        if attempt_number == 1
                        else RetryKind.REQUEST_RETRY
                    ),
                    request_fingerprint=envelope.request_fingerprint,
                    elapsed_ms=max(
                        0, int((self._clock() - attempt_started) * 1000)
                    ),
                    ok=False,
                    error_kind=abandoned.reason,
                    retryable=False,
                )
                attempts.append(attempt)
                _notify_attempt(on_attempt, attempt)
                raise ModelCallError(
                    "model response abandoned before consumption",
                    tuple(attempts),
                ) from None
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
                initial_backoff = float(policy.initial_backoff_s)
                if (
                    _status_code(exc) == 429
                    and policy.rate_limit_initial_backoff_s is not None
                ):
                    initial_backoff = float(policy.rate_limit_initial_backoff_s)
                max_backoff = float(policy.max_backoff_s)
                bounded_backoff = min(initial_backoff, max_backoff)
                for _ in range(attempt_number - 1):
                    product = bounded_backoff * float(policy.backoff_multiplier)
                    bounded_backoff = (
                        max_backoff
                        if not math.isfinite(product)
                        else min(product, max_backoff)
                    )
                delay = min(
                    bounded_backoff,
                    remaining,
                )
                if delay > 0:
                    if stop_event is not None:
                        if stop_event.wait(delay):
                            raise ModelCallError("model request canceled", tuple(attempts))
                    else:
                        self._sleeper(delay)
            else:
                elapsed_ms = max(
                    0, int((self._clock() - attempt_started) * 1000)
                )
                abandoned_reason = None
                if stop_event is not None and stop_event.is_set():
                    abandoned_reason = "ModelResponseCanceled"
                elif self._clock() >= deadline:
                    abandoned_reason = "ModelResponseDeadlineExceeded"
                if abandoned_reason is not None:
                    attempt = ModelAttempt(
                        attempt=attempt_number,
                        kind=(
                            RetryKind.INITIAL_REQUEST
                            if attempt_number == 1
                            else RetryKind.REQUEST_RETRY
                        ),
                        request_fingerprint=envelope.request_fingerprint,
                        elapsed_ms=elapsed_ms,
                        ok=False,
                        error_kind=abandoned_reason,
                        retryable=False,
                    )
                    attempts.append(attempt)
                    _notify_attempt(on_attempt, attempt)
                    raise ModelCallError(
                        "model response abandoned before consumption",
                        tuple(attempts),
                    )
                attempt = ModelAttempt(
                    attempt=attempt_number,
                    kind=(
                        RetryKind.INITIAL_REQUEST
                        if attempt_number == 1
                        else RetryKind.REQUEST_RETRY
                    ),
                    request_fingerprint=envelope.request_fingerprint,
                    elapsed_ms=elapsed_ms,
                    ok=True,
                )
                attempts.append(attempt)
                _notify_attempt(on_attempt, attempt)
                return ModelCallOutcome(response=response, attempts=tuple(attempts))
        raise ModelCallError(
            f"model transport failed after {len(attempts)} attempt(s): {last_exc}",
            tuple(attempts),
        )


__all__ = ["ModelCallOutcome", "ModelCallError", "ModelCallRuntime"]
