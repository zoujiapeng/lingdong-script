"""Deterministic progress guard for continuous surface mutations.

The controller can prove that input changed pixels without proving that the
visible result moved toward the user's goal.  This module keeps those signals
separate and blocks repeated geometry only after fresh semantic observations
show that two applied mutations left the visible state materially unchanged.

It is intentionally independent from the agent loop and from screen capture.
Callers provide already-extracted paths, target identity, structured effect
evidence, and semantic observation text.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence


Point = tuple[float, float]
Region = tuple[float, float, float, float]


@dataclass(frozen=True)
class SemanticProgressPolicy:
    """Conservative thresholds for the small local state machine."""

    description_similarity: float = 0.86
    verification_failure_similarity: float = 0.75
    target_region_iou: float = 0.80
    duplicate_ratio: float = 0.50
    duplicate_min_paths: int = 4
    shape_similarity: float = 0.90
    placement_iou: float = 0.70
    broad_surface_coverage: float = 0.12
    max_guard_blocks: int = 2
    resample_points: int = 16
    quantization_steps: int = 32


@dataclass(frozen=True)
class TargetKey:
    """Identity and optional physical region of one editable surface."""

    identity: str | int
    region: Region | None = None


@dataclass(frozen=True)
class GeometryFingerprint:
    """Compact, translation-tolerant description of one path mutation."""

    path_count: int
    unique_path_count: int
    duplicate_ratio: float
    shape_tokens: frozenset[tuple[int, ...]]
    placed_bbox: tuple[float, float, float, float]
    surface_coverage: float
    signature: str


@dataclass(frozen=True)
class GeometryComparison:
    """Why a candidate is or is not materially different."""

    duplicate_ratio: float
    shape_similarity: float
    placement_iou: float
    minimum_surface_coverage: float
    near_repeat: bool


@dataclass(frozen=True)
class GuardDecision:
    """Pre-dispatch decision returned to the caller."""

    allowed: bool
    reason: str
    safe_stop: bool = False
    requires_fresh_semantic: bool = False
    comparison: GeometryComparison | None = None
    guard_blocks: int = 0
    blocked_signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "allowed": self.allowed,
            "reason": self.reason,
            "safe_stop": self.safe_stop,
            "requires_fresh_semantic": self.requires_fresh_semantic,
            "semantic_progress_guard": not self.allowed,
            "guard_blocks": self.guard_blocks,
            "blocked_signature": self.blocked_signature,
        }
        if self.comparison is not None:
            payload["geometry"] = {
                "duplicate_ratio": round(self.comparison.duplicate_ratio, 6),
                "shape_similarity": round(self.comparison.shape_similarity, 6),
                "placement_iou": round(self.comparison.placement_iou, 6),
                "minimum_surface_coverage": round(
                    self.comparison.minimum_surface_coverage, 6,
                ),
                "near_repeat": self.comparison.near_repeat,
            }
        return payload


class ObservationKind(str, Enum):
    DESCRIPTION = "description"
    VERIFICATION_FAILURE = "verification_failure"


@dataclass(frozen=True)
class SemanticObservation:
    effect_generation: int
    kind: ObservationKind
    fingerprint: Counter[str]
    confidence: float | None = None


@dataclass(frozen=True)
class ProgressSnapshot:
    target: TargetKey | None
    effect_generation: int
    observation_generation: int
    pixel_effect_only: bool
    semantic_stagnation: bool
    fresh_semantic_required: bool
    guard_blocks: int
    blocked_signature: str


def _iter_dicts(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_dicts(child)


def structured_effect_applied(payload: Any) -> bool:
    """Return true only for an explicit successful pixel-effect record.

    A generic ``verified=true`` flag is deliberately insufficient: several
    controller routes use it to confirm dispatch or a local pixel delta, and
    neither one is semantic goal progress.
    """

    if not isinstance(payload, Mapping):
        return False
    if (
        payload.get("ok") is not True
        or payload.get("blocked") is True
        or payload.get("mutation_applied") is False
    ):
        return False
    return any(
        record.get("visual_change_detected") is True
        or record.get("effect_observed") is True
        for record in _iter_dicts(payload)
    )


def structured_primitive_paths(spec: Mapping[str, Any]) -> list[list[list[float]]]:
    """Expand one controller ``shape + box`` primitive into stable paths.

    ``execute_subgoal`` accepts compact structured primitives and only expands
    them to ``draw_strokes`` inside the controller.  The semantic-progress
    guard runs before that expansion, so it needs an equivalent, side-effect
    free geometry representation in the public 0..1000 canvas space.

    The expansion intentionally mirrors the controller's declared box
    contract: lines use endpoints unless ``box_mode=xywh`` is explicit, while
    closed shapes use ``[x, y, width, height]`` unless an endpoint mode is
    explicit.  Invalid/unknown primitives return no paths and remain
    fail-neutral instead of manufacturing geometry.
    """

    if not isinstance(spec, Mapping):
        return []
    explicit = " ".join((
        str(spec.get("shape") or ""),
        str(spec.get("object") or ""),
    )).strip().casefold()
    shape = ""
    for candidate, terms in (
        ("triangle", ("triangle", "三角", "三角形")),
        ("rectangle", ("rectangle", "square", "rect", "方形", "矩形", "正方形")),
        ("circle", ("circle", "圆", "圆形")),
        ("ellipse", ("ellipse", "oval", "椭圆")),
        ("line", ("line", "线", "直线")),
        ("check", ("check", "勾", "对号")),
    ):
        if any(term in explicit for term in terms):
            shape = candidate
            break
    if not shape:
        return []

    raw_box = spec.get("box")
    if not isinstance(raw_box, (list, tuple)):
        return []
    try:
        values = [float(value) for value in raw_box]
    except (TypeError, ValueError):
        return []
    if not all(math.isfinite(value) for value in values):
        return []

    def clamp(value: float) -> float:
        return max(0.0, min(1000.0, value))

    mode = str(spec.get("box_mode") or "").strip().casefold().replace("-", "_")
    if shape in {"triangle", "line"} and len(values) >= 6 and len(values) % 2 == 0:
        def safe_point_coordinate(value: float) -> float:
            bounded = clamp(value)
            if bounded <= 0.0:
                return 30.0
            if bounded >= 1000.0:
                return 970.0
            return bounded

        points = [
            [safe_point_coordinate(values[index]), safe_point_coordinate(values[index + 1])]
            for index in range(0, len(values), 2)
        ]
        if shape == "triangle" and points[-1] != points[0]:
            points.append(list(points[0]))
        return [points]

    if shape == "line":
        if len(values) != 4:
            return []
        x1, y1, third, fourth = values
        if mode in {"xywh", "offset", "width_height"}:
            x2, y2 = x1 + third, y1 + fourth
        else:
            x2, y2 = third, fourth
        return [[[clamp(x1), clamp(y1)], [clamp(x2), clamp(y2)]]]

    if len(values) != 4:
        return []
    x, y, third, fourth = values
    if mode in {"endpoints", "points", "xyxy"}:
        width, height = third - x, fourth - y
    else:
        width, height = third, fourth
    left = clamp(x)
    top = clamp(y)
    # Match the controller's normalized-region clamp: malformed negative or
    # out-of-bounds extents collapse to a tiny valid region, never flip sides.
    width = min(1000.0 - left, max(1.0, width))
    height = min(1000.0 - top, max(1.0, height))
    right = left + width
    bottom = top + height
    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0
    half = max(2.0, min(width, height) / 2.0)

    if shape == "rectangle":
        return [[[left, top], [right, top], [right, bottom], [left, bottom], [left, top]]]
    if shape == "triangle":
        return [[
            [center_x, center_y - half],
            [center_x - half, center_y + half],
            [center_x + half, center_y + half],
            [center_x, center_y - half],
        ]]
    if shape in {"circle", "ellipse"}:
        radius_x = half if shape == "circle" else max(2.0, width / 2.0)
        radius_y = half if shape == "circle" else max(2.0, height / 2.0)
        steps = 32 if shape == "circle" else 40
        return [[
            [
                center_x + radius_x * math.cos(math.tau * index / steps),
                center_y + radius_y * math.sin(math.tau * index / steps),
            ]
            for index in range(steps + 1)
        ]]
    # check
    return [[
        [center_x - half, center_y],
        [center_x - half / 4.0, center_y + half],
        [center_x + half, center_y - half],
    ]]


def _coerce_paths(paths: Sequence[Sequence[Sequence[float]]]) -> list[list[Point]]:
    if not isinstance(paths, Sequence) or isinstance(paths, (str, bytes)) or not paths:
        raise ValueError("paths must contain at least one path")
    result: list[list[Point]] = []
    for path_index, path in enumerate(paths):
        if (
            not isinstance(path, Sequence)
            or isinstance(path, (str, bytes))
            or len(path) < 2
        ):
            raise ValueError(f"paths[{path_index}] must contain at least two points")
        points: list[Point] = []
        for point_index, point in enumerate(path):
            if (
                not isinstance(point, Sequence)
                or isinstance(point, (str, bytes))
                or len(point) != 2
            ):
                raise ValueError(
                    f"paths[{path_index}][{point_index}] must be exactly [x, y]"
                )
            x, y = point
            if (
                isinstance(x, bool)
                or isinstance(y, bool)
                or not isinstance(x, (int, float))
                or not isinstance(y, (int, float))
                or not math.isfinite(float(x))
                or not math.isfinite(float(y))
            ):
                raise ValueError(
                    f"paths[{path_index}][{point_index}] coordinates must be finite numbers"
                )
            points.append((float(x), float(y)))
        result.append(points)
    return result


def _resample_path(points: Sequence[Point], count: int) -> tuple[Point, ...]:
    count = max(2, int(count))
    cleaned: list[Point] = [points[0]]
    for point in points[1:]:
        if point != cleaned[-1]:
            cleaned.append(point)
    if len(cleaned) == 1:
        return tuple(cleaned[0] for _ in range(count))

    cumulative = [0.0]
    for start, end in zip(cleaned, cleaned[1:]):
        cumulative.append(cumulative[-1] + math.dist(start, end))
    total = cumulative[-1]
    if total <= 1e-12:
        return tuple(cleaned[0] for _ in range(count))

    sampled: list[Point] = []
    segment = 0
    for index in range(count):
        distance = total * index / (count - 1)
        while segment + 1 < len(cumulative) and cumulative[segment + 1] < distance:
            segment += 1
        if segment + 1 >= len(cleaned):
            sampled.append(cleaned[-1])
            continue
        low, high = cumulative[segment], cumulative[segment + 1]
        ratio = 0.0 if high <= low else (distance - low) / (high - low)
        x1, y1 = cleaned[segment]
        x2, y2 = cleaned[segment + 1]
        sampled.append((x1 + (x2 - x1) * ratio, y1 + (y2 - y1) * ratio))
    return tuple(sampled)


def _bbox(points: Iterable[Point]) -> tuple[float, float, float, float]:
    materialized = list(points)
    xs = [point[0] for point in materialized]
    ys = [point[1] for point in materialized]
    return min(xs), min(ys), max(xs), max(ys)


def _canonical_token(
    points: Sequence[Point],
    *,
    bounds: tuple[float, float, float, float],
    quantization_steps: int,
) -> tuple[int, ...]:
    left, top, right, bottom = bounds
    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0
    scale = max(right - left, bottom - top, 1e-9)
    steps = max(4, int(quantization_steps))
    forward: list[int] = []
    for x, y in points:
        forward.extend((
            int(round((x - center_x) * steps / scale)),
            int(round((y - center_y) * steps / scale)),
        ))
    reverse: list[int] = []
    for x, y in reversed(points):
        reverse.extend((
            int(round((x - center_x) * steps / scale)),
            int(round((y - center_y) * steps / scale)),
        ))
    return min(tuple(forward), tuple(reverse))


def _normalize_surface_bbox(
    bounds: tuple[float, float, float, float],
    surface: Region,
) -> tuple[float, float, float, float]:
    left, top, width, height = (float(value) for value in surface)
    if width <= 0 or height <= 0:
        raise ValueError("surface width and height must be positive")
    x1, y1, x2, y2 = bounds
    normalized = (
        (x1 - left) / width,
        (y1 - top) / height,
        (x2 - left) / width,
        (y2 - top) / height,
    )
    return tuple(max(0.0, min(1.0, value)) for value in normalized)  # type: ignore[return-value]


def fingerprint_geometry(
    paths: Sequence[Sequence[Sequence[float]]],
    *,
    surface: Region = (0.0, 0.0, 1000.0, 1000.0),
    policy: SemanticProgressPolicy | None = None,
) -> GeometryFingerprint:
    """Create a stable path fingerprint in one known surface coordinate space."""

    policy = policy or SemanticProgressPolicy()
    prepared = _coerce_paths(paths)
    sampled = [
        _resample_path(path, policy.resample_points)
        for path in prepared
    ]
    intrinsic_tokens = [
        _canonical_token(
            path,
            bounds=_bbox(path),
            quantization_steps=policy.quantization_steps,
        )
        for path in sampled
    ]
    unique_path_count = len(set(intrinsic_tokens))
    path_count = len(sampled)

    all_points = [point for path in sampled for point in path]
    global_bounds = _bbox(all_points)
    shape_tokens = frozenset(
        _canonical_token(
            path,
            bounds=global_bounds,
            quantization_steps=policy.quantization_steps,
        )
        for path in sampled
    )
    placed_bbox = _normalize_surface_bbox(global_bounds, surface)
    x1, y1, x2, y2 = placed_bbox
    coverage = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    signature_source = json.dumps(
        {
            "shape": [list(token) for token in sorted(shape_tokens)],
            "placement": [round(value, 4) for value in placed_bbox],
            "count": path_count,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    signature = hashlib.sha256(signature_source.encode("utf-8")).hexdigest()[:20]
    return GeometryFingerprint(
        path_count=path_count,
        unique_path_count=unique_path_count,
        duplicate_ratio=1.0 - unique_path_count / path_count,
        shape_tokens=shape_tokens,
        placed_bbox=placed_bbox,
        surface_coverage=coverage,
        signature=signature,
    )


def _bbox_iou(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    ax1, ay1, ax2, ay2 = first
    bx1, by1, bx2, by2 = second
    intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(
        0.0, min(ay2, by2) - max(ay1, by1),
    )
    first_area = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    second_area = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = first_area + second_area - intersection
    return 0.0 if union <= 0.0 else intersection / union


def compare_geometry(
    previous: GeometryFingerprint,
    candidate: GeometryFingerprint,
    *,
    policy: SemanticProgressPolicy | None = None,
    repetition_expected: bool = False,
) -> GeometryComparison:
    """Compare path content, placement, and duplicate structure."""

    policy = policy or SemanticProgressPolicy()
    union = previous.shape_tokens | candidate.shape_tokens
    shape_similarity = (
        1.0
        if not union
        else len(previous.shape_tokens & candidate.shape_tokens) / len(union)
    )
    placement_iou = _bbox_iou(previous.placed_bbox, candidate.placed_bbox)
    minimum_coverage = min(
        previous.surface_coverage,
        candidate.surface_coverage,
    )
    duplicate_repeat = bool(
        not repetition_expected
        and candidate.path_count >= policy.duplicate_min_paths
        and candidate.duplicate_ratio >= policy.duplicate_ratio
    )
    shape_repeat = bool(
        shape_similarity >= policy.shape_similarity
        and (
            placement_iou >= policy.placement_iou
            or minimum_coverage >= policy.broad_surface_coverage
        )
    )
    return GeometryComparison(
        duplicate_ratio=candidate.duplicate_ratio,
        shape_similarity=shape_similarity,
        placement_iou=placement_iou,
        minimum_surface_coverage=minimum_coverage,
        near_repeat=duplicate_repeat or shape_repeat,
    )


def _normalized_semantic_text(value: Any) -> str:
    if isinstance(value, str):
        raw = value
    else:
        try:
            raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            raw = str(value)
    raw = unicodedata.normalize("NFKC", raw).casefold()[:12000]
    normalized = "".join(char if char.isalnum() else " " for char in raw)
    return re.sub(r"\s+", " ", normalized).strip()


def semantic_fingerprint(value: Any) -> Counter[str]:
    """Language-agnostic character-trigram fingerprint for short observations."""

    normalized = _normalized_semantic_text(value)
    if not normalized:
        return Counter()
    padded = f" {normalized} "
    if len(padded) < 3:
        return Counter({padded: 1})
    return Counter(padded[index:index + 3] for index in range(len(padded) - 2))


def semantic_similarity(first: Counter[str], second: Counter[str]) -> float:
    """Cosine similarity for two semantic fingerprints."""

    if not first or not second:
        return 0.0
    dot = sum(count * second.get(token, 0) for token, count in first.items())
    first_norm = math.sqrt(sum(count * count for count in first.values()))
    second_norm = math.sqrt(sum(count * count for count in second.values()))
    if first_norm <= 0.0 or second_norm <= 0.0:
        return 0.0
    return dot / (first_norm * second_norm)


def _same_target(
    first: TargetKey,
    second: TargetKey,
    *,
    minimum_region_iou: float,
) -> bool:
    if first.identity != second.identity:
        return False
    if first.region is None or second.region is None:
        return True

    def to_edges(region: Region) -> tuple[float, float, float, float]:
        left, top, width, height = region
        return left, top, left + width, top + height

    return _bbox_iou(to_edges(first.region), to_edges(second.region)) >= minimum_region_iou


class ContinuousSemanticProgress:
    """Small state machine that guards semantic stagnation without planning."""

    def __init__(self, policy: SemanticProgressPolicy | None = None) -> None:
        self.policy = policy or SemanticProgressPolicy()
        self._target: TargetKey | None = None
        self._effect_generation = 0
        self._last_geometry: GeometryFingerprint | None = None
        self._observations: list[SemanticObservation] = []
        self._revision_required = False
        self._awaiting_semantic_generation = 0
        self._pending_material_change = False
        self._guard_blocks = 0
        self._blocked_signature = ""

    @property
    def snapshot(self) -> ProgressSnapshot:
        observation_generation = (
            self._observations[-1].effect_generation if self._observations else 0
        )
        return ProgressSnapshot(
            target=self._target,
            effect_generation=self._effect_generation,
            observation_generation=observation_generation,
            pixel_effect_only=(
                self._effect_generation > 0
                and observation_generation < self._effect_generation
            ),
            semantic_stagnation=self._revision_required,
            fresh_semantic_required=bool(self._awaiting_semantic_generation),
            guard_blocks=self._guard_blocks,
            blocked_signature=self._blocked_signature,
        )

    def reset(self, target: TargetKey | None = None) -> None:
        self._target = target
        self._effect_generation = 0
        self._last_geometry = None
        self._observations.clear()
        self._revision_required = False
        self._awaiting_semantic_generation = 0
        self._pending_material_change = False
        self._guard_blocks = 0
        self._blocked_signature = ""

    def _ensure_target(self, target: TargetKey) -> bool:
        if self._target is None:
            self._target = target
            return False
        if _same_target(
            self._target,
            target,
            minimum_region_iou=self.policy.target_region_iou,
        ):
            self._target = target
            return False
        self.reset(target)
        return True

    def record_effect(
        self,
        *,
        target: TargetKey,
        geometry: GeometryFingerprint,
        applied: bool,
    ) -> bool:
        """Record execution effect only; this never records goal progress."""

        self._ensure_target(target)
        if not applied:
            self._pending_material_change = False
            return False
        self._effect_generation += 1
        self._last_geometry = geometry
        if self._pending_material_change:
            self._revision_required = False
            self._awaiting_semantic_generation = self._effect_generation
            self._guard_blocks = 0
            self._blocked_signature = ""
        self._pending_material_change = False
        return True

    def _record_observation(self, observation: SemanticObservation) -> bool:
        same_generation = bool(
            self._observations
            and self._observations[-1].effect_generation
            == observation.effect_generation
        )
        if same_generation:
            # Rewording the same pixels cannot manufacture progress or clear a
            # guard. Keep the latest text only for the next real generation.
            self._observations[-1] = observation
            return True

        self._observations.append(observation)
        self._observations = self._observations[-2:]
        if self._awaiting_semantic_generation == observation.effect_generation:
            self._awaiting_semantic_generation = 0

        stagnant = False
        if len(self._observations) == 2:
            previous, current = self._observations
            if (
                previous.effect_generation != current.effect_generation
                and previous.kind == current.kind
            ):
                threshold = (
                    self.policy.description_similarity
                    if current.kind == ObservationKind.DESCRIPTION
                    else self.policy.verification_failure_similarity
                )
                stagnant = semantic_similarity(
                    previous.fingerprint,
                    current.fingerprint,
                ) >= threshold
        self._revision_required = stagnant
        if not stagnant:
            self._guard_blocks = 0
            self._blocked_signature = ""
        return True

    def record_description(
        self,
        text: str,
        *,
        target: TargetKey,
        ok: bool = True,
    ) -> bool:
        """Record a free-text observation without treating it as success."""

        self._ensure_target(target)
        if not ok or self._effect_generation <= 0:
            return False
        fingerprint = semantic_fingerprint(text)
        if not fingerprint:
            return False
        return self._record_observation(SemanticObservation(
            effect_generation=self._effect_generation,
            kind=ObservationKind.DESCRIPTION,
            fingerprint=fingerprint,
        ))

    def record_verification(
        self,
        *,
        target: TargetKey,
        ok: bool,
        passed: bool,
        confidence: float | None,
        minimum_confidence: float = 0.80,
        details: Any = "",
    ) -> bool:
        """Record one structured high-confidence semantic verdict."""

        self._ensure_target(target)
        if (
            not ok
            or self._effect_generation <= 0
            or not isinstance(passed, bool)
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            return False
        try:
            threshold = float(minimum_confidence)
        except (TypeError, ValueError):
            threshold = 0.80
        threshold = max(0.0, min(1.0, threshold))
        if float(confidence) < threshold:
            return False
        if passed:
            self.reset(target)
            return True
        fingerprint = semantic_fingerprint(details or "verification failed")
        return self._record_observation(SemanticObservation(
            effect_generation=self._effect_generation,
            kind=ObservationKind.VERIFICATION_FAILURE,
            fingerprint=fingerprint,
            confidence=float(confidence),
        ))

    def evaluate_candidate(
        self,
        *,
        target: TargetKey,
        geometry: GeometryFingerprint,
        repetition_expected: bool = False,
    ) -> GuardDecision:
        """Evaluate one same-class mutation before dispatch."""

        target_changed = self._ensure_target(target)
        if target_changed:
            return GuardDecision(allowed=True, reason="target_changed")

        comparison = (
            compare_geometry(
                self._last_geometry,
                geometry,
                policy=self.policy,
                repetition_expected=repetition_expected,
            )
            if self._last_geometry is not None
            else None
        )
        if self._awaiting_semantic_generation:
            return self._blocked_decision(
                reason="fresh_semantic_required",
                geometry=geometry,
                comparison=comparison,
            )
        if not self._revision_required:
            return GuardDecision(
                allowed=True,
                reason="no_semantic_stagnation",
                comparison=comparison,
            )
        if comparison is not None and comparison.near_repeat:
            return self._blocked_decision(
                reason="material_change_required",
                geometry=geometry,
                comparison=comparison,
            )

        # Permit one objectively different repair. It only becomes a fresh
        # semantic requirement if execution actually changes pixels.
        self._pending_material_change = True
        return GuardDecision(
            allowed=True,
            reason="material_change_allowed",
            requires_fresh_semantic=True,
            comparison=comparison,
            guard_blocks=self._guard_blocks,
        )

    def _blocked_decision(
        self,
        *,
        reason: str,
        geometry: GeometryFingerprint,
        comparison: GeometryComparison | None,
    ) -> GuardDecision:
        self._guard_blocks += 1
        self._blocked_signature = geometry.signature
        return GuardDecision(
            allowed=False,
            reason=reason,
            safe_stop=self._guard_blocks >= self.policy.max_guard_blocks,
            requires_fresh_semantic=(reason == "fresh_semantic_required"),
            comparison=comparison,
            guard_blocks=self._guard_blocks,
            blocked_signature=self._blocked_signature,
        )
