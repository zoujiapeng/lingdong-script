"""Read-only deterministic P5b Mission-completion verification."""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal, Mapping

from pydantic import BeforeValidator, model_validator
from ..mission import EvidenceRef
from .contracts import (
    TRUST_ORDER,
    InvalidVerificationSchema,
    VerifierAssessment,
    WireBool,
    WireEvidenceRefs,
    WireInteger,
    WireStrings,
    WireText,
    _WireModel,
    _array,
    _evidence,
    conditions_cover,
)
ACCEPTED_OBLIGATION_VERIFICATION_FORMAT = "accepted-obligation-verification/0"
MISSION_COMPLETION_REQUEST_FORMAT = "mission-completion-verification-request/0"
MISSION_COMPLETION_RESULT_FORMAT = "mission-completion-verification-result/0"
MISSION_COMPLETION_AGGREGATE_FORMAT = "mission-completion-aggregate/0"
MISSION_COMPLETION_SCOPE = "mission"
# Mission completion accepts only evidence tiers that are independent of the
# GUI-writing Actor.  The visual tier is deliberately distinct from model_self.
MISSION_COMPLETION_TRUST = TRUST_ORDER + ("independent_visual_model",)
_DISALLOWED_TRUST = frozenset(("actor_claim", "model_self", "telemetry_only"))
_DISALLOWED_KIND = frozenset(("claim", "model_claim"))
_AGGREGATE_KIND = "mission_completion_aggregate"

def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

class AcceptedObligationVerification(_WireModel):
    """One required obligation as frozen by Runtime after acceptance."""
    format: Literal[ACCEPTED_OBLIGATION_VERIFICATION_FORMAT] = (
        ACCEPTED_OBLIGATION_VERIFICATION_FORMAT
    )
    obligation_id: WireText
    accepted_verification_id: WireText
    verified_evidence_refs: WireStrings
    evidence_refs: WireEvidenceRefs
    @model_validator(mode="after")
    def _exact_evidence(self) -> "AcceptedObligationVerification":
        if not self.verified_evidence_refs:
            raise ValueError("accepted obligation verification requires evidence")
        actual_ids = tuple(ref.id for ref in self.evidence_refs)
        if actual_ids != self.verified_evidence_refs:
            raise ValueError(
                "full evidence must exactly cover verified_evidence_refs in order"
            )
        for ref in self.evidence_refs:
            if ref.obligation_id not in (None, "", self.obligation_id):
                raise ValueError(f"evidence {ref.id!r} belongs to another obligation")
            if ref.trust in _DISALLOWED_TRUST or ref.kind in _DISALLOWED_KIND:
                raise ValueError(f"evidence {ref.id!r} is not independent")
            if ref.trust not in MISSION_COMPLETION_TRUST:
                raise ValueError(f"evidence {ref.id!r} has unsupported trust")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.model_dump(mode="json", exclude={"evidence_refs"}),
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
        }

def _accepted(value: Any) -> AcceptedObligationVerification:
    if isinstance(value, AcceptedObligationVerification):
        return value
    return AcceptedObligationVerification.from_dict(value)

WireAcceptedObligations = Annotated[
    tuple[AcceptedObligationVerification, ...],
    BeforeValidator(
        lambda value: tuple(
            _accepted(item) for item in _array(value, "required_obligations")
        )
    ),
]

class MissionCompletionVerificationRequest(_WireModel):
    """Immutable Runtime snapshot consumed by the read-only verifier."""
    format: Literal[MISSION_COMPLETION_REQUEST_FORMAT] = (
        MISSION_COMPLETION_REQUEST_FORMAT
    )
    mission_id: WireText
    run_id: WireText
    mission_revision: WireInteger
    required_obligations: WireAcceptedObligations
    @model_validator(mode="after")
    def _exact_scope(self) -> "MissionCompletionVerificationRequest":
        if not self.required_obligations:
            raise ValueError("at least one required obligation is required")
        obligation_ids = tuple(item.obligation_id for item in self.required_obligations)
        verification_ids = tuple(
            item.accepted_verification_id for item in self.required_obligations
        )
        if len(obligation_ids) != len(set(obligation_ids)):
            raise ValueError("required obligation ids must be unique")
        if len(verification_ids) != len(set(verification_ids)):
            raise ValueError("accepted verification ids must be unique")
        for item in self.required_obligations:
            for ref in item.evidence_refs:
                if ref.mission_id != self.mission_id or ref.run_id != self.run_id:
                    raise ValueError(
                        f"evidence {ref.id!r} is outside the request mission/run"
                    )
        return self

    @property
    def required_conditions(self) -> tuple[str, ...]:
        """Identity-free slots used by the generic assessment contract."""
        return tuple(
            f"required_obligation_slot_{index}_is_exactly_verified"
            for index in range(len(self.required_obligations))
        )

    @property
    def accepted_verification_ids(self) -> tuple[str, ...]:
        return tuple(item.accepted_verification_id for item in self.required_obligations)

    @property
    def obligation_evidence_ids(self) -> tuple[str, ...]:
        # A mission-level fact may legitimately prove more than one
        # obligation. Keep each obligation's exact evidence list in the
        # aggregate payload, but bind the Mission event to one stable copy of
        # each immutable evidence identity.
        return tuple(dict.fromkeys(
            ref.id
            for item in self.required_obligations
            for ref in item.evidence_refs
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.model_dump(mode="json", exclude={"required_obligations"}),
            "required_obligations": [
                item.to_dict() for item in self.required_obligations
            ],
        }

    def aggregate_payload(self) -> dict[str, Any]:
        return {
            "format": MISSION_COMPLETION_AGGREGATE_FORMAT,
            "mission_id": self.mission_id,
            "run_id": self.run_id,
            "mission_revision": self.mission_revision,
            "required_obligations": [
                item.to_dict() for item in self.required_obligations
            ],
        }

class DeterministicMissionCompletionVerifier:
    """Mechanically verify a canonical frozen completion snapshot."""
    verifier_kind = "deterministic_mission_completion"
    def verify(
        self, request: MissionCompletionVerificationRequest
    ) -> VerifierAssessment:
        if not isinstance(request, MissionCompletionVerificationRequest):
            raise InvalidVerificationSchema(
                "request must be MissionCompletionVerificationRequest"
            )
        # Round-trip validation prevents unvalidated ``model_construct`` values
        # from bypassing exact coverage, scope, uniqueness, or trust checks.
        checked = MissionCompletionVerificationRequest.from_dict(request.to_dict())
        return VerifierAssessment(
            verified=True,
            satisfied_conditions=checked.required_conditions,
            missing_conditions=(),
            contradictions=(),
            confidence=1.0,
            concise_reason=(
                "every required obligation has one accepted verification and "
                "exact independent run-scoped evidence"
            ),
        )

class MissionCompletionVerificationResult(_WireModel):
    """Stable Runtime-bound material; callers perform registry/event writes."""
    format: Literal[MISSION_COMPLETION_RESULT_FORMAT] = (
        MISSION_COMPLETION_RESULT_FORMAT
    )
    verification_id: WireText
    scope: Literal[MISSION_COMPLETION_SCOPE] = MISSION_COMPLETION_SCOPE
    mission_revision: WireInteger
    verified: WireBool
    accepted_obligation_verification_ids: WireStrings
    obligation_evidence_refs: WireStrings
    aggregate_payload_json: WireText
    aggregate_payload_hash: WireText
    aggregate_evidence: Annotated[EvidenceRef, BeforeValidator(_evidence)]
    @model_validator(mode="after")
    def _coherent(self) -> "MissionCompletionVerificationResult":
        if not self.verified:
            raise ValueError("mission completion result must be verified")
        try:
            payload = json.loads(self.aggregate_payload_json)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("aggregate_payload_json is not valid JSON") from exc
        if type(payload) is not dict or _canonical_json(payload) != self.aggregate_payload_json:
            raise ValueError("aggregate_payload_json must be a canonical JSON object")
        if _sha256(self.aggregate_payload_json) != self.aggregate_payload_hash:
            raise ValueError("aggregate payload hash does not match its content")
        expected_keys = {
            "format", "mission_id", "run_id", "mission_revision",
            "required_obligations",
        }
        if set(payload) != expected_keys or payload.get("format") != MISSION_COMPLETION_AGGREGATE_FORMAT:
            raise ValueError("aggregate payload does not use the canonical schema")
        request = MissionCompletionVerificationRequest.from_dict({
            "format": MISSION_COMPLETION_REQUEST_FORMAT,
            "mission_id": payload["mission_id"],
            "run_id": payload["run_id"],
            "mission_revision": payload["mission_revision"],
            "required_obligations": payload["required_obligations"],
        })
        aggregate = self.aggregate_evidence
        if (
            request.mission_revision != self.mission_revision
            or request.accepted_verification_ids
            != self.accepted_obligation_verification_ids
            or request.obligation_evidence_ids != self.obligation_evidence_refs
            or aggregate.mission_id != request.mission_id
            or aggregate.run_id != request.run_id
            or aggregate.kind != _AGGREGATE_KIND
            or aggregate.trust != "deterministic"
            or aggregate.obligation_id is not None
            or aggregate.payload_hash != self.aggregate_payload_hash
        ):
            raise ValueError("mission completion result is not bound to its aggregate")
        expected_evidence_id = "evidence-mission-" + _sha256(
            "evidence|" + self.aggregate_payload_hash
        )[:24]
        expected_verification_id = "mission-verification-" + _sha256(
            "verification|" + self.aggregate_payload_hash
        )[:24]
        if aggregate.id != expected_evidence_id or self.verification_id != expected_verification_id:
            raise ValueError("mission completion identities are not canonical")
        if aggregate.id in self.obligation_evidence_refs:
            raise ValueError("aggregate evidence must have an independent identity")
        if self.verification_id in self.accepted_obligation_verification_ids:
            raise ValueError("mission verification identity must be independent")
        return self

    @classmethod
    def bind(
        cls,
        request: MissionCompletionVerificationRequest,
        assessment: VerifierAssessment,
    ) -> "MissionCompletionVerificationResult":
        if not isinstance(request, MissionCompletionVerificationRequest):
            raise InvalidVerificationSchema(
                "request must be MissionCompletionVerificationRequest"
            )
        if not isinstance(assessment, VerifierAssessment):
            raise InvalidVerificationSchema("assessment must be VerifierAssessment")
        checked = MissionCompletionVerificationRequest.from_dict(request.to_dict())
        if (
            not assessment.verified
            or assessment.missing_conditions
            or assessment.contradictions
            or not conditions_cover(
                checked.required_conditions, assessment.satisfied_conditions
            )
        ):
            raise InvalidVerificationSchema(
                "mission completion requires a fully covered positive assessment"
            )
        payload_json = _canonical_json(checked.aggregate_payload())
        payload_hash = _sha256(payload_json)
        evidence_id = "evidence-mission-" + _sha256(
            "evidence|" + payload_hash
        )[:24]
        verification_id = "mission-verification-" + _sha256(
            "verification|" + payload_hash
        )[:24]
        aggregate = EvidenceRef(
            id=evidence_id,
            mission_id=checked.mission_id,
            run_id=checked.run_id,
            kind=_AGGREGATE_KIND,
            trust="deterministic",
            payload_hash=payload_hash,
        )
        return cls(
            verification_id=verification_id,
            mission_revision=checked.mission_revision,
            verified=True,
            accepted_obligation_verification_ids=checked.accepted_verification_ids,
            obligation_evidence_refs=checked.obligation_evidence_ids,
            aggregate_payload_json=payload_json,
            aggregate_payload_hash=payload_hash,
            aggregate_evidence=aggregate,
        )

    @property
    def event_evidence_refs(self) -> tuple[str, ...]:
        return self.obligation_evidence_refs + (self.aggregate_evidence.id,)

    def aggregate_payload(self) -> dict[str, Any]:
        return json.loads(self.aggregate_payload_json)

    def to_mission_event_payload(self) -> dict[str, Any]:
        """Return event material without constructing or appending an event."""
        return {
            "verified": True,
            "verification_id": self.verification_id,
            "accepted_obligation_verification_ids": list(
                self.accepted_obligation_verification_ids
            ),
            "evidence_refs": list(self.event_evidence_refs),
            "aggregate_evidence_id": self.aggregate_evidence.id,
            "aggregate_payload_hash": self.aggregate_payload_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.model_dump(mode="json", exclude={"aggregate_evidence"}),
            "aggregate_evidence": self.aggregate_evidence.to_dict(),
        }

__all__ = [
    "ACCEPTED_OBLIGATION_VERIFICATION_FORMAT",
    "MISSION_COMPLETION_REQUEST_FORMAT",
    "MISSION_COMPLETION_RESULT_FORMAT",
    "MISSION_COMPLETION_AGGREGATE_FORMAT",
    "MISSION_COMPLETION_SCOPE",
    "MISSION_COMPLETION_TRUST",
    "AcceptedObligationVerification",
    "MissionCompletionVerificationRequest",
    "DeterministicMissionCompletionVerifier",
    "MissionCompletionVerificationResult",
]
