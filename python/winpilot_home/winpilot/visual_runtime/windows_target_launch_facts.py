"""One-shot, read-only Windows facts for a launched executable."""

from __future__ import annotations

import math
import ntpath
from typing import Callable
from ..action.raw_device import RawTargetLaunchReceipt
from ..action.replay_policy import MutationOutcome
from ..action.windows_target_launch import WindowsLaunchProcessIdentity
from ..process_identity import (
    ProcessIdentityError, canonical_executable_path, process_identity_for,
)
from ..vnext.runtime.target_activation import ActivatedTargetIdentity
from ..vnext.runtime.target_launch import TargetLaunchEnvelope
from ..vnext.targeting.executable_presence import (
    ExecutablePresenceReader,
    ExecutablePresenceSnapshot,
)
from ..vnext.targeting.windows_facts import (
    WindowsTargetFactsReader,
    WindowsTargetFactsSnapshot,
)
from .atomic_click_controller import (
    TargetActivationObservation,
    TargetLaunchObservationResult,
    TargetLaunchObservationState,
)
from .windows_desktop_facts import (
    TargetActivationFacts,
    WindowsDesktopFactsReader,
)


class WindowsTargetLaunchFactsError(ValueError):
    """A requested process identity cannot be read canonically."""


class PsutilLaunchProcessIdentityReader:
    """Read one PID through exactly one injected ``psutil.Process`` object."""

    def __init__(
        self,
        process_factory: Callable[[int], object] | None = None,
    ) -> None:
        if process_factory is None:
            import psutil

            process_factory = psutil.Process
        if not callable(process_factory):
            raise TypeError("process_factory must be callable")
        self._process_factory = process_factory

    def __call__(self, process_id: int) -> WindowsLaunchProcessIdentity:
        if type(process_id) is not int or process_id <= 0:
            raise WindowsTargetLaunchFactsError("process_id must be positive")
        try:
            process = self._process_factory(process_id)
            observed_pid = getattr(process, "pid")
            created = process.create_time()
            executable = process.exe()
            if observed_pid != process_id or type(observed_pid) is not int:
                raise ValueError("psutil returned a different process")
            if (
                type(created) not in {int, float}
                or not math.isfinite(float(created))
                or float(created) <= 0
            ):
                raise ValueError("process creation time is unavailable")
            creation_time_ns = int(float(created) * 1_000_000_000)
            path = canonical_executable_path(ntpath.normpath(executable))
            identity = process_identity_for(
                process_id=process_id,
                creation_time_ns=creation_time_ns,
                executable_path=path,
            )
            return WindowsLaunchProcessIdentity(
                process_id,
                creation_time_ns,
                path,
                identity,
            )
        except (ProcessIdentityError, TypeError, ValueError) as exc:
            raise WindowsTargetLaunchFactsError(
                "launched process identity is unavailable"
            ) from exc
        except Exception as exc:
            raise WindowsTargetLaunchFactsError(
                "launched process identity source is unavailable"
            ) from exc


class WindowsTargetLaunchObservationAdapter:
    """Compose three fresh read-only fact owners without polling or mutation."""

    def __init__(
        self,
        presence: ExecutablePresenceReader,
        target_facts: WindowsTargetFactsReader,
        desktop_facts: WindowsDesktopFactsReader,
    ) -> None:
        if not isinstance(presence, ExecutablePresenceReader):
            raise TypeError("presence must be ExecutablePresenceReader")
        if not isinstance(target_facts, WindowsTargetFactsReader):
            raise TypeError("target_facts must be WindowsTargetFactsReader")
        if not isinstance(desktop_facts, WindowsDesktopFactsReader):
            raise TypeError("desktop_facts must be WindowsDesktopFactsReader")
        self._presence = presence
        self._target_facts = target_facts
        self._desktop_facts = desktop_facts

    @staticmethod
    def _result(
        state: TargetLaunchObservationState,
        observation: TargetActivationObservation | None = None,
    ) -> TargetLaunchObservationResult:
        return TargetLaunchObservationResult(state, observation)

    def observe_launch(
        self,
        envelope: TargetLaunchEnvelope,
        *,
        raw_receipt: RawTargetLaunchReceipt,
        selected_hwnd: int | None,
    ) -> TargetLaunchObservationResult:
        if not isinstance(envelope, TargetLaunchEnvelope):
            raise TypeError("envelope must be TargetLaunchEnvelope")
        if not isinstance(raw_receipt, RawTargetLaunchReceipt):
            raise TypeError("raw_receipt must be RawTargetLaunchReceipt")
        if selected_hwnd is not None and (
            type(selected_hwnd) is not int or selected_hwnd <= 0
        ):
            raise ValueError("selected_hwnd must be positive or null")
        if raw_receipt.outcome is not MutationOutcome.APPLIED:
            return self._result(TargetLaunchObservationState.SOURCE_UNAVAILABLE)
        process_id = raw_receipt.process_id
        process_identity = raw_receipt.process_identity
        executable_path = envelope.candidate.launch_target
        assert (
            process_id is not None
            and process_identity is not None
            and executable_path is not None
        )

        try:
            presence = self._presence.read(executable_path)
        except Exception:
            return self._result(TargetLaunchObservationState.SOURCE_UNAVAILABLE)
        if (
            not isinstance(presence, ExecutablePresenceSnapshot)
            or ntpath.normcase(presence.executable_path)
            != ntpath.normcase(executable_path)
            or presence.unresolved_process_ids
        ):
            return self._result(TargetLaunchObservationState.SOURCE_UNAVAILABLE)
        same_pid = tuple(
            fact
            for fact in presence.exact_instances
            if fact.process_id == process_id
        )
        if len(presence.exact_instances) > 1:
            return self._result(TargetLaunchObservationState.AMBIGUOUS)
        if same_pid and same_pid[0].process_identity != process_identity:
            return self._result(TargetLaunchObservationState.PROCESS_REUSED)

        try:
            snapshot = self._target_facts.read()
        except Exception:
            return self._result(TargetLaunchObservationState.SOURCE_UNAVAILABLE)
        if (
            not isinstance(snapshot, WindowsTargetFactsSnapshot)
            or snapshot.captured_at < presence.captured_at
        ):
            return self._result(TargetLaunchObservationState.SOURCE_UNAVAILABLE)
        process = next(
            (
                fact
                for fact in snapshot.processes
                if fact.process_id == process_id
            ),
            None,
        )
        if process is None:
            return self._result(TargetLaunchObservationState.SOURCE_UNAVAILABLE)
        if process.process_identity != process_identity:
            return self._result(TargetLaunchObservationState.PROCESS_REUSED)
        exact_path = ntpath.normcase(executable_path)
        exact_pids = tuple(
            fact.process_id for fact in snapshot.processes
            if ntpath.normcase(fact.executable_path) == exact_path
        )
        if len(exact_pids) > 1:
            return self._result(TargetLaunchObservationState.AMBIGUOUS)
        if exact_pids != (process_id,):
            return self._result(TargetLaunchObservationState.SOURCE_UNAVAILABLE)
        if not same_pid:
            return self._result(TargetLaunchObservationState.SOURCE_UNAVAILABLE)
        visible_roots = tuple(
            window
            for window in snapshot.windows
            if window.process_id == process_id and window.visible
        )
        if not visible_roots:
            return self._result(TargetLaunchObservationState.NOT_READY)
        if selected_hwnd is None:
            if len(visible_roots) != 1:
                return self._result(TargetLaunchObservationState.AMBIGUOUS)
            hwnd = visible_roots[0].hwnd
        else:
            selected = tuple(
                window for window in visible_roots if window.hwnd == selected_hwnd
            )
            if len(selected) != 1:
                return self._result(TargetLaunchObservationState.NOT_READY)
            hwnd = selected_hwnd

        try:
            facts = self._desktop_facts.read_activation_facts(hwnd)
        except Exception:
            return self._result(TargetLaunchObservationState.SOURCE_UNAVAILABLE)
        if not isinstance(facts, TargetActivationFacts):
            return self._result(TargetLaunchObservationState.SOURCE_UNAVAILABLE)
        target = facts.target
        if (
            target.process_id == process_id
            and target.process_identity != process_identity
        ):
            return self._result(TargetLaunchObservationState.PROCESS_REUSED)
        if (
            target.hwnd != hwnd
            or target.root_hwnd != hwnd
            or target.process_id != process_id
            or target.process_identity != process_identity
        ):
            return self._result(TargetLaunchObservationState.AMBIGUOUS)
        if not facts.exact_foreground:
            return self._result(TargetLaunchObservationState.NOT_READY)
        try:
            activated = ActivatedTargetIdentity(
                hwnd=target.hwnd,
                root_hwnd=target.root_hwnd,
                process_id=target.process_id,
                process_identity=target.process_identity,
                window_region=facts.window_region,
                title_snapshot=facts.title_snapshot,
            )
            observation = TargetActivationObservation(activated, True)
        except Exception:
            return self._result(TargetLaunchObservationState.SOURCE_UNAVAILABLE)
        return self._result(
            TargetLaunchObservationState.READY,
            observation,
        )


__all__ = [
    "PsutilLaunchProcessIdentityReader",
    "WindowsTargetLaunchFactsError",
    "WindowsTargetLaunchObservationAdapter",
]
