"""Shared dataclasses for the low-latency GUI controller."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..utils.screenshot import Region


@dataclass(frozen=True)
class ScreenDelta:
    """Cheap frame-diff result in screen coordinates."""

    changed: bool
    ratio: float
    regions: list[Region] = field(default_factory=list)
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "ratio": round(float(self.ratio), 6),
            "regions": [
                {
                    "left": r.left,
                    "top": r.top,
                    "width": r.width,
                    "height": r.height,
                }
                for r in self.regions
            ],
            "elapsed_ms": int(self.elapsed_ms),
        }


@dataclass(frozen=True)
class VisualAnchor:
    """A semantic target that can be re-located instead of replaying raw coords."""

    label: str = ""
    expected_region: str = ""
    visual_anchor: str = ""
    fallback_text: tuple[str, ...] = ()
    role: str = ""


@dataclass
class BatchMetrics:
    """Latency and frequency counters for one controller batch."""

    started_ms: int = 0
    elapsed_ms: int = 0
    action_count: int = 0
    failed_at: int | None = None
    observe_count: int = 0
    verify_count: int = 0
    screen_wait_count: int = 0
    auto_observe_count: int = 0
    internal_action_count: int = 0
    compound_round_trips_saved: int = 0
    controller_round_trips_saved: int = 0
    tool_latencies_ms: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        avg = (
            sum(self.tool_latencies_ms) / len(self.tool_latencies_ms)
            if self.tool_latencies_ms else 0.0
        )
        return {
            "elapsed_ms": int(self.elapsed_ms),
            "action_count": int(self.action_count),
            "failed_at": self.failed_at,
            "observe_count": int(self.observe_count),
            "verify_count": int(self.verify_count),
            "screen_wait_count": int(self.screen_wait_count),
            "auto_observe_count": int(self.auto_observe_count),
            "internal_action_count": int(self.internal_action_count),
            "compound_round_trips_saved": int(self.compound_round_trips_saved),
            "controller_round_trips_saved": int(self.controller_round_trips_saved),
            "avg_tool_latency_ms": round(avg, 1),
            "tool_latencies_ms": list(self.tool_latencies_ms),
        }
