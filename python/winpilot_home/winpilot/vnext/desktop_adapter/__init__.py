"""Default-unreachable atomic desktop bridge for the minimal vNext Runtime."""

from .atomic import (
    AtomicDesktopAdapter,
    DesktopTargetProbe,
    DesktopWorkspacePort,
    LiveTargetSnapshot,
    RawReceiptTraceSink,
)
from .workspace import (
    DesktopWorkspace,
    WorkspaceObservation,
    WorkspaceSurface,
)

__all__ = [
    "AtomicDesktopAdapter",
    "DesktopTargetProbe",
    "DesktopWorkspace",
    "DesktopWorkspacePort",
    "LiveTargetSnapshot",
    "RawReceiptTraceSink",
    "WorkspaceObservation",
    "WorkspaceSurface",
]
