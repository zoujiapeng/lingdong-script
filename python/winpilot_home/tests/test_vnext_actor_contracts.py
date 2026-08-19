"""Identity-free Actor proposal and Runtime-owned envelope tests."""
from __future__ import annotations

import ast
from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
import random
from types import MappingProxyType

import pytest
from pydantic import ValidationError

from winpilot.visual_runtime.frame_authority import FrameAuthority
from winpilot.vnext.actor import (
    ACTOR_ENVELOPE_FORMAT,
    ACTOR_PROPOSAL_FORMAT,
    ActorDecisionEnvelope,
    BlockedReport,
    ClaimObligationDone,
    DesktopActionProposal,
    ImageRegion,
    InspectionRequest,
    InvalidActorAdmission,
    InvalidActorEnvelope,
    InvalidActorProposal,
    ObserveProposal,
    ReplanRequest,
    actor_proposal_to_json,
    bind_actor_decision,
    parse_actor_proposal,
    proposal_tool_name,
)
from winpilot.vnext.json_contract import (
    MAX_JSON_ARRAY_ITEMS,
    MAX_JSON_DEPTH,
    MAX_JSON_KEY_LENGTH,
    MAX_JSON_NODES,
    MAX_JSON_STRING_LENGTH,
    MAX_SIGNED_64,
    MIN_SIGNED_64,
)
from winpilot.vnext.mission import (
    MissionDefinition,
    MissionReducer,
    ObligationSpec,
    TargetBinding,
)
from winpilot.vnext.runtime import PointerDisposition, PointerLeaseState


def _module_name(path: Path, package_root: Path) -> str:
    relative = path.relative_to(package_root.parent).with_suffix("")
    return ".".join(relative.parts)


def _imports(path: Path, package_root: Path) -> tuple[str, ...]:
    tree = ast.parse(
        path.read_text(encoding="utf-8"),
        filename=str(path),
    )
    package = _module_name(path, package_root).rpartition(".")[0]
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                keep = max(0, len(parts) - node.level + 1)
                prefix = ".".join(parts[:keep])
                imported = ".".join(
                    part
                    for part in (prefix, node.module or "")
                    if part
                )
            else:
                imported = node.module or ""
            found.append(imported)
            found.extend(
                f"{imported}.{alias.name}"
                for alias in node.names
                if imported and alias.name != "*"
            )
    return tuple(found)


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _nested_json(depth: int) -> dict:
    root: dict = {}
    cursor = root
    for _ in range(depth):
        child: dict = {}
        cursor["next"] = child
        cursor = child
    return root


def _schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }


def _point_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "image_x": {"type": "number", "minimum": 0},
            "image_y": {"type": "number", "minimum": 0},
        },
        "required": ["image_x", "image_y"],
    }


def _drag_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "image_x1": {"type": "number", "minimum": 0},
            "image_y1": {"type": "number", "minimum": 0},
            "image_x2": {"type": "number", "minimum": 0},
            "image_y2": {"type": "number", "minimum": 0},
            "keep_holding": {"type": "boolean"},
        },
        "required": [
            "image_x1",
            "image_y1",
            "image_x2",
            "image_y2",
        ],
    }


def _mission_reducer(
    capability: str = "visual.interact",
    *,
    bound: bool = True,
) -> MissionReducer:
    definition = MissionDefinition(
        mission_id="mission-actor",
        run_id="run-actor",
        original_goal="Complete the visible task",
        obligations=(
            ObligationSpec(
                id="obligation-current",
                desired_state="the requested visible state exists",
                acceptance_criteria=("the visible result is confirmed",),
                required_capabilities=(capability,),
            ),
        ),
    )
    reducer = MissionReducer(definition)
    if bound:
        reducer.apply(
            reducer.event(
                "target_bound",
                payload={
                    "target_binding": TargetBinding(
                        binding_id="binding-current",
                        hwnd=9123,
                        process_id=44,
                        executable_identity="opaque-process",
                        title_snapshot="",
                        epoch=7,
                    ).to_dict(),
                },
            )
        )
    return reducer


def _mission(
    capability: str = "visual.interact",
    *,
    bound: bool = True,
):
    return _mission_reducer(
        capability,
        bound=bound,
    ).projection


@dataclass(frozen=True)
class _FrameFacts:
    frame_id: int = 82
    epoch: int = 7
    hwnd: int = 9123
    scope: str = "target_window"


@dataclass(frozen=True)
class _BinderState:
    turn_index: int
    frame: _FrameFacts
    pointer: PointerLeaseState


def _state(**overrides) -> _BinderState:
    values = {
        "turn_index": 5,
        "frame": _FrameFacts(),
        "pointer": PointerLeaseState(PointerDisposition.RELEASED),
    }
    values.update(overrides)
    return _BinderState(**values)


def _runtime(
    *,
    pointer: PointerLeaseState | None = None,
):
    reducer = _mission_reducer()
    mission = reducer.projection
    state = _state(
        **({} if pointer is None else {"pointer": pointer})
    )
    return reducer, mission, None, state, None, None


def _action(**overrides) -> DesktopActionProposal:
    values = {
        "format": ACTOR_PROPOSAL_FORMAT,
        "kind": "desktop_action",
        "action": "click",
        "args": {"image_x": 10, "image_y": 20},
        "local_intent": "activate the visible target",
    }
    values.update(overrides)
    return parse_actor_proposal(values)


def _bind(**overrides) -> ActorDecisionEnvelope:
    reducer, mission, profile, state, catalog, projection = _runtime()
    del profile, catalog, projection
    values = {
        "mission_reducer": reducer,
        "proposal": _action(),
        "turn_index": state.turn_index,
        "frame_id": state.frame.frame_id,
        "frame_epoch": state.frame.epoch,
        "frame_hwnd": state.frame.hwnd,
        "frame_scope": state.frame.scope,
        "pointer_disposition": state.pointer.disposition.value,
        "pointer_lease_id": state.pointer.lease_id,
        "frame_region": (-1200, 0, 1200, 800),
        "submitted_image_size": (900, 600),
        "frame_image_sha256": "b" * 64,
    }
    values.update(overrides)
    return bind_actor_decision(**values)


def _held_runtime():
    return _runtime(
        pointer=PointerLeaseState(
            PointerDisposition.HELD,
            "pointer-lease-current",
            True,
        ),
    )


def _held_drag() -> DesktopActionProposal:
    return _action(
        action="drag",
        args={
            "image_x1": 10,
            "image_y1": 20,
            "image_x2": 30,
            "image_y2": 40,
            "keep_holding": True,
        },
        local_intent="continue the held drag",
    )


def _all_proposals():
    return (
        _action(),
        ObserveProposal(
            format=ACTOR_PROPOSAL_FORMAT,
            observation_kind="full",
            image_region=None,
            question="What changed?",
        ),
        ObserveProposal(
            format=ACTOR_PROPOSAL_FORMAT,
            observation_kind="image_region",
            image_region=ImageRegion(
                x=1.0, y=2.0, width=30.0, height=40.0
            ),
            question="Describe the small control",
        ),
        InspectionRequest(
            format=ACTOR_PROPOSAL_FORMAT,
            region=ImageRegion(
                x=2.0, y=3.0, width=4.0, height=5.0
            ),
            question="Describe visible topology only",
        ),
        ClaimObligationDone(
            format=ACTOR_PROPOSAL_FORMAT,
            concise_claim="The requested state now appears visible",
        ),
        BlockedReport(
            format=ACTOR_PROPOSAL_FORMAT,
            blocker_kind="visual_uncertainty",
            observed_facts=(
                "Two visible candidates remain indistinguishable",
            ),
        ),
        ReplanRequest(
            format=ACTOR_PROPOSAL_FORMAT,
            reason_kind="strategy_conflict",
            observed_conflicts=(
                "The observed state conflicts with the obligation",
            ),
            failed_strategy_summaries=(
                "The first distinct route made no progress",
            ),
        ),
    )


def test_all_proposal_variants_are_strict_identity_free_round_trips() -> None:
    expected_tools = (
        "click",
        "observe",
        "observe",
        "request_inspection",
        "claim_obligation_done",
        "blocked",
        "request_replan",
    )
    for proposal, tool_name in zip(_all_proposals(), expected_tools):
        raw = proposal.model_dump(mode="json")
        replayed = parse_actor_proposal(raw)
        assert replayed == proposal
        assert json.loads(actor_proposal_to_json(replayed)) == raw
        assert proposal_tool_name(proposal) == tool_name
        assert set(raw).isdisjoint({
            "mission_id",
            "run_id",
            "mission_revision",
            "obligation_id",
            "frame_id",
            "evidence_refs",
            "broker_projection_id",
            "grant_ids",
        })


def test_model_wire_uses_one_typed_parse_and_one_outcome() -> None:
    defaulted = parse_actor_proposal({
        "kind": "blocked",
        "blocker_kind": "unknown",
        "observed_facts": [],
    })
    assert defaulted.format == ACTOR_PROPOSAL_FORMAT
    with pytest.raises(InvalidActorProposal):
        parse_actor_proposal({
            **_action().model_dump(mode="json"),
            "concise_claim": "also claim completion",
        })
    mapping_subclass = type("MappingLike", (dict,), {})
    assert parse_actor_proposal(
        mapping_subclass(_action().model_dump(mode="json"))
    ).action == "click"
    for kind in ("done", "fail", "action_batch", "desktop_actions"):
        with pytest.raises(InvalidActorProposal):
            parse_actor_proposal({
                "format": ACTOR_PROPOSAL_FORMAT,
                "kind": kind,
            })


def test_every_proposal_branch_requires_its_semantic_fields() -> None:
    for proposal in _all_proposals():
        raw = proposal.model_dump(mode="json")
        without_format = dict(raw)
        del without_format["format"]
        assert parse_actor_proposal(without_format).format == ACTOR_PROPOSAL_FORMAT
        for field in tuple(raw):
            if field == "format":
                continue
            missing = dict(raw)
            del missing[field]
            with pytest.raises(InvalidActorProposal):
                parse_actor_proposal(missing)

    raw = _action().model_dump(mode="json")
    for alias in (
        "type",
        "revision",
        "evidence_id",
        "description",
        "criteria",
    ):
        with pytest.raises(InvalidActorProposal):
            parse_actor_proposal({**raw, alias: "legacy"})


def test_proposal_wire_rejects_coercible_scalars_and_accepts_typed_sequences() -> None:
    raw = _action().model_dump(mode="json")
    for field, value in (
        ("action", 1),
        ("action", True),
        ("action", None),
        ("action", "1"),
        ("format", "true"),
        ("local_intent", 9),
        ("local_intent", False),
        ("local_intent", None),
    ):
        with pytest.raises(InvalidActorProposal):
            parse_actor_proposal({**raw, field: value})

    blocked = BlockedReport(
        blocker_kind="unknown",
        observed_facts=("one visible fact",),
    ).model_dump(mode="json")
    parsed = parse_actor_proposal({
        **blocked,
        "observed_facts": ("tuple is accepted by the typed model",),
    })
    assert parsed.observed_facts == ("tuple is accepted by the typed model",)
    for value in (None, "facts", [1], [True]):
        with pytest.raises(InvalidActorProposal):
            parse_actor_proposal({**blocked, "observed_facts": value})


def test_image_region_wire_and_model_require_exact_finite_floats() -> None:
    raw = {
        "format": ACTOR_PROPOSAL_FORMAT,
        "kind": "request_inspection",
        "region": {
            "x": 1.0,
            "y": 2.0,
            "width": 3.0,
            "height": 4.0,
        },
        "question": "Inspect the submitted image region",
    }
    assert parse_actor_proposal(raw).region.x == 1.0
    for field in ("x", "y", "width", "height"):
        for value in (1, "1.0", True, None, math.inf, math.nan):
            region = {**raw["region"], field: value}
            with pytest.raises(InvalidActorProposal):
                parse_actor_proposal({**raw, "region": region})
    for change in (
        {"type": "region"},
        {"revision": 1},
        {"description": "legacy"},
        {"criteria": []},
    ):
        with pytest.raises(InvalidActorProposal):
            parse_actor_proposal({
                **raw,
                "region": {**raw["region"], **change},
            })


def test_proposal_wire_uses_one_shared_bounded_json_budget() -> None:
    minimum = _action(args={
        "minimum": MIN_SIGNED_64,
        "maximum": MAX_SIGNED_64,
        "large_finite": 1e308,
    })
    assert minimum.args["minimum"] == MIN_SIGNED_64
    assert minimum.args["maximum"] == MAX_SIGNED_64
    assert minimum.args["large_finite"] == 1e308

    cyclic: dict = {}
    cyclic["self"] = cyclic
    cases = (
        (cyclic, "cyclic"),
        (_nested_json(MAX_JSON_DEPTH + 1), "depth"),
        (
            {f"node_{index}": None for index in range(MAX_JSON_NODES)},
            "node",
        ),
        ({"text": "x" * (MAX_JSON_STRING_LENGTH + 1)}, "string"),
        ({"items": [None] * (MAX_JSON_ARRAY_ITEMS + 1)}, "array"),
        ({"k" * (MAX_JSON_KEY_LENGTH + 1): None}, "key"),
        ({"integer": MAX_SIGNED_64 + 1}, "signed-64"),
        ({"integer": MIN_SIGNED_64 - 1}, "signed-64"),
        ({"float": math.inf}, "finite"),
        ({"float": math.nan}, "finite"),
        (
            {"strings": ["x" * MAX_JSON_STRING_LENGTH] * 5},
            "UTF-8",
        ),
    )
    for args, message in cases:
        with pytest.raises(InvalidActorProposal, match=message):
            _action(args=args)


def test_proposal_whole_wire_rejects_nested_container_subclasses() -> None:
    class DictSubclass(dict):
        pass

    class ListSubclass(list):
        pass

    for args in (
        {"nested": DictSubclass({"value": 1})},
        {"nested": ListSubclass([1])},
        {"nested": (1, 2)},
        {"nested": object()},
    ):
        with pytest.raises(InvalidActorProposal, match="non-literal JSON"):
            _action(args=args)


def test_proposal_text_is_preserved_and_canonical_tokens_are_not_trimmed() -> None:
    proposal = _action(local_intent="  preserve these spaces  ")
    assert proposal.local_intent == "  preserve these spaces  "
    with pytest.raises(InvalidActorProposal):
        _action(action=" click ")
    with pytest.raises(InvalidActorProposal):
        parse_actor_proposal({
            "format": ACTOR_PROPOSAL_FORMAT,
            "kind": "request_capability",
            "capability": " pointer.dynamic ",
            "reason": "No trimming is allowed",
        })


def test_proposal_serialization_does_not_reparse_a_valid_model() -> None:
    proposal = ReplanRequest(
        reason_kind="strategy_conflict",
        observed_conflicts=tuple(
            f"conflict-{index}" for index in range(2200)
        ),
        failed_strategy_summaries=tuple(
            f"failed-{index}" for index in range(2200)
        ),
    )
    wire = json.loads(actor_proposal_to_json(proposal))
    assert len(wire["observed_conflicts"]) == 2200
    assert len(wire["failed_strategy_summaries"]) == 2200


@pytest.mark.parametrize(
    "field",
    (
        "mission_id",
        "missionId",
        "mission-id",
        "FRAME_ID",
        "catalogFingerprint",
        "profile-id",
        "state_fingerprint",
        "turnIndex",
        "frameScope",
        "executor_id",
        "eventId",
        "receipt-id",
        "payload_sha256",
        "evidence_refs",
        "target_binding_id",
        "pointerLeaseId",
        "artifact_id",
        "hwnd",
    ),
)
def test_surplus_runtime_identity_hints_reach_the_mechanical_owner(
    field: str,
) -> None:
    args = {"outer": [{"inner": {field: "invented"}}]}
    assert _action(args=args).model_dump(mode="json")["args"] == args


@pytest.mark.parametrize(
    "field",
    (
        "physical_x",
        "screenY",
        "desktop-x",
        "frame_x1",
        "absoluteCoordinates",
        "client_x",
        "windowY",
        "native-x",
        "global_point",
        "monitorCoordinates",
        "cursor_x",
    ),
)
def test_surplus_coordinate_hints_reach_the_mechanical_owner(field: str) -> None:
    assert _action(args={field: 9}).args[field] == 9
    assert _action(args={"coordinateSpace": "client"}).args == {
        "coordinateSpace": "client"
    }


def test_surplus_batch_hints_are_ignored_but_batch_actions_are_rejected() -> None:
    for field in (
        "actions",
        "tool_calls",
        "actionBatch",
        "operations",
    ):
        args = {field: [{"action": "click"}]}
        assert _action(args=args).model_dump(mode="json")["args"] == args
    for action in (
        "done",
        "fail",
        "claim_obligation_done",
        "run_action_batch",
        "finish_task",
    ):
        with pytest.raises(
            InvalidActorProposal,
            match="not one GUI proposal",
        ):
            _action(action=action)


def test_action_args_are_bounded_finite_json_and_deeply_immutable() -> None:
    source = {"nested": [{"image_x": 1}], "enabled": True}
    proposal = _action(args=source)
    source["nested"][0]["image_x"] = 999
    assert proposal.args["nested"][0]["image_x"] == 1
    with pytest.raises(TypeError):
        proposal.args["new"] = 1
    with pytest.raises(TypeError):
        proposal.args["nested"][0]["image_x"] = 2

    for bad in (math.nan, math.inf, object(), 1 << 80):
        with pytest.raises(InvalidActorProposal):
            _action(args={"value": bad})
    assert _action(args={"value": 1e308}).args["value"] == 1e308
    with pytest.raises(InvalidActorProposal):
        _action(args={1: "non-string key"})
    with pytest.raises(InvalidActorProposal):
        _action(args={"text": "x" * 1_048_577})
    with pytest.raises(InvalidActorProposal, match="array length"):
        _action(args={"values": [0] * 4097})

    cyclic: dict = {}
    cyclic["self"] = cyclic
    with pytest.raises(InvalidActorProposal, match="cyclic"):
        _action(args=cyclic)

    deep = leaf = {}
    for index in range(1200):
        nested = {"level": index}
        leaf["next"] = nested
        leaf = nested
    with pytest.raises(InvalidActorProposal, match="depth"):
        _action(args=deep)

    class ExecutableMapping(dict):
        def items(self):
            raise AssertionError("mapping subclass was executed")

    hostile = ExecutableMapping({"image_x": 1, "image_y": 2})
    for value in (hostile, MappingProxyType(hostile)):
        with pytest.raises(InvalidActorProposal, match="non-literal JSON"):
            _action(args=value)


def test_semantic_text_and_arrays_have_explicit_resource_bounds() -> None:
    with pytest.raises(ValidationError, match="text length"):
        DesktopActionProposal(
            action="click",
            args={},
            local_intent="x" * 16_385,
        )
    with pytest.raises(ValidationError, match="array item"):
        BlockedReport(
            blocker_kind="unknown",
            observed_facts=tuple(
                f"fact-{index}" for index in range(4097)
            ),
        )


def test_image_regions_only_validate_bounded_image_local_shape() -> None:
    region = ImageRegion(
        x=50_000.0, y=40_000.0, width=20.0, height=10.0
    )
    assert region.x == 50_000
    for bad in (-1, math.nan, math.inf, True, 1 << 80, 10**5000):
        with pytest.raises(ValidationError):
            ImageRegion(x=bad, y=0.0, width=1.0, height=1.0)


def test_proposal_serialization_is_canonical_for_order_and_signed_zero() -> None:
    rng = random.Random(41)
    items = [(f"key_{index}", index) for index in range(30)]
    baseline = None
    for _ in range(20):
        rng.shuffle(items)
        serialized = actor_proposal_to_json(
            _action(args={**dict(items), "zero": -0.0})
        )
        baseline = serialized if baseline is None else baseline
        assert serialized == baseline
        assert json.loads(serialized)["args"]["zero"] == 0.0


def test_runtime_binder_derives_all_authority_and_round_trips() -> None:
    envelope = _bind()
    raw = envelope.to_dict()
    assert raw["format"] == ACTOR_ENVELOPE_FORMAT
    assert raw["mission_id"] == "mission-actor"
    assert raw["run_id"] == "run-actor"
    assert raw["obligation_id"] == "obligation-current"
    assert raw["turn_index"] == 5
    assert raw["frame_id"] == 82
    assert raw["frame_scope"] == "target_window"
    assert raw["target_binding_id"] == "binding-current"
    assert raw["pointer_disposition"] == "released"
    assert raw["pointer_lease_id"] is None
    assert ActorDecisionEnvelope.from_dict(raw) == envelope
    assert json.loads(envelope.to_json()) == raw
    assert envelope.epoch == raw["frame_epoch"]
    assert envelope.hwnd == raw["frame_hwnd"]
    assert envelope.region == tuple(raw["frame_region"])
    assert envelope.submitted_size == tuple(raw["submitted_image_size"])
    assert envelope.image_sha256 == raw["frame_image_sha256"]


def test_full_desktop_binder_keeps_frame_facts_without_window_admission() -> None:
    reducer = _mission_reducer()
    state = _state(
        frame=_FrameFacts(83, 4, 999, "full_desktop"),
    )

    envelope = bind_actor_decision(
        mission_reducer=reducer,
        proposal=_action(),
        turn_index=state.turn_index,
        frame_id=state.frame.frame_id,
        frame_epoch=state.frame.epoch,
        frame_hwnd=state.frame.hwnd,
        frame_scope=state.frame.scope,
        pointer_disposition=state.pointer.disposition.value,
        pointer_lease_id=state.pointer.lease_id,
        frame_region=(-1200, 0, 1200, 800),
        submitted_image_size=(900, 600),
        frame_image_sha256="b" * 64,
    )

    assert envelope.frame_id == 83
    assert envelope.frame_scope == "full_desktop"
    assert envelope.frame_hwnd == 999
    assert envelope.target_binding_id == "binding-current"


def test_runtime_binder_preserves_one_owned_held_drag_lease() -> None:
    reducer, _mission, _unused, state, _unused2, _unused3 = _held_runtime()
    envelope = bind_actor_decision(
        mission_reducer=reducer,
        proposal=_held_drag(),
        turn_index=state.turn_index,
        frame_id=state.frame.frame_id,
        frame_epoch=state.frame.epoch,
        frame_hwnd=state.frame.hwnd,
        frame_scope=state.frame.scope,
        pointer_disposition=state.pointer.disposition.value,
        pointer_lease_id=state.pointer.lease_id,
        frame_region=(-1200, 0, 1200, 800),
        submitted_image_size=(900, 600),
        frame_image_sha256="b" * 64,
    )
    assert envelope.pointer_disposition == "held"
    assert envelope.pointer_lease_id == "pointer-lease-current"
    assert envelope.proposal.action == "drag"
    assert ActorDecisionEnvelope.from_dict(envelope.to_dict()) == envelope


def test_held_envelope_accepts_mouse_keyboard_actions_and_tracks_lease() -> None:
    reducer, _mission, _unused, state, _unused2, _unused3 = _held_runtime()
    common = {
        "mission_reducer": reducer,
        "turn_index": state.turn_index,
        "frame_id": state.frame.frame_id,
        "frame_epoch": state.frame.epoch,
        "frame_hwnd": state.frame.hwnd,
        "frame_scope": state.frame.scope,
        "pointer_disposition": state.pointer.disposition.value,
        "pointer_lease_id": state.pointer.lease_id,
        "frame_region": (-1200, 0, 1200, 800),
        "submitted_image_size": (900, 600),
        "frame_image_sha256": "b" * 64,
    }
    for proposal in (
        _action(),
        _action(action="scroll", args={}),
        _action(action="hotkey", args={}),
        _held_drag(),
    ):
        envelope = bind_actor_decision(**common, proposal=proposal)
        assert envelope.pointer_disposition == "held"
        assert envelope.pointer_lease_id == "pointer-lease-current"
        assert envelope.proposal.action == proposal.action

    raw = bind_actor_decision(
        **common,
        proposal=_held_drag(),
    ).to_dict()
    with pytest.raises(InvalidActorEnvelope, match="requires lease_id"):
        ActorDecisionEnvelope.from_dict(
            {**raw, "pointer_lease_id": None}
        )
    with pytest.raises(
        InvalidActorEnvelope,
        match="released pointer cannot retain",
    ):
        ActorDecisionEnvelope.from_dict(
            {**raw, "pointer_disposition": "released"}
        )


def test_envelope_decision_ids_are_deterministic_per_material() -> None:
    envelope = _bind()
    changed_frame = _bind(frame_image_sha256="e" * 64)
    changed_action = _bind(proposal=_action(action="double_click"))
    assert len({
        envelope.decision_id,
        changed_frame.decision_id,
        changed_action.decision_id,
    }) == 3
    assert _bind().decision_id == envelope.decision_id


def test_envelope_wire_is_strict_bounded_and_frozen() -> None:
    envelope = _bind()
    raw = envelope.to_dict()
    with pytest.raises(InvalidActorEnvelope, match="missing=.*format"):
        ActorDecisionEnvelope.from_dict({
            key: value
            for key, value in raw.items()
            if key != "format"
        })
    for change in (
        {"frame_id": 0},
        {"frame_scope": "desktop"},
        {"frame_region": [0, 0, 0, 1]},
        {"submitted_image_size": [0, 1]},
        {"frame_image_sha256": "A" * 64},
        {"mission_revision": True},
        {"unexpected": "authority"},
    ):
        with pytest.raises(InvalidActorEnvelope):
            ActorDecisionEnvelope.from_dict({**raw, **change})
    with pytest.raises(ValidationError):
        envelope.turn_index = 9


def test_envelope_wire_requires_plain_exact_complete_fields_and_no_aliases() -> None:
    raw = _bind().to_dict()

    class DictSubclass(dict):
        pass

    for value in (
        MappingProxyType(raw),
        DictSubclass(raw),
        (),
        None,
        object(),
    ):
        with pytest.raises(InvalidActorEnvelope, match="plain JSON object"):
            ActorDecisionEnvelope.from_dict(value)  # type: ignore[arg-type]

    for field in tuple(raw):
        missing = dict(raw)
        del missing[field]
        with pytest.raises(InvalidActorEnvelope, match="missing"):
            ActorDecisionEnvelope.from_dict(missing)
    without_pointer_lease = dict(raw)
    del without_pointer_lease["pointer_lease_id"]
    with pytest.raises(InvalidActorEnvelope, match="pointer_lease_id"):
        ActorDecisionEnvelope.from_dict(without_pointer_lease)

    for alias in (
        "type",
        "revision",
        "evidence_id",
        "description",
        "criteria",
    ):
        with pytest.raises(InvalidActorEnvelope, match="unknown"):
            ActorDecisionEnvelope.from_dict({**raw, alias: "legacy"})


def test_envelope_wire_rejects_coercible_scalars_none_and_non_list_arrays() -> None:
    raw = _bind().to_dict()
    for field, value in (
        ("mission_revision", "1"),
        ("mission_revision", True),
        ("mission_revision", 1.0),
        ("mission_revision", None),
        ("turn_index", "5"),
        ("frame_id", True),
        ("frame_epoch", "7"),
        ("frame_hwnd", 9123.0),
        ("pointer_lease_id", "false"),
        ("pointer_disposition", "held"),
        ("frame_scope", True),
    ):
        with pytest.raises(InvalidActorEnvelope):
            ActorDecisionEnvelope.from_dict({**raw, field: value})
    for field in (
        "frame_region",
        "submitted_image_size",
    ):
        with pytest.raises(InvalidActorEnvelope, match="non-literal JSON"):
            ActorDecisionEnvelope.from_dict({
                **raw,
                field: tuple(raw[field]),
            })

    for index_value in ("0", True, 0.0, None):
        region = list(raw["frame_region"])
        region[0] = index_value
        with pytest.raises(InvalidActorEnvelope):
            ActorDecisionEnvelope.from_dict({
                **raw,
                "frame_region": region,
            })


def test_envelope_wire_uses_one_shared_bounded_json_budget() -> None:
    envelope = _bind()
    serialized = envelope.to_json()

    def with_args(args):
        raw = json.loads(serialized)
        raw["proposal"]["args"] = args
        return raw

    cyclic: dict = {}
    cyclic["self"] = cyclic
    cases = (
        (with_args(cyclic), "cyclic"),
        (with_args(_nested_json(MAX_JSON_DEPTH + 1)), "depth"),
        (
            with_args({
                f"node_{index}": None
                for index in range(MAX_JSON_NODES)
            }),
            "node",
        ),
        (
            with_args({
                "text": "x" * (MAX_JSON_STRING_LENGTH + 1)
            }),
            "string",
        ),
        (
            with_args({
                "items": [None] * (MAX_JSON_ARRAY_ITEMS + 1)
            }),
            "array",
        ),
        (
            with_args({
                "k" * (MAX_JSON_KEY_LENGTH + 1): None
            }),
            "key",
        ),
        (
            {
                **json.loads(serialized),
                "mission_revision": MAX_SIGNED_64 + 1,
            },
            "signed-64",
        ),
        (
            {
                **json.loads(serialized),
                "frame_region": [
                    MIN_SIGNED_64 - 1,
                    0,
                    1,
                    1,
                ],
            },
            "signed-64",
        ),
        (with_args({"value": math.inf}), "finite"),
        (with_args({"value": math.nan}), "finite"),
        (
            with_args({
                "strings": ["x" * MAX_JSON_STRING_LENGTH] * 5
            }),
            "UTF-8",
        ),
    )
    for raw, message in cases:
        with pytest.raises(InvalidActorEnvelope, match=message):
            ActorDecisionEnvelope.from_dict(raw)

    bounded = _bind(frame_region=(
        MIN_SIGNED_64,
        MAX_SIGNED_64,
        1,
        1,
    ))
    assert ActorDecisionEnvelope.from_dict(bounded.to_dict()) == bounded


def test_envelope_nested_wire_rejects_non_json_containers_and_aliases() -> None:
    raw = _bind().to_dict()

    class DictSubclass(dict):
        pass

    class ListSubclass(list):
        pass

    for args in (
        {"nested": DictSubclass({"value": 1})},
        {"nested": ListSubclass([1])},
        {"nested": (1, 2)},
        {"nested": object()},
    ):
        changed = json.loads(json.dumps(raw))
        changed["proposal"]["args"] = args
        with pytest.raises(InvalidActorEnvelope, match="non-literal JSON"):
            ActorDecisionEnvelope.from_dict(changed)
    for alias in (
        "type",
        "revision",
        "evidence_id",
        "description",
        "criteria",
    ):
        proposal = dict(raw["proposal"])
        proposal[alias] = "legacy"
        with pytest.raises(InvalidActorEnvelope):
            ActorDecisionEnvelope.from_dict({
                **raw,
                "proposal": proposal,
            })


def test_model_copy_update_is_bounded_literal_json_ingress() -> None:
    envelope = _bind()
    assert envelope.model_copy() == envelope

    class DictSubclass(dict):
        pass

    for update in (
        MappingProxyType({"frame_id": 1}),
        DictSubclass({"frame_id": 1}),
        (),
        object(),
    ):
        with pytest.raises(InvalidActorEnvelope, match="plain JSON object"):
            envelope.model_copy(update=update)  # type: ignore[arg-type]

    cyclic: dict = {}
    cyclic["self"] = cyclic
    for update, message in (
        ({"legacy": cyclic}, "cyclic"),
        ({"legacy": _nested_json(MAX_JSON_DEPTH + 1)}, "depth"),
        (
            {"legacy": {
                f"node_{index}": None
                for index in range(MAX_JSON_NODES)
            }},
            "node",
        ),
        (
            {"legacy": "x" * (MAX_JSON_STRING_LENGTH + 1)},
            "string",
        ),
        (
            {"legacy": [None] * (MAX_JSON_ARRAY_ITEMS + 1)},
            "array",
        ),
        ({"legacy": object()}, "non-literal JSON"),
        ({"legacy": (1, 2)}, "non-literal JSON"),
        ({"legacy": math.inf}, "finite"),
        ({"legacy": MAX_SIGNED_64 + 1}, "signed-64"),
    ):
        with pytest.raises(InvalidActorEnvelope, match=message):
            envelope.model_copy(update=update)
    for update in (
        {"frame_id": "82"},
        {"frame_id": True},
        {"pointer_lease_id": "false"},
        {"type": ACTOR_ENVELOPE_FORMAT},
    ):
        with pytest.raises(InvalidActorEnvelope, match="model_copy update"):
            envelope.model_copy(update=update)


def test_envelope_structurally_pins_the_real_frame_authority() -> None:
    authority = FrameAuthority()
    frame = authority.commit(
        epoch=7,
        hwnd=9123,
        region=(-1200, 0, 1200, 800),
        scope="target_window",
        reason="actor contract test",
        submitted_size=(900, 600),
        image_b64="c3VibWl0dGVkLWZyYW1l",
        captured_at=1.0,
    )
    reducer = _mission_reducer()
    envelope = bind_actor_decision(
        mission_reducer=reducer,
        proposal=_action(),
        turn_index=5,
        frame_id=frame.frame_id,
        frame_epoch=frame.epoch,
        frame_hwnd=frame.hwnd,
        frame_scope=frame.scope,
        pointer_disposition="released",
        pointer_lease_id=None,
        frame_region=frame.region,
        submitted_image_size=frame.submitted_size,
        frame_image_sha256=frame.image_sha256,
    )

    called, result = authority.run_if_current(
        envelope,
        lambda: "pinned",
    )
    assert called is True
    assert result == "pinned"


@pytest.mark.parametrize(
    "args",
    (
        {"image_x": 10},
        {"image_x": 10, "image_y": 20, "extra": True},
        {"image_x": True, "image_y": 20},
        {"image_x": -1, "image_y": 20},
    ),
)
def test_binder_leaves_desktop_argument_mechanics_to_the_adapter(args) -> None:
    admitted = _bind(proposal=_action(args=args))
    assert admitted.proposal.args == args


def test_binder_accepts_finite_pointer_overshoot_but_keeps_regions_bounded() -> None:
    accepted = _bind(proposal=_action(args={
        "image_x": 899,
        "image_y": 599,
    }))
    assert accepted.proposal.args["image_x"] == 899
    for args in (
        {"image_x": 900, "image_y": 599},
        {"image_x": 899, "image_y": 600},
    ):
        edge = _bind(proposal=_action(args=args))
        assert edge.proposal.args == args
    for args in (
        {"image_x": 901, "image_y": 599},
        {"image_x": 899, "image_y": 601},
    ):
        overshoot = _bind(proposal=_action(args=args))
        assert overshoot.proposal.args == args

    with pytest.raises(InvalidActorAdmission, match="frame_region"):
        _bind(frame_region=(0, 0, 0, 1))
    with pytest.raises(InvalidActorAdmission, match="submitted_image_size"):
        _bind(submitted_image_size=(0, 1))


def test_actor_package_is_exact_pure_and_default_unreachable() -> None:
    home = Path(__file__).resolve().parents[1]
    package_root = home / "winpilot"
    actor_dir = home / "winpilot" / "vnext" / "actor"
    runtime_dir = home / "winpilot" / "vnext" / "runtime"
    desktop_adapter_atomic = (
        home
        / "winpilot"
        / "vnext"
        / "desktop_adapter"
        / "atomic.py"
    )
    visual_actor = (
        home
        / "winpilot"
        / "vnext"
        / "model_roles"
        / "visual_actor.py"
    )
    assert tuple(sorted(path.name for path in actor_dir.glob("*.py"))) == (
        "__init__.py",
        "envelope.py",
        "proposals.py",
    )
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in actor_dir.glob("*.py")
    )
    for forbidden in (
        "openai",
        "anthropic",
        "electron",
        "win32",
        "pyautogui",
        "desktopactionqueue",
        "rawdevice",
        "fastguicontroller",
        "target_acquisition",
        "visual_runtime",
        "from .model",
        "from .views",
        "from .runtime",
    ):
        assert forbidden not in source.lower()

    allowed_product_prefixes = (
        "winpilot.vnext.actor",
        "winpilot.vnext.json_contract",
        "winpilot.vnext.mission",
        "winpilot.vnext.runtime",
    )
    for path in actor_dir.glob("*.py"):
        product_imports = tuple(
            imported
            for imported in _imports(path, package_root)
            if _matches_prefix(imported, "winpilot")
        )
        assert all(
            any(
                _matches_prefix(imported, prefix)
                for prefix in allowed_product_prefixes
            )
            for imported in product_imports
        )

    outside_actor = (
        path
        for path in package_root.rglob("*.py")
        if "actor" not in path.parts
        and runtime_dir not in path.parents
        and path != desktop_adapter_atomic
        and path != visual_actor
    )
    violations = tuple(
        (path, imported)
        for path in outside_actor
        for imported in _imports(path, package_root)
        if _matches_prefix(imported, "winpilot.vnext.actor")
    )
    assert violations == ()
    adapter_actor_imports = tuple(
        imported
        for imported in _imports(desktop_adapter_atomic, package_root)
        if _matches_prefix(imported, "winpilot.vnext.actor")
    )
    assert adapter_actor_imports == (
        "winpilot.vnext.actor",
        "winpilot.vnext.actor.ActorDecisionEnvelope",
        "winpilot.vnext.actor.DesktopActionProposal",
    )
    visual_actor_imports = tuple(
        imported
        for imported in _imports(visual_actor, package_root)
        if _matches_prefix(imported, "winpilot.vnext.actor")
    )
    assert visual_actor_imports == (
        "winpilot.vnext.actor",
        "winpilot.vnext.actor.ACTOR_PROPOSAL_FORMAT",
        "winpilot.vnext.actor.InvalidActorProposal",
    )


def test_import_scanner_expands_absolute_and_relative_aliases(
    tmp_path,
) -> None:
    package_root = tmp_path / "winpilot"
    consumer = package_root / "vnext" / "bridge" / "consumer.py"
    consumer.parent.mkdir(parents=True)
    consumer.write_text(
        "from winpilot.vnext import actor\n"
        "from .. import actor as relative_actor\n",
        encoding="utf-8",
    )
    imports = _imports(consumer, package_root)
    assert imports.count("winpilot.vnext.actor") == 2
