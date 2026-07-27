from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
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
from winpilot.orchestrator import vnext_live_canary as canary
from winpilot.agent.frame_authority import FrameAuthority
from winpilot.utils.log import BUS
from winpilot.utils.screenshot import Region
from winpilot.visual_runtime.keyboard_contracts import HotkeyChord
from winpilot.visual_runtime.frame_observer import FrameObserver
from winpilot.vnext.actor import InvalidActorProposal, parse_actor_proposal
from winpilot.vnext.capabilities import (
    ToolArgumentValidationError,
    ToolArgumentValidator,
)
from winpilot.vnext.evidence import EvidenceRegistry
from winpilot.vnext.mission import (
    EvidencePolicy,
    EvidenceRef,
    MissionDefinition,
    MissionReducer,
    ObligationSpec,
    TargetBinding,
)
from winpilot.vnext.runtime import MissionRuntimeResult, RuntimeStatus
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


def _jpeg(width: int, height: int, level: int = 127) -> str:
    image = np.full((height, width, 3), level, dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def _graph_dict() -> dict[str, Any]:
    graph_id = f"vnext-live-canary-{canary._fnv1a_utf16(GOAL)}"
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
                "prepared_at": NOW_MS - 800,
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
            "live_canary_enabled": True,
            "max_turns": 30,
            "max_runtime_s": 300,
            "max_model_calls": 60,
        },
    }


def _facts(
    *,
    process_id: int = PID,
    foreground_root_hwnd: int | None = HWND,
    region: tuple[int, int, int, int] = REGION,
) -> canary._TargetFacts:
    return canary._TargetFacts(
        hwnd=HWND,
        root_hwnd=HWND,
        process_id=process_id,
        executable_identity=r"c:\apps\canary.exe",
        title="Canary Target",
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

    def run_one_turn(
        self,
        *,
        turn_index: int,
        stop_event: Any,
    ) -> MissionRuntimeResult:
        assert turn_index == self.calls
        assert not stop_event.is_set()
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
            "frame_id": 9,
            "frame_epoch": self.frame_epoch,
        }

    def close(self) -> None:
        self.closed = True


def _dependencies(
    *,
    session: _FakeSession | None = None,
    reader: Any = None,
    client_factory: Any = None,
) -> canary._CompositionDependencies:
    def build(build_spec: Any) -> _FakeSession | None:
        if session is None:
            return None
        session.mission_id = build_spec.mission_id
        session.run_id = build_spec.run_id
        session.target_binding_id = build_spec.target.binding_id
        session.frame_epoch = build_spec.target.captured_at_ms
        return session

    return canary._CompositionDependencies(
        wall_clock=lambda: NOW_MS / 1000.0,
        monotonic=lambda: 10.0,
        target_reader=reader or (lambda _hwnd: _facts()),
        hit_test_root=lambda _x, _y: HWND,
        client_factory=client_factory,
        session_builder=(
            build if session is not None else None
        ),
    )


def test_prepare_registers_only_exact_agent_goal_and_emits_no_live_trace() -> None:
    session = _FakeSession(
        [MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED)]
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        manager = ArpRunManager()
        execution = canary.prepare_live_vnext_execution(
            _graph(),
            _inputs(),
            _config(),
            run_manager=manager,
            _dependencies=_dependencies(session=session),
        )
    finally:
        BUS.remove_sink(events.append)

    assert execution.handlers.keys() == {ArpNodeKind.AGENT_GOAL}
    assert execution.runtime_state == {
        "runtime_version": "vnext",
        "runtime_budget": {
            "max_turns": 30,
            "max_runtime_s": 300,
            "max_model_calls": 60,
        },
        "no_legacy_fallback": True,
        "experience_policy": "disabled",
        "target_binding_id": execution.runtime_state["target_binding_id"],
    }
    assert not [event for event in events if event["type"].startswith("vnext_")]


@pytest.mark.parametrize(
    "mutate",
    (
        lambda raw: raw["nodes"].append(deepcopy(raw["nodes"][2])),
        lambda raw: raw["nodes"][1]["config"].update({"task": "hidden"}),
        lambda raw: raw["nodes"][1].update({"timeout_s": 300}),
        lambda raw: raw["policy"].update({"max_node_visits": 2}),
        lambda raw: raw["metadata"].update({"task_contract": {}}),
        lambda raw: raw.update({"id": "vnext-live-canary-wrong"}),
    ),
)
def test_exact_graph_rejects_every_expansion(mutate: Any) -> None:
    raw = _graph_dict()
    mutate(raw)
    graph = ArpGraph.model_validate(raw)

    with pytest.raises(ValueError, match="vNext|AGENT_GOAL"):
        canary.prepare_live_vnext_execution(
            graph,
            _inputs(),
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
                    {"submitted_at": NOW_MS - 17_000}
                ),
                inputs["execution_context"]["desktop_handoff"].update(
                    {"prepared_at": NOW_MS - 16_500}
                ),
                inputs["execution_context"]["target_window"].update(
                    {"captured_at": NOW_MS - 16_000}
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
def test_execution_context_is_exact_and_fresh(change: Any, match: str) -> None:
    inputs = _inputs()
    change(inputs)

    with pytest.raises(ValueError, match=match):
        canary.prepare_live_vnext_execution(
            _graph(),
            inputs,
            _config(),
            run_manager=ArpRunManager(),
            _dependencies=_dependencies(
                session=_FakeSession(
                    [MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED)]
                )
            ),
        )


@pytest.mark.parametrize(
    "current",
    (
        _facts(process_id=PID + 1),
        _facts(foreground_root_hwnd=HWND + 1),
        _facts(region=(101, 200, 640, 480)),
    ),
)
def test_current_target_identity_mismatch_fails_before_session(current: Any) -> None:
    built = False

    def builder(_build: Any) -> _FakeSession:
        nonlocal built
        built = True
        return _FakeSession(
            [MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED)]
        )

    deps = canary._CompositionDependencies(
        wall_clock=lambda: NOW_MS / 1000.0,
        monotonic=lambda: 10.0,
        target_reader=lambda _hwnd: current,
        hit_test_root=lambda _x, _y: HWND,
        session_builder=builder,
    )

    with pytest.raises(ValueError, match="current HWND"):
        canary.prepare_live_vnext_execution(
            _graph(),
            _inputs(),
            _config(),
            run_manager=ArpRunManager(),
            _dependencies=deps,
        )
    assert built is False


def test_tool_catalog_is_exact_and_keeps_hotkey_order_as_downstream_gate() -> None:
    catalog = canary._tool_catalog()
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


def test_exact_capture_rejects_clamping_and_decoded_dimension_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int, Region]] = []
    returned = [_jpeg(1120, 280), _jpeg(1119, 280)]
    monkeypatch.setattr(
        canary,
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
        canary,
        "capture_window_jpeg_b64",
        capture,
    )
    visible = Region(100, 120, 2240, 560)
    good = canary._capture_exact_target_jpeg(HWND, region=visible)
    assert good
    assert canary._jpeg_dimensions(good) == (1120, 280)
    assert calls == [(HWND, 1120, visible)]

    assert canary._capture_exact_target_jpeg(HWND, region=visible) == ""
    assert calls[-1] == (HWND, 1120, visible)
    assert (
        canary._capture_exact_target_jpeg(
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
                RuntimeStatus.MISSION_VERIFIED,
                accepted_event_ids=("event-verified",),
            ),
        ]
    )
    events: list[dict[str, Any]] = []
    manager = ArpRunManager()
    execution = canary.prepare_live_vnext_execution(
        _graph(),
        _inputs(),
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(session=session),
    )
    BUS.add_sink(events.append)
    try:
        snapshot = manager.run_prepared(execution)
    finally:
        BUS.remove_sink(events.append)

    assert snapshot["status"] == ArpRunStatus.DONE.value
    assert snapshot["runtime_version"] == "vnext"
    assert snapshot["result"]["status"] == "mission_verified"
    assert snapshot["result"]["turns_used"] == 3
    assert snapshot["result"]["model_calls"] == 3
    assert snapshot["result"]["no_legacy_fallback"] is True
    assert session.calls == 3
    assert session.closed is True
    assert len(snapshot["evidence"]) == 1
    assert snapshot["evidence"][0]["verified"] is True
    starts = [item for item in events if item["type"] == "vnext_canary_start"]
    done = [item for item in events if item["type"] == "vnext_canary_done"]
    turns = [item for item in events if item["type"] == "vnext_canary_turn"]
    assert len(starts) == len(done) == 1
    assert len(turns) == 3
    assert starts[0]["runtime_budget"] == {
        "max_turns": 30,
        "max_runtime_s": 300,
        "max_model_calls": 60,
    }
    assert starts[0]["no_legacy_fallback"] is True
    assert done[0]["status"] == "mission_verified"


def test_handler_stops_on_blocked_without_retry_and_closes() -> None:
    session = _FakeSession(
        [
            MissionRuntimeResult(
                RuntimeStatus.BLOCKED,
                detail="independent verifier could not establish the goal",
            ),
            MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED),
        ]
    )
    manager = ArpRunManager()
    execution = canary.prepare_live_vnext_execution(
        _graph(),
        _inputs(),
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(session=session),
    )

    snapshot = manager.run_prepared(execution)

    assert snapshot["status"] == ArpRunStatus.FAILED.value
    assert session.calls == 1
    assert session.closed is True
    assert "independent verifier" in snapshot["error"]
    assert snapshot["evidence"] == []


class _SnapshotErrorSession(_FakeSession):
    def completion_snapshot(self) -> dict[str, Any]:
        raise RuntimeError("snapshot unavailable")


def test_completion_snapshot_error_still_closes_and_fails_closed() -> None:
    session = _SnapshotErrorSession(
        [MissionRuntimeResult(RuntimeStatus.MISSION_VERIFIED)]
    )
    manager = ArpRunManager()
    execution = canary.prepare_live_vnext_execution(
        _graph(),
        _inputs(),
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(session=session),
    )

    snapshot = manager.run_prepared(execution)

    assert snapshot["status"] == ArpRunStatus.FAILED.value
    assert "completion snapshot failed closed" in snapshot["error"]
    assert session.closed is True
    assert snapshot["evidence"] == []


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
    execution = canary.prepare_live_vnext_execution(
        _graph(),
        _inputs(),
        _config(),
        run_manager=manager,
        _dependencies=_dependencies(session=session),
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        result = manager.run_prepared(execution)
    finally:
        BUS.remove_sink(events.append)

    assert result["status"] == ArpRunStatus.FAILED.value
    assert result["evidence"] == []
    done = [
        event for event in events
        if event["type"] == "vnext_canary_done"
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
                evidence_policy=EvidencePolicy(
                    allow_independent_visual_verifier=True,
                ),
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
        executable_identity=r"c:\apps\canary.exe",
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
    state = canary._LiveRuntimeState(
        reducer=reducer,
        registry=registry,
        binding=binding,
        target_evidence=target_ref,
        target_probe=canary._BoundTargetProbe(
            _facts(),
            reader=lambda _hwnd: _facts(),
            hit_test_root=lambda _x, _y: HWND,
        ),
        authority=authority,
        observer=FrameObserver(authority),
        pause_controller=pause,
    )

    assert state.current(reducer.projection, turn_index=0).broker_state.paused is True

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
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **request: Any) -> Any:
        self.requests.append(request)
        if self.on_request is not None:
            return self.on_request(request)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    def close(self) -> None:
        self.closed = True
        if self.close_error:
            raise RuntimeError(self.close_error)


def _click_response() -> dict[str, Any]:
    return {
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


def _install_safe_backends(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[str],
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
    ) -> RawBackendResult:
        del button, count, mechanism, hwnd
        assert final_guard(x, y).value == "admitted"
        calls.append("click")
        return RawBackendResult(
            True,
            {"via": "physical_sendinput"},
        )

    def unexpected(*_args: Any, **_kwargs: Any) -> RawBackendResult:
        raise AssertionError("unexpected fake desktop primitive")

    monkeypatch.setattr(
        canary,
        "ADMITTED_PHYSICAL_CLICK_BACKEND",
        AdmittedPhysicalClickBackend(click),
    )
    monkeypatch.setattr(
        canary,
        "ADMITTED_PHYSICAL_SCROLL_BACKEND",
        AdmittedPhysicalScrollBackend(unexpected),
    )
    monkeypatch.setattr(
        canary,
        "ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND",
        AdmittedPhysicalTypeTextBackend(unexpected),
    )
    monkeypatch.setattr(
        canary,
        "ADMITTED_PHYSICAL_HOTKEY_BACKEND",
        AdmittedPhysicalHotkeyBackend(unexpected),
    )


def _real_session_with_captures(
    monkeypatch: pytest.MonkeyPatch,
    *,
    images: list[str],
    clients: tuple[_RawClient, _RawClient],
    clock: list[float] | None = None,
    deadline: float = 310.0,
) -> tuple[Any, list[str], list[dict[str, Any]]]:
    clock = clock or [10.0]
    capture_values = list(images)

    monkeypatch.setattr(
        canary,
        "virtual_screen_region",
        lambda: Region(0, 0, 1920, 1080),
    )

    def capture(
        _hwnd: int,
        *,
        max_side: int,
        region: Region,
    ) -> str:
        assert max_side == canary._MAX_SUBMITTED_SIDE
        assert region == Region(*REGION)
        if not capture_values:
            raise AssertionError("unexpected extra capture")
        return capture_values.pop(0)

    monkeypatch.setattr(
        canary,
        "capture_window_jpeg_b64",
        capture,
    )
    raw_calls: list[str] = []
    _install_safe_backends(monkeypatch, raw_calls)
    factory_calls: list[dict[str, Any]] = []
    pending_clients = list(clients)

    def factory(**kwargs: Any) -> _RawClient:
        factory_calls.append(kwargs)
        return pending_clients.pop(0)

    deps = canary._CompositionDependencies(
        wall_clock=lambda: NOW_MS / 1000.0,
        monotonic=lambda: clock[0],
        target_reader=lambda _hwnd: _facts(),
        hit_test_root=lambda _x, _y: HWND,
        client_factory=factory,
    )
    graph = _graph()
    build = canary._SessionBuild(
        graph=graph,
        goal=GOAL,
        run_id="arp-test-live-frames",
        mission_id="vnext-mission-test-live-frames",
        target=canary._freeze_prebound_target(
            graph,
            _inputs(),
            dependencies=deps,
        ),
        budget=canary._freeze_budget(_config()),
        model=canary._freeze_model(
            _config(),
            canary._freeze_budget(_config()),
        ),
        dependencies=deps,
        deadline=lambda: deadline,
        pause_controller=None,
    )
    return (
        canary._build_live_session(build),
        raw_calls,
        factory_calls,
    )


def test_initial_capture_dimension_mismatch_stops_before_model_or_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_client = _RawClient(_click_response())
    verifier_client = _RawClient()
    session, raw_calls, _factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[_jpeg(639, 480)],
        clients=(actor_client, verifier_client),
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
    assert verifier_client.requests == []
    assert raw_calls == []
    assert session._authority.current is None


def test_post_capture_dimension_mismatch_is_unknown_and_never_replayed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_client = _RawClient(_click_response())
    verifier_client = _RawClient()
    session, raw_calls, _factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[
            _jpeg(640, 480),
            _jpeg(639, 480),
        ],
        clients=(actor_client, verifier_client),
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

    assert first.status is RuntimeStatus.BLOCKED
    assert second.status is RuntimeStatus.BLOCKED
    assert raw_calls == ["click"]
    assert len(actor_client.requests) == 1
    tools = {
        tool["function"]["name"]: tool["function"]["parameters"]
        for tool in actor_client.requests[0]["tools"]
    }
    for name in {"click", "double_click", "scroll", "type_text", "hotkey"}:
        assert (
            tools[name]["properties"]["local_intent"]["pattern"]
            == r"\S"
        )


def test_each_model_call_is_clamped_to_one_second_remaining_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [10.0]

    def slow_request(request: dict[str, Any]) -> Any:
        assert request["timeout"] == pytest.approx(1.0)
        clock[0] += 1.1
        raise TimeoutError("slow fake exceeded its request timeout")

    actor_client = _RawClient(on_request=slow_request)
    verifier_client = _RawClient()
    session, raw_calls, factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[_jpeg(640, 480)],
        clients=(actor_client, verifier_client),
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
        pytest.approx(1.0),
    ]
    assert len(actor_client.requests) == 1
    assert raw_calls == []
    assert result.status is RuntimeStatus.ACTOR_ERROR
    assert clock[0] == pytest.approx(11.1)


def test_real_composition_builds_independent_zero_retry_roles_and_closes() -> None:
    calls: list[dict[str, Any]] = []
    clients: list[_RawClient] = []

    def factory(**kwargs: Any) -> _RawClient:
        calls.append(kwargs)
        client = _RawClient()
        clients.append(client)
        return client

    deps = _dependencies(client_factory=factory)
    graph = _graph()
    target = canary._freeze_prebound_target(
        graph,
        _inputs(),
        dependencies=deps,
    )
    budget = canary._freeze_budget(_config())
    model = canary._freeze_model(_config(), budget)
    build = canary._SessionBuild(
        graph=graph,
        goal=GOAL,
        run_id="arp-test-live-composition",
        mission_id="vnext-mission-test-live-composition",
        target=target,
        budget=budget,
        model=model,
        dependencies=deps,
        deadline=lambda: 310.0,
        pause_controller=None,
    )

    session = canary._build_live_session(build)

    assert len(clients) == 2
    assert clients[0] is not clients[1]
    assert [item["max_retries"] for item in calls] == [0, 0]
    runtime = session._runtime
    assert runtime._actor is not runtime._verifier
    assert runtime._controller.mutation_permits is runtime.mutation_permits
    assert {
        tool.name for tool in runtime._catalog.tools
    } == {
        "click",
        "double_click",
        "scroll",
        "type_text",
        "hotkey",
        "claim_obligation_done",
        "blocked",
    }

    session.close()
    assert all(client.closed for client in clients)
    assert runtime.mutation_permits.closed is True
    assert session._registry.closed is True


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

    permits = CloseResource("permits", fail=True)
    registry = CloseResource("registry", fail=False)
    actor_client = _RawClient(close_error="actor close unavailable")
    verifier_client = _RawClient(
        close_error="verifier close unavailable"
    )
    session = canary._LiveCanarySession(
        runtime=object(),
        reducer=object(),
        registry=registry,
        permits=permits,
        authority=FrameAuthority(),
        binding=object(),
        counter=object(),
        clients=(actor_client, verifier_client),
    )

    with pytest.raises(ExceptionGroup) as caught:
        session.close()

    assert permits.calls == registry.calls == 1
    assert actor_client.closed is True
    assert verifier_client.closed is True
    messages = [str(error) for error in caught.value.exceptions]
    assert len(messages) == 3
    assert any("mutation permit authority" in message for message in messages)
    assert any("Visual Actor client" in message for message in messages)
    assert any(
        "Visual ObligationVerifier client" in message
        for message in messages
    )


def test_composition_root_has_no_forbidden_product_imports_or_fallback() -> None:
    source = Path(canary.__file__).read_text(encoding="utf-8")

    assert "default_handlers" not in source
    assert "CapabilityPlanner" not in source
    assert "TaskContractCompiler" not in source
    assert "winpilot.targeting" not in source
    assert "winpilot.inspector" not in source
    assert "winpilot.profiler" not in source
    assert "winpilot.mcp" not in source
    assert "fallback_to_legacy" not in source
    assert source.count("from ..vnext") >= 1
