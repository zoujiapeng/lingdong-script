"""Default-unreachable atomic desktop bridge for the minimal vNext Runtime."""

from .atomic import (
    AtomicDesktopAdapter,
    DesktopTargetProbe,
    LiveTargetSnapshot,
    RawReceiptTraceSink,
)

__all__ = [
    "AtomicDesktopAdapter",
    "DesktopTargetProbe",
    "LiveTargetSnapshot",
    "RawReceiptTraceSink",
]
