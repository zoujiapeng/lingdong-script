"""Pure P2a contract, policy, serialization, and injected shadow tests."""
from __future__ import annotations

import math
import random

import pytest

from winpilot.vnext.profiling import (
    CognitiveServices,
    DEFAULT_CAPABILITY_CATALOG,
    ExecutionDepth,
    ExecutionPolicy,
    ExecutionProfile,
    InvalidProfileSchema,
    ProfileShadowCase,
    ProfileShadowEvaluator,
    ProfileShadowResult,
    ShadowExpectation,
    TaskDimensions,
    TaskProfileProposal,
    UnsupportedCapability,
)


ALL_PROVIDERS = tuple(CognitiveServices._FIELDS)


def _proposal(**overrides) -> TaskProfileProposal:
    values = {
        "dimensions": TaskDimensions(),
        "recommended_depth": ExecutionDepth.STANDARD,
        "minimum_depth": ExecutionDepth.DIRECT,
        "requested_services": CognitiveServices(),
        "broad_capability_hypotheses": ("visual.interact",),
        "upgrade_triggers": ("new cross-application dependency",),
        "downgrade_conditions": ("evidence becomes deterministic",),
        "confidence": 0.95,
        "rationale": ("ordinary semantic pressure",),
    }
    values.update(overrides)
    return TaskProfileProposal(**values)


def _case(case_id: str, proposal: TaskProfileProposal, **overrides) -> ProfileShadowCase:
    values = {
        "case_id": case_id,
        "mission_id": f"mission-{case_id}",
        "goal_revision": 0,
        "proposal": proposal,
        "available_capabilities": tuple(sorted(DEFAULT_CAPABILITY_CATALOG)),
        "provider_availability": ALL_PROVIDERS,
    }
    values.update(overrides)
    return ProfileShadowCase(**values)


def test_contracts_are_strict_versioned_and_round_trip_canonically() -> None:
    proposal = _proposal()
    assert TaskProfileProposal.from_dict(proposal.to_dict()) == proposal
    profile = ExecutionPolicy(provider_availability=ALL_PROVIDERS).evaluate(
        proposal, mission_id="m1", goal_revision=3
    )
    assert ExecutionProfile.from_dict(profile.to_dict()) == profile
    assert proposal.format == "task-profile-proposal/0"
    assert profile.format == "execution-profile/0"

    for invalid in (math.nan, math.inf, -0.1, 1.1, True):
        raw = {**proposal.to_dict(), "confidence": invalid}
        with pytest.raises(InvalidProfileSchema):
            TaskProfileProposal.from_dict(raw)
    with pytest.raises(InvalidProfileSchema):
        TaskProfileProposal.from_dict({**proposal.to_dict(), "format": "task-profile-proposal/9"})
    with pytest.raises(InvalidProfileSchema):
        ExecutionProfile.from_dict({**profile.to_dict(), "format": "execution-profile/9"})
    with pytest.raises(InvalidProfileSchema):
        ExecutionProfile.from_dict({**profile.to_dict(), "unexpected": True})


@pytest.mark.parametrize(
    "field,value",
    (
        ("runtime_version", "vnext"),
        ("application_workflow", ["open", "search"]),
        ("obligation_patch", {"add": "x"}),
        ("button", "submit"),
        ("tool", "click"),
        ("coordinate", [10, 20]),
        ("action", {"kind": "click"}),
        ("run_id", "run-1"),
        ("mission_id", "mission-1"),
        ("frame_id", "frame-1"),
    ),
)
def test_proposal_rejects_runtime_gui_plan_and_identity_authority(field, value) -> None:
    with pytest.raises(InvalidProfileSchema, match="forbidden|non-canonical"):
        TaskProfileProposal.from_dict({**_proposal().to_dict(), field: value})


def test_nested_forbidden_authority_is_rejected_before_policy() -> None:
    raw = _proposal().to_dict()
    raw["rationale"] = [{"runtime_version": "legacy"}]
    with pytest.raises(InvalidProfileSchema, match="runtime_version"):
        TaskProfileProposal.from_dict(raw)


@pytest.mark.parametrize(
    "proposal,reason",
    (
        (None, "proposal_missing"),
        ({"not": "canonical"}, "proposal_schema_invalid"),
        (_proposal(confidence=0.2), "proposal_low_confidence"),
        (
            _proposal(broad_capability_hypotheses=("vendor.secret",)),
            "proposal_unknown_capability:",
        ),
    ),
)
def test_policy_safe_standard_fallback_is_audited(proposal, reason) -> None:
    profile = ExecutionPolicy(min_confidence=0.6).evaluate(
        proposal, mission_id="m1", goal_revision=0
    )
    assert profile.source == "safe_default"
    assert profile.accepted_depth is ExecutionDepth.STANDARD
    assert profile.services == CognitiveServices(
        planner=False,
        obligation_verifier=False,
        mission_verifier=False,
        visual_inspector=False,
        deep_reasoning=False,
    )
    assert any(item.startswith(reason) for item in profile.policy_reasons)
    assert "provider_unavailable:obligation_verifier" in profile.policy_reasons


def test_unknown_or_noncanonical_capabilities_fail_closed() -> None:
    policy = ExecutionPolicy()
    with pytest.raises(UnsupportedCapability):
        policy.validate_proposal(
            _proposal(broad_capability_hypotheses=("vendor.secret",))
        )
    with pytest.raises(InvalidProfileSchema):
        _proposal(broad_capability_hypotheses=("ClickTool",))
    with pytest.raises(UnsupportedCapability):
        policy.evaluate(
            _proposal(),
            mission_id="m",
            goal_revision=0,
            available_capabilities=("visual.interact", "vendor.secret"),
        )


def test_provider_and_runtime_availability_can_only_tighten() -> None:
    proposal = _proposal(
        dimensions=TaskDimensions(
            step_dependency="multi_stage",
            application_scope="cross_application",
            artifact_flow="handoff",
            external_wait="persistent",
            irreversibility="high",
            visual_precision="geometric",
            continuous_pointer="cross_frame",
            completion_observability="weak",
            identity_binding="multiple",
            user_interaction="ongoing",
        ),
        recommended_depth=ExecutionDepth.ORCHESTRATED,
        minimum_depth=ExecutionDepth.STANDARD,
        requested_services=CognitiveServices(
            planner=True,
            obligation_verifier=True,
            mission_verifier=True,
            visual_inspector=True,
            deep_reasoning=True,
        ),
        broad_capability_hypotheses=("visual.interact", "pointer.dynamic"),
    )
    policy = ExecutionPolicy(provider_availability={"obligation_verifier": True})
    profile = policy.evaluate(
        proposal,
        mission_id="m1",
        goal_revision=1,
        available_capabilities=("visual.interact",),
    )
    assert profile.accepted_depth is ExecutionDepth.ORCHESTRATED
    assert profile.accepted_capabilities == ("visual.interact",)
    assert profile.services == CognitiveServices(
        planner=False,
        obligation_verifier=True,
        mission_verifier=False,
        visual_inspector=False,
        deep_reasoning=False,
    )
    assert "capability_unavailable:pointer.dynamic" in profile.policy_reasons
    assert "provider_unavailable:planner" in profile.policy_reasons
    with pytest.raises(InvalidProfileSchema):
        ExecutionPolicy(provider_availability=("unknown_role",))


def test_overprofiled_atomic_task_is_safely_reduced() -> None:
    proposal = _proposal(
        recommended_depth=ExecutionDepth.ORCHESTRATED,
        requested_services=CognitiveServices(
            planner=True,
            obligation_verifier=True,
            mission_verifier=True,
            visual_inspector=True,
            deep_reasoning=True,
        ),
    )
    profile = ExecutionPolicy(provider_availability=ALL_PROVIDERS).evaluate(
        proposal, mission_id="m", goal_revision=2
    )
    assert profile.accepted_depth is ExecutionDepth.STANDARD
    assert not profile.services.planner
    assert not profile.services.mission_verifier
    assert not profile.services.visual_inspector
    assert not profile.services.deep_reasoning
    assert "depth_reduced:no_orchestration_pressure" in profile.policy_reasons


def test_underprofiled_cross_application_handoff_is_raised() -> None:
    proposal = _proposal(
        dimensions=TaskDimensions(
            step_dependency="multi_stage",
            application_scope="cross_application",
            artifact_flow="handoff",
            identity_binding="multiple",
            user_interaction="approval",
        ),
        recommended_depth=ExecutionDepth.DIRECT,
        minimum_depth=ExecutionDepth.DIRECT,
        requested_services=CognitiveServices(
            planner=True, obligation_verifier=False, mission_verifier=True
        ),
    )
    profile = ExecutionPolicy(provider_availability=ALL_PROVIDERS).evaluate(
        proposal, mission_id="m", goal_revision=0
    )
    assert profile.accepted_depth is ExecutionDepth.ORCHESTRATED
    assert profile.services.planner
    assert profile.services.obligation_verifier
    assert profile.services.mission_verifier
    assert "depth_raised:task_pressure" in profile.policy_reasons


def test_shadow_detects_over_and_under_profile_without_mutation() -> None:
    evaluator = ProfileShadowEvaluator(
        ExecutionPolicy(provider_availability=ALL_PROVIDERS)
    )
    over = evaluator.evaluate(
        _case(
            "over",
            _proposal(),
            expectation=ShadowExpectation(
                maximum_depth=ExecutionDepth.DIRECT,
                forbidden_services=("obligation_verifier",),
            ),
        )
    )
    under = evaluator.evaluate(
        _case(
            "under",
            _proposal(),
            expectation=ShadowExpectation(
                minimum_depth=ExecutionDepth.ORCHESTRATED,
                required_services=("planner",),
                required_capabilities=("pointer.dynamic",),
            ),
        )
    )
    assert over.over_profile and not over.under_profile
    assert under.under_profile and not under.over_profile
    assert "required_service_missing:planner" in under.under_profile_reasons


def test_task_rewrite_and_application_label_do_not_change_architecture() -> None:
    first = _proposal(rationale=("short wording",))
    rewritten = _proposal(rationale=("same task expressed another way",))
    evaluator = ProfileShadowEvaluator(
        ExecutionPolicy(provider_availability=ALL_PROVIDERS)
    )
    results = evaluator.replay(
        (
            _case("App Alpha phrasing", first, comparison_group="semantic-1"),
            _case("App Beta phrasing", rewritten, comparison_group="semantic-1"),
        )
    )
    assert results[0].structural_fingerprint == results[1].structural_fingerprint
    assert results[0].profile.mission_id != results[1].profile.mission_id


def test_runtime_version_overreach_falls_back_without_selecting_runtime() -> None:
    raw = {**_proposal().to_dict(), "runtime_version": "vnext"}
    profile = ExecutionPolicy().evaluate(raw, mission_id="m", goal_revision=0)
    assert profile.source == "safe_default"
    assert "proposal_schema_invalid" in profile.policy_reasons
    assert "runtime_version" not in profile.to_dict()


def test_shadow_case_serialization_and_replay_are_deterministic() -> None:
    case = _case("replay", _proposal(), comparison_group="g")
    assert ProfileShadowCase.from_dict(case.to_dict()) == case
    evaluator = ProfileShadowEvaluator(
        ExecutionPolicy(provider_availability=ALL_PROVIDERS)
    )
    first = tuple(item.to_dict() for item in evaluator.replay((case, case)))
    second = tuple(item.to_dict() for item in evaluator.replay((case, case)))
    assert first == second
    assert ProfileShadowResult.from_dict(first[0]).to_dict() == first[0]
    tampered = {**first[0], "structural_fingerprint": "incorrect"}
    with pytest.raises(InvalidProfileSchema, match="fingerprint mismatch"):
        ProfileShadowResult.from_dict(tampered)


def test_property_randomized_policy_is_strict_and_replay_stable() -> None:
    rng = random.Random(17)
    choices = {
        "step_dependency": ("atomic", "local_sequence", "multi_stage"),
        "application_scope": ("current_surface", "single_application", "cross_application", "unknown"),
        "artifact_flow": ("none", "produce", "consume", "handoff"),
        "external_wait": ("none", "bounded", "persistent"),
        "irreversibility": ("low", "medium", "high"),
        "visual_precision": ("ordinary", "fine", "geometric"),
        "continuous_pointer": ("none", "atomic_drag", "cross_frame"),
        "completion_observability": ("deterministic", "visible", "mixed", "weak"),
        "identity_binding": ("none", "target", "recipient", "artifact", "multiple"),
        "user_interaction": ("none", "approval", "clarification", "ongoing"),
    }
    policy = ExecutionPolicy(provider_availability=ALL_PROVIDERS)
    for index in range(120):
        dimensions = TaskDimensions(
            **{name: rng.choice(values) for name, values in choices.items()}
        )
        recommended = rng.choice(tuple(ExecutionDepth))
        minimum = rng.choice(tuple(ExecutionDepth)[: recommended.rank + 1])
        capabilities = tuple(
            sorted(rng.sample(tuple(sorted(DEFAULT_CAPABILITY_CATALOG)), rng.randint(0, 4)))
        )
        proposal = _proposal(
            dimensions=dimensions,
            recommended_depth=recommended,
            minimum_depth=minimum,
            confidence=0.6 + rng.random() * 0.4,
            broad_capability_hypotheses=capabilities,
            requested_services=CognitiveServices(
                planner=bool(rng.getrandbits(1)),
                obligation_verifier=bool(rng.getrandbits(1)),
                mission_verifier=bool(rng.getrandbits(1)),
                visual_inspector=bool(rng.getrandbits(1)),
                deep_reasoning=bool(rng.getrandbits(1)),
            ),
        )
        first = policy.evaluate(proposal, mission_id="m", goal_revision=index)
        second = policy.evaluate(proposal.to_dict(), mission_id="m", goal_revision=index)
        assert first.to_dict() == second.to_dict()
        assert set(first.accepted_capabilities) <= DEFAULT_CAPABILITY_CATALOG
        assert not (
            set(first.to_dict())
            & {"runtime_version", "application_workflow", "tool_sequence", "coordinate"}
        )


def test_top_level_compatibility_is_not_a_second_contract_owner() -> None:
    from winpilot.vnext.execution_profile import TaskProfileProposal as compatibility

    assert compatibility is TaskProfileProposal
