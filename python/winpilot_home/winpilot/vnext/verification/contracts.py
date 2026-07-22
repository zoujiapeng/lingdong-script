"""Strict P5a contracts for read-only obligation verification.

Evaluators emit only identity-free :class:`VerifierAssessment` values.
Runtime identity and immutable evidence references are added later by
``ObligationVerificationResult.bind``.  This module has no model, desktop, or
Mission mutation dependency.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Any, Iterable, Literal, Mapping

from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError, model_validator

from ..mission import EvidencePolicy, EvidenceRef


VERIFIER_ASSESSMENT_FORMAT = "verifier-assessment/0"
OBLIGATION_VERIFICATION_REQUEST_FORMAT = "obligation-verification-request/0"
OBLIGATION_VERIFICATION_RESULT_FORMAT = "obligation-verification-result/0"
VERIFICATION_SCOPE = "obligation"
TRUST_ORDER = (
    "deterministic",
    "application_oracle",
    "provider_receipt",
    "trusted_external_harness",
)
FORBIDDEN_EFFECT_CONDITION_PREFIX = "forbidden_effect_absent:"


class VerificationContractError(ValueError):
    """A verification value does not satisfy its canonical contract."""


class InvalidVerificationSchema(VerificationContractError):
    """A wire value is malformed, non-JSON, or contains unknown fields."""


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _literal_bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a JSON boolean")
    return value


def _confidence(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("confidence must be null or a finite number in [0, 1]")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError("confidence must be null or a finite number in [0, 1]")
    return result


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a JSON array of strings")
    result = tuple(_text(item, f"{name}[]") for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must not contain duplicates")
    return result


def _array(value: Any, name: str) -> list[Any] | tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be an array")
    return value


def _wire_text(value: Any) -> str:
    return _text(value, "value")


def _wire_bool(value: Any) -> bool:
    return _literal_bool(value, "value")


def _wire_integer(value: Any) -> int:
    return _integer(value, "value")


def _wire_strings(value: Any) -> tuple[str, ...]:
    return _strings(value, "value")


WireText = Annotated[str, BeforeValidator(_wire_text)]
WireBool = Annotated[bool, BeforeValidator(_wire_bool)]
WireInteger = Annotated[int, BeforeValidator(_wire_integer)]
WireStrings = Annotated[tuple[str, ...], BeforeValidator(_wire_strings)]
WireConfidence = Annotated[float | None, BeforeValidator(_confidence)]


def _json_value(value: Any, path: str = "value", seen: set[int] | None = None) -> None:
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise InvalidVerificationSchema(f"{path} contains a non-JSON number")
        return
    if type(value) not in {list, dict}:
        raise InvalidVerificationSchema(f"{path} contains a non-JSON value")
    ancestors = seen if seen is not None else set()
    marker = id(value)
    if marker in ancestors:
        raise InvalidVerificationSchema(f"{path} contains a cyclic value")
    ancestors.add(marker)
    try:
        values = enumerate(value) if type(value) is list else value.items()
        for key, item in values:
            if type(value) is dict and type(key) is not str:
                raise InvalidVerificationSchema(f"{path} object keys must be strings")
            _json_value(item, f"{path}.{key}", ancestors)
    finally:
        ancestors.remove(marker)


def _condition_key(value: str) -> str:
    return " ".join(str(value).split()).casefold()


def conditions_cover(required: Iterable[str], supplied: Iterable[str]) -> bool:
    return {_condition_key(value) for value in required}.issubset(
        {_condition_key(value) for value in supplied}
    )


def _conditions_are_consistent(
    satisfied: Iterable[str],
    missing: Iterable[str],
    contradictions: Iterable[str],
) -> bool:
    positive = {_condition_key(item) for item in satisfied}
    negative = {_condition_key(item) for item in missing}
    negative.update(_condition_key(item) for item in contradictions)
    return not positive.intersection(negative)


def forbidden_effect_condition(effect: str) -> str:
    return FORBIDDEN_EFFECT_CONDITION_PREFIX + _text(effect, "forbidden_effect")


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]):
        if type(raw) is not dict:
            raise InvalidVerificationSchema(f"{cls.__name__} must be a JSON object")
        _json_value(raw, cls.__name__)
        expected = set(cls.model_fields)
        missing = expected - set(raw)
        unknown = set(raw) - expected
        if missing or unknown:
            details = []
            if missing:
                details.append(f"missing={sorted(missing)!r}")
            if unknown:
                details.append(f"unknown={sorted(unknown)!r}")
            raise InvalidVerificationSchema(
                f"{cls.__name__} has non-canonical fields ({', '.join(details)})"
            )
        try:
            return cls.model_validate(raw)
        except (ValidationError, ValueError, TypeError) as exc:
            raise InvalidVerificationSchema(str(exc)) from exc

    @classmethod
    def from_json(cls, raw: str):
        if type(raw) is not str:
            raise InvalidVerificationSchema(f"{cls.__name__} JSON must be a string")

        def reject_constant(value: str) -> None:
            raise InvalidVerificationSchema(
                f"{cls.__name__} contains non-JSON number {value}"
            )

        try:
            value = json.loads(raw, parse_constant=reject_constant)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            if isinstance(exc, InvalidVerificationSchema):
                raise
            raise InvalidVerificationSchema(f"{cls.__name__} is not valid JSON") from exc
        return cls.from_dict(value)


class VerifierAssessment(_WireModel):
    """Identity-free output of one read-only verification role."""

    format: Literal[VERIFIER_ASSESSMENT_FORMAT] = VERIFIER_ASSESSMENT_FORMAT
    verified: WireBool
    satisfied_conditions: WireStrings = ()
    missing_conditions: WireStrings = ()
    contradictions: WireStrings = ()
    confidence: WireConfidence = None
    concise_reason: WireText = "verification was inconclusive"

    @model_validator(mode="after")
    def _consistent(self) -> "VerifierAssessment":
        if not _conditions_are_consistent(
            self.satisfied_conditions,
            self.missing_conditions,
            self.contradictions,
        ):
            raise ValueError(
                "satisfied conditions cannot also be missing or contradictory"
            )
        if self.verified and (self.missing_conditions or self.contradictions):
            raise ValueError(
                "verified assessment cannot contain missing conditions or contradictions"
            )
        return self


_POLICY_FIELDS = frozenset({
    "required_evidence_kinds",
    "preferred_evidence_kinds",
    "allow_independent_visual_verifier",
    "max_verification_attempts",
    "uncertain_policy",
})
_EVIDENCE_FIELDS = frozenset({
    "id", "mission_id", "run_id", "kind", "trust", "obligation_id",
    "payload_hash", "frame_id", "action_id", "artifact_id", "created_at",
    "content_sha256",
})


def _policy(value: Any) -> EvidencePolicy:
    if isinstance(value, EvidencePolicy):
        return value
    if type(value) is not dict or set(value) != _POLICY_FIELDS:
        raise ValueError("evidence_policy must use the canonical schema")
    return EvidencePolicy(
        required_evidence_kinds=_strings(
            value["required_evidence_kinds"], "required_evidence_kinds"
        ),
        preferred_evidence_kinds=_strings(
            value["preferred_evidence_kinds"], "preferred_evidence_kinds"
        ),
        allow_independent_visual_verifier=_literal_bool(
            value["allow_independent_visual_verifier"],
            "allow_independent_visual_verifier",
        ),
        max_verification_attempts=_integer(
            value["max_verification_attempts"], "max_verification_attempts", 1
        ),
        uncertain_policy=_text(value["uncertain_policy"], "uncertain_policy"),
    )


def _evidence(value: Any) -> EvidenceRef:
    if isinstance(value, EvidenceRef):
        return value
    if type(value) is not dict or set(value) != _EVIDENCE_FIELDS:
        raise ValueError("evidence_ref must use the canonical schema")
    for name in ("id", "mission_id", "run_id", "kind", "trust", "payload_hash"):
        _text(value[name], f"evidence_ref.{name}")
    for name in ("obligation_id", "action_id", "artifact_id", "content_sha256"):
        if value[name] is not None and type(value[name]) is not str:
            raise ValueError(f"evidence_ref.{name} must be a string or null")
    if value["frame_id"] is not None:
        _integer(value["frame_id"], "evidence_ref.frame_id")
    created_at = value["created_at"]
    if created_at is not None and (
        isinstance(created_at, bool)
        or not isinstance(created_at, (int, float))
        or not math.isfinite(float(created_at))
    ):
        raise ValueError("evidence_ref.created_at must be a finite number or null")
    return EvidenceRef.from_dict(value)


WireEvidencePolicy = Annotated[EvidencePolicy, BeforeValidator(_policy)]
WireEvidenceRefs = Annotated[
    tuple[EvidenceRef, ...],
    BeforeValidator(
        lambda value: tuple(_evidence(item) for item in _array(value, "evidence_refs"))
    ),
]


class ObligationVerificationRequest(_WireModel):
    """Runtime-bound, immutable input to a read-only obligation verifier."""

    format: Literal[OBLIGATION_VERIFICATION_REQUEST_FORMAT] = (
        OBLIGATION_VERIFICATION_REQUEST_FORMAT
    )
    mission_id: WireText
    run_id: WireText
    mission_revision: WireInteger
    obligation_id: WireText
    claim_fingerprint: WireText
    desired_state: WireText
    acceptance_criteria: WireStrings
    forbidden_effects: WireStrings = ()
    evidence_policy: WireEvidencePolicy = EvidencePolicy()
    evidence_refs: WireEvidenceRefs = ()

    @model_validator(mode="after")
    def _scope(self) -> "ObligationVerificationRequest":
        if (
            len(self.claim_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in self.claim_fingerprint)
        ):
            raise ValueError("claim_fingerprint must be a lowercase SHA-256 digest")
        if not self.acceptance_criteria:
            raise ValueError("acceptance_criteria cannot be empty")
        if len(self.evidence_refs) != len({ref.id for ref in self.evidence_refs}):
            raise ValueError("evidence_refs must not contain duplicate ids")
        for ref in self.evidence_refs:
            if ref.mission_id != self.mission_id or ref.run_id != self.run_id:
                raise ValueError(f"evidence {ref.id!r} is outside the request mission/run")
            if ref.obligation_id not in (None, "", self.obligation_id):
                raise ValueError(f"evidence {ref.id!r} belongs to another obligation")
            if ref.frame_id is not None and (
                type(ref.frame_id) is not int or ref.frame_id < 0
            ):
                raise ValueError(f"evidence {ref.id!r} has an invalid frame_id")
        required = self.required_conditions
        if len(required) != len({_condition_key(item) for item in required}):
            raise ValueError("required verification conditions collide")
        return self

    @property
    def required_conditions(self) -> tuple[str, ...]:
        return self.acceptance_criteria + tuple(
            forbidden_effect_condition(effect) for effect in self.forbidden_effects
        )

    @property
    def trusted_evidence_refs(self) -> tuple[EvidenceRef, ...]:
        rank = {trust: index for index, trust in enumerate(TRUST_ORDER)}
        return tuple(sorted(
            (ref for ref in self.evidence_refs if ref.trust in rank),
            key=lambda ref: (rank[ref.trust], ref.id),
        ))

    def for_trust(self, trust: str) -> "ObligationVerificationRequest":
        if trust not in TRUST_ORDER:
            raise InvalidVerificationSchema(f"unsupported verifier evidence trust: {trust!r}")
        return ObligationVerificationRequest(
            **{
                **self.model_dump(exclude={"evidence_refs"}),
                "evidence_refs": tuple(
                    ref for ref in self.evidence_refs if ref.trust == trust
                ),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.model_dump(
                mode="json", exclude={"evidence_policy", "evidence_refs"}
            ),
            "evidence_policy": self.evidence_policy.to_dict(),
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
        }


class ObligationVerificationResult(_WireModel):
    """Runtime-bound result suitable for conversion to a Mission event."""

    format: Literal[OBLIGATION_VERIFICATION_RESULT_FORMAT] = (
        OBLIGATION_VERIFICATION_RESULT_FORMAT
    )
    verification_id: WireText
    scope: Literal[VERIFICATION_SCOPE] = VERIFICATION_SCOPE
    subject_id: WireText
    mission_revision: WireInteger
    verified: WireBool
    satisfied_conditions: WireStrings
    missing_conditions: WireStrings
    contradictions: WireStrings
    evidence_refs: WireStrings
    verifier_kind: WireText
    confidence: WireConfidence = None

    @model_validator(mode="after")
    def _consistent(self) -> "ObligationVerificationResult":
        if not _conditions_are_consistent(
            self.satisfied_conditions,
            self.missing_conditions,
            self.contradictions,
        ):
            raise ValueError(
                "satisfied conditions cannot also be missing or contradictory"
            )
        if self.verified and (
            self.missing_conditions or self.contradictions or not self.evidence_refs
        ):
            raise ValueError(
                "verified result requires evidence and no missing or contradictory conditions"
            )
        return self

    @classmethod
    def bind(
        cls,
        request: ObligationVerificationRequest,
        assessment: VerifierAssessment,
        *,
        verifier_kind: str,
        evidence_refs: Iterable[str] | None = None,
    ) -> "ObligationVerificationResult":
        if not isinstance(request, ObligationVerificationRequest):
            raise InvalidVerificationSchema("request must be ObligationVerificationRequest")
        if not isinstance(assessment, VerifierAssessment):
            raise InvalidVerificationSchema("assessment must be VerifierAssessment")
        kind = _text(verifier_kind, "verifier_kind")
        available = {ref.id: ref for ref in request.trusted_evidence_refs}
        used = tuple(available) if evidence_refs is None else _strings(
            tuple(evidence_refs), "evidence_refs"
        )
        unknown = set(used) - set(available)
        if unknown:
            raise InvalidVerificationSchema(
                f"result references untrusted or unbound evidence: {sorted(unknown)!r}"
            )
        if assessment.verified and not conditions_cover(
            request.required_conditions, assessment.satisfied_conditions
        ):
            raise InvalidVerificationSchema(
                "verified assessment does not cover every required condition"
            )
        if assessment.verified and not used:
            raise InvalidVerificationSchema("verified result requires trusted evidence")
        canonical = {
            "mission_id": request.mission_id,
            "run_id": request.run_id,
            "mission_revision": request.mission_revision,
            "subject_id": request.obligation_id,
            "claim_fingerprint": request.claim_fingerprint,
            "assessment": assessment.to_dict(),
            "evidence_refs": list(used),
            "verifier_kind": kind,
        }
        digest = hashlib.sha256(
            json.dumps(
                canonical, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()[:24]
        return cls(
            verification_id=f"verification-{digest}",
            subject_id=request.obligation_id,
            mission_revision=request.mission_revision,
            verified=assessment.verified,
            satisfied_conditions=assessment.satisfied_conditions,
            missing_conditions=assessment.missing_conditions,
            contradictions=assessment.contradictions,
            evidence_refs=used,
            verifier_kind=kind,
            confidence=assessment.confidence,
        )

    def to_mission_event_payload(
        self,
        request: ObligationVerificationRequest,
    ) -> dict[str, Any]:
        """Project this result into the reducer's canonical payload vocabulary.

        Tagged forbidden-effect conditions are private to the verifier dispatcher.
        Runtime emits the obligation's original strings so reducer coverage remains
        mechanical and no internal encoding crosses the Mission event boundary.
        """
        if not isinstance(request, ObligationVerificationRequest):
            raise InvalidVerificationSchema("request must be ObligationVerificationRequest")
        if (
            self.subject_id != request.obligation_id
            or self.mission_revision != request.mission_revision
        ):
            raise InvalidVerificationSchema("result does not belong to the request")

        satisfied = {_condition_key(item) for item in self.satisfied_conditions}
        forbidden_tokens = {
            _condition_key(forbidden_effect_condition(effect)): effect
            for effect in request.forbidden_effects
        }

        def public_conditions(values: Iterable[str]) -> list[str]:
            return [forbidden_tokens.get(_condition_key(value), value) for value in values]

        return {
            "obligation_id": request.obligation_id,
            "claim_fingerprint": request.claim_fingerprint,
            "verification_id": self.verification_id,
            "verified": self.verified,
            "satisfied_criteria": [
                criterion
                for criterion in request.acceptance_criteria
                if _condition_key(criterion) in satisfied
            ],
            "checked_forbidden_effects": [
                effect
                for token, effect in forbidden_tokens.items()
                if token in satisfied
            ],
            "missing_conditions": public_conditions(self.missing_conditions),
            "contradictions": public_conditions(self.contradictions),
            "evidence_refs": list(self.evidence_refs),
            "verifier_kind": self.verifier_kind,
            "confidence": self.confidence,
        }


__all__ = [
    "VERIFIER_ASSESSMENT_FORMAT",
    "OBLIGATION_VERIFICATION_REQUEST_FORMAT",
    "OBLIGATION_VERIFICATION_RESULT_FORMAT",
    "VERIFICATION_SCOPE",
    "TRUST_ORDER",
    "FORBIDDEN_EFFECT_CONDITION_PREFIX",
    "VerificationContractError",
    "InvalidVerificationSchema",
    "VerifierAssessment",
    "ObligationVerificationRequest",
    "ObligationVerificationResult",
    "conditions_cover",
    "forbidden_effect_condition",
]
