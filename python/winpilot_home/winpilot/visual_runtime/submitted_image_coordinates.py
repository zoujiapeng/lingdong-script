"""Shared pure submitted-image to normalized-frame coordinate mapping.

This module knows only the exact image dimensions submitted to a visual model.
It does not parse actions, read runtime/session state, capture pixels, publish
events, or execute input.  Callers retain ownership of frame identity and the
later normalized-frame to physical-screen mapping.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping


SubmittedSize = tuple[int, int]


@dataclass(frozen=True, slots=True)
class SubmittedImageCoordinateMapper:
    """Map exact submitted-image pixels without mutating caller payloads."""

    submitted_size: SubmittedSize | None

    def _dimensions(self, *, missing_error: str) -> SubmittedSize:
        size = self.submitted_size
        if (
            not isinstance(size, tuple)
            or len(size) != 2
            or any(type(value) is not int for value in size)
            or min(size) <= 0
        ):
            raise ValueError(missing_error)
        return size

    @staticmethod
    def _number(
        value: Any,
        field: str,
        *,
        numeric_error: str | None = None,
    ) -> float:
        if isinstance(value, bool):
            raise ValueError(
                numeric_error
                or f"{field} must be a numeric submitted-image pixel coordinate"
            )
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            raise ValueError(
                numeric_error
                or f"{field} must be a numeric submitted-image pixel coordinate"
            ) from None
        return parsed

    def map_point_pair(
        self,
        payload: Mapping[str, Any],
        *,
        image_keys: tuple[str, str],
        frame_keys: tuple[str, str],
        context: str,
    ) -> tuple[dict[str, Any], dict[str, float]]:
        """Return a copied payload and exact image-coordinate provenance."""

        mapped = dict(payload)
        image_x_key, image_y_key = image_keys
        frame_x_key, frame_y_key = frame_keys
        present = (image_x_key in mapped, image_y_key in mapped)
        if not any(present):
            return mapped, {}
        if not all(present):
            raise ValueError(
                f"{context} requires both {image_x_key} and {image_y_key}"
            )
        if frame_x_key in mapped or frame_y_key in mapped:
            raise ValueError(
                f"{context} received mixed submitted-image and normalized-frame coordinates"
            )

        image_width, image_height = self._dimensions(
            missing_error=(
                f"{context} submitted-image coordinates require the current "
                "frame image dimensions"
            ),
        )
        image_x = self._number(mapped[image_x_key], image_x_key)
        image_y = self._number(mapped[image_y_key], image_y_key)
        if not (
            math.isfinite(image_x)
            and math.isfinite(image_y)
            and 0.0 <= image_x <= max(0, image_width - 1)
            and 0.0 <= image_y <= max(0, image_height - 1)
        ):
            raise ValueError(
                f"{image_x_key}/{image_y_key} must stay inside the exact submitted "
                f"image bounds 0..{max(0, image_width - 1)} by "
                f"0..{max(0, image_height - 1)}"
            )

        mapped.pop(image_x_key)
        mapped.pop(image_y_key)
        mapped[frame_x_key] = image_x * 1000.0 / max(1, image_width - 1)
        mapped[frame_y_key] = image_y * 1000.0 / max(1, image_height - 1)
        return mapped, {image_x_key: image_x, image_y_key: image_y}

    def _map_ordered_points(
        self, raw_points: list[Any], *, field: str, context: str,
    ) -> tuple[list[list[float]], list[list[float]]]:
        frame_points: list[list[float]] = []
        image_points: list[list[float]] = []
        for index, raw in enumerate(raw_points):
            if not isinstance(raw, (list, tuple)) or len(raw) != 2:
                raise ValueError(f"{field}[{index}] must be [x,y]")
            normalized, original = self.map_point_pair(
                {"image_x": raw[0], "image_y": raw[1]},
                image_keys=("image_x", "image_y"),
                frame_keys=("frame_x", "frame_y"), context=context,
            )
            frame_points.append([normalized["frame_x"], normalized["frame_y"]])
            image_points.append([original["image_x"], original["image_y"]])
        return frame_points, image_points

    def map_drag_image_coordinates(
        self, payload: Mapping[str, Any], *, context: str, max_points: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Map one image-authored drag while preserving ordered path intent."""

        mapped = dict(payload)
        endpoint_keys = ("image_x1", "image_y1", "image_x2", "image_y2")
        if not any(key in mapped for key in endpoint_keys):
            if "image_points" in mapped:
                raise ValueError(
                    "image_points require drag endpoints in the same submitted-image coordinate source"
                )
            return mapped, {}

        source: dict[str, Any] = {}
        for suffix in ("1", "2"):
            mapped, endpoint = self.map_point_pair(
                mapped,
                image_keys=(f"image_x{suffix}", f"image_y{suffix}"),
                frame_keys=(f"frame_x{suffix}", f"frame_y{suffix}"), context=context,
            )
            source.update(endpoint)
        if len(source) != 4:
            raise ValueError("drag submitted-image coordinates require both complete endpoints")
        if "frame_points" in mapped:
            raise ValueError("drag received image endpoints with normalized frame_points")

        if "image_points" not in mapped:
            return mapped, source
        raw_points = mapped.pop("image_points")
        if not isinstance(raw_points, list):
            raise ValueError("image_points must be an array of [x,y] pixels")
        compact = [raw for index, raw in enumerate(raw_points)
                   if not index or raw != raw_points[index - 1]]
        if len(compact) > max_points:
            raise ValueError(f"image_points accepts at most {max_points} meaningful points")

        frame_points, image_points = self._map_ordered_points(
            compact, field="image_points", context=context,
        )
        mapped["frame_points"] = frame_points
        source["image_points"] = image_points
        return mapped, source

    def map_image_strokes(
        self, payload: Mapping[str, Any], *, context: str, max_strokes: int,
        max_points_per_stroke: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Map image-authored strokes without simplifying or reordering them."""

        mapped = dict(payload)
        if "image_strokes" not in mapped:
            return mapped, {}
        if "frame_strokes" in mapped:
            raise ValueError(
                f"{context} received mixed submitted-image and normalized-frame coordinates"
            )
        raw_strokes = mapped.pop("image_strokes")
        if not isinstance(raw_strokes, list) or not raw_strokes:
            raise ValueError("image_strokes must contain at least one stroke")
        if len(raw_strokes) > max_strokes:
            raise ValueError(f"image_strokes accepts at most {max_strokes} strokes")

        frame_strokes: list[list[list[float]]] = []
        image_strokes: list[list[list[float]]] = []
        for stroke_index, raw_stroke in enumerate(raw_strokes):
            if not isinstance(raw_stroke, list) or not (
                2 <= len(raw_stroke) <= max_points_per_stroke
            ):
                raise ValueError(
                    f"image_strokes[{stroke_index}] must contain 2..{max_points_per_stroke} [x,y] points"
                )
            frame_stroke, image_stroke = self._map_ordered_points(
                raw_stroke, field=f"image_strokes[{stroke_index}]", context=context,
            )
            frame_strokes.append(frame_stroke)
            image_strokes.append(image_stroke)
        mapped["frame_strokes"] = frame_strokes
        return mapped, {"image_strokes": image_strokes}

    def map_constraint_image_region(
        self,
        payload: Mapping[str, Any],
        *,
        context: str,
    ) -> tuple[dict[str, Any], list[float]]:
        """Map one inclusive image-bounds constraint on a copied payload."""

        mapped = dict(payload)
        if "constraint_image_region" not in mapped:
            return mapped, []
        if "constraint_frame_region" in mapped:
            raise ValueError(
                f"{context} received both submitted-image and normalized-frame constraints"
            )

        image_width, image_height = self._dimensions(
            missing_error=(
                f"{context} submitted-image constraint requires the current "
                "image dimensions"
            ),
        )
        raw = mapped["constraint_image_region"]
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            raise ValueError(
                "constraint_image_region requires [left,top,right,bottom] pixel bounds"
            )
        numeric_error = (
            "constraint_image_region requires numeric "
            "[left,top,right,bottom] pixel bounds"
        )
        image_left, image_top, image_right, image_bottom = (
            self._number(
                value,
                "constraint_image_region",
                numeric_error=numeric_error,
            )
            for value in raw
        )
        if not (
            all(math.isfinite(value) for value in (
                image_left, image_top, image_right, image_bottom,
            ))
            and 0.0 <= image_left < image_right <= max(0, image_width - 1)
            and 0.0 <= image_top < image_bottom <= max(0, image_height - 1)
        ):
            raise ValueError(
                "constraint_image_region must be a positive rectangle inside the exact "
                f"submitted image bounds 0..{max(0, image_width - 1)} by "
                f"0..{max(0, image_height - 1)}"
            )

        source = [image_left, image_top, image_right, image_bottom]
        mapped.pop("constraint_image_region")
        mapped["constraint_frame_region"] = [
            image_left * 1000.0 / max(1, image_width - 1),
            image_top * 1000.0 / max(1, image_height - 1),
            image_right * 1000.0 / max(1, image_width - 1),
            image_bottom * 1000.0 / max(1, image_height - 1),
        ]
        return mapped, source


__all__ = ["SubmittedImageCoordinateMapper", "SubmittedSize"]
