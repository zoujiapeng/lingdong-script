"""Stable geometry contracts for visual interaction.

The classes in this module deliberately know nothing about Paint, games,
CAPTCHAs, or any other application.  They bind a detected interaction surface
to one authoritative window/frame identity, keep that binding stable while
content changes, and allow a planner or controller to declare a smaller action
ROI without redefining the underlying surface.

All bounds use ``[left, top, right, bottom]`` ordering in the declared
coordinate space.  The caller chooses the coordinate space (for example
``frame_0_1000`` or physical pixels) and must not mix spaces in one binding.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


Bounds = tuple[int, int, int, int]
FrameRegion = tuple[int, int, int, int]


def _bounds(values: Iterable[int]) -> Bounds:
    result = tuple(int(value) for value in values)
    if len(result) != 4:
        raise ValueError("interaction bounds must contain four coordinates")
    left, top, right, bottom = result
    if right <= left or bottom <= top:
        raise ValueError("interaction bounds must have positive width and height")
    return left, top, right, bottom


@dataclass(frozen=True)
class InteractionRegion:
    """One explicit interaction region in a named coordinate space."""

    bounds: Bounds
    coordinate_space: str = "frame_0_1000"
    label: str = "interaction_surface"
    source: str = ""
    confidence: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "bounds", _bounds(self.bounds))
        if not str(self.coordinate_space or "").strip():
            raise ValueError("coordinate_space is required")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    @property
    def left(self) -> int:
        return self.bounds[0]

    @property
    def top(self) -> int:
        return self.bounds[1]

    @property
    def right(self) -> int:
        return self.bounds[2]

    @property
    def bottom(self) -> int:
        return self.bounds[3]

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def area(self) -> int:
        return self.width * self.height

    def contains(self, other: "InteractionRegion", *, tolerance: int = 0) -> bool:
        self._require_same_space(other)
        margin = max(0, int(tolerance))
        return bool(
            self.left - margin <= other.left
            and self.top - margin <= other.top
            and self.right + margin >= other.right
            and self.bottom + margin >= other.bottom
        )

    def contains_point(self, x: int, y: int, *, tolerance: int = 0) -> bool:
        margin = max(0, int(tolerance))
        return bool(
            self.left - margin <= int(x) <= self.right + margin
            and self.top - margin <= int(y) <= self.bottom + margin
        )

    def intersection(self, other: "InteractionRegion") -> "InteractionRegion | None":
        self._require_same_space(other)
        left = max(self.left, other.left)
        top = max(self.top, other.top)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return None
        return InteractionRegion(
            (left, top, right, bottom),
            coordinate_space=self.coordinate_space,
            label=self.label,
            source=self.source,
            confidence=min(self.confidence, other.confidence),
        )

    def inset(self, amount: int) -> "InteractionRegion":
        inset = max(0, int(amount))
        if self.width <= inset * 2 or self.height <= inset * 2:
            return self
        return InteractionRegion(
            (
                self.left + inset,
                self.top + inset,
                self.right - inset,
                self.bottom - inset,
            ),
            coordinate_space=self.coordinate_space,
            label=self.label,
            source=self.source,
            confidence=self.confidence,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "bounds": list(self.bounds),
            "coordinate_space": self.coordinate_space,
            "label": self.label,
            "source": self.source,
            "confidence": round(float(self.confidence), 4),
        }

    def _require_same_space(self, other: "InteractionRegion") -> None:
        if self.coordinate_space != other.coordinate_space:
            raise ValueError(
                "interaction regions use different coordinate spaces: "
                f"{self.coordinate_space!r} != {other.coordinate_space!r}"
            )


@dataclass(frozen=True)
class SurfaceBindingResult:
    """Decision returned for one authoritative visual observation."""

    region: InteractionRegion | None
    policy: str
    generation: int
    blocked_reason: str = ""
    retained_anchor: bool = False

    @property
    def ok(self) -> bool:
        return self.region is not None and not self.blocked_reason

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "policy": self.policy,
            "generation": self.generation,
            "blocked_reason": self.blocked_reason,
            "retained_anchor": self.retained_anchor,
            "region": self.region.to_dict() if self.region else None,
        }


class SurfaceBinding:
    """Temporal binding for one window-backed visual interaction surface.

    The client region is an authority envelope, not an automatic action area.
    A credible visual candidate is still required.  Once established, ordinary
    detector jitter and edge-continuous shrink retain the original outer
    boundary.  A central subset is treated as a possible overlay, while a true
    layout displacement must be observed repeatedly before rebinding.
    """

    def __init__(
        self,
        *,
        edge_tolerance: int = 12,
        displacement_confirmations: int = 2,
        min_retained_area_ratio: float = 0.35,
    ) -> None:
        self.edge_tolerance = max(0, int(edge_tolerance))
        self.displacement_confirmations = max(2, int(displacement_confirmations))
        self.min_retained_area_ratio = max(
            0.05, min(1.0, float(min_retained_area_ratio)),
        )
        self._anchor: InteractionRegion | None = None
        self._target_key: tuple[object, ...] | None = None
        self._pending: InteractionRegion | None = None
        self._pending_count = 0
        self._generation = 0
        self._last_frame_id = 0
        self._action_rois: dict[str, InteractionRegion] = {}

    @property
    def anchor(self) -> InteractionRegion | None:
        return self._anchor

    @property
    def generation(self) -> int:
        return self._generation

    def reset(self) -> None:
        self._anchor = None
        self._target_key = None
        self._pending = None
        self._pending_count = 0
        self._last_frame_id = 0
        self._action_rois.clear()

    def update(
        self,
        candidate: InteractionRegion | None,
        *,
        hwnd: int,
        frame_region: FrameRegion,
        capture_scope: str,
        client_region: InteractionRegion | None = None,
        frame_id: int = 0,
        ambiguous: bool = False,
        initial: bool = False,
    ) -> SurfaceBindingResult:
        """Bind one candidate from an authoritative frame.

        ``client_region`` must use the same coordinate space as ``candidate``.
        It clips antialiasing/window-border spill but is never promoted to a
        mutation surface by itself.  Positive ``frame_id`` values are required
        to increase monotonically for a stable target.
        """
        normalized_frame = tuple(int(value) for value in frame_region)
        if len(normalized_frame) != 4:
            raise ValueError("frame_region must contain left, top, width, height")
        if normalized_frame[2] <= 0 or normalized_frame[3] <= 0:
            raise ValueError("frame_region width and height must be positive")
        client_key = (
            client_region.coordinate_space,
            client_region.bounds,
        ) if client_region is not None else None
        target_key = (
            int(hwnd), normalized_frame, str(capture_scope or ""), client_key,
        )
        same_target = self._target_key == target_key and self._anchor is not None
        if candidate is not None and client_region is not None:
            try:
                candidate = candidate.intersection(client_region)
            except ValueError:
                candidate = None
            if candidate is None:
                if initial or not same_target:
                    self._target_key = target_key
                    self._anchor = None
                    self._clear_pending()
                    self._last_frame_id = max(0, int(frame_id))
                    self._action_rois.clear()
                return self._blocked(
                    "candidate_outside_window_client_region",
                    "candidate was outside or incompatible with the window client region",
                )
        if initial or not same_target:
            self._target_key = target_key
            self._pending = None
            self._pending_count = 0
            self._last_frame_id = max(0, int(frame_id))
            self._action_rois.clear()
            if ambiguous or candidate is None:
                self._anchor = None
                return self._blocked(
                    "ambiguous_initial_surface" if ambiguous else "not_detected",
                    "current pixels did not ground one interaction surface",
                )
            self._anchor = candidate
            self._generation += 1
            return SurfaceBindingResult(
                candidate, "fresh_target_binding", self._generation,
            )

        assert self._anchor is not None
        current_frame_id = max(0, int(frame_id))
        if (
            current_frame_id
            and self._last_frame_id
            and current_frame_id <= self._last_frame_id
        ):
            return self._blocked(
                "stale_authoritative_frame",
                "surface observation did not advance the authoritative frame",
            )
        if current_frame_id:
            self._last_frame_id = current_frame_id
        if ambiguous:
            self._clear_pending()
            return self._blocked(
                "ambiguous_current_surface_fail_closed",
                "current pixels contain multiple or obscured interaction surfaces",
            )
        if candidate is None:
            self._clear_pending()
            return self._blocked(
                "current_detection_missing_fail_closed",
                "current pixels no longer ground the bound interaction surface",
            )
        if candidate.coordinate_space != self._anchor.coordinate_space:
            self._clear_pending()
            return self._blocked(
                "coordinate_space_changed_fail_closed",
                "candidate coordinate space changed inside one target binding",
            )

        anchor = self._anchor
        deltas = tuple(
            abs(current - previous)
            for current, previous in zip(candidate.bounds, anchor.bounds)
        )
        matching_edges = sum(
            delta <= self.edge_tolerance for delta in deltas
        )
        candidate_is_subset = anchor.contains(
            candidate, tolerance=self.edge_tolerance,
        )
        candidate_area_ratio = candidate.area / max(1, anchor.area)
        detector_jitter = all(delta <= self.edge_tolerance for delta in deltas)
        edge_continuous_shrink = bool(
            candidate_is_subset
            and matching_edges >= 3
            and candidate_area_ratio >= self.min_retained_area_ratio
        )
        if detector_jitter or edge_continuous_shrink:
            self._clear_pending()
            return SurfaceBindingResult(
                anchor,
                (
                    "stable_binding_retained_after_component_shrink"
                    if edge_continuous_shrink
                    and candidate.area < anchor.area * 0.90
                    else "stable_binding_retained_after_detector_jitter"
                ),
                self._generation,
                retained_anchor=True,
            )
        if candidate_is_subset:
            self._clear_pending()
            return self._blocked(
                "possible_nested_surface_fail_closed",
                "a central subset may be an overlay rather than a new surface",
            )

        if self._same_pending(candidate):
            self._pending_count += 1
        else:
            self._pending = candidate
            self._pending_count = 1
        if self._pending_count < self.displacement_confirmations:
            return self._blocked(
                "layout_displacement_pending_confirmation",
                "surface displacement requires repeated matching frames",
            )

        self._anchor = candidate
        self._generation += 1
        self._clear_pending()
        self._action_rois.clear()
        return SurfaceBindingResult(
            candidate,
            "rebound_after_confirmed_layout_displacement",
            self._generation,
        )

    def declare_action_roi(
        self,
        name: str,
        region: InteractionRegion,
    ) -> InteractionRegion:
        """Declare a task/action-specific ROI inside the stable surface.

        This is intentionally generic: an ROI may represent a drag lane, a
        form field, a verification target, a board, or any other region chosen
        from current evidence.  Declaring it never changes the outer surface.
        """
        key = str(name or "").strip()
        if not key:
            raise ValueError("action ROI name is required")
        if self._anchor is None:
            raise RuntimeError("cannot declare an action ROI without a surface binding")
        if not self._anchor.contains(region, tolerance=self.edge_tolerance):
            raise ValueError("action ROI must be contained by the bound surface")
        self._action_rois[key] = region
        return region

    def action_region(self, name: str | None = None) -> InteractionRegion | None:
        if name is None:
            return self._anchor
        return self._action_rois.get(str(name))

    def metadata(self) -> dict[str, object]:
        return {
            "generation": self._generation,
            "last_frame_id": self._last_frame_id,
            "surface": self._anchor.to_dict() if self._anchor else None,
            "action_rois": {
                name: region.to_dict()
                for name, region in sorted(self._action_rois.items())
            },
        }

    def _same_pending(self, candidate: InteractionRegion) -> bool:
        return bool(
            self._pending is not None
            and self._pending.coordinate_space == candidate.coordinate_space
            and all(
                abs(current - previous) <= self.edge_tolerance
                for current, previous in zip(
                    candidate.bounds, self._pending.bounds,
                )
            )
        )

    def _clear_pending(self) -> None:
        self._pending = None
        self._pending_count = 0

    def _blocked(self, policy: str, reason: str) -> SurfaceBindingResult:
        return SurfaceBindingResult(
            None,
            policy,
            self._generation,
            blocked_reason=reason,
        )
