"""Read-only target discovery sidecar tests for MissionRuntime."""
from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from threading import Event
from typing import Any, Mapping

import pytest

from winpilot.vnext.actor import ACTOR_PROPOSAL_FORMAT
from winpilot.vnext.capabilities import (
    AuthoritativeFrameState,
    BrokerRuntimeState,
    CapabilityBroker,
    MutationKind,
    TargetState,
    ToolCatalog,
    ToolRoute,
    ToolSpec,
)
from winpilot.vnext.evidence import EvidenceRegistry
from winpilot.vnext.mission import (
    MissionDefinition,
    MissionReducer,
    ObligationSpec,
)
from winpilot.vnext.profiling import ExecutionPolicy
from winpilot.vnext.runtime import (
    MissionRuntime,
    MutationPermitAuthority,
    RuntimeFrame,
    RuntimeSnapshot,
    RuntimeStatus,
)
from winpilot.vnext.targeting import (
    ACTIVATE_TARGET_TOOL_NAME,
    DISCOVER_TARGET_TOOL_NAME,
    TargetActivation,
    TargetCandidate,
    TargetDiscoverySnapshot,
    TargetState as CandidateState,
    activate_target_tool_schema,
    candidate_ref_for,
    discover_target_tool_schema,
    launch_identity_for,
)


class _Actor:
    def __init__(self, *responses: Mapping[str, Any]) -> None:
        self._responses = list(responses)
        self.turns = []

    def propose(self, turn, **_kwargs):
        self.turns.append(turn)
        return dict(self._responses.pop(0))


class _State:
    def __init__(self, snapshots: Mapping[int, RuntimeSnapshot]) -> None:
        self.snapshots = dict(snapshots)
        self.current_override: RuntimeSnapshot | None = None
        self.observe_calls = []
        self.current_calls = []

    def observe(self, _projection, *, turn_index: int, image_region=None):
        self.observe_calls.append((turn_index, image_region))
        return self.snapshots[turn_index]

    def current(self, _projection, *, turn_index: int):
        self.current_calls.append(turn_index)
        return self.current_override or self.snapshots[turn_index]


class _Controller:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, envelope, **_kwargs):
        self.calls.append(envelope)
        raise AssertionError("read-only target discovery reached Controller")


class _Discovery:
    def __init__(self, snapshot: TargetDiscoverySnapshot, *, hook=None) -> None:
        self.snapshot = snapshot
        self.hook = hook
        self.calls = []

    def discover(self, query: str, *, stop_event=None) -> TargetDiscoverySnapshot:
        self.calls.append((query, stop_event))
        if self.hook is not None:
            self.hook()
        return self.snapshot


def _definition() -> MissionDefinition:
    return MissionDefinition(
        mission_id="mission-target-discovery",
        run_id="run-target-discovery",
        original_goal="Open the requested desktop application",
        obligations=(
            ObligationSpec(
                id="open-target",
                desired_state="the requested application is ready",
                acceptance_criteria=("the requested application is visible",),
                required_capabilities=("visual.interact",),
            ),
        ),
    )


def _frame(frame_id: int) -> RuntimeFrame:
    image_b64 = base64.b64encode(f"desktop-{frame_id}".encode()).decode("ascii")
    return RuntimeFrame(
        frame_id=frame_id,
        epoch=0,
        hwnd=1,
        region=(0, 0, 1280, 720),
        scope="full_desktop",
        submitted_size=(1280, 720),
        image_b64=image_b64,
        image_sha256=hashlib.sha256(image_b64.encode("ascii")).hexdigest(),
    )


def _snapshot(frame_id: int, *, turn_index: int) -> RuntimeSnapshot:
    frame = _frame(frame_id)
    return RuntimeSnapshot(
        broker_state=BrokerRuntimeState(
            turn_index=turn_index,
            available_capabilities=("visual.interact",),
            target=TargetState(),
            frame=AuthoritativeFrameState(
                frame_id=frame.frame_id,
                hwnd=frame.hwnd,
                epoch=frame.epoch,
                scope=frame.scope,
                authoritative=True,
                fresh=True,
            ),
        ),
        frame=frame,
        target=None,
    )


def _candidate() -> TargetCandidate:
    target = r"C:\Apps\Orbit.exe"
    return TargetCandidate(
        candidate_ref=candidate_ref_for(
            activation=TargetActivation.OPEN_APP,
            identity=launch_identity_for(kind="exe", target=target),
        ),
        activation=TargetActivation.OPEN_APP,
        state=CandidateState.INSTALLED,
        label="Orbit",
        provenance=("installed_catalog",),
        launch_kind="exe",
        launch_target=target,
    )


def _catalog() -> ToolCatalog:
    common = (
        "target_unbound",
        "authoritative_full_desktop_frame",
        "pointer_released",
        "runtime_healthy",
    )
    return ToolCatalog((
        ToolSpec(
            DISCOVER_TARGET_TOOL_NAME,
            discover_target_tool_schema(),
            ToolRoute.VISUAL_ACTOR,
            MutationKind.NONE,
            required_any_capabilities=("visual.interact",),
            prerequisites=common,
            order=1,
        ),
        ToolSpec(
            ACTIVATE_TARGET_TOOL_NAME,
            activate_target_tool_schema(),
            ToolRoute.VISUAL_ACTOR,
            MutationKind.GUI,
            required_any_capabilities=("visual.interact",),
            prerequisites=(*common, "target_candidates_available"),
            order=2,
        ),
    ))


def _discover(query: str = "Orbit") -> dict[str, Any]:
    return {
        "format": ACTOR_PROPOSAL_FORMAT,
        "kind": "discover_target",
        "query": query,
    }


def _activate(ordinal: int = 1) -> dict[str, Any]:
    return {
        "format": ACTOR_PROPOSAL_FORMAT,
        "kind": "desktop_action",
        "action": ACTIVATE_TARGET_TOOL_NAME,
        "args": {"candidate_ordinal": ordinal},
        "local_intent": "make the selected application available",
    }


def _runtime(
    *,
    actor: _Actor,
    state: _State,
    discovery: _Discovery | None,
):
    definition = _definition()
    evidence = EvidenceRegistry(definition.mission_id, definition.run_id)
    reducer = MissionReducer(definition, evidence=evidence)
    catalog = _catalog()
    permits = MutationPermitAuthority(definition.mission_id, definition.run_id)
    controller = _Controller()
    profile = ExecutionPolicy().safe_default(
        definition.mission_id,
        definition.goal_revision,
        available_capabilities=("visual.interact",),
    )
    runtime = MissionRuntime(
        reducer=reducer,
        profile=profile,
        broker=CapabilityBroker(catalog),
        catalog=catalog,
        actor=actor,
        state=state,
        controller=controller,
        target_discovery=discovery,
        mutation_permits=permits,
    )
    return runtime, reducer, evidence, permits, controller


def test_catalog_discovery_requires_an_injected_read_only_port() -> None:
    with pytest.raises(TypeError, match="requires one read-only"):
        _runtime(
            actor=_Actor(_discover()),
            state=_State({0: _snapshot(10, turn_index=0)}),
            discovery=None,
        )


def test_discovery_snapshot_rejects_more_than_sixteen_notices() -> None:
    with pytest.raises(ValueError, match="notices"):
        TargetDiscoverySnapshot(
            query="Orbit",
            candidates=(),
            captured_at=1.0,
            notices=tuple(f"notice_{index}" for index in range(17)),
        )


def test_discovery_projects_one_turn_choices_without_mutation_or_mission_write() -> None:
    discovery_snapshot = TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(_candidate(),),
        captured_at=1.0,
    )
    discovery = _Discovery(discovery_snapshot)
    actor = _Actor(_discover(), _activate())
    state = _State({
        0: _snapshot(10, turn_index=0),
        1: _snapshot(11, turn_index=1),
    })
    runtime, reducer, evidence, permits, controller = _runtime(
        actor=actor,
        state=state,
        discovery=discovery,
    )
    initial_revision = reducer.projection.revision

    first = runtime.run_one_turn(turn_index=0)
    second = runtime.run_one_turn(turn_index=1)

    assert first.status is RuntimeStatus.OBSERVED
    assert first.accepted_event_ids == ()
    assert second.status is RuntimeStatus.UNAVAILABLE
    assert second.accepted_event_ids == ()
    assert [query for query, _stop in discovery.calls] == ["Orbit"]
    assert reducer.projection.revision == initial_revision
    assert reducer.projection.accepted_event_ids == ()
    assert evidence.snapshot() == ()
    assert controller.calls == []
    assert permits.pending_count == permits.active_count == permits.consumed_count == 0

    second_turn = actor.turns[1]
    assert [choice.model_dump() for choice in second_turn.target_choices] == [{
        "ordinal": 1,
        "label": "Orbit",
        "state": "installed",
        "window_title": None,
    }]
    assert tuple(tool.name for tool in second_turn.tools) == (
        DISCOVER_TARGET_TOOL_NAME,
        ACTIVATE_TARGET_TOOL_NAME,
    )
    runtime.discard_ephemeral_state()


def test_empty_discovery_projects_one_turn_identity_free_feedback() -> None:
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(),
        captured_at=1.0,
        notices=("installed_catalog_unavailable",),
    ))
    actor = _Actor(_discover(), _discover())
    state = _State({
        0: _snapshot(10, turn_index=0),
        1: _snapshot(11, turn_index=1),
    })
    runtime, reducer, evidence, permits, controller = _runtime(
        actor=actor,
        state=state,
        discovery=discovery,
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    assert runtime.run_one_turn(turn_index=1).status is RuntimeStatus.OBSERVED

    second_turn = actor.turns[1]
    assert second_turn.target_discovery_feedback is not None
    assert second_turn.target_discovery_feedback.model_dump(mode="json") == {
        "format": "actor-target-discovery-feedback/0",
        "query": "Orbit",
        "outcome": "no_candidates",
    }
    assert second_turn.target_choices == ()
    assert tuple(tool.name for tool in second_turn.tools) == (
        DISCOVER_TARGET_TOOL_NAME,
    )
    assert reducer.projection.accepted_event_ids == ()
    assert evidence.snapshot() == ()
    assert controller.calls == []
    assert permits.pending_count == permits.active_count == 0
    runtime.discard_ephemeral_state()


def test_empty_discovery_feedback_is_consumed_exactly_once() -> None:
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(),
        captured_at=1.0,
    ))
    actor = _Actor(_discover(), _activate(), _activate())
    state = _State({
        0: _snapshot(10, turn_index=0),
        1: _snapshot(11, turn_index=1),
        2: _snapshot(12, turn_index=2),
    })
    runtime, _reducer, _evidence, permits, controller = _runtime(
        actor=actor,
        state=state,
        discovery=discovery,
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    assert runtime.run_one_turn(turn_index=1).status is RuntimeStatus.UNAVAILABLE
    assert runtime.run_one_turn(turn_index=2).status is RuntimeStatus.UNAVAILABLE

    assert actor.turns[1].target_discovery_feedback is not None
    assert actor.turns[2].target_discovery_feedback is None
    assert discovery.calls == [("Orbit", None)]
    assert controller.calls == []
    assert permits.pending_count == permits.active_count == 0
    runtime.discard_ephemeral_state()


def test_empty_discovery_feedback_expires_when_next_turn_is_skipped() -> None:
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(),
        captured_at=1.0,
    ))
    actor = _Actor(_discover(), _activate())
    state = _State({
        0: _snapshot(10, turn_index=0),
        2: _snapshot(12, turn_index=2),
    })
    runtime, _reducer, _evidence, _permits, controller = _runtime(
        actor=actor,
        state=state,
        discovery=discovery,
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    assert runtime.run_one_turn(turn_index=2).status is RuntimeStatus.UNAVAILABLE
    assert actor.turns[1].target_discovery_feedback is None
    assert controller.calls == []
    runtime.discard_ephemeral_state()


def test_empty_discovery_feedback_expires_after_mission_revision_change() -> None:
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(),
        captured_at=1.0,
    ))
    actor = _Actor(_discover(), _activate())
    state = _State({
        0: _snapshot(10, turn_index=0),
        1: _snapshot(11, turn_index=1),
    })
    runtime, reducer, _evidence, _permits, controller = _runtime(
        actor=actor,
        state=state,
        discovery=discovery,
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    reducer.apply(reducer.event(
        "obligation_blocked",
        payload={
            "obligation_id": "open-target",
            "blocker_kind": "external_state_changed",
            "reason": "accepted Mission state changed",
        },
    ))
    reducer.apply(reducer.event(
        "obligation_unblocked",
        payload={"obligation_id": "open-target"},
    ))

    assert runtime.run_one_turn(turn_index=1).status is RuntimeStatus.UNAVAILABLE
    assert actor.turns[1].target_discovery_feedback is None
    assert controller.calls == []
    runtime.discard_ephemeral_state()


def test_discard_ephemeral_state_removes_empty_discovery_feedback() -> None:
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(),
        captured_at=1.0,
    ))
    actor = _Actor(_discover(), _activate())
    state = _State({
        0: _snapshot(10, turn_index=0),
        1: _snapshot(11, turn_index=1),
    })
    runtime, _reducer, _evidence, _permits, controller = _runtime(
        actor=actor,
        state=state,
        discovery=discovery,
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    runtime.discard_ephemeral_state()
    assert runtime.run_one_turn(turn_index=1).status is RuntimeStatus.UNAVAILABLE
    assert actor.turns[1].target_discovery_feedback is None
    assert controller.calls == []
    runtime.discard_ephemeral_state()


def test_pending_candidates_expire_when_the_immediate_next_turn_is_skipped() -> None:
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(_candidate(),),
        captured_at=1.0,
    ))
    actor = _Actor(_discover(), _discover())
    state = _State({
        0: _snapshot(10, turn_index=0),
        2: _snapshot(12, turn_index=2),
    })
    runtime, _reducer, _evidence, permits, controller = _runtime(
        actor=actor,
        state=state,
        discovery=discovery,
    )

    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    assert runtime.run_one_turn(turn_index=2).status is RuntimeStatus.OBSERVED
    assert actor.turns[1].target_choices == ()
    assert tuple(tool.name for tool in actor.turns[1].tools) == (
        DISCOVER_TARGET_TOOL_NAME,
    )
    assert controller.calls == []
    assert permits.pending_count == permits.consumed_count == 0
    runtime.discard_ephemeral_state()


def test_candidate_authority_drift_rejects_before_the_second_actor_request() -> None:
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(_candidate(),),
        captured_at=1.0,
    ))
    actor = _Actor(_discover(), _activate())
    state = _State({
        0: _snapshot(10, turn_index=0),
        1: _snapshot(11, turn_index=1),
    })
    runtime, _reducer, _evidence, permits, controller = _runtime(
        actor=actor,
        state=state,
        discovery=discovery,
    )
    assert runtime.run_one_turn(turn_index=0).status is RuntimeStatus.OBSERVED
    state.current_override = _snapshot(12, turn_index=1)

    result = runtime.run_one_turn(turn_index=1)

    assert result.status is RuntimeStatus.STATE_REJECTED
    assert len(actor.turns) == 1
    assert controller.calls == []
    assert permits.pending_count == permits.consumed_count == 0
    runtime.discard_ephemeral_state()


def test_discovery_rejects_a_snapshot_for_a_different_query() -> None:
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Different application",
        candidates=(_candidate(),),
        captured_at=1.0,
    ))
    actor = _Actor(_discover("Orbit"))
    runtime, reducer, evidence, permits, controller = _runtime(
        actor=actor,
        state=_State({0: _snapshot(10, turn_index=0)}),
        discovery=discovery,
    )

    result = runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.STATE_REJECTED
    assert "query" in result.detail
    assert runtime._pending_target_discovery is None
    assert reducer.projection.accepted_event_ids == ()
    assert evidence.snapshot() == ()
    assert permits.pending_count == 0
    assert controller.calls == []


def test_mission_change_during_discovery_discards_the_snapshot() -> None:
    holder = {}
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(_candidate(),),
        captured_at=1.0,
    ), hook=lambda: holder["reducer"].apply(holder["reducer"].event(
        "obligation_blocked",
        payload={"obligation_id": "open-target", "reason": "external change"},
    )))
    runtime, reducer, _evidence, permits, controller = _runtime(
        actor=_Actor(_discover()),
        state=_State({0: _snapshot(10, turn_index=0)}),
        discovery=discovery,
    )
    holder["reducer"] = reducer

    result = runtime.run_one_turn(turn_index=0)

    assert result.status is RuntimeStatus.STALE_DISCARDED
    assert runtime._pending_target_discovery is None
    assert permits.pending_count == 0
    assert controller.calls == []


def test_stop_during_discovery_cancels_without_retaining_candidates() -> None:
    stop = Event()
    discovery = _Discovery(TargetDiscoverySnapshot(
        query="Orbit",
        candidates=(_candidate(),),
        captured_at=1.0,
    ), hook=stop.set)
    runtime, _reducer, _evidence, permits, controller = _runtime(
        actor=_Actor(_discover()),
        state=_State({0: _snapshot(10, turn_index=0)}),
        discovery=discovery,
    )

    result = runtime.run_one_turn(turn_index=0, stop_event=stop)

    assert result.status is RuntimeStatus.CANCELED
    assert discovery.calls == [("Orbit", stop)]
    assert runtime._pending_target_discovery is None
    assert permits.pending_count == 0
    assert controller.calls == []
