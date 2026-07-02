"""Low-latency GUI control primitives.

The existing agent loop remains the high-level planner. This package contains
small, testable pieces for the fast inner loop: cheap screen deltas, visual
anchors, element caching, screen watcher bulletins, and batched GUI action
execution.
"""
