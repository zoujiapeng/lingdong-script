"""Compatibility imports for the P2a profiling package.

The unique implementation owner is :mod:`winpilot.vnext.profiling`.  This
module preserves the initial P2a import path without maintaining a second
schema or policy implementation.
"""

from .profiling import *  # noqa: F401,F403
from .profiling import __all__
