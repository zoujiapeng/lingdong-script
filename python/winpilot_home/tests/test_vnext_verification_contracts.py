"""Pure P5a verifier contracts; no MissionRuntime or model integration."""
from __future__ import annotations

import ast
from dataclasses import replace
from importlib.util import resolve_name
import json
from pathlib import Path
from threading import Event
from typing import Any, Mapping

import pytest

from winpilot.vnext.mission import EvidencePolicy, EvidenceRef
from winpilot.vnext.verification import (
    DeterministicFirstVerifier,
    InvalidVerificationSchema,
    ObligationVerificationRequest,
    ObligationVerificationResult,
    TRUST_ORDER,
    VerifierAssessment,
    forbidden_effect_condition,
)


def _evidence(evidence_id: str, trust: str, *, obligation_id: str | None = "o1") -> EvidenceRef:
    return EvidenceRef(
        id=evidence_id,
        mission_id="mission-1",
        run_id="run-1",
        kind=f"{trust}.proof",
        trust=trust,
        obligation_id=obligation_id,
        payload_hash=f"payload-{evidence_id}",
    )


def _request(
    *,
    criteria: tuple[str, ...] = ("requested state is present",),
    forbidden: tuple[str, ...] = (),
    evidence: tuple[EvidenceRef, ...] = (),
) -> ObligationVerificationRequest:
    return ObligationVerificationRequest(
        mission_id="mission-1",
        run_id="run-1",
        mission_revision=7,
        obligation_id="o1",
        claim_fingerprint="a" * 64,
        desired_state="the requested state exists",
        acceptance_criteria=criteria,
        forbidden_effects=forbidden,
        evidence_policy=EvidencePolicy(),
        evidence_refs=evidence,
    )


class ScriptedEvaluator:
    def __init__(
        self,
        evaluator_id: str,
        evidence_trust: str,
        response: VerifierAssessment | Mapping[str, Any] | None,
        calls: list[tuple[str, tuple[str, ...]]] | None = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self.evaluator_id = evaluator_id
        self.evidence_trust = evidence_trust
        self.response = response
        self.calls = calls if calls is not None else []
        self.raises = raises

    def evaluate(self, request, *, stop_event=None):
        self.calls.append((
            self.evaluator_id,
            tuple(ref.trust for ref in request.evidence_refs),
        ))
        if self.raises is not None:
            raise self.raises
        return self.response


def _partial(condition: str) -> VerifierAssessment:
    return VerifierAssessment(
        verified=False,
        satisfied_conditions=(condition,),
        concise_reason="this evidence tier proves one condition",
    )


def test_assessment_is_versioned_identity_free_and_strict_json() -> None:
    assessment = VerifierAssessment(
        verified=False,
        satisfied_conditions=("one",),
        missing_conditions=("two",),
        confidence=0.5,
        concise_reason="one condition is still missing",
    )
    assert VerifierAssessment.from_json(assessment.to_json()) == assessment
    assert set(assessment.to_dict()) == {
        "format",
        "verified",
        "satisfied_conditions",
        "missing_conditions",
        "contradictions",
        "confidence",
        "concise_reason",
    }
    assert not {
        "mission_id",
        "run_id",
        "revision",
        "obligation_id",
        "subject_id",
        "evidence_refs",
        "verification_id",
        "tool",
        "steps",
        "coordinates",
    } & set(assessment.to_dict())

    raw = assessment.to_dict()
    for field, value in (
        ("mission_id", "smuggled"),
        ("evidence_refs", ["proof"]),
        ("tool", "click"),
        ("steps", ["click the button"]),
    ):
        with pytest.raises(InvalidVerificationSchema, match="unknown"):
            VerifierAssessment.from_dict({**raw, field: value})
    with pytest.raises(InvalidVerificationSchema, match="JSON boolean"):
        VerifierAssessment.from_dict({**raw, "verified": "false"})
    with pytest.raises(InvalidVerificationSchema, match="non-JSON|JSON array"):
        VerifierAssessment.from_dict({**raw, "missing_conditions": ("two",)})
    with pytest.raises(InvalidVerificationSchema, match="non-JSON"):
        VerifierAssessment.from_json(
            json.dumps({**raw, "confidence": float("nan")})
        )


@pytest.mark.parametrize("verified", (1, 0, "true", None))
def test_assessment_constructor_requires_literal_boolean(verified) -> None:
    with pytest.raises(ValueError, match="JSON boolean"):
        VerifierAssessment(verified=verified)


def test_request_round_trip_binds_scope_and_ignores_untrusted_evidence() -> None:
    trusted = _evidence("trusted", "deterministic")
    actor_claim = _evidence("claim", "actor_claim")
    request = _request(
        forbidden=("another document is overwritten",),
        evidence=(actor_claim, trusted),
    )

    assert ObligationVerificationRequest.from_json(request.to_json()) == request
    assert [ref.id for ref in request.trusted_evidence_refs] == ["trusted"]
    assert request.required_conditions == (
        "requested state is present",
        forbidden_effect_condition("another document is overwritten"),
    )
    assert [ref.id for ref in request.for_trust("deterministic").evidence_refs] == [
        "trusted"
    ]

    raw = request.to_dict()
    with pytest.raises(InvalidVerificationSchema, match="integer"):
        ObligationVerificationRequest.from_dict({**raw, "mission_revision": True})
    policy = dict(raw["evidence_policy"])
    policy["allow_independent_visual_verifier"] = "false"
    with pytest.raises(InvalidVerificationSchema, match="JSON boolean"):
        ObligationVerificationRequest.from_dict({**raw, "evidence_policy": policy})
    with pytest.raises(ValueError, match="outside"):
        _request(evidence=(replace(trusted, run_id="other-run"),))
    with pytest.raises(ValueError, match="another obligation"):
        _request(evidence=(replace(trusted, obligation_id="other-obligation"),))


def test_runtime_binding_accepts_real_visual_trust_as_policy_gated_evidence() -> None:
    visual = _evidence("visual", "independent_visual_model")
    request = _request(evidence=(visual,))
    assessment = VerifierAssessment(
        verified=True,
        satisfied_conditions=request.required_conditions,
        missing_conditions=(),
        contradictions=(),
        concise_reason="current pixels cover the requested condition",
    )

    result = ObligationVerificationResult.bind(
        request,
        assessment,
        verifier_kind="independent_visual_model",
        evidence_refs=(visual.id,),
    )

    assert result.evidence_refs == (visual.id,)


def test_runtime_binding_is_stable_and_requires_full_coverage_and_trusted_evidence() -> None:
    proof = _evidence("proof", "deterministic")
    request = _request(
        forbidden=("unrelated content is changed",),
        evidence=(proof,),
    )
    incomplete = VerifierAssessment(
        verified=True,
        satisfied_conditions=request.acceptance_criteria,
        concise_reason="forbidden effects were not checked",
    )
    with pytest.raises(InvalidVerificationSchema, match="every required condition"):
        ObligationVerificationResult.bind(
            request,
            incomplete,
            verifier_kind="deterministic_first",
        )

    complete = VerifierAssessment(
        verified=True,
        satisfied_conditions=request.required_conditions,
        confidence=1.0,
        concise_reason="all conditions are proven",
    )
    first = ObligationVerificationResult.bind(
        request,
        complete,
        verifier_kind="deterministic_first",
    )
    second = ObligationVerificationResult.bind(
        request,
        complete,
        verifier_kind="deterministic_first",
    )
    assert first == second
    assert first.subject_id == request.obligation_id
    assert first.mission_revision == request.mission_revision
    assert first.evidence_refs == ("proof",)
    assert ObligationVerificationResult.from_json(first.to_json()) == first

    with pytest.raises(InvalidVerificationSchema, match="untrusted or unbound"):
        ObligationVerificationResult.bind(
            request,
            complete,
            verifier_kind="deterministic_first",
            evidence_refs=("not-in-request",),
        )
    with pytest.raises(InvalidVerificationSchema, match="trusted evidence"):
        ObligationVerificationResult.bind(
            _request(forbidden=request.forbidden_effects, evidence=()),
            complete,
            verifier_kind="deterministic_first",
        )

    payload = first.to_mission_event_payload(request)
    assert payload["satisfied_criteria"] == list(request.acceptance_criteria)
    assert payload["checked_forbidden_effects"] == list(request.forbidden_effects)
    assert all(
        not value.startswith("forbidden_effect_absent:")
        for key in ("satisfied_criteria", "checked_forbidden_effects")
        for value in payload[key]
    )


def test_dispatcher_uses_actual_trust_order_and_combines_partial_coverage() -> None:
    conditions = ("criterion-a", "criterion-b", "criterion-c")
    forbidden = ("forbidden-d",)
    required = conditions + (forbidden_effect_condition(forbidden[0]),)
    calls: list[tuple[str, tuple[str, ...]]] = []
    evaluators = [
        ScriptedEvaluator("harness", "trusted_external_harness", _partial(required[3]), calls),
        ScriptedEvaluator("receipt", "provider_receipt", _partial(required[2]), calls),
        ScriptedEvaluator("oracle", "application_oracle", _partial(required[1]), calls),
        ScriptedEvaluator("local", "deterministic", _partial(required[0]), calls),
    ]
    request = _request(
        criteria=conditions,
        forbidden=forbidden,
        evidence=tuple(
            _evidence(f"evidence-{index}", trust)
            for index, trust in enumerate(TRUST_ORDER)
        ),
    )

    assessment = DeterministicFirstVerifier(evaluators).verify(request)

    assert assessment.verified is True
    assert assessment.satisfied_conditions == required
    assert [item[0] for item in calls] == ["local", "oracle", "receipt", "harness"]
    assert all(trusts == (TRUST_ORDER[index],) for index, (_, trusts) in enumerate(calls))


def test_no_evaluator_or_incomplete_coverage_is_inconclusive() -> None:
    forbidden = ("existing work is damaged",)
    request = _request(
        criteria=("criterion-a", "criterion-b"),
        forbidden=forbidden,
        evidence=(_evidence("proof", "deterministic"),),
    )
    empty = DeterministicFirstVerifier().verify(request)
    assert empty.verified is False
    assert empty.missing_conditions == request.required_conditions
    assert empty.contradictions == ()

    partial = DeterministicFirstVerifier((
        ScriptedEvaluator(
            "local",
            "deterministic",
            _partial("criterion-a"),
        ),
    )).verify(request)
    assert partial.verified is False
    assert partial.satisfied_conditions == ("criterion-a",)
    assert partial.missing_conditions == (
        "criterion-b",
        forbidden_effect_condition(forbidden[0]),
    )


def test_contradiction_overrides_complete_positive_coverage() -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []
    request = _request(evidence=(
        _evidence("local-proof", "deterministic"),
        _evidence("oracle-proof", "application_oracle"),
    ))
    positive = VerifierAssessment(
        verified=True,
        satisfied_conditions=request.required_conditions,
        concise_reason="local state appears complete",
    )
    contradiction = VerifierAssessment(
        verified=False,
        contradictions=("application oracle reports the state is absent",),
        concise_reason="oracle contradiction",
    )
    verifier = DeterministicFirstVerifier((
        ScriptedEvaluator("local", "deterministic", positive, calls),
        ScriptedEvaluator("oracle", "application_oracle", contradiction, calls),
    ))

    assessment = verifier.verify(request)

    assert assessment.verified is False
    assert assessment.contradictions == contradiction.contradictions
    assert [item[0] for item in calls] == ["local", "oracle"]


def test_evaluator_is_never_called_without_matching_actual_evidence_trust() -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []
    request = _request(evidence=(_evidence("claim", "actor_claim"),))
    evaluator = ScriptedEvaluator(
        "payload-claims-deterministic",
        "deterministic",
        VerifierAssessment(
            verified=True,
            satisfied_conditions=request.required_conditions,
            concise_reason="payload self-reported deterministic trust",
        ),
        calls,
    )

    assessment = DeterministicFirstVerifier((evaluator,)).verify(request)

    assert assessment.verified is False
    assert calls == []


def test_malformed_or_raising_evaluator_fails_closed() -> None:
    proof = _evidence("proof", "deterministic")
    request = _request(evidence=(proof,))
    malformed = VerifierAssessment(
        verified=False,
        missing_conditions=request.required_conditions,
        concise_reason="template",
    ).to_dict()
    malformed["verified"] = "true"
    verifier = DeterministicFirstVerifier((
        ScriptedEvaluator("broken", "deterministic", malformed),
        ScriptedEvaluator(
            "raising",
            "deterministic",
            None,
            raises=RuntimeError("offline"),
        ),
    ))

    assessment = verifier.verify(request)

    assert assessment.verified is False
    assert assessment.missing_conditions == request.required_conditions
    assert "failed closed" in assessment.concise_reason


def test_canceled_dispatch_is_inconclusive_without_evaluator_call() -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []
    request = _request(evidence=(_evidence("proof", "deterministic"),))
    evaluator = ScriptedEvaluator("local", "deterministic", None, calls)
    stop = Event()
    stop.set()

    assessment = DeterministicFirstVerifier((evaluator,)).verify(
        request,
        stop_event=stop,
    )

    assert assessment.verified is False
    assert assessment.missing_conditions == request.required_conditions
    assert calls == []


def test_verification_package_has_no_mutation_owner_sdk_or_legacy_done_import() -> None:
    root = Path(__file__).parents[1] / "winpilot" / "vnext" / "verification"
    assert {path.name for path in root.glob("*.py")} == {
        "__init__.py",
        "action_effect.py",
        "contracts.py",
        "dispatcher.py",
        "mission_completion.py",
        "ports.py",
    }
    forbidden = (
        "anthropic",
        "cohere",
        "google.genai",
        "google.generativeai",
        "groq",
        "mistralai",
        "ollama",
        "openai",
        "together",
        "win32",
        "winpilot.action",
        "winpilot.agent.done_verifier",
        "winpilot.agent.loop",
        "winpilot.agent.tools",
        "winpilot.fast.controller",
        "winpilot.modeling",
        "winpilot.orchestrator",
        "winpilot.server",
        "winpilot.vnext.actor",
        "winpilot.vnext.bridge",
        "winpilot.vnext.capabilities",
        "winpilot.vnext.profiling",
        "winpilot.vnext.runtime",
        "winpilot.vnext.targeting",
    )
    offenders: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*.py")):
        module = "winpilot.vnext.verification"
        if path.name != "__init__.py":
            module += "." + path.stem
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                raw = f"{'.' * node.level}{node.module or ''}"
                imported = [resolve_name(raw, module.rpartition(".")[0])] if node.level else [raw]
            for name in imported:
                if any(name == item or name.startswith(item + ".") for item in forbidden):
                    offenders.append((path.name, node.lineno, name))
    assert offenders == []
