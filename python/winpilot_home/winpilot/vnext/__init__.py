"""vNext sidecar namespace.

Mission P0/P1 and the pure P2a execution-profile policy are deliberately not
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
    "TASK_PROFILE_PROPOSAL_FORMAT",
    "EXECUTION_PROFILE_FORMAT",
    "ExecutionProfileError",
    "ExecutionPolicyError",
    "InvalidProfileSchema",
    "UnsupportedCapability",
    "ExecutionDepth",
    "TaskDimensions",
    "CognitiveServices",
    "ProfileTrigger",
    "TaskProfileProposal",
    "ExecutionProfile",
    "DEFAULT_CAPABILITY_CATALOG",
    "ExecutionPolicy",
    "TASK_PROFILER_INPUT_FORMAT",
    "TASK_PROFILER_REQUEST_FORMAT",
    "TASK_PROFILER_ROLE",
    "TASK_PROFILER_SYSTEM_CONTRACT",
    "TASK_PROFILE_PROPOSAL_JSON_SCHEMA",
    "ProfilerObligationView",
    "TaskProfilerInput",
    "TaskProfilerRequest",
    "TaskProfilerModel",
    "TaskProfiler",
    "TASK_PROFILER_SHADOW_RECORD_FORMAT",
    "TaskProfilerShadowRecord",
    "TaskProfilerShadowRuntime",
    "PROFILE_SHADOW_CASE_FORMAT",
    "PROFILE_SHADOW_RESULT_FORMAT",
    "ShadowExpectation",
    "ProfileShadowCase",
    "ProfileShadowResult",
    "ProfileShadowEvaluator",
)


def __getattr__(name: str):
    # Keep importing the legacy trace observer cheap: P0 should not eagerly
    # import the P1 mission implementation into every process that records a
    # screenshot.  Direct package imports still retain a convenient API.
    if name in {
        "MissionDefinition", "MissionEvent", "MissionProjection", "MissionReducer", "MissionCard",
        "project_legacy_contract", "project_legacy_trace",
    }:
        from . import mission
        return getattr(mission, name)
    if name in {
        "TASK_PROFILE_PROPOSAL_FORMAT", "EXECUTION_PROFILE_FORMAT", "ExecutionProfileError", "ExecutionPolicyError",
        "InvalidProfileSchema", "UnsupportedCapability", "ExecutionDepth", "TaskDimensions",
        "CognitiveServices", "ProfileTrigger", "TaskProfileProposal", "ExecutionProfile",
        "DEFAULT_CAPABILITY_CATALOG", "ExecutionPolicy", "PROFILE_SHADOW_CASE_FORMAT",
        "PROFILE_SHADOW_RESULT_FORMAT", "ShadowExpectation", "ProfileShadowCase",
        "ProfileShadowResult", "ProfileShadowEvaluator", "TASK_PROFILER_INPUT_FORMAT",
        "TASK_PROFILER_REQUEST_FORMAT", "TASK_PROFILER_ROLE", "TASK_PROFILER_SYSTEM_CONTRACT",
        "TASK_PROFILE_PROPOSAL_JSON_SCHEMA", "ProfilerObligationView", "TaskProfilerInput",
        "TaskProfilerRequest", "TaskProfilerModel", "TaskProfiler",
        "TASK_PROFILER_SHADOW_RECORD_FORMAT", "TaskProfilerShadowRecord",
        "TaskProfilerShadowRuntime",
    }:
        from . import profiling
        return getattr(profiling, name)
    raise AttributeError(name)
