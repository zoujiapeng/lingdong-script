"""Capture and publish the single frame allowed to ground GUI actions.

This owner performs only mechanical work.  It does not interpret pixels,
construct model messages, publish product events, or decide whether an action
or mission succeeded.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import time
from typing import Any

from .frame_authority import FrameAuthority, FrameState
from ..utils.image_capture import (
    VisualCaptureScope,
    capture_window_jpeg_b64,
    select_visual_capture_scope,
)
from ..utils.screenshot import Region, virtual_screen_region


@dataclass(frozen=True, slots=True)
class FrameCapture:
    """Immutable pixels and geometry awaiting an authority CAS commit."""

    epoch: int
    hwnd: int
    region: Region
    scope: str
    reason: str
    submitted_size: tuple[int, int]
    image_b64: str
    captured_at: float
    expected_previous_frame_id: int


class FrameObserver:
    """Mechanical two-phase frame observer shared by runtime callers.

    The split preserves the legacy ordering in which diagnostics run after the
    screenshot but before publication.  ``commit`` remains compare-and-swap
    guarded, so any authority change during those diagnostics fails closed.
    """

    def __init__(
        self,
        authority: FrameAuthority,
        *,
        capture: Callable[..., str] = capture_window_jpeg_b64,
        select_scope: Callable[..., VisualCaptureScope] = select_visual_capture_scope,
        settled_initial_capture: Callable[[int, Region], str] | None = None,
        virtual_desktop: Callable[[], Region] = virtual_screen_region,
        captured_at: Callable[[], float] = time.time,
        max_submitted_side: int = 1120,
    ) -> None:
        self._authority = authority
        self._capture = capture
        self._select_scope = select_scope
        self._settled_initial_capture = settled_initial_capture
        self._virtual_desktop = virtual_desktop
        self._captured_at = captured_at
        self._max_submitted_side = max(1, int(max_submitted_side))

    def capture(
        self,
        *,
        epoch: int,
        hwnd: int,
        phase: str,
        action: str = "",
        provided_image_b64: str = "",
        provided_region: Region | None = None,
        cached_capture: Mapping[str, Any] | None = None,
        related_window: Callable[[int | None, int | None], bool] | None = None,
        expected_previous_frame_id: int | None = None,
        scope_override: str | None = None,
    ) -> FrameCapture | None:
        """Capture an exact pixel/geometry pair without publishing it.

        ``scope_override`` lets a desktop-shell binding label its full-desktop
        frame as the bound target surface instead of ``full_desktop``.
        """

        if expected_previous_frame_id is None:
            previous = self._authority.current
            expected_previous_frame_id = previous.frame_id if previous is not None else 0

        region: Region
        if provided_region is not None:
            region = provided_region
            scope = (
                str(scope_override)
                if scope_override is not None
                else (
                    "full_desktop"
                    if region == self._virtual_desktop()
                    else "target_window"
                )
            )
            reason = "preflight_exact_capture_region"
        elif self._cache_matches(cached_capture, provided_image_b64, hwnd):
            assert cached_capture is not None
            region = cached_capture["region"]
            scope = str(
                scope_override
                or cached_capture.get("scope")
                or "target_window"
            )
            reason = str(
                cached_capture.get("scope_reason") or "observe_exact_capture_region"
            )
        else:
            decision = self._select_scope(
                int(hwnd) or None,
                enabled=None,
                related_window=related_window,
            )
            region = decision.region
            scope = str(scope_override or decision.scope)
            reason = str(decision.reason)

        if region.width < 16 or region.height < 16:
            return None

        image_b64 = str(provided_image_b64 or "")
        if not image_b64:
            if (
                phase == "initial"
                and action == "target_prebind"
                and self._settled_initial_capture is not None
            ):
                image_b64 = str(self._settled_initial_capture(int(hwnd), region) or "")
            else:
                image_b64 = str(self._capture(int(hwnd), region=region) or "")
        if not image_b64:
            return None

        scale = min(
            1.0,
            self._max_submitted_side / max(1, region.width, region.height),
        )
        return FrameCapture(
            epoch=int(epoch),
            hwnd=int(hwnd),
            region=region,
            scope=scope,
            reason=reason,
            submitted_size=(
                max(1, int(region.width * scale)),
                max(1, int(region.height * scale)),
            ),
            image_b64=image_b64,
            captured_at=float(self._captured_at()),
            expected_previous_frame_id=int(expected_previous_frame_id),
        )

    def commit(self, captured: FrameCapture) -> FrameState:
        """Publish a prepared capture if its authority predecessor is current."""

        region = captured.region
        return self._authority.commit(
            epoch=captured.epoch,
            hwnd=captured.hwnd,
            region=(region.left, region.top, region.width, region.height),
            scope=captured.scope,
            reason=captured.reason,
            submitted_size=captured.submitted_size,
            image_b64=captured.image_b64,
            captured_at=captured.captured_at,
            expected_previous_frame_id=captured.expected_previous_frame_id,
        )

    @staticmethod
    def _cache_matches(
        cached_capture: Mapping[str, Any] | None,
        provided_image_b64: str,
        hwnd: int,
    ) -> bool:
        if not provided_image_b64 or not isinstance(cached_capture, Mapping):
            return False
        try:
            return bool(
                cached_capture.get("image_b64") == provided_image_b64
                and int(cached_capture.get("hwnd") or 0) == int(hwnd)
                and cached_capture.get("region") is not None
            )
        except (TypeError, ValueError):
            return False
