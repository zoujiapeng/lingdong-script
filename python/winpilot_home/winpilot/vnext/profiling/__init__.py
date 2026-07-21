"""Pure P2a profiling contracts, policy, and shadow evaluation."""

from .contracts import (
    EXECUTION_PROFILE_FORMAT,
    TASK_PROFILE_PROPOSAL_FORMAT,
    CognitiveServices,
    ExecutionDepth,
    ExecutionPolicyError,
    ExecutionProfile,
    ExecutionProfileError,
    InvalidProfileSchema,
    ProfileTrigger,
    TaskDimensions,
    TaskProfileProposal,
    UnsupportedCapability,
)
from .policy import DEFAULT_CAPABILITY_CATALOG, ExecutionPolicy
from .provider import OpenAITaskProfilerModel
from .legacy_shadow import (
    LegacyTaskProfilerShadowHook,
    ShadowTraceWriter,
    profiler_input_from_legacy_contract,
)
from .profiler import (
    TASK_PROFILER_INPUT_FORMAT,
    TASK_PROFILER_REQUEST_FORMAT,
    TASK_PROFILER_ROLE,
    TASK_PROFILER_SYSTEM_CONTRACT,
    TASK_PROFILE_PROPOSAL_JSON_SCHEMA,
    ProfilerObligationView,
    TaskProfiler,
    TaskProfilerInput,
    TaskProfilerModel,
    TaskProfilerRequest,
)
from .runtime import (
    TASK_PROFILER_SHADOW_RECORD_FORMAT,
    TaskProfilerShadowRecord,
    TaskProfilerShadowRuntime,
)
from .shadow import (
    PROFILE_SHADOW_CASE_FORMAT,
    PROFILE_SHADOW_RESULT_FORMAT,
    ProfileShadowCase,
    ProfileShadowEvaluator,
    ProfileShadowResult,
    ShadowExpectation,
)

__all__ = [
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
    "OpenAITaskProfilerModel",
    "ShadowTraceWriter",
    "profiler_input_from_legacy_contract",
    "LegacyTaskProfilerShadowHook",
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
]
