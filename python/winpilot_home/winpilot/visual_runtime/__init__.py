"""Shared mechanical owners for legacy and vNext visual runtimes."""

from .frame_observer import FrameCapture, FrameObserver
from .submitted_image_coordinates import SubmittedImageCoordinateMapper

__all__ = ["FrameCapture", "FrameObserver", "SubmittedImageCoordinateMapper"]
