"""Pure ExecutionProfile contracts and deterministic acceptance policy."""

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
]
