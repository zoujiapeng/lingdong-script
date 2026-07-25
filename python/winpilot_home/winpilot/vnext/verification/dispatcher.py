"""Deterministic-first dispatcher for injected read-only evidence evaluators."""
from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Any, Mapping, Protocol

from .contracts import (
    TRUST_ORDER,
    InvalidVerificationSchema,
    ObligationVerificationRequest,
    VerifierAssessment,
    _condition_key,
)


class EvidenceEvaluator(Protocol):
    """One injected evaluator restricted to evidence of a declared trust tier."""

    evaluator_id: str
    evidence_trust: str

    def evaluate(
        self,
        request: ObligationVerificationRequest,
        *,
        stop_event: Event | None = None,
    ) -> VerifierAssessment | Mapping[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class _RegisteredEvaluator:
    evaluator_id: str
    evidence_trust: str
    evaluator: EvidenceEvaluator


def _missing(request: ObligationVerificationRequest, satisfied: set[str]) -> tuple[str, ...]:
    return tuple(
        condition
        for condition in request.required_conditions
        if _condition_key(condition) not in satisfied
    )


def _inconclusive(
    request: ObligationVerificationRequest,
    *,
    satisfied: set[str] | None = None,
    reason: str,
    confidence: float | None = None,
) -> VerifierAssessment:
    covered = satisfied or set()
    return VerifierAssessment(
        verified=False,
        satisfied_conditions=tuple(
            condition
            for condition in request.required_conditions
            if _condition_key(condition) in covered
        ),
        missing_conditions=_missing(request, covered),
        contradictions=(),
        confidence=confidence,
        concise_reason=reason,
    )


class DeterministicFirstVerifier:
    """Compose trusted, injected facts without interpreting task prose.

    Evaluators are called only when the request contains evidence whose actual
    ``EvidenceRef.trust`` matches their declared tier.  Each evaluator sees
    only that tier.  Coverage may be combined across tiers, but any explicit
    contradiction fails closed before a positive result is returned.
    """

    verifier_kind = "deterministic_first"

    def __init__(self, evaluators: tuple[EvidenceEvaluator, ...] | list[EvidenceEvaluator] = ()) -> None:
        rank = {trust: index for index, trust in enumerate(TRUST_ORDER)}
        registered: list[_RegisteredEvaluator] = []
        seen: set[str] = set()
        for evaluator in evaluators:
            evaluator_id = getattr(evaluator, "evaluator_id", None)
            trust = getattr(evaluator, "evidence_trust", None)
            if type(evaluator_id) is not str or not evaluator_id.strip():
                raise InvalidVerificationSchema("evaluator_id must be a non-empty string")
            evaluator_id = evaluator_id.strip()
            if evaluator_id in seen:
                raise InvalidVerificationSchema("evaluator_id must be unique")
            if trust not in rank:
                raise InvalidVerificationSchema(
                    f"evaluator {evaluator_id!r} has unsupported evidence trust"
                )
            if not callable(getattr(evaluator, "evaluate", None)):
                raise InvalidVerificationSchema(
                    f"evaluator {evaluator_id!r} has no evaluate method"
                )
            seen.add(evaluator_id)
            registered.append(_RegisteredEvaluator(evaluator_id, trust, evaluator))
        self._evaluators = tuple(sorted(
            registered,
            key=lambda item: (rank[item.evidence_trust], item.evaluator_id),
        ))

    @property
    def evaluator_ids(self) -> tuple[str, ...]:
        return tuple(item.evaluator_id for item in self._evaluators)

    def verify(
        self,
        request: ObligationVerificationRequest,
        *,
        stop_event: Event | None = None,
    ) -> VerifierAssessment:
        if not isinstance(request, ObligationVerificationRequest):
            raise InvalidVerificationSchema(
                "request must be ObligationVerificationRequest"
            )
        if stop_event is not None and stop_event.is_set():
            return _inconclusive(request, reason="verification was canceled")

        required = {
            _condition_key(condition): condition
            for condition in request.required_conditions
        }
        satisfied: set[str] = set()
        confidence_values: list[float] = []
        reports = 0
        evaluator_errors = 0

        for registration in self._evaluators:
            if stop_event is not None and stop_event.is_set():
                return _inconclusive(
                    request,
                    satisfied=satisfied,
                    reason="verification was canceled",
                    confidence=min(confidence_values) if confidence_values else None,
                )
            scoped = request.for_trust(registration.evidence_trust)
            if not scoped.evidence_refs:
                continue
            try:
                raw = registration.evaluator.evaluate(
                    scoped,
                    stop_event=stop_event,
                )
                if raw is None:
                    continue
                assessment = (
                    raw
                    if isinstance(raw, VerifierAssessment)
                    else VerifierAssessment.from_dict(raw)
                )
            except Exception:
                evaluator_errors += 1
                continue
            supplied = {
                _condition_key(condition): condition
                for condition in assessment.satisfied_conditions
            }
            missing = {
                _condition_key(condition)
                for condition in assessment.missing_conditions
            }
            if assessment.contradictions:
                contradiction_keys = {
                    _condition_key(item) for item in assessment.contradictions
                }
                consistent_satisfied = satisfied - contradiction_keys
                return VerifierAssessment(
                    verified=False,
                    satisfied_conditions=tuple(
                        condition
                        for condition in request.required_conditions
                        if _condition_key(condition) in consistent_satisfied
                    ),
                    missing_conditions=_missing(request, consistent_satisfied),
                    contradictions=assessment.contradictions,
                    confidence=assessment.confidence,
                    concise_reason=(
                        "trusted evidence contradicts the obligation; positive "
                        "evidence cannot override it"
                    ),
                )
            if (set(supplied) | missing) - set(required):
                evaluator_errors += 1
                continue
            reports += 1
            if assessment.confidence is not None:
                confidence_values.append(assessment.confidence)
            satisfied.update(set(supplied) & set(required))

        confidence = min(confidence_values) if confidence_values else None
        if set(required).issubset(satisfied) and reports:
            return VerifierAssessment(
                verified=True,
                satisfied_conditions=request.required_conditions,
                missing_conditions=(),
                contradictions=(),
                confidence=confidence,
                concise_reason=(
                    "read-only evidence evaluators covered every acceptance "
                    "criterion and forbidden-effect check"
                ),
            )
        if not reports:
            reason = "no trusted evaluator produced a usable assessment"
        else:
            reason = "trusted evaluators did not cover every required condition"
        if evaluator_errors:
            reason += f"; {evaluator_errors} evaluator result(s) failed closed"
        return _inconclusive(
            request,
            satisfied=satisfied,
            reason=reason,
            confidence=confidence,
        )


__all__ = ["EvidenceEvaluator", "DeterministicFirstVerifier"]
