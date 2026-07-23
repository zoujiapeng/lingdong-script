"""Read-only vNext desktop target discovery contracts."""

from .contracts import (
    ACTIVATE_TARGET_TOOL_NAME,
    DISCOVER_TARGET_TOOL_NAME,
    MAX_TARGET_CANDIDATES,
    MAX_TARGET_QUERY_CHARS,
    TARGET_CANDIDATE_FORMAT,
    TARGET_DISCOVERY_FORMAT,
    TargetActivation,
    TargetCandidate,
    TargetContractError,
    TargetDiscoverySnapshot,
    TargetState,
    activate_target_tool_schema,
    candidate_fingerprint_for,
    canonical_candidate_fingerprint,
    candidate_ref_for,
    discover_target_tool_schema,
    launch_identity_for,
    process_identity_for,
)
from .discovery import TargetDiscovery
from .fresh_windows_discovery import FreshWindowsTargetDiscovery
from .lease import (
    TargetChoiceFact,
    TargetSelectionLease,
    TargetSelectionLeaseError,
    TargetSelectionScope,
)
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
    "ACTIVATE_TARGET_TOOL_NAME", "DISCOVER_TARGET_TOOL_NAME",
    "MAX_TARGET_CANDIDATES", "MAX_TARGET_QUERY_CHARS",
    "TARGET_CANDIDATE_FORMAT", "TARGET_DISCOVERY_FORMAT",
    "TargetActivation", "TargetCandidate", "TargetContractError",
    "TargetDiscovery", "FreshWindowsTargetDiscovery",
    "TargetDiscoverySnapshot", "TargetState",
    "TargetChoiceFact", "TargetSelectionLease", "TargetSelectionLeaseError",
    "TargetSelectionScope",
    "ProcessTargetFact", "PsutilWin32TargetFactsBackend", "WindowTargetFact",
    "WindowsTargetFactsAdapter", "WindowsTargetFactsBackend",
    "WindowsTargetFactsError", "WindowsTargetFactsReader",
    "WindowsTargetFactsSnapshot",
    "activate_target_tool_schema", "candidate_fingerprint_for",
    "canonical_candidate_fingerprint", "candidate_ref_for",
    "discover_target_tool_schema",
    "launch_identity_for", "process_identity_for",
]
