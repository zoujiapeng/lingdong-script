"""P5b deterministic Mission completion verifier contracts and handoff."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json

import pytest
from pydantic import ValidationError

from winpilot.vnext.evidence import EvidenceRegistry
from winpilot.vnext.mission import (
    DuplicateEventError,
    EvidenceRef,
    MissionDefinition,
    MissionReducer,
    MissionStatus,
)
from winpilot.vnext.verification import (
    ACCEPTED_OBLIGATION_VERIFICATION_FORMAT,
    MISSION_COMPLETION_AGGREGATE_FORMAT,
    MISSION_COMPLETION_REQUEST_FORMAT,
    AcceptedObligationVerification,
    DeterministicMissionCompletionVerifier,
    InvalidVerificationSchema,
    MissionCompletionVerificationRequest,
    MissionCompletionVerificationResult,
    VerifierAssessment,
)


def _evidence(
    evidence_id: str,
    *,
    obligation_id: str | None,
    mission_id: str = "mission-1",
    run_id: str = "run-1",
    trust: str = "deterministic",
    kind: str = "state_proof",
) -> EvidenceRef:
    return EvidenceRef(
        id=evidence_id,
        mission_id=mission_id,
        run_id=run_id,
        kind=kind,
        trust=trust,
        obligation_id=obligation_id,
        payload_hash=f"payload-{evidence_id}",
    )


def _accepted(
    obligation_id: str,
    verification_id: str,
    refs: tuple[EvidenceRef, ...],
    *,
    evidence_ids: tuple[str, ...] | None = None,
) -> AcceptedObligationVerification:
    return AcceptedObligationVerification(
        obligation_id=obligation_id,
        accepted_verification_id=verification_id,
        verified_evidence_refs=(
            tuple(ref.id for ref in refs) if evidence_ids is None else evidence_ids
        ),
        evidence_refs=refs,
    )


def _request() -> MissionCompletionVerificationRequest:
    first = _accepted(
        "first",
        "verification-first",
        (_evidence("first-proof", obligation_id="first"),),
    )
    second = _accepted(
        "second",
        "verification-second",
        (_evidence("second-proof", obligation_id="second", trust="provider_receipt"),),
    )
    return MissionCompletionVerificationRequest(
        mission_id="mission-1",
        run_id="run-1",
        mission_revision=5,
        required_obligations=(first, second),
    )


def test_request_is_strict_versioned_replayable_and_exactly_scoped() -> None:
    request = _request()

    assert MissionCompletionVerificationRequest.from_json(request.to_json()) == request
    assert request.accepted_verification_ids == (
        "verification-first",
        "verification-second",
    )
    assert request.obligation_evidence_ids == ("first-proof", "second-proof")
    assert request.to_dict()["format"] == MISSION_COMPLETION_REQUEST_FORMAT
    assert (
        request.to_dict()["required_obligations"][0]["format"]
        == ACCEPTED_OBLIGATION_VERIFICATION_FORMAT
    )

    raw = request.to_dict()
    with pytest.raises(InvalidVerificationSchema, match="unsupported|literal"):
        MissionCompletionVerificationRequest.from_dict({**raw, "format": "request/9"})
    with pytest.raises(InvalidVerificationSchema, match="unknown"):
        MissionCompletionVerificationRequest.from_dict({**raw, "runtime": "smuggled"})
    with pytest.raises(InvalidVerificationSchema, match="non-JSON"):
        MissionCompletionVerificationRequest.from_dict({
            **raw,
            "required_obligations": tuple(raw["required_obligations"]),
        })


@pytest.mark.parametrize(
    ("ids", "refs", "message"),
    [
        (("proof",), (), "exactly cover"),
        ((), (_evidence("proof", obligation_id="goal"),), "requires evidence|exactly cover"),
        (
            ("proof",),
            (
                _evidence("proof", obligation_id="goal"),
                _evidence("extra", obligation_id="goal"),
            ),
            "exactly cover",
        ),
    ],
    ids=("missing-full-ref", "missing-accepted-id", "extra-full-ref"),
)
def test_accepted_verification_rejects_missing_or_extra_evidence(
    ids, refs, message
) -> None:
    with pytest.raises(ValidationError, match=message):
        _accepted("goal", "verification-goal", refs, evidence_ids=ids)


@pytest.mark.parametrize("trust", ("actor_claim", "model_self", "telemetry_only"))
def test_actor_or_telemetry_evidence_is_rejected_by_actual_trust(trust: str) -> None:
    ref = _evidence("proof", obligation_id="goal", trust=trust)
    with pytest.raises(ValidationError, match="not independent"):
        _accepted("goal", "verification-goal", (ref,))


@pytest.mark.parametrize("kind", ("claim", "model_claim"))
def test_claim_kind_cannot_hide_behind_deterministic_trust(kind: str) -> None:
    ref = _evidence("proof", obligation_id="goal", kind=kind)
    with pytest.raises(ValidationError, match="not independent"):
        _accepted("goal", "verification-goal", (ref,))


def test_independent_visual_evidence_is_allowed_but_unknown_trust_fails_closed() -> None:
    visual = _evidence(
        "visual-proof",
        obligation_id="goal",
        trust="independent_visual_model",
    )
    assert _accepted("goal", "verification-goal", (visual,)).evidence_refs == (visual,)

    unknown = replace(visual, id="unknown-proof", trust="payload_says_trusted")
    with pytest.raises(ValidationError, match="unsupported trust"):
        _accepted("goal", "verification-goal", (unknown,))


@pytest.mark.parametrize(
    "changed",
    (
        {"mission_id": "other-mission"},
        {"run_id": "other-run"},
        {"obligation_id": "other-obligation"},
    ),
)
def test_evidence_scope_and_obligation_association_are_checked(changed) -> None:
    ref = replace(_evidence("proof", obligation_id="goal"), **changed)
    if changed.get("obligation_id"):
        with pytest.raises(ValidationError, match="another obligation"):
            _accepted("goal", "verification-goal", (ref,))
        return
    item = _accepted("goal", "verification-goal", (ref,))
    with pytest.raises(ValidationError, match="outside"):
        MissionCompletionVerificationRequest(
            mission_id="mission-1",
            run_id="run-1",
            mission_revision=1,
            required_obligations=(item,),
        )


def test_request_rejects_empty_and_duplicate_obligation_identities() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        MissionCompletionVerificationRequest(
            mission_id="mission-1",
            run_id="run-1",
            mission_revision=0,
            required_obligations=(),
        )

    proof = _evidence("shared", obligation_id=None)
    first = _accepted("first", "verification-shared", (proof,))
    duplicate_obligation = _accepted("first", "verification-other", (
        replace(proof, id="other"),
    ))
    duplicate_verification = _accepted("second", "verification-shared", (
        replace(proof, id="second"),
    ))
    for items, message in (
        ((first, duplicate_obligation), "obligation ids"),
        ((first, duplicate_verification), "verification ids"),
    ):
        with pytest.raises(ValidationError, match=message):
            MissionCompletionVerificationRequest(
                mission_id="mission-1",
                run_id="run-1",
                mission_revision=1,
                required_obligations=items,
            )


def test_mission_level_evidence_can_cover_multiple_obligations_once() -> None:
    shared = _evidence("shared", obligation_id=None)
    request = MissionCompletionVerificationRequest(
        mission_id="mission-1",
        run_id="run-1",
        mission_revision=1,
        required_obligations=(
            _accepted("first", "verification-first", (shared,)),
            _accepted("second", "verification-second", (shared,)),
        ),
    )

    assert request.obligation_evidence_ids == (shared.id,)
    result = MissionCompletionVerificationResult.bind(
        request, DeterministicMissionCompletionVerifier().verify(request)
    )
    assert result.event_evidence_refs == (
        shared.id,
        result.aggregate_evidence.id,
    )


def test_verifier_assessment_is_positive_and_contains_no_runtime_identity() -> None:
    request = _request()
    assessment = DeterministicMissionCompletionVerifier().verify(request)

    assert assessment.verified is True
    assert assessment.satisfied_conditions == request.required_conditions
    wire = assessment.to_json()
    for identity in (
        request.mission_id,
        request.run_id,
        *request.accepted_verification_ids,
        *request.obligation_evidence_ids,
        *(item.obligation_id for item in request.required_obligations),
    ):
        assert identity not in wire


def test_verifier_revalidates_forged_constructed_request() -> None:
    good = _request()
    forged = MissionCompletionVerificationRequest.model_construct(
        **{
            **good.model_dump(),
            "required_obligations": (),
        }
    )
    with pytest.raises(InvalidVerificationSchema, match="at least one"):
        DeterministicMissionCompletionVerifier().verify(forged)


def test_runtime_binding_is_stable_self_contained_and_hashes_full_evidence() -> None:
    request = _request()
    assessment = DeterministicMissionCompletionVerifier().verify(request)
    first = MissionCompletionVerificationResult.bind(request, assessment)
    second = MissionCompletionVerificationResult.bind(request, assessment)

    assert first == second
    assert MissionCompletionVerificationResult.from_json(first.to_json()) == first
    assert first.scope == "mission"
    assert first.aggregate_evidence.obligation_id is None
    assert first.aggregate_evidence.trust == "deterministic"
    assert first.aggregate_evidence.payload_hash == first.aggregate_payload_hash
    assert hashlib.sha256(first.aggregate_payload_json.encode()).hexdigest() == (
        first.aggregate_payload_hash
    )
    payload = first.aggregate_payload()
    assert payload["format"] == MISSION_COMPLETION_AGGREGATE_FORMAT
    assert payload["required_obligations"] == [
        item.to_dict() for item in request.required_obligations
    ]
    event_payload = first.to_mission_event_payload()
    assert event_payload["accepted_obligation_verification_ids"] == [
        "verification-first",
        "verification-second",
    ]
    assert event_payload["evidence_refs"] == [
        "first-proof",
        "second-proof",
        first.aggregate_evidence.id,
    ]


def test_binding_rejects_incomplete_or_negative_assessment_and_tampering() -> None:
    request = _request()
    incomplete = VerifierAssessment(
        verified=True,
        satisfied_conditions=request.required_conditions[:1],
        concise_reason="one slot only",
    )
    negative = VerifierAssessment(
        verified=False,
        missing_conditions=request.required_conditions,
        concise_reason="not complete",
    )
    for assessment in (incomplete, negative):
        with pytest.raises(InvalidVerificationSchema, match="fully covered"):
            MissionCompletionVerificationResult.bind(request, assessment)

    result = MissionCompletionVerificationResult.bind(
        request, DeterministicMissionCompletionVerifier().verify(request)
    )
    raw = result.to_dict()
    with pytest.raises(InvalidVerificationSchema, match="hash"):
        MissionCompletionVerificationResult.from_dict({
            **raw,
            "aggregate_payload_hash": "0" * 64,
        })


def test_concurrent_binding_and_registry_append_are_idempotent() -> None:
    request = _request()
    assessment = DeterministicMissionCompletionVerifier().verify(request)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = tuple(pool.map(
            lambda _index: MissionCompletionVerificationResult.bind(
                request, assessment
            ),
            range(64),
        ))
    assert len({result.to_json() for result in results}) == 1

    registry = EvidenceRegistry(request.mission_id, request.run_id)
    registry.append_many(
        ref for item in request.required_obligations for ref in item.evidence_refs
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        tuple(pool.map(lambda result: registry.append(result.aggregate_evidence), results))
    assert len(registry) == 3


def test_result_is_ready_for_same_registry_and_revision_guarded_mission_event() -> None:
    definition = MissionDefinition.from_goal(
        "goal", mission_id="mission-1", run_id="run-1"
    )
    proof = _evidence("goal-proof", obligation_id="obligation-1")
    registry = EvidenceRegistry("mission-1", "run-1")
    registry.append(proof)
    reducer = MissionReducer(definition, evidence=registry)
    reducer.apply(reducer.event(
        "obligation_claimed", payload={"obligation_id": "obligation-1"}
    ))
    reducer.apply(reducer.event(
        "verification_recorded",
        payload={
            "obligation_id": "obligation-1",
            "verified": True,
            "verification_id": "verification-goal",
            "satisfied_criteria": ["goal"],
            "evidence_refs": [proof.id],
        },
    ))
    assert reducer.projection.status is MissionStatus.AWAITING_MISSION_VERIFICATION
    state = reducer.projection.obligation_state("obligation-1")
    request = MissionCompletionVerificationRequest(
        mission_id="mission-1",
        run_id="run-1",
        mission_revision=reducer.projection.revision,
        required_obligations=(
            _accepted(
                "obligation-1",
                state.verification_id or "",
                (proof,),
                evidence_ids=state.verified_evidence_refs,
            ),
        ),
    )
    result = MissionCompletionVerificationResult.bind(
        request, DeterministicMissionCompletionVerifier().verify(request)
    )

    registry.append(result.aggregate_evidence)
    first = reducer.event(
        "mission_verified", payload=result.to_mission_event_payload()
    )
    concurrent = reducer.event(
        "mission_verified", payload=result.to_mission_event_payload()
    )
    assert first.event_id == concurrent.event_id
    reducer.apply(first)
    assert reducer.projection.status is MissionStatus.VERIFIED
    with pytest.raises(DuplicateEventError):
        reducer.apply(concurrent)
