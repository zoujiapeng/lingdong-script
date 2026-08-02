"""Screen capture utilities: full-screen / window-region capture via mss.

Process is set DPI-aware at import time so all coordinates from UIA, Win32
and mss agree (physical pixels).
"""
from __future__ import annotations

import ctypes.wintypes
import threading
from dataclasses import dataclass

import mss
import numpy as np

from .dpi import ensure_process_dpi_awareness

ensure_process_dpi_awareness()

_local = threading.local()


def _get_mss() -> mss.MSS:
    """mss instances are not thread-safe; keep one per thread."""
    if not hasattr(_local, "sct"):
        _local.sct = mss.MSS()
    return _local.sct


@dataclass(frozen=True)
class Region:
    """Screen-space rectangle in physical pixels."""

    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.width // 2, self.top + self.height // 2)

    def clamp_to(self, bounds: "Region") -> "Region":
        left = max(bounds.left, min(self.left, bounds.right - 1))
        top = max(bounds.top, min(self.top, bounds.bottom - 1))
        right = max(left + 1, min(self.right, bounds.right))
        bottom = max(top + 1, min(self.bottom, bounds.bottom))
        return Region(left, top, right - left, bottom - top)

    def clamp_to_screen(self) -> "Region":
        return self.clamp_to(virtual_screen_region())


@dataclass(frozen=True)
class DesktopGeometry:
    """Authoritative physical-pixel coordinate space for the desktop."""

    virtual: Region
    primary: Region

    def screen_from_capture(self, x: int, y: int, region: Region | None = None) -> tuple[int, int]:
        origin = region or self.virtual
        return origin.left + int(x), origin.top + int(y)

    def capture_from_screen(self, x: int, y: int, region: Region | None = None) -> tuple[int, int]:
        origin = region or self.virtual
        return int(x) - origin.left, int(y) - origin.top


def _virtual_metrics() -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    return (
        int(user32.GetSystemMetrics(76)),
        int(user32.GetSystemMetrics(77)),
        int(user32.GetSystemMetrics(78)),
        int(user32.GetSystemMetrics(79)),
    )


def virtual_screen_region() -> Region:
    left, top, width, height = _virtual_metrics()
    if width <= 0 or height <= 0:
        monitor = _get_mss().monitors[0]
        return Region(
            int(monitor["left"]), int(monitor["top"]),
            int(monitor["width"]), int(monitor["height"]),
        )
    return Region(left, top, width, height)


def monitor_regions() -> tuple[Region, ...]:
    """Return each connected monitor in authoritative physical pixels."""
    regions = []
    for monitor in _get_mss().monitors[1:]:
        region = Region(
            int(monitor["left"]), int(monitor["top"]),
            int(monitor["width"]), int(monitor["height"]),
        )
        if region.width > 0 and region.height > 0:
            regions.append(region)
    return tuple(regions)


def primary_screen_region() -> Region:
    user32 = ctypes.windll.user32
    return Region(0, 0, int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1)))


def desktop_geometry() -> DesktopGeometry:
    return DesktopGeometry(virtual=virtual_screen_region(), primary=primary_screen_region())


def screen_size() -> tuple[int, int]:
    region = virtual_screen_region()
    return region.width, region.height


def capture(region: Region | None = None) -> np.ndarray:
    """Capture screen (or region) as BGR ndarray (OpenCV convention)."""
    sct = _get_mss()
    region = (region or virtual_screen_region()).clamp_to_screen()
    monitor = {
        "left": region.left,
        "top": region.top,
        "width": region.width,
        "height": region.height,
    }
    shot = sct.grab(monitor)
    frame = np.asarray(shot, dtype=np.uint8)  # BGRA
    return frame[:, :, :3].copy()  # BGR


def window_region(hwnd: int) -> Region:
    """Window rectangle in physical pixels, DWM-accurate (no invisible borders)."""
    rect = ctypes.wintypes.RECT()
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    res = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd,
        DWMWA_EXTENDED_FRAME_BOUNDS,
        ctypes.byref(rect),
        ctypes.sizeof(rect),
    )
    if res != 0:  # fall back to GetWindowRect
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise OSError(f"GetWindowRect 失败 hwnd={hwnd}")
    return Region(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
