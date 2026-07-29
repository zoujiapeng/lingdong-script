from __future__ import annotations

from dataclasses import replace

from winpilot.vnext.desktop_adapter import (
    DesktopWorkspace,
    WorkspaceSurface,
)
from winpilot.vnext.mission import TargetBinding


HWND_A = 101
HWND_B = 202
PID_A = 1001
PID_B = 2002


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
    evidence_id: str = "evidence-a",
    epoch: int = 10,
) -> TargetBinding:
    return TargetBinding(
        binding_id=binding_id,
        hwnd=hwnd,
        process_id=process_id,
        executable_identity=executable,
        title_snapshot=f"Window {hwnd}",
        established_by_evidence_ref=evidence_id,
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
        evidence_id="evidence-b",
        epoch=11,
    )
    workspace.adopt(observation, replacement)

    assert workspace.binding == replacement
    assert workspace.generation == 1
    assert workspace.snapshot(HWND_A) is None
    assert workspace.snapshot(HWND_B).hwnd == HWND_B


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


def test_non_window_changing_action_never_adopts_new_foreground() -> None:
    topology = _Topology()
    workspace = _workspace(topology)
    topology.foreground = HWND_B

    workspace.finish_action(
        action_id="decision-1",
        action="scroll",
        input_completed=True,
    )

    assert not workspace.observe().replaces_binding


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
