"""P4a identity-free Actor contract and pure input-projection tests."""
from __future__ import annotations

import ast
import json
import math
import random
from pathlib import Path

import pytest
from pydantic import ValidationError

from winpilot.vnext.actor import (
    ACTOR_ENVELOPE_FORMAT,
    ACTOR_FRAME_VIEW_FORMAT,
    ACTOR_MISSION_VIEW_FORMAT,
    ACTOR_PROPOSAL_FORMAT,
    ACTOR_TOOL_VIEW_FORMAT,
    ACTOR_TURN_INPUT_FORMAT,
    ActorDecisionEnvelope,
    ActorTurnInput,
    BlockedReport,
    CapabilityRequest,
    ClaimObligationDone,
    DesktopActionProposal,
    ImageRegion,
    InspectionRequest,
    InvalidActorEnvelope,
    InvalidActorProposal,
    InvalidActorView,
    ObserveProposal,
    ReplanRequest,
    actor_proposal_to_json,
    parse_actor_proposal,
    project_actor_turn_input,
)
from winpilot.vnext.capabilities.contracts import (
    MutationKind,
    ToolCatalog,
    ToolRoute,
    ToolSpec,
)
from winpilot.vnext.capabilities.state import (
    AuthoritativeFrameState,
    BrokerProjection,
    ExecutionRoute,
    PointerDisposition,
    PointerLeaseState,
)
from winpilot.vnext.mission.contracts import (
    ActiveObligationCard,
    ActionEffectReason,
    ActionEffectSummary,
    ArtifactRef,
    FailureSummary,
    MissionCard,
    TargetBinding,
    TransitionSummary,
    VerifiedFact,
)
from winpilot.visual_runtime.commit_admission import (
    FrameCommitIdentity,
    TargetCommitIdentity,
)


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


def _card() -> MissionCard:
    effect = ActionEffectSummary(
        action_id="decision-runtime-1",
        input_completed=True,
        before_frame_id=80,
        after_frame_id=81,
        before_frame_evidence_ref="evidence-frame-before-1",
        after_frame_evidence_ref="evidence-frame-after-1",
        before_frame_payload_hash="a" * 64,
        after_frame_payload_hash="b" * 64,
        target_binding_valid=True,
        frame_comparable=True,
        local_visual_change="unchanged",
        change_ratio=0.0,
        reason=ActionEffectReason.EXACT_PAYLOAD,
        evidence_ref="evidence-action-effect-1",
    )
    return MissionCard(
        mission_id="mission-actor-test",
        mission_revision=4,
        original_goal="Complete the unknown visible task.",
        active_obligation=ActiveObligationCard(
            id="obligation-current",
            desired_state="The visible task is completed",
            acceptance_criteria=("The requested visible result exists",),
            forbidden_effects=("Do not alter unrelated content",),
            required_capabilities=("visual.interact",),
            depends_on=(),
        ),
        verified_facts=(
            VerifiedFact(
                id="fact-runtime-1",
                statement="The required image artifact is available",
                evidence_refs=("evidence-runtime-1",),
                scope="obligation-current",
                valid_for_obligation_ids=("obligation-current",),
            ),
        ),
        artifact_bindings=(
            ArtifactRef(
                artifact_id="artifact-runtime-1",
                media_type="image/png",
                sha256="a" * 64,
                producer_obligation_id="obligation-current",
                evidence_ref="evidence-runtime-1",
            ),
        ),
        target_binding=TargetBinding(
            binding_id="binding-runtime-1",
            hwnd=9123,
            process_id=44,
            executable_identity="example.exe",
            title_snapshot="Example Surface",
            established_by_evidence_ref="evidence-target-1",
            epoch=7,
        ),
        last_transition=TransitionSummary(
            action="click",
            input_completed=True,
            visible_effect="The visible surface changed locally",
            evidence_refs=(
                effect.before_frame_evidence_ref,
                effect.after_frame_evidence_ref,
                effect.evidence_ref,
            ),
            frame_id=81,
            obligation_id="obligation-current",
            action_id=effect.action_id,
            action_effect=effect,
        ),
        failure_summary=FailureSummary(
            kind="no_visible_effect",
            message="The expected result was not visible",
            evidence_refs=("evidence-failure-1",),
            count=1,
            obligation_id="obligation-current",
        ),
        no_progress_streak=2,
    )


def _frame(**overrides) -> AuthoritativeFrameState:
    values = {
        "frame_id": 82,
        "hwnd": 9123,
        "epoch": 7,
        "authoritative": True,
        "fresh": True,
    }
    values.update(overrides)
    return AuthoritativeFrameState(**values)


def _catalog(schema: dict | None = None) -> ToolCatalog:
    return ToolCatalog((
        ToolSpec(
            name="click",
            schema=schema or {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "image_x": {"type": "number"},
                    "image_y": {"type": "number"},
                },
                "required": ["image_x", "image_y"],
            },
            route=ToolRoute.VISUAL_ACTOR,
            mutation_kind=MutationKind.GUI,
            required_any_capabilities=("visual.interact",),
        ),
    ))


def _projection(**overrides) -> BrokerProjection:
    values = {
        "projection_id": "projection-runtime-1",
        "mission_revision": 4,
        "obligation_id": "obligation-current",
        "execution_route": ExecutionRoute.VISUAL_ACTOR,
        "tool_names": ("click",),
        "active_grants": (),
        "missing_capabilities": (),
        "projection_reasons": ("visual route is available",),
        "target_binding_id": "binding-runtime-1",
    }
    values.update(overrides)
    return BrokerProjection(**values)


def test_all_proposal_variants_are_strict_discriminated_round_trips() -> None:
    proposals = (
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
            image_region=ImageRegion(x=1, y=2, width=30, height=40),
            question="Describe the small control",
        ),
        CapabilityRequest(
            format=ACTOR_PROPOSAL_FORMAT,
            capability="pointer.dynamic",
            reason="the visible interaction requires a held pointer",
        ),
        InspectionRequest(
            format=ACTOR_PROPOSAL_FORMAT,
            region=ImageRegion(x=2, y=3, width=4, height=5),
            question="Describe visible topology only",
        ),
        ClaimObligationDone(
            format=ACTOR_PROPOSAL_FORMAT,
            concise_claim="The requested state now appears visible",
        ),
        BlockedReport(
            format=ACTOR_PROPOSAL_FORMAT,
            blocker_kind="visual_uncertainty",
            observed_facts=("Two candidates remain indistinguishable",),
        ),
        ReplanRequest(
            format=ACTOR_PROPOSAL_FORMAT,
            reason_kind="strategy_conflict",
            observed_conflicts=("The observed state conflicts with the obligation",),
            failed_strategy_summaries=("The first distinct route made no progress",),
        ),
    )
    for proposal in proposals:
        raw = proposal.model_dump(mode="json")
        replayed = parse_actor_proposal(raw)
        assert replayed == proposal
        assert json.loads(actor_proposal_to_json(replayed)) == raw
        assert set(raw).isdisjoint({
            "mission_id", "run_id", "mission_revision", "obligation_id",
            "frame_id", "evidence_refs", "broker_projection_id", "grant_ids",
        })


def test_model_wire_requires_explicit_version_and_exact_one_outcome() -> None:
    with pytest.raises(InvalidActorProposal, match="explicit format"):
        parse_actor_proposal({
            "kind": "blocked", "blocker_kind": "unknown", "observed_facts": [],
        })
    with pytest.raises(InvalidActorProposal):
        parse_actor_proposal({
            **_action().model_dump(mode="json"),
            "concise_claim": "also claim completion",
        })
    for kind in ("done", "fail", "action_batch", "desktop_actions"):
        with pytest.raises(InvalidActorProposal, match="unsupported actor proposal kind"):
            parse_actor_proposal({"format": ACTOR_PROPOSAL_FORMAT, "kind": kind})


@pytest.mark.parametrize(
    "field",
    (
        "mission_id", "missionId", "mission-id", "FRAME_ID", "frameId",
        "obligation_id", "evidence_refs", "brokerProjectionId", "grant_ids",
        "target_binding_id", "pointerLeaseId", "artifact_id", "hwnd",
    ),
)
def test_runtime_identity_is_rejected_at_any_argument_depth(field: str) -> None:
    with pytest.raises(InvalidActorProposal, match="Runtime-owned identity"):
        parse_actor_proposal({
            **_action().model_dump(mode="json"),
            "args": {"outer": [{"inner": {field: "invented"}}]},
        })


@pytest.mark.parametrize(
    "field",
    ("physical_x", "screenY", "desktop-x", "frame_x1", "absoluteCoordinates"),
)
def test_physical_and_frame_coordinates_are_rejected(field: str) -> None:
    with pytest.raises(InvalidActorProposal, match="submitted-image coordinate"):
        parse_actor_proposal({
            **_action().model_dump(mode="json"), "args": {field: 9},
        })


def test_single_continuous_transactions_allow_paths_but_reject_action_batches() -> None:
    for action, args in (
        ("drag", {
            "image_x1": 1, "image_y1": 2, "image_x2": 9, "image_y2": 8,
            "points": [{"image_x": 4, "image_y": 5}],
        }),
        ("draw_strokes", {
            "strokes": [[{"image_x": 1, "image_y": 2}, {"image_x": 3, "image_y": 4}]],
        }),
    ):
        assert _action(action=action, args=args).action == action
    for field in ("actions", "tool_calls", "actionBatch", "operations"):
        with pytest.raises(InvalidActorProposal, match="multiple action outcomes"):
            _action(args={field: [{"action": "click"}]})
    for action in ("done", "fail", "claim_obligation_done", "run_action_batch"):
        with pytest.raises(InvalidActorProposal, match="not one GUI proposal"):
            _action(action=action)


def test_action_args_are_finite_json_and_deeply_immutable() -> None:
    proposal = _action(args={"nested": [{"image_x": 1}], "enabled": True})
    with pytest.raises(TypeError):
        proposal.args["new"] = 1
    with pytest.raises(TypeError):
        proposal.args["nested"][0]["image_x"] = 2
    for bad in (math.nan, math.inf, object()):
        with pytest.raises(InvalidActorProposal):
            _action(args={"value": bad})
    cyclic: dict = {}
    cyclic["self"] = cyclic
    with pytest.raises(InvalidActorProposal, match="cyclic"):
        _action(args=cyclic)


def test_image_regions_only_validate_image_local_shape_not_runtime_bounds() -> None:
    # Submitted-image upper bounds are known only to Runtime; P4a deliberately
    # accepts them here and leaves exact current-frame validation to P4 runtime.
    region = ImageRegion(x=50_000, y=40_000, width=20, height=10)
    assert region.x == 50_000
    for bad in (-1, math.nan, math.inf, True):
        with pytest.raises(ValidationError):
            ImageRegion(x=bad, y=0, width=1, height=1)


def test_serialization_is_stable_under_argument_insertion_order() -> None:
    rng = random.Random(41)
    items = [(f"key_{index}", index) for index in range(30)]
    baseline = None
    for _ in range(20):
        rng.shuffle(items)
        serialized = actor_proposal_to_json(_action(args=dict(items)))
        baseline = serialized if baseline is None else baseline
        assert serialized == baseline


def test_runtime_envelope_binds_all_authority_and_round_trips() -> None:
    envelope = ActorDecisionEnvelope(
        format=ACTOR_ENVELOPE_FORMAT,
        decision_id="decision-1",
        mission_id="mission-1",
        run_id="run-1",
        mission_revision=4,
        obligation_id="obligation-1",
        frame=FrameCommitIdentity(
            frame_id=82,
            epoch=7,
            hwnd=9123,
            region=(0, 0, 1200, 800),
            scope="target_window",
            submitted_size=(1200, 800),
            image_sha256="b" * 64,
        ),
        target=TargetCommitIdentity(
            binding_id="binding-1",
            hwnd=9123,
            root_hwnd=9000,
            process_id=44,
            epoch=7,
            window_region=(0, 0, 1200, 800),
        ),
        pointer=PointerLeaseState(PointerDisposition.RELEASED),
        broker_projection_id="projection-1",
        capability_grant_ids=("grant-b", "grant-a"),
        allowed_evidence_scope=("evidence-b", "evidence-a"),
        proposal=_action(),
    )
    raw = json.loads(envelope.to_json())
    assert raw["capability_grant_ids"] == ["grant-a", "grant-b"]
    assert raw["allowed_evidence_scope"] == ["evidence-a", "evidence-b"]
    assert raw["frame"]["scope"] == "target_window"
    assert raw["target"]["root_hwnd"] == 9000
    assert raw["pointer"] == {
        "disposition": "released", "lease_id": None, "physically_owned": False,
    }
    assert ActorDecisionEnvelope.from_dict(raw).to_json() == envelope.to_json()
    assert raw["proposal"].keys().isdisjoint({"mission_id", "frame_id", "grant_ids"})


def test_envelope_is_strict_and_frame_identity_is_atomic() -> None:
    raw = json.loads(ActorDecisionEnvelope(
        decision_id="d", mission_id="m", run_id="r", mission_revision=0,
        obligation_id="o",
        frame=FrameCommitIdentity(
            1, 0, 1, (0, 0, 1, 1), "full_desktop", (1, 1), "c" * 64,
        ),
        target=None,
        pointer=PointerLeaseState(PointerDisposition.RELEASED),
        broker_projection_id="p",
        capability_grant_ids=(), allowed_evidence_scope=(), proposal=_action(),
    ).to_json())
    with pytest.raises(InvalidActorEnvelope, match="explicit format"):
        ActorDecisionEnvelope.from_dict({key: value for key, value in raw.items() if key != "format"})
    with pytest.raises(InvalidActorEnvelope):
        ActorDecisionEnvelope.from_dict({
            **raw,
            "frame": {**raw["frame"], "region": (0, 0, 0, 1)},
        })
    with pytest.raises(InvalidActorEnvelope):
        ActorDecisionEnvelope.from_dict({**raw, "mission_revision": True})
    with pytest.raises(InvalidActorEnvelope):
        ActorDecisionEnvelope.from_dict({**raw, "unexpected": "authority"})


@pytest.mark.parametrize(
    "field,value",
    (
        ("scope", ""),
        ("frame_id", 0),
        ("image_sha256", "not-a-digest"),
    ),
)
def test_envelope_rejects_invalid_frame_commit_identity(field: str, value: object) -> None:
    envelope = ActorDecisionEnvelope(
        decision_id="d", mission_id="m", run_id="r", mission_revision=0,
        obligation_id="o",
        frame=FrameCommitIdentity(
            1, 0, 1, (0, 0, 1, 1), "full_desktop", (1, 1), "c" * 64,
        ),
        target=None,
        pointer=PointerLeaseState(PointerDisposition.RELEASED),
        broker_projection_id="p", capability_grant_ids=(),
        allowed_evidence_scope=(), proposal=_action(),
    )
    raw = json.loads(envelope.to_json())
    with pytest.raises(InvalidActorEnvelope):
        ActorDecisionEnvelope.from_dict({
            **raw,
            "frame": {**raw["frame"], field: value},
        })


def test_envelope_rejects_frame_target_identity_disagreement() -> None:
    frame = FrameCommitIdentity(
        1, 4, 10, (0, 0, 100, 100), "target_window", (100, 100), "c" * 64,
    )
    target = TargetCommitIdentity(
        "binding", 11, 11, 7, 4, (0, 0, 100, 100),
    )
    with pytest.raises(ValidationError, match="frame and target identity disagree"):
        ActorDecisionEnvelope(
            decision_id="d", mission_id="m", run_id="r", mission_revision=0,
            obligation_id="o", frame=frame, target=target,
            pointer=PointerLeaseState(PointerDisposition.RELEASED),
            broker_projection_id="p", capability_grant_ids=(),
            allowed_evidence_scope=(), proposal=_action(),
        )


def test_actor_turn_projection_strips_runtime_identity_and_keeps_semantics() -> None:
    assert ACTOR_TURN_INPUT_FORMAT == "actor-turn-input/3"
    assert ACTOR_MISSION_VIEW_FORMAT == "actor-mission-view/2"
    turn = project_actor_turn_input(_card(), _frame(), _catalog(), _projection())
    raw = json.loads(turn.to_json())
    assert raw["format"] == ACTOR_TURN_INPUT_FORMAT
    assert raw["mission"]["format"] == ACTOR_MISSION_VIEW_FORMAT
    assert raw["frame"] == {
        "format": ACTOR_FRAME_VIEW_FORMAT,
        "image_available": True,
        "authoritative": True,
        "fresh": True,
        "coordinate_space": "submitted_image_pixels",
    }
    assert raw["tools"][0]["format"] == ACTOR_TOOL_VIEW_FORMAT
    assert raw["tools"][0]["requires_local_intent"] is True
    assert raw["tools"][0]["schema"]["properties"]["image_x"] == {"type": "number"}
    text = turn.to_json()
    for secret in (
        "mission-actor-test", "obligation-current", "artifact-runtime-1",
        "evidence-runtime-1", "binding-runtime-1", "projection-runtime-1", "9123",
    ):
        assert secret not in text
    assert raw["mission"]["original_goal"] == "Complete the unknown visible task."
    assert raw["mission"]["active_obligation"]["desired_state"]
    assert raw["mission"]["target_title"] == "Example Surface"
    assert raw["mission"]["unchanged_frame_streak"] == 2
    assert raw["mission"]["last_transition"] == {
        "action": "click",
        "input_completed": True,
        "visible_effect": "The visible surface changed locally",
        "local_visual_change": "unchanged",
        "frame_comparable": True,
        "target_binding_valid": True,
    }
    assert _card().last_transition.action_effect.semantic_completion == "unknown"
    for runtime_identity in (
        "decision-runtime-1",
        "evidence-frame-before-1",
        "evidence-frame-after-1",
        "evidence-action-effect-1",
        "80",
        "81",
    ):
        assert runtime_identity not in text
    assert ActorTurnInput.from_dict(raw).to_json() == turn.to_json()


@pytest.mark.parametrize(
    "mutate",
    (
        lambda raw: raw.update(format="actor-turn-input/2"),
        lambda raw: raw["mission"].update(format="actor-mission-view/1"),
        lambda raw: raw["tools"][0].update(format="actor-tool-view/0"),
        lambda raw: raw["mission"]["last_transition"].update(
            semantic_completion="unknown"
        ),
    ),
)
def test_actor_turn_v3_rejects_stale_formats_and_removed_completion_hint(mutate) -> None:
    raw = json.loads(
        project_actor_turn_input(_card(), _frame(), _catalog(), _projection()).to_json()
    )
    mutate(raw)

    with pytest.raises(InvalidActorView):
        ActorTurnInput.from_dict(raw)


@pytest.mark.parametrize(
    "card,frame,projection",
    (
        (_card(), _frame(fresh=False), _projection()),
        (_card(), _frame(hwnd=9999), _projection()),
        (_card(), _frame(), _projection(mission_revision=5)),
        (_card(), _frame(), _projection(obligation_id="other-obligation")),
        (_card(), _frame(), _projection(target_binding_id="other-binding")),
        (_card(), _frame(), _projection(execution_route=ExecutionRoute.BLOCKED, tool_names=())),
    ),
)
def test_actor_turn_projection_fails_closed_on_stale_or_misaligned_state(
    card: MissionCard,
    frame: AuthoritativeFrameState,
    projection: BrokerProjection,
) -> None:
    with pytest.raises(InvalidActorView):
        project_actor_turn_input(card, frame, _catalog(), projection)


def test_actor_tool_schema_is_strict_json_identity_free_and_immutable() -> None:
    turn = project_actor_turn_input(_card(), _frame(), _catalog(), _projection())
    tool = turn.tools[0]
    with pytest.raises(TypeError):
        tool.input_schema["properties"]["image_x"]["type"] = "string"
    bad_schema = {
        "type": "object",
        "properties": {"frameId": {"type": "integer"}},
    }
    with pytest.raises(ValidationError, match="Runtime-owned identity"):
        project_actor_turn_input(_card(), _frame(), _catalog(bad_schema), _projection())


def test_actor_package_has_no_model_controller_or_product_runtime_imports() -> None:
    actor_dir = Path(__file__).parents[1] / "winpilot" / "vnext" / "actor"
    forbidden = (
        "openai", "winpilot.agent", "winpilot.action", "winpilot.fast",
        "winpilot.model", "winpilot.server", "winpilot.tasking",
    )
    for path in actor_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names = ((node.module or ""),)
            else:
                continue
            assert not any(name.startswith(forbidden) for name in names), (path, names)
