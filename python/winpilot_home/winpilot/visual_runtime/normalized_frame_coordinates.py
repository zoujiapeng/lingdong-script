"""Pure normalized-frame to physical-screen point mapping.

This module knows only the authoritative physical frame region.  It does not
parse model payloads, repair provider output, inspect windows, or execute
input.  Runtime callers retain ownership of frame and target identity.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import TypeAlias


PhysicalFrameRegion: TypeAlias = tuple[int, int, int, int]
PhysicalPoint: TypeAlias = tuple[int, int]


@dataclass(frozen=True, slots=True)
class NormalizedFrameCoordinateMapper:
    """Map coordinates in the inclusive 0..1000 frame space to pixels.

    ``region`` is ``(left, top, width, height)``.  Its right and bottom edges
    are exclusive, so normalized coordinate 1000 maps to the final pixel
    inside the captured region rather than one pixel beyond it.
    """

    region: PhysicalFrameRegion

    def __post_init__(self) -> None:
        region = self.region
        if (
            not isinstance(region, tuple)
            or len(region) != 4
            or any(type(value) is not int for value in region)
            or region[2] <= 0
            or region[3] <= 0
        ):
            raise ValueError(
                "region must be an integer (left, top, width, height) tuple "
                "with positive width and height"
            )

    @staticmethod
    def _coordinate(value: object, field: str) -> float:
        if type(value) not in (int, float):
            raise ValueError(f"{field} must be a finite numeric value in 0..1000")
        try:
            parsed = float(value)
        except OverflowError:
            raise ValueError(
                f"{field} must be a finite numeric value in 0..1000"
            ) from None
        if not math.isfinite(parsed) or not 0.0 <= parsed <= 1000.0:
            raise ValueError(f"{field} must be a finite numeric value in 0..1000")
        return parsed

    def map_point(self, frame_x: object, frame_y: object) -> PhysicalPoint:
        """Return the physical pixel for one normalized-frame point."""

        normalized_x = self._coordinate(frame_x, "frame_x")
        normalized_y = self._coordinate(frame_y, "frame_y")
        left, top, width, height = self.region
        return (
            round(left + normalized_x / 1000.0 * max(0, width - 1)),
            round(top + normalized_y / 1000.0 * max(0, height - 1)),
        )


__all__ = [
    "NormalizedFrameCoordinateMapper",
    "PhysicalFrameRegion",
    "PhysicalPoint",
]
