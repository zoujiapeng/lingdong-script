"""Canonical run-scoped storage for already-constructed vNext evidence."""
from __future__ import annotations

from collections.abc import Iterable
import threading

from ..mission.contracts import EvidenceRef


class EvidenceRegistryError(ValueError):
    """Evidence cannot be accepted without changing its stated identity."""


class EvidenceRegistryClosed(EvidenceRegistryError):
    """The run-scoped registry no longer accepts writes."""


class EvidenceRegistry:
    """Thread-safe immutable-evidence index for exactly one mission and run.

    The registry never creates, enriches, or interprets evidence.  An ID may
    be appended repeatedly only when the complete :class:`EvidenceRef` is
    identical.  Snapshots are sorted by ID so concurrent insertion order does
    not become replay state.
    """

    def __init__(self, mission_id: str, run_id: str) -> None:
        if type(mission_id) is not str or not mission_id.strip():
            raise EvidenceRegistryError("mission_id must be a non-empty string")
        if type(run_id) is not str or not run_id.strip():
            raise EvidenceRegistryError("run_id must be a non-empty string")
        self.mission_id = mission_id.strip()
        self.run_id = run_id.strip()
        self._refs: dict[str, EvidenceRef] = {}
        self._closed = False
        self._lock = threading.RLock()

    def _validate(self, ref: object) -> EvidenceRef:
        if not isinstance(ref, EvidenceRef):
            raise EvidenceRegistryError("registry accepts only EvidenceRef values")
        if ref.mission_id != self.mission_id or ref.run_id != self.run_id:
            raise EvidenceRegistryError(
                f"evidence {ref.id!r} is outside registry mission/run scope"
            )
        return ref

    def append(self, ref: EvidenceRef) -> EvidenceRef:
        """Idempotently append one complete reference."""

        return self.append_many((ref,))[0]

    def append_many(self, refs: Iterable[EvidenceRef]) -> tuple[EvidenceRef, ...]:
        """Atomically append a batch, or leave the registry unchanged."""

        if isinstance(refs, (str, bytes)):
            raise EvidenceRegistryError("refs must be an iterable of EvidenceRef")
        try:
            candidates = tuple(refs)
        except TypeError as exc:
            raise EvidenceRegistryError(
                "refs must be an iterable of EvidenceRef"
            ) from exc

        batch: dict[str, EvidenceRef] = {}
        for candidate in candidates:
            ref = self._validate(candidate)
            previous = batch.get(ref.id)
            if previous is not None and previous != ref:
                raise EvidenceRegistryError(
                    f"conflicting evidence identity in batch: {ref.id}"
                )
            batch[ref.id] = ref

        with self._lock:
            if self._closed:
                raise EvidenceRegistryClosed("evidence registry is closed")
            for evidence_id, ref in batch.items():
                previous = self._refs.get(evidence_id)
                if previous is not None and previous != ref:
                    raise EvidenceRegistryError(
                        f"conflicting evidence identity: {evidence_id}"
                    )
            self._refs.update(batch)
            return tuple(self._refs[key] for key in sorted(batch))

    def snapshot(self) -> tuple[EvidenceRef, ...]:
        """Return a stable immutable view accepted by EvidenceScope."""

        with self._lock:
            return tuple(self._refs[key] for key in sorted(self._refs))

    def close(self) -> None:
        with self._lock:
            self._closed = True

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def __len__(self) -> int:
        with self._lock:
            return len(self._refs)


__all__ = [
    "EvidenceRegistry",
    "EvidenceRegistryClosed",
    "EvidenceRegistryError",
]
