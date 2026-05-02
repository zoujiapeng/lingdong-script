"""Resolution adaptation — scale coordinates between different screen resolutions."""
from typing import Optional
import pyautogui


class ResolutionAdapter:
    """Adapt automation coordinates to different screen resolutions.

    Stores a "base" resolution and scales all coordinates proportionally
    to the current actual resolution.
    """

    def __init__(self, base_width: Optional[int] = None,
                 base_height: Optional[int] = None):
        self.base_width = base_width
        self.base_height = base_height
        self._current_width = None
        self._current_height = None

    def set_base(self, width: int, height: int):
        """Set the base (reference) resolution."""
        self.base_width = width
        self.base_height = height

    def calibrate(self, reference_element: tuple[tuple[int, int], tuple[int, int]],
                  base_element: tuple[tuple[int, int], tuple[int, int]]):
        """Calibrate resolution using known reference points.

        Args:
            reference_element: ((screen_x, screen_y), (screen_w, screen_h))
                of a known UI element at current resolution.
            base_element: ((base_x, base_y), (base_w, base_h))
                of the same element at base resolution.
        """
        if not self.base_width or not self.base_height:
            self.base_width = pyautogui.size().width
            self.base_height = pyautogui.size().height

        # Compute scale factors from the reference element
        sw = reference_element[1][0] / base_element[1][0]
        sh = reference_element[1][1] / base_element[1][1]
        sx = (reference_element[0][0] - base_element[0][0] * sw) / self.base_width
        sy = (reference_element[0][1] - base_element[0][1] * sh) / self.base_height

        # Store offsets and scale for later use
        self._scale_x = sw
        self._scale_y = sh
        self._offset_x = reference_element[0][0] - base_element[0][0]
        self._offset_y = reference_element[0][1] - base_element[0][1]

    @property
    def current_size(self) -> tuple[int, int]:
        """Get current screen resolution."""
        return pyautogui.size()

    @property
    def scale_x(self) -> float:
        """Get X scale factor from base to current resolution."""
        w, _ = self.current_size
        if self.base_width:
            return w / self.base_width
        return 1.0

    @property
    def scale_y(self) -> float:
        """Get Y scale factor."""
        _, h = self.current_size
        if self.base_height:
            return h / self.base_height
        return 1.0

    def adapt(self, x: float, y: float) -> tuple[float, float]:
        """Adapt coordinates from base resolution to current.

        Args:
            x, y: Coordinates at base resolution.

        Returns:
            (adapted_x, adapted_y) at current resolution.
        """
        return (x * self.scale_x, y * self.scale_y)

    def adapt_region(self, x: float, y: float, w: float, h: float
                     ) -> tuple[float, float, float, float]:
        """Adapt a region from base to current resolution."""
        return (
            x * self.scale_x,
            y * self.scale_y,
            w * self.scale_x,
            h * self.scale_y,
        )

    def inverse_adapt(self, x: float, y: float) -> tuple[float, float]:
        """Convert current-screen coordinates back to base resolution."""
        return (x / self.scale_x, y / self.scale_y)
