"""Single owner for the pixels that ground the next GUI action.

``FrameState`` is immutable.  Consumers may retain an older state for history,
but only ``FrameAuthority.current`` is allowed to ground a new action.  The
authority swaps that reference once, after a capture and all of its geometry
have been assembled; it never captures the screen itself.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import threading
import time
from typing import Callable, Protocol, TypeVar


FrameRegion = tuple[int, int, int, int]
SubmittedSize = tuple[int, int]
_CallResult = TypeVar("_CallResult")


class FrameIdentityLike(Protocol):
    """Structural identity accepted by the atomic frame commit gate."""

    frame_id: int
    epoch: int
    hwnd: int
    region: FrameRegion
    scope: str
    submitted_size: SubmittedSize
    image_sha256: str


@dataclass(frozen=True, slots=True)
class FrameState:
    """Immutable identity and payload for one submitted visual frame."""

    frame_id: int
    epoch: int
    hwnd: int
    region: FrameRegion
    scope: str
    reason: str
    submitted_size: SubmittedSize
    image_b64: str
    image_sha256: str
    captured_at: float
    committed_at: float

    @property
    def commit_identity(self) -> tuple[
        int,
        int,
        int,
        FrameRegion,
        str,
        SubmittedSize,
        str,
    ]:
        """Return the complete immutable identity used at input commit time."""

        return (
            self.frame_id,
            self.epoch,
            self.hwnd,
            self.region,
            self.scope,
            self.submitted_size,
            self.image_sha256,
        )

    def is_fresh_for(
        self,
        *,
        epoch: int,
        max_age_s: float | None = None,
        now: float | None = None,
    ) -> bool:
        """Return whether this state is still usable in the supplied epoch."""

        if int(epoch) != self.epoch:
            return False
        if max_age_s is None:
            return True
        age = (time.time() if now is None else float(now)) - self.captured_at
        return age <= max(0.0, float(max_age_s))


class FrameAuthority:
    """Atomically publishes the one frame allowed to ground current actions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._current: FrameState | None = None
        self._pin_owner_thread_id: int | None = None

    @property
    def current(self) -> FrameState | None:
        with self._lock:
            return self._current

    def commit(
        self,
        *,
        epoch: int,
        hwnd: int,
        region: FrameRegion,
        scope: str,
        reason: str,
        submitted_size: SubmittedSize,
        image_b64: str,
        captured_at: float,
        expected_previous_frame_id: int | None = None,
    ) -> FrameState:
        """Build and publish a complete state in one locked reference swap."""

        normalized_region = tuple(int(value) for value in region)
        normalized_size = tuple(int(value) for value in submitted_size)
        if len(normalized_region) != 4 or min(normalized_region[2:]) <= 0:
            raise ValueError("frame region must contain positive width and height")
        if len(normalized_size) != 2 or min(normalized_size) <= 0:
            raise ValueError("submitted size must contain positive width and height")
        if not image_b64:
            raise ValueError("authoritative frame payload must not be empty")

        with self._lock:
            if self._pin_owner_thread_id == threading.get_ident():
                raise RuntimeError(
                    "cannot commit an authoritative frame from its pinned callback"
                )
            previous_id = self._current.frame_id if self._current is not None else 0
            if (
                expected_previous_frame_id is not None
                and int(expected_previous_frame_id) != previous_id
            ):
                raise RuntimeError("authoritative frame changed before commit")
            state = FrameState(
                frame_id=previous_id + 1,
                epoch=int(epoch),
                hwnd=int(hwnd),
                region=normalized_region,  # type: ignore[arg-type]
                scope=str(scope or ""),
                reason=str(reason or ""),
                submitted_size=normalized_size,  # type: ignore[arg-type]
                image_b64=str(image_b64),
                image_sha256=hashlib.sha256(image_b64.encode("ascii")).hexdigest(),
                captured_at=float(captured_at),
                committed_at=time.time(),
            )
            self._current = state
            return state

    def run_if_current(
        self,
        expected: FrameIdentityLike,
        callback: Callable[[], _CallResult],
    ) -> tuple[bool, _CallResult | None]:
        """Call once under the authority lock iff ``expected`` is still current.

        This is a pin-and-call boundary, not a check-then-act helper: another
        thread cannot publish a new frame between the identity comparison and
        the callback.  A rejected identity never invokes the callback.  The
        callback's exceptions are deliberately not translated because input
        may already have started by then.
        """

        owner_thread_id = threading.get_ident()
        with self._lock:
            if self._pin_owner_thread_id == owner_thread_id:
                raise RuntimeError(
                    "cannot recursively enter an authoritative frame pin"
                )
            state = self._current
            if state is None or state.commit_identity != (
                expected.frame_id,
                expected.epoch,
                expected.hwnd,
                expected.region,
                expected.scope,
                expected.submitted_size,
                expected.image_sha256,
            ):
                return False, None
            self._pin_owner_thread_id = owner_thread_id
            try:
                return True, callback()
            finally:
                self._pin_owner_thread_id = None

    def matches(self, frame_id: int, epoch: int) -> bool:
        """Gate structured annotations against the current pixel identity."""

        with self._lock:
            state = self._current
            return bool(
                state is not None
                and state.frame_id == int(frame_id)
                and state.epoch == int(epoch)
            )
