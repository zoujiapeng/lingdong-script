from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
from itertools import permutations
import json
from pathlib import Path
from types import SimpleNamespace
import threading
from typing import Any

import cv2
import numpy as np
import pytest

from winpilot.action.raw_device import (
    AdmittedPhysicalClickBackend,
    AdmittedPhysicalHotkeyBackend,
    AdmittedPhysicalScrollBackend,
    AdmittedPhysicalTypeTextBackend,
    CANONICAL_HOTKEY_KEYS,
    CANONICAL_HOTKEY_MODIFIERS,
    MAX_TYPE_TEXT_CODEPOINTS,
    MutationOutcome,
    RawActionReceipt,
    RawBackendResult,
)
from winpilot.orchestrator.contracts import (
    ArpEdge,
    ArpGraph,
    ArpNode,
    ArpNodeKind,
    ArpPolicy,
    ArpRetryPolicy,
    ArpRunStatus,
)
from winpilot.orchestrator.runtime import ArpRunManager
from winpilot.orchestrator import goal_admission
from winpilot.orchestrator import live_agent
from winpilot.visual_runtime.frame_authority import FrameAuthority
from winpilot.utils.log import BUS
from winpilot.utils.screenshot import Region
from winpilot.visual_runtime.keyboard_contracts import HotkeyChord
from winpilot.visual_runtime.frame_observer import FrameObserver
from winpilot.vnext.actor import InvalidActorProposal, parse_actor_proposal
from winpilot.vnext.capabilities import (
    ExecutionMedium,
    ToolArgumentValidationError,
    ToolArgumentValidator,
)
from winpilot.vnext.evidence import EvidenceRegistry
from winpilot.vnext.mission import (
    EvidenceRef,
    MissionDefinition,
    MissionReducer,
    MissionStatus,
    ObligationSpec,
    TargetBinding,
    is_safe_action_summary,
)
from winpilot.vnext.mission import contracts as mission_contracts
from winpilot.vnext.runtime import (
    MissionRuntimeResult,
    RuntimeStatus,
)
from winpilot.vnext.runtime import (
    CURRENT_FRAME_EVIDENCE_KIND,
    POST_ACTION_FRAME_EVIDENCE_KIND,
    RuntimeFrameIdentity,
)


NOW_MS = 1_900_000_000_000
HWND = 81234
PID = 731
CONTROL_PID = 999
RECT = [100, 200, 740, 680]
REGION = (100, 200, 640, 480)
GOAL = "Make the requested visible state exist"
THREAD_ID = "thread-vnext-composition"
TURN_ID = "turn-vnext-composition"
PRIVATE_TEXT = "PRIVATE-TEXT-c4-public-projection-7f2d"
PRIVATE_CHORD = "ctrl+shift+f11"
PRIVATE_X = 77_889_911
PRIVATE_Y = 66_778_822
PRIVATE_AMOUNT = -99_887_766
_PRIVATE_EVIDENCE_FIELDS = {
    "actor_envelope",
    "canonical_arguments",
    "physical_arguments",
    "submitted_arguments",
    "physical_attempt",
    "physical_attempts",
    "runtime_evidence_report",
    "exact_text",
    "hotkey_chord",
    "raw_model_response",
    "reasoning",
}
_PUBLIC_RUNTIME_EVIDENCE_FIELDS = {
    "available",
    "status",
    "journal_head_sha256",
    "journal_event_count",
    "report_sha256",
    "audit_locator",
    "parameter_visibility",
}


def _nested_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(map(str, value)).union(
            *(_nested_keys(item) for item in value.values()),
            set(),
        )
    if isinstance(value, (list, tuple)):
        return set().union(
            *(_nested_keys(item) for item in value),
            set(),
        )
    return set()


def _nested_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [
            nested
            for item in value.values()
            for nested in _nested_values(item)
        ]
    if isinstance(value, (list, tuple)):
        return [
            nested
            for item in value
            for nested in _nested_values(item)
        ]
    return [value]


def _assert_private_runtime_evidence_absent(value: Any) -> None:
    assert not (_nested_keys(value) & _PRIVATE_EVIDENCE_FIELDS)
    nested_values = _nested_values(value)
    for sentinel in (
        PRIVATE_TEXT,
        PRIVATE_CHORD,
        PRIVATE_X,
        PRIVATE_Y,
        PRIVATE_AMOUNT,
    ):
        assert sentinel not in nested_values
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
    )
    for sentinel in (
        PRIVATE_TEXT,
        PRIVATE_CHORD,
        '"x": 77889911',
        '"y": 66778822',
        '"amount": -99887766',
    ):
        assert sentinel not in serialized


def _jpeg(width: int, height: int, level: int = 127) -> str:
    image = np.full((height, width, 3), level, dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def _graph_dict() -> dict[str, Any]:
    graph_id = f"vnext-live-agent-{live_agent._fnv1a_utf16(GOAL)}"
    retry = {
        "max_attempts": 1,
        "delay_ms": 0,
        "backoff": 1,
    }
    return {
        "format": "arp/1",
        "id": graph_id,
        "name": GOAL,
        "description": "",
        "nodes": [
            {
                "id": "start",
                "kind": "start",
                "name": "start",
                "config": {},
                "retry": retry,
                "disabled": False,
                "x": 0,
                "y": 0,
            },
            {
                "id": "full-goal",
                "kind": "agent_goal",
                "name": "full goal",
                "config": {"goal": GOAL},
                "retry": retry,
                "disabled": False,
                "x": 0,
                "y": 100,
            },
            {
                "id": "end",
                "kind": "end",
                "name": "end",
                "config": {},
                "retry": retry,
                "disabled": False,
                "x": 0,
                "y": 200,
            },
        ],
        "edges": [
            {
                "id": "start-to-full-goal",
                "source": "start",
                "target": "full-goal",
                "branch": "next",
                "label": "",
            },
            {
                "id": "full-goal-to-end",
                "source": "full-goal",
                "target": "end",
                "branch": "next",
                "label": "",
            },
        ],
        "variables": {},
        "policy": {
            "max_node_visits": 1,
            "fail_fast": True,
            "require_evidence_on_done": True,
            "allow_shell": False,
            "allow_desktop_mutation": True,
        },
        "metadata": {},
    }


def _graph() -> ArpGraph:
    return ArpGraph.model_validate(_graph_dict())


def _inputs(*, captured_at: int = NOW_MS - 500) -> dict[str, Any]:
    return {
        "execution_context": {
            "format": "execution-context/1",
            "entrypoint": "native_agent_cockpit",
            "task_id": _graph().id,
            "submitted_at": NOW_MS - 1_000,
            "desktop_handoff": {
                "required": True,
                "strategy": "foreground_after_control_surface_yield",
                "yielded": True,
                "control_surface_pid": CONTROL_PID,
                # Renderer and Electron main-process clocks can differ by 1 ms.
                "prepared_at": NOW_MS - 1_001,
                "reason": "",
            },
            "target_window": {
                "format": "desktop-target/1",
                "hwnd": HWND,
                "title": "Canary Target",
                "process_id": PID,
                "class_name": "CanaryWindow",
                "rect": list(RECT),
                "captured_at": captured_at,
                "foreground_verified": True,
                "usable": True,
                "failure_type": "",
                "reason": "",
                "binding_mode": "window",
            },
        }
    }


def _config() -> dict[str, Any]:
    return {
        "api": {
            "provider": "openai",
            "base_url": "https://provider.invalid/v1",
            "api_key": "test-secret",
            "model": "gpt-4o-mini",
            "request_timeout_s": 75,
            "request_total_timeout_s": 120,
        },
        "vnext": {
            "live_agent_enabled": True,
            "max_turns": 30,
            "max_runtime_s": 300,
            "max_model_calls": 60,
        },
    }


def _observe_desktop_response(content: str = "") -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {
                            "id": "call-observe-desktop",
                            "type": "function",
                            "function": {
                                "name": "observe_desktop",
                                "arguments": "{}",
                            },
                        }
                    ],
                }
            }
        ]
    }


def _direct_reply_response(reply: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": reply,
                    "tool_calls": [],
                }
            }
        ]
    }


def _prepare(
    graph: ArpGraph,
    inputs: dict[str, Any],
    config_snapshot: dict[str, Any],
    *,
    run_manager: Any,
    _dependencies: live_agent._CompositionDependencies,
    history: tuple[dict[str, str], ...] = (),
) -> Any:
    return live_agent.prepare_live_vnext_execution(
        graph,
        inputs,
        config_snapshot,
        run_manager=run_manager,
        thread_id=THREAD_ID,
        turn_id=TURN_ID,
        history=history,
        _dependencies=_dependencies,
    )


def _run_with_desktop_lease(
    execution: Any,
    action: Any,
    *,
    inputs: dict[str, Any] | None = None,
) -> Any:
    """Deliver the exact target only after the Actor requests one lease."""

    lease = execution.runtime_state["_desktop_lease"]
    stop = threading.Event()
    errors: list[BaseException] = []

    def deliver() -> None:
        while not stop.wait(0.001):
            snapshot = lease.snapshot()
            if snapshot["status"] == "waiting":
                try:
                    lease.bind(
                        run_id=snapshot["run_id"],
                        lease_id=snapshot["lease_id"],
                        inputs=deepcopy(inputs or _inputs()),
                    )
                except BaseException as exc:  # surfaced in the test thread
                    errors.append(exc)
                return
            if snapshot["status"] in {"closed", "delivered"}:
                return

    worker = threading.Thread(target=deliver, daemon=True)
    worker.start()
    try:
        result = action()
    finally:
        stop.set()
        worker.join(timeout=2.0)
    assert not worker.is_alive()
    if errors:
        raise errors[0]
    return result


def _run_prepared(manager: ArpRunManager, execution: Any) -> dict[str, Any]:
    return _run_with_desktop_lease(
        execution,
        lambda: manager.run_prepared(execution),
    )


def _facts(
    *,
    process_id: int = PID,
    foreground_root_hwnd: int | None = HWND,
    region: tuple[int, int, int, int] = REGION,
    title: str = "Canary Target",
) -> live_agent._TargetFacts:
    return live_agent._TargetFacts(
        hwnd=HWND,
        root_hwnd=HWND,
        process_id=process_id,
        executable_identity=r"c:\apps\live_agent.exe",
        title=title,
        class_name="CanaryWindow",
        region=region,
        foreground_root_hwnd=foreground_root_hwnd,
    )


@dataclass
class _FakeSession:
    results: list[MissionRuntimeResult]
    model_calls: int = 0
    closed: bool = False
    calls: int = 0
    mission_id: str = ""
    run_id: str = ""
    target_binding_id: str = ""
    frame_epoch: int = 0
    rejection_feedbacks: list[str | None] = field(default_factory=list)

    def run_one_turn(
        self,
        *,
        turn_index: int,
        stop_event: Any,
        rejection_feedback: str | None = None,
    ) -> MissionRuntimeResult:
        assert turn_index == self.calls
        assert not stop_event.is_set()
        self.rejection_feedbacks.append(rejection_feedback)
        result = self.results[self.calls]
        self.calls += 1
        self.model_calls += 1
        return result

    def completion_snapshot(self) -> dict[str, Any]:
        verified = bool(
            self.calls
            and self.results[self.calls - 1].status
            is RuntimeStatus.MISSION_VERIFIED
        )
        return {
            "mission_id": self.mission_id,
            "run_id": self.run_id,
            "mission_status": "verified" if verified else "blocked",
            "mission_verification_id": "mission-verification" if verified else None,
            "obligation_verification_id": "obligation-verification" if verified else None,
            "verified_evidence_refs": ["e-visual"] if verified else [],
            "verified_evidence_bindings": (
                [
                    {
                        "id": "e-visual",
                        "mission_id": self.mission_id,
                        "run_id": self.run_id,
                        "obligation_id": "full-goal",
                        "frame_id": 9,
                    }
                ]
                if verified
                else []
            ),
            "target_binding_id": self.target_binding_id,
            "target_hwnd": HWND,
            "target_epoch": self.frame_epoch,
            "frame_id": 9,
            "frame_hwnd": HWND,
            "frame_epoch": self.frame_epoch,
        }

    def close(self) -> None:
        self.closed = True


class _StubActorBundle:
    def __init__(self) -> None:
        self.actor = SimpleNamespace(
            propose=lambda *_args, **_kwargs: pytest.fail(
                "stub Actor must not be invoked during composition"
            )
        )
        self.counter = SimpleNamespace(count=0)
        self.closed = False
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True


def _dependencies(
    *,
    session: _FakeSession | None = None,
    reader: Any = None,
    client_factory: Any = None,
) -> live_agent._CompositionDependencies:
    def build(build_spec: Any) -> _FakeSession | None:
        if session is None:
            return None
        session.mission_id = build_spec.mission_id
        session.run_id = build_spec.run_id
        session.target_binding_id = build_spec.target.binding_id
        session.frame_epoch = build_spec.target.captured_at_ms
        session.model_calls = build_spec.actor_bundle.counter.count
        return session

    def default_client_factory(**_kwargs: Any) -> Any:
        return _RawClient(_observe_desktop_response())

    return live_agent._CompositionDependencies(
        wall_clock=lambda: NOW_MS / 1000.0,
        monotonic=lambda: 10.0,
        target_reader=reader or (lambda _hwnd: _facts()),
        hit_test_root=lambda _x, _y: HWND,
        client_factory=client_factory or default_client_factory,
        session_builder=(
            build if session is not None else None
        ),
    )


def test_live_target_reader_uses_visible_dwm_bounds_for_maximized_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import win32gui
    import win32process

    monkeypatch.setattr(win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(win32gui, "GetAncestor", lambda hwnd, _flag: hwnd)
    monkeypatch.setattr(win32gui, "GetForegroundWindow", lambda: HWND)
    monkeypatch.setattr(win32gui, "GetWindowText", lambda _hwnd: "Canary Target")
    monkeypatch.setattr(win32gui, "GetClassName", lambda _hwnd: "CanaryWindow")
    monkeypatch.setattr(
        win32gui,
        "GetWindowRect",
        lambda _hwnd: pytest.fail("raw resize-border bounds must not be used"),
    )
    monkeypatch.setattr(
        win32process,
        "GetWindowThreadProcessId",
        lambda _hwnd: (1, PID),
    )
    monkeypatch.setattr(
        live_agent,
        "_process_executable_identity",
        lambda _pid: r"c:\apps\live_agent.exe",
    )
    monkeypatch.setattr(
        live_agent,
        "window_region",
        lambda _hwnd: Region(0, 0, 1920, 1040),
    )
    monkeypatch.setattr(
        live_agent,
        "virtual_screen_region",
        lambda: Region(0, 0, 1920, 1080),
    )

    facts = live_agent._read_win32_target(HWND)

    assert facts is not None
    assert facts.region == (0, 0, 1920, 1040)


def test_live_target_reader_clips_invisible_maximized_resize_border(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import win32gui
    import win32process

    monkeypatch.setattr(win32gui, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(win32gui, "GetAncestor", lambda hwnd, _flag: hwnd)
    monkeypatch.setattr(win32gui, "GetForegroundWindow", lambda: HWND)
    monkeypatch.setattr(win32gui, "GetWindowText", lambda _hwnd: "Canary Target")
    monkeypatch.setattr(win32gui, "GetClassName", lambda _hwnd: "CanaryWindow")
    monkeypatch.setattr(
        win32process,
        "GetWindowThreadProcessId",
        lambda _hwnd: (1, PID),
    )
    monkeypatch.setattr(
        live_agent,
        "_process_executable_identity",
        lambda _pid: r"c:\apps\live_agent.exe",
    )
    monkeypatch.setattr(
        live_agent,
        "window_region",
        lambda _hwnd: Region(-10, 0, 1940, 1040),
    )
    monkeypatch.setattr(
        live_agent,
        "virtual_screen_region",
        lambda: Region(0, 0, 1920, 1040),
    )

    facts = live_agent._read_win32_target(HWND)

    assert facts is not None
    assert facts.region == (0, 0, 1920, 1040)


def test_foreground_reader_selects_only_enabled_owned_modal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import win32gui
    import win32process

    dialog = HWND + 1
    monkeypatch.setattr(win32gui, "GetForegroundWindow", lambda: HWND)
    monkeypatch.setattr(win32gui, "GetAncestor", lambda hwnd, _flag: hwnd)
    monkeypatch.setattr(
        win32gui,
        "IsWindowEnabled",
        lambda hwnd: hwnd == dialog,
    )
    monkeypatch.setattr(win32gui, "IsWindowVisible", lambda _hwnd: True)
    monkeypatch.setattr(win32gui, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(
        win32gui,
        "GetWindow",
        lambda hwnd, _flag: HWND if hwnd == dialog else 0,
    )
    monkeypatch.setattr(
        win32gui,
        "EnumWindows",
        lambda callback, extra: (
            callback(HWND, extra),
            callback(dialog, extra),
        ),
    )
    monkeypatch.setattr(
        win32process,
        "GetWindowThreadProcessId",
        lambda _hwnd: (1, PID),
    )

    assert live_agent._read_win32_foreground_root() == dialog


def test_hit_test_keeps_owned_top_level_surface_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import win32gui

    popup = HWND + 1
    monkeypatch.setattr(
        win32gui,
        "WindowFromPoint",
        lambda _point: popup,
    )
    monkeypatch.setattr(
        win32gui,
        "GetAncestor",
        lambda hwnd, flag: HWND if flag == 3 else hwnd,
    )
    monkeypatch.setattr(
        live_agent,
        "_read_win32_foreground_root",
        lambda: popup,
    )

    assert live_agent._hit_test_win32_root(300, 400) == popup


@pytest.mark.parametrize(
    ("popup_pid", "expected"),
    ((PID, HWND), (PID + 1, HWND + 1)),
)
def test_hit_test_maps_only_same_process_popup_to_foreground_surface(
    monkeypatch: pytest.MonkeyPatch,
    popup_pid: int,
    expected: int,
) -> None:
    import win32gui
    import win32process

    popup = HWND + 1
    monkeypatch.setattr(win32gui, "WindowFromPoint", lambda _point: popup)
    monkeypatch.setattr(win32gui, "GetAncestor", lambda hwnd, _flag: hwnd)
    monkeypatch.setattr(
        win32process,
        "GetWindowThreadProcessId",
        lambda hwnd: (1, popup_pid if hwnd == popup else PID),
    )
    monkeypatch.setattr(
        live_agent,
        "_read_win32_foreground_root",
        lambda: HWND,
    )

    assert live_agent._hit_test_win32_root(300, 400) == expected


def test_hit_test_keeps_unowned_same_process_sibling_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import win32gui

    sibling = HWND + 2
    monkeypatch.setattr(
        win32gui,
        "WindowFromPoint",
        lambda _point: sibling,
    )
    monkeypatch.setattr(
        win32gui,
        "GetAncestor",
        lambda hwnd, _flag: hwnd,
    )

    assert live_agent._hit_test_win32_root(300, 400) == sibling


def test_server_owned_graph_builder_emits_the_exact_live_contract() -> None:
    graph = live_agent.build_exact_live_vnext_graph(f"  {GOAL}  ")

    assert live_agent._validate_exact_graph(graph) == GOAL
    assert graph.name == GOAL
    assert graph.metadata == {}
    assert graph.variables == {}
    assert [node.kind for node in graph.nodes] == [
        ArpNodeKind.START,
        ArpNodeKind.AGENT_GOAL,
        ArpNodeKind.END,
    ]
    assert graph.nodes[1].config == {"goal": GOAL}
    assert graph.policy.allow_shell is False
    assert graph.policy.allow_desktop_mutation is True


def test_prepare_registers_only_exact_agent_goal_and_emits_no_live_trace() -> None:
    session = _FakeSession(
        [MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED)]
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        manager = ArpRunManager()
        execution = _prepare(
            _graph(),
            {},
            _config(),
            run_manager=manager,
            _dependencies=_dependencies(session=session),
        )
    finally:
        BUS.remove_sink(events.append)

    assert execution.handlers.keys() == {ArpNodeKind.AGENT_GOAL}
    assert execution.inputs == {}
    lease = execution.runtime_state["_desktop_lease"]
    assert execution.runtime_state == {
        "runtime_version": "vnext",
        "runtime_budget": {
            "max_turns": 30,
            "max_runtime_s": 300,
            "max_model_calls": 60,
        },
        "no_legacy_fallback": True,
        "experience_policy": "disabled",
        "thread_id": THREAD_ID,
        "turn_id": TURN_ID,
        "desktop_lease": lease.snapshot(),
        "_desktop_lease": lease,
    }
    assert lease.snapshot()["status"] == "idle"
    assert lease.snapshot()["run_id"] == ""
    assert not [event for event in events if event["type"].startswith("vnext_")]


def test_prepare_rejects_any_initial_desktop_binding() -> None:
    with pytest.raises(
        ValueError,
        match="must not pre-bind a desktop target",
    ):
        _prepare(
            _graph(),
            _inputs(),
            _config(),
            run_manager=ArpRunManager(),
            _dependencies=_dependencies(),
        )
def test_direct_reply_is_terminal_without_desktop_session_or_evidence(
    tmp_path: Path,
) -> None:
    reply = "The answer is available without using the desktop."
    clients: list[_RawClient] = []
    session_builds: list[Any] = []

    def client_factory(**_kwargs: Any) -> _RawClient:
        client = _RawClient(_direct_reply_response(reply))
        clients.append(client)
        return client

    def forbidden_session(build: Any) -> Any:
        session_builds.append(build)
        raise AssertionError("direct reply must not compose a desktop session")

    def forbidden_target(_hwnd: int) -> Any:
        raise AssertionError("direct reply must not inspect a desktop target")

    dependencies = live_agent._CompositionDependencies(
        wall_clock=lambda: NOW_MS / 1000.0,
        monotonic=lambda: 10.0,
        target_reader=forbidden_target,
        hit_test_root=lambda _x, _y: pytest.fail(
            "direct reply must not hit-test the desktop"
        ),
        client_factory=client_factory,
        session_builder=forbidden_session,
    )
    history = (
        {"role": "user", "content": "Remember the token amber."},
        {"role": "assistant", "content": "I will remember amber."},
    )
    manager = ArpRunManager()
    execution = _prepare(
        _graph(),
        {},
        _config(),
        run_manager=manager,
        history=history,
        _dependencies=dependencies,
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        snapshot = _run_prepared(manager, execution)
    finally:
        BUS.remove_sink(events.append)

    assert snapshot["status"] == ArpRunStatus.REPLY.value
    assert snapshot["result"] == {
        "runtime_version": "vnext",
        "runtime_budget": {
            "max_turns": 30,
            "max_runtime_s": 300,
            "max_model_calls": 60,
        },
        "no_legacy_fallback": True,
        "status": "reply",
        "turns_used": 1,
        "model_calls": 1,
    }
    assert snapshot["evidence"] == []
    assert execution.runtime_state["user_result"] == reply
    assert execution.runtime_state["_desktop_lease"].snapshot()["status"] == "idle"
    assert session_builds == []
    assert len(clients) == 1
    assert clients[0].closed is True
    assert len(clients[0].requests) == 1
    assert clients[0].requests[0]["messages"] == [
        {"role": "system", "content": live_agent.INITIAL_ACTOR_SYSTEM_CONTRACT},
        *history,
        {"role": "user", "content": GOAL},
    ]
    event_types = [event["type"] for event in events]
    assert not any(
        event_type.startswith("desktop_lease_")
        for event_type in event_types
    )
    assert "live_agent_turn" not in event_types
    assert "runtime_evidence_sealed" not in event_types
    done = [event for event in events if event["type"] == "arp_run_done"]
    assert len(done) == 1
    assert done[0]["status"] == ArpRunStatus.REPLY.value
    assert done[0]["user_result"] == reply
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("provider", "model_name", "payload", "expected_url"),
    (
        (
            "responses",
            "gpt-4o-mini",
            {
                "id": "resp-native",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "native reply"}
                        ],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
            "https://api.openai.com/v1/responses",
        ),
        (
            "anthropic",
            "claude-sonnet-4-20250514",
            {
                "id": "msg-native",
                "model": "claude-sonnet-4-20250514",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "native reply"}],
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
            "https://api.anthropic.com/v1/messages",
        ),
        (
            "gemini",
            "gemini-2.5-pro",
            {
                "responseId": "gem-native",
                "modelVersion": "gemini-2.5-pro",
                "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {
                                "role": "model",
                                "parts": [{"text": "native reply"}],
                            },
                        }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": 12,
                },
            },
            (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-2.5-pro:generateContent"
            ),
        ),
    ),
)
def test_native_provider_protocols_cross_deadline_boundary_and_report_usage(
    provider: str,
    model_name: str,
    payload: dict[str, Any],
    expected_url: str,
) -> None:
    config = _config()
    config["api"] = {
        **config["api"],
        "provider": provider,
        "protocol": provider,
        "base_url": "",
        "model": model_name,
    }
    budget = live_agent._freeze_budget(config)
    model = live_agent._freeze_model(config, budget)
    transport = _NativeTransport(payload)
    dependencies = live_agent._CompositionDependencies(
        monotonic=lambda: 10.0,
        client_factory=lambda **_kwargs: pytest.fail(
            "native protocols must not construct the OpenAI SDK client"
        ),
        provider_transport_factory=lambda: transport,
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    bundle = None
    try:
        bundle = live_agent._build_actor_resources(
            run_id=f"run-native-{provider}",
            mission_id=f"mission-native-{provider}",
            budget=budget,
            model=model,
            dependencies=dependencies,
            deadline=lambda: 200.0,
        )
        admission = bundle.actor.begin_turn(
            "Reply directly.",
            stop_event=threading.Event(),
        )
    finally:
        if bundle is not None:
            bundle.close()
        BUS.remove_sink(events.append)

    assert admission.kind == "reply"
    assert admission.reply == "native reply"
    assert transport.closed is True
    assert len(transport.calls) == 1
    assert transport.calls[0]["url"] == expected_url
    assert transport.calls[0]["timeout"] == 75.0
    assert bundle is not None
    assert bundle.usage_total["prompt_tokens"] == 10
    assert bundle.usage_total["completion_tokens"] == 2
    usage_events = [event for event in events if event["type"] == "agent_usage"]
    assert len(usage_events) == 1
    assert usage_events[0]["total"]["prompt_tokens"] == 10
    assert usage_events[0]["model_role"] == "vnext_visual_actor"


def test_model_tool_shape_diagnostics_keep_types_but_not_argument_content() -> None:
    response = {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "function": {
                        "name": "keyboard",
                        "arguments": json.dumps({
                            "action": "type",
                            "text": "PRIVATE-TEXT",
                            "task_state_update": {
                                "scratchpad": "PRIVATE-STATE",
                            },
                        }),
                    },
                }],
            },
        }],
    }

    shapes = live_agent._model_tool_shapes(response)

    assert shapes == [{
        "name": "keyboard",
        "discriminator": "type",
        "argument_types": {
            "action": "string",
            "task_state_update": "object",
            "text": "string",
        },
    }]
    assert "PRIVATE" not in json.dumps(shapes)


def test_model_response_shape_records_truncation_without_content() -> None:
    response = {
        "choices": [{
            "finish_reason": "length",
            "message": {
                "content": "PRIVATE-CONTENT",
                "tool_calls": [],
            },
        }],
    }

    shape = live_agent._model_response_shape(response)

    assert shape == {
        "choice_count": 1,
        "finish_reason": "length",
        "content_type": "str",
        "content_chars": 15,
        "tool_call_count": 0,
    }
    assert "PRIVATE" not in json.dumps(shape)


def test_vnext_usage_and_cache_tokens_reach_runtime_and_terminal_event(
    tmp_path: Path,
) -> None:
    response = _direct_reply_response("usage reply")
    response["usage"] = {
        "prompt_tokens": 100,
        "completion_tokens": 5,
        "prompt_tokens_details": {
            "cached_tokens": 60,
            "cache_write_tokens": 20,
        },
    }
    client = _RawClient(response)
    manager = ArpRunManager()
    execution = _prepare(
        _graph(),
        {},
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(
            client_factory=lambda **_kwargs: client,
        ),
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        snapshot = _run_prepared(manager, execution)
    finally:
        BUS.remove_sink(events.append)

    expected = {
        "prompt_tokens": 100,
        "completion_tokens": 5,
        "cache_hit_tokens": 60,
        "cache_miss_tokens": 20,
        "cache_creation_tokens": 20,
        "reasoning_tokens": 0,
        "cost_cny": 0.0,
        "cost_estimated": False,
        "cost_currency": "CNY",
        "provider": "openai",
        "model": "gpt-4o-mini",
    }
    assert snapshot["status"] == ArpRunStatus.REPLY.value
    assert snapshot["result"]["model_usage"] == expected
    assert execution.runtime_state["model_usage"] == expected
    usage = [event for event in events if event["type"] == "agent_usage"]
    assert len(usage) == 1
    assert usage[0]["total"] == expected
    done = [event for event in events if event["type"] == "live_agent_done"]
    assert len(done) == 1
    assert done[0]["model_usage"] == expected


def test_cancel_before_initial_actor_turn_closes_model_without_desktop_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw_constructions: list[bool] = []
    client_calls: list[dict[str, Any]] = []
    clients: list[_RawClient] = []

    def forbidden_raw(*_args: Any, **_kwargs: Any) -> Any:
        raw_constructions.append(True)
        raise AssertionError("RawDevice must not be constructed")

    def client_factory(**kwargs: Any) -> Any:
        client_calls.append(kwargs)
        client = _RawClient(_observe_desktop_response())
        clients.append(client)
        return client

    class CancelBeforeComposition:
        paused = True

        @staticmethod
        def wait_if_paused(stop_event: threading.Event) -> bool:
            stop_event.set()
            return False

    monkeypatch.setattr(live_agent, "RawDeviceExecutor", forbidden_raw)
    graph = _graph()
    execution = _prepare(
        graph,
        {},
        _config(),
        run_manager=ArpRunManager(),
        _dependencies=_dependencies(
            client_factory=client_factory,
        ),
    )
    run_id = "arp-test-pre-composition-cancel"
    stop_event = threading.Event()
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        result = execution.handlers[ArpNodeKind.AGENT_GOAL](
            next(node for node in graph.nodes if node.id == "full-goal"),
                {
                    "run_id": run_id,
                    "pause_controller": CancelBeforeComposition(),
                    "runtime_state": {},
                },
                stop_event,
        )
    finally:
        BUS.remove_sink(events.append)

    assert result.ok is False
    assert result.branch == "canceled"
    assert result.output is not None
    assert result.output["failure_stage"] == "canceled"
    assert "canceled before the initial Actor turn" in result.error
    assert raw_constructions == []
    assert len(client_calls) == len(clients) == 1
    assert clients[0].closed is True
    assert clients[0].requests == []
    sealed = [
        event
        for event in events
        if event["type"] == "runtime_evidence_sealed"
    ]
    assert sealed == []
    assert [
        event["type"]
        for event in events
        if event["type"]
        in {
            "live_agent_start",
            "runtime_evidence_sealed",
            "live_agent_done",
        }
    ] == [
        "live_agent_start",
        "live_agent_done",
    ]


@pytest.mark.parametrize(
    "mutate",
    (
        lambda raw: raw["nodes"].append(deepcopy(raw["nodes"][2])),
        lambda raw: raw["nodes"][1]["config"].update({"task": "hidden"}),
        lambda raw: raw["nodes"][1].update({"timeout_s": 300}),
        lambda raw: raw["policy"].update({"max_node_visits": 2}),
        lambda raw: raw["metadata"].update({"task_contract": {}}),
        lambda raw: raw.update({"id": "vnext-live-agent-wrong"}),
    ),
)
def test_exact_graph_rejects_every_expansion(mutate: Any) -> None:
    raw = _graph_dict()
    mutate(raw)
    graph = ArpGraph.model_validate(raw)

    with pytest.raises(ValueError, match="vNext|AGENT_GOAL"):
        _prepare(
            graph,
            {},
            _config(),
            run_manager=ArpRunManager(),
            _dependencies=_dependencies(
                session=_FakeSession(
                    [MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED)]
                )
            ),
        )


@pytest.mark.parametrize(
    ("change", "match"),
    (
        (
            lambda inputs: inputs.update({"runtime_version": "vnext"}),
            "inputs fields mismatch",
        ),
        (
            lambda inputs: inputs["execution_context"].update(
                {"entrypoint": "web_workbench"}
            ),
            "native Electron",
        ),
        (
            lambda inputs: (
                inputs["execution_context"].update(
                    {"submitted_at": NOW_MS - 62_000}
                ),
                inputs["execution_context"]["desktop_handoff"].update(
                    {"prepared_at": NOW_MS - 61_500}
                ),
                inputs["execution_context"]["target_window"].update(
                    {"captured_at": NOW_MS - 61_000}
                ),
            ),
            "stale",
        ),
        (
            lambda inputs: inputs["execution_context"]["desktop_handoff"].update(
                {"yielded": False}
            ),
            "handoff",
        ),
    ),
)
def test_desktop_lease_target_context_is_exact_and_fresh(
    change: Any,
    match: str,
) -> None:
    inputs = _inputs()
    change(inputs)

    with pytest.raises(ValueError, match=match):
        live_agent._freeze_prebound_target(
            _graph(),
            inputs,
            dependencies=_dependencies(),
        )


@pytest.mark.parametrize(
    ("current", "mismatch"),
    (
        (_facts(process_id=PID + 1), "process_id"),
        (_facts(region=(101, 200, 640, 480)), "region"),
    ),
)
def test_current_target_identity_mismatch_fails_before_session(
    current: Any,
    mismatch: str,
) -> None:
    built = False

    def builder(_build: Any) -> _FakeSession:
        nonlocal built
        built = True
        return _FakeSession(
            [MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED)]
        )

    deps = live_agent._CompositionDependencies(
        wall_clock=lambda: NOW_MS / 1000.0,
        monotonic=lambda: 10.0,
        target_reader=lambda _hwnd: current,
        hit_test_root=lambda _x, _y: HWND,
        session_builder=builder,
    )

    with pytest.raises(
        ValueError,
        match=rf"current Electron target mismatch: {mismatch}",
    ):
        live_agent._freeze_prebound_target(
            _graph(),
            _inputs(),
            dependencies=deps,
        )
    assert built is False


def test_prebound_target_ignores_dynamic_title_and_foreground_changes() -> None:
    current = _facts(
        title="Canary Target — updated",
        foreground_root_hwnd=HWND + 1,
    )

    target = live_agent._freeze_prebound_target(
        _graph(),
        _inputs(),
        dependencies=_dependencies(reader=lambda _hwnd: current),
    )

    assert target.hwnd == HWND
    assert target.process_id == PID
    assert target.title == "Canary Target"
    assert target.probe.snapshot(HWND) is not None


def test_prebound_target_normalizes_client_rect_to_visible_desktop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs()
    inputs["execution_context"]["target_window"]["rect"] = [
        -10,
        0,
        1930,
        1040,
    ]
    monkeypatch.setattr(
        live_agent,
        "virtual_screen_region",
        lambda: Region(0, 0, 1920, 1040),
    )

    target = live_agent._freeze_prebound_target(
        _graph(),
        inputs,
        dependencies=_dependencies(
            reader=lambda _hwnd: _facts(region=(0, 0, 1920, 1040)),
        ),
    )

    assert target.region == (0, 0, 1920, 1040)


def test_desktop_shell_binding_relaxes_foreground_and_keeps_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs()
    target_window = inputs["execution_context"]["target_window"]
    target_window["binding_mode"] = "desktop_shell"
    target_window["foreground_verified"] = False
    target_window["title"] = "Program Manager"
    target_window["class_name"] = "Progman"
    target_window["hwnd"] = 65878
    target_window["process_id"] = 19076
    target_window["rect"] = [0, 0, 1536, 864]
    monkeypatch.setattr(
        live_agent,
        "virtual_screen_region",
        lambda: Region(0, 0, 1536, 864),
    )
    shell_facts = live_agent._TargetFacts(
        hwnd=65878,
        root_hwnd=65878,
        process_id=19076,
        executable_identity=r"c:\windows\explorer.exe",
        title="Program Manager",
        class_name="Progman",
        region=(0, 0, 1536, 864),
        foreground_root_hwnd=None,
    )

    target = live_agent._freeze_prebound_target(
        _graph(),
        inputs,
        dependencies=_dependencies(
            reader=lambda _hwnd: shell_facts,
        ),
    )

    assert target.desktop_shell is True
    assert target.binding_id.startswith("vnext-target-")
    assert target.trace_identity()["binding_mode"] == "desktop_shell"
    assert target.probe.snapshot(65878) is not None


def test_desktop_shell_binding_rejects_when_shell_identity_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs()
    target_window = inputs["execution_context"]["target_window"]
    target_window["binding_mode"] = "desktop_shell"
    target_window["foreground_verified"] = False
    target_window["title"] = "Program Manager"
    target_window["class_name"] = "Progman"
    target_window["hwnd"] = 65878
    target_window["process_id"] = 19076
    target_window["rect"] = [0, 0, 1536, 864]
    monkeypatch.setattr(
        live_agent,
        "virtual_screen_region",
        lambda: Region(0, 0, 1536, 864),
    )
    changed = live_agent._TargetFacts(
        hwnd=65878,
        root_hwnd=65878,
        process_id=9999,
        executable_identity=r"c:\windows\explorer.exe",
        title="Program Manager",
        class_name="Progman",
        region=(0, 0, 1536, 864),
        foreground_root_hwnd=None,
    )

    with pytest.raises(ValueError, match="process_id"):
        live_agent._freeze_prebound_target(
            _graph(),
            inputs,
            dependencies=_dependencies(
                reader=lambda _hwnd: changed,
            ),
        )


def test_tool_catalog_is_exact_and_keeps_hotkey_order_as_downstream_gate() -> None:
    catalog = live_agent._tool_catalog()
    schemas = {
        tool.name: tool.schema
        for tool in catalog.tools
    }

    assert (
        schemas["type_text"]["properties"]["text"]["maxLength"]
        == MAX_TYPE_TEXT_CODEPOINTS
        == 4096
    )
    hotkey = schemas["hotkey"]["properties"]["keys"]
    assert set(hotkey["items"]["enum"]) == set(CANONICAL_HOTKEY_KEYS)
    assert hotkey["maxItems"] == len(CANONICAL_HOTKEY_MODIFIERS) + 1 == 5
    assert hotkey["uniqueItems"] is True
    drag = schemas["drag"]
    assert tuple(drag["required"]) == (
        "image_x1",
        "image_y1",
        "image_x2",
        "image_y2",
    )
    assert drag["properties"]["image_points"]["maxItems"] == 32
    assert drag["properties"]["image_points"]["items"]["minItems"] == 2
    assert drag["properties"]["image_points"]["items"]["maxItems"] == 2
    assert tuple(drag["properties"]["image_points"]["default"]) == ()
    assert drag["properties"]["keep_holding"] == {
        "type": "boolean", "default": False,
    }
    validator = ToolArgumentValidator(catalog)
    straight = {
        "image_x1": 10,
        "image_y1": 20,
        "image_x2": 300,
        "image_y2": 200,
    }
    assert validator.validate("drag", straight) == straight
    curved = {
        **straight,
        "image_points": [[80, 40], [160, 120]],
        "keep_holding": False,
    }
    assert validator.validate("drag", curved) == curved
    with pytest.raises(ToolArgumentValidationError):
        validator.validate(
            "drag",
            {**straight, "image_points": [[1, 2, 3]]},
        )
    with pytest.raises(ToolArgumentValidationError):
        validator.validate(
            "drag",
            {
                **straight,
                "image_points": [[index, index] for index in range(33)],
            },
        )
    blocker = schemas["blocked"]["properties"]["blocker_kind"]
    assert blocker["maxLength"] == 64
    assert blocker["pattern"] == r"^[a-z][a-z0-9_]{0,63}$"
    assert (
        schemas["blocked"]["properties"]["observed_facts"]["items"][
            "pattern"
        ]
        == r"\S"
    )
    assert (
        schemas["claim_obligation_done"]["properties"]["concise_claim"][
            "pattern"
        ]
        == r"\S"
    )
    media = {
        tool.name: tool.execution_medium
        for tool in catalog.tools
    }
    assert {
        media[name]
        for name in live_agent._APPROVED_ACTIONS
    } == {ExecutionMedium.GUI}
    assert media["claim_obligation_done"] is ExecutionMedium.CONTROL
    assert media["blocked"] is ExecutionMedium.CONTROL

    with pytest.raises(ValueError, match="ordered modifiers"):
        HotkeyChord(("shift", "ctrl", "a"))
    validator = ToolArgumentValidator(catalog)
    with pytest.raises(ToolArgumentValidationError):
        validator.validate(
            "claim_obligation_done",
            {"concise_claim": " \t "},
        )
    with pytest.raises(ToolArgumentValidationError):
        validator.validate(
            "blocked",
            {
                "blocker_kind": "visible_blocker",
                "observed_facts": [" \n "],
            },
        )
    with pytest.raises(InvalidActorProposal):
        parse_actor_proposal(
            {
                "format": "actor-proposal/0",
                "kind": "desktop_action",
                "action": "click",
                "args": {"image_x": 1, "image_y": 1},
                "local_intent": "   ",
            }
        )


def test_mission_hotkey_summaries_match_the_physical_chord_contract() -> None:
    primary_keys = sorted(
        CANONICAL_HOTKEY_KEYS.difference(CANONICAL_HOTKEY_MODIFIERS)
    )
    # Every physical hotkey chord projects to the single bare summary
    # "hotkey"; per-chord detail lives in the structured previous-action view,
    # not in the completed-action history.
    for modifier in CANONICAL_HOTKEY_MODIFIERS:
        assert HotkeyChord((modifier,)).keys == (modifier,)
        assert is_safe_action_summary("hotkey", "hotkey")
    for modifier_count in range(len(CANONICAL_HOTKEY_MODIFIERS) + 1):
        for modifiers in permutations(
            CANONICAL_HOTKEY_MODIFIERS,
            modifier_count,
        ):
            for primary in primary_keys:
                keys = (*modifiers, primary)
                # The bare "hotkey" summary is accepted for every chord that
                # is physically representable; per-chord detail is not part
                # of the summary anymore.
                try:
                    HotkeyChord(keys)
                except ValueError:
                    continue
                assert is_safe_action_summary("hotkey", "hotkey"), keys

    # Non-canonical summaries are still rejected by the privacy gate.
    for summary in (
        "hotkey_",
        "hotkey_ctrl_ctrl_a",
        "hotkey_a_ctrl",
        "hotkey_secret_token",
    ):
        assert not is_safe_action_summary("hotkey", summary)


def test_exact_capture_rejects_clamping_and_decoded_dimension_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int, Region]] = []
    returned = [_jpeg(1120, 280), _jpeg(1119, 280)]
    monkeypatch.setattr(
        live_agent,
        "virtual_screen_region",
        lambda: Region(0, 0, 3000, 1600),
    )

    def capture(
        hwnd: int,
        *,
        max_side: int,
        region: Region,
    ) -> str:
        calls.append((hwnd, max_side, region))
        return returned.pop(0)

    monkeypatch.setattr(
        live_agent,
        "capture_window_jpeg_b64",
        capture,
    )
    visible = Region(100, 120, 2240, 560)
    good = live_agent._capture_exact_target_jpeg(HWND, region=visible)
    assert good
    assert live_agent._jpeg_dimensions(good) == (1120, 280)
    assert calls == [(HWND, 1120, visible)]

    assert live_agent._capture_exact_target_jpeg(HWND, region=visible) == ""
    assert calls[-1] == (HWND, 1120, visible)
    assert (
        live_agent._capture_exact_target_jpeg(
            HWND,
            region=Region(-1, 120, 640, 480),
        )
        == ""
    )
    assert len(calls) == 2


def test_handler_runs_bounded_redecisions_to_verified_evidence_and_closes() -> None:
    session = _FakeSession(
        [
            MissionRuntimeResult(
                RuntimeStatus.ACTION_RECORDED,
                accepted_event_ids=("event-action",),
            ),
            MissionRuntimeResult(RuntimeStatus.STALE_DISCARDED),
            MissionRuntimeResult(
                RuntimeStatus.VERIFICATION_RECORDED,
                accepted_event_ids=("event-verification-recorded",),
            ),
            MissionRuntimeResult(
                RuntimeStatus.MISSION_VERIFIED,
                accepted_event_ids=("event-verified",),
            ),
        ]
    )
    events: list[dict[str, Any]] = []
    manager = ArpRunManager()
    execution = _prepare(
        _graph(),
        {},
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(
            session=session,
            client_factory=lambda **_kwargs: _RawClient(
                _observe_desktop_response(
                    "好的，我先查看当前桌面，再继续完成任务。"
                )
            ),
        ),
    )
    BUS.add_sink(events.append)
    try:
        snapshot = _run_prepared(manager, execution)
    finally:
        BUS.remove_sink(events.append)

    assert snapshot["status"] == ArpRunStatus.DONE.value
    assert snapshot["runtime_version"] == "vnext"
    assert snapshot["result"]["status"] == "mission_verified"
    assert snapshot["result"]["turns_used"] == 4
    assert snapshot["result"]["model_calls"] == 5
    assert snapshot["result"]["no_legacy_fallback"] is True
    assert session.calls == 4
    assert session.model_calls == 5
    assert session.closed is True
    assert len(snapshot["evidence"]) == 1
    assert snapshot["evidence"][0]["verified"] is True
    starts = [item for item in events if item["type"] == "live_agent_start"]
    done = [item for item in events if item["type"] == "live_agent_done"]
    turns = [item for item in events if item["type"] == "live_agent_turn"]
    messages = [
        item for item in events if item["type"] == "live_agent_message"
    ]
    activities = [
        item for item in events if item["type"] == "live_agent_activity"
    ]
    assert len(starts) == len(done) == 1
    assert [item["narration"] for item in messages] == [
        "好的，我先查看当前桌面，再继续完成任务。"
    ]
    assert messages[0]["phase"] == "starting"
    assert [item["kind"] for item in activities[:2]] == [
        "message",
        "tool_start",
    ]
    assert activities[0]["content"] == (
        "好的，我先查看当前桌面，再继续完成任务。"
    )
    assert activities[1]["tool"] == "observe_desktop"
    assert len(turns) == 4
    assert turns[-2]["status"] == "verification_recorded"
    assert starts[0]["runtime_budget"] == {
        "max_turns": 30,
        "max_runtime_s": 300,
        "max_model_calls": 60,
    }
    assert starts[0]["no_legacy_fallback"] is True
    assert done[0]["status"] == "mission_verified"
    event_types = [item["type"] for item in events]
    assert event_types.index("vnext_model_call") < event_types.index(
        "live_agent_activity"
    )
    assert event_types.index("live_agent_activity") < event_types.index(
        "live_agent_message"
    )
    assert event_types.index("live_agent_message") < event_types.index(
        "desktop_lease_requested"
    )
    assert event_types.index("desktop_lease_requested") < event_types.index(
        "desktop_lease_bound"
    )
    assert event_types.index("desktop_lease_bound") < event_types.index(
        "live_agent_turn"
    )


def test_handler_recovers_from_one_invalid_proposal_without_prompt_repair() -> None:
    private_detail = "PRIVATE INVALID MODEL RESPONSE"
    session = _FakeSession(
        [
            MissionRuntimeResult(
                RuntimeStatus.INVALID_PROPOSAL,
                detail=private_detail,
            ),
            MissionRuntimeResult(
                RuntimeStatus.MISSION_VERIFIED,
                accepted_event_ids=("event-verified",),
            ),
        ]
    )
    events: list[dict[str, Any]] = []
    manager = ArpRunManager()
    execution = _prepare(
        _graph(),
        {},
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(session=session),
    )
    BUS.add_sink(events.append)
    try:
        snapshot = _run_prepared(manager, execution)
    finally:
        BUS.remove_sink(events.append)

    assert snapshot["status"] == ArpRunStatus.DONE.value
    assert snapshot["result"]["status"] == "mission_verified"
    assert snapshot["result"]["turns_used"] == 2
    assert session.calls == 2
    turns = [
        item for item in events if item["type"] == "live_agent_turn"
    ]
    assert turns[0]["status"] == "invalid_proposal"
    assert turns[0]["recovery"] == {
        "kind": "bounded_invalid_proposal_redecision",
        "available": True,
    }
    assert turns[1]["recovery"] is None
    assert private_detail not in json.dumps(
        {"snapshot": snapshot, "events": events},
        ensure_ascii=False,
    )


def test_handler_keeps_redeciding_after_invalid_proposals_until_budget() -> None:
    session = _FakeSession(
        [
            MissionRuntimeResult(RuntimeStatus.INVALID_PROPOSAL),
            MissionRuntimeResult(RuntimeStatus.INVALID_PROPOSAL),
            MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED),
        ]
    )
    events: list[dict[str, Any]] = []
    manager = ArpRunManager()
    execution = _prepare(
        _graph(),
        {},
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(session=session),
    )
    BUS.add_sink(events.append)
    try:
        snapshot = _run_prepared(manager, execution)
    finally:
        BUS.remove_sink(events.append)

    assert snapshot["status"] == ArpRunStatus.DONE.value
    assert snapshot["result"]["status"] == "mission_verified"
    assert snapshot["result"]["turns_used"] == 3
    assert session.calls == 3
    turns = [
        item for item in events if item["type"] == "live_agent_turn"
    ]
    assert [item["recovery"]["available"] for item in turns[:2]] == [
        True,
        True,
    ]
    assert turns[-1]["recovery"] is None


def test_invalid_proposal_rejection_detail_is_fed_to_the_next_turn() -> None:
    session = _FakeSession(
        [
            MissionRuntimeResult(
                RuntimeStatus.INVALID_PROPOSAL,
                detail=(
                    "tool arguments rejected: tool=hotkey code=schema_mismatch "
                    "keyword=enum path=('keys', 0)"
                ),
            ),
            MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED),
        ]
    )
    manager = ArpRunManager()
    execution = _prepare(
        _graph(),
        {},
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(session=session),
    )

    snapshot = _run_prepared(manager, execution)

    assert snapshot["result"]["status"] == "mission_verified"
    assert session.calls == 2
    assert session.rejection_feedbacks == [
        None,
        "tool arguments rejected: tool=hotkey code=schema_mismatch keyword=enum path=('keys', 0)",
    ]


def test_successful_turn_resets_invalid_proposal_recovery_streak() -> None:
    session = _FakeSession(
        [
            MissionRuntimeResult(RuntimeStatus.INVALID_PROPOSAL),
            MissionRuntimeResult(RuntimeStatus.ACTION_RECORDED),
            MissionRuntimeResult(RuntimeStatus.INVALID_PROPOSAL),
            MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED),
        ]
    )
    manager = ArpRunManager()
    execution = _prepare(
        _graph(),
        {},
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(session=session),
    )

    snapshot = _run_prepared(manager, execution)

    assert snapshot["status"] == ArpRunStatus.DONE.value
    assert snapshot["result"]["turns_used"] == 4
    assert session.calls == 4


def test_handler_redecides_once_after_actor_transport_error() -> None:
    private_detail = "HTTP 400 PRIVATE PROVIDER BODY"
    session = _FakeSession(
        [
            MissionRuntimeResult(
                RuntimeStatus.ACTOR_ERROR,
                detail=private_detail,
            ),
            MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED),
        ]
    )
    events: list[dict[str, Any]] = []
    manager = ArpRunManager()
    execution = _prepare(
        _graph(),
        {},
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(session=session),
    )
    BUS.add_sink(events.append)
    try:
        snapshot = _run_prepared(manager, execution)
    finally:
        BUS.remove_sink(events.append)

    assert snapshot["status"] == ArpRunStatus.DONE.value
    assert snapshot["result"]["turns_used"] == 2
    assert session.calls == 2
    turns = [
        item for item in events if item["type"] == "live_agent_turn"
    ]
    assert turns[0]["status"] == "actor_error"
    assert turns[0]["recovery"] == {
        "kind": "bounded_actor_error_redecision",
        "available": True,
    }
    assert session.rejection_feedbacks == [
        None,
        (
            "The previous model request timed out or failed. Re-observe the "
            "current screen and state, account for any completed tool work, "
            "and choose the shortest useful next step."
        ),
    ]
    assert private_detail not in session.rejection_feedbacks[1]
    assert private_detail not in json.dumps(
        {"snapshot": snapshot, "events": events},
        ensure_ascii=False,
    )


def test_handler_keeps_redeciding_after_actor_errors_until_budget() -> None:
    session = _FakeSession(
        [
            MissionRuntimeResult(RuntimeStatus.ACTOR_ERROR),
            MissionRuntimeResult(RuntimeStatus.ACTOR_ERROR),
            MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED),
        ]
    )
    events: list[dict[str, Any]] = []
    manager = ArpRunManager()
    execution = _prepare(
        _graph(),
        {},
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(session=session),
    )
    BUS.add_sink(events.append)
    try:
        snapshot = _run_prepared(manager, execution)
    finally:
        BUS.remove_sink(events.append)

    assert snapshot["status"] == ArpRunStatus.DONE.value
    assert snapshot["result"]["status"] == "mission_verified"
    assert snapshot["result"]["turns_used"] == 3
    assert session.calls == 3
    turns = [
        item for item in events if item["type"] == "live_agent_turn"
    ]
    assert [item["recovery"]["available"] for item in turns[:2]] == [
        True,
        True,
    ]
    assert turns[-1]["recovery"] is None


def test_handler_stops_on_blocked_without_retry_and_closes() -> None:
    session = _FakeSession(
        [
            MissionRuntimeResult(
                RuntimeStatus.BLOCKED,
                detail="the requested state could not be established",
            ),
            MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED),
        ]
    )
    manager = ArpRunManager()
    execution = _prepare(
        _graph(),
        {},
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(session=session),
    )

    snapshot = _run_prepared(manager, execution)

    assert snapshot["status"] == ArpRunStatus.FAILED.value
    assert session.calls == 1
    assert session.closed is True
    # The real per-turn failure reason is preserved instead of being replaced
    # by a generic status string.
    assert snapshot["error"] == "the requested state could not be established"
    assert snapshot["evidence"] == []


class _SnapshotErrorSession(_FakeSession):
    def completion_snapshot(self) -> dict[str, Any]:
        raise RuntimeError("snapshot unavailable")


def test_completion_snapshot_error_still_closes_and_fails_closed() -> None:
    session = _SnapshotErrorSession(
        [MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED)]
    )
    manager = ArpRunManager()
    execution = _prepare(
        _graph(),
        {},
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(session=session),
    )

    snapshot = _run_prepared(manager, execution)

    assert snapshot["status"] == ArpRunStatus.FAILED.value
    assert "completion snapshot failed closed" in snapshot["error"]
    assert session.closed is True
    assert snapshot["evidence"] == []


def test_verified_result_survives_unavailable_diagnostic_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class VerifiedSessionWithoutDiagnosticAnchor(_FakeSession):
        def completion_snapshot(self) -> dict[str, Any]:
            snapshot = super().completion_snapshot()
            snapshot["result"] = "the requested state is visibly present"
            return snapshot

        def close(self) -> None:
            self.closed = True
            raise RuntimeError("diagnostic cleanup unavailable")

    session = VerifiedSessionWithoutDiagnosticAnchor(
        [MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED)]
    )

    def build(build_spec: Any) -> _FakeSession:
        session.mission_id = build_spec.mission_id
        session.run_id = build_spec.run_id
        session.target_binding_id = build_spec.target.binding_id
        session.frame_epoch = build_spec.target.captured_at_ms
        return session

    monkeypatch.setattr(live_agent, "_build_live_session", build)
    manager = ArpRunManager()
    execution = _prepare(
        _graph(),
        {},
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(),
    )

    snapshot = _run_prepared(manager, execution)

    assert snapshot["status"] == ArpRunStatus.DONE.value
    assert snapshot["result"]["status"] == RuntimeStatus.MISSION_VERIFIED.value
    assert snapshot["result"]["runtime_evidence"]["available"] is False
    assert session.closed is True


def test_product_failure_detail_is_typed_status_only() -> None:
    assert live_agent._public_runtime_detail(
        RuntimeStatus.STATE_REJECTED
    ) == (
        "vNext Runtime returned state_rejected"
    )


def test_public_turn_projection_omits_private_failure_feedback() -> None:
    sentinel = "PRIVATE-SCREEN-SENTINEL"
    target = live_agent._freeze_prebound_target(
        _graph(),
        _inputs(),
        dependencies=_dependencies(),
    )

    projected = live_agent._public_turn_snapshot(
        {
            "mission_revision": 3,
            "mission_status": MissionStatus.RUNNING.value,
            "target_binding_id": target.binding_id,
            "target_hwnd": target.hwnd,
            "frame_id": 2,
            "frame_epoch": target.captured_at_ms,
            "frame_evidence_refs": [],
            "action_effect": None,
            "failure_message": sentinel,
            "failure_summary": {"message": sentinel},
        },
        target=target,
    )

    assert sentinel not in json.dumps(projected, sort_keys=True)
    assert "failure_message" not in projected
    assert "failure_summary" not in projected


def test_completion_projection_follows_current_workspace_target() -> None:
    target = live_agent._freeze_prebound_target(
        _graph(),
        _inputs(),
        dependencies=_dependencies(),
    )
    successor_hwnd = HWND + 1
    successor_epoch = target.captured_at_ms + 1
    source = {
        "mission_id": "mission-current-target",
        "run_id": "run-current-target",
        "mission_status": MissionStatus.VERIFIED.value,
        "mission_verification_id": "mission-verification-current",
        "obligation_verification_id": "obligation-verification-current",
        "verified_evidence_refs": ["e-current"],
        "verified_evidence_bindings": [
            {
                "id": "e-current",
                "mission_id": "mission-current-target",
                "run_id": "run-current-target",
                "obligation_id": "full-goal",
                "frame_id": 9,
            }
        ],
        "target_binding_id": "vnext-workspace-successor",
        "target_hwnd": successor_hwnd,
        "target_epoch": successor_epoch,
        "frame_id": 9,
        "frame_hwnd": successor_hwnd,
        "frame_epoch": successor_epoch,
    }

    assert (
        live_agent._completion_binding_error(
            source,
            mission_id="mission-current-target",
            run_id="run-current-target",
        )
        is None
    )
    projected = live_agent._public_completion_snapshot(
        source,
        mission_id="mission-current-target",
        run_id="run-current-target",
        target=target,
    )

    assert projected["target_binding_id"] == "vnext-workspace-successor"
    assert projected["target_hwnd"] == successor_hwnd


def test_model_transport_payload_is_absent_from_bus_and_node_result() -> None:
    sentinel = "EXACT_TYPE_TEXT_PROMPT_OR_MODEL_RESPONSE"
    session = _FakeSession(
        [
            MissionRuntimeResult(
                RuntimeStatus.STATE_REJECTED,
                detail=(
                    "model transport failed after 1 attempt(s): "
                    f"{sentinel}"
                ),
            )
        ]
    )
    manager = ArpRunManager()
    execution = _prepare(
        _graph(),
        {},
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(session=session),
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        snapshot = _run_prepared(manager, execution)
    finally:
        BUS.remove_sink(events.append)

    public_wire = json.dumps(
        {"snapshot": snapshot, "events": events},
        sort_keys=True,
    )
    assert sentinel not in public_wire
    # The typed transport prefix survives, but the provider payload tail is
    # stripped at the public boundary.
    assert snapshot["error"] == (
        "model transport failed after 1 attempt(s):"
    )
    done = [
        event
        for event in events
        if event["type"] == "live_agent_done"
    ]
    assert len(done) == 1
    # The typed transport prefix survives the BUS boundary too; the sensitive
    # provider payload tail does not.
    assert done[0]["detail"] == "model transport failed after 1 attempt(s):"




def test_raw_receipt_trace_projects_fixed_safe_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from winpilot.recorder import trace

    run_id = "run-private-receipt"
    trace_root = tmp_path / "public-traces"
    monkeypatch.setattr(trace, "TRACES_DIR", trace_root)
    recorder = trace.TraceRecorder()
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        recorder.start(run_id)
        live_agent._RawReceiptTrace().record(
            RawActionReceipt(
                action="scroll",
                outcome=MutationOutcome.APPLIED,
                backend_result=RawBackendResult(
                    input_completed=True,
                    facts={
                        "exact_text": PRIVATE_TEXT,
                        "hotkey_chord": PRIVATE_CHORD,
                        "x": PRIVATE_X,
                        "y": PRIVATE_Y,
                        "amount": PRIVATE_AMOUNT,
                    },
                ),
            ),
            metadata={
                "run_id": run_id,
                "mission_id": "mission-private-receipt",
                "decision_id": "decision-private-receipt",
                "controller_action": "scroll",
                "target_hwnd": HWND,
                "target_binding_id": "binding-private-receipt",
                "frame_id": 5,
                "frame_epoch": 4,
            },
        )
        recorder.stop()
    finally:
        recorder.stop()
        BUS.remove_sink(events.append)
        BUS.remove_sink(recorder._on_event)

    event = next(
        item for item in events if item["type"] == "vnext_raw_receipt"
    )
    assert set(event) == {
        "type",
        "ts",
        "runtime_version",
        "no_legacy_fallback",
        "run_id",
        "mission_id",
        "decision_id",
        "controller_action",
        "raw_action",
        "outcome",
        "target_hwnd",
        "target_binding_id",
        "frame_id",
        "frame_epoch",
        "input_completed",
        "error_kind",
        "parameter_visibility",
    }
    assert event["input_completed"] is True
    assert event["error_kind"] is None
    assert (
        event["parameter_visibility"]
        == "private_runtime_evidence"
    )
    records = [
        json.loads(line)
        for line in (
            trace_root / f"{run_id}.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    _assert_private_runtime_evidence_absent(
        {"bus": event, "trace": records}
    )


@pytest.mark.parametrize(
    "scope",
    (
        {},
        {"mission_id": "mission-private"},
        {"run_id": "run-private"},
        {
            "mission_id": "other-mission",
            "run_id": "run-private",
        },
        {
            "mission_id": "mission-private",
            "run_id": "other-run",
        },
    ),
)
def test_public_runtime_evidence_summary_requires_exact_explicit_scope(
    scope: dict[str, Any],
) -> None:
    """Per-action durable evidence was removed; the summary stays unavailable."""
    anchor = {
        **scope,
        "journal_head_sha256": "a" * 64,
        "journal_event_count": 9,
        "report_sha256": "b" * 64,
        "report_status": "complete",
    }

    summary = live_agent._public_runtime_evidence_summary(
        anchor,
        mission_id="mission-private",
        run_id="run-private",
    )

    assert summary == {
        "available": False,
        "status": "incomplete",
        "journal_head_sha256": None,
        "journal_event_count": 0,
        "report_sha256": None,
        "audit_locator": live_agent._runtime_evidence_audit_locator(
            "mission-private",
            "run-private",
        ),
        "parameter_visibility": "private_runtime_evidence",
    }


@pytest.mark.parametrize(
    "mutate",
    (
        lambda snapshot: snapshot.update({"run_id": "other-run"}),
        lambda snapshot: snapshot.update({"target_hwnd": HWND + 1}),
        lambda snapshot: snapshot.update({"frame_id": 10}),
        lambda snapshot: snapshot["verified_evidence_bindings"][0].update(
            {"run_id": "other-run"}
        ),
    ),
)
def test_invalid_completion_binding_is_non_success_before_done_event(
    mutate: Any,
) -> None:
    class InvalidBindingSession(_FakeSession):
        def completion_snapshot(self) -> dict[str, Any]:
            snapshot = super().completion_snapshot()
            mutate(snapshot)
            return snapshot

    session = InvalidBindingSession(
        [MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED)]
    )
    manager = ArpRunManager()
    execution = _prepare(
        _graph(),
        {},
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(session=session),
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        result = _run_prepared(manager, execution)
    finally:
        BUS.remove_sink(events.append)

    assert result["status"] == ArpRunStatus.FAILED.value
    assert result["evidence"] == []
    done = [
        event for event in events
        if event["type"] == "live_agent_done"
    ]
    assert len(done) == 1
    assert done[0]["status"] == RuntimeStatus.STATE_REJECTED.value
    assert "run-bound verified mission evidence" in result["error"]


def test_pause_invalidates_actor_frame_but_not_atomic_post_reconcile_frame() -> None:
    mission_id = "vnext-mission-pause-test"
    run_id = "arp-pause-test"
    definition = MissionDefinition(
        mission_id=mission_id,
        run_id=run_id,
        original_goal=GOAL,
        obligations=(
            ObligationSpec(
                id="full-goal",
                desired_state=GOAL,
                acceptance_criteria=(GOAL,),
                required_capabilities=("visual.interact",),
            ),
        ),
    )
    registry = EvidenceRegistry(mission_id, run_id)
    reducer = MissionReducer(definition, evidence=registry)
    target_ref = EvidenceRef(
        id="e-target-pause",
        mission_id=mission_id,
        run_id=run_id,
        obligation_id="full-goal",
        kind="target_binding",
        trust="deterministic",
        payload_hash="target-pause-payload",
    )
    binding = TargetBinding(
        binding_id="binding-pause",
        hwnd=HWND,
        process_id=PID,
        executable_identity=r"c:\apps\live_agent.exe",
        title_snapshot="Canary Target",
        established_by_evidence_ref=target_ref.id,
        epoch=7,
    )
    registry.append(target_ref)
    reducer.apply(
        reducer.event(
            "target_bound",
            payload={"target_binding": binding.to_dict()},
            evidence_refs=(target_ref.id,),
        )
    )
    authority = FrameAuthority()
    first = authority.commit(
        epoch=7,
        hwnd=HWND,
        region=REGION,
        scope="target_window",
        reason="test-current",
        submitted_size=(640, 480),
        image_b64="/9j/2Q==",
        captured_at=1.0,
        expected_previous_frame_id=0,
    )

    def frame_ref(state: Any, kind: str, evidence_id: str) -> EvidenceRef:
        identity = RuntimeFrameIdentity(
            frame_id=state.frame_id,
            epoch=state.epoch,
            hwnd=state.hwnd,
            region=state.region,
            scope=state.scope,
            submitted_size=state.submitted_size,
            image_sha256=state.image_sha256,
        )
        return EvidenceRef(
            id=evidence_id,
            mission_id=mission_id,
            run_id=run_id,
            obligation_id="full-goal",
            kind=kind,
            trust="deterministic",
            payload_hash=identity.payload_hash,
            frame_id=state.frame_id,
        )

    registry.append(
        frame_ref(first, CURRENT_FRAME_EVIDENCE_KIND, "e-current-pause")
    )
    pause = type("_Pause", (), {"paused": True})()
    state = live_agent._LiveRuntimeState(
        reducer=reducer,
        registry=registry,
        binding=binding,
        target_evidence=target_ref,
        target_probe=live_agent._BoundTargetProbe(
            _facts(),
            reader=lambda _hwnd: _facts(),
            hit_test_root=lambda _x, _y: HWND,
        ),
        authority=authority,
        observer=FrameObserver(authority),
        pause_controller=pause,
    )

    initial_broker_state = state.current(
        reducer.projection,
        turn_index=0,
    ).broker_state
    assert initial_broker_state.paused is True

    second = authority.commit(
        epoch=7,
        hwnd=HWND,
        region=REGION,
        scope="target_window",
        reason="test-post",
        submitted_size=(640, 480),
        image_b64="/9j/4A==",
        captured_at=2.0,
        expected_previous_frame_id=first.frame_id,
    )
    registry.append(
        frame_ref(
            second,
            POST_ACTION_FRAME_EVIDENCE_KIND,
            "e-post-pause",
        )
    )

    assert state.current(reducer.projection, turn_index=0).broker_state.paused is False


def test_goal_admission_audit_is_validated_without_runtime_policy() -> None:
    context = {
        "runtime_state": {
            "goal_admission": {
                "format": "goal-admission/1",
                "goal": GOAL,
                "route": "live_agent",
                "runtime_version": "vnext",
                "reason": "single_semantic_agent",
                "no_legacy_fallback": True,
            },
        },
    }

    assert live_agent._validate_goal_admission(context, goal=GOAL) is None


class _RawClient:
    def __init__(
        self,
        response: Any = None,
        *,
        on_request: Any = None,
        close_error: str = "",
    ) -> None:
        self.response = response
        self.on_request = on_request
        self.close_error = close_error
        self.requests: list[dict[str, Any]] = []
        self.closed = False
        self.close_calls = 0
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **request: Any) -> Any:
        self.requests.append(request)
        if self.on_request is not None:
            return self.on_request(request)
        response = self.response
        if type(response) is list:
            index = len(self.requests) - 1
            if index >= len(response):
                raise AssertionError("Unexpected extra provider request")
            response = response[index]
        if isinstance(response, BaseException):
            raise response
        return response

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True
        if self.close_error:
            raise RuntimeError(self.close_error)


class _NativeTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def post_json(self, **request: Any) -> dict[str, Any]:
        self.calls.append(deepcopy(request))
        return deepcopy(self.response)

    def close(self) -> None:
        self.closed = True


def _runtime_evidence_resources(
    root: Path,
    *,
    suffix: str,
) -> tuple[
    RuntimeEvidencePlane,
    FrameArtifactWriter,
    RuntimeEvidenceJournal,
]:
    mission_id = f"vnext-mission-{suffix}"
    run_id = f"arp-{suffix}"
    journal = RuntimeEvidenceJournal.create(
        root / run_id,
        mission_id,
        run_id,
    )
    writer = FrameArtifactWriter.create(journal.run_directory)
    return (
        RuntimeEvidencePlane(
            mission_id,
            run_id,
            journal=journal,
        ),
        writer,
        journal,
    )


def _click_responses() -> list[dict[str, Any]]:
    planned = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "click",
                                "arguments": json.dumps(
                                    {
                                        "image_x": 20,
                                        "image_y": 20,
                                        "task_state_update": {
                                            "scratchpad": None,
                                        },
                                        "local_intent": (
                                            "activate the requested control once"
                                        ),
                                    },
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    ],
                }
            }
        ]
    }
    return [planned]


def _scroll_responses() -> list[dict[str, Any]]:
    planned = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "scroll",
                                "arguments": json.dumps(
                                    {
                                        "image_x": 37,
                                        "image_y": 29,
                                        "amount": -713,
                                        "task_state_update": {
                                            "scratchpad": None,
                                        },
                                        "local_intent": (
                                            "scroll the requested surface once"
                                        ),
                                    },
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    ],
                }
            }
        ]
    }
    return [planned]


def _install_safe_backends(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
    *,
    on_click: Any = None,
) -> None:
    def click(
        x: int,
        y: int,
        *,
        button: Any,
        count: Any,
        mechanism: Any,
        hwnd: int,
        final_guard: Any,
        enforce_foreground: bool = True,
    ) -> RawBackendResult:
        del button, count, mechanism, hwnd, enforce_foreground
        assert final_guard(x, y).value == "admitted"
        if on_click is not None:
            on_click()
        calls.append("click")
        return RawBackendResult(
            True,
            {"via": "physical_sendinput"},
        )

    def unexpected(*_args: Any, **_kwargs: Any) -> RawBackendResult:
        raise AssertionError("unexpected fake desktop primitive")

    monkeypatch.setattr(
        live_agent,
        "ADMITTED_PHYSICAL_CLICK_BACKEND",
        AdmittedPhysicalClickBackend(click),
    )
    monkeypatch.setattr(
        live_agent,
        "ADMITTED_PHYSICAL_SCROLL_BACKEND",
        AdmittedPhysicalScrollBackend(unexpected),
    )
    monkeypatch.setattr(
        live_agent,
        "ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND",
        AdmittedPhysicalTypeTextBackend(unexpected),
    )
    monkeypatch.setattr(
        live_agent,
        "ADMITTED_PHYSICAL_HOTKEY_BACKEND",
        AdmittedPhysicalHotkeyBackend(unexpected),
    )


def _real_session_with_captures(
    monkeypatch: pytest.MonkeyPatch,
    *,
    images: list[str],
    client: _RawClient,
    clock: list[float] | None = None,
    deadline: float = 310.0,
    install_safe_backends: bool = True,
    hit_test_root: Any = None,
    target_reader: Any = None,
    on_click: Any = None,
    capture_regions: list[Region] | None = None,
    on_capture: Any = None,
    screen_first: bool = False,
) -> tuple[Any, list[str], list[dict[str, Any]]]:
    clock = clock or [10.0]
    capture_values = list(images)
    expected_capture_regions = (
        None if capture_regions is None else list(capture_regions)
    )
    # Screen-first frames are the default; the window-crop path is the
    # explicit opt-in these composition tests exercise by default.
    monkeypatch.setitem(
        live_agent.CONFIG._data["controller"],
        "target_window_capture_enabled",
        not screen_first,
    )

    monkeypatch.setattr(
        live_agent,
        "virtual_screen_region",
        lambda: Region(0, 0, 1920, 1080),
    )

    def capture(
        hwnd: int,
        *,
        max_side: int,
        region: Region,
    ) -> str:
        assert max_side == live_agent._MAX_SUBMITTED_SIDE
        if expected_capture_regions is None:
            expected_region = (
                Region(0, 0, 1920, 1080)
                if screen_first
                else Region(*REGION)
            )
        else:
            expected_region = expected_capture_regions.pop(0)
        assert region == expected_region
        if not capture_values:
            raise AssertionError("unexpected extra capture")
        if on_capture is not None:
            on_capture(hwnd, region)
        return capture_values.pop(0)

    monkeypatch.setattr(
        live_agent,
        "capture_window_jpeg_b64",
        capture,
    )
    raw_calls: list[str] = []
    if install_safe_backends:
        _install_safe_backends(
            monkeypatch,
            raw_calls,
            on_click=on_click,
        )
    factory_calls: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> _RawClient:
        factory_calls.append(kwargs)
        return client

    deps = live_agent._CompositionDependencies(
        wall_clock=lambda: NOW_MS / 1000.0,
        monotonic=lambda: clock[0],
        target_reader=(
            (lambda _hwnd: _facts())
            if target_reader is None
            else target_reader
        ),
        hit_test_root=(
            (lambda _x, _y: HWND)
            if hit_test_root is None
            else hit_test_root
        ),
        client_factory=factory,
    )
    graph = _graph()
    budget = live_agent._freeze_budget(_config())
    model = live_agent._freeze_model(_config(), budget)
    actor_bundle = live_agent._build_actor_resources(
        run_id="arp-test-live-frames",
        mission_id="vnext-mission-test-live-frames",
        budget=budget,
        model=model,
        dependencies=deps,
        deadline=lambda: deadline,
    )
    build = live_agent._SessionBuild(
        graph=graph,
        goal=GOAL,
        run_id="arp-test-live-frames",
        mission_id="vnext-mission-test-live-frames",
        target=live_agent._freeze_prebound_target(
            graph,
            _inputs(),
            dependencies=deps,
        ),
        budget=budget,
        model=model,
        dependencies=deps,
        deadline=lambda: deadline,
        actor_bundle=actor_bundle,
        pause_controller=None,
    )
    return (
        live_agent._build_live_session(build),
        raw_calls,
        factory_calls,
    )


def test_fresh_observation_is_auditable_without_pixel_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from winpilot.recorder import trace

    session, raw_calls, _factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[
            _jpeg(640, 480, level=120),
            _jpeg(640, 480, level=180),
        ],
        client=_RawClient(_click_responses()),
    )
    trace_root = tmp_path / "public-traces"
    monkeypatch.setattr(trace, "TRACES_DIR", trace_root)
    recorder = trace.TraceRecorder()
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        recorder.start("arp-test-live-frames")
        result = session.run_one_turn(
            turn_index=0,
            stop_event=threading.Event(),
        )
        session.close()
        recorder.stop()
    finally:
        recorder.stop()
        BUS.remove_sink(events.append)
        BUS.remove_sink(recorder._on_event)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert session.model_calls == 1
    assert raw_calls == ["click"]
    model_events = [
        item
        for item in events
        if item["type"] == "vnext_model_call"
    ]
    assert [item["model_role"] for item in model_events] == [
        live_agent.VISUAL_ACTOR_MODEL_ROLE,
    ]
    assert len(
        {item["request_fingerprint"] for item in model_events}
    ) == 1
    observation = next(
        item
        for item in events
        if item["type"] == "vnext_gui_observation"
    )
    assert set(observation) == {
        "type",
        "ts",
        "runtime_version",
        "no_legacy_fallback",
        "run_id",
        "mission_id",
        "obligation_id",
        "execution_medium",
        "observation_kind",
        "target_binding_id",
        "target_hwnd",
        "frame_id",
        "frame_epoch",
        "frame_scope",
        "authoritative",
        "fresh",
        "evidence_refs",
        "parameter_visibility",
    }
    assert observation["execution_medium"] == "control"
    assert observation["observation_kind"] == (
        "fresh_authoritative_target_frame"
    )
    assert observation["target_binding_id"] == session._binding.binding_id
    assert observation["target_hwnd"] == HWND
    assert observation["frame_id"] == 1
    assert observation["frame_epoch"] == session._binding.epoch
    assert observation["frame_scope"] == "target_window"
    assert observation["authoritative"] is True
    assert observation["fresh"] is True
    assert len(observation["evidence_refs"]) == 1
    assert (
        observation["parameter_visibility"]
        == "private_runtime_evidence"
    )
    records = [
        json.loads(line)
        for line in (
            trace_root / "arp-test-live-frames.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    traced = [
        item
        for item in records
        if item["type"] == "vnext_gui_observation"
    ]
    assert len(traced) == 1
    assert traced[0]["evidence_refs"] == observation["evidence_refs"]
    _assert_private_runtime_evidence_absent(
        {"bus": observation, "trace": traced}
    )


def test_completed_click_adopts_stable_cross_process_foreground_next_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    next_hwnd = HWND + 50
    foreground = [HWND]

    def reader(hwnd: int) -> live_agent._TargetFacts | None:
        if hwnd == HWND:
            return live_agent._TargetFacts(
                hwnd=HWND,
                root_hwnd=HWND,
                process_id=PID,
                executable_identity=r"c:\apps\live_agent.exe",
                title="Canary Target",
                class_name="CanaryWindow",
                region=REGION,
                foreground_root_hwnd=foreground[0],
            )
        if hwnd == next_hwnd:
            return live_agent._TargetFacts(
                hwnd=next_hwnd,
                root_hwnd=next_hwnd,
                process_id=PID + 1,
                executable_identity=r"c:\apps\successor.exe",
                title="Successor",
                class_name="SuccessorWindow",
                region=REGION,
                foreground_root_hwnd=foreground[0],
            )
        return None

    session, raw_calls, _factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[
            _jpeg(640, 480, level=100),
            _jpeg(640, 480, level=120),
            _jpeg(640, 480, level=180),
        ],
        client=_RawClient(_click_responses()),
        target_reader=reader,
        on_click=lambda: foreground.__setitem__(0, next_hwnd),
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        first = session.run_one_turn(
            turn_index=0,
            stop_event=threading.Event(),
        )
        assert first.status is RuntimeStatus.ACTION_RECORDED
        assert session._reducer.projection.target_binding.hwnd == HWND

        observed = session._runtime._state.observe(
            session._reducer.projection,
            turn_index=1,
        )
        replacement = session._reducer.projection.target_binding
        assert replacement is not None
        assert replacement.hwnd == next_hwnd
        assert observed.frame.hwnd == next_hwnd
        assert observed.target.hwnd == next_hwnd
        assert session._authority.current.hwnd == next_hwnd
    finally:
        session.close()
        BUS.remove_sink(events.append)

    assert raw_calls == ["click"]
    adoption = [
        item
        for item in events
        if item["type"] == "vnext_workspace_target_adopted"
    ]
    assert len(adoption) == 1
    assert adoption[0]["target_hwnd"] == next_hwnd
    assert adoption[0]["causal_action"] == "click"


def test_screen_first_frame_shows_desktop_and_click_rebinds_visible_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Screen-first frames let the model click any window it can see.

    The authoritative frame covers the whole virtual desktop, the pointer
    guard admits a click whose hit root is a different top-level window, and
    the causal successor hand-off re-binds the workspace to that window on the
    next observation after the click raises it.
    """
    next_hwnd = HWND + 50
    foreground = [HWND]

    def reader(hwnd: int) -> live_agent._TargetFacts | None:
        if hwnd == HWND:
            return live_agent._TargetFacts(
                hwnd=HWND,
                root_hwnd=HWND,
                process_id=PID,
                executable_identity=r"c:\apps\live_agent.exe",
                title="Canary Target",
                class_name="CanaryWindow",
                region=REGION,
                foreground_root_hwnd=foreground[0],
            )
        if hwnd == next_hwnd:
            return live_agent._TargetFacts(
                hwnd=next_hwnd,
                root_hwnd=next_hwnd,
                process_id=PID + 1,
                executable_identity=r"c:\apps\successor.exe",
                title="Successor",
                class_name="SuccessorWindow",
                region=REGION,
                foreground_root_hwnd=foreground[0],
            )
        return None

    session, raw_calls, _factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[
            _jpeg(1120, 630, level=90),
            _jpeg(1120, 630, level=110),
            _jpeg(1120, 630, level=150),
        ],
        client=_RawClient(_click_responses()),
        target_reader=reader,
        hit_test_root=lambda _x, _y: next_hwnd,
        on_click=lambda: foreground.__setitem__(0, next_hwnd),
        screen_first=True,
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        first = session.run_one_turn(
            turn_index=0,
            stop_event=threading.Event(),
        )
        assert first.status is RuntimeStatus.ACTION_RECORDED
        assert session._reducer.projection.target_binding.hwnd == HWND

        observation = next(
            item
            for item in events
            if item["type"] == "vnext_gui_observation"
        )
        assert observation["frame_scope"] == "full_desktop"
        assert observation["target_hwnd"] == HWND

        observed = session._runtime._state.observe(
            session._reducer.projection,
            turn_index=1,
        )
        replacement = session._reducer.projection.target_binding
        assert replacement is not None
        assert replacement.hwnd == next_hwnd
        assert observed.frame.hwnd == next_hwnd
        assert observed.target.hwnd == next_hwnd
    finally:
        session.close()
        BUS.remove_sink(events.append)

    assert raw_calls == ["click"]
    adoption = [
        item
        for item in events
        if item["type"] == "vnext_workspace_target_adopted"
    ]
    assert len(adoption) == 1
    assert adoption[0]["target_hwnd"] == next_hwnd
    assert adoption[0]["causal_action"] == "click"


def test_screen_first_observation_survives_missing_old_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, raw_calls, _factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[_jpeg(1120, 630, level=90)],
        client=_RawClient(_click_responses()),
        screen_first=True,
    )
    workspace = session._runtime._state._workspace
    monkeypatch.setattr(
        workspace,
        "observe",
        lambda: (_ for _ in ()).throw(
            RuntimeError("current workspace surface is unavailable")
        ),
    )
    monkeypatch.setattr(
        workspace,
        "snapshot",
        lambda _hwnd: pytest.fail(
            "screen-first observation consulted the old target"
        ),
    )

    try:
        observed = session._runtime._state.observe(
            session._reducer.projection,
            turn_index=0,
        )
    finally:
        session.close()

    assert observed.frame.scope == "full_desktop"
    assert observed.frame.hwnd == HWND
    assert raw_calls == []


def _launch_responses() -> list[dict[str, Any]]:
    planned = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "launch_application",
                                "arguments": json.dumps(
                                    {
                                        "app": "Notepad",
                                        "arguments": "",
                                        "task_state_update": {
                                            "scratchpad": None,
                                        },
                                        "local_intent": (
                                            "start the requested application"
                                        ),
                                    },
                                    separators=(",", ":"),
                                ),
                            },
                        }
                    ],
                }
            }
        ]
    }
    return [planned]


def test_real_composition_launches_a_generic_application_and_arms_successor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launched: list[tuple[str, str]] = []

    def launcher(app: str, arguments: str) -> dict[str, Any]:
        launched.append((app, arguments))
        return {"ok": True, "via": "fake", "target": app}

    monkeypatch.setattr(
        live_agent,
        "_launch_application_primitive",
        launcher,
    )
    session, raw_calls, _factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[
            _jpeg(640, 480, level=100),
            _jpeg(640, 480, level=120),
        ],
        client=_RawClient(_launch_responses()),
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        result = session.run_one_turn(
            turn_index=0,
            stop_event=threading.Event(),
        )
    finally:
        session.close()
        BUS.remove_sink(events.append)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert launched == [("Notepad", "")]
    assert raw_calls == []
    transition = session._reducer.projection.last_transition
    assert transition is not None
    assert transition.action == "launch_application"
    assert transition.input_completed is True
    receipts = [
        item
        for item in events
        if item["type"] == "vnext_raw_receipt"
        and item["raw_action"] == "launch_application"
    ]
    assert len(receipts) == 1
    assert receipts[0]["input_completed"] is True
    assert receipts[0]["target_window"]["process"] == "live_agent.exe"
def test_real_composition_launch_failure_is_recoverable_before_any_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        live_agent,
        "_launch_application_primitive",
        lambda _app, _arguments: {
            "ok": False,
            "error": "no such application",
        },
    )
    session, raw_calls, _factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[
            _jpeg(640, 480, level=100),
        ],
        client=_RawClient(_launch_responses()),
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        result = session.run_one_turn(
            turn_index=0,
            stop_event=threading.Event(),
        )
    finally:
        session.close()
        BUS.remove_sink(events.append)

    assert result.status is RuntimeStatus.STALE_DISCARDED
    assert raw_calls == []
    receipts = [
        item
        for item in events
        if item["type"] == "vnext_raw_receipt"
        and item["raw_action"] == "launch_application"
    ]
    assert len(receipts) == 1
    assert receipts[0]["input_completed"] is False
    assert receipts[0]["error_kind"] == "launch_application_failed"


def test_desktop_shell_launch_adopts_the_launched_window_next_observation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    shell_hwnd = 65878
    launched_hwnd = 70000
    foreground = [shell_hwnd]

    def shell_reader(hwnd: int) -> live_agent._TargetFacts | None:
        if hwnd == shell_hwnd:
            return live_agent._TargetFacts(
                hwnd=shell_hwnd,
                root_hwnd=shell_hwnd,
                process_id=19076,
                executable_identity=r"c:\windows\explorer.exe",
                title="Program Manager",
                class_name="Progman",
                region=(0, 0, 1920, 1080),
                foreground_root_hwnd=foreground[0],
            )
        if hwnd == launched_hwnd:
            return live_agent._TargetFacts(
                hwnd=launched_hwnd,
                root_hwnd=launched_hwnd,
                process_id=8812,
                executable_identity=r"c:\apps\launched.exe",
                title="Launched App",
                class_name="LaunchedWindow",
                region=(0, 0, 1920, 1080),
                foreground_root_hwnd=foreground[0],
            )
        return None

    launched: list[tuple[str, str]] = []

    def launcher(app: str, arguments: str) -> dict[str, Any]:
        launched.append((app, arguments))
        foreground[0] = launched_hwnd
        return {"ok": True, "via": "fake", "target": app}

    monkeypatch.setattr(
        live_agent,
        "_launch_application_primitive",
        launcher,
    )
    monkeypatch.setattr(
        live_agent,
        "virtual_screen_region",
        lambda: Region(0, 0, 1920, 1080),
    )

    def capture(
        hwnd: int,
        *,
        max_side: int,
        region: Region,
    ) -> str:
        assert max_side == live_agent._MAX_SUBMITTED_SIDE
        assert region == Region(0, 0, 1920, 1080)
        return _jpeg(1120, 630, level=150)

    monkeypatch.setattr(
        live_agent,
        "capture_window_jpeg_b64",
        capture,
    )
    raw_calls: list[str] = []
    _install_safe_backends(monkeypatch, raw_calls)
    factory_calls: list[dict[str, Any]] = []
    client = _RawClient(_launch_responses())

    def factory(**kwargs: Any) -> _RawClient:
        factory_calls.append(kwargs)
        return client

    deps = live_agent._CompositionDependencies(
        wall_clock=lambda: NOW_MS / 1000.0,
        monotonic=lambda: 10.0,
        target_reader=shell_reader,
        hit_test_root=lambda _x, _y: shell_hwnd,
        client_factory=factory,
    )
    inputs = _inputs()
    target_window = inputs["execution_context"]["target_window"]
    target_window["binding_mode"] = "desktop_shell"
    target_window["foreground_verified"] = False
    target_window["title"] = "Program Manager"
    target_window["class_name"] = "Progman"
    target_window["hwnd"] = shell_hwnd
    target_window["process_id"] = 19076
    target_window["rect"] = [0, 0, 1920, 1080]
    graph = _graph()
    budget = live_agent._freeze_budget(_config())
    model = live_agent._freeze_model(_config(), budget)
    actor_bundle = live_agent._build_actor_resources(
        run_id="arp-test-shell-launch",
        mission_id="vnext-mission-test-shell-launch",
        budget=budget,
        model=model,
        dependencies=deps,
        deadline=lambda: 310.0,
    )
    build = live_agent._SessionBuild(
        graph=graph,
        goal=GOAL,
        run_id="arp-test-shell-launch",
        mission_id="vnext-mission-test-shell-launch",
        target=live_agent._freeze_prebound_target(
            graph,
            inputs,
            dependencies=deps,
        ),
        budget=budget,
        model=model,
        dependencies=deps,
        deadline=lambda: 310.0,
        actor_bundle=actor_bundle,
        pause_controller=None,
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    session = None
    try:
        session = live_agent._build_live_session(build)
        assert session._controller._desktop_shell is True
        result = session.run_one_turn(
            turn_index=0,
            stop_event=threading.Event(),
        )
        assert result.status is RuntimeStatus.ACTION_RECORDED
        assert launched == [("Notepad", "")]

        # The mechanical fuel gauge is wired end-to-end: the visual turn's
        # user context carries consumed/max turns, calls, and wall seconds.
        visual_request = client.requests[0]
        turn_text = visual_request["messages"][-1]["content"][0]["text"]
        turn_payload = json.loads(turn_text)
        assert turn_payload["budget"]["max_turns"] == budget.max_turns
        assert turn_payload["budget"]["max_model_calls"] == budget.max_model_calls
        assert turn_payload["budget"]["max_runtime_s"] == budget.max_runtime_s
        assert turn_payload["budget"]["turns_used"] == 0
        assert turn_payload["budget"]["model_calls_used"] == 0
        assert turn_payload["budget"]["elapsed_s"] >= 0

        observed = session._runtime._state.observe(
            session._reducer.projection,
            turn_index=1,
        )
        replacement = session._reducer.projection.target_binding
        assert replacement is not None
        assert replacement.hwnd == launched_hwnd
        assert observed.frame.hwnd == launched_hwnd
        assert observed.target.hwnd == launched_hwnd
        # The desktop-shell flag must retire once a real window is adopted,
        # so later frames return to the screen-first full-desktop scope
        # instead of keeping the labelled window crop.
        assert session._controller._is_desktop_shell is False
        assert (
            session._runtime._state._workspace.desktop_shell
            is False
        )
        assert observed.frame.scope == "full_desktop"
    finally:
        if session is not None:
            session.close()
        BUS.remove_sink(events.append)

    adoption = [
        item
        for item in events
        if item["type"] == "vnext_workspace_target_adopted"
    ]
    assert len(adoption) == 1
    assert adoption[0]["target_hwnd"] == launched_hwnd
    assert adoption[0]["causal_action"] == "launch_application"


def test_full_desktop_successor_is_discarded_before_stable_atomic_adoption(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transient_hwnd = HWND + 25
    next_hwnd = HWND + 50
    desktop = Region(0, 0, 1920, 1080)
    foreground = [HWND]
    physical_clicks = [0]
    session_holder: list[Any] = []
    authoritative_before_transition: list[Any] = []
    events: list[dict[str, Any]] = []
    captures: list[tuple[int, Region]] = []

    def facts(
        hwnd: int,
        *,
        process_id: int,
        executable_identity: str,
        title: str,
        class_name: str,
        region: Region,
    ) -> live_agent._TargetFacts:
        return live_agent._TargetFacts(
            hwnd=hwnd,
            root_hwnd=hwnd,
            process_id=process_id,
            executable_identity=executable_identity,
            title=title,
            class_name=class_name,
            region=(region.left, region.top, region.width, region.height),
            foreground_root_hwnd=foreground[0],
        )

    def reader(hwnd: int) -> live_agent._TargetFacts | None:
        if hwnd == HWND:
            return facts(
                HWND,
                process_id=PID,
                executable_identity=r"c:\apps\live_agent.exe",
                title="Canary Target",
                class_name="CanaryWindow",
                region=Region(*REGION),
            )
        if hwnd == transient_hwnd:
            return facts(
                transient_hwnd,
                process_id=PID + 1,
                executable_identity=r"c:\system\desktop_surface.exe",
                title="Transient Desktop Surface",
                class_name="DesktopSurface",
                region=desktop,
            )
        if hwnd == next_hwnd:
            return facts(
                next_hwnd,
                process_id=PID + 2,
                executable_identity=r"c:\apps\stable_successor.exe",
                title="Stable Successor",
                class_name="StableWindow",
                region=Region(*REGION),
            )
        return None

    def after_click() -> None:
        physical_clicks[0] += 1
        if physical_clicks[0] == 1:
            foreground[0] = transient_hwnd

    def after_capture(hwnd: int, region: Region) -> None:
        captures.append((hwnd, region))
        if region != desktop:
            return
        session = session_holder[0]
        before = authoritative_before_transition[0]
        projection_binding = session._reducer.projection.target_binding
        assert projection_binding is not None
        assert projection_binding.hwnd == HWND
        assert session._runtime._state._workspace.binding.hwnd == HWND
        assert session._authority.current == before
        assert not [
            item
            for item in events
            if item["type"] == "vnext_workspace_target_adopted"
        ]
        foreground[0] = next_hwnd

    session, raw_calls, _factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[
            _jpeg(640, 480, level=80),
            _jpeg(640, 480, level=100),
            _jpeg(1120, 630, level=120),
            _jpeg(640, 480, level=160),
            _jpeg(640, 480, level=180),
        ],
        client=_RawClient(_click_responses() * 2),
        target_reader=reader,
        hit_test_root=lambda _x, _y: foreground[0],
        on_click=after_click,
        capture_regions=[
            Region(*REGION),
            Region(*REGION),
            desktop,
            Region(*REGION),
            Region(*REGION),
        ],
        on_capture=after_capture,
    )
    session_holder.append(session)
    session._runtime._state._observer._virtual_desktop = lambda: desktop
    BUS.add_sink(events.append)
    try:
        first = session.run_one_turn(
            turn_index=0,
            stop_event=threading.Event(),
        )
        assert first.status is RuntimeStatus.ACTION_RECORDED
        before = session._authority.current
        assert before is not None
        assert before.hwnd == HWND
        authoritative_before_transition.append(before)

        second = session.run_one_turn(
            turn_index=1,
            stop_event=threading.Event(),
        )
        assert second.status is RuntimeStatus.ACTION_RECORDED
        replacement = session._reducer.projection.target_binding
        assert replacement is not None
        assert replacement.hwnd == next_hwnd
        assert session._runtime._state._workspace.binding == replacement
        assert session._controller is not None
        assert session._controller.frozen_target_binding == replacement
        current = session._authority.current
        assert current is not None
        assert current.hwnd == next_hwnd
        assert current.epoch == replacement.epoch
        assert current.frame_id == before.frame_id + 2
    finally:
        session.close()
        BUS.remove_sink(events.append)

    assert raw_calls == ["click", "click"]
    assert captures[-3:] == [
        (transient_hwnd, desktop),
        (next_hwnd, Region(*REGION)),
        (next_hwnd, Region(*REGION)),
    ]
    adoption = [
        item
        for item in events
        if item["type"] == "vnext_workspace_target_adopted"
    ]
    assert len(adoption) == 1
    assert adoption[0]["target_hwnd"] == next_hwnd
    assert adoption[0]["causal_action"] == "click"
    observations = [
        item
        for item in events
        if item["type"] == "vnext_gui_observation"
    ]
    assert [item["target_hwnd"] for item in observations] == [
        HWND,
        next_hwnd,
    ]
    target_evidence = [
        item
        for item in session._registry.snapshot()
        if item.kind == "target_binding"
    ]
    assert len(target_evidence) == 2
    assert target_evidence[-1].id == adoption[0]["target_binding_id"].replace(
        "vnext-workspace-",
        "vnext-workspace-target-",
    )


def test_initial_capture_dimension_mismatch_stops_before_model_or_raw(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    actor_client = _RawClient(_click_responses())
    session, raw_calls, _factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[_jpeg(639, 480)],
        client=actor_client,
    )
    try:
        result = session.run_one_turn(
            turn_index=0,
            stop_event=threading.Event(),
        )
    finally:
        session.close()

    assert result.status is RuntimeStatus.STATE_REJECTED
    assert actor_client.requests == []
    assert raw_calls == []
    assert session._authority.current is None


def test_observation_retries_one_target_transition_during_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session, raw_calls, _factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[
            _jpeg(640, 480, level=100),
            _jpeg(640, 480, level=120),
        ],
        client=_RawClient(_click_responses()),
    )
    workspace = session._runtime._state._workspace
    original_snapshot = workspace.snapshot
    snapshot_calls = 0

    def snapshot(hwnd: int) -> Any:
        nonlocal snapshot_calls
        snapshot_calls += 1
        return None if snapshot_calls == 2 else original_snapshot(hwnd)

    monkeypatch.setattr(workspace, "snapshot", snapshot)
    try:
        observed = session._runtime._state.observe(
            session._reducer.projection,
            turn_index=0,
        )
    finally:
        session.close()

    assert observed.frame.hwnd == HWND
    assert session._authority.current.frame_id == observed.frame.frame_id
    assert (
        session._authority.current.image_sha256
        == observed.frame.image_sha256
    )
    assert snapshot_calls >= 5
    assert raw_calls == []


def test_post_capture_failure_defers_to_fresh_observation_without_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    actor_client = _RawClient(_click_responses())
    session, raw_calls, _factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[
            _jpeg(640, 480),
            _jpeg(639, 480),
        ],
        client=actor_client,
    )
    try:
        first = session.run_one_turn(
            turn_index=0,
            stop_event=threading.Event(),
        )
        second = session.run_one_turn(
            turn_index=1,
            stop_event=threading.Event(),
        )
    finally:
        session.close()

    assert first.status is RuntimeStatus.ACTION_RECORDED
    assert second.status is RuntimeStatus.STATE_REJECTED
    assert raw_calls == ["click"]
    assert len(actor_client.requests) == 1
    tools = {
        tool["function"]["name"]: tool["function"]["parameters"]
        for tool in actor_client.requests[0]["tools"]
    }
    assert {
        "click",
        "double_click",
        "drag",
        "scroll",
        "type_text",
        "press_key",
        "claim_obligation_done",
        "blocked",
    } <= set(tools)
    for name in {
        "click",
        "double_click",
        "drag",
        "scroll",
        "type_text",
        "press_key",
    }:
        local_intent = tools[name]["properties"]["local_intent"]
        assert local_intent["type"] == "string"
        assert local_intent["default"] == ""
        assert "pattern" not in local_intent


def test_each_model_call_is_clamped_to_one_second_remaining_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clock = [10.0]

    def slow_request(request: dict[str, Any]) -> Any:
        assert request["timeout"] == pytest.approx(1.0)
        clock[0] += 1.1
        raise TimeoutError("slow fake exceeded its request timeout")

    actor_client = _RawClient(on_request=slow_request)
    session, raw_calls, factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[_jpeg(640, 480)],
        client=actor_client,
        clock=clock,
        deadline=11.0,
    )
    try:
        result = session.run_one_turn(
            turn_index=0,
            stop_event=threading.Event(),
        )
    finally:
        session.close()

    assert [call["timeout"] for call in factory_calls] == [
        pytest.approx(1.0),
    ]
    assert len(actor_client.requests) == 1
    assert raw_calls == []
    assert result.status is RuntimeStatus.ACTOR_ERROR
    assert clock[0] == pytest.approx(11.1)












def test_run_bound_lease_rejects_target_changed_after_actor_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    current_target = [_facts()]
    raw_constructions: list[bool] = []
    client_calls: list[dict[str, Any]] = []
    clients: list[_RawClient] = []

    def forbidden_raw(*_args: Any, **_kwargs: Any) -> Any:
        raw_constructions.append(True)
        raise AssertionError("RawDevice must not be constructed")

    def client_factory(**kwargs: Any) -> Any:
        client_calls.append(kwargs)
        client = _RawClient(_observe_desktop_response())
        clients.append(client)
        return client

    dependencies = live_agent._CompositionDependencies(
        wall_clock=lambda: NOW_MS / 1000.0,
        monotonic=lambda: 10.0,
        target_reader=lambda _hwnd: current_target[0],
        hit_test_root=lambda _x, _y: HWND,
        client_factory=client_factory,
    )
    graph = _graph()
    execution = _prepare(
        graph,
        {},
        _config(),
        run_manager=ArpRunManager(),
        _dependencies=dependencies,
    )
    current_target[0] = _facts(process_id=PID + 1)
    run_id = "arp-test-changed-target-after-start"
    lease = execution.runtime_state["_desktop_lease"]
    results: list[Any] = []
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(live_agent, "RawDeviceExecutor", forbidden_raw)

    def invoke_handler() -> None:
        results.append(
            execution.handlers[ArpNodeKind.AGENT_GOAL](
                next(node for node in graph.nodes if node.id == "full-goal"),
                {"run_id": run_id, "runtime_state": {}},
                threading.Event(),
            )
        )

    BUS.add_sink(events.append)
    worker = threading.Thread(target=invoke_handler, daemon=True)
    try:
        worker.start()
        for _ in range(2_000):
            lease_snapshot = lease.snapshot()
            if lease_snapshot["status"] == "waiting":
                break
            threading.Event().wait(0.001)
        else:
            pytest.fail("Actor did not request the desktop lease")
        with pytest.raises(
            live_agent.DesktopLeaseError,
            match="process_id",
        ):
            lease.bind(
                run_id=run_id,
                lease_id=lease_snapshot["lease_id"],
                inputs=_inputs(),
            )
        lease.fail(
            run_id=run_id,
            lease_id=lease_snapshot["lease_id"],
            reason="target changed before desktop lease binding",
        )
        worker.join(timeout=2.0)
    finally:
        BUS.remove_sink(events.append)

    assert not worker.is_alive()
    assert len(results) == 1
    result = results[0]
    assert result.ok is False
    assert result.branch == "fail"
    assert result.output is not None
    assert result.output["failure_stage"] == "target_binding"
    assert "target changed before desktop lease binding" in result.error
    assert raw_constructions == []
    assert len(client_calls) == len(clients) == 1
    assert len(clients[0].requests) == 1
    assert clients[0].closed is True
    event_types = [event["type"] for event in events]
    assert [
        event_type
        for event_type in event_types
        if event_type.startswith("desktop_lease_")
    ] == [
        "desktop_lease_requested",
        "desktop_lease_rejected",
        "desktop_lease_failed",
    ]
    assert "runtime_evidence_sealed" not in event_types






def test_session_cleanup_closes_every_resource_and_aggregates_errors() -> None:
    class CloseResource:
        def __init__(self, name: str, *, fail: bool) -> None:
            self.name = name
            self.fail = fail
            self.calls = 0

        def close(self) -> None:
            self.calls += 1
            if self.fail:
                raise RuntimeError(f"{self.name} unavailable")

    registry = CloseResource("registry", fail=True)
    actor_client = _RawClient(close_error="actor close unavailable")
    session = live_agent._LiveAgentSession(
        runtime=object(),
        reducer=object(),
        registry=registry,
        authority=FrameAuthority(),
        binding=object(),
        counter=object(),
        client=actor_client,
    )

    with pytest.raises(ExceptionGroup) as caught:
        session.close()

    assert registry.calls == 1
    assert actor_client.closed is True
    messages = [str(error) for error in caught.value.exceptions]
    assert len(messages) == 2
    assert any("evidence registry" in message for message in messages)
    assert any("Visual Actor client" in message for message in messages)


def test_composition_root_has_no_forbidden_product_imports_or_fallback() -> None:
    source = Path(live_agent.__file__).read_text(encoding="utf-8")

    assert "default_handlers" not in source
    assert "CapabilityPlanner" not in source
    assert "TaskContractCompiler" not in source
    assert "winpilot.targeting" not in source
    assert "winpilot.inspector" not in source
    assert "winpilot.profiler" not in source
    assert "winpilot.mcp" not in source
    assert "from ..agent" not in source
    assert "fallback_to_legacy" not in source
    assert source.count("from ..vnext") >= 1


def test_goal_admission_has_no_legacy_tasking_or_agent_dependency() -> None:
    source = Path(goal_admission.__file__).read_text(encoding="utf-8")

    assert "tasking" not in source
    assert "TaskContractCompiler" not in source
    assert "AgentRunner" not in source
    assert "default_handlers" not in source
    assert "legacy_goal_graph_task" not in source


def test_flow_fragment_resources_keep_local_budget_without_run_telemetry() -> None:
    response = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-flow-fragment",
                    "type": "function",
                    "function": {
                        "name": "propose_flow_fragment",
                        "arguments": json.dumps({
                            "summary": "click",
                            "nodes": [{
                                "local_id": "click",
                                "type": "click",
                                "label": "Click",
                                "params_json": "{\"x\":10,\"y\":20}",
                            }],
                            "edges": [],
                        }),
                    },
                }],
            },
        }],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }
    raw_client = _RawClient(response)
    config = _config()
    budget = live_agent._freeze_budget(config)
    model = live_agent._freeze_model(config, budget)
    dependencies = live_agent._CompositionDependencies(
        monotonic=lambda: 10.0,
        client_factory=lambda **_kwargs: raw_client,
    )
    events: list[dict[str, Any]] = []
    sink = events.append
    BUS.add_sink(sink)
    bundle = None
    try:
        bundle = live_agent._build_actor_resources(
            run_id="flow-fragment-test",
            mission_id="flow-fragment-proposal-test",
            budget=budget,
            model=model,
            dependencies=dependencies,
            deadline=lambda: 200.0,
            publish_run_telemetry=False,
            role_factory=live_agent.FlowFragmentProposer,
        )
        fragment = bundle.actor.propose_flow_fragment(
            {"request": "click"},
            catalog_ports={"click": ("output",)},
            catalog_params={"click": frozenset({"x", "y"})},
        )
    finally:
        if bundle is not None:
            bundle.close()
        BUS.remove_sink(sink)

    assert fragment["nodes"][0]["params"] == {"x": 10, "y": 20}
    assert bundle is not None
    assert bundle.counter.count == 1
    assert bundle.usage_total["prompt_tokens"] == 7
    assert bundle.usage_total["completion_tokens"] == 3
    assert not {
        event.get("type") for event in events
    } & {"vnext_model_call", "agent_usage"}


def test_status_query_reply_uses_real_runtime_facts_without_desktop_lease(
    tmp_path: Path,
) -> None:
    status_reply = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "memory is disabled",
                    "tool_calls": [],
                }
            }
        ]
    }
    clients: list[_RawClient] = []

    def client_factory(**_kwargs: Any) -> _RawClient:
        client = _RawClient(
            [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "status-1",
                                        "type": "function",
                                        "function": {
                                            "name": "get_memory_status",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                status_reply,
            ]
        )
        clients.append(client)
        return client

    def forbidden_session(build: Any) -> Any:
        raise AssertionError("status query must not compose a desktop session")

    dependencies = live_agent._CompositionDependencies(
        wall_clock=lambda: NOW_MS / 1000.0,
        monotonic=lambda: 10.0,
        target_reader=lambda _hwnd: pytest.fail(
            "status query must not inspect a desktop target"
        ),
        hit_test_root=lambda _x, _y: pytest.fail(
            "status query must not hit-test the desktop"
        ),
        client_factory=client_factory,
        session_builder=forbidden_session,
        config_snapshot=lambda: {
            **_config(),
            "agent": {"experience_memory": True},
        },
    )
    manager = ArpRunManager()
    execution = _prepare(
        _graph(),
        {},
        _config(),
        run_manager=manager,
        _dependencies=dependencies,
    )
    snapshot = _run_prepared(manager, execution)

    assert snapshot["status"] == ArpRunStatus.REPLY.value
    assert execution.runtime_state["user_result"] == "memory is disabled"
    assert (
        execution.runtime_state["_desktop_lease"].snapshot()["status"]
        == "idle"
    )
    assert len(clients) == 1
    assert len(clients[0].requests) == 2
    status_tool = json.loads(clients[0].requests[1]["messages"][3]["content"])
    assert status_tool["format"] == "memory-status/1"
    assert status_tool["long_term_experience_memory"] == "disabled"
    assert status_tool["enabled"] is False
    assert status_tool["experience_policy"] == "disabled"
    assert status_tool["config_agent_experience_memory"] is True


def test_failure_stage_classification_is_explicit() -> None:
    from winpilot.orchestrator.live_agent import (
        classify_live_agent_failure_stage,
    )
    from winpilot.vnext.runtime import RuntimeStatus

    cases = (
        (RuntimeStatus.INVALID_PROPOSAL, "bad", {}, "tool_format"),
        (RuntimeStatus.ACTOR_ERROR, "provider timeout", {}, "model_routing"),
        # blocked is the Actor's own semantic stop verdict, not a system
        # physical-input failure.
        (RuntimeStatus.BLOCKED, "outcome unknown", {}, "model_routing"),
        # unrecognized state_rejected is a system precondition failure, not an
        # automatic model verdict.
        (
            RuntimeStatus.STATE_REJECTED,
            "controller rejected unknown precondition",
            {},
            "physical_input",
        ),
        (
            RuntimeStatus.STATE_REJECTED,
            "desktop lease timed out",
            {},
            "desktop_handoff",
        ),
        (
            RuntimeStatus.STATE_REJECTED,
            "current Electron target mismatch: process_id",
            {},
            "target_binding",
        ),
        (
            RuntimeStatus.STATE_REJECTED,
            "fresh target-window JPEG capture failed closed",
            {},
            "screenshot",
        ),
        (
            RuntimeStatus.STATE_REJECTED,
            "vNext verified completion result failed closed",
            {},
            "result_verification",
        ),
        (
            RuntimeStatus.STATE_REJECTED,
            "vNext model-call budget exhausted",
            {},
            "model_routing",
        ),
        (
            RuntimeStatus.CANCELED,
            "run canceled",
            {},
            "canceled",
        ),
        (
            RuntimeStatus.MISSION_VERIFIED,
            "",
            {},
            "",
        ),
    )
    for status, detail, lease, expected in cases:
        assert (
            classify_live_agent_failure_stage(
                status,
                detail,
                lease_state=lease,
            )
            == expected
        ), (status, detail)
