"""Runtime-private, one-shot authority for ordinal target selection."""
from __future__ import annotations

from dataclasses import dataclass
import threading

from ...visual_runtime.commit_admission import FrameCommitIdentity
from .contracts import (
    MAX_TARGET_CANDIDATES,
    TargetCandidate,
    TargetContractError,
    TargetDiscoverySnapshot,
    canonical_candidate_fingerprint,
)


class TargetSelectionLeaseError(ValueError):
    """The private candidate mapping is invalid, stale, or already consumed."""


def _identity(value: object, name: str) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or len(value.strip()) > 256
        or any(ord(char) < 32 for char in value)
    ):
        raise TargetSelectionLeaseError(f"{name} must be bounded non-empty text")
    return value.strip()


def _counter(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise TargetSelectionLeaseError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class TargetSelectionScope:
    """Exact Runtime authority bound to one frozen Actor decision request."""

    mission_id: str
    run_id: str
    mission_revision: int
    obligation_id: str
    turn_index: int
    frame: FrameCommitIdentity
    broker_projection_id: str
    candidate_fingerprint: str

    def __post_init__(self) -> None:
        for name in (
            "mission_id", "run_id", "obligation_id", "broker_projection_id",
        ):
            object.__setattr__(self, name, _identity(getattr(self, name), name))
        for name in ("mission_revision", "turn_index"):
            object.__setattr__(self, name, _counter(getattr(self, name), name))
        if not isinstance(self.frame, FrameCommitIdentity):
            raise TargetSelectionLeaseError(
                "frame must be the exact FrameCommitIdentity shown to the Actor"
            )
        try:
            fingerprint = canonical_candidate_fingerprint(self.candidate_fingerprint)
        except TargetContractError as exc:
            raise TargetSelectionLeaseError(str(exc)) from exc
        object.__setattr__(self, "candidate_fingerprint", fingerprint)


@dataclass(frozen=True, slots=True)
class TargetChoiceFact:
    """Safe semantic facts derived from one private lease mapping."""

    ordinal: int
    label: str
    state: str
    window_title: str | None


class TargetSelectionLease:
    """Freeze ordinal-to-candidate mapping and permit at most one consume attempt.

    This object is an ephemeral Runtime sidecar. It is intentionally not
    serializable and must never enter Mission events, evidence, traces, or the
    Actor payload.
    """

    __slots__ = ("_candidates", "_choice_facts", "_consumed", "_lock", "_scope")

    def __init__(
        self,
        *,
        scope: TargetSelectionScope,
        discovery: TargetDiscoverySnapshot,
    ) -> None:
        if not isinstance(scope, TargetSelectionScope):
            raise TargetSelectionLeaseError("scope must be TargetSelectionScope")
        if not isinstance(discovery, TargetDiscoverySnapshot):
            raise TargetSelectionLeaseError("discovery must be TargetDiscoverySnapshot")
        candidates = discovery.candidates
        if (
            not candidates
            or len(candidates) > MAX_TARGET_CANDIDATES
            or any(not isinstance(item, TargetCandidate) for item in candidates)
        ):
            raise TargetSelectionLeaseError(
                "ordered_candidates must contain 1..16 TargetCandidate values"
            )
        refs = tuple(item.candidate_ref for item in candidates)
        if len(refs) != len(set(refs)):
            raise TargetSelectionLeaseError("ordered_candidates must be unique")
        if scope.candidate_fingerprint != discovery.candidate_fingerprint:
            raise TargetSelectionLeaseError(
                "scope does not identify the frozen candidate mapping"
            )
        self._scope = scope
        self._candidates = candidates
        self._choice_facts = tuple(
            TargetChoiceFact(
                ordinal=index,
                label=item.label,
                state=item.state.value,
                window_title=item.window_title or None,
            )
            for index, item in enumerate(candidates, start=1)
        )
        self._consumed = False
        self._lock = threading.Lock()

    @property
    def scope(self) -> TargetSelectionScope:
        return self._scope

    def choice_facts(self) -> tuple[TargetChoiceFact, ...]:
        """Return only the safe view derived from this still-live lease."""

        with self._lock:
            if self._consumed:
                raise TargetSelectionLeaseError("target selection lease is exhausted")
            return self._choice_facts

    @property
    def consumed(self) -> bool:
        with self._lock:
            return self._consumed

    def discard(self) -> bool:
        """Expire the lease; return whether this call performed the expiry."""

        with self._lock:
            if self._consumed:
                return False
            self._consumed = True
            self._candidates = ()
            self._choice_facts = ()
            return True

    def consume(
        self,
        *,
        scope: TargetSelectionScope,
        candidate_ordinal: int,
    ) -> TargetCandidate:
        """Atomically consume once, then resolve against the original ordering."""

        with self._lock:
            if self._consumed:
                raise TargetSelectionLeaseError("target selection lease is exhausted")
            candidates = self._candidates
            self._consumed = True
            self._candidates = ()
            self._choice_facts = ()
        if scope != self._scope:
            raise TargetSelectionLeaseError("target selection lease is stale")
        if (
            type(candidate_ordinal) is not int
            or candidate_ordinal < 1
            or candidate_ordinal > len(candidates)
        ):
            raise TargetSelectionLeaseError("candidate_ordinal is outside the frozen mapping")
        return candidates[candidate_ordinal - 1]


__all__ = [
    "TargetChoiceFact", "TargetSelectionLease", "TargetSelectionLeaseError",
    "TargetSelectionScope",
]
