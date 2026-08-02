"""One mutable desktop workspace above per-action atomic input.

The workspace does not plan, launch applications, or execute input.  It owns
only the current logical window and a causal, two-observation hand-off from a
completed action to a newly foreground window.  An action keeps using the
binding that grounded its frame; a pending hand-off can be adopted only by the
next two observations.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import threading
import time

from ..mission import TargetBinding
from .atomic import LiveTargetSnapshot


PhysicalRegion = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class WorkspaceSurface:
    """Read-only mechanical facts for one visible top-level window."""

    hwnd: int
    root_hwnd: int
    process_id: int
    executable_identity: str
    title: str
    class_name: str
    region: PhysicalRegion
    foreground_root_hwnd: int | None

    def __post_init__(self) -> None:
        for name in ("hwnd", "root_hwnd", "process_id"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.root_hwnd != self.hwnd:
            raise ValueError("workspace surfaces must be top-level roots")
        if (
            type(self.executable_identity) is not str
            or not self.executable_identity
        ):
            raise ValueError("executable_identity must be non-empty")
        for name in ("title", "class_name"):
            if type(getattr(self, name)) is not str:
                raise ValueError(f"{name} must be a string")
        region = tuple(self.region)
        if (
            len(region) != 4
            or any(type(value) is not int for value in region)
            or region[2] <= 0
            or region[3] <= 0
        ):
            raise ValueError(
                "region must be (left, top, positive width, positive height)"
            )
        foreground = self.foreground_root_hwnd
        if foreground is not None and (
            type(foreground) is not int or foreground <= 0
        ):
            raise ValueError("foreground_root_hwnd must be positive or null")
        object.__setattr__(self, "region", region)

    @property
    def stable_identity(self) -> tuple[int, int, int, str]:
        return (
            self.hwnd,
            self.root_hwnd,
            self.process_id,
            self.executable_identity,
        )

    def to_live_snapshot(self) -> LiveTargetSnapshot:
        return LiveTargetSnapshot(
            hwnd=self.hwnd,
            root_hwnd=self.root_hwnd,
            process_id=self.process_id,
            executable_identity=self.executable_identity,
            region=self.region,
            foreground_root_hwnd=self.foreground_root_hwnd,
        )


@dataclass(frozen=True, slots=True)
class WorkspaceObservation:
    """A stable surface selected for the next authoritative frame."""

    surface: WorkspaceSurface
    generation: int
    replaces_binding: bool = False
    causal_action_id: str | None = None
    causal_action: str | None = None


@dataclass(frozen=True, slots=True)
class _PendingTransition:
    source_binding_id: str
    action_id: str
    action: str
    generation: int
    observations_left: int
    rejected_identity: tuple[int, int, int, str] | None = None


class DesktopWorkspace:
    """Own the current window and causal foreground hand-offs."""

    _WINDOW_CHANGING_ACTIONS = frozenset(
        {"click", "double_click", "hotkey"}
    )
    _SUCCESSOR_SAMPLES = 5
    _SUCCESSOR_SAMPLE_DELAY_S = 0.05
    _SUCCESSOR_OBSERVATIONS = 2

    def __init__(
        self,
        *,
        binding: TargetBinding,
        surface: WorkspaceSurface,
        reader: Callable[[int], WorkspaceSurface | None],
        foreground_reader: Callable[[], int | None],
        hit_test_root: Callable[[int, int], int | None],
        wait: Callable[[float], None] = time.sleep,
    ) -> None:
        if type(binding) is not TargetBinding:
            raise TypeError("binding must be canonical TargetBinding")
        if type(surface) is not WorkspaceSurface:
            raise TypeError("surface must be WorkspaceSurface")
        if (
            not callable(reader)
            or not callable(foreground_reader)
            or not callable(hit_test_root)
            or not callable(wait)
        ):
            raise TypeError(
                "workspace readers, hit test, and wait must be callable"
            )
        self._require_binding_surface(binding, surface)
        self._binding = TargetBinding.from_dict(binding.to_dict())
        self._surface = surface
        self._reader = reader
        self._foreground_reader = foreground_reader
        self._hit_test = hit_test_root
        self._wait = wait
        self._pending: _PendingTransition | None = None
        self._generation = 0
        self._lock = threading.RLock()

    @staticmethod
    def _require_binding_surface(
        binding: TargetBinding,
        surface: WorkspaceSurface,
    ) -> None:
        if (
            binding.hwnd != surface.hwnd
            or binding.process_id != surface.process_id
            or binding.executable_identity != surface.executable_identity
        ):
            raise ValueError("binding and workspace surface disagree")

    @property
    def binding(self) -> TargetBinding:
        with self._lock:
            return self._binding

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def _read_stable(self, hwnd: int) -> WorkspaceSurface | None:
        first = self._reader(int(hwnd))
        second = self._reader(int(hwnd))
        if (
            type(first) is not WorkspaceSurface
            or type(second) is not WorkspaceSurface
            or first.stable_identity != second.stable_identity
            or first.region != second.region
            or second.foreground_root_hwnd != second.hwnd
        ):
            return None
        return second

    def _read_foreground(self) -> int | None:
        try:
            value = self._foreground_reader()
        except Exception:
            return None
        return value if type(value) is int and value > 0 else None

    def _causal_successor(
        self,
        pending: _PendingTransition,
    ) -> WorkspaceSurface | None:
        """Resolve one bounded successor without consulting the old hwnd."""

        for sample_index in range(self._SUCCESSOR_SAMPLES):
            foreground = self._read_foreground()
            if foreground not in (None, self._binding.hwnd):
                candidate = self._read_stable(foreground)
                if (
                    candidate is not None
                    and candidate.stable_identity
                    != pending.rejected_identity
                ):
                    return candidate
            if sample_index + 1 < self._SUCCESSOR_SAMPLES:
                self._wait(self._SUCCESSOR_SAMPLE_DELAY_S)
        return None

    def _current_observation(self) -> WorkspaceObservation | None:
        current = self._reader(self._binding.hwnd)
        if (
            type(current) is not WorkspaceSurface
            or current.stable_identity != self._surface.stable_identity
        ):
            return None
        return WorkspaceObservation(
            surface=current,
            generation=self._generation,
        )

    def _pending_for(
        self,
        observation: WorkspaceObservation,
    ) -> _PendingTransition | None:
        pending = self._pending
        if (
            not observation.replaces_binding
            or observation.generation != self._generation
            or pending is None
            or pending.source_binding_id != self._binding.binding_id
            or pending.generation != self._generation
            or observation.causal_action_id != pending.action_id
            or observation.surface.stable_identity
            == pending.rejected_identity
        ):
            return None
        return pending

    def observe(self) -> WorkspaceObservation:
        """Return the current surface or one stable causal successor."""

        with self._lock:
            pending = self._pending
            while (
                pending is not None
                and pending.source_binding_id == self._binding.binding_id
                and pending.generation == self._generation
            ):
                candidate = self._causal_successor(pending)
                if candidate is not None:
                    return WorkspaceObservation(
                        surface=candidate,
                        generation=self._generation,
                        replaces_binding=True,
                        causal_action_id=pending.action_id,
                        causal_action=pending.action,
                    )
                self._pending = (
                    None
                    if pending.observations_left <= 1
                    else replace(
                        pending,
                        observations_left=pending.observations_left - 1,
                    )
                )

                current = self._current_observation()
                if current is not None:
                    return current
                pending = self._pending

            current = self._current_observation()
            if current is None:
                raise RuntimeError("current workspace surface is unavailable")
            # An unrelated foreground window is intentionally not adopted.
            # The old surface remains observable, while the atomic adapter's
            # pre-input foreground guard prevents accidental input.
            return current

    def validate(
        self,
        observation: WorkspaceObservation,
    ) -> WorkspaceSurface | None:
        """Revalidate one tentative successor without changing binding."""

        if type(observation) is not WorkspaceObservation:
            raise TypeError("observation must be WorkspaceObservation")
        with self._lock:
            if self._pending_for(observation) is None:
                return None
            current = self._read_stable(observation.surface.hwnd)
            if (
                current is None
                or current.stable_identity
                != observation.surface.stable_identity
                or current.region != observation.surface.region
                or current.class_name != observation.surface.class_name
            ):
                return None
            return current

    def discard(self, observation: WorkspaceObservation) -> None:
        """Reject one tentative successor while preserving bounded hand-off."""

        if type(observation) is not WorkspaceObservation:
            raise TypeError("observation must be WorkspaceObservation")
        with self._lock:
            pending = self._pending_for(observation)
            if pending is None:
                raise RuntimeError("workspace discard is stale or uncaused")
            self._pending = (
                None
                if pending.observations_left <= 1
                else replace(
                    pending,
                    observations_left=pending.observations_left - 1,
                    rejected_identity=observation.surface.stable_identity,
                )
            )

    def adopt(
        self,
        observation: WorkspaceObservation,
        binding: TargetBinding,
    ) -> None:
        """Install exactly the successor returned by ``observe``."""

        if type(observation) is not WorkspaceObservation:
            raise TypeError("observation must be WorkspaceObservation")
        if type(binding) is not TargetBinding:
            raise TypeError("binding must be canonical TargetBinding")
        with self._lock:
            if self._pending_for(observation) is None:
                raise RuntimeError("workspace adoption is stale or uncaused")
            self._require_binding_surface(binding, observation.surface)
            self._binding = TargetBinding.from_dict(binding.to_dict())
            self._surface = observation.surface
            self._pending = None
            self._generation += 1

    def snapshot(self, hwnd: int) -> LiveTargetSnapshot | None:
        """Implement the atomic adapter's read-only target probe."""

        with self._lock:
            if int(hwnd) != self._binding.hwnd:
                return None
            current = self._reader(self._binding.hwnd)
            if (
                type(current) is not WorkspaceSurface
                or current.stable_identity
                != self._surface.stable_identity
            ):
                return None
            return current.to_live_snapshot()

    def hit_test_root(self, x: int, y: int) -> int | None:
        try:
            value = self._hit_test(int(x), int(y))
        except Exception:
            return None
        return value if type(value) is int and value > 0 else None

    def finish_action(
        self,
        *,
        action_id: str,
        action: str,
        input_completed: bool | None,
    ) -> None:
        """Record, but never immediately adopt, a causal successor."""

        if (
            input_completed is not True
            or action not in self._WINDOW_CHANGING_ACTIONS
            or type(action_id) is not str
            or not action_id
        ):
            return
        with self._lock:
            # Arm the next observation even if the source window closes or the
            # successor has not appeared yet.  The observation owns bounded,
            # stable foreground discovery; this method never adopts directly.
            self._pending = _PendingTransition(
                source_binding_id=self._binding.binding_id,
                action_id=action_id,
                action=action,
                generation=self._generation,
                observations_left=self._SUCCESSOR_OBSERVATIONS,
                rejected_identity=None,
            )


__all__ = [
    "DesktopWorkspace",
    "WorkspaceObservation",
    "WorkspaceSurface",
]
