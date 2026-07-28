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
from winpilot.vnext.evidence_plane import (
    FrameArtifactWriter,
    RuntimeEvidenceJournal,
    RuntimeEvidenceJournalError,
    RuntimeEvidencePlane,
    canonical_sha256,
)
from winpilot.vnext.mission import (
    EvidencePolicy,
    EvidenceRef,
    MissionDefinition,
    MissionReducer,
    MissionStatus,
    ObligationSpec,
    TargetBinding,
)
from winpilot.vnext.runtime import (
    MissionRuntimeResult,
    MutationPermitAuthority,
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
    evidence_root: Path | None = None,
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
        runtime_evidence_root=evidence_root,
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


def test_pre_composition_cancel_seals_unique_empty_evidence_without_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw_constructions: list[bool] = []
    client_calls: list[dict[str, Any]] = []

    def forbidden_raw(*_args: Any, **_kwargs: Any) -> Any:
        raw_constructions.append(True)
        raise AssertionError("RawDevice must not be constructed")

    def forbidden_client(**kwargs: Any) -> Any:
        client_calls.append(kwargs)
        raise AssertionError("model client must not be constructed")

    class CancelBeforeComposition:
        paused = True

        @staticmethod
        def wait_if_paused(stop_event: threading.Event) -> bool:
            stop_event.set()
            return False

    monkeypatch.setattr(canary, "RawDeviceExecutor", forbidden_raw)
    graph = _graph()
    execution = canary.prepare_live_vnext_execution(
        graph,
        _inputs(),
        _config(),
        run_manager=ArpRunManager(),
        _dependencies=_dependencies(
            client_factory=forbidden_client,
            evidence_root=tmp_path,
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
            },
            stop_event,
        )
    finally:
        BUS.remove_sink(events.append)

    assert result.ok is False
    assert result.branch == "canceled"
    assert result.output["runtime_version"] == "vnext"
    assert result.output["no_legacy_fallback"] is True
    assert result.output["model_calls"] == 0
    assert raw_constructions == []
    assert client_calls == []
    sealed = [
        event
        for event in events
        if event["type"] == "runtime_evidence_sealed"
    ]
    assert len(sealed) == 1
    anchor = sealed[0]
    assert anchor["run_id"] == run_id
    assert anchor["journal_event_count"] == 0
    assert anchor["report_status"] == "incomplete"
    assert anchor["no_legacy_fallback"] is True
    report = RuntimeEvidencePlane.rebuild_report(
        tmp_path / run_id,
        anchor["mission_id"],
        run_id,
        expected_journal_head_sha256=anchor[
            "journal_head_sha256"
        ],
    )
    assert canonical_sha256(report.to_dict()) == anchor["report_sha256"]
    assert [
        event["type"]
        for event in events
        if event["type"]
        in {
            "vnext_canary_start",
            "runtime_evidence_sealed",
            "vnext_canary_done",
        }
    ] == [
        "vnext_canary_start",
        "runtime_evidence_sealed",
        "vnext_canary_done",
    ]


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
    assert snapshot["error"] == "vNext Runtime returned blocked"
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


def test_product_failure_detail_is_typed_status_only() -> None:
    assert canary._public_runtime_detail(
        RuntimeStatus.STATE_REJECTED
    ) == (
        "vNext Runtime returned state_rejected"
    )


def test_public_turn_projection_omits_private_verifier_feedback() -> None:
    sentinel = "PRIVATE-SCREEN-SENTINEL"
    target = canary._freeze_prebound_target(
        _graph(),
        _inputs(),
        dependencies=_dependencies(),
    )

    projected = canary._public_turn_snapshot(
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
        snapshot = manager.run_prepared(execution)
    finally:
        BUS.remove_sink(events.append)

    public_wire = json.dumps(
        {"snapshot": snapshot, "events": events},
        sort_keys=True,
    )
    assert sentinel not in public_wire
    assert snapshot["error"] == "vNext Runtime returned state_rejected"
    done = [
        event
        for event in events
        if event["type"] == "vnext_canary_done"
    ]
    assert len(done) == 1
    assert done[0]["detail"] == "vNext Runtime returned state_rejected"


def test_public_result_source_projects_only_safe_runtime_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from winpilot.orchestrator.projection import RunProjectionStore
    from winpilot.recorder import trace

    class PrivateSnapshotSession(_FakeSession):
        def _private_report(self) -> dict[str, Any]:
            actor_envelope = {
                "decision_id": "decision-private",
                "mission_id": self.mission_id,
                "run_id": self.run_id,
                "proposal": {
                    "kind": "desktop_action",
                    "action": "type_text",
                    "args": {"text": PRIVATE_TEXT},
                },
            }
            physical_attempt = {
                "decision_id": "decision-private",
                "action_id": "decision-private",
                "action": "scroll",
                "submitted_arguments": {
                    "image_x": PRIVATE_X,
                    "image_y": PRIVATE_Y,
                    "amount": PRIVATE_AMOUNT,
                },
                "physical_arguments": {
                    "kind": "scroll",
                    "x": PRIVATE_X,
                    "y": PRIVATE_Y,
                    "amount": PRIVATE_AMOUNT,
                },
            }
            return {
                "format": "runtime-evidence-report/1",
                "mission_id": self.mission_id,
                "run_id": self.run_id,
                "status": "complete",
                "actor_decisions": [
                    {
                        "decision_id": "decision-private",
                        "canonical_arguments": {
                            "text": PRIVATE_TEXT,
                            "keys": PRIVATE_CHORD.split("+"),
                        },
                        "actor_envelope": actor_envelope,
                    }
                ],
                "admissions": [],
                "physical_attempts": [physical_attempt],
                "action_bindings": [],
                "frame_artifacts": [],
                "journal_head_sha256": "a" * 64,
                "journal_event_count": 9,
                "missing_evidence": [],
                "verification_failures": [],
            }

        def turn_snapshot(self) -> dict[str, Any]:
            return {
                "mission_revision": 7,
                "mission_status": "verified",
                "target_binding_id": self.target_binding_id,
                "target_hwnd": HWND,
                "frame_id": 9,
                "frame_epoch": self.frame_epoch,
                "frame_evidence_refs": ["e-visual"],
                "action_effect": None,
                "runtime_evidence_report": self._private_report(),
                "canonical_arguments": {"text": PRIVATE_TEXT},
                "physical_arguments": {
                    "x": PRIVATE_X,
                    "y": PRIVATE_Y,
                    "amount": PRIVATE_AMOUNT,
                },
                "hotkey_chord": PRIVATE_CHORD,
            }

        def completion_snapshot(self) -> dict[str, Any]:
            snapshot = super().completion_snapshot()
            snapshot.update(
                {
                    "runtime_evidence_report": self._private_report(),
                    "actor_envelope": self._private_report()[
                        "actor_decisions"
                    ][0]["actor_envelope"],
                    "canonical_arguments": {"text": PRIVATE_TEXT},
                    "physical_arguments": {
                        "x": PRIVATE_X,
                        "y": PRIVATE_Y,
                        "amount": PRIVATE_AMOUNT,
                        "hotkey_chord": PRIVATE_CHORD,
                    },
                    "physical_attempt": self._private_report()[
                        "physical_attempts"
                    ][0],
                }
            )
            return snapshot

        @property
        def runtime_evidence_anchor(self) -> dict[str, Any] | None:
            if not self.closed:
                return None
            return {
                "mission_id": self.mission_id,
                "run_id": self.run_id,
                "journal_head_sha256": "a" * 64,
                "journal_event_count": 9,
                "report_sha256": "b" * 64,
                "report_status": "complete",
                "missing_evidence_count": 0,
                "verification_failure_count": 0,
            }

    session = PrivateSnapshotSession(
        [
            MissionRuntimeResult(
                RuntimeStatus.MISSION_VERIFIED,
                accepted_event_ids=("event-verified",),
            )
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
    trace_root = tmp_path / "public-traces"
    monkeypatch.setattr(trace, "TRACES_DIR", trace_root)
    recorder = trace.TraceRecorder()
    projection = RunProjectionStore()
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        recorder.start(execution.run_id)
        snapshot = manager.run_prepared(execution)
        recorder.stop()
    finally:
        recorder.stop()
        BUS.remove_sink(events.append)
        BUS.remove_sink(projection.on_event)
        BUS.remove_sink(recorder._on_event)

    records = [
        json.loads(line)
        for line in (
            trace_root / f"{execution.run_id}.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    public_projection = projection.get(execution.run_id)
    public_surfaces = {
        "execution": snapshot,
        "bus": events,
        "trace": records,
        "run_projection": public_projection,
    }
    for surface in public_surfaces.values():
        _assert_private_runtime_evidence_absent(surface)

    summary = snapshot["result"]["runtime_evidence"]
    assert set(summary) == _PUBLIC_RUNTIME_EVIDENCE_FIELDS
    assert summary == {
        "available": True,
        "status": "complete",
        "journal_head_sha256": "a" * 64,
        "journal_event_count": 9,
        "report_sha256": "b" * 64,
        "audit_locator": canary._runtime_evidence_audit_locator(
            session.mission_id,
            session.run_id,
        ),
        "parameter_visibility": "private_runtime_evidence",
    }
    assert "/" not in summary["audit_locator"]
    assert "\\" not in summary["audit_locator"]
    assert "evidence.jsonl" not in summary["audit_locator"]
    done = [
        event
        for event in events
        if event["type"] == "vnext_canary_done"
    ]
    assert len(done) == 1
    assert done[0]["runtime_evidence"] == summary


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
        canary._RawReceiptTrace().record(
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
    anchor = {
        **scope,
        "journal_head_sha256": "a" * 64,
        "journal_event_count": 9,
        "report_sha256": "b" * 64,
        "report_status": "complete",
    }

    summary = canary._public_runtime_evidence_summary(
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
        "audit_locator": canary._runtime_evidence_audit_locator(
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
        self.close_calls = 0
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
        self.close_calls += 1
        self.closed = True
        if self.close_error:
            raise RuntimeError(self.close_error)


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


def _scroll_response() -> dict[str, Any]:
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
                                "name": "scroll",
                                "arguments": json.dumps(
                                    {
                                        "image_x": 37,
                                        "image_y": 29,
                                        "amount": -713,
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
    evidence_root: Path,
    clock: list[float] | None = None,
    deadline: float = 310.0,
    install_safe_backends: bool = True,
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
    if install_safe_backends:
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
        runtime_evidence_root=evidence_root,
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
    tmp_path: Path,
) -> None:
    actor_client = _RawClient(_click_response())
    verifier_client = _RawClient()
    session, raw_calls, _factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[_jpeg(639, 480)],
        clients=(actor_client, verifier_client),
        evidence_root=tmp_path,
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


def test_live_session_keeps_verifier_recovery_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session, _raw_calls, _factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[_jpeg(640, 480)],
        clients=(_RawClient(), _RawClient()),
        evidence_root=tmp_path,
        install_safe_backends=False,
    )
    try:
        obligation = session._reducer.definition.obligations[0]

        assert obligation.evidence_policy.max_verification_attempts == 3
        assert obligation.evidence_policy.uncertain_policy == "blocked"
    finally:
        session.close()


def test_post_capture_dimension_mismatch_is_unknown_and_never_replayed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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
        evidence_root=tmp_path,
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
    tmp_path: Path,
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
        evidence_root=tmp_path,
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


def test_completed_action_seals_one_authenticated_evidence_anchor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    actor_client = _RawClient(_click_response())
    verifier_client = _RawClient()
    session, raw_calls, _factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[
            _jpeg(640, 480, level=120),
            _jpeg(640, 480, level=180),
        ],
        clients=(actor_client, verifier_client),
        evidence_root=tmp_path,
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        result = session.run_one_turn(
            turn_index=0,
            stop_event=threading.Event(),
        )
        session.close()
    finally:
        BUS.remove_sink(events.append)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert raw_calls == ["click"]
    sealed = [
        event
        for event in events
        if event["type"] == "runtime_evidence_sealed"
    ]
    assert len(sealed) == 1
    anchor = session.runtime_evidence_anchor
    assert anchor is not None
    assert anchor["journal_event_count"] == 7
    assert anchor["report_status"] == "complete"
    assert anchor["missing_evidence_count"] == 0
    assert anchor["verification_failure_count"] == 0
    assert set(sealed[0]) == {
        "type",
        "ts",
        "runtime_version",
        "no_legacy_fallback",
        "mission_id",
        "run_id",
        "journal_head_sha256",
        "journal_event_count",
        "report_sha256",
        "report_status",
        "missing_evidence_count",
        "verification_failure_count",
    }
    report = RuntimeEvidencePlane.rebuild_report(
        tmp_path / "arp-test-live-frames",
        "vnext-mission-test-live-frames",
        "arp-test-live-frames",
        expected_journal_head_sha256=anchor[
            "journal_head_sha256"
        ],
    )
    assert canonical_sha256(report.to_dict()) == anchor["report_sha256"]
    assert len(report.actor_decisions) == 1
    assert len(report.admissions) == 1
    assert len(report.physical_attempts) == 1
    assert len(report.action_bindings) == 1
    assert len(report.frame_artifacts) == 2
    assert (
        report.action_bindings[0].decision_id
        == report.physical_attempts[0].decision_id
        == report.actor_decisions[0].decision_id
    )


def test_real_composition_scroll_keeps_public_summary_private_and_evidence_exact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from winpilot.action import executor as action_executor

    assert (
        canary.ADMITTED_PHYSICAL_CLICK_BACKEND
        is action_executor.ADMITTED_PHYSICAL_CLICK_BACKEND
    )
    assert (
        canary.ADMITTED_PHYSICAL_SCROLL_BACKEND
        is action_executor.ADMITTED_PHYSICAL_SCROLL_BACKEND
    )
    assert (
        canary.ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND
        is action_executor.ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND
    )
    assert (
        canary.ADMITTED_PHYSICAL_HOTKEY_BACKEND
        is action_executor.ADMITTED_PHYSICAL_HOTKEY_BACKEND
    )

    cursor = [(901, 407)]
    moved: list[tuple[int, int]] = []
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(action_executor, "_dry_run", lambda: False)
    monkeypatch.setattr(
        action_executor,
        "multi_input_cooperation_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        action_executor,
        "_cursor_pos",
        lambda: cursor[0],
    )

    def move_mouse(x: int, y: int) -> None:
        cursor[0] = (x, y)
        moved.append((x, y))

    monkeypatch.setattr(action_executor, "_raw_move_mouse", move_mouse)
    monkeypatch.setattr(
        action_executor,
        "_restore_cursor_after_agent_input",
        lambda _saved, _expected, _hwnd: True,
    )
    monkeypatch.setattr(
        action_executor,
        "_mouse_input",
        lambda flags, **kwargs: (
            "mouse",
            flags,
            tuple(sorted(kwargs.items())),
        ),
    )
    monkeypatch.setattr(
        action_executor,
        "_send_inputs",
        lambda inputs: sent.append(tuple(inputs)),
    )
    monkeypatch.setattr(action_executor.time, "sleep", lambda _seconds: None)
    for legacy_name in (
        "execute_scroll_input",
        "scroll",
        "type_text",
        "press_keys",
    ):
        monkeypatch.setattr(
            action_executor,
            legacy_name,
            lambda *_args, _name=legacy_name, **_kwargs: (_ for _ in ()).throw(
                AssertionError(f"legacy fallback called: {_name}")
            ),
        )

    actor_client = _RawClient(_scroll_response())
    verifier_client = _RawClient()
    session, fake_raw_calls, _factory_calls = _real_session_with_captures(
        monkeypatch,
        images=[
            _jpeg(640, 480, level=120),
            _jpeg(640, 480, level=180),
        ],
        clients=(actor_client, verifier_client),
        evidence_root=tmp_path,
        install_safe_backends=False,
    )
    raw_device = session._runtime._controller._raw
    backends = raw_device._backends
    assert backends.scroll is None
    assert backends.click is None
    assert (
        backends.admitted_physical_click
        is action_executor.ADMITTED_PHYSICAL_CLICK_BACKEND
    )
    assert (
        backends.admitted_physical_scroll
        is action_executor.ADMITTED_PHYSICAL_SCROLL_BACKEND
    )
    assert (
        backends.admitted_physical_type_text
        is action_executor.ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND
    )
    assert (
        backends.admitted_physical_hotkey
        is action_executor.ADMITTED_PHYSICAL_HOTKEY_BACKEND
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        result = session.run_one_turn(
            turn_index=0,
            stop_event=threading.Event(),
        )
        session.close()
    finally:
        BUS.remove_sink(events.append)

    assert result.status is RuntimeStatus.ACTION_RECORDED
    assert fake_raw_calls == []
    assert moved == [(137, 229)]
    assert len(sent) == 1
    public_actions = [
        event
        for event in events
        if event.get("type") == "action"
    ]
    assert len(public_actions) == 1
    assert set(public_actions[0]) == {
        "type",
        "ts",
        "action",
        "input_completed",
        "via",
        "parameter_visibility",
    }
    assert public_actions[0] == {
        "type": "action",
        "ts": public_actions[0]["ts"],
        "action": "scroll",
        "input_completed": True,
        "via": "physical_sendinput",
        "parameter_visibility": "private_runtime_evidence",
    }
    assert not ({"x", "y", "amount"} & set(public_actions[0]))

    anchor = session.runtime_evidence_anchor
    assert anchor is not None
    report = RuntimeEvidencePlane.rebuild_report(
        tmp_path / "arp-test-live-frames",
        "vnext-mission-test-live-frames",
        "arp-test-live-frames",
        expected_journal_head_sha256=anchor["journal_head_sha256"],
    )
    assert len(report.physical_attempts) == 1
    attempt = report.physical_attempts[0].to_dict()
    assert attempt["action"] == "scroll"
    assert attempt["submitted_arguments"] == {
        "image_x": 37,
        "image_y": 29,
        "amount": -713,
    }
    assert attempt["physical_arguments"] == {
        "kind": "wheel",
        "x": 137,
        "y": 229,
        "signed_wheel_amount": -713,
        "event_wheel_delta": -85_560,
    }
    assert len(report.actor_decisions) == 1
    assert len(report.admissions) == 1
    assert len(report.action_bindings) == 1
    assert len(report.frame_artifacts) == 2
    binding = report.action_bindings[0]
    assert binding.raw_action_receipt["input_completed"] is True
    assert binding.physical_attempt_count == 1
    assert (
        binding.decision_id
        == report.physical_attempts[0].decision_id
        == report.admissions[0].decision_id
        == report.actor_decisions[0].decision_id
    )


def _composition_build(
    dependencies: canary._CompositionDependencies,
    *,
    run_id: str,
    mission_id: str,
) -> canary._SessionBuild:
    graph = _graph()
    budget = canary._freeze_budget(_config())
    return canary._SessionBuild(
        graph=graph,
        goal=GOAL,
        run_id=run_id,
        mission_id=mission_id,
        target=canary._freeze_prebound_target(
            graph,
            _inputs(),
            dependencies=dependencies,
        ),
        budget=budget,
        model=canary._freeze_model(_config(), budget),
        dependencies=dependencies,
        deadline=lambda: 310.0,
        pause_controller=None,
    )


def _assert_single_incomplete_anchor(
    events: list[dict[str, Any]],
    root: Path,
    build: canary._SessionBuild,
) -> dict[str, Any]:
    sealed = [
        event
        for event in events
        if event["type"] == "runtime_evidence_sealed"
    ]
    assert len(sealed) == 1
    anchor = sealed[0]
    assert anchor["run_id"] == build.run_id
    assert anchor["mission_id"] == build.mission_id
    assert anchor["journal_event_count"] == 0
    assert anchor["report_status"] == "incomplete"
    report = RuntimeEvidencePlane.rebuild_report(
        root / build.run_id,
        build.mission_id,
        build.run_id,
        expected_journal_head_sha256=anchor[
            "journal_head_sha256"
        ],
    )
    assert canonical_sha256(report.to_dict()) == anchor["report_sha256"]
    return anchor


def test_evidence_setup_failure_precedes_models_and_raw_device(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "arp-test-evidence-setup-failure"
    (tmp_path / run_id).mkdir()
    raw_constructions: list[bool] = []
    client_calls: list[dict[str, Any]] = []
    closed_registries: list[EvidenceRegistry] = []
    original_registry_close = EvidenceRegistry.close

    def tracked_registry_close(registry: EvidenceRegistry) -> None:
        closed_registries.append(registry)
        original_registry_close(registry)

    def forbidden_raw(*_args: Any, **_kwargs: Any) -> Any:
        raw_constructions.append(True)
        raise AssertionError("RawDevice must not be constructed")

    def client_factory(**kwargs: Any) -> _RawClient:
        client_calls.append(kwargs)
        return _RawClient()

    monkeypatch.setattr(canary, "RawDeviceExecutor", forbidden_raw)
    monkeypatch.setattr(
        EvidenceRegistry,
        "close",
        tracked_registry_close,
    )
    deps = _dependencies(
        client_factory=client_factory,
        evidence_root=tmp_path,
    )
    graph = _graph()
    budget = canary._freeze_budget(_config())
    build = canary._SessionBuild(
        graph=graph,
        goal=GOAL,
        run_id=run_id,
        mission_id="vnext-mission-evidence-setup-failure",
        target=canary._freeze_prebound_target(
            graph,
            _inputs(),
            dependencies=deps,
        ),
        budget=budget,
        model=canary._freeze_model(_config(), budget),
        dependencies=deps,
        deadline=lambda: 310.0,
        pause_controller=None,
    )

    with pytest.raises(
        RuntimeEvidenceJournalError,
        match="exclusively created",
    ):
        canary._build_live_session(build)

    assert raw_constructions == []
    assert client_calls == []
    assert closed_registries == []


def test_registry_setup_failure_seals_unique_incomplete_evidence_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "arp-test-registry-setup-failure"
    mission_id = "vnext-mission-registry-setup-failure"
    raw_constructions: list[bool] = []
    client_calls: list[dict[str, Any]] = []
    journal_closes: list[RuntimeEvidenceJournal] = []
    original_journal_close = RuntimeEvidenceJournal.close

    def failed_registry(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("synthetic registry setup failure")

    def tracked_journal_close(journal: RuntimeEvidenceJournal) -> None:
        journal_closes.append(journal)
        original_journal_close(journal)

    def forbidden_raw(*_args: Any, **_kwargs: Any) -> Any:
        raw_constructions.append(True)
        raise AssertionError("RawDevice must not be constructed")

    def client_factory(**kwargs: Any) -> _RawClient:
        client_calls.append(kwargs)
        return _RawClient()

    monkeypatch.setattr(canary, "EvidenceRegistry", failed_registry)
    monkeypatch.setattr(canary, "RawDeviceExecutor", forbidden_raw)
    monkeypatch.setattr(
        RuntimeEvidenceJournal,
        "close",
        tracked_journal_close,
    )
    deps = _dependencies(
        client_factory=client_factory,
        evidence_root=tmp_path,
    )
    graph = _graph()
    budget = canary._freeze_budget(_config())
    build = canary._SessionBuild(
        graph=graph,
        goal=GOAL,
        run_id=run_id,
        mission_id=mission_id,
        target=canary._freeze_prebound_target(
            graph,
            _inputs(),
            dependencies=deps,
        ),
        budget=budget,
        model=canary._freeze_model(_config(), budget),
        dependencies=deps,
        deadline=lambda: 310.0,
        pause_controller=None,
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        with pytest.raises(
            RuntimeError,
            match="synthetic registry setup failure",
        ):
            canary._build_live_session(build)
    finally:
        BUS.remove_sink(events.append)

    assert len(journal_closes) == 1
    assert raw_constructions == []
    assert client_calls == []
    sealed = [
        event
        for event in events
        if event["type"] == "runtime_evidence_sealed"
    ]
    assert len(sealed) == 1
    anchor = sealed[0]
    assert anchor["run_id"] == run_id
    assert anchor["mission_id"] == mission_id
    assert anchor["journal_event_count"] == 0
    assert anchor["report_status"] == "incomplete"
    report = RuntimeEvidencePlane.rebuild_report(
        tmp_path / run_id,
        mission_id,
        run_id,
        expected_journal_head_sha256=anchor[
            "journal_head_sha256"
        ],
    )
    assert canonical_sha256(report.to_dict()) == anchor["report_sha256"]


def test_pre_controller_composition_failure_seals_evidence_and_closes_registry_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    closed_registries: list[EvidenceRegistry] = []
    raw_constructions: list[bool] = []
    client_calls: list[dict[str, Any]] = []
    original_registry_close = EvidenceRegistry.close

    def tracked_registry_close(registry: EvidenceRegistry) -> None:
        closed_registries.append(registry)
        original_registry_close(registry)

    def failed_reducer(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("synthetic reducer setup failure")

    def forbidden_raw(*_args: Any, **_kwargs: Any) -> Any:
        raw_constructions.append(True)
        raise AssertionError("RawDevice must not be constructed")

    def client_factory(**kwargs: Any) -> _RawClient:
        client_calls.append(kwargs)
        return _RawClient()

    monkeypatch.setattr(
        EvidenceRegistry,
        "close",
        tracked_registry_close,
    )
    monkeypatch.setattr(canary, "MissionReducer", failed_reducer)
    monkeypatch.setattr(canary, "RawDeviceExecutor", forbidden_raw)
    deps = _dependencies(
        client_factory=client_factory,
        evidence_root=tmp_path,
    )
    graph = _graph()
    budget = canary._freeze_budget(_config())
    build = canary._SessionBuild(
        graph=graph,
        goal=GOAL,
        run_id="arp-test-pre-evidence-failure",
        mission_id="vnext-mission-pre-evidence-failure",
        target=canary._freeze_prebound_target(
            graph,
            _inputs(),
            dependencies=deps,
        ),
        budget=budget,
        model=canary._freeze_model(_config(), budget),
        dependencies=deps,
        deadline=lambda: 310.0,
        pause_controller=None,
    )

    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        with pytest.raises(
            RuntimeError,
            match="synthetic reducer setup failure",
        ):
            canary._build_live_session(build)
    finally:
        BUS.remove_sink(events.append)

    assert len(closed_registries) == 1
    assert closed_registries[0].closed is True
    assert raw_constructions == []
    assert client_calls == []
    sealed = [
        event
        for event in events
        if event["type"] == "runtime_evidence_sealed"
    ]
    assert len(sealed) == 1
    anchor = sealed[0]
    assert anchor["run_id"] == build.run_id
    assert anchor["mission_id"] == build.mission_id
    assert anchor["journal_event_count"] == 0
    assert anchor["report_status"] == "incomplete"
    report = RuntimeEvidencePlane.rebuild_report(
        tmp_path / build.run_id,
        build.mission_id,
        build.run_id,
        expected_journal_head_sha256=anchor[
            "journal_head_sha256"
        ],
    )
    assert canonical_sha256(report.to_dict()) == anchor["report_sha256"]


def test_changed_target_after_explicit_start_seals_unique_incomplete_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    current_target = [_facts()]
    raw_constructions: list[bool] = []
    client_calls: list[dict[str, Any]] = []

    def forbidden_raw(*_args: Any, **_kwargs: Any) -> Any:
        raw_constructions.append(True)
        raise AssertionError("RawDevice must not be constructed")

    def forbidden_client(**kwargs: Any) -> Any:
        client_calls.append(kwargs)
        raise AssertionError("model client must not be constructed")

    dependencies = canary._CompositionDependencies(
        wall_clock=lambda: NOW_MS / 1000.0,
        monotonic=lambda: 10.0,
        target_reader=lambda _hwnd: current_target[0],
        hit_test_root=lambda _x, _y: HWND,
        client_factory=forbidden_client,
        runtime_evidence_root=tmp_path,
    )
    graph = _graph()
    execution = canary.prepare_live_vnext_execution(
        graph,
        _inputs(),
        _config(),
        run_manager=ArpRunManager(),
        _dependencies=dependencies,
    )
    current_target[0] = _facts(process_id=PID + 1)
    run_id = "arp-test-changed-target-after-start"
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(canary, "RawDeviceExecutor", forbidden_raw)
    BUS.add_sink(events.append)
    try:
        result = execution.handlers[ArpNodeKind.AGENT_GOAL](
            next(node for node in graph.nodes if node.id == "full-goal"),
            {"run_id": run_id},
            threading.Event(),
        )
    finally:
        BUS.remove_sink(events.append)

    assert result.ok is False
    assert result.branch == "fail"
    assert result.output["runtime_version"] == "vnext"
    assert result.output["no_legacy_fallback"] is True
    assert result.output["model_calls"] == 0
    assert raw_constructions == []
    assert client_calls == []
    selected_events = [
        event
        for event in events
        if event["type"]
        in {
            "vnext_canary_start",
            "runtime_evidence_sealed",
            "vnext_canary_done",
        }
    ]
    assert [event["type"] for event in selected_events] == [
        "vnext_canary_start",
        "runtime_evidence_sealed",
        "vnext_canary_done",
    ]
    anchor = selected_events[1]
    assert anchor["run_id"] == run_id
    assert anchor["journal_event_count"] == 0
    assert anchor["report_status"] == "incomplete"
    report = RuntimeEvidencePlane.rebuild_report(
        tmp_path / run_id,
        anchor["mission_id"],
        run_id,
        expected_journal_head_sha256=anchor[
            "journal_head_sha256"
        ],
    )
    assert canonical_sha256(report.to_dict()) == anchor["report_sha256"]


def test_expired_target_before_runtime_composition_seals_incomplete_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clock = [NOW_MS / 1000.0]
    raw_constructions: list[bool] = []
    client_calls: list[dict[str, Any]] = []

    def forbidden_raw(*_args: Any, **_kwargs: Any) -> Any:
        raw_constructions.append(True)
        raise AssertionError("RawDevice must not be constructed")

    def forbidden_client(**kwargs: Any) -> Any:
        client_calls.append(kwargs)
        raise AssertionError("model client must not be constructed")

    dependencies = canary._CompositionDependencies(
        wall_clock=lambda: clock[0],
        monotonic=lambda: 10.0,
        target_reader=lambda _hwnd: _facts(),
        hit_test_root=lambda _x, _y: HWND,
        client_factory=forbidden_client,
        runtime_evidence_root=tmp_path,
    )
    build = _composition_build(
        dependencies,
        run_id="arp-test-expired-target-after-start",
        mission_id="vnext-mission-expired-target-after-start",
    )
    clock[0] += (canary._TARGET_MAX_AGE_MS + 1) / 1000.0
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(canary, "RawDeviceExecutor", forbidden_raw)
    BUS.add_sink(events.append)
    try:
        with pytest.raises(
            RuntimeError,
            match="target expired before vNext execution",
        ):
            canary._build_live_session(build)
    finally:
        BUS.remove_sink(events.append)

    assert raw_constructions == []
    assert client_calls == []
    _assert_single_incomplete_anchor(events, tmp_path, build)


def test_controller_setup_failure_seals_and_closes_all_created_owners_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw_calls: list[str] = []
    client_calls: list[dict[str, Any]] = []
    journal_closes: list[RuntimeEvidenceJournal] = []
    registry_closes: list[EvidenceRegistry] = []
    permit_closes: list[MutationPermitAuthority] = []
    original_journal_close = RuntimeEvidenceJournal.close
    original_registry_close = EvidenceRegistry.close
    original_permit_close = MutationPermitAuthority.close

    def failed_controller(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("synthetic controller setup failure")

    def forbidden_client(**kwargs: Any) -> _RawClient:
        client_calls.append(kwargs)
        return _RawClient()

    def tracked_journal_close(journal: RuntimeEvidenceJournal) -> None:
        journal_closes.append(journal)
        original_journal_close(journal)

    def tracked_registry_close(registry: EvidenceRegistry) -> None:
        registry_closes.append(registry)
        original_registry_close(registry)

    def tracked_permit_close(permits: MutationPermitAuthority) -> None:
        permit_closes.append(permits)
        original_permit_close(permits)

    _install_safe_backends(monkeypatch, raw_calls)
    monkeypatch.setattr(canary, "AtomicDesktopAdapter", failed_controller)
    monkeypatch.setattr(
        RuntimeEvidenceJournal,
        "close",
        tracked_journal_close,
    )
    monkeypatch.setattr(
        EvidenceRegistry,
        "close",
        tracked_registry_close,
    )
    monkeypatch.setattr(
        MutationPermitAuthority,
        "close",
        tracked_permit_close,
    )
    dependencies = _dependencies(
        client_factory=forbidden_client,
        evidence_root=tmp_path,
    )
    build = _composition_build(
        dependencies,
        run_id="arp-test-controller-setup-failure",
        mission_id="vnext-mission-controller-setup-failure",
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        with pytest.raises(
            RuntimeError,
            match="synthetic controller setup failure",
        ):
            canary._build_live_session(build)
    finally:
        BUS.remove_sink(events.append)

    assert raw_calls == []
    assert client_calls == []
    assert len(journal_closes) == 1
    assert len(registry_closes) == 1
    assert registry_closes[0].closed is True
    assert len(permit_closes) == 1
    assert permit_closes[0].closed is True
    _assert_single_incomplete_anchor(events, tmp_path, build)


@pytest.mark.parametrize(
    "failure_stage,expected_clients",
    (
        ("second_model_client", 1),
        ("mission_runtime", 2),
        ("live_session", 2),
    ),
)
def test_model_runtime_session_setup_failure_seals_and_closes_once(
    failure_stage: str,
    expected_clients: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw_calls: list[str] = []
    clients: list[_RawClient] = []
    journal_closes: list[RuntimeEvidenceJournal] = []
    registry_closes: list[EvidenceRegistry] = []
    permit_closes: list[MutationPermitAuthority] = []
    original_journal_close = RuntimeEvidenceJournal.close
    original_registry_close = EvidenceRegistry.close
    original_permit_close = MutationPermitAuthority.close
    error_text = f"synthetic {failure_stage} setup failure"

    def factory(**_kwargs: Any) -> _RawClient:
        if failure_stage == "second_model_client" and clients:
            raise RuntimeError(error_text)
        client = _RawClient()
        clients.append(client)
        return client

    def failed_owner(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(error_text)

    def tracked_journal_close(journal: RuntimeEvidenceJournal) -> None:
        journal_closes.append(journal)
        original_journal_close(journal)

    def tracked_registry_close(registry: EvidenceRegistry) -> None:
        registry_closes.append(registry)
        original_registry_close(registry)

    def tracked_permit_close(permits: MutationPermitAuthority) -> None:
        permit_closes.append(permits)
        original_permit_close(permits)

    _install_safe_backends(monkeypatch, raw_calls)
    if failure_stage == "mission_runtime":
        monkeypatch.setattr(canary, "MissionRuntime", failed_owner)
    elif failure_stage == "live_session":
        monkeypatch.setattr(canary, "_LiveCanarySession", failed_owner)
    monkeypatch.setattr(
        RuntimeEvidenceJournal,
        "close",
        tracked_journal_close,
    )
    monkeypatch.setattr(
        EvidenceRegistry,
        "close",
        tracked_registry_close,
    )
    monkeypatch.setattr(
        MutationPermitAuthority,
        "close",
        tracked_permit_close,
    )
    dependencies = _dependencies(
        client_factory=factory,
        evidence_root=tmp_path,
    )
    build = _composition_build(
        dependencies,
        run_id=f"arp-test-{failure_stage}-failure",
        mission_id=f"vnext-mission-{failure_stage}-failure",
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        with pytest.raises(RuntimeError, match=error_text):
            canary._build_live_session(build)
    finally:
        BUS.remove_sink(events.append)

    assert raw_calls == []
    assert len(clients) == expected_clients
    assert all(client.closed for client in clients)
    assert all(client.close_calls == 1 for client in clients)
    assert len(journal_closes) == 1
    assert len(registry_closes) == 1
    assert registry_closes[0].closed is True
    assert len(permit_closes) == 1
    assert permit_closes[0].closed is True
    _assert_single_incomplete_anchor(events, tmp_path, build)


def test_real_composition_builds_independent_zero_retry_roles_and_closes(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    clients: list[_RawClient] = []

    def factory(**kwargs: Any) -> _RawClient:
        calls.append(kwargs)
        client = _RawClient()
        clients.append(client)
        return client

    deps = _dependencies(
        client_factory=factory,
        evidence_root=tmp_path,
    )
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

    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        session = canary._build_live_session(build)

        assert len(clients) == 2
        assert clients[0] is not clients[1]
        assert [item["max_retries"] for item in calls] == [0, 0]
        runtime = session._runtime
        assert runtime._actor is not runtime._verifier
        assert runtime._controller.mutation_permits is runtime.mutation_permits
        assert runtime._runtime_evidence is session._runtime_evidence
        assert (
            runtime._controller.runtime_evidence_plane
            is session._runtime_evidence
        )
        assert (
            runtime._controller._frame_artifacts
            is session._frame_artifacts
        )
        assert (
            session._runtime_evidence.journal
            is session._evidence_journal
        )
        assert (
            session._frame_artifacts.run_directory
            == session._evidence_journal.run_directory
        )
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
        session.close()
    finally:
        BUS.remove_sink(events.append)
    assert all(client.closed for client in clients)
    assert runtime.mutation_permits.closed is True
    assert session._registry.closed is True
    sealed = [
        event
        for event in events
        if event["type"] == "runtime_evidence_sealed"
    ]
    assert len(sealed) == 1
    anchor = session.runtime_evidence_anchor
    assert anchor is not None
    assert {
        key: value
        for key, value in sealed[0].items()
        if key != "ts"
    } == {
        "type": "runtime_evidence_sealed",
        "runtime_version": "vnext",
        "no_legacy_fallback": True,
        **anchor,
    }
    assert sealed[0]["run_id"] == build.run_id
    assert sealed[0]["mission_id"] == build.mission_id
    assert sealed[0]["journal_event_count"] == 0
    assert sealed[0]["report_status"] == "incomplete"


def test_session_rejects_mismatched_runtime_evidence_resources(
    tmp_path: Path,
) -> None:
    plane, _writer, journal = _runtime_evidence_resources(
        tmp_path,
        suffix="scope-a",
    )
    _other_plane, other_writer, other_journal = (
        _runtime_evidence_resources(
            tmp_path,
            suffix="scope-b",
        )
    )
    try:
        with pytest.raises(
            ValueError,
            match="must share one run scope",
        ):
            canary._LiveCanarySession(
                runtime=object(),
                reducer=object(),
                registry=object(),
                permits=object(),
                authority=FrameAuthority(),
                binding=object(),
                counter=object(),
                clients=(object(), object()),
                runtime_evidence=plane,
                frame_artifacts=other_writer,
                evidence_journal=journal,
            )
    finally:
        journal.close()
        other_journal.close()


def test_evidence_rebuild_failure_closes_journal_once_without_leaking(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plane, writer, journal = _runtime_evidence_resources(
        tmp_path,
        suffix="seal-failure",
    )
    permits = MutationPermitAuthority(plane.mission_id, plane.run_id)
    registry = EvidenceRegistry(plane.mission_id, plane.run_id)
    actor_client = _RawClient()
    verifier_client = _RawClient()
    session = canary._LiveCanarySession(
        runtime=object(),
        reducer=object(),
        registry=registry,
        permits=permits,
        authority=FrameAuthority(),
        binding=object(),
        counter=object(),
        clients=(actor_client, verifier_client),
        runtime_evidence=plane,
        frame_artifacts=writer,
        evidence_journal=journal,
    )
    sentinel = "PRIVATE_MODEL_RESPONSE_OR_TYPED_TEXT"
    close_calls: list[RuntimeEvidenceJournal] = []
    original_close = RuntimeEvidenceJournal.close

    def tracked_close(current: RuntimeEvidenceJournal) -> None:
        if current is journal:
            close_calls.append(current)
        original_close(current)

    def failed_rebuild(
        _cls: type[RuntimeEvidencePlane],
        *_args: Any,
        **_kwargs: Any,
    ) -> Any:
        raise RuntimeError(sentinel)

    monkeypatch.setattr(
        RuntimeEvidenceJournal,
        "close",
        tracked_close,
    )
    monkeypatch.setattr(
        RuntimeEvidencePlane,
        "rebuild_report",
        classmethod(failed_rebuild),
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        with pytest.raises(ExceptionGroup) as caught:
            session.close()
        session.close()
    finally:
        BUS.remove_sink(events.append)

    assert close_calls == [journal]
    assert permits.closed is True
    assert registry.closed is True
    assert actor_client.closed is True
    assert verifier_client.closed is True
    assert session.runtime_evidence_anchor is None
    assert not any(
        event["type"] == "runtime_evidence_sealed"
        for event in events
    )
    assert sentinel not in json.dumps(events, sort_keys=True)
    messages = [str(error) for error in caught.value.exceptions]
    assert messages == ["runtime evidence seal failed"]
    assert sentinel not in json.dumps(messages)


def test_verified_session_fails_closed_on_incomplete_evidence_report(
    tmp_path: Path,
) -> None:
    plane, writer, journal = _runtime_evidence_resources(
        tmp_path,
        suffix="verified-incomplete",
    )
    permits = MutationPermitAuthority(plane.mission_id, plane.run_id)
    registry = EvidenceRegistry(plane.mission_id, plane.run_id)
    actor_client = _RawClient()
    verifier_client = _RawClient()
    reducer = SimpleNamespace(
        projection=SimpleNamespace(status=MissionStatus.VERIFIED)
    )
    session = canary._LiveCanarySession(
        runtime=object(),
        reducer=reducer,
        registry=registry,
        permits=permits,
        authority=FrameAuthority(),
        binding=object(),
        counter=object(),
        clients=(actor_client, verifier_client),
        runtime_evidence=plane,
        frame_artifacts=writer,
        evidence_journal=journal,
    )
    events: list[dict[str, Any]] = []
    BUS.add_sink(events.append)
    try:
        with pytest.raises(
            ExceptionGroup,
            match="session cleanup failed",
        ):
            session.close()
        session.close()
    finally:
        BUS.remove_sink(events.append)

    sealed = [
        event
        for event in events
        if event["type"] == "runtime_evidence_sealed"
    ]
    assert len(sealed) == 1
    assert sealed[0]["report_status"] == "incomplete"
    assert sealed[0]["missing_evidence_count"] > 0
    assert session.runtime_evidence_anchor is not None
    assert permits.closed is True
    assert registry.closed is True
    assert actor_client.closed is True
    assert verifier_client.closed is True


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
