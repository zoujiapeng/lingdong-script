"""Pure P2a contract, policy, and injected shadow tests.

These tests never create a model client, start the legacy runner, or touch a
desktop.  The injected capability/service sets stand in for runtime facts.
"""
from __future__ import annotations

import random

import pytest

from winpilot.vnext import (
    CognitiveServices,
    ExecutionDepth,
    ExecutionPolicy,
    ExecutionProfile,
    InvalidProfileSchema,
    TaskDimensions,
    TaskProfileProposal,
    UnsupportedCapability,
)


def _proposal(**overrides) -> TaskProfileProposal:
    values = {
        "dimensions": TaskDimensions(),
        "recommended_depth": ExecutionDepth.STANDARD,
        "minimum_depth": ExecutionDepth.DIRECT,
        "requested_services": CognitiveServices(),
        "broad_capability_hypotheses": ("visual.interact",),
        "upgrade_triggers": ("new cross-application dependency",),
        "downgrade_conditions": ("evidence is deterministic",),
        "confidence": 0.95,
        "rationale": ("semantic pressure is currently ordinary",),
    }
    values.update(overrides)
    return TaskProfileProposal(**values)


def test_p2a_contracts_are_strict_and_round_trip_canonically() -> None:
    proposal = _proposal()
    assert TaskProfileProposal.from_dict(proposal.to_dict()) == proposal
    assert "runtime_version" not in proposal.to_dict()
    assert "application_workflow" not in proposal.to_dict()
    assert "coordinate" not in proposal.to_dict()

    profile = ExecutionPolicy().evaluate(proposal, mission_id="m1", goal_revision=3)
    assert ExecutionProfile.from_dict(profile.to_dict()) == profile
    assert profile.format == "execution-profile/0"
    assert "runtime_version" not in profile.to_dict()

    with pytest.raises(InvalidProfileSchema):
        TaskProfileProposal.from_dict({**proposal.to_dict(), "runtime_version": "x"})
    with pytest.raises(InvalidProfileSchema):
        TaskProfileProposal.from_dict({**proposal.to_dict(), "dimensions": {**proposal.to_dict()["dimensions"], "coordinate": "x"}})
    with pytest.raises(InvalidProfileSchema):
        TaskProfileProposal.from_dict({key: value for key, value in proposal.to_dict().items() if key != "confidence"})
    with pytest.raises(InvalidProfileSchema):
        TaskProfileProposal.from_dict({**proposal.to_dict(), "format": "task-profile-proposal/9"})
    with pytest.raises(InvalidProfileSchema):
        CognitiveServices.from_dict({**CognitiveServices().to_dict(), "planner": 1})
    with pytest.raises(InvalidProfileSchema):
        ExecutionProfile.from_dict({**profile.to_dict(), "format": "execution-profile/9"})
    with pytest.raises(InvalidProfileSchema):
        ExecutionProfile.from_dict({**profile.to_dict(), "unexpected": True})


def test_policy_uses_safe_standard_for_missing_malformed_low_confidence_and_unsupported() -> None:
    policy = ExecutionPolicy(min_confidence=0.6)
    for proposal, reason in (
        (None, "proposal_missing"),
        ({"not": "canonical"}, "proposal_malformed_or_unsupported"),
        (_proposal(confidence=0.2), "proposal_low_confidence"),
        (_proposal(broad_capability_hypotheses=("vendor.secret",)), "proposal_malformed_or_unsupported"),
    ):
        profile = policy.evaluate(proposal, mission_id="m1", goal_revision=0)
        assert profile.source == "safe_default"
        assert profile.accepted_depth is ExecutionDepth.STANDARD
        assert profile.policy_reasons[0] == "safe_standard_default"
        assert reason in profile.policy_reasons

    unavailable = policy.evaluate(
        None,
        mission_id="m1",
        goal_revision=0,
        available_services={"obligation_verifier": False},
    )
    assert unavailable.services.obligation_verifier is False
    assert "service_unavailable:obligation_verifier" in unavailable.policy_reasons

    with pytest.raises(UnsupportedCapability):
        policy.validate_proposal(_proposal(broad_capability_hypotheses=("vendor.secret",)))


def test_policy_injection_restricts_services_and_capabilities_without_runtime_calls() -> None:
    policy = ExecutionPolicy(
        capability_catalog={"visual.interact", "pointer.dynamic"},
        available_services={"obligation_verifier": True, "planner": False},
    )
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
    profile = policy.evaluate(proposal, mission_id="m1", goal_revision=1)
    assert profile.accepted_depth is ExecutionDepth.ORCHESTRATED
    assert profile.accepted_capabilities == ("pointer.dynamic", "visual.interact")
    assert profile.services.planner is False
    assert profile.services.obligation_verifier is True
    assert profile.services.mission_verifier is False
    assert profile.services.visual_inspector is False
    assert profile.services.deep_reasoning is False
    assert any(reason.startswith("service_unavailable:") for reason in profile.policy_reasons)


def test_policy_reduces_overprofiled_standard_pressure_deterministically() -> None:
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
    first = ExecutionPolicy().evaluate(proposal, mission_id="m", goal_revision=2)
    second = ExecutionPolicy().evaluate(proposal, mission_id="m", goal_revision=2)
    assert first.to_dict() == second.to_dict()
    assert first.accepted_depth is ExecutionDepth.STANDARD
    assert first.services.planner is False
    assert first.services.mission_verifier is False
    assert first.services.visual_inspector is False
    assert first.services.deep_reasoning is False
    assert "orchestrated_depth_reduced_without_structural_pressure" in first.policy_reasons


def test_property_style_random_dimensions_are_replay_stable() -> None:
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
    policy = ExecutionPolicy()
    for index in range(80):
        dimensions = TaskDimensions(**{name: rng.choice(values) for name, values in choices.items()})
        depth = rng.choice(tuple(ExecutionDepth))
        minimum = min(tuple(ExecutionDepth), key=lambda item: abs(item.rank - rng.randint(0, depth.rank)))
        proposal = _proposal(
            dimensions=dimensions,
            recommended_depth=depth,
            minimum_depth=minimum,
            confidence=0.6 + rng.random() * 0.4,
            requested_services=CognitiveServices(
                planner=bool(rng.getrandbits(1)),
                obligation_verifier=True,
                mission_verifier=bool(rng.getrandbits(1)),
                visual_inspector=bool(rng.getrandbits(1)),
                deep_reasoning=bool(rng.getrandbits(1)),
            ),
        )
        first = policy.evaluate(proposal, mission_id="m", goal_revision=index)
        second = policy.evaluate(proposal.to_dict(), mission_id="m", goal_revision=index)
        assert first.to_dict() == second.to_dict()
        assert set(first.accepted_capabilities) <= policy.capability_catalog
        assert not (set(first.to_dict()) & {"runtime_version", "application_workflow", "tool_sequence", "coordinate"})
