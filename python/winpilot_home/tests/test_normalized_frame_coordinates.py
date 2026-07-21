from __future__ import annotations

import math
import random

import pytest

from winpilot.visual_runtime.normalized_frame_coordinates import (
    NormalizedFrameCoordinateMapper,
)


@pytest.mark.parametrize(
    "region, expected_corners",
    [
        ((10, 20, 101, 51), ((10, 20), (110, 20), (10, 70), (110, 70))),
        ((-1920, -1080, 1920, 1080), (
            (-1920, -1080), (-1, -1080), (-1920, -1), (-1, -1),
        )),
        ((-7, 13, 1, 1), ((-7, 13), (-7, 13), (-7, 13), (-7, 13))),
    ],
)
def test_maps_all_corners_inside_the_inclusive_physical_pixel_bounds(
    region: tuple[int, int, int, int],
    expected_corners: tuple[tuple[int, int], ...],
) -> None:
    mapper = NormalizedFrameCoordinateMapper(region)

    assert (
        mapper.map_point(0, 0),
        mapper.map_point(1000, 0),
        mapper.map_point(0, 1000),
        mapper.map_point(1000, 1000),
    ) == expected_corners


def test_preserves_the_existing_rounding_formula() -> None:
    mapper = NormalizedFrameCoordinateMapper((-11, 7, 4, 6))

    assert mapper.map_point(500, 500) == (
        round(-11 + 0.5 * 3),
        round(7 + 0.5 * 5),
    )


@pytest.mark.parametrize(
    "coordinate",
    [
        True,
        False,
        None,
        "250",
        [],
        math.nan,
        math.inf,
        -math.inf,
        -0.001,
        1000.001,
        pytest.param(10**10_000, id="oversized-integer"),
    ],
)
def test_rejects_non_numeric_non_finite_and_out_of_bounds_coordinates(
    coordinate: object,
) -> None:
    mapper = NormalizedFrameCoordinateMapper((0, 0, 100, 100))

    with pytest.raises(ValueError, match="finite numeric value in 0..1000"):
        mapper.map_point(coordinate, 500)
    with pytest.raises(ValueError, match="finite numeric value in 0..1000"):
        mapper.map_point(500, coordinate)


@pytest.mark.parametrize(
    "region",
    [
        None,
        [0, 0, 1, 1],
        (0, 0, 1),
        (0, 0, 1, 1, 1),
        (False, 0, 1, 1),
        (0, 0, 0, 1),
        (0, 0, 1, 0),
        (0, 0, -1, 1),
        (0, 0, 1.0, 1),
    ],
)
def test_rejects_invalid_physical_regions(region: object) -> None:
    with pytest.raises(ValueError, match="positive width and height"):
        NormalizedFrameCoordinateMapper(region)  # type: ignore[arg-type]


def test_randomized_mapping_matches_formula_and_remains_inside_region() -> None:
    rng = random.Random(20260722)
    for _ in range(1000):
        left = rng.randint(-10_000, 10_000)
        top = rng.randint(-10_000, 10_000)
        width = rng.randint(1, 8192)
        height = rng.randint(1, 8192)
        frame_x = rng.uniform(0.0, 1000.0)
        frame_y = rng.uniform(0.0, 1000.0)
        mapper = NormalizedFrameCoordinateMapper((left, top, width, height))

        physical_x, physical_y = mapper.map_point(frame_x, frame_y)

        assert physical_x == round(left + frame_x / 1000.0 * (width - 1))
        assert physical_y == round(top + frame_y / 1000.0 * (height - 1))
        assert left <= physical_x <= left + width - 1
        assert top <= physical_y <= top + height - 1


def test_randomized_mapping_is_monotonic_per_axis() -> None:
    rng = random.Random(20260723)
    for _ in range(500):
        region = (
            rng.randint(-5000, 5000),
            rng.randint(-5000, 5000),
            rng.randint(1, 4096),
            rng.randint(1, 4096),
        )
        mapper = NormalizedFrameCoordinateMapper(region)
        x1, x2 = sorted((rng.uniform(0, 1000), rng.uniform(0, 1000)))
        y1, y2 = sorted((rng.uniform(0, 1000), rng.uniform(0, 1000)))

        first = mapper.map_point(x1, y1)
        second = mapper.map_point(x2, y2)

        assert first[0] <= second[0]
        assert first[1] <= second[1]
