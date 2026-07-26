"""Versioned contracts for model requests, retries, polls, and redecisions."""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


MODEL_REQUEST_FORMAT = "model-request/0"
_ROLE_PATTERN = re.compile(r"[a-z][a-z0-9_]{1,63}")


class InvalidModelRequest(ValueError):
    pass


class RetryKind(str, Enum):
    INITIAL_REQUEST = "initial_request"
    REQUEST_RETRY = "request_retry"


class FollowupKind(str, Enum):
    REQUEST_RETRY = "request_retry"
    POLL = "poll"
    REDECISION = "redecision"
    ACTION_REPLAY = "action_replay"


FrozenJSON = tuple[Any, ...] | str | bool | int | float | None


def _freeze_json(
    value: Any,
    path: str = "request",
    *,
    _ancestors: set[int] | None = None,
) -> FrozenJSON:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidModelRequest(f"{path} contains a non-finite number")
        return value
    if isinstance(value, (list, tuple, Mapping)):
        ancestors = _ancestors if _ancestors is not None else set()
        marker = id(value)
        if marker in ancestors:
            raise InvalidModelRequest(f"{path} contains a cyclic JSON value")
        ancestors.add(marker)
        try:
            if isinstance(value, (list, tuple)):
                return (
                    "list",
                    *(
                        _freeze_json(item, f"{path}[]", _ancestors=ancestors)
                        for item in value
                    ),
                )
            if any(not isinstance(key, str) for key in value):
                raise InvalidModelRequest(f"{path} object keys must be strings")
            return (
                "object",
                *(
                    (
                        key,
                        _freeze_json(
                            value[key], f"{path}.{key}", _ancestors=ancestors
                        ),
                    )
                    for key in sorted(value)
                ),
            )
        finally:
            ancestors.remove(marker)
    raise InvalidModelRequest(f"{path} contains non-JSON value {type(value).__name__}")


def _thaw_json(value: FrozenJSON) -> Any:
    if not isinstance(value, tuple):
        return value
    if not value:
        raise InvalidModelRequest("frozen request contains an empty tuple")
    if value[0] == "list":
        return [_thaw_json(item) for item in value[1:]]
    if value[0] == "object":
        return {str(key): _thaw_json(item) for key, item in value[1:]}
    raise InvalidModelRequest("frozen request contains an unknown container tag")


def _update_hash(digest: Any, value: FrozenJSON) -> None:
    """Hash JSON values without materializing image-bearing canonical JSON."""
    if value is None:
        digest.update(b"n;")
    elif isinstance(value, bool):
        digest.update(b"b1;" if value else b"b0;")
    elif isinstance(value, int):
        digest.update(b"i")
        digest.update(str(value).encode("ascii"))
        digest.update(b";")
    elif isinstance(value, float):
        digest.update(b"f")
        digest.update(value.hex().encode("ascii"))
        digest.update(b";")
    elif isinstance(value, str):
        encoded = value.encode("utf-8")
        digest.update(b"s")
        digest.update(str(len(encoded)).encode("ascii"))
        digest.update(b":")
        digest.update(encoded)
        digest.update(b";")
    elif value and value[0] == "list":
        digest.update(b"l[")
        for item in value[1:]:
            _update_hash(digest, item)
        digest.update(b"]")
    elif value and value[0] == "object":
        digest.update(b"o{")
        for key, item in value[1:]:
            _update_hash(digest, str(key))
            _update_hash(digest, item)
        digest.update(b"}")
    else:
        raise InvalidModelRequest("frozen request contains an invalid value")


def _fingerprint(value: FrozenJSON) -> str:
    digest = hashlib.sha256()
    _update_hash(digest, value)
    return digest.hexdigest()


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidModelRequest(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True, slots=True)
class ModelRetryPolicy:
    max_attempts: int = 1
    total_timeout_s: float = 120.0
    initial_backoff_s: float = 1.0
    rate_limit_initial_backoff_s: float | None = None
    backoff_multiplier: float = 2.0
    max_backoff_s: float = 20.0

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 5:
            raise InvalidModelRequest("max_attempts must be in [1, 5]")
        for name in (
            "total_timeout_s",
            "initial_backoff_s",
            "backoff_multiplier",
            "max_backoff_s",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise InvalidModelRequest(f"{name} must be finite")
            if name == "total_timeout_s" and float(value) <= 0:
                raise InvalidModelRequest("total_timeout_s must be positive")
            if name != "total_timeout_s" and float(value) < 0:
                raise InvalidModelRequest(f"{name} must be non-negative")
        rate_limit_backoff = self.rate_limit_initial_backoff_s
        if rate_limit_backoff is not None and (
            isinstance(rate_limit_backoff, bool)
            or not isinstance(rate_limit_backoff, (int, float))
            or not math.isfinite(float(rate_limit_backoff))
            or float(rate_limit_backoff) < 0
        ):
            raise InvalidModelRequest(
                "rate_limit_initial_backoff_s must be finite, non-negative, or null"
            )


@dataclass(frozen=True, slots=True)
class ModelRequestEnvelope:
    decision_id: str
    model_role: str
    purpose: str
    request_fingerprint: str
    _frozen_request: FrozenJSON = field(repr=False, compare=False)
    format: str = MODEL_REQUEST_FORMAT

    def __post_init__(self) -> None:
        if self.format != MODEL_REQUEST_FORMAT:
            raise InvalidModelRequest(f"unsupported model request format {self.format!r}")
        object.__setattr__(self, "decision_id", _text(self.decision_id, "decision_id"))
        role = _text(self.model_role, "model_role")
        if not _ROLE_PATTERN.fullmatch(role):
            raise InvalidModelRequest("model_role must be a canonical role label")
        object.__setattr__(self, "model_role", role)
        object.__setattr__(self, "purpose", _text(self.purpose, "purpose"))
        decoded = _thaw_json(self._frozen_request)
        if not isinstance(decoded, dict):
            raise InvalidModelRequest("provider request must be a JSON object")
        expected = _fingerprint(self._frozen_request)
        if self.request_fingerprint != expected:
            raise InvalidModelRequest("request fingerprint does not match frozen request")

    @classmethod
    def bind(
        cls,
        *,
        decision_id: str,
        model_role: str,
        purpose: str,
        provider_request: Mapping[str, Any],
    ) -> "ModelRequestEnvelope":
        frozen = _freeze_json(provider_request)
        if not isinstance(frozen, tuple) or not frozen or frozen[0] != "object":
            raise InvalidModelRequest("provider request must be a JSON object")
        return cls(
            decision_id=decision_id,
            model_role=model_role,
            purpose=purpose,
            request_fingerprint=_fingerprint(frozen),
            _frozen_request=frozen,
        )

    def provider_request(self) -> dict[str, Any]:
        value = _thaw_json(self._frozen_request)
        if not isinstance(value, dict):
            raise InvalidModelRequest("provider request must be a JSON object")
        return value

    def to_dict(self, *, include_request: bool = False) -> dict[str, Any]:
        value = {
            "format": self.format,
            "decision_id": self.decision_id,
            "model_role": self.model_role,
            "purpose": self.purpose,
            "request_fingerprint": self.request_fingerprint,
        }
        if include_request:
            value["provider_request"] = self.provider_request()
        return value


@dataclass(frozen=True, slots=True)
class ModelAttempt:
    attempt: int
    kind: RetryKind
    request_fingerprint: str
    elapsed_ms: int
    ok: bool
    error_kind: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        if type(self.attempt) is not int or self.attempt < 1:
            raise InvalidModelRequest("attempt must be positive")
        expected_kind = RetryKind.INITIAL_REQUEST if self.attempt == 1 else RetryKind.REQUEST_RETRY
        if self.kind is not expected_kind:
            raise InvalidModelRequest("attempt kind does not match attempt number")
        if type(self.elapsed_ms) is not int or self.elapsed_ms < 0:
            raise InvalidModelRequest("elapsed_ms must be non-negative")
        if not re.fullmatch(r"[0-9a-f]{64}", self.request_fingerprint):
            raise InvalidModelRequest("attempt request_fingerprint must be SHA-256")
        if type(self.ok) is not bool or type(self.retryable) is not bool:
            raise InvalidModelRequest("attempt ok/retryable must be boolean")
        if self.ok and (self.error_kind is not None or self.retryable):
            raise InvalidModelRequest("successful attempt cannot carry an error")
        if not self.ok and (
            not isinstance(self.error_kind, str) or not self.error_kind.strip()
        ):
            raise InvalidModelRequest("failed attempt requires error_kind")


def classify_followup(
    previous: ModelRequestEnvelope,
    current: ModelRequestEnvelope | None,
    declared: FollowupKind,
) -> FollowupKind:
    """Fail closed when a changed request is mislabeled as transport retry."""
    if not isinstance(declared, FollowupKind):
        raise InvalidModelRequest("declared followup kind is invalid")
    if declared is FollowupKind.ACTION_REPLAY:
        raise InvalidModelRequest(
            "action replay is governed by the mutation replay contract, not model retry"
        )
    if declared is FollowupKind.POLL:
        if current is not None:
            raise InvalidModelRequest("poll must not submit another model request")
        return declared
    if current is None:
        raise InvalidModelRequest(f"{declared.value} requires a request")
    if declared is FollowupKind.REQUEST_RETRY:
        if (
            previous.decision_id != current.decision_id
            or previous.model_role != current.model_role
            or previous.purpose != current.purpose
            or previous.request_fingerprint != current.request_fingerprint
        ):
            raise InvalidModelRequest(
                "request_retry must preserve decision id and exact request fingerprint"
            )
    elif declared is FollowupKind.REDECISION:
        if previous.decision_id == current.decision_id:
            raise InvalidModelRequest("redecision requires a new decision id")
    return declared


__all__ = [
    "MODEL_REQUEST_FORMAT",
    "InvalidModelRequest",
    "RetryKind",
    "FollowupKind",
    "ModelRetryPolicy",
    "ModelRequestEnvelope",
    "ModelAttempt",
    "classify_followup",
]
