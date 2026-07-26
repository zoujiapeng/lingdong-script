"""Run-scoped, single-use permits for one exact Actor desktop mutation."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import hashlib
import threading

from ..actor import ActorDecisionEnvelope
from .contracts import RuntimeCommitIdentity


class MutationPermitError(ValueError):
    """A permit cannot be issued for the requested immutable decision."""


class MutationPermitReason(str, Enum):
    ALLOWED = "allowed"
    MISSING = "missing"
    ALREADY_CONSUMED = "already_consumed"
    ENVELOPE_MISMATCH = "envelope_mismatch"
    COMMIT_IDENTITY_CHANGED = "commit_identity_changed"
    RUNTIME_STATE_CHANGED = "runtime_state_changed"
    RUNTIME_STATE_UNAVAILABLE = "runtime_state_unavailable"
    AUTHORITY_CLOSED = "authority_closed"


@dataclass(frozen=True, slots=True)
class RuntimeMutationPermit:
    permit_id: str | None
    decision_id: str
    envelope_sha256: str
    expected_commit_fingerprint: str | None
    observed_commit_fingerprint: str | None
    turn_index: int | None
    allowed: bool
    reason: MutationPermitReason


@dataclass(frozen=True, slots=True)
class _PendingPermit:
    permit_id: str
    envelope: ActorDecisionEnvelope
    envelope_sha256: str
    commit_identity: RuntimeCommitIdentity
    turn_index: int
    still_current: Callable[[], bool]


class MutationPermitAuthority:
    """Ephemeral execution authority for exactly one mission/run pair."""

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
        if (
            type(consumed_history_limit) is not int
            or consumed_history_limit < 1
        ):
            raise MutationPermitError(
                "consumed_history_limit must be positive"
            )
        self.mission_id = mission_id.strip()
        self.run_id = run_id.strip()
        self._history_limit = consumed_history_limit
        self._pending: dict[str, _PendingPermit] = {}
        self._active: dict[str, _PendingPermit] = {}
        self._consumed: OrderedDict[str, str] = OrderedDict()
        self._last_issued_turn = -1
        self._closed = False
        self._lock = threading.RLock()

    @staticmethod
    def _fingerprint(envelope: ActorDecisionEnvelope) -> str:
        return hashlib.sha256(
            envelope.to_json().encode("utf-8")
        ).hexdigest()

    def issue(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        turn_index: int,
        commit_identity: RuntimeCommitIdentity,
        still_current: Callable[[], bool],
    ) -> str:
        if type(envelope) is not ActorDecisionEnvelope:
            raise MutationPermitError(
                "permit requires canonical ActorDecisionEnvelope"
            )
        if (
            envelope.mission_id != self.mission_id
            or envelope.run_id != self.run_id
        ):
            raise MutationPermitError(
                "envelope is outside permit mission/run"
            )
        if type(turn_index) is not int or turn_index < 0:
            raise MutationPermitError(
                "turn_index must be a non-negative integer"
            )
        if type(commit_identity) is not RuntimeCommitIdentity:
            raise MutationPermitError(
                "commit_identity must be RuntimeCommitIdentity"
            )
        if not callable(still_current):
            raise MutationPermitError("still_current must be callable")
        if turn_index != envelope.turn_index:
            raise MutationPermitError(
                "permit turn_index and Actor envelope disagree"
            )
        if (
            envelope.frame_id != commit_identity.frame.frame_id
            or envelope.frame_epoch != commit_identity.frame.epoch
            or envelope.frame_hwnd != commit_identity.frame.hwnd
            or envelope.frame_region != commit_identity.frame.region
            or envelope.frame_scope != commit_identity.frame.scope
            or envelope.submitted_image_size
            != commit_identity.frame.submitted_size
            or envelope.frame_image_sha256
            != commit_identity.frame.image_sha256
            or envelope.target_binding_id
            != commit_identity.target.binding_id
        ):
            raise MutationPermitError(
                "envelope and commit identity disagree"
            )
        fingerprint = self._fingerprint(envelope)
        permit_id = "permit-" + hashlib.sha256(
            (
                f"{self.run_id}|{turn_index}|{fingerprint}|"
                f"{commit_identity.fingerprint}"
            ).encode("utf-8")
        ).hexdigest()[:24]
        with self._lock:
            if self._closed:
                raise MutationPermitError("permit authority is closed")
            if turn_index <= self._last_issued_turn:
                raise MutationPermitError(
                    "permit turn_index must increase monotonically"
                )
            decision_id = envelope.decision_id
            if (
                decision_id in self._pending
                or decision_id in self._active
                or decision_id in self._consumed
            ):
                raise MutationPermitError(
                    "decision already has a mutation attempt"
                )
            self._pending[decision_id] = _PendingPermit(
                permit_id=permit_id,
                envelope=envelope,
                envelope_sha256=fingerprint,
                commit_identity=commit_identity,
                turn_index=turn_index,
                still_current=still_current,
            )
            self._last_issued_turn = turn_index
        return permit_id

    def begin_admission(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        current_commit_identity: RuntimeCommitIdentity,
    ) -> RuntimeMutationPermit:
        """Consume once at commit time and hold admission until cleanup."""

        if type(envelope) is not ActorDecisionEnvelope:
            raise MutationPermitError(
                "admission requires canonical ActorDecisionEnvelope"
            )
        if type(current_commit_identity) is not RuntimeCommitIdentity:
            raise MutationPermitError(
                "current_commit_identity must be RuntimeCommitIdentity"
            )
        fingerprint = self._fingerprint(envelope)
        with self._lock:
            if self._closed:
                return self._result(
                    envelope,
                    fingerprint,
                    None,
                    current_commit_identity,
                    MutationPermitReason.AUTHORITY_CLOSED,
                )
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
                return self._result(
                    envelope,
                    fingerprint,
                    None,
                    current_commit_identity,
                    reason,
                )
            self._remember(
                envelope.decision_id,
                pending.envelope_sha256,
            )

        if (
            fingerprint != pending.envelope_sha256
            or envelope != pending.envelope
        ):
            return self._result(
                envelope,
                fingerprint,
                pending,
                current_commit_identity,
                MutationPermitReason.ENVELOPE_MISMATCH,
            )
        if current_commit_identity != pending.commit_identity:
            return self._result(
                envelope,
                fingerprint,
                pending,
                current_commit_identity,
                MutationPermitReason.COMMIT_IDENTITY_CHANGED,
            )
        try:
            current = pending.still_current()
        except Exception:
            return self._result(
                envelope,
                fingerprint,
                pending,
                current_commit_identity,
                MutationPermitReason.RUNTIME_STATE_UNAVAILABLE,
            )
        if current is not True:
            return self._result(
                envelope,
                fingerprint,
                pending,
                current_commit_identity,
                MutationPermitReason.RUNTIME_STATE_CHANGED,
            )
        with self._lock:
            if self._closed:
                return self._result(
                    envelope,
                    fingerprint,
                    pending,
                    current_commit_identity,
                    MutationPermitReason.AUTHORITY_CLOSED,
                )
            self._active[envelope.decision_id] = pending
        return self._result(
            envelope,
            fingerprint,
            pending,
            current_commit_identity,
            MutationPermitReason.ALLOWED,
        )

    def active_admission_matches(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        current_commit_identity: RuntimeCommitIdentity,
    ) -> bool:
        """Read whether this exact envelope and full identity own commit."""

        if (
            type(envelope) is not ActorDecisionEnvelope
            or type(current_commit_identity) is not RuntimeCommitIdentity
        ):
            return False
        with self._lock:
            pending = self._active.get(envelope.decision_id)
            return bool(
                not self._closed
                and pending is not None
                and pending.envelope == envelope
                and pending.commit_identity == current_commit_identity
            )

    def revalidate_active_admission(
        self,
        envelope: ActorDecisionEnvelope,
        *,
        current_commit_identity: RuntimeCommitIdentity,
    ) -> bool:
        """Recheck live Runtime state and the exact active permit identity.

        The stored Runtime callback is deliberately invoked outside the permit
        lock.  A second locked comparison then proves the same admission is
        still active, so close/cleanup races fail closed.
        """

        if (
            type(envelope) is not ActorDecisionEnvelope
            or type(current_commit_identity) is not RuntimeCommitIdentity
        ):
            return False
        with self._lock:
            pending = self._active.get(envelope.decision_id)
            if not (
                not self._closed
                and pending is not None
                and pending.envelope == envelope
                and pending.commit_identity == current_commit_identity
            ):
                return False
            still_current = pending.still_current
        try:
            current = still_current()
        except Exception:
            return False
        if current is not True:
            return False
        with self._lock:
            return bool(
                not self._closed
                and self._active.get(envelope.decision_id) is pending
                and pending.envelope == envelope
                and pending.commit_identity == current_commit_identity
            )

    def finish_admission(self, decision_id: str) -> bool:
        with self._lock:
            return self._active.pop(str(decision_id), None) is not None

    def revoke(self, decision_id: str) -> bool:
        """Permanently retire one unconsumed permit."""

        with self._lock:
            pending = self._pending.pop(str(decision_id), None)
            if pending is None:
                return False
            self._remember(
                pending.envelope.decision_id,
                pending.envelope_sha256,
            )
            return True

    def close(self) -> None:
        with self._lock:
            for pending in self._pending.values():
                self._remember(
                    pending.envelope.decision_id,
                    pending.envelope_sha256,
                )
            self._pending.clear()
            self._active.clear()
            self._closed = True

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    @property
    def consumed_count(self) -> int:
        with self._lock:
            return len(self._consumed)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def _remember(self, decision_id: str, fingerprint: str) -> None:
        self._consumed[decision_id] = fingerprint
        self._consumed.move_to_end(decision_id)
        while len(self._consumed) > self._history_limit:
            self._consumed.popitem(last=False)

    @staticmethod
    def _result(
        envelope: ActorDecisionEnvelope,
        fingerprint: str,
        pending: _PendingPermit | None,
        observed: RuntimeCommitIdentity,
        reason: MutationPermitReason,
    ) -> RuntimeMutationPermit:
        return RuntimeMutationPermit(
            permit_id=None if pending is None else pending.permit_id,
            decision_id=envelope.decision_id,
            envelope_sha256=fingerprint,
            expected_commit_fingerprint=(
                None
                if pending is None
                else pending.commit_identity.fingerprint
            ),
            observed_commit_fingerprint=observed.fingerprint,
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
