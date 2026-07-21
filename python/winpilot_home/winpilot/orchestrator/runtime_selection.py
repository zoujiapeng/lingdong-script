"""Run-scoped selection of the one desktop product runtime.

The selector is deliberately smaller than an execution profile.  Product
configuration chooses ``legacy`` or ``vnext`` once when a run is created;
task prose, graph metadata, model output, and handler inputs are not selector
inputs.  Until the live vNext coordinator exists, selecting it fails closed
instead of entering a legacy desktop handler.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class InvalidDesktopRuntimeSelection(ValueError):
    """The product configuration did not name a supported runtime."""


class DesktopRuntimeUnavailable(RuntimeError):
    """The selected runtime has no live product coordinator."""

    MESSAGE = (
        "vNext desktop runtime is not available; "
        "the run was stopped without legacy fallback"
    )

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)


@dataclass(frozen=True, slots=True)
class DesktopRuntimeSelection:
    """Strict, immutable product-runtime identity owned by one ARP run."""

    runtime_version: str = "legacy"

    def __post_init__(self) -> None:
        raw = self.runtime_version
        if type(raw) is not str or raw not in {"legacy", "vnext"}:
            raise InvalidDesktopRuntimeSelection(
                "desktop runtime_version must be exactly 'legacy' or 'vnext'; "
                f"got {raw!r}"
            )

    @classmethod
    def from_config(cls, raw: Any) -> "DesktopRuntimeSelection":
        """Parse the sole product-config value; missing means legacy."""
        return cls("legacy" if raw is None else raw)

    def require_live_coordinator(self) -> None:
        """Reject an explicitly selected runtime that is not wired yet."""
        if self.runtime_version == "vnext":
            raise DesktopRuntimeUnavailable()


def selection_from_runtime_context(
    context: Mapping[str, Any],
) -> DesktopRuntimeSelection:
    """Read the Runtime-owned value without accepting payload coercion.

    Missing selection preserves compatibility for direct legacy handler tests
    and non-product callers.  A present value must be the immutable contract;
    strings and dictionaries are not accepted as alternate selectors.
    """
    raw = context.get("desktop_runtime_selection")
    if raw is None:
        return DesktopRuntimeSelection()
    if not isinstance(raw, DesktopRuntimeSelection):
        raise InvalidDesktopRuntimeSelection(
            "desktop_runtime_selection context must be Runtime-owned"
        )
    return raw


__all__ = [
    "DesktopRuntimeSelection",
    "DesktopRuntimeUnavailable",
    "InvalidDesktopRuntimeSelection",
    "selection_from_runtime_context",
]
