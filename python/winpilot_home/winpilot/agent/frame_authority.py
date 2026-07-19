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


FrameRegion = tuple[int, int, int, int]
SubmittedSize = tuple[int, int]


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

    def matches(self, frame_id: int, epoch: int) -> bool:
        """Gate structured annotations against the current pixel identity."""

        with self._lock:
            state = self._current
            return bool(
                state is not None
                and state.frame_id == int(frame_id)
                and state.epoch == int(epoch)
            )
