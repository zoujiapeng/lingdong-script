from __future__ import annotations

import base64
import copy
import hashlib
import itertools
import json
from pathlib import Path
import threading
from typing import Any

import pytest

from winpilot.action.raw_device import RawActionReceipt, RawBackendResult
from winpilot.action.replay_policy import MutationOutcome
from winpilot.vnext.evidence_plane import (
    ActionExecutionBinding,
    ActorDecisionRecord,
    AdmissionRecord,
    FrameArtifactWriter,
    FrameArtifactReference,
    FrameArtifactRole,
    GENESIS_EVENT_SHA256,
    JOURNAL_FILENAME,
    PhysicalAttemptRecord,
    RuntimeEvidenceContractError,
    RuntimeEvidenceJournal,
    RuntimeEvidenceJournalEvent,
    RuntimeEvidencePlane,
    RuntimeEvidencePlaneError,
    RuntimeEvidenceReport,
    RuntimeEvidenceReportStatus,
    canonical_sha256,
    runtime_evidence_report_sha256,
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
BEFORE_PAYLOAD = b"\xff\xd8\xff-before-\xff\xd9"
AFTER_PAYLOAD = b"\xff\xd8\xff-after-\xff\xd9"
_RUN_COUNTER = itertools.count()


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
    action: str = "click",
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
        action=action,
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
    action: str = "click",
    outcome: str = "completed",
    input_completed: bool | None = True,
    post_frame_id: int | None = 2,
    after_payload: bytes = AFTER_PAYLOAD,
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
        action=action,
        input_completed=input_completed,
        post_frame_evidence=post_evidence,
        post_frame_id=post_frame_id,
    ).to_dict()


def _default_arguments(action: str) -> dict[str, Any]:
    return copy.deepcopy(
        {
            "click": {"image_x": 10.0, "image_y": 20.0},
            "double_click": {"image_x": 10.0, "image_y": 20.0},
            "drag": {
                "image_x1": 10.0,
                "image_y1": 20.0,
                "image_x2": 70.0,
                "image_y2": 60.0,
            },
            "scroll": {
                "image_x": 10.0,
                "image_y": 20.0,
                "amount": -240,
            },
            "type_text": {"text": "exact text"},
            "hotkey": {"keys": ["ctrl", "l"]},
        }[action]
    )


def _projection_id(envelope: dict[str, Any]) -> str:
    material = {
        "format": "broker-projection/1",
        "mission_id": envelope["mission_id"],
        "run_id": envelope["run_id"],
        "catalog_fingerprint": envelope["catalog_fingerprint"],
        "profile_id": envelope["profile_id"],
        "state_fingerprint": envelope["state_fingerprint"],
        "mission_revision": envelope["mission_revision"],
        "obligation_id": envelope["obligation_id"],
        "turn_index": envelope["turn_index"],
        "execution_route": "visual_actor",
        "executor_id": None,
        "target_binding_id": envelope["target_binding_id"],
        "frame_id": envelope["frame_id"],
        "frame_scope": envelope["frame_scope"],
        "pointer_lease_id": envelope["pointer_lease_id"],
        "tool_names": [envelope["proposal"]["action"]],
        "active_grant_ids": [],
        "missing_capabilities": [],
        "projection_reasons": [],
    }
    canonical = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "projection-" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()[:24]


def _actor_envelope(
    *,
    action: str = "click",
    arguments: dict[str, Any] | None = None,
    before_payload: bytes = BEFORE_PAYLOAD,
    pointer_disposition: str = "released",
    pointer_lease_id: str | None = None,
) -> dict[str, Any]:
    envelope = {
        "format": "actor-envelope/1",
        "decision_id": DECISION_ID,
        "mission_id": MISSION_ID,
        "run_id": RUN_ID,
        "mission_revision": 0,
        "mission_projection_sha256": "1" * 64,
        "accepted_mission_event_ids": ["event-target-bound"],
        "obligation_id": OBLIGATION_ID,
        "turn_index": 0,
        "catalog_fingerprint": "2" * 64,
        "profile_id": "fixed-standard",
        "state_fingerprint": "3" * 64,
        "frame_id": 1,
        "frame_epoch": 4,
        "frame_hwnd": 901,
        "frame_scope": "target_window",
        "frame_region": [10, 20, 100, 80],
        "submitted_image_size": [100, 80],
        "frame_image_sha256": _runtime_image_sha256(before_payload),
        "target_binding_id": "target-binding",
        "pointer_disposition": pointer_disposition,
        "pointer_lease_id": pointer_lease_id,
        "broker_projection_id": "projection-pending",
        "capability_grant_ids": [],
        "allowed_evidence_scope": ["evidence-current-frame"],
        "proposal": {
            "format": "actor-proposal/0",
            "kind": "desktop_action",
            "action": action,
            "args": (
                copy.deepcopy(arguments)
                if arguments is not None
                else _default_arguments(action)
            ),
            "local_intent": "perform one bounded desktop action",
        },
    }
    envelope["broker_projection_id"] = _projection_id(envelope)
    return envelope


def _tool_schema(action: str = "click") -> dict[str, Any]:
    properties = {
        "click": {
            "image_x": {"type": "number"},
            "image_y": {"type": "number"},
        },
        "double_click": {
            "image_x": {"type": "number"},
            "image_y": {"type": "number"},
        },
        "drag": {
            "image_x1": {"type": "number"},
            "image_y1": {"type": "number"},
            "image_x2": {"type": "number"},
            "image_y2": {"type": "number"},
        },
        "scroll": {
            "image_x": {"type": "number"},
            "image_y": {"type": "number"},
            "amount": {"type": "integer"},
        },
        "type_text": {"text": {"type": "string"}},
        "hotkey": {
            "keys": {
                "type": "array",
                "items": {"type": "string"},
            }
        },
    }[action]
    return {
        "type": "object",
        "properties": copy.deepcopy(properties),
        "required": list(properties),
        "additionalProperties": False,
    }


def _broker_projection(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    return {
        "format": "broker-projection/1",
        "projection_id": envelope["broker_projection_id"],
        "mission_id": envelope["mission_id"],
        "run_id": envelope["run_id"],
        "catalog_fingerprint": envelope["catalog_fingerprint"],
        "profile_id": envelope["profile_id"],
        "state_fingerprint": envelope["state_fingerprint"],
        "mission_revision": envelope["mission_revision"],
        "obligation_id": envelope["obligation_id"],
        "turn_index": envelope["turn_index"],
        "execution_route": "visual_actor",
        "executor_id": None,
        "target_binding_id": envelope["target_binding_id"],
        "frame_id": envelope["frame_id"],
        "frame_scope": envelope["frame_scope"],
        "pointer_lease_id": envelope["pointer_lease_id"],
        "tool_names": [envelope["proposal"]["action"]],
        "active_grants": [
            {"grant_id": grant_id}
            for grant_id in envelope["capability_grant_ids"]
        ],
        "missing_capabilities": [],
        "projection_reasons": [],
    }


def _nested_keys(value: Any) -> set[str]:
    if type(value) is dict:
        return set(value).union(
            *(_nested_keys(item) for item in value.values()),
            set(),
        )
    if type(value) is list:
        return set().union(
            *(_nested_keys(item) for item in value),
            set(),
        )
    return set()


ENVELOPE_SHA256 = canonical_sha256(_actor_envelope())


def _plane(
    *,
    clock_ns=lambda: 123_456_789,
    run_directory: Path | None = None,
) -> RuntimeEvidencePlane:
    journal = (
        None
        if run_directory is None
        else RuntimeEvidenceJournal.create(
            run_directory,
            MISSION_ID,
            RUN_ID,
        )
    )
    return RuntimeEvidencePlane(
        MISSION_ID,
        RUN_ID,
        clock_ns=clock_ns,
        journal=journal,
    )


def _record_decision(
    plane: RuntimeEvidencePlane,
    *,
    arguments: dict[str, Any] | None = None,
    action: str = "click",
    pointer_disposition: str = "released",
    pointer_lease_id: str | None = None,
) -> ActorDecisionRecord:
    envelope = _actor_envelope(
        action=action,
        arguments=arguments,
        pointer_disposition=pointer_disposition,
        pointer_lease_id=pointer_lease_id,
    )
    return plane.record_actor_decision(
        actor_envelope=envelope,
        tool_schema=_tool_schema(action),
        broker_projection=_broker_projection(envelope),
    )


def _decision_envelope_sha256(plane: RuntimeEvidencePlane) -> str:
    if plane.decision_records:
        return plane.decision_records[0].actor_envelope_sha256
    return ENVELOPE_SHA256


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
        actor_envelope_sha256=_decision_envelope_sha256(plane),
    )


def _attempt_facts(
    action: str,
    submitted_arguments: dict[str, Any],
    *,
    started_held: bool = False,
) -> tuple[dict[str, Any], str]:
    if action in {"click", "double_click"}:
        return (
            {
                "kind": "pointer_click",
                "x": 20,
                "y": 40,
                "button": "left",
                "click_count": 2 if action == "double_click" else 1,
            },
            "raw_device.admitted_physical_click/0",
        )
    if action == "scroll":
        return (
            {
                "kind": "wheel",
                "x": 20,
                "y": 40,
                "signed_wheel_amount": submitted_arguments["amount"],
                "event_wheel_delta": submitted_arguments["amount"] * 120,
            },
            "raw_device.admitted_physical_scroll/0",
        )
    if action == "drag":
        physical = {
            "kind": "pointer_drag",
            "x1": 20,
            "y1": 40,
            "x2": 80,
            "y2": 80,
            "button": "left",
        }
        if "image_points" in submitted_arguments:
            physical["points"] = [
                [int(point[0]) + 10, int(point[1]) + 20]
                for point in submitted_arguments["image_points"]
            ]
        if "keep_holding" in submitted_arguments:
            physical["started_held"] = started_held
            physical["keep_holding"] = submitted_arguments["keep_holding"]
        return physical, "raw_device.admitted_physical_drag/0"
    if action == "type_text":
        return (
            {
                "kind": "type_text",
                "text": submitted_arguments["text"],
            },
            "raw_device.admitted_physical_type_text/0",
        )
    keys = submitted_arguments["keys"]
    return (
        {
            "kind": "hotkey",
            "modifiers": keys[:-1],
            "key": keys[-1],
        },
        "raw_device.admitted_physical_hotkey/0",
    )


def _reserve_attempt(
    plane: RuntimeEvidencePlane,
    *,
    started_held: bool = False,
) -> PhysicalAttemptRecord:
    decision = plane.decision_records[0]
    submitted = dict(decision.canonical_arguments)
    physical, route = _attempt_facts(
        decision.action,
        submitted,
        started_held=started_held,
    )
    return plane.reserve_physical_attempt(
        decision_id=DECISION_ID,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        actor_envelope_sha256=decision.actor_envelope_sha256,
        submitted_arguments=submitted,
        physical_arguments=physical,
        raw_device_route=route,
        mechanism="physical_sendinput",
    )


def _record_raw_receipt(plane: RuntimeEvidencePlane) -> None:
    decision = plane.decision_records[0]
    plane.record_raw_receipt(
        decision_id=DECISION_ID,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        actor_envelope_sha256=decision.actor_envelope_sha256,
        receipt=_receipt(
            action=(
                "click"
                if decision.action == "double_click"
                else decision.action
            )
        ),
    )


def _record_receipt(plane: RuntimeEvidencePlane) -> None:
    _reserve_attempt(plane)
    _record_raw_receipt(plane)


def _record_transition(
    plane: RuntimeEvidencePlane,
    *,
    after_payload: bytes,
) -> ActionExecutionBinding | None:
    decision = plane.decision_records[0]
    return plane.record_controller_transition(
        decision_id=DECISION_ID,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        actor_envelope_sha256=decision.actor_envelope_sha256,
        transition=_transition(
            action=decision.action,
            after_payload=after_payload,
        ),
        post_frame_identity=_frame_identity(2, after_payload),
    )


def _frame_reference(
    *,
    frame_id: int,
    payload: bytes,
    role: FrameArtifactRole,
) -> FrameArtifactReference:
    identity = _frame_identity(frame_id, payload)
    return FrameArtifactReference(
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        decision_id=DECISION_ID,
        action_id=DECISION_ID,
        frame_id=frame_id,
        frame_identity=identity,
        artifact_uri=(
            f"artifacts/{role.value}-frame-{frame_id:020d}.jpg"
        ),
        raw_jpeg_sha256=hashlib.sha256(payload).hexdigest(),
        runtime_frame_image_sha256=identity["image_sha256"],
        capture_timestamp_ns=(
            123_456_788
            if role is FrameArtifactRole.BEFORE
            else 123_456_790
        ),
        role=role,
    )


def _complete_plane(
    tmp_path: Path,
    *,
    include_admission: bool = True,
    include_receipt: bool = True,
    include_transition: bool = True,
    include_before_artifact: bool = True,
    include_after_artifact: bool = True,
) -> tuple[RuntimeEvidencePlane, dict[str, bytes]]:
    before_payload = BEFORE_PAYLOAD
    after_payload = AFTER_PAYLOAD
    run_directory = (
        tmp_path / f"evidence-run-{next(_RUN_COUNTER)}"
        if include_before_artifact
        else None
    )
    plane = _plane(run_directory=run_directory)
    _record_decision(plane)
    before_reference = _frame_reference(
        frame_id=1,
        payload=before_payload,
        role=FrameArtifactRole.BEFORE,
    )
    after_reference = _frame_reference(
        frame_id=2,
        payload=after_payload,
        role=FrameArtifactRole.AFTER,
    )
    payloads: dict[str, bytes] = {}
    if include_before_artifact:
        plane.record_frame_artifact(before_reference)
        payloads[before_reference.artifact_uri] = before_payload
    if include_admission:
        _record_admission(plane, before_payload=before_payload)
    if include_admission and (
        include_receipt
        or include_after_artifact
        or include_transition
    ):
        _reserve_attempt(plane)
    if include_after_artifact and include_admission:
        plane.record_frame_artifact(after_reference)
        payloads[after_reference.artifact_uri] = after_payload
    if include_receipt and include_admission:
        _record_raw_receipt(plane)
    if include_transition and include_receipt and include_admission:
        _record_transition(plane, after_payload=after_payload)
    return plane, payloads


def _persist_frame(
    plane: RuntimeEvidencePlane,
    writer: FrameArtifactWriter,
    *,
    frame_id: int,
    payload: bytes,
    role: FrameArtifactRole,
) -> FrameArtifactReference:
    identity = _frame_identity(frame_id, payload)
    timestamp_ns = (
        123_456_788
        if role is FrameArtifactRole.BEFORE
        else 123_456_790
    )
    result = writer.write_frame(
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        decision_id=DECISION_ID,
        action_id=DECISION_ID,
        role=role.value,
        frame_identity=identity,
        image_b64=base64.b64encode(payload).decode("ascii"),
        capture_timestamp_ns=timestamp_ns,
    )
    reference = FrameArtifactReference(
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        decision_id=DECISION_ID,
        action_id=DECISION_ID,
        frame_id=frame_id,
        frame_identity=identity,
        artifact_uri=result.artifact_uri,
        raw_jpeg_sha256=result.sha256,
        runtime_frame_image_sha256=identity["image_sha256"],
        capture_timestamp_ns=result.capture_timestamp_ns,
        role=result.role,
    )
    return plane.record_frame_artifact(reference)


def _complete_persisted_run(
    tmp_path: Path,
) -> tuple[Path, str]:
    run_directory = (
        tmp_path / f"persisted-run-{next(_RUN_COUNTER)}"
    )
    plane = _plane(run_directory=run_directory)
    writer = FrameArtifactWriter.create(run_directory)
    _record_decision(plane)
    _persist_frame(
        plane,
        writer,
        frame_id=1,
        payload=BEFORE_PAYLOAD,
        role=FrameArtifactRole.BEFORE,
    )
    _record_admission(plane, before_payload=BEFORE_PAYLOAD)
    _reserve_attempt(plane)
    _persist_frame(
        plane,
        writer,
        frame_id=2,
        payload=AFTER_PAYLOAD,
        role=FrameArtifactRole.AFTER,
    )
    _record_raw_receipt(plane)
    _record_transition(plane, after_payload=AFTER_PAYLOAD)
    journal = plane.journal
    assert journal is not None
    head = journal.head
    journal.close()
    return run_directory, head


def _rewrite_actor_arguments(
    run_directory: Path,
    *,
    mutation: str,
) -> None:
    journal = RuntimeEvidenceJournal.open_read_only(
        run_directory,
        MISSION_ID,
        RUN_ID,
    )
    event_wires = [event.to_dict() for event in journal.events]
    actor_payload = event_wires[0]["payload"]
    if mutation == "deleted":
        del actor_payload["canonical_arguments"]
    else:
        assert mutation == "tampered"
        actor_payload["canonical_arguments"]["image_x"] = 999.0

    previous = GENESIS_EVENT_SHA256
    rewritten: list[RuntimeEvidenceJournalEvent] = []
    for wire in event_wires:
        event = RuntimeEvidenceJournalEvent.build(
            sequence=wire["sequence"],
            previous_event_sha256=previous,
            event_kind=wire["event_kind"],
            mission_id=wire["mission_id"],
            run_id=wire["run_id"],
            decision_id=wire["decision_id"],
            action_id=wire["action_id"],
            payload=wire["payload"],
        )
        rewritten.append(event)
        previous = event.event_sha256
    encoded = b"\n".join(
        json.dumps(
            event.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        for event in rewritten
    ) + b"\n"
    (run_directory / JOURNAL_FILENAME).write_bytes(encoded)


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
        "run_id",
        "mission_revision",
        "obligation_id",
        "turn_index",
        "proposal_kind",
        "action",
        "canonical_arguments",
        "canonical_arguments_sha256",
        "actor_envelope",
        "actor_envelope_sha256",
        "tool_schema",
        "tool_schema_sha256",
        "broker_projection",
        "broker_projection_sha256",
    }
    assert not {
        "reasoning",
        "prompt",
        "model_response",
    } & _nested_keys(wire)
    assert wire["canonical_arguments"] == {
        "image_x": 10.0,
        "image_y": 20.0,
    }
    assert wire["actor_envelope"] == _actor_envelope(
        arguments={"image_y": 20.0, "image_x": 10.0},
    )
    assert wire["broker_projection"] == _broker_projection(
        wire["actor_envelope"]
    )
    assert wire["tool_schema"] == _tool_schema()
    assert record.canonical_arguments_sha256 == canonical_sha256(
        {"image_x": 10.0, "image_y": 20.0}
    )
    assert record.actor_envelope_sha256 == canonical_sha256(
        wire["actor_envelope"]
    )
    assert record.broker_projection_sha256 == canonical_sha256(
        wire["broker_projection"]
    )
    assert record.tool_schema_sha256 == canonical_sha256(
        wire["tool_schema"]
    )
    assert ActorDecisionRecord.from_dict(wire) == record
    for forbidden in ("prompt", "reasoning", "model_response"):
        with pytest.raises(
            RuntimeEvidenceContractError,
            match="fields mismatch",
        ):
            ActorDecisionRecord.from_dict(
                {**wire, forbidden: "secret"}
            )


@pytest.mark.parametrize(
    "forbidden",
    ("prompt", "reasoning", "model_response"),
)
def test_actor_envelope_rejects_nested_model_private_fields(
    forbidden: str,
) -> None:
    record = _record_decision(_plane())
    wire = record.to_dict()
    wire["actor_envelope"][forbidden] = "secret"

    with pytest.raises(
        RuntimeEvidenceContractError,
        match="actor_envelope fields mismatch",
    ):
        ActorDecisionRecord.from_dict(wire)


def test_actor_decision_record_rejects_stale_actor_envelope_sha256() -> None:
    record = _record_decision(_plane())
    wire = record.to_dict()
    assert wire["actor_envelope_sha256"] != "f" * 64
    wire["actor_envelope_sha256"] = "f" * 64

    with pytest.raises(
        RuntimeEvidenceContractError,
        match="actor_envelope_sha256",
    ):
        ActorDecisionRecord.from_dict(wire)


def test_actor_decision_record_rejects_stale_tool_schema_sha256() -> None:
    record = _record_decision(_plane())
    wire = record.to_dict()
    assert wire["tool_schema_sha256"] != "f" * 64
    wire["tool_schema_sha256"] = "f" * 64

    with pytest.raises(
        RuntimeEvidenceContractError,
        match="tool_schema_sha256",
    ):
        ActorDecisionRecord.from_dict(wire)


def test_actor_decision_record_rejects_broker_projection_with_stale_canonical_id(
) -> None:
    record = _record_decision(_plane())
    wire = record.to_dict()
    wire["broker_projection"]["projection_reasons"] = ["changed"]

    with pytest.raises(
        RuntimeEvidenceContractError,
        match=(
            "broker_projection id does not identify its canonical content"
        ),
    ):
        ActorDecisionRecord.from_dict(wire)


def test_actor_decision_record_rejects_stale_broker_projection_sha256() -> None:
    record = _record_decision(_plane())
    wire = record.to_dict()
    assert wire["broker_projection_sha256"] != "f" * 64
    wire["broker_projection_sha256"] = "f" * 64

    with pytest.raises(
        RuntimeEvidenceContractError,
        match="broker_projection_sha256",
    ):
        ActorDecisionRecord.from_dict(wire)


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
        before_payload=BEFORE_PAYLOAD,
    )
    barrier = threading.Barrier(3)
    accepted: list[PhysicalAttemptRecord] = []
    rejected: list[str] = []

    def worker() -> None:
        barrier.wait()
        try:
            accepted.append(_reserve_attempt(plane))
        except RuntimeEvidencePlaneError as exc:
            rejected.append(str(exc))

    threads = (
        threading.Thread(target=worker),
        threading.Thread(target=worker),
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
    assert plane.physical_attempt_records == tuple(accepted)
    attempt = accepted[0]
    assert attempt.attempt_index == 1
    assert attempt.submitted_arguments == {
        "image_x": 10.0,
        "image_y": 20.0,
    }
    assert attempt.physical_arguments == {
        "kind": "pointer_click",
        "x": 20,
        "y": 40,
        "button": "left",
        "click_count": 1,
    }
    assert attempt.reservation_sha256 == canonical_sha256(
        attempt.reservation_material()
    )


def test_drag_evidence_keeps_legacy_endpoint_only_records_compatible() -> None:
    plane = _plane()
    decision = _record_decision(plane, action="drag")
    _record_admission(plane, before_payload=BEFORE_PAYLOAD)

    attempt = _reserve_attempt(plane)

    assert decision.to_dict()["canonical_arguments"] == _default_arguments(
        "drag"
    )
    assert attempt.to_dict()["submitted_arguments"] == _default_arguments(
        "drag"
    )
    assert attempt.to_dict()["physical_arguments"] == {
        "kind": "pointer_drag",
        "x1": 20,
        "y1": 40,
        "x2": 80,
        "y2": 80,
        "button": "left",
    }
    assert ActorDecisionRecord.from_dict(decision.to_dict()) == decision
    assert PhysicalAttemptRecord.from_dict(attempt.to_dict()) == attempt


def test_drag_evidence_accepts_paired_submitted_and_physical_paths() -> None:
    submitted = {
        **_default_arguments("drag"),
        "image_points": [[25.5, 30], [40, 50.25]],
    }
    plane = _plane()
    decision = _record_decision(
        plane,
        action="drag",
        arguments=submitted,
    )
    _record_admission(plane, before_payload=BEFORE_PAYLOAD)

    attempt = _reserve_attempt(plane)

    assert decision.to_dict()["canonical_arguments"] == submitted
    assert attempt.to_dict()["submitted_arguments"] == submitted
    assert attempt.to_dict()["physical_arguments"]["points"] == [
        [35, 50],
        [50, 70],
    ]
    assert PhysicalAttemptRecord.from_dict(attempt.to_dict()) == attempt


def test_drag_hold_evidence_round_trips_from_released_pointer() -> None:
    submitted = {
        **_default_arguments("drag"),
        "keep_holding": True,
    }
    plane = _plane()
    decision = _record_decision(
        plane,
        action="drag",
        arguments=submitted,
    )
    _record_admission(plane, before_payload=BEFORE_PAYLOAD)

    attempt = _reserve_attempt(plane)

    assert decision.to_dict()["actor_envelope"]["pointer_disposition"] == (
        "released"
    )
    assert decision.to_dict()["actor_envelope"]["pointer_lease_id"] is None
    assert attempt.to_dict()["physical_arguments"] == {
        "kind": "pointer_drag",
        "x1": 20,
        "y1": 40,
        "x2": 80,
        "y2": 80,
        "button": "left",
        "started_held": False,
        "keep_holding": True,
    }
    assert ActorDecisionRecord.from_dict(decision.to_dict()) == decision
    assert PhysicalAttemptRecord.from_dict(attempt.to_dict()) == attempt


def test_drag_hold_evidence_round_trips_held_pointer_continuation() -> None:
    submitted = {
        **_default_arguments("drag"),
        "image_points": [[25.5, 30], [40, 50.25]],
        "keep_holding": False,
    }
    plane = _plane()
    decision = _record_decision(
        plane,
        action="drag",
        arguments=submitted,
        pointer_disposition="held",
        pointer_lease_id="pointer-lease-1",
    )
    _record_admission(plane, before_payload=BEFORE_PAYLOAD)

    attempt = _reserve_attempt(plane, started_held=True)
    decision_wire = decision.to_dict()
    attempt_wire = attempt.to_dict()

    assert decision_wire["actor_envelope"]["pointer_disposition"] == "held"
    assert decision_wire["actor_envelope"]["pointer_lease_id"] == (
        "pointer-lease-1"
    )
    assert decision_wire["broker_projection"]["pointer_lease_id"] == (
        "pointer-lease-1"
    )
    assert attempt_wire["physical_arguments"]["points"] == [
        [35, 50],
        [50, 70],
    ]
    assert attempt_wire["physical_arguments"]["started_held"] is True
    assert attempt_wire["physical_arguments"]["keep_holding"] is False
    assert ActorDecisionRecord.from_dict(decision_wire) == decision
    assert PhysicalAttemptRecord.from_dict(attempt_wire) == attempt


def test_drag_hold_evidence_accepts_only_held_stationary_release() -> None:
    submitted = {
        "image_x1": 70.0,
        "image_y1": 60.0,
        "image_x2": 70.0,
        "image_y2": 60.0,
        "image_points": [],
        "keep_holding": False,
    }
    plane = _plane()
    decision = _record_decision(
        plane,
        action="drag",
        arguments=submitted,
        pointer_disposition="held",
        pointer_lease_id="pointer-lease-1",
    )
    _record_admission(plane, before_payload=BEFORE_PAYLOAD)
    attempt = plane.reserve_physical_attempt(
        decision_id=DECISION_ID,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        actor_envelope_sha256=decision.actor_envelope_sha256,
        submitted_arguments=submitted,
        physical_arguments={
            "kind": "pointer_drag",
            "x1": 80,
            "y1": 80,
            "x2": 80,
            "y2": 80,
            "button": "left",
            "points": [],
            "started_held": True,
            "keep_holding": False,
        },
        raw_device_route="raw_device.admitted_physical_drag/0",
        mechanism="physical_sendinput",
    )

    assert decision.to_dict()["canonical_arguments"] == submitted
    assert PhysicalAttemptRecord.from_dict(attempt.to_dict()) == attempt
    with pytest.raises(
        RuntimeEvidenceContractError,
        match="only a held-pointer release",
    ):
        _record_decision(
            _plane(),
            action="drag",
            arguments=submitted,
        )


@pytest.mark.parametrize(
    ("pointer_disposition", "pointer_lease_id", "action", "message"),
    (
        (
            "released",
            "pointer-lease-1",
            "drag",
            "must not carry a pointer lease",
        ),
        ("held", None, "drag", "canonical identity"),
        ("unknown", None, "drag", "must be released or held"),
        ("held", "pointer-lease-1", "click", "only a drag proposal"),
    ),
)
def test_actor_envelope_rejects_invalid_pointer_hold_states(
    pointer_disposition: str,
    pointer_lease_id: str | None,
    action: str,
    message: str,
) -> None:
    envelope = _actor_envelope(
        action=action,
        pointer_disposition=pointer_disposition,
        pointer_lease_id=pointer_lease_id,
    )

    with pytest.raises(RuntimeEvidenceContractError, match=message):
        _plane().record_actor_decision(
            actor_envelope=envelope,
            tool_schema=_tool_schema(action),
            broker_projection=_broker_projection(envelope),
        )


def test_held_actor_envelope_rejects_projection_pointer_lease_mismatch() -> None:
    envelope = _actor_envelope(
        action="drag",
        arguments={
            **_default_arguments("drag"),
            "keep_holding": False,
        },
        pointer_disposition="held",
        pointer_lease_id="pointer-lease-1",
    )
    projection = _broker_projection(envelope)
    projection["pointer_lease_id"] = "pointer-lease-other"

    with pytest.raises(
        RuntimeEvidenceContractError,
        match="changed Actor envelope identity",
    ):
        _plane().record_actor_decision(
            actor_envelope=envelope,
            tool_schema=_tool_schema("drag"),
            broker_projection=projection,
        )


def test_drag_evidence_rejects_non_boolean_submitted_keep_holding() -> None:
    with pytest.raises(
        RuntimeEvidenceContractError,
        match="keep_holding must be boolean",
    ):
        _record_decision(
            _plane(),
            action="drag",
            arguments={
                **_default_arguments("drag"),
                "keep_holding": 1,
            },
        )


@pytest.mark.parametrize(
    ("image_points", "message"),
    (
        ([[1, 2]] * 33, "at most 32"),
        ([[1, 2, 3]], "two-number point"),
        ([[-1, 2]], "finite non-negative"),
        ([[float("inf"), 2]], "must be finite"),
        ([[True, 2]], "finite non-negative"),
    ),
)
def test_drag_evidence_rejects_invalid_submitted_paths(
    image_points: list[Any],
    message: str,
) -> None:
    valid = _record_decision(
        _plane(),
        action="drag",
        arguments={
            **_default_arguments("drag"),
            "image_points": [[25, 30]],
        },
    )
    wire = valid.to_dict()
    wire["canonical_arguments"]["image_points"] = image_points
    wire["actor_envelope"]["proposal"]["args"]["image_points"] = copy.deepcopy(
        image_points
    )

    with pytest.raises(RuntimeEvidenceContractError, match=message):
        ActorDecisionRecord.from_dict(wire)


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda wire: wire["physical_arguments"].pop("points"),
            "path fields must be paired",
        ),
        (
            lambda wire: wire["submitted_arguments"].pop("image_points"),
            "path fields must be paired",
        ),
        (
            lambda wire: wire["physical_arguments"]["points"].append([60, 70]),
            "paths must have equal length",
        ),
        (
            lambda wire: wire["physical_arguments"].__setitem__(
                "points",
                [[1, 2]] * 33,
            ),
            "at most 32",
        ),
        (
            lambda wire: wire["physical_arguments"].__setitem__(
                "points",
                [[1.5, 2]],
            ),
            "two-integer point",
        ),
    ),
)
def test_drag_evidence_rejects_mismatched_or_invalid_physical_paths(
    mutate: Any,
    message: str,
) -> None:
    plane = _plane()
    _record_decision(
        plane,
        action="drag",
        arguments={
            **_default_arguments("drag"),
            "image_points": [[25, 30]],
        },
    )
    _record_admission(plane, before_payload=BEFORE_PAYLOAD)
    attempt = _reserve_attempt(plane)
    wire = attempt.to_dict()
    mutate(wire)

    with pytest.raises(RuntimeEvidenceContractError, match=message):
        PhysicalAttemptRecord.from_dict(wire)


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda wire: wire["physical_arguments"].pop("started_held"),
            "physical_arguments fields mismatch",
        ),
        (
            lambda wire: wire["physical_arguments"].__setitem__(
                "started_held",
                1,
            ),
            "started_held must be boolean",
        ),
        (
            lambda wire: wire["physical_arguments"].pop("keep_holding"),
            "physical_arguments fields mismatch",
        ),
        (
            lambda wire: (
                wire["physical_arguments"].pop("started_held"),
                wire["physical_arguments"].pop("keep_holding"),
            ),
            "hold fields must be paired",
        ),
        (
            lambda wire: wire["submitted_arguments"].pop("keep_holding"),
            "hold fields must be paired",
        ),
        (
            lambda wire: wire["physical_arguments"].__setitem__(
                "keep_holding",
                False,
            ),
            "keep_holding changed submitted intent",
        ),
    ),
)
def test_drag_evidence_rejects_invalid_physical_hold_states(
    mutate: Any,
    message: str,
) -> None:
    plane = _plane()
    _record_decision(
        plane,
        action="drag",
        arguments={
            **_default_arguments("drag"),
            "keep_holding": True,
        },
    )
    _record_admission(plane, before_payload=BEFORE_PAYLOAD)
    attempt = _reserve_attempt(plane)
    wire = attempt.to_dict()
    mutate(wire)

    with pytest.raises(RuntimeEvidenceContractError, match=message):
        PhysicalAttemptRecord.from_dict(wire)


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


def test_complete_report_binds_all_records_and_is_deterministic(
    tmp_path: Path,
) -> None:
    plane, payloads = _complete_plane(tmp_path)

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
    assert len(first.physical_attempts) == 1
    assert len(first.action_bindings) == 1
    assert len(first.frame_artifacts) == 2
    assert first.journal_event_count == 7
    assert first.journal_head_sha256 is not None
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

    journal = plane.journal
    assert journal is not None
    journal.close()
    replayed_journal = RuntimeEvidenceJournal.open_read_only(
        journal.run_directory,
        MISSION_ID,
        RUN_ID,
    )
    replayed = RuntimeEvidencePlane.from_journal(
        replayed_journal,
        expected_journal_head_sha256=first.journal_head_sha256,
    )
    assert replayed.sealed is True
    assert replayed.build_report(
        artifact_reader=payloads.__getitem__,
    ).to_dict() == first.to_dict()


def test_report_hash_is_compatible_and_streams_large_valid_aggregate() -> None:
    small = _plane()
    _record_decision(small)
    small_report = small.build_report()

    assert runtime_evidence_report_sha256(
        small_report
    ) == canonical_sha256(small_report.to_dict())

    large = _plane()
    for index in range(50):
        envelope = _actor_envelope()
        envelope["decision_id"] = f"decision-evidence-{index:04d}"
        envelope["mission_revision"] = index
        envelope["turn_index"] = index
        envelope["frame_id"] = index + 1
        envelope["broker_projection_id"] = _projection_id(envelope)
        large.record_actor_decision(
            actor_envelope=envelope,
            tool_schema=_tool_schema(),
            broker_projection=_broker_projection(envelope),
        )
    large_report = large.build_report()

    for record in large_report.actor_decisions:
        assert canonical_sha256(record.to_dict())
    with pytest.raises(
        RuntimeEvidenceContractError,
        match="node count exceeds 4096",
    ):
        canonical_sha256(large_report.to_dict())
    expected = hashlib.sha256(
        json.dumps(
            large_report.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert runtime_evidence_report_sha256(large_report) == expected
    assert runtime_evidence_report_sha256(large_report) == expected


def test_restarted_sealed_plane_cannot_reserve_or_call_raw_again(
    tmp_path: Path,
) -> None:
    plane, _payloads = _complete_plane(tmp_path)
    journal = plane.journal
    assert journal is not None
    journal.close()
    replayed = RuntimeEvidencePlane.from_journal(
        RuntimeEvidenceJournal.open_read_only(
            journal.run_directory,
            MISSION_ID,
            RUN_ID,
        )
    )
    raw_calls: list[str] = []

    def reserve_then_call_raw() -> None:
        _reserve_attempt(replayed)
        raw_calls.append("called")

    with pytest.raises(
        RuntimeEvidencePlaneError,
        match="replayed evidence plane is sealed",
    ):
        reserve_then_call_raw()

    assert replayed.sealed is True
    assert len(replayed.physical_attempt_records) == 1
    assert raw_calls == []


def test_rebuild_report_authenticates_complete_journal_and_artifacts(
    tmp_path: Path,
) -> None:
    run_directory, expected_head = _complete_persisted_run(tmp_path)

    report = RuntimeEvidencePlane.rebuild_report(
        run_directory,
        MISSION_ID,
        RUN_ID,
        expected_journal_head_sha256=expected_head,
    )

    assert report.status is RuntimeEvidenceReportStatus.COMPLETE
    assert report.missing_evidence == ()
    assert report.verification_failures == ()
    assert len(report.actor_decisions) == 1
    assert len(report.physical_attempts) == 1
    assert len(report.action_bindings) == 1
    assert len(report.frame_artifacts) == 2
    assert report.journal_head_sha256 == expected_head


@pytest.mark.parametrize("mutation", ("deleted", "tampered"))
def test_rebuild_report_rejects_rehashed_canonical_argument_damage(
    tmp_path: Path,
    mutation: str,
) -> None:
    run_directory, _expected_head = _complete_persisted_run(tmp_path)
    _rewrite_actor_arguments(
        run_directory,
        mutation=mutation,
    )

    report = RuntimeEvidencePlane.rebuild_report(
        run_directory,
        MISSION_ID,
        RUN_ID,
    )

    assert (
        report.status
        is RuntimeEvidenceReportStatus.VERIFICATION_FAILED
    )
    assert "durable_journal_corrupted" in (
        report.verification_failures
    )
    assert len(report.physical_attempts) == 0
    assert len(report.action_bindings) == 0


def test_complete_report_cannot_be_self_declared_without_records() -> None:
    with pytest.raises(
        RuntimeEvidenceContractError,
        match="cannot self-authenticate",
    ):
        RuntimeEvidenceReport.from_dict(
            {
                "format": "runtime-evidence-report/1",
                "mission_id": MISSION_ID,
                "run_id": RUN_ID,
                "status": "complete",
                "actor_decisions": [],
                "admissions": [],
                "physical_attempts": [],
                "action_bindings": [],
                "frame_artifacts": [],
                "journal_head_sha256": None,
                "journal_event_count": 0,
                "missing_evidence": [],
                "verification_failures": [],
            }
        )


def test_binding_retains_recomputable_reasoning_free_snapshots(
    tmp_path: Path,
) -> None:
    plane, payloads = _complete_plane(tmp_path)

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
        1, BEFORE_PAYLOAD
    )
    assert wire["after_frame_identity"] == _frame_identity(
        2, AFTER_PAYLOAD
    )
    assert wire["physical_attempt_count"] == 1
    assert wire["attempt_reservation_sha256"]
    assert wire["physical_attempt_record_sha256"] == canonical_sha256(
        plane.physical_attempt_records[0].to_dict()
    )
    assert wire["before_artifact_reference_sha256"]
    assert wire["after_artifact_reference_sha256"]
    assert not {
        "reasoning",
        "prompt",
        "model_response",
    } & _nested_keys(wire)


def test_serialized_complete_report_cannot_rewrite_run_scope(
    tmp_path: Path,
) -> None:
    plane, payloads = _complete_plane(tmp_path)
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
    assert (
        report.status
        is RuntimeEvidenceReportStatus.VERIFICATION_FAILED
    )
    assert f"{DECISION_ID}_raw_receipt" in report.missing_evidence
    assert report.verification_failures == (
        f"{DECISION_ID}_unreserved_raw_receipt",
    )


def test_missing_physical_attempt_never_reports_complete() -> None:
    plane = _plane()
    _record_decision(plane)
    _record_admission(plane, before_payload=BEFORE_PAYLOAD)

    report = plane.build_report()

    assert report.status is RuntimeEvidenceReportStatus.INCOMPLETE
    assert (
        f"{DECISION_ID}_physical_attempt"
        in report.missing_evidence
    )
    assert "durable_journal" in report.missing_evidence


@pytest.mark.parametrize("outcome", ("completed", "unknown"))
def test_admission_and_after_frame_cannot_switch_target_identity(
    outcome: str,
) -> None:
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
    transition = _transition(outcome=outcome)
    transition["post_frame_evidence"][0]["payload_hash"] = canonical_sha256(
        after
    )
    with pytest.raises(
        RuntimeEvidenceContractError,
        match="one target commit",
    ):
        plane.record_controller_transition(
            decision_id=DECISION_ID,
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            actor_envelope_sha256=ENVELOPE_SHA256,
            transition=transition,
            post_frame_identity=after,
        )


def test_unknown_applied_transition_can_record_same_window_geometry_change() -> None:
    plane = _plane()
    _record_decision(plane)
    _record_admission(
        plane,
        before_payload=b"\xff\xd8-before-\xff\xd9",
    )
    _record_receipt(plane)
    after = _frame_identity(2, AFTER_PAYLOAD)
    after["region"] = [20, 30, 800, 600]
    after["submitted_size"] = [800, 600]
    transition = _transition(
        outcome="unknown",
        input_completed=True,
    )
    transition["post_frame_evidence"][0]["payload_hash"] = canonical_sha256(
        after
    )

    binding = plane.record_controller_transition(
        decision_id=DECISION_ID,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        actor_envelope_sha256=ENVELOPE_SHA256,
        transition=transition,
        post_frame_identity=after,
    )

    assert binding.controller_transition["outcome"] == "unknown"
    assert binding.to_dict()["after_frame_identity"] == after
    assert ActionExecutionBinding.from_dict(binding.to_dict()) == binding


def test_completed_transition_cannot_relax_frame_geometry() -> None:
    plane = _plane()
    _record_decision(plane)
    _record_admission(
        plane,
        before_payload=b"\xff\xd8-before-\xff\xd9",
    )
    _record_receipt(plane)
    after = _frame_identity(2, AFTER_PAYLOAD)
    after["region"] = [20, 30, 800, 600]
    after["submitted_size"] = [800, 600]
    transition = _transition()
    transition["post_frame_evidence"][0]["payload_hash"] = canonical_sha256(
        after
    )

    with pytest.raises(
        RuntimeEvidenceContractError,
        match="one target commit",
    ):
        plane.record_controller_transition(
            decision_id=DECISION_ID,
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            actor_envelope_sha256=ENVELOPE_SHA256,
            transition=transition,
            post_frame_identity=after,
        )


def test_unverified_artifacts_and_mixed_missing_failure_fail_closed(
    tmp_path: Path,
) -> None:
    plane, payloads = _complete_plane(tmp_path)

    unverified = plane.build_report()
    assert unverified.status is RuntimeEvidenceReportStatus.INCOMPLETE
    assert "before_frame_1_artifact_unverified" in (
        unverified.missing_evidence
    )

    partial, partial_payloads = _complete_plane(
        tmp_path,
        include_after_artifact=False,
    )
    before_ref = partial.frame_artifact_references[0]
    partial_payloads[before_ref.artifact_uri] = (
        b"\xff\xd8\xff-tampered-\xff\xd9"
    )
    mixed = partial.build_report(
        artifact_reader=partial_payloads.__getitem__,
    )
    assert (
        mixed.status
        is RuntimeEvidenceReportStatus.VERIFICATION_FAILED
    )
    assert mixed.verification_failures == (
        "before_frame_1_artifact_hash_mismatch",
    )
    assert (
        f"{DECISION_ID}_after_frame_artifact"
        in mixed.missing_evidence
    )


def test_duplicate_receipt_permanently_fails_verification(
    tmp_path: Path,
) -> None:
    plane, payloads = _complete_plane(tmp_path)

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
    tmp_path: Path,
) -> None:
    if missing_part == "decision":
        report = _plane().build_report()
    else:
        plane, payloads = _complete_plane(
            tmp_path,
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


def test_frame_artifact_hash_change_fails_verification(
    tmp_path: Path,
) -> None:
    plane, payloads = _complete_plane(tmp_path)
    reference = next(
        item
        for item in plane.frame_artifact_references
        if item.role is FrameArtifactRole.AFTER
    )
    payloads[reference.artifact_uri] = (
        b"\xff\xd8\xff-tampered-\xff\xd9"
    )

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


def test_frame_artifact_must_match_runtime_frame_identity(
    tmp_path: Path,
) -> None:
    plane, payloads = _complete_plane(tmp_path)
    reference = next(
        item
        for item in plane.frame_artifact_references
        if item.role is FrameArtifactRole.AFTER
    )
    replacement = b"\xff\xd8\xff-replacement-\xff\xd9"
    payloads[reference.artifact_uri] = replacement
    changed_reference = FrameArtifactReference(
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        decision_id=DECISION_ID,
        action_id=DECISION_ID,
        frame_id=reference.frame_id,
        frame_identity=reference.frame_identity,
        artifact_uri="artifacts/replacement-frame-0002.jpg",
        raw_jpeg_sha256=hashlib.sha256(replacement).hexdigest(),
        runtime_frame_image_sha256=(
            reference.runtime_frame_image_sha256
        ),
        capture_timestamp_ns=reference.capture_timestamp_ns,
        role=reference.role,
    )
    isolated = _plane(
        run_directory=(
            tmp_path / f"evidence-run-{next(_RUN_COUNTER)}"
        )
    )
    _record_decision(isolated)
    before_reference = _frame_reference(
        frame_id=1,
        payload=BEFORE_PAYLOAD,
        role=FrameArtifactRole.BEFORE,
    )
    isolated.record_frame_artifact(before_reference)
    _record_admission(isolated, before_payload=BEFORE_PAYLOAD)
    _reserve_attempt(isolated)
    isolated.record_frame_artifact(changed_reference)
    _record_raw_receipt(isolated)
    _record_transition(isolated, after_payload=AFTER_PAYLOAD)
    isolated_payloads = {
        before_reference.artifact_uri: BEFORE_PAYLOAD,
        changed_reference.artifact_uri: replacement,
    }

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
        "mission_id",
        "run_id",
        "decision_id",
        "action_id",
        "frame_id",
        "frame_identity",
        "artifact_uri",
        "raw_jpeg_sha256",
        "runtime_frame_image_sha256",
        "capture_timestamp_ns",
        "role",
    }
    assert FrameArtifactReference.from_dict(wire) == reference
    with pytest.raises(RuntimeEvidenceContractError, match="artifact_uri"):
        FrameArtifactReference(
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            decision_id=DECISION_ID,
            action_id=DECISION_ID,
            frame_id=1,
            frame_identity=_frame_identity(1, BEFORE_PAYLOAD),
            artifact_uri="data:image/jpeg;base64,/9j/",
            raw_jpeg_sha256="0" * 64,
            runtime_frame_image_sha256=_runtime_image_sha256(
                BEFORE_PAYLOAD
            ),
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


def test_action_binding_requires_unique_shared_action_identity(
    tmp_path: Path,
) -> None:
    plane, _payloads = _complete_plane(tmp_path)
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
    _record_decision(plane, action="double_click")
    _record_admission(
        plane,
        before_payload=BEFORE_PAYLOAD,
    )
    attempt = _reserve_attempt(plane)
    _record_raw_receipt(plane)
    binding = _record_transition(
        plane,
        after_payload=AFTER_PAYLOAD,
    )

    assert binding is not None
    assert binding.action == "double_click"
    assert binding.raw_action_receipt["action"] == "click"
    assert attempt.physical_arguments["click_count"] == 2
