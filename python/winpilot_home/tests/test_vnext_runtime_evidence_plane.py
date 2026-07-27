from __future__ import annotations

import base64
import hashlib
import threading
from typing import Any

import pytest

from winpilot.action.raw_device import RawActionReceipt, RawBackendResult
from winpilot.action.replay_policy import MutationOutcome
from winpilot.vnext.evidence_plane import (
    ActionExecutionBinding,
    ActorDecisionRecord,
    AdmissionRecord,
    FrameArtifactReference,
    FrameArtifactRole,
    RuntimeEvidenceContractError,
    RuntimeEvidencePlane,
    RuntimeEvidencePlaneError,
    RuntimeEvidenceReport,
    RuntimeEvidenceReportStatus,
    canonical_sha256,
)
from winpilot.vnext.mission import EvidenceRef
from winpilot.vnext.runtime import (
    ControllerOutcome,
    ControllerTransition,
)


MISSION_ID = "mission-evidence"
RUN_ID = "run-evidence"
OBLIGATION_ID = "full-goal"
DECISION_ID = "decision-evidence"
ENVELOPE_SHA256 = hashlib.sha256(b"canonical-envelope").hexdigest()


def _runtime_image_sha256(payload: bytes) -> str:
    return hashlib.sha256(base64.b64encode(payload)).hexdigest()


def _frame_identity(frame_id: int, payload: bytes) -> dict[str, Any]:
    return {
        "frame_id": frame_id,
        "epoch": 4,
        "hwnd": 901,
        "region": [10, 20, 100, 80],
        "scope": "target_window",
        "submitted_size": [100, 80],
        "image_sha256": _runtime_image_sha256(payload),
        "media_type": "image/jpeg",
    }


def _target_identity() -> dict[str, Any]:
    return {
        "binding_id": "target-binding",
        "hwnd": 901,
        "process_id": 77,
        "executable_identity": "opaque-executable",
        "epoch": 4,
    }


def _receipt(
    *,
    outcome: str = "applied",
    input_completed: bool | None = True,
) -> dict[str, Any]:
    mutation_outcome = MutationOutcome(outcome)
    expected = {
        MutationOutcome.APPLIED: True,
        MutationOutcome.NOT_APPLIED: False,
        MutationOutcome.NOT_STARTED: False,
        MutationOutcome.OUTCOME_UNKNOWN: None,
    }[mutation_outcome]
    assert input_completed is expected
    return RawActionReceipt(
        action="click",
        outcome=mutation_outcome,
        backend_result=(
            RawBackendResult(
                input_completed=input_completed,
                facts={"via": "physical_sendinput"},
            )
            if outcome in {"applied", "not_applied"}
            else None
        ),
        error_kind=(
            "synthetic_unknown"
            if outcome in {"not_started", "outcome_unknown"}
            else None
        ),
    ).to_dict()


def _transition(
    *,
    outcome: str = "completed",
    input_completed: bool | None = True,
    post_frame_id: int | None = 2,
    after_payload: bytes = b"\xff\xd8\xff-after-\xff\xd9",
) -> dict[str, Any]:
    post_evidence = (
        ()
        if post_frame_id is None
        else (
            EvidenceRef(
                id=f"evidence-post-frame-{post_frame_id}",
                mission_id=MISSION_ID,
                run_id=RUN_ID,
                kind="post_action_frame",
                trust="deterministic",
                obligation_id=OBLIGATION_ID,
                payload_hash=canonical_sha256(
                    _frame_identity(post_frame_id, after_payload)
                ),
                frame_id=post_frame_id,
                action_id=DECISION_ID,
            ),
        )
    )
    return ControllerTransition(
        outcome=ControllerOutcome(outcome),
        action_id=DECISION_ID,
        action="click",
        input_completed=input_completed,
        post_frame_evidence=post_evidence,
        post_frame_id=post_frame_id,
    ).to_dict()


def _plane(*, clock_ns=lambda: 123_456_789) -> RuntimeEvidencePlane:
    return RuntimeEvidencePlane(
        MISSION_ID,
        RUN_ID,
        clock_ns=clock_ns,
    )


def _record_decision(
    plane: RuntimeEvidencePlane,
    *,
    arguments: dict[str, Any] | None = None,
) -> ActorDecisionRecord:
    return plane.record_actor_decision(
        decision_id=DECISION_ID,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        obligation_id=OBLIGATION_ID,
        proposal_kind="desktop_action",
        action="click",
        canonical_arguments=arguments
        or {"image_x": 10.0, "image_y": 20.0},
        tool_schema={
            "type": "object",
            "properties": {
                "image_x": {"type": "number"},
                "image_y": {"type": "number"},
            },
            "required": ["image_x", "image_y"],
            "additionalProperties": False,
        },
        broker_projection={
            "format": "broker-projection/0",
            "projection_id": "projection-evidence",
            "tool_names": ["click"],
        },
        actor_envelope_sha256=ENVELOPE_SHA256,
    )


def _record_admission(
    plane: RuntimeEvidencePlane,
    *,
    before_payload: bytes,
) -> AdmissionRecord:
    return plane.record_admission(
        decision_id=DECISION_ID,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        frame_identity=_frame_identity(1, before_payload),
        target_identity=_target_identity(),
        mutation_permit_id="permit-evidence",
        controller_route="vnext_atomic_desktop_adapter",
        actor_envelope_sha256=ENVELOPE_SHA256,
    )


def _record_receipt(plane: RuntimeEvidencePlane) -> None:
    plane.reserve_physical_attempt(
        decision_id=DECISION_ID,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        actor_envelope_sha256=ENVELOPE_SHA256,
    )
    plane.record_raw_receipt(
        decision_id=DECISION_ID,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        actor_envelope_sha256=ENVELOPE_SHA256,
        receipt=_receipt(),
    )


def _record_transition(
    plane: RuntimeEvidencePlane,
    *,
    after_payload: bytes,
) -> ActionExecutionBinding | None:
    return plane.record_controller_transition(
        decision_id=DECISION_ID,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        actor_envelope_sha256=ENVELOPE_SHA256,
        transition=_transition(after_payload=after_payload),
        post_frame_identity=_frame_identity(2, after_payload),
    )


def _frame_reference(
    *,
    frame_id: int,
    payload: bytes,
    role: FrameArtifactRole,
) -> FrameArtifactReference:
    return FrameArtifactReference(
        frame_id=frame_id,
        artifact_uri=f"artifact://run-evidence/frame-{frame_id}.jpg",
        sha256=hashlib.sha256(payload).hexdigest(),
        capture_timestamp_ns=(
            123_456_788
            if role is FrameArtifactRole.BEFORE
            else 123_456_790
        ),
        role=role,
    )


def _complete_plane(
    *,
    include_admission: bool = True,
    include_receipt: bool = True,
    include_transition: bool = True,
    include_before_artifact: bool = True,
    include_after_artifact: bool = True,
) -> tuple[RuntimeEvidencePlane, dict[str, bytes]]:
    before_payload = b"\xff\xd8\xff-before-\xff\xd9"
    after_payload = b"\xff\xd8\xff-after-\xff\xd9"
    plane = _plane()
    _record_decision(plane)
    if include_admission:
        _record_admission(plane, before_payload=before_payload)
    if include_receipt and include_admission:
        _record_receipt(plane)
    if include_transition and include_receipt and include_admission:
        _record_transition(plane, after_payload=after_payload)
    references = (
        (
            _frame_reference(
                frame_id=1,
                payload=before_payload,
                role=FrameArtifactRole.BEFORE,
            )
            if include_before_artifact
            else None
        ),
        (
            _frame_reference(
                frame_id=2,
                payload=after_payload,
                role=FrameArtifactRole.AFTER,
            )
            if include_after_artifact
            else None
        ),
    )
    payloads: dict[str, bytes] = {}
    for reference, payload in zip(
        references,
        (before_payload, after_payload),
        strict=True,
    ):
        if reference is not None:
            plane.record_frame_artifact(reference)
            payloads[reference.artifact_uri] = payload
    return plane, payloads


def test_actor_decision_record_contains_hashes_without_reasoning() -> None:
    plane = _plane()
    record = _record_decision(
        plane,
        arguments={"image_y": 20.0, "image_x": 10.0},
    )

    wire = record.to_dict()

    assert set(wire) == {
        "format",
        "decision_id",
        "mission_id",
        "obligation_id",
        "proposal_kind",
        "action",
        "canonical_arguments_sha256",
        "tool_schema_sha256",
        "broker_projection_sha256",
    }
    assert not {
        "args",
        "local_intent",
        "reasoning",
        "prompt",
        "model_response",
    } & set(wire)
    assert record.canonical_arguments_sha256 == canonical_sha256(
        {"image_x": 10.0, "image_y": 20.0}
    )
    assert ActorDecisionRecord.from_dict(wire) == record
    with pytest.raises(
        RuntimeEvidenceContractError,
        match="fields mismatch",
    ):
        ActorDecisionRecord.from_dict({**wire, "reasoning": "secret"})


def test_decision_id_cannot_be_reused_even_for_identical_content() -> None:
    plane = _plane()
    first = _record_decision(plane)

    with pytest.raises(
        RuntimeEvidencePlaneError,
        match="cannot be reused",
    ):
        _record_decision(plane)

    assert plane.decision_records == (first,)


def test_admission_without_decision_is_rejected_atomically() -> None:
    plane = _plane()

    with pytest.raises(
        RuntimeEvidencePlaneError,
        match="without a decision",
    ):
        _record_admission(
            plane,
            before_payload=b"\xff\xd8-before-\xff\xd9",
        )

    assert plane.admission_records == ()


def test_admission_requires_the_recorded_actor_envelope() -> None:
    plane = _plane()
    _record_decision(plane)

    with pytest.raises(
        RuntimeEvidencePlaneError,
        match="admission envelope identity changed",
    ):
        plane.record_admission(
            decision_id=DECISION_ID,
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            frame_identity=_frame_identity(
                1, b"\xff\xd8-before-\xff\xd9"
            ),
            target_identity=_target_identity(),
            mutation_permit_id="permit-evidence",
            controller_route="vnext_atomic_desktop_adapter",
            actor_envelope_sha256="f" * 64,
        )

    assert plane.admission_records == ()


def test_admission_uses_injected_timestamp_and_rejects_bool() -> None:
    plane = _plane(clock_ns=lambda: 999)
    _record_decision(plane)

    admission = _record_admission(
        plane,
        before_payload=b"\xff\xd8-before-\xff\xd9",
    )

    assert admission.accepted_timestamp_ns == 999
    invalid = _plane(clock_ns=lambda: True)
    _record_decision(invalid)
    with pytest.raises(
        RuntimeEvidenceContractError,
        match="accepted_timestamp_ns",
    ):
        _record_admission(
            invalid,
            before_payload=b"\xff\xd8-before-\xff\xd9",
        )


def test_one_decision_has_at_most_one_physical_attempt_concurrently() -> None:
    plane = _plane()
    _record_decision(plane)
    _record_admission(
        plane,
        before_payload=b"\xff\xd8-before-\xff\xd9",
    )
    barrier = threading.Barrier(3)
    accepted: list[str] = []
    rejected: list[str] = []

    def worker(label: str) -> None:
        barrier.wait()
        try:
            plane.reserve_physical_attempt(
                decision_id=DECISION_ID,
                mission_id=MISSION_ID,
                run_id=RUN_ID,
                actor_envelope_sha256=ENVELOPE_SHA256,
            )
            accepted.append(label)
        except RuntimeEvidencePlaneError as exc:
            rejected.append(str(exc))

    threads = (
        threading.Thread(target=worker, args=("first",)),
        threading.Thread(target=worker, args=("second",)),
    )
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert len(accepted) == 1
    assert rejected == [
        "decision already reserved its physical attempt"
    ]


def test_receipt_without_action_binding_cannot_enter_report() -> None:
    plane = _plane()
    _record_decision(plane)
    _record_admission(
        plane,
        before_payload=b"\xff\xd8-before-\xff\xd9",
    )
    _record_receipt(plane)

    report = plane.build_report()

    assert report.status is RuntimeEvidenceReportStatus.INCOMPLETE
    assert report.action_bindings == ()
    assert f"{DECISION_ID}_action_binding" in report.missing_evidence
    assert (
        f"{DECISION_ID}_controller_transition"
        in report.missing_evidence
    )
    assert "raw_receipt" not in report.to_dict()
    assert RuntimeEvidenceReport.from_dict(report.to_dict()) == report


def test_receipt_and_transition_snapshots_reject_unknown_fields() -> None:
    plane = _plane()
    _record_decision(plane)
    _record_admission(
        plane,
        before_payload=b"\xff\xd8-before-\xff\xd9",
    )
    invalid_receipt = {**_receipt(), "reasoning": "forbidden"}
    with pytest.raises(
        RuntimeEvidencePlaneError,
        match="raw_action_receipt fields mismatch",
    ):
        plane.record_raw_receipt(
            decision_id=DECISION_ID,
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            actor_envelope_sha256=ENVELOPE_SHA256,
            receipt=invalid_receipt,
        )

    _record_receipt(plane)
    invalid_transition = {
        **_transition(),
        "model_summary": "forbidden",
    }
    with pytest.raises(
        RuntimeEvidencePlaneError,
        match="controller_transition fields mismatch",
    ):
        plane.record_controller_transition(
            decision_id=DECISION_ID,
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            actor_envelope_sha256=ENVELOPE_SHA256,
            transition=invalid_transition,
            post_frame_identity=_frame_identity(
                2,
                b"\xff\xd8-after-\xff\xd9",
            ),
        )
    assert plane.action_bindings == ()


def test_receipt_and_transition_reject_impossible_typed_facts() -> None:
    plane = _plane()
    _record_decision(plane)
    _record_admission(
        plane,
        before_payload=b"\xff\xd8-before-\xff\xd9",
    )
    impossible_receipt = _receipt()
    impossible_receipt["backend_result"]["input_completed"] = False
    with pytest.raises(
        RuntimeEvidencePlaneError,
        match="backend_result contradicts",
    ):
        plane.record_raw_receipt(
            decision_id=DECISION_ID,
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            actor_envelope_sha256=ENVELOPE_SHA256,
            receipt=impossible_receipt,
        )

    plane = _plane()
    _record_decision(plane)
    _record_admission(
        plane,
        before_payload=b"\xff\xd8-before-\xff\xd9",
    )
    _record_receipt(plane)
    impossible_transition = _transition()
    impossible_transition["post_frame_evidence"] = []
    with pytest.raises(
        RuntimeEvidencePlaneError,
        match="completed transition requires",
    ):
        plane.record_controller_transition(
            decision_id=DECISION_ID,
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            actor_envelope_sha256=ENVELOPE_SHA256,
            transition=impossible_transition,
            post_frame_identity=_frame_identity(
                2, b"\xff\xd8-after-\xff\xd9"
            ),
        )


def test_transition_evidence_scope_and_payload_are_bound() -> None:
    plane = _plane()
    _record_decision(plane)
    _record_admission(
        plane,
        before_payload=b"\xff\xd8-before-\xff\xd9",
    )
    _record_receipt(plane)
    wrong_scope = _transition()
    wrong_scope["post_frame_evidence"][0]["run_id"] = "other-run"
    with pytest.raises(
        RuntimeEvidencePlaneError,
        match="evidence scope changed",
    ):
        plane.record_controller_transition(
            decision_id=DECISION_ID,
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            actor_envelope_sha256=ENVELOPE_SHA256,
            transition=wrong_scope,
            post_frame_identity=_frame_identity(
                2, b"\xff\xd8\xff-after-\xff\xd9"
            ),
        )

    plane = _plane()
    _record_decision(plane)
    _record_admission(
        plane,
        before_payload=b"\xff\xd8-before-\xff\xd9",
    )
    _record_receipt(plane)
    wrong_payload = _transition()
    wrong_payload["post_frame_evidence"][0]["payload_hash"] = "f" * 64
    with pytest.raises(
        RuntimeEvidenceContractError,
        match="payload hash changed",
    ):
        plane.record_controller_transition(
            decision_id=DECISION_ID,
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            actor_envelope_sha256=ENVELOPE_SHA256,
            transition=wrong_payload,
            post_frame_identity=_frame_identity(
                2, b"\xff\xd8\xff-after-\xff\xd9"
            ),
        )


def test_complete_report_binds_all_records_and_is_deterministic() -> None:
    plane, payloads = _complete_plane()

    first = plane.build_report(
        artifact_reader=payloads.__getitem__,
    )
    second = plane.build_report(
        artifact_reader=payloads.__getitem__,
    )

    assert first.status is RuntimeEvidenceReportStatus.COMPLETE
    assert first.missing_evidence == ()
    assert first.verification_failures == ()
    assert len(first.actor_decisions) == 1
    assert len(first.admissions) == 1
    assert len(first.action_bindings) == 1
    assert len(first.frame_artifacts) == 2
    binding = first.action_bindings[0]
    assert binding.action_id == binding.decision_id == DECISION_ID
    assert binding.before_frame_id == 1
    assert binding.after_frame_id == 2
    assert first.to_dict() == second.to_dict()
    with pytest.raises(
        RuntimeEvidenceContractError,
        match="cannot self-authenticate",
    ):
        RuntimeEvidenceReport.from_dict(first.to_dict())
    assert "passed" not in first.to_dict()
    assert "done" not in first.to_dict()


def test_complete_report_cannot_be_self_declared_without_records() -> None:
    with pytest.raises(
        RuntimeEvidenceContractError,
        match="cannot self-authenticate",
    ):
        RuntimeEvidenceReport.from_dict(
            {
                "format": "runtime-evidence-report/0",
                "mission_id": MISSION_ID,
                "run_id": RUN_ID,
                "status": "complete",
                "actor_decisions": [],
                "admissions": [],
                "action_bindings": [],
                "frame_artifacts": [],
                "missing_evidence": [],
                "verification_failures": [],
            }
        )


def test_binding_retains_recomputable_reasoning_free_snapshots() -> None:
    plane, payloads = _complete_plane()

    binding = plane.build_report(
        artifact_reader=payloads.__getitem__,
    ).action_bindings[0]
    wire = binding.to_dict()

    assert wire["raw_action_receipt_sha256"] == canonical_sha256(
        wire["raw_action_receipt"]
    )
    assert wire["controller_transition_sha256"] == canonical_sha256(
        wire["controller_transition"]
    )
    assert wire["before_frame_identity"] == _frame_identity(
        1, b"\xff\xd8\xff-before-\xff\xd9"
    )
    assert wire["after_frame_identity"] == _frame_identity(
        2, b"\xff\xd8\xff-after-\xff\xd9"
    )
    assert wire["physical_attempt_count"] == 1
    assert wire["attempt_reservation_sha256"]
    assert wire["before_artifact_reference_sha256"]
    assert wire["after_artifact_reference_sha256"]
    assert not {"reasoning", "prompt", "model_response"} & set(wire)


def test_serialized_complete_report_cannot_rewrite_run_scope() -> None:
    plane, payloads = _complete_plane()
    wire = plane.build_report(
        artifact_reader=payloads.__getitem__,
    ).to_dict()
    wire["run_id"] = "other-run"

    with pytest.raises(
        RuntimeEvidenceContractError,
        match="cannot self-authenticate",
    ):
        RuntimeEvidenceReport.from_dict(wire)


def test_physical_receipt_requires_admission_and_attempt_reservation() -> None:
    plane = _plane()
    _record_decision(plane)

    with pytest.raises(
        RuntimeEvidencePlaneError,
        match="attempt reservation",
    ):
        plane.record_raw_receipt(
            decision_id=DECISION_ID,
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            actor_envelope_sha256=ENVELOPE_SHA256,
            receipt=_receipt(),
        )

    report = plane.build_report()
    assert report.status is RuntimeEvidenceReportStatus.INCOMPLETE
    assert f"{DECISION_ID}_raw_receipt" in report.missing_evidence


def test_admission_and_after_frame_cannot_switch_target_identity() -> None:
    plane = _plane()
    _record_decision(plane)
    target = _target_identity()
    target["hwnd"] += 1
    with pytest.raises(
        RuntimeEvidenceContractError,
        match="identities disagree",
    ):
        plane.record_admission(
            decision_id=DECISION_ID,
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            frame_identity=_frame_identity(
                1, b"\xff\xd8-before-\xff\xd9"
            ),
            target_identity=target,
            mutation_permit_id="permit-evidence",
            controller_route="vnext_atomic_desktop_adapter",
            actor_envelope_sha256=ENVELOPE_SHA256,
        )

    plane = _plane()
    _record_decision(plane)
    _record_admission(
        plane,
        before_payload=b"\xff\xd8-before-\xff\xd9",
    )
    _record_receipt(plane)
    after = _frame_identity(2, b"\xff\xd8-after-\xff\xd9")
    after["hwnd"] += 1
    with pytest.raises(
        RuntimeEvidenceContractError,
        match="one target commit",
    ):
        plane.record_controller_transition(
            decision_id=DECISION_ID,
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            actor_envelope_sha256=ENVELOPE_SHA256,
            transition=_transition(),
            post_frame_identity=after,
        )


def test_unverified_artifacts_and_mixed_missing_failure_are_incomplete() -> None:
    plane, payloads = _complete_plane()

    unverified = plane.build_report()
    assert unverified.status is RuntimeEvidenceReportStatus.INCOMPLETE
    assert "before_frame_1_artifact_unverified" in (
        unverified.missing_evidence
    )

    partial, partial_payloads = _complete_plane(
        include_after_artifact=False,
    )
    before_ref = partial.frame_artifact_references[0]
    partial_payloads[before_ref.artifact_uri] = b"tampered"
    mixed = partial.build_report(
        artifact_reader=partial_payloads.__getitem__,
    )
    assert mixed.status is RuntimeEvidenceReportStatus.INCOMPLETE
    assert mixed.verification_failures == (
        "before_frame_1_artifact_hash_mismatch",
    )
    assert (
        f"{DECISION_ID}_after_frame_artifact"
        in mixed.missing_evidence
    )


def test_duplicate_receipt_permanently_fails_verification() -> None:
    plane, payloads = _complete_plane()

    with pytest.raises(
        RuntimeEvidencePlaneError,
        match="already has a raw receipt",
    ):
        plane.record_raw_receipt(
            decision_id=DECISION_ID,
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            actor_envelope_sha256=ENVELOPE_SHA256,
            receipt=_receipt(),
        )

    report = plane.build_report(
        artifact_reader=payloads.__getitem__,
    )
    assert (
        report.status
        is RuntimeEvidenceReportStatus.VERIFICATION_FAILED
    )
    assert report.verification_failures == (
        f"{DECISION_ID}_duplicate_raw_receipt",
    )
    forged = report.to_dict()
    forged["status"] = "complete"
    forged["verification_failures"] = []
    with pytest.raises(
        RuntimeEvidenceContractError,
        match="cannot self-authenticate",
    ):
        RuntimeEvidenceReport.from_dict(forged)


@pytest.mark.parametrize(
    ("missing_part", "expected_fragment"),
    (
        ("decision", "actor_decision"),
        ("admission", f"{DECISION_ID}_admission"),
        ("receipt", f"{DECISION_ID}_raw_receipt"),
        ("transition", f"{DECISION_ID}_controller_transition"),
        ("before_artifact", f"{DECISION_ID}_before_frame_artifact"),
        ("after_artifact", f"{DECISION_ID}_after_frame_artifact"),
    ),
)
def test_missing_any_critical_evidence_makes_report_incomplete(
    missing_part: str,
    expected_fragment: str,
) -> None:
    if missing_part == "decision":
        report = _plane().build_report()
    else:
        plane, payloads = _complete_plane(
            include_admission=missing_part != "admission",
            include_receipt=missing_part != "receipt",
            include_transition=missing_part != "transition",
            include_before_artifact=missing_part != "before_artifact",
            include_after_artifact=missing_part != "after_artifact",
        )
        report = plane.build_report(
            artifact_reader=payloads.__getitem__,
        )

    assert report.status is RuntimeEvidenceReportStatus.INCOMPLETE
    assert expected_fragment in report.missing_evidence


def test_frame_artifact_hash_change_fails_verification() -> None:
    plane, payloads = _complete_plane()
    reference = plane.frame_artifact_references[1]
    payloads[reference.artifact_uri] = b"\xff\xd8-tampered-\xff\xd9"

    report = plane.build_report(
        artifact_reader=payloads.__getitem__,
    )

    assert (
        report.status
        is RuntimeEvidenceReportStatus.VERIFICATION_FAILED
    )
    assert report.missing_evidence == ()
    assert report.verification_failures == (
        "after_frame_2_artifact_hash_mismatch",
    )


def test_frame_artifact_must_match_runtime_frame_identity() -> None:
    plane, payloads = _complete_plane()
    reference = plane.frame_artifact_references[1]
    replacement = b"\xff\xd8-replacement-\xff\xd9"
    payloads[reference.artifact_uri] = replacement
    changed_reference = FrameArtifactReference(
        frame_id=reference.frame_id,
        artifact_uri="artifact://run-evidence/replacement.jpg",
        sha256=hashlib.sha256(replacement).hexdigest(),
        capture_timestamp_ns=reference.capture_timestamp_ns,
        role=reference.role,
    )
    isolated, isolated_payloads = _complete_plane(
        include_after_artifact=False,
    )
    isolated.record_frame_artifact(changed_reference)
    isolated_payloads[changed_reference.artifact_uri] = replacement

    report = isolated.build_report(
        artifact_reader=isolated_payloads.__getitem__,
    )

    assert (
        report.status
        is RuntimeEvidenceReportStatus.VERIFICATION_FAILED
    )
    assert report.verification_failures == (
        "after_frame_2_runtime_frame_hash_mismatch",
    )


def test_frame_artifact_reference_is_reference_only_and_strict() -> None:
    reference = _frame_reference(
        frame_id=1,
        payload=b"\xff\xd8-before-\xff\xd9",
        role=FrameArtifactRole.BEFORE,
    )
    wire = reference.to_dict()

    assert set(wire) == {
        "format",
        "frame_id",
        "artifact_uri",
        "sha256",
        "capture_timestamp_ns",
        "role",
    }
    assert FrameArtifactReference.from_dict(wire) == reference
    with pytest.raises(RuntimeEvidenceContractError, match="artifact_uri"):
        FrameArtifactReference(
            frame_id=1,
            artifact_uri="data:image/jpeg;base64,/9j/",
            sha256="0" * 64,
            capture_timestamp_ns=1,
            role="before",
        )
    with pytest.raises(
        RuntimeEvidenceContractError,
        match="fields mismatch",
    ):
        FrameArtifactReference.from_dict(
            {**wire, "image_b64": "/9j/"}
        )


def test_action_binding_requires_unique_shared_action_identity() -> None:
    plane, _payloads = _complete_plane()
    binding = plane.action_bindings[0]
    wire = binding.to_dict()

    assert ActionExecutionBinding.from_dict(wire) == binding
    with pytest.raises(
        RuntimeEvidenceContractError,
        match="action_id must equal decision_id",
    ):
        ActionExecutionBinding.from_dict(
            {**wire, "action_id": "different-action"}
        )


def test_controller_transition_snapshot_is_reasoning_free_and_canonical() -> None:
    transition = ControllerTransition(
        outcome=ControllerOutcome.REJECTED,
        action_id=DECISION_ID,
        action="click",
        input_completed=False,
    )

    assert transition.to_dict() == {
        "format": "controller-transition/0",
        "outcome": "rejected",
        "action_id": DECISION_ID,
        "action": "click",
        "input_completed": False,
        "visible_effect": "",
        "post_frame_evidence": [],
        "post_frame_id": None,
        "action_effect": None,
        "action_effect_evidence": None,
    }


def test_double_click_may_bind_only_to_raw_click_receipt() -> None:
    plane = _plane()
    plane.record_actor_decision(
        decision_id=DECISION_ID,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        obligation_id=OBLIGATION_ID,
        proposal_kind="desktop_action",
        action="double_click",
        canonical_arguments={"image_x": 10.0, "image_y": 20.0},
        tool_schema={"type": "object"},
        broker_projection={"format": "broker-projection/0"},
        actor_envelope_sha256=ENVELOPE_SHA256,
    )
    _record_admission(
        plane,
        before_payload=b"\xff\xd8-before-\xff\xd9",
    )
    plane.reserve_physical_attempt(
        decision_id=DECISION_ID,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        actor_envelope_sha256=ENVELOPE_SHA256,
    )

    plane.record_raw_receipt(
        decision_id=DECISION_ID,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        actor_envelope_sha256=ENVELOPE_SHA256,
        receipt=_receipt(),
    )
    transition = _transition(
        after_payload=b"\xff\xd8-after-\xff\xd9"
    )
    transition["action"] = "double_click"
    binding = plane.record_controller_transition(
        decision_id=DECISION_ID,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        actor_envelope_sha256=ENVELOPE_SHA256,
        transition=transition,
        post_frame_identity=_frame_identity(
            2,
            b"\xff\xd8-after-\xff\xd9",
        ),
    )

    assert binding is not None
    assert binding.action == "double_click"
