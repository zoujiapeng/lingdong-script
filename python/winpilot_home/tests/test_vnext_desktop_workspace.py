from __future__ import annotations

from dataclasses import replace

import pytest

from winpilot.vnext.desktop_adapter import (
    DesktopWorkspace,
    WorkspaceSurface,
)
from winpilot.vnext.mission import TargetBinding


HWND_A = 101
HWND_B = 202
HWND_TRANSIENT = 303
PID_A = 1001
PID_B = 2002
PID_TRANSIENT = 3003


def _surface(
    hwnd: int,
    process_id: int,
    executable: str,
    *,
    foreground: int,
    region: tuple[int, int, int, int] = (0, 0, 800, 600),
) -> WorkspaceSurface:
    return WorkspaceSurface(
        hwnd=hwnd,
        root_hwnd=hwnd,
        process_id=process_id,
        executable_identity=executable,
        title=f"Window {hwnd}",
        class_name="TestWindow",
        region=region,
        foreground_root_hwnd=foreground,
    )


def _binding(
    hwnd: int = HWND_A,
    process_id: int = PID_A,
    executable: str = "C:/a.exe",
    *,
    binding_id: str = "binding-a",
    epoch: int = 10,
) -> TargetBinding:
    return TargetBinding(
        binding_id=binding_id,
        hwnd=hwnd,
        process_id=process_id,
        executable_identity=executable,
        title_snapshot=f"Window {hwnd}",
        epoch=epoch,
    )


class _Topology:
    def __init__(self) -> None:
        self.foreground = HWND_A
        self.windows = {
            HWND_A: _surface(
                HWND_A,
                PID_A,
                "C:/a.exe",
                foreground=self.foreground,
            ),
            HWND_B: _surface(
                HWND_B,
                PID_B,
                "C:/b.exe",
                foreground=self.foreground,
            ),
        }

    def read(self, hwnd: int) -> WorkspaceSurface | None:
        surface = self.windows.get(hwnd)
        return (
            None
            if surface is None
            else replace(
                surface,
                foreground_root_hwnd=self.foreground,
            )
        )

    def hit_test(self, _x: int, _y: int) -> int:
        return self.foreground


def _workspace(topology: _Topology) -> DesktopWorkspace:
    return DesktopWorkspace(
        binding=_binding(),
        surface=topology.read(HWND_A),
        reader=topology.read,
        foreground_reader=lambda: topology.foreground,
        hit_test_root=topology.hit_test,
        wait=lambda _seconds: None,
    )


def _shell_workspace(topology: _Topology) -> DesktopWorkspace:
    return DesktopWorkspace(
        binding=_binding(),
        surface=topology.read(HWND_A),
        reader=topology.read,
        foreground_reader=lambda: topology.foreground,
        hit_test_root=topology.hit_test,
        wait=lambda _seconds: None,
        desktop_shell=True,
    )


def test_desktop_shell_flag_retires_when_a_real_window_is_adopted() -> None:
    topology = _Topology()
    workspace = _shell_workspace(topology)
    assert workspace.desktop_shell is True

    topology.foreground = HWND_B
    workspace.finish_action(
        action_id="decision-1",
        action="launch_application",
        input_completed=True,
    )
    observation = workspace.observe()
    assert observation.replaces_binding

    replacement = _binding(
        HWND_B,
        PID_B,
        "C:/b.exe",
        binding_id="binding-b",
        epoch=11,
    )
    workspace.adopt(observation, replacement)

    assert workspace.binding == replacement
    assert workspace.desktop_shell is False


def test_unrelated_foreground_is_observed_but_never_adopted() -> None:
    topology = _Topology()
    workspace = _workspace(topology)
    topology.foreground = HWND_B

    observation = workspace.observe()

    assert observation.surface.hwnd == HWND_A
    assert not observation.replaces_binding
    assert workspace.binding.hwnd == HWND_A
    assert workspace.snapshot(HWND_A).foreground_root_hwnd == HWND_B


def test_completed_click_adopts_stable_foreground_on_next_observation() -> None:
    topology = _Topology()
    workspace = _workspace(topology)
    topology.foreground = HWND_B

    workspace.finish_action(
        action_id="decision-1",
        action="click",
        input_completed=True,
    )

    assert workspace.binding.hwnd == HWND_A
    observation = workspace.observe()
    assert observation.replaces_binding
    assert observation.surface.hwnd == HWND_B
    assert observation.causal_action_id == "decision-1"

    replacement = _binding(
        HWND_B,
        PID_B,
        "C:/b.exe",
        binding_id="binding-b",
        epoch=11,
    )
    workspace.adopt(observation, replacement)

    assert workspace.binding == replacement
    assert workspace.generation == 1
    assert workspace.snapshot(HWND_A) is None
    assert workspace.snapshot(HWND_B).hwnd == HWND_B


def test_discarded_full_desktop_successor_is_never_adopted() -> None:
    topology = _Topology()
    topology.windows[HWND_TRANSIENT] = _surface(
        HWND_TRANSIENT,
        PID_TRANSIENT,
        "C:/transient.exe",
        foreground=HWND_TRANSIENT,
        region=(0, 0, 1920, 1080),
    )
    workspace = _workspace(topology)
    workspace.finish_action(
        action_id="decision-1",
        action="hotkey",
        input_completed=True,
    )
    topology.foreground = HWND_TRANSIENT

    transient = workspace.observe()

    assert transient.replaces_binding
    assert transient.surface.hwnd == HWND_TRANSIENT
    assert workspace.validate(transient) is not None
    workspace.discard(transient)
    assert workspace.binding.hwnd == HWND_A
    assert workspace.generation == 0
    assert workspace.validate(transient) is None
    stale_replacement = _binding(
        HWND_TRANSIENT,
        PID_TRANSIENT,
        "C:/transient.exe",
        binding_id="binding-transient",
        epoch=11,
    )
    with pytest.raises(RuntimeError, match="stale or uncaused"):
        workspace.adopt(transient, stale_replacement)

    topology.foreground = HWND_B
    successor = workspace.observe()
    replacement = _binding(
        HWND_B,
        PID_B,
        "C:/b.exe",
        binding_id="binding-b",
        epoch=11,
    )
    workspace.adopt(successor, replacement)

    assert successor.replaces_binding
    assert successor.surface.hwnd == HWND_B
    assert workspace.binding == replacement
    assert workspace.generation == 1
    assert workspace.snapshot(HWND_TRANSIENT) is None


def test_two_discarded_successors_exhaust_bounded_handoff() -> None:
    third_hwnd = 404
    third_pid = 4004
    topology = _Topology()
    topology.windows[HWND_TRANSIENT] = _surface(
        HWND_TRANSIENT,
        PID_TRANSIENT,
        "C:/transient.exe",
        foreground=HWND_TRANSIENT,
    )
    topology.windows[third_hwnd] = _surface(
        third_hwnd,
        third_pid,
        "C:/third.exe",
        foreground=third_hwnd,
    )
    workspace = _workspace(topology)
    workspace.finish_action(
        action_id="decision-1",
        action="hotkey",
        input_completed=True,
    )

    topology.foreground = HWND_B
    first = workspace.observe()
    workspace.discard(first)
    topology.foreground = HWND_TRANSIENT
    second = workspace.observe()
    workspace.discard(second)
    topology.foreground = third_hwnd

    observation = workspace.observe()

    assert not observation.replaces_binding
    assert observation.surface.hwnd == HWND_A
    assert workspace.binding.hwnd == HWND_A
    assert workspace.generation == 0


def test_successor_that_loses_foreground_fails_validation() -> None:
    topology = _Topology()
    workspace = _workspace(topology)
    workspace.finish_action(
        action_id="decision-1",
        action="click",
        input_completed=True,
    )
    topology.foreground = HWND_B

    successor = workspace.observe()
    topology.foreground = HWND_A

    assert successor.replaces_binding
    assert successor.surface.hwnd == HWND_B
    assert workspace.validate(successor) is None
    assert workspace.binding.hwnd == HWND_A
    assert workspace.generation == 0
    assert workspace.snapshot(HWND_A).hwnd == HWND_A


def test_not_started_input_never_creates_causal_transition() -> None:
    topology = _Topology()
    workspace = _workspace(topology)
    topology.foreground = HWND_B

    workspace.finish_action(
        action_id="decision-1",
        action="click",
        input_completed=False,
    )

    assert not workspace.observe().replaces_binding
    assert workspace.binding.hwnd == HWND_A


def test_any_completed_action_adopts_only_a_real_foreground_change() -> None:
    topology = _Topology()
    workspace = _workspace(topology)

    # scroll that does NOT change the foreground must not adopt anything.
    workspace.finish_action(
        action_id="decision-1",
        action="scroll",
        input_completed=True,
    )
    assert not workspace.observe().replaces_binding

    # the same scroll, when the foreground genuinely changed, adopts the
    # successor: window hand-off is semantic (driven by the observation), not
    # gated by an action-name list.
    topology.foreground = HWND_B
    workspace.finish_action(
        action_id="decision-2",
        action="scroll",
        input_completed=True,
    )
    observation = workspace.observe()
    assert observation.replaces_binding
    assert observation.surface.hwnd == HWND_B


def test_disappearing_pending_candidate_is_discarded() -> None:
    topology = _Topology()
    workspace = _workspace(topology)
    topology.foreground = HWND_B
    workspace.finish_action(
        action_id="decision-1",
        action="hotkey",
        input_completed=True,
    )
    del topology.windows[HWND_B]

    observation = workspace.observe()

    assert observation.surface.hwnd == HWND_A
    assert not observation.replaces_binding
    assert workspace.binding.hwnd == HWND_A


def test_source_can_disappear_before_successor_is_observed() -> None:
    topology = _Topology()
    workspace = _workspace(topology)

    workspace.finish_action(
        action_id="decision-1",
        action="hotkey",
        input_completed=True,
    )
    del topology.windows[HWND_A]
    topology.foreground = HWND_B

    observation = workspace.observe()

    assert observation.replaces_binding
    assert observation.surface.hwnd == HWND_B
    assert observation.causal_action_id == "decision-1"


def test_missing_source_keeps_bounded_handoff_for_delayed_successor() -> None:
    topology = _Topology()
    samples = iter((HWND_A,) * 5 + (HWND_B,))

    def foreground_reader() -> int:
        topology.foreground = next(samples)
        return topology.foreground

    workspace = DesktopWorkspace(
        binding=_binding(),
        surface=topology.read(HWND_A),
        reader=topology.read,
        foreground_reader=foreground_reader,
        hit_test_root=topology.hit_test,
        wait=lambda _seconds: None,
    )
    workspace.finish_action(
        action_id="decision-1",
        action="hotkey",
        input_completed=True,
    )
    del topology.windows[HWND_A]

    observation = workspace.observe()

    assert observation.replaces_binding
    assert observation.surface.hwnd == HWND_B
    assert observation.causal_action_id == "decision-1"
    assert workspace.binding.hwnd == HWND_A
    assert workspace.generation == 0


def test_delayed_foreground_successor_is_sampled_within_causal_window() -> None:
    topology = _Topology()
    samples = iter((HWND_A, HWND_A, HWND_B))

    def foreground_reader() -> int:
        topology.foreground = next(samples)
        return topology.foreground

    workspace = DesktopWorkspace(
        binding=_binding(),
        surface=topology.read(HWND_A),
        reader=topology.read,
        foreground_reader=foreground_reader,
        hit_test_root=topology.hit_test,
        wait=lambda _seconds: None,
    )
    workspace.finish_action(
        action_id="decision-1",
        action="hotkey",
        input_completed=True,
    )

    observation = workspace.observe()

    assert observation.replaces_binding
    assert observation.surface.hwnd == HWND_B


def test_successor_can_arrive_after_one_intervening_observation() -> None:
    topology = _Topology()
    workspace = _workspace(topology)
    workspace.finish_action(
        action_id="decision-1",
        action="click",
        input_completed=True,
    )

    assert not workspace.observe().replaces_binding
    topology.foreground = HWND_B

    observation = workspace.observe()

    assert observation.replaces_binding
    assert observation.surface.hwnd == HWND_B
    assert observation.causal_action_id == "decision-1"


def test_current_window_geometry_can_change_between_actions() -> None:
    topology = _Topology()
    workspace = _workspace(topology)
    topology.windows[HWND_A] = replace(
        topology.windows[HWND_A],
        region=(20, 30, 1024, 700),
    )

    observation = workspace.observe()

    assert observation.surface.region == (20, 30, 1024, 700)
    assert workspace.snapshot(HWND_A).region == (20, 30, 1024, 700)
