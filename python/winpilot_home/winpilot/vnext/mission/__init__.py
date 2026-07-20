"""P0/P1 mission contracts, reducer, replay, and legacy shadow helpers."""

from .contracts import *
from .reducer import MissionReducer, reduce_projection, replay
from .shadow import (
    legacy_shadow_projection,
    legacy_trace_to_events,
    project_legacy_contract,
    project_legacy_trace,
)

__all__ = [
    *[name for name in globals() if not name.startswith("_")],
    "MissionReducer",
    "reduce_projection",
    "replay",
    "project_legacy_contract",
    "legacy_shadow_projection",
    "legacy_trace_to_events",
    "project_legacy_trace",
]
