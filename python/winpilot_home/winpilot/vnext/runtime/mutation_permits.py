"""Run-scoped, single-use admission permits for GUI mutation.

The permit owner is ephemeral execution state, not Mission completion truth.
Runtime issues one permit for one already-bound Actor envelope.  A Controller
may consume it exactly once after acquiring the desktop queue and before raw
input.  Rejection never restores the permit; recovery requires a new decision.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import hashlib
import threading

from ..actor import ActorDecisionEnvelope


class MutationPermitError(ValueError):
    pass


class MutationPermitReason(str, Enum):
    ALLOWED = "allowed"
    MISSING = "missing"
    ALREADY_CONSUMED = "already_consumed"
    ENVELOPE_MISMATCH = "envelope_mismatch"
    RUNTIME_STATE_CHANGED = "runtime_state_changed"
    RUNTIME_STATE_UNAVAILABLE = "runtime_state_unavailable"
    AUTHORITY_CLOSED = "authority_closed"


@dataclass(frozen=True, slots=True)
class RuntimeMutationPermit:
    """Immutable result of one atomic permit-consumption attempt."""

    permit_id: str | None
    decision_id: str
    envelope_sha256: str
    turn_index: int | None
    allowed: bool
    reason: MutationPermitReason


@dataclass(frozen=True, slots=True)
class _PendingPermit:
    permit_id: str
    envelope: ActorDecisionEnvelope
    envelope_sha256: str
    turn_index: int
    still_current: Callable[[], bool]


class MutationPermitAuthority:
    """Thread-safe owner of permits for one mission/run pair."""

    def __init__(
        self,
        mission_id: str,
        run_id: str,
        *,
        consumed_history_limit: int = 2048,
    ) -> None:
        if type(mission_id) is not str or not mission_id.strip():
            raise MutationPermitError("mission_id must be non-empty")
        if type(run_id) is not str or not run_id.strip():
            raise MutationPermitError("run_id must be non-empty")
        if type(consumed_history_limit) is not int or consumed_history_limit < 1:
            raise MutationPermitError("consumed_history_limit must be positive")
        self.mission_id = mission_id.strip()
        self.run_id = run_id.strip()
        self._limit = consumed_history_limit
        self._pending: dict[str, _PendingPermit] = {}
        self._active: dict[str, _PendingPermit] = {}
        self._consumed: OrderedDict[str, str] = OrderedDict()
        self._last_issued_turn = -1
        self._closed = False
        self._lock = threading.RLock()

    @staticmethod
    def _fingerprint(envelope: ActorDecisionEnvelope) -> str:
        return hashlib.sha256(envelope.to_json().encode("utf-8")).hexdigest()

    def issue(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        turn_index: int,
        still_current: Callable[[], bool],
    ) -> str:
        if not isinstance(envelope, ActorDecisionEnvelope):
            raise MutationPermitError("envelope must be ActorDecisionEnvelope")
        if envelope.mission_id != self.mission_id or envelope.run_id != self.run_id:
            raise MutationPermitError("envelope is outside permit mission/run scope")
        if type(turn_index) is not int or turn_index < 0:
            raise MutationPermitError("turn_index must be a non-negative integer")
        if not callable(still_current):
            raise MutationPermitError("still_current must be callable")
        fingerprint = self._fingerprint(envelope)
        permit_id = "permit-" + hashlib.sha256(
            f"{self.run_id}|{turn_index}|{fingerprint}".encode("utf-8")
        ).hexdigest()[:24]
        with self._lock:
            if self._closed:
                raise MutationPermitError("permit authority is closed")
            if turn_index <= self._last_issued_turn:
                raise MutationPermitError("permit turn_index must increase monotonically")
            if (
                envelope.decision_id in self._pending
                or envelope.decision_id in self._active
                or envelope.decision_id in self._consumed
            ):
                raise MutationPermitError("decision already has an admission attempt")
            self._pending[envelope.decision_id] = _PendingPermit(
                permit_id,
                envelope,
                fingerprint,
                turn_index,
                still_current,
            )
            self._last_issued_turn = turn_index
        return permit_id

    def _consume(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        activate: bool,
    ) -> RuntimeMutationPermit:
        """Retire one pending permit and optionally hold its commit admission."""

        if not isinstance(envelope, ActorDecisionEnvelope):
            raise MutationPermitError("envelope must be ActorDecisionEnvelope")
        fingerprint = self._fingerprint(envelope)
        with self._lock:
            if self._closed:
                return self._result(envelope, fingerprint, None, MutationPermitReason.AUTHORITY_CLOSED)
            pending = self._pending.pop(envelope.decision_id, None)
            if pending is None:
                reason = (
                    MutationPermitReason.ALREADY_CONSUMED
                    if (
                        envelope.decision_id in self._active
                        or envelope.decision_id in self._consumed
                    )
                    else MutationPermitReason.MISSING
                )
                return self._result(envelope, fingerprint, None, reason)
            self._remember(envelope.decision_id, pending.envelope_sha256)

        if fingerprint != pending.envelope_sha256 or envelope != pending.envelope:
            return self._result(
                envelope, fingerprint, pending, MutationPermitReason.ENVELOPE_MISMATCH,
            )
        try:
            current = pending.still_current()
        except Exception:
            return self._result(
                envelope, fingerprint, pending,
                MutationPermitReason.RUNTIME_STATE_UNAVAILABLE,
            )
        result = self._result(
            envelope,
            fingerprint,
            pending,
            (
                MutationPermitReason.ALLOWED
                if current is True
                else MutationPermitReason.RUNTIME_STATE_CHANGED
            ),
        )
        if activate and result.allowed:
            with self._lock:
                if self._closed:
                    return self._result(
                        envelope,
                        fingerprint,
                        pending,
                        MutationPermitReason.AUTHORITY_CLOSED,
                    )
                self._active[envelope.decision_id] = pending
        return result

    def consume(self, envelope: ActorDecisionEnvelope) -> RuntimeMutationPermit:
        """Consume once without opening a Controller commit admission."""

        return self._consume(envelope, activate=False)

    def begin_admission(
        self, envelope: ActorDecisionEnvelope,
    ) -> RuntimeMutationPermit:
        """Consume once and expose exact authorization until ``finish_admission``.

        The active entry is transient execution authority held by this same
        owner.  It is not replayable history and is cleared whether commit
        validation, raw input, or post-action observation succeeds or raises.
        """

        return self._consume(envelope, activate=True)

    def active_admission_matches(
        self,
        decision_id: str,
        *,
        frame: object,
        target: object,
        pointer: object,
    ) -> bool:
        """Read whether one exact Runtime envelope currently owns admission."""

        with self._lock:
            if self._closed:
                return False
            pending = self._active.get(str(decision_id))
            if pending is None:
                return False
            envelope = pending.envelope
            return bool(
                envelope.decision_id == decision_id
                and envelope.frame == frame
                and envelope.target == target
                and envelope.pointer == pointer
            )

    def finish_admission(self, decision_id: str) -> bool:
        """Close transient commit authority without making it replayable."""

        with self._lock:
            return self._active.pop(str(decision_id), None) is not None

    def revoke(self, decision_id: str) -> bool:
        """Permanently retire an unconsumed permit."""

        with self._lock:
            pending = self._pending.pop(str(decision_id), None)
            if pending is None:
                return False
            self._remember(pending.envelope.decision_id, pending.envelope_sha256)
            return True

    def close(self) -> None:
        with self._lock:
            for pending in self._pending.values():
                self._remember(pending.envelope.decision_id, pending.envelope_sha256)
            self._pending.clear()
            self._active.clear()
            self._closed = True

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def consumed_count(self) -> int:
        with self._lock:
            return len(self._consumed)

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def _remember(self, decision_id: str, fingerprint: str) -> None:
        self._consumed[decision_id] = fingerprint
        self._consumed.move_to_end(decision_id)
        while len(self._consumed) > self._limit:
            self._consumed.popitem(last=False)

    @staticmethod
    def _result(
        envelope: ActorDecisionEnvelope,
        fingerprint: str,
        pending: _PendingPermit | None,
        reason: MutationPermitReason,
    ) -> RuntimeMutationPermit:
        return RuntimeMutationPermit(
            permit_id=None if pending is None else pending.permit_id,
            decision_id=envelope.decision_id,
            envelope_sha256=fingerprint,
            turn_index=None if pending is None else pending.turn_index,
            allowed=reason is MutationPermitReason.ALLOWED,
            reason=reason,
        )


__all__ = [
    "MutationPermitAuthority",
    "MutationPermitError",
    "MutationPermitReason",
    "RuntimeMutationPermit",
]
