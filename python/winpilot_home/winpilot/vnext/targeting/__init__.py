"""Read-only vNext desktop target discovery contracts."""

from .contracts import (
    MAX_TARGET_CANDIDATES,
    MAX_TARGET_QUERY_CHARS,
    TARGET_CANDIDATE_FORMAT,
    TARGET_DISCOVERY_FORMAT,
    TargetActivation,
    TargetCandidate,
    TargetContractError,
    TargetDiscoverySnapshot,
    TargetState,
    candidate_ref_for,
    launch_identity_for,
    process_identity_for,
)
from .discovery import TargetDiscovery
from .windows_facts import (
    ProcessTargetFact,
    PsutilWin32TargetFactsBackend,
    WindowTargetFact,
    WindowsTargetFactsAdapter,
    WindowsTargetFactsBackend,
    WindowsTargetFactsError,
    WindowsTargetFactsReader,
    WindowsTargetFactsSnapshot,
)

__all__ = [
    "MAX_TARGET_CANDIDATES", "MAX_TARGET_QUERY_CHARS",
    "TARGET_CANDIDATE_FORMAT", "TARGET_DISCOVERY_FORMAT",
    "TargetActivation", "TargetCandidate", "TargetContractError",
    "TargetDiscovery", "TargetDiscoverySnapshot", "TargetState",
    "ProcessTargetFact", "PsutilWin32TargetFactsBackend", "WindowTargetFact",
    "WindowsTargetFactsAdapter", "WindowsTargetFactsBackend",
    "WindowsTargetFactsError", "WindowsTargetFactsReader",
    "WindowsTargetFactsSnapshot",
    "candidate_ref_for", "launch_identity_for", "process_identity_for",
]
