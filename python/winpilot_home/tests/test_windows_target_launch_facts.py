from __future__ import annotations

import ast
import hashlib
import math
from pathlib import Path
import random

import pytest

from winpilot.action.raw_device import RawTargetLaunchReceipt
from winpilot.action.replay_policy import MutationOutcome
from winpilot.process_identity import process_identity_for
from winpilot.vnext.actor import ACTOR_PROPOSAL_FORMAT, DesktopActionProposal
from winpilot.vnext.capabilities import PointerLeaseState
from winpilot.vnext.runtime.target_launch import TargetLaunchEnvelope
from winpilot.vnext.targeting import (
    TargetCandidate,
    candidate_fingerprint_for,
    candidate_ref_for,
    launch_identity_for,
)
from winpilot.vnext.targeting.executable_presence import ExecutablePresenceReader
from winpilot.vnext.targeting.windows_facts import WindowsTargetFactsReader
from winpilot.visual_runtime.atomic_click_controller import (
    TargetLaunchObservationPort,
    TargetLaunchObservationState,
)
from winpilot.visual_runtime.commit_admission import FrameCommitIdentity
from winpilot.visual_runtime.windows_desktop_facts import (
    WindowsDesktopFactsReader,
)
from winpilot.visual_runtime.windows_target_launch_facts import (
    PsutilLaunchProcessIdentityReader,
    WindowsTargetLaunchFactsError,
    WindowsTargetLaunchObservationAdapter,
)


PATH = r"C:\Apps\Orbit\Orbit.exe"
PID = 707
CREATED = 1_700_000_000_123_456_000
HWND = 505
IDENTITY = process_identity_for(
    process_id=PID,
    creation_time_ns=CREATED,
    executable_path=PATH,
)


def _presence_row(
    process_id: int = PID,
    *,
    created: int | None = CREATED,
    path: str | None = PATH,
    name: str | None = "Orbit.exe",
) -> dict[str, object]:
    return {
        "process_id": process_id,
        "creation_time_ns": created,
        "executable_path": path,
        "process_name": name,
    }


def _process_row(
    process_id: int = PID,
    *,
    created: int = CREATED,
    path: str = PATH,
) -> dict[str, object]:
    return {
        "process_id": process_id,
        "creation_time_ns": created,
        "executable_path": path,
    }


def _window_row(
    hwnd: int = HWND,
    *,
    process_id: int = PID,
    visible: bool = True,
) -> dict[str, object]:
    return {
        "hwnd": hwnd,
        "process_id": process_id,
        "title": "Orbit",
        "visible": visible,
    }


def _envelope() -> TargetLaunchEnvelope:
    candidate = TargetCandidate(
        candidate_ref=candidate_ref_for(
            activation="open_app",
            identity=launch_identity_for(kind="exe", target=PATH),
        ),
        activation="open_app",
        state="installed",
        label="Orbit",
        provenance=("installed_catalog",),
        launch_kind="exe",
        launch_target=PATH,
    )
    proposal = DesktopActionProposal.model_validate({
        "format": ACTOR_PROPOSAL_FORMAT,
        "kind": "desktop_action",
        "action": "activate_target",
        "args": {"candidate_ordinal": 1},
        "local_intent": "activate selected target",
    })
    return TargetLaunchEnvelope(
        decision_id="decision-launch-facts",
        mission_id="mission-launch-facts",
        run_id="run-launch-facts",
        mission_revision=1,
        obligation_id="open-target",
        frame=FrameCommitIdentity(
            frame_id=1,
            epoch=0,
            hwnd=1,
            region=(0, 0, 1920, 1080),
            scope="full_desktop",
            submitted_size=(1280, 720),
            image_sha256=hashlib.sha256(b"frame").hexdigest(),
        ),
        pointer=PointerLeaseState(),
        broker_projection_id="projection-launch-facts",
        capability_grant_ids=(),
        allowed_evidence_scope=(),
        candidate_fingerprint=candidate_fingerprint_for((candidate,)),
        proposal=proposal,
        candidate=candidate,
    )


def _receipt(
    outcome: MutationOutcome = MutationOutcome.APPLIED,
) -> RawTargetLaunchReceipt:
    if outcome is MutationOutcome.APPLIED:
        return RawTargetLaunchReceipt(
            outcome,
            1,
            process_id=PID,
            process_identity=IDENTITY,
        )
    return RawTargetLaunchReceipt(outcome, 0, error_kind="not_started")


class _PresenceBackend:
    def __init__(self, rows=(_presence_row(),), *, raises=False) -> None:
        self.rows = tuple(rows)
        self.raises = raises
        self.calls = 0

    def read_processes(self):
        self.calls += 1
        if self.raises:
            raise RuntimeError("presence unavailable")
        return self.rows


class _TargetBackend:
    def __init__(
        self,
        *,
        processes=(_process_row(),),
        windows=(_window_row(),),
        raises=False,
    ) -> None:
        self.processes = tuple(processes)
        self.windows = tuple(windows)
        self.raises = raises
        self.process_calls = 0
        self.window_calls = 0

    def read_processes(self):
        self.process_calls += 1
        if self.raises:
            raise RuntimeError("target facts unavailable")
        return self.processes

    def read_windows(self):
        self.window_calls += 1
        return self.windows


class _DesktopBackend:
    def __init__(self) -> None:
        self.live = {HWND}
        self.roots = {HWND: HWND}
        self.pids = {HWND: PID}
        self.creation_times = {PID: CREATED}
        self.paths = {PID: PATH}
        self.regions = {HWND: (40, 50, 900, 600)}
        self.foreground = HWND
        self.raises = False

    def is_window(self, hwnd):
        if self.raises:
            raise RuntimeError("desktop unavailable")
        return hwnd in self.live

    def root_hwnd(self, hwnd):
        return self.roots[hwnd]

    def process_id(self, hwnd):
        return self.pids[hwnd]

    def process_creation_time_ns(self, process_id):
        return self.creation_times[process_id]

    def process_executable_path(self, process_id):
        return self.paths[process_id]

    def window_region(self, hwnd):
        return self.regions[hwnd]

    def foreground_hwnd(self):
        return self.foreground

    def window_title(self, _hwnd):
        return "Orbit"

    def window_from_point(self, _point):
        raise AssertionError("launch observation must not hit-test")


class _CountingDesktopReader(WindowsDesktopFactsReader):
    def __init__(self, backend) -> None:
        super().__init__(backend)
        self.activation_reads = 0

    def read_activation_facts(self, hwnd):
        self.activation_reads += 1
        return super().read_activation_facts(hwnd)


def _adapter(
    *,
    presence_rows=(_presence_row(),),
    processes=(_process_row(),),
    windows=(_window_row(),),
    presence_raises=False,
    target_raises=False,
    presence_time=10.0,
    target_time=11.0,
    desktop=None,
):
    presence_backend = _PresenceBackend(
        presence_rows,
        raises=presence_raises,
    )
    target_backend = _TargetBackend(
        processes=processes,
        windows=windows,
        raises=target_raises,
    )
    desktop_backend = desktop or _DesktopBackend()
    desktop_reader = _CountingDesktopReader(desktop_backend)
    adapter = WindowsTargetLaunchObservationAdapter(
        ExecutablePresenceReader(
            presence_backend,
            clock=lambda: presence_time,
        ),
        WindowsTargetFactsReader(
            target_backend,
            clock=lambda: target_time,
        ),
        desktop_reader,
    )
    return adapter, presence_backend, target_backend, desktop_reader


class _Process:
    def __init__(self, *, pid=PID, created=CREATED / 1e9, path=PATH) -> None:
        self.pid = pid
        self.created = created
        self.path = path
        self.create_calls = 0
        self.exe_calls = 0

    def create_time(self):
        self.create_calls += 1
        return self.created

    def exe(self):
        self.exe_calls += 1
        return self.path


def test_psutil_identity_reader_uses_one_process_and_each_fact_once() -> None:
    process = _Process()
    factory_calls = []
    reader = PsutilLaunchProcessIdentityReader(
        lambda pid: factory_calls.append(pid) or process
    )

    result = reader(PID)

    assert factory_calls == [PID]
    assert process.create_calls == process.exe_calls == 1
    assert (
        result.process_id,
        result.creation_time_ns,
        result.executable_path,
        result.process_identity,
    ) == (PID, CREATED, PATH, IDENTITY)


@pytest.mark.parametrize(
    "process_id,process",
    (
        (True, _Process()),
        (PID, _Process(pid=PID + 1)),
        (PID, _Process(created=math.nan)),
        (PID, _Process(created=0)),
        (PID, _Process(path=r"Orbit.exe")),
    ),
)
def test_psutil_identity_reader_rejects_weak_or_changed_facts(
    process_id,
    process,
) -> None:
    with pytest.raises(WindowsTargetLaunchFactsError):
        PsutilLaunchProcessIdentityReader(lambda _pid: process)(process_id)


@pytest.mark.parametrize("selected_hwnd", (None, HWND))
def test_ready_requires_all_three_exact_one_shot_fact_sources(
    selected_hwnd,
) -> None:
    adapter, presence, target, desktop = _adapter()

    result = adapter.observe_launch(
        _envelope(),
        raw_receipt=_receipt(),
        selected_hwnd=selected_hwnd,
    )

    assert isinstance(adapter, TargetLaunchObservationPort)
    assert result.state is TargetLaunchObservationState.READY
    assert result.observation is not None
    assert result.observation.exact_foreground is True
    assert result.observation.activated_target.hwnd == HWND
    assert result.observation.activated_target.process_identity == IDENTITY
    assert presence.calls == 1
    assert target.process_calls == target.window_calls == 1
    assert desktop.activation_reads == 1


@pytest.mark.parametrize(
    "windows,foreground",
    (
        ((), HWND),
        ((_window_row(visible=False),), HWND),
        ((_window_row(),), 0),
    ),
)
def test_no_visible_root_or_no_exact_foreground_is_not_ready(
    windows,
    foreground,
) -> None:
    desktop_backend = _DesktopBackend()
    desktop_backend.foreground = foreground
    adapter, _presence, _target, desktop = _adapter(
        windows=windows,
        desktop=desktop_backend,
    )

    result = adapter.observe_launch(
        _envelope(),
        raw_receipt=_receipt(),
        selected_hwnd=None,
    )

    assert result.state is TargetLaunchObservationState.NOT_READY
    assert result.observation is None
    assert desktop.activation_reads == (1 if windows and windows[0]["visible"] else 0)


@pytest.mark.parametrize(
    "presence_rows,processes",
    (
        ((), ()),
        ((_presence_row(),), ()),
    ),
)
def test_missing_raw_process_is_not_claimed_exited(presence_rows, processes) -> None:
    adapter, _presence, _target, desktop = _adapter(
        presence_rows=presence_rows,
        processes=processes,
        windows=(),
    )

    result = adapter.observe_launch(
        _envelope(),
        raw_receipt=_receipt(),
        selected_hwnd=None,
    )

    assert result.state is TargetLaunchObservationState.SOURCE_UNAVAILABLE
    assert desktop.activation_reads == 0


def test_presence_absence_with_same_later_identity_is_source_unavailable() -> None:
    adapter, _presence, target, _desktop = _adapter(
        presence_rows=(),
        processes=(_process_row(),),
        windows=(),
    )

    result = adapter.observe_launch(
        _envelope(),
        raw_receipt=_receipt(),
        selected_hwnd=None,
    )

    assert result.state is TargetLaunchObservationState.SOURCE_UNAVAILABLE
    assert target.process_calls == 1


def test_raw_pid_reused_by_another_executable_is_process_reused() -> None:
    other_path = r"C:\Apps\Other\Other.exe"
    adapter, _presence, target, _desktop = _adapter(
        presence_rows=(
            _presence_row(path=other_path, name="Other.exe"),
        ),
        processes=(_process_row(path=other_path),),
        windows=(),
    )

    result = adapter.observe_launch(
        _envelope(),
        raw_receipt=_receipt(),
        selected_hwnd=None,
    )

    assert result.state is TargetLaunchObservationState.PROCESS_REUSED
    assert target.process_calls == 1


@pytest.mark.parametrize("layer", ("presence", "target", "desktop"))
def test_same_pid_with_changed_strong_identity_is_process_reused(layer) -> None:
    presence_rows = (_presence_row(),)
    processes = (_process_row(),)
    desktop = _DesktopBackend()
    if layer == "presence":
        presence_rows = (_presence_row(created=CREATED + 1),)
    elif layer == "target":
        processes = (_process_row(created=CREATED + 1),)
    else:
        desktop.creation_times[PID] = CREATED + 1
    adapter, _presence, _target, _desktop = _adapter(
        presence_rows=presence_rows,
        processes=processes,
        desktop=desktop,
    )

    result = adapter.observe_launch(
        _envelope(),
        raw_receipt=_receipt(),
        selected_hwnd=None,
    )

    assert result.state is TargetLaunchObservationState.PROCESS_REUSED


@pytest.mark.parametrize(
    "presence_rows,windows,selected_hwnd",
    (
        (
            (_presence_row(), _presence_row(PID + 1)),
            (_window_row(),),
            None,
        ),
        (
            (_presence_row(),),
            (_window_row(), _window_row(HWND + 1)),
            None,
        ),
    ),
)
def test_nonunique_or_changed_window_authority_is_ambiguous(
    presence_rows,
    windows,
    selected_hwnd,
) -> None:
    processes = tuple(
        _process_row(row["process_id"])
        for row in presence_rows
    )
    adapter, _presence, _target, desktop = _adapter(
        presence_rows=presence_rows,
        processes=processes,
        windows=windows,
    )

    result = adapter.observe_launch(
        _envelope(),
        raw_receipt=_receipt(),
        selected_hwnd=selected_hwnd,
    )

    assert result.state is TargetLaunchObservationState.AMBIGUOUS
    assert desktop.activation_reads == 0


def test_second_exact_process_in_fresher_snapshot_is_ambiguous() -> None:
    adapter, _presence, target, desktop = _adapter(
        processes=(_process_row(), _process_row(PID + 1)),
        windows=(_window_row(),),
    )

    result = adapter.observe_launch(
        _envelope(),
        raw_receipt=_receipt(),
        selected_hwnd=None,
    )

    assert result.state is TargetLaunchObservationState.AMBIGUOUS
    assert target.process_calls == target.window_calls == 1
    assert desktop.activation_reads == 0


def test_selected_hwnd_rechecks_fixed_root_without_reselecting() -> None:
    adapter, _presence, _target, desktop = _adapter(
        windows=(_window_row(), _window_row(HWND + 1)),
    )

    result = adapter.observe_launch(
        _envelope(),
        raw_receipt=_receipt(),
        selected_hwnd=HWND,
    )

    assert result.state is TargetLaunchObservationState.READY
    assert result.observation is not None
    assert result.observation.activated_target.hwnd == HWND
    assert desktop.activation_reads == 1


def test_missing_fixed_hwnd_is_not_ready_and_is_not_replaced() -> None:
    adapter, _presence, _target, desktop = _adapter(
        windows=(_window_row(HWND + 1),),
    )

    result = adapter.observe_launch(
        _envelope(),
        raw_receipt=_receipt(),
        selected_hwnd=HWND,
    )

    assert result.state is TargetLaunchObservationState.NOT_READY
    assert result.observation is None
    assert desktop.activation_reads == 0


@pytest.mark.parametrize(
    "kwargs",
    (
        {"presence_rows": (_presence_row(created=None),)},
        {"presence_raises": True},
        {"target_raises": True},
        {"presence_time": 12.0, "target_time": 11.0},
    ),
)
def test_incomplete_or_unavailable_snapshot_is_explicit(kwargs) -> None:
    adapter, _presence, _target, _desktop = _adapter(**kwargs)

    result = adapter.observe_launch(
        _envelope(),
        raw_receipt=_receipt(),
        selected_hwnd=None,
    )

    assert result.state is TargetLaunchObservationState.SOURCE_UNAVAILABLE


def test_desktop_source_failure_is_explicit() -> None:
    desktop = _DesktopBackend()
    desktop.raises = True
    adapter, _presence, _target, reader = _adapter(desktop=desktop)

    result = adapter.observe_launch(
        _envelope(),
        raw_receipt=_receipt(),
        selected_hwnd=None,
    )

    assert result.state is TargetLaunchObservationState.SOURCE_UNAVAILABLE
    assert reader.activation_reads == 1


def test_nonapplied_receipt_reads_no_source() -> None:
    adapter, presence, target, desktop = _adapter()

    result = adapter.observe_launch(
        _envelope(),
        raw_receipt=_receipt(MutationOutcome.NOT_STARTED),
        selected_hwnd=None,
    )

    assert result.state is TargetLaunchObservationState.SOURCE_UNAVAILABLE
    assert presence.calls == target.process_calls == desktop.activation_reads == 0


def test_random_source_order_does_not_change_unique_ready_result() -> None:
    randomizer = random.Random(731)
    other_process = _process_row(
        PID + 1,
        path=r"C:\Apps\Other\Other.exe",
    )
    other_window = _window_row(HWND + 1, process_id=PID + 1)
    for _ in range(50):
        processes = [_process_row(), other_process]
        windows = [_window_row(), other_window]
        randomizer.shuffle(processes)
        randomizer.shuffle(windows)
        adapter, presence, target, desktop = _adapter(
            processes=processes,
            windows=windows,
        )

        result = adapter.observe_launch(
            _envelope(),
            raw_receipt=_receipt(),
            selected_hwnd=HWND,
        )

        assert result.state is TargetLaunchObservationState.READY
        assert presence.calls == target.process_calls == desktop.activation_reads == 1


def test_adapter_is_bounded_and_contains_no_mutation_or_wait_mechanism() -> None:
    source_path = Path(
        "python/winpilot_home/winpilot/visual_runtime/"
        "windows_target_launch_facts.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert len(source.splitlines()) <= 260
    assert not any(isinstance(node, (ast.For, ast.While)) for node in ast.walk(tree))
    lowered = source.casefold()
    for forbidden in (
        "setforegroundwindow",
        "shellexecute",
        "subprocess",
        "sleep(",
        "wait(",
        "pywinauto",
        "uia",
        "ocr",
    ):
        assert forbidden not in lowered
