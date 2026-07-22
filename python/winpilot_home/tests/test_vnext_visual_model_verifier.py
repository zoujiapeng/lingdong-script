"""P5c visual verifier attestation and Runtime evidence binding tests."""
from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from threading import Event
from typing import Any, Mapping

import pytest

from winpilot.vnext.evidence import (
    CURRENT_FRAME_EVIDENCE_KIND,
    POST_ACTION_FRAME_EVIDENCE_KIND,
    EvidenceRegistry,
    current_frame_payload_hash,
    frame_identity_payload_hash,
)
from winpilot.vnext.mission import EvidencePolicy, EvidenceRef
from winpilot.vnext.runtime.contracts import RuntimeFrame
from winpilot.vnext.verification.contracts import (
    ObligationVerificationRequest,
    VerifierAssessment,
    forbidden_effect_condition,
)
from winpilot.vnext.verification.visual_model import (
    VISUAL_VERIFIER_EVIDENCE_KIND,
    ReadOnlyVisualVerifierAdapter,
    VisualVerifierAttestation,
    VisualVerifierCanceled,
    VisualVerifierContractError,
    VisualVerifierEvidenceRejected,
    VisualVerifierModelRequest,
    append_visual_verifier_evidence,
)


MISSION = "mission-visual"
RUN = "run-visual"
OBLIGATION = "obligation-visual"
CRITERION = "the requested visual result is visible"
FORBIDDEN = "unrelated content was modified"


def _frame(
    *,
    frame_id: int = 9,
    epoch: int = 3,
    salt: str = "current",
    evidence_id: str = "frame-proof",
) -> RuntimeFrame:
    image_b64 = base64.b64encode(f"frame:{salt}".encode()).decode("ascii")
    return RuntimeFrame(
        frame_id=frame_id,
        epoch=epoch,
        hwnd=404,
        region=(20, 30, 800, 600),
        scope="target_window",
        submitted_size=(800, 600),
        image_b64=image_b64,
        image_sha256=hashlib.sha256(image_b64.encode("ascii")).hexdigest(),
        evidence_refs=(evidence_id,),
    )


def _frame_proof(
    frame: RuntimeFrame,
    *,
    mission_id: str = MISSION,
    run_id: str = RUN,
    obligation_id: str = OBLIGATION,
    trust: str = "deterministic",
    frame_id: int | None = None,
    payload_hash: str | None = None,
) -> EvidenceRef:
    return EvidenceRef(
        id=frame.evidence_refs[0],
        mission_id=mission_id,
        run_id=run_id,
        obligation_id=obligation_id,
        kind=POST_ACTION_FRAME_EVIDENCE_KIND,
        trust=trust,
        payload_hash=(
            payload_hash
            if payload_hash is not None
            else frame_identity_payload_hash(frame.commit_identity)
        ),
        frame_id=frame.frame_id if frame_id is None else frame_id,
        action_id="decision-visual",
    )


def _request(
    proof: EvidenceRef,
    *,
    allow_visual: bool = True,
    revision: int = 5,
    claim: str = "a" * 64,
) -> ObligationVerificationRequest:
    return ObligationVerificationRequest(
        mission_id=proof.mission_id,
        run_id=proof.run_id,
        mission_revision=revision,
        obligation_id=OBLIGATION,
        claim_fingerprint=claim,
        desired_state="a correct visual artifact is present",
        acceptance_criteria=(CRITERION,),
        forbidden_effects=(FORBIDDEN,),
        evidence_policy=EvidencePolicy(
            allow_independent_visual_verifier=allow_visual,
        ),
        evidence_refs=(proof,),
    )


def _assessment() -> VerifierAssessment:
    return VerifierAssessment(
        verified=True,
        satisfied_conditions=(CRITERION, forbidden_effect_condition(FORBIDDEN)),
        missing_conditions=(),
        contradictions=(),
        confidence=0.93,
        concise_reason="the frame visibly satisfies every requested condition",
    )


class _Model:
    def __init__(self, result: VerifierAssessment | Mapping[str, Any] | None = None) -> None:
        self.result = result if result is not None else _assessment()
        self.calls: list[VisualVerifierModelRequest] = []

    def assess(
        self,
        request: VisualVerifierModelRequest,
        *,
        stop_event: Event | None = None,
    ) -> VerifierAssessment | Mapping[str, Any]:
        self.calls.append(request)
        return self.result


def _setup(
    *,
    proof: EvidenceRef | None = None,
    frame: RuntimeFrame | None = None,
    allow_visual: bool = True,
) -> tuple[RuntimeFrame, EvidenceRef, ObligationVerificationRequest, EvidenceRegistry]:
    current = frame or _frame()
    evidence = proof or _frame_proof(current)
    request = _request(evidence, allow_visual=allow_visual)
    registry = EvidenceRegistry(evidence.mission_id, evidence.run_id)
    registry.append(evidence)
    return current, evidence, request, registry


def test_model_sees_only_identity_free_input_and_adapter_cannot_write_registry() -> None:
    frame, _proof, request, registry = _setup()
    model = _Model()
    attestation = ReadOnlyVisualVerifierAdapter(model).attest(request, frame)

    assert len(registry) == 1
    assert len(model.calls) == 1
    model_payload = model.calls[0].to_dict()
    assert set(model_payload).isdisjoint({
        "mission_id", "run_id", "mission_revision", "obligation_id",
        "frame_id", "evidence_id", "evidence_refs", "claim_fingerprint",
    })
    assert attestation.assessment == _assessment()
    assert attestation.input_frame_sha256 == frame.image_sha256
    assert attestation.model_request_fingerprint == model.calls[0].fingerprint
    assert attestation == VisualVerifierAttestation.from_dict(attestation.to_dict())


def test_runtime_appends_deterministic_visual_evidence_idempotently() -> None:
    frame, proof, request, registry = _setup()
    attestation = ReadOnlyVisualVerifierAdapter(_Model()).attest(request, frame)

    first = append_visual_verifier_evidence(request, frame, attestation, registry)
    second = append_visual_verifier_evidence(request, frame, attestation, registry)

    assert first == second
    assert len(registry) == 2
    assert first.id.startswith("visual-evidence-")
    assert first.mission_id == request.mission_id
    assert first.run_id == request.run_id
    assert first.obligation_id == request.obligation_id
    assert first.kind == VISUAL_VERIFIER_EVIDENCE_KIND
    assert first.trust == "independent_visual_model"
    assert first.frame_id == frame.frame_id
    assert first.payload_hash != proof.payload_hash
    assert first.created_at is None


def test_current_observation_frame_can_be_verified_without_dummy_mutation() -> None:
    frame = _frame()
    proof = replace(
        _frame_proof(frame),
        kind=CURRENT_FRAME_EVIDENCE_KIND,
        payload_hash=current_frame_payload_hash(frame.commit_identity),
        action_id=None,
    )
    frame, _proof, request, registry = _setup(frame=frame, proof=proof)
    attestation = ReadOnlyVisualVerifierAdapter(_Model()).attest(request, frame)

    evidence = append_visual_verifier_evidence(
        request, frame, attestation, registry
    )

    assert evidence.trust == "independent_visual_model"
    assert evidence.frame_id == frame.frame_id


def test_policy_must_explicitly_allow_visual_verification() -> None:
    frame, _proof, request, registry = _setup(allow_visual=False)
    attestation = ReadOnlyVisualVerifierAdapter(_Model()).attest(request, frame)

    with pytest.raises(VisualVerifierEvidenceRejected, match="disabled"):
        append_visual_verifier_evidence(request, frame, attestation, registry)
    assert len(registry) == 1


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"trust": "application_oracle"}, "authoritative frame"),
        ({"frame_id": 8}, "authoritative frame"),
        ({"payload_hash": "b" * 64}, "authoritative frame"),
    ],
)
def test_frame_proof_trust_identity_and_hash_fail_closed(
    change: dict[str, Any],
    message: str,
) -> None:
    frame = _frame()
    proof = _frame_proof(frame, **change)
    frame, _proof, request, registry = _setup(frame=frame, proof=proof)
    attestation = ReadOnlyVisualVerifierAdapter(_Model()).attest(request, frame)

    with pytest.raises(VisualVerifierEvidenceRejected, match=message):
        append_visual_verifier_evidence(request, frame, attestation, registry)
    assert len(registry) == 1


def test_request_evidence_must_be_the_exact_canonical_registry_value() -> None:
    frame = _frame()
    requested_proof = _frame_proof(frame)
    request = _request(requested_proof)
    registry = EvidenceRegistry(MISSION, RUN)
    registry.append(replace(requested_proof, action_id="different-action"))
    attestation = ReadOnlyVisualVerifierAdapter(_Model()).attest(request, frame)

    with pytest.raises(VisualVerifierEvidenceRejected, match="canonical"):
        append_visual_verifier_evidence(request, frame, attestation, registry)


def test_registry_scope_is_runtime_bound() -> None:
    frame, proof, request, _registry = _setup()
    wrong = EvidenceRegistry("different-mission", RUN)
    wrong.append(replace(proof, mission_id="different-mission"))
    attestation = ReadOnlyVisualVerifierAdapter(_Model()).attest(request, frame)

    with pytest.raises(VisualVerifierEvidenceRejected, match="mission/run"):
        append_visual_verifier_evidence(request, frame, attestation, wrong)


def test_attestation_from_an_old_frame_is_stale_even_if_runtime_ids_are_hidden() -> None:
    old_frame, _proof, request, registry = _setup()
    attestation = ReadOnlyVisualVerifierAdapter(_Model()).attest(request, old_frame)
    current = _frame(
        frame_id=10,
        epoch=4,
        salt="new",
        evidence_id="frame-proof-new",
    )
    current_proof = _frame_proof(current)
    registry.append(current_proof)
    current_request = _request(
        current_proof,
        revision=request.mission_revision,
        claim=request.claim_fingerprint,
    )

    with pytest.raises(VisualVerifierEvidenceRejected, match="stale or mismatched"):
        append_visual_verifier_evidence(
            current_request, current, attestation, registry
        )


def test_attestation_from_an_old_claim_or_revision_is_stale() -> None:
    frame, proof, request, registry = _setup()
    attestation = ReadOnlyVisualVerifierAdapter(_Model()).attest(request, frame)
    current_request = _request(
        proof,
        revision=request.mission_revision + 1,
        claim="c" * 64,
    )

    with pytest.raises(
        VisualVerifierEvidenceRejected,
        match="verification_request_fingerprint",
    ):
        append_visual_verifier_evidence(
            current_request, frame, attestation, registry
        )


@pytest.mark.parametrize(
    "field",
    [
        "input_frame_sha256",
        "input_frame_fingerprint",
        "verification_request_fingerprint",
        "model_request_fingerprint",
    ],
)
def test_runtime_rejects_forged_attestation_binding(field: str) -> None:
    frame, _proof, request, registry = _setup()
    attestation = ReadOnlyVisualVerifierAdapter(_Model()).attest(request, frame)
    forged = replace(attestation, **{field: "0" * 64})

    with pytest.raises(VisualVerifierEvidenceRejected, match="stale or mismatched"):
        append_visual_verifier_evidence(request, frame, forged, registry)


def test_attestation_rejects_wrong_assessment_hash_and_unknown_fields() -> None:
    frame, _proof, request, _registry = _setup()
    attestation = ReadOnlyVisualVerifierAdapter(_Model()).attest(request, frame)

    with pytest.raises(VisualVerifierContractError, match="assessment_sha256"):
        replace(attestation, assessment_sha256="0" * 64)
    malformed = {**attestation.to_dict(), "run_id": RUN}
    with pytest.raises(VisualVerifierContractError, match="exactly"):
        VisualVerifierAttestation.from_dict(malformed)
    model_request = VisualVerifierModelRequest.from_runtime(request, frame).to_dict()
    model_request["frame_id"] = frame.frame_id
    with pytest.raises(VisualVerifierContractError, match="exactly"):
        VisualVerifierModelRequest.from_dict(model_request)


def test_model_cannot_smuggle_runtime_identity_in_assessment() -> None:
    frame, _proof, request, _registry = _setup()
    malformed = {**_assessment().to_dict(), "mission_id": MISSION}

    with pytest.raises(VisualVerifierContractError, match="malformed"):
        ReadOnlyVisualVerifierAdapter(_Model(malformed)).attest(request, frame)


def test_cancellation_before_or_after_model_call_produces_no_attestation() -> None:
    frame, _proof, request, registry = _setup()
    stopped = Event()
    stopped.set()
    model = _Model()
    with pytest.raises(VisualVerifierCanceled):
        ReadOnlyVisualVerifierAdapter(model).attest(
            request, frame, stop_event=stopped
        )
    assert model.calls == []

    late_stop = Event()

    class CancelingModel(_Model):
        def assess(self, request, *, stop_event=None):
            assert stop_event is late_stop
            stop_event.set()
            return super().assess(request, stop_event=stop_event)

    with pytest.raises(VisualVerifierCanceled):
        ReadOnlyVisualVerifierAdapter(CancelingModel()).attest(
            request, frame, stop_event=late_stop
        )
    assert len(registry) == 1


def test_cancellation_before_runtime_append_never_writes_evidence() -> None:
    frame, _proof, request, registry = _setup()
    attestation = ReadOnlyVisualVerifierAdapter(_Model()).attest(request, frame)
    stopped = Event()
    stopped.set()

    with pytest.raises(VisualVerifierCanceled):
        append_visual_verifier_evidence(
            request, frame, attestation, registry, stop_event=stopped
        )
    assert len(registry) == 1


def test_conflicting_preexisting_visual_evidence_does_not_overwrite_registry() -> None:
    frame, proof, request, registry = _setup()
    attestation = ReadOnlyVisualVerifierAdapter(_Model()).attest(request, frame)
    expected = append_visual_verifier_evidence(request, frame, attestation, registry)

    conflicting = EvidenceRegistry(MISSION, RUN)
    conflicting.append(proof)
    conflicting.append(replace(expected, trust="deterministic"))
    with pytest.raises(VisualVerifierEvidenceRejected, match="registry rejected"):
        append_visual_verifier_evidence(
            request, frame, attestation, conflicting
        )
    assert len(conflicting) == 2
