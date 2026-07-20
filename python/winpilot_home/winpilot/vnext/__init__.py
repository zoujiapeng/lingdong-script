"""vNext sidecar namespace.

Only the pure mission shadow is implemented in P0/P1.  It is deliberately not
imported by the legacy runner, model, controller, or task-contract endpoint.
"""

__all__ = (
    "MissionDefinition",
    "MissionEvent",
    "MissionProjection",
    "MissionReducer",
    "MissionCard",
    "project_legacy_contract",
    "project_legacy_trace",
)


def __getattr__(name: str):
    # Keep importing the legacy trace observer cheap: P0 should not eagerly
    # import the P1 mission implementation into every process that records a
    # screenshot.  Direct package imports still retain a convenient API.
    if name in __all__:
        from . import mission
        return getattr(mission, name)
    raise AttributeError(name)
