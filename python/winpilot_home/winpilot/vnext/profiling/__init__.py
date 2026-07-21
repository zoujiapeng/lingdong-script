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
    "PROFILE_SHADOW_CASE_FORMAT",
    "PROFILE_SHADOW_RESULT_FORMAT",
    "ShadowExpectation",
    "ProfileShadowCase",
    "ProfileShadowResult",
    "ProfileShadowEvaluator",
]
