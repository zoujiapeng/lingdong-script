"""Bezier curve generation for human-like mouse movement."""
import math
import random
from typing import Optional
import numpy as np


class BezierCurve:
    """Generate Bezier curve paths for simulating human-like mouse movements."""

    @staticmethod
    def cubic_bezier(t: float, p0: tuple[float, float], p1: tuple[float, float],
                     p2: tuple[float, float], p3: tuple[float, float]) -> tuple[float, float]:
        """Evaluate cubic Bezier curve at parameter t in [0, 1]."""
        u = 1 - t
        x = u ** 3 * p0[0] + 3 * u ** 2 * t * p1[0] + 3 * u * t ** 2 * p2[0] + t ** 3 * p3[0]
        y = u ** 3 * p0[1] + 3 * u ** 2 * t * p1[1] + 3 * u * t ** 2 * p2[1] + t ** 3 * p3[1]
        return (x, y)

    @staticmethod
    def generate_path(start_x: float, start_y: float, end_x: float, end_y: float,
                      num_points: Optional[int] = None,
                      smoothness: float = 0.4,
                      randomness: float = 0.2) -> list[tuple[float, float]]:
        """Generate a human-like mouse movement path using cubic Bezier curves.

        Args:
            start_x, start_y: Starting coordinates.
            end_x, end_y: Target coordinates.
            num_points: Number of path points. Auto-calculated if None.
            smoothness: Control point offset magnitude (0-1). Higher = more curved.
            randomness: Random perturbation magnitude (0-1).

        Returns:
            List of (x, y) waypoints forming a smooth path.
        """
        if num_points is None:
            dist = math.hypot(end_x - start_x, end_y - start_y)
            num_points = max(10, min(100, int(dist / 5)))

        start = (float(start_x), float(start_y))
        end = (float(end_x), float(end_y))

        dx = end[0] - start[0]
        dy = end[1] - start[1]

        # Generate control points with randomness for human-like motion
        offset_mag = math.hypot(dx, dy) * smoothness
        r1 = random.uniform(-randomness, randomness)
        r2 = random.uniform(-randomness, randomness)
        r3 = random.uniform(-randomness, randomness)
        r4 = random.uniform(-randomness, randomness)

        cp1 = (
            start[0] + dx * 0.25 + offset_mag * (0.3 * r1 + 0.2),
            start[1] + dy * 0.25 + offset_mag * (0.3 * r2 + 0.2)
        )
        cp2 = (
            end[0] - dx * 0.25 + offset_mag * (0.3 * r3 - 0.1),
            end[1] - dy * 0.25 + offset_mag * (0.3 * r4 - 0.1)
        )

        path = []
        for i in range(num_points):
            t = i / (num_points - 1)
            # Ease-out curve: more points near the end (human-like)
            t = 1 - (1 - t) ** 1.5
            x, y = BezierCurve.cubic_bezier(t, start, cp1, cp2, end)
            path.append((x, y))

        return path

    @staticmethod
    def generate_with_overshoot(start_x: float, start_y: float,
                                 end_x: float, end_y: float,
                                 overshoot_px: float = 15.0,
                                 num_points: int = 50) -> list[tuple[float, float]]:
        """Generate path that slightly overshoots then corrects (human-like)."""
        start = (float(start_x), float(start_y))
        end = (float(end_x), float(end_y))

        dx = end[0] - start[0]
        dy = end[1] - start[1]
        dist = math.hypot(dx, dy)

        if dist < overshoot_px * 2:
            return BezierCurve.generate_path(start_x, start_y, end_x, end_y, num_points)

        # Overshoot beyond the target
        nx, ny = dx / dist, dy / dist
        overshoot = (
            end[0] + nx * overshoot_px,
            end[1] + ny * overshoot_px
        )

        # Two control points for overshoot -> correction
        cp1 = (
            start[0] + dx * 0.3 + ny * random.uniform(10, 30),
            start[1] + dy * 0.3 - nx * random.uniform(10, 30)
        )
        cp2 = (
            overshoot[0] + nx * random.uniform(5, 15) + ny * random.uniform(2, 8),
            overshoot[1] + ny * random.uniform(5, 15) - nx * random.uniform(2, 8)
        )

        path = []
        for i in range(num_points):
            t = i / (num_points - 1)
            x, y = BezierCurve.cubic_bezier(t, start, cp1, cp2, overshoot)
            path.append((x, y))

        return path

    @staticmethod
    def compute_delays(path_length: int,
                       base_duration: float = 0.3,
                       jitter: float = 0.1) -> list[float]:
        """Generate per-step delays that vary naturally.

        Args:
            path_length: Number of points in the movement path.
            base_duration: Total movement duration in seconds.
            jitter: Random delay jitter magnitude.

        Returns:
            List of delays between each step.
        """
        if path_length <= 1:
            return [base_duration]

        # Non-uniform timing: start fast, slow down near target
        phases = [i / (path_length - 1) for i in range(path_length)]
        speed_mult = [1.0 + 0.5 * math.sin(p * math.pi) for p in phases]

        total_time = base_duration * path_length / 10
        delays = [total_time * m / sum(speed_mult) for m in speed_mult]

        # Add jitter
        delays = [d * (1 + random.uniform(-jitter, jitter)) for d in delays]
        return delays
