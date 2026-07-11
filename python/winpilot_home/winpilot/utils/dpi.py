"""Process-wide Windows DPI coordinate initialization."""
from __future__ import annotations

import ctypes
import threading

_LOCK = threading.Lock()
_INITIALIZED = False


def ensure_process_dpi_awareness() -> None:
    """Put every WinPilot module in the same physical-pixel coordinate space."""
    global _INITIALIZED
    if _INITIALIZED:
        return
    with _LOCK:
        if _INITIALIZED:
            return
        try:
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (Windows 10+).
            if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                _INITIALIZED = True
                return
        except (AttributeError, OSError):
            pass
        try:
            # PROCESS_PER_MONITOR_DPI_AWARE (Windows 8.1 fallback).
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass
        _INITIALIZED = True
