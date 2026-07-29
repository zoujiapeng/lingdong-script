"""Run-scoped canonical binding of vNext Runtime execution evidence."""
from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Mapping

from .artifacts import (
    FrameArtifactCorruptedError,
    FrameArtifactError,
    FrameArtifactMissingError,
    FrameArtifactWriter,
)
from .contracts import (
    ActionExecutionBinding,
    ActorDecisionRecord,
    AdmissionRecord,
    FrameArtifactReference,
    FrameArtifactRole,
    PhysicalAttemptRecord,
    RuntimeEvidenceContractError,
    RuntimeEvidenceReport,
    RuntimeEvidenceReportStatus,
    canonical_controller_transition,
    canonical_raw_action_receipt,
    canonical_sha256,
)
from .journal import (
    JOURNAL_FILENAME,
    RuntimeEvidenceJournal,
    RuntimeEvidenceJournalError,
    RuntimeEvidenceJournalEvent,
)


class RuntimeEvidencePlaneError(ValueError):
    """Evidence cannot be accepted without breaking the canonical chain."""


ArtifactReader = Callable[[str], bytes]
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class _DecisionState:
    record: ActorDecisionRecord
    actor_envelope_sha256: str


@dataclass(frozen=True, slots=True)
class _RawReceiptState:
    snapshot: Mapping[str, Any]
    sha256: str
    physical_attempt_count: int


@dataclass(frozen=True, slots=True)
class _TransitionState:
    snapshot: Mapping[str, Any]
    sha256: str


def _required_text(value: Any, name: str) -> str:
    if type(value) is not str or not value:
        raise RuntimeEvidencePlaneError(f"{name} must be non-empty")
    return value


def _plain_mapping(value: Any, name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise RuntimeEvidencePlaneError(
            f"{name} must be a plain canonical mapping"
        )
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _thaw_json(item)
            for key, item in value.items()
        }
    if type(value) is tuple:
        return [_thaw_json(item) for item in value]
    if type(value) is list:
        return [_thaw_json(item) for item in value]
    return value


def _sha256(value: Any, name: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise RuntimeEvidencePlaneError(
            f"{name} must be a lowercase SHA-256 digest"
        )
    return value


def _canonical_receipt(value: Any) -> Mapping[str, Any]:
    try:
        return canonical_raw_action_receipt(value)
    except RuntimeEvidenceContractError as exc:
        raise RuntimeEvidencePlaneError(str(exc)) from exc


def _canonical_transition(value: Any) -> Mapping[str, Any]:
    try:
        return canonical_controller_transition(value)
    except RuntimeEvidenceContractError as exc:
        raise RuntimeEvidencePlaneError(str(exc)) from exc


def _actor_frame_identity(decision: _DecisionState) -> dict[str, Any]:
    envelope = decision.record.actor_envelope
    return {
        "frame_id": envelope["frame_id"],
        "epoch": envelope["frame_epoch"],
        "hwnd": envelope["frame_hwnd"],
        "region": envelope["frame_region"],
        "scope": envelope["frame_scope"],
        "submitted_size": envelope["submitted_image_size"],
        "image_sha256": envelope["frame_image_sha256"],
        "media_type": "image/jpeg",
    }


def _is_pre_admission_rejection(
    receipt: Mapping[str, Any],
    transition: Mapping[str, Any],
) -> bool:
    return bool(
        receipt["outcome"] == "not_started"
        and receipt["input_completed"] is False
        and transition["outcome"] == "rejected"
        and transition["input_completed"] is False
        and transition["post_frame_id"] is None
        and not transition["post_frame_evidence"]
        and transition["action_effect"] is None
        and transition["action_effect_evidence"] is None
    )


class RuntimeEvidencePlane:
    """The only run-scoped owner of the canonical execution audit chain.

    The plane stores immutable references and reasoning-free execution facts.
    It never stores frame bytes or decides semantic mission completion.
    """

    def __init__(
        self,
        mission_id: str,
        run_id: str,
        *,
        clock_ns: Callable[[], int] = time.time_ns,
        journal: RuntimeEvidenceJournal | None = None,
    ) -> None:
        self.mission_id = _required_text(mission_id, "mission_id")
        self.run_id = _required_text(run_id, "run_id")
        if not callable(clock_ns):
            raise TypeError("clock_ns must be callable")
        if journal is not None and type(journal) is not RuntimeEvidenceJournal:
            raise TypeError(
                "journal must be RuntimeEvidenceJournal or null"
            )
        if journal is not None and (
            journal.mission_id != self.mission_id
            or journal.run_id != self.run_id
        ):
            raise ValueError(
                "journal mission/run scope differs from evidence plane"
            )
        self._clock_ns = clock_ns
        self._journal = journal
        self._decisions: dict[str, _DecisionState] = {}
        self._admissions: dict[str, AdmissionRecord] = {}
        self._attempts: dict[str, PhysicalAttemptRecord] = {}
        self._receipts: dict[str, _RawReceiptState] = {}
        self._transitions: dict[str, _TransitionState] = {}
        self._bindings: dict[str, ActionExecutionBinding] = {}
        self._frame_artifacts: dict[
            tuple[str, FrameArtifactRole], FrameArtifactReference
        ] = {}
        self._replay_artifact_reader: ArtifactReader | None = None
        self._replay_missing_evidence: set[str] = set()
        self._verification_failures: set[str] = set()
        self._sealed = journal is not None and not journal.writable
        self._lock = threading.RLock()

    @property
    def journal(self) -> RuntimeEvidenceJournal | None:
        return self._journal

    @property
    def durable(self) -> bool:
        return self._journal is not None

    @property
    def sealed(self) -> bool:
        return self._sealed

    def _ensure_mutable(self) -> None:
        if self._sealed:
            raise RuntimeEvidencePlaneError(
                "replayed evidence plane is sealed"
            )

    def _append(
        self,
        event_kind: str,
        payload: dict[str, Any],
        *,
        decision_id: str,
    ) -> RuntimeEvidenceJournalEvent | None:
        self._ensure_mutable()
        journal = self._journal
        if journal is None:
            return None
        try:
            return journal.append(
                event_kind,
                _thaw_json(payload),
                decision_id=decision_id,
                action_id=decision_id,
            )
        except RuntimeEvidenceJournalError as exc:
            raise RuntimeEvidencePlaneError(
                "durable evidence append failed closed"
            ) from exc

    def _check_scope(self, *, mission_id: str, run_id: str) -> None:
        if mission_id != self.mission_id or run_id != self.run_id:
            raise RuntimeEvidencePlaneError(
                "evidence is outside plane mission/run scope"
            )

    def _decision_for_envelope(
        self,
        decision_id: str,
        actor_envelope_sha256: str,
        *,
        phase: str,
    ) -> _DecisionState:
        digest = _sha256(actor_envelope_sha256, "actor_envelope_sha256")
        decision = self._decisions.get(decision_id)
        if decision is None:
            raise RuntimeEvidencePlaneError(
                f"{phase} cannot exist without a decision"
            )
        if decision.actor_envelope_sha256 != digest:
            self._verification_failures.add(
                f"{decision_id}_actor_envelope_identity_changed"
            )
            raise RuntimeEvidencePlaneError(
                f"{phase} envelope identity changed"
            )
        return decision

    def record_actor_decision(
        self,
        *,
        actor_envelope: dict[str, Any],
        tool_schema: dict[str, Any],
        broker_projection: dict[str, Any],
    ) -> ActorDecisionRecord:
        self._ensure_mutable()
        envelope = _plain_mapping(actor_envelope, "actor_envelope")
        proposal = _plain_mapping(
            envelope.get("proposal"),
            "actor_envelope.proposal",
        )
        decision_id = _required_text(
            envelope.get("decision_id"),
            "decision_id",
        )
        mission_id = _required_text(envelope.get("mission_id"), "mission_id")
        run_id = _required_text(envelope.get("run_id"), "run_id")
        self._check_scope(mission_id=mission_id, run_id=run_id)
        record = ActorDecisionRecord(
            decision_id=decision_id,
            mission_id=mission_id,
            run_id=run_id,
            mission_revision=envelope.get("mission_revision"),
            obligation_id=envelope.get("obligation_id"),
            turn_index=envelope.get("turn_index"),
            proposal_kind=proposal.get("kind"),
            action=proposal.get("action"),
            canonical_arguments=_plain_mapping(
                proposal.get("args"),
                "actor_envelope.proposal.args",
            ),
            canonical_arguments_sha256=canonical_sha256(proposal["args"]),
            actor_envelope=envelope,
            actor_envelope_sha256=canonical_sha256(envelope),
            broker_projection=_plain_mapping(
                broker_projection,
                "broker_projection",
            ),
            broker_projection_sha256=canonical_sha256(broker_projection),
            tool_schema=_plain_mapping(tool_schema, "tool_schema"),
            tool_schema_sha256=canonical_sha256(
                _plain_mapping(tool_schema, "tool_schema")
            ),
        )
        envelope_sha = record.actor_envelope_sha256
        with self._lock:
            if decision_id in self._decisions:
                self._verification_failures.add(
                    f"{decision_id}_decision_id_reused"
                )
                raise RuntimeEvidencePlaneError(
                    "decision_id cannot be reused"
                )
            self._append(
                "actor_decision",
                record.to_dict(),
                decision_id=decision_id,
            )
            self._decisions[decision_id] = _DecisionState(
                record=record,
                actor_envelope_sha256=envelope_sha,
            )
        return record

    def record_admission(
        self,
        *,
        decision_id: str,
        mission_id: str,
        run_id: str,
        frame_identity: dict[str, Any],
        target_identity: dict[str, Any],
        mutation_permit_id: str,
        controller_route: str,
        actor_envelope_sha256: str,
    ) -> AdmissionRecord:
        self._ensure_mutable()
        self._check_scope(mission_id=mission_id, run_id=run_id)
        with self._lock:
            decision = self._decision_for_envelope(
                decision_id,
                actor_envelope_sha256,
                phase="admission",
            )
            if decision_id in self._admissions:
                self._verification_failures.add(
                    f"{decision_id}_admission_reused"
                )
                raise RuntimeEvidencePlaneError(
                    "decision already has an admission"
                )
            before = self._frame_artifacts.get(
                (decision_id, FrameArtifactRole.BEFORE)
            )
            if self._journal is not None and before is None:
                raise RuntimeEvidencePlaneError(
                    "durable admission requires the before-frame artifact"
                )
            record = AdmissionRecord(
                decision_id=decision_id,
                actor_decision_record_sha256=canonical_sha256(
                    decision.record.to_dict()
                ),
                actor_envelope_sha256=decision.actor_envelope_sha256,
                frame_identity=frame_identity,
                target_identity=target_identity,
                mutation_permit_id=mutation_permit_id,
                controller_route=controller_route,
                accepted_timestamp_ns=self._clock_ns(),
            )
            if before is not None and (
                before.frame_identity != record.frame_identity
                or before.mission_id != self.mission_id
                or before.run_id != self.run_id
            ):
                raise RuntimeEvidencePlaneError(
                    "before-frame artifact changed admission identity"
                )
            self._append(
                "admission",
                record.to_dict(),
                decision_id=decision_id,
            )
            self._admissions[decision_id] = record
        return record

    def reserve_physical_attempt(
        self,
        *,
        decision_id: str,
        mission_id: str,
        run_id: str,
        actor_envelope_sha256: str,
        submitted_arguments: dict[str, Any],
        physical_arguments: dict[str, Any],
        raw_device_route: str,
        mechanism: str,
    ) -> PhysicalAttemptRecord:
        """Atomically reserve the sole RawDevice crossing for one decision."""

        self._ensure_mutable()
        self._check_scope(mission_id=mission_id, run_id=run_id)
        with self._lock:
            self._decision_for_envelope(
                decision_id,
                actor_envelope_sha256,
                phase="physical attempt",
            )
            if decision_id not in self._admissions:
                raise RuntimeEvidencePlaneError(
                    "physical attempt cannot precede admission"
                )
            if decision_id in self._attempts:
                self._verification_failures.add(
                    f"{decision_id}_duplicate_physical_attempt"
                )
                raise RuntimeEvidencePlaneError(
                    "decision already reserved its physical attempt"
                )
            decision = self._decisions[decision_id]
            admission = self._admissions[decision_id]
            record = PhysicalAttemptRecord.create(
                mission_id=self.mission_id,
                run_id=self.run_id,
                obligation_id=decision.record.obligation_id,
                decision_id=decision_id,
                action_id=decision_id,
                action=decision.record.action,
                attempt_index=1,
                submitted_arguments=submitted_arguments,
                physical_arguments=physical_arguments,
                raw_device_route=raw_device_route,
                mechanism=mechanism,
                target_identity=admission.target_identity,
                frame_identity=admission.frame_identity,
                mutation_permit_id=admission.mutation_permit_id,
                actor_decision_record_sha256=canonical_sha256(
                    decision.record.to_dict()
                ),
                admission_record_sha256=canonical_sha256(
                    admission.to_dict()
                ),
                timestamp_ns=self._clock_ns(),
            )
            if record.submitted_arguments != decision.record.canonical_arguments:
                raise RuntimeEvidencePlaneError(
                    "physical attempt changed canonical submitted arguments"
                )
            self._append(
                "physical_attempt",
                record.to_dict(),
                decision_id=decision_id,
            )
            self._attempts[decision_id] = record
            return record

    def record_raw_receipt(
        self,
        *,
        decision_id: str,
        mission_id: str,
        run_id: str,
        actor_envelope_sha256: str,
        receipt: dict[str, Any],
    ) -> None:
        self._ensure_mutable()
        self._check_scope(mission_id=mission_id, run_id=run_id)
        try:
            snapshot = _canonical_receipt(receipt)
        except RuntimeEvidencePlaneError:
            with self._lock:
                if decision_id in self._decisions:
                    self._verification_failures.add(
                        f"{decision_id}_invalid_raw_receipt"
                    )
            raise
        digest = canonical_sha256(dict(snapshot))
        with self._lock:
            decision = self._decision_for_envelope(
                decision_id,
                actor_envelope_sha256,
                phase="raw receipt",
            )
            expected_raw_action = (
                "click"
                if decision.record.action == "double_click"
                else decision.record.action
            )
            if snapshot["action"] != expected_raw_action:
                self._verification_failures.add(
                    f"{decision_id}_raw_action_changed"
                )
                raise RuntimeEvidencePlaneError(
                    "raw receipt action changed"
                )
            if decision_id in self._receipts:
                self._verification_failures.add(
                    f"{decision_id}_duplicate_raw_receipt"
                )
                raise RuntimeEvidencePlaneError(
                    "decision already has a raw receipt"
                )
            reserved = decision_id in self._attempts
            if snapshot["outcome"] != "not_started" and not reserved:
                self._verification_failures.add(
                    f"{decision_id}_unreserved_raw_receipt"
                )
                raise RuntimeEvidencePlaneError(
                    "physical receipt lacks an attempt reservation"
                )
            self._append(
                "raw_receipt",
                dict(snapshot),
                decision_id=decision_id,
            )
            self._receipts[decision_id] = _RawReceiptState(
                snapshot=snapshot,
                sha256=digest,
                physical_attempt_count=1 if reserved else 0,
            )

    def record_controller_transition(
        self,
        *,
        decision_id: str,
        mission_id: str,
        run_id: str,
        actor_envelope_sha256: str,
        transition: dict[str, Any],
        post_frame_identity: dict[str, Any] | None = None,
    ) -> ActionExecutionBinding:
        self._ensure_mutable()
        self._check_scope(mission_id=mission_id, run_id=run_id)
        try:
            snapshot = _canonical_transition(transition)
        except RuntimeEvidencePlaneError:
            with self._lock:
                if decision_id in self._decisions:
                    self._verification_failures.add(
                        f"{decision_id}_invalid_controller_transition"
                    )
            raise
        digest = canonical_sha256(dict(snapshot))
        with self._lock:
            decision = self._decision_for_envelope(
                decision_id,
                actor_envelope_sha256,
                phase="Controller transition",
            )
            if decision_id in self._transitions:
                self._verification_failures.add(
                    f"{decision_id}_duplicate_controller_transition"
                )
                raise RuntimeEvidencePlaneError(
                    "decision already has a Controller transition"
                )
            admission = self._admissions.get(decision_id)
            receipt = self._receipts.get(decision_id)
            if receipt is None:
                raise RuntimeEvidencePlaneError(
                    "Controller transition requires a raw receipt"
                )
            attempt = self._attempts.get(decision_id)
            before_ref = self._frame_artifacts.get(
                (decision_id, FrameArtifactRole.BEFORE)
            )
            after_ref = self._frame_artifacts.get(
                (decision_id, FrameArtifactRole.AFTER)
            )
            pre_admission_rejection = bool(
                admission is None
                and attempt is None
                and _is_pre_admission_rejection(
                    receipt.snapshot,
                    snapshot,
                )
            )
            if admission is None and not pre_admission_rejection:
                raise RuntimeEvidencePlaneError(
                    "Controller transition requires admission unless "
                    "input was proven not started"
                )
            before_identity = (
                _actor_frame_identity(decision)
                if admission is None
                else admission.frame_identity
            )
            if before_ref is not None and (
                before_ref.frame_identity != before_identity
            ):
                raise RuntimeEvidencePlaneError(
                    "before artifact changed Controller frame identity"
                )
            if pre_admission_rejection and (
                after_ref is not None
                or post_frame_identity is not None
            ):
                raise RuntimeEvidencePlaneError(
                    "pre-admission rejection cannot bind an after frame"
                )
            refs = list(snapshot["post_frame_evidence"])
            effect_ref = snapshot["action_effect_evidence"]
            if effect_ref is not None:
                refs.append(effect_ref)
            if any(
                ref["mission_id"] != self.mission_id
                or ref["run_id"] != self.run_id
                or ref["obligation_id"] != decision.record.obligation_id
                or ref["action_id"] != decision_id
                for ref in refs
            ) or (
                effect_ref is not None
                and (
                    effect_ref["kind"] != "deterministic_action_effect"
                    or effect_ref["trust"] != "deterministic"
                )
            ):
                self._verification_failures.add(
                    f"{decision_id}_transition_evidence_scope_changed"
                )
                raise RuntimeEvidencePlaneError(
                    "Controller transition evidence scope changed"
                )
            resolved_after_identity = (
                after_ref.frame_identity
                if after_ref is not None
                else post_frame_identity
            )
            if (
                after_ref is not None
                and post_frame_identity is not None
                and canonical_sha256(after_ref.frame_identity)
                != canonical_sha256(post_frame_identity)
            ):
                raise RuntimeEvidencePlaneError(
                    "post frame identity changed from durable artifact"
                )
            binding = ActionExecutionBinding(
                action_id=decision_id,
                decision_id=decision_id,
                action=decision.record.action,
                actor_envelope_sha256=actor_envelope_sha256,
                raw_action_receipt=receipt.snapshot,
                raw_action_receipt_sha256=receipt.sha256,
                controller_transition=snapshot,
                controller_transition_sha256=digest,
                physical_attempt_count=receipt.physical_attempt_count,
                attempt_reservation_sha256=(
                    attempt.reservation_sha256
                    if attempt is not None
                    else None
                ),
                physical_attempt_record_sha256=(
                    canonical_sha256(attempt.to_dict())
                    if attempt is not None
                    else None
                ),
                before_frame_identity=before_identity,
                after_frame_identity=resolved_after_identity,
                before_artifact_reference_sha256=(
                    canonical_sha256(before_ref.to_dict())
                    if before_ref is not None
                    else None
                ),
                after_artifact_reference_sha256=(
                    canonical_sha256(after_ref.to_dict())
                    if after_ref is not None
                    else None
                ),
            )
            self._append(
                "controller_transition",
                {
                    "transition": dict(snapshot),
                    "binding": binding.to_dict(),
                },
                decision_id=decision_id,
            )
            self._transitions[decision_id] = _TransitionState(
                snapshot=snapshot, sha256=digest
            )
            self._bindings[decision_id] = binding
            return binding

    def record_frame_artifact(
        self, reference: FrameArtifactReference
    ) -> FrameArtifactReference:
        self._ensure_mutable()
        if type(reference) is not FrameArtifactReference:
            raise TypeError("reference must be FrameArtifactReference")
        key = (reference.decision_id, reference.role)
        with self._lock:
            decision = self._decisions.get(reference.decision_id)
            if decision is None:
                raise RuntimeEvidencePlaneError(
                    "frame artifact cannot exist without a decision"
                )
            if (
                reference.mission_id != self.mission_id
                or reference.run_id != self.run_id
                or reference.action_id != reference.decision_id
            ):
                raise RuntimeEvidencePlaneError(
                    "frame artifact is outside plane scope"
                )
            if reference.role is FrameArtifactRole.BEFORE:
                envelope = decision.record.actor_envelope
                expected = {
                    "frame_id": envelope["frame_id"],
                    "epoch": envelope["frame_epoch"],
                    "hwnd": envelope["frame_hwnd"],
                    "region": envelope["frame_region"],
                    "scope": envelope["frame_scope"],
                    "submitted_size": envelope["submitted_image_size"],
                    "image_sha256": envelope["frame_image_sha256"],
                    "media_type": "image/jpeg",
                }
                if reference.frame_identity != expected:
                    raise RuntimeEvidencePlaneError(
                        "before artifact changed Actor frame identity"
                    )
                if reference.decision_id in self._admissions:
                    raise RuntimeEvidencePlaneError(
                        "before artifact cannot follow admission"
                    )
            elif reference.decision_id not in self._attempts:
                raise RuntimeEvidencePlaneError(
                    "after artifact cannot precede physical attempt"
                )
            if reference.decision_id in self._receipts:
                raise RuntimeEvidencePlaneError(
                    "frame artifact cannot follow raw receipt"
                )
            previous = self._frame_artifacts.get(key)
            if previous is not None:
                self._verification_failures.add(
                    f"{reference.role.value}_frame_{reference.frame_id}"
                    "_artifact_reference_reused"
                )
                raise RuntimeEvidencePlaneError(
                    "frame artifact reference is immutable"
                )
            self._append(
                "frame_artifact",
                reference.to_dict(),
                decision_id=reference.decision_id,
            )
            self._frame_artifacts[key] = reference
        return reference

    @property
    def decision_records(self) -> tuple[ActorDecisionRecord, ...]:
        with self._lock:
            return tuple(
                self._decisions[key].record
                for key in sorted(self._decisions)
            )

    @property
    def admission_records(self) -> tuple[AdmissionRecord, ...]:
        with self._lock:
            return tuple(
                self._admissions[key] for key in sorted(self._admissions)
            )

    @property
    def physical_attempt_records(self) -> tuple[PhysicalAttemptRecord, ...]:
        with self._lock:
            return tuple(
                self._attempts[key] for key in sorted(self._attempts)
            )

    @property
    def action_bindings(self) -> tuple[ActionExecutionBinding, ...]:
        with self._lock:
            return tuple(
                self._bindings[key] for key in sorted(self._bindings)
            )

    @property
    def frame_artifact_references(
        self,
    ) -> tuple[FrameArtifactReference, ...]:
        with self._lock:
            return tuple(
                self._frame_artifacts[key]
                for key in sorted(
                    self._frame_artifacts,
                    key=lambda item: (item[0], item[1].value),
                )
            )

    @classmethod
    def from_journal(
        cls,
        journal: RuntimeEvidenceJournal,
        *,
        artifact_store: FrameArtifactWriter | None = None,
        expected_journal_head_sha256: str | None = None,
    ) -> "RuntimeEvidencePlane":
        """Strictly fold a read-only journal into a sealed evidence plane."""

        if type(journal) is not RuntimeEvidenceJournal:
            raise TypeError("journal must be RuntimeEvidenceJournal")
        if journal.writable:
            raise RuntimeEvidencePlaneError(
                "journal replay requires a closed/read-only journal"
            )
        if (
            artifact_store is not None
            and type(artifact_store) is not FrameArtifactWriter
        ):
            raise TypeError(
                "artifact_store must be FrameArtifactWriter or null"
            )
        if artifact_store is not None and artifact_store.writable:
            raise RuntimeEvidencePlaneError(
                "artifact replay requires a read-only artifact store"
            )
        if artifact_store is not None and (
            artifact_store.run_directory != journal.run_directory
        ):
            raise RuntimeEvidencePlaneError(
                "journal and artifact store use different run directories"
            )
        expected_head = (
            None
            if expected_journal_head_sha256 is None
            else _sha256(
                expected_journal_head_sha256,
                "expected_journal_head_sha256",
            )
        )
        plane = cls(journal.mission_id, journal.run_id)
        if expected_head is not None and journal.head != expected_head:
            plane._verification_failures.add(
                "journal_head_mismatch"
            )
        for event in journal.events:
            try:
                plane._apply_replayed_event(event)
            except (
                RuntimeEvidenceContractError,
                RuntimeEvidencePlaneError,
                KeyError,
                TypeError,
            ):
                plane._verification_failures.add(
                    "durable_journal_corrupted"
                )
                break
        plane._journal = journal
        if artifact_store is not None:
            plane._attach_replayed_artifacts(artifact_store)
        plane._sealed = True
        return plane

    @classmethod
    def replay(
        cls,
        journal: RuntimeEvidenceJournal,
        *,
        artifact_store: FrameArtifactWriter | None = None,
        expected_journal_head_sha256: str | None = None,
    ) -> "RuntimeEvidencePlane":
        """Compatibility alias for strict sealed journal replay."""

        return cls.from_journal(
            journal,
            artifact_store=artifact_store,
            expected_journal_head_sha256=(
                expected_journal_head_sha256
            ),
        )

    @classmethod
    def rebuild_report(
        cls,
        run_directory: os.PathLike[str] | str,
        mission_id: str,
        run_id: str,
        expected_journal_head_sha256: str | None = None,
    ) -> RuntimeEvidenceReport:
        """Rebuild and authenticate one durable report without write access."""

        expected_head = (
            None
            if expected_journal_head_sha256 is None
            else _sha256(
                expected_journal_head_sha256,
                "expected_journal_head_sha256",
            )
        )
        try:
            journal = RuntimeEvidenceJournal.open_read_only(
                run_directory,
                mission_id,
                run_id,
            )
        except RuntimeEvidenceJournalError:
            plane = cls(mission_id, run_id)
            try:
                journal_path = (
                    Path(os.fspath(run_directory)).resolve(strict=False)
                    / JOURNAL_FILENAME
                )
                journal_path.lstat()
            except FileNotFoundError:
                pass
            except (OSError, RuntimeError, TypeError):
                plane._verification_failures.add(
                    "durable_journal_corrupted"
                )
            else:
                plane._verification_failures.add(
                    "durable_journal_corrupted"
                )
            plane._sealed = True
            return plane.build_report()

        artifact_store: FrameArtifactWriter | None = None
        artifact_store_missing = False
        artifact_store_corrupted = False
        try:
            artifact_store = FrameArtifactWriter.open_read_only(
                journal.run_directory
            )
        except FrameArtifactMissingError:
            artifact_store_missing = True
        except FrameArtifactError:
            artifact_store_corrupted = True

        plane = cls.from_journal(
            journal,
            artifact_store=artifact_store,
            expected_journal_head_sha256=expected_head,
        )
        if artifact_store_missing:
            plane._replay_missing_evidence.add(
                "frame_artifact_store"
            )
        if artifact_store_corrupted:
            plane._verification_failures.add(
                "frame_artifact_store_corrupted"
            )
        return plane.build_report()

    def _attach_replayed_artifacts(
        self,
        artifact_store: FrameArtifactWriter,
    ) -> None:
        references = self.frame_artifact_references
        by_uri: dict[str, FrameArtifactReference] = {}
        for reference in references:
            if reference.artifact_uri in by_uri:
                self._verification_failures.add(
                    "frame_artifact_uri_reused"
                )
                continue
            by_uri[reference.artifact_uri] = reference

        def read_artifact(artifact_uri: str) -> bytes:
            reference = by_uri.get(artifact_uri)
            if reference is None:
                raise FrameArtifactCorruptedError(
                    "artifact URI is absent from the replayed journal"
                )
            wire = reference.to_dict()
            return artifact_store.read_frame(
                mission_id=reference.mission_id,
                run_id=reference.run_id,
                decision_id=reference.decision_id,
                action_id=reference.action_id,
                role=reference.role.value,
                frame_identity=wire["frame_identity"],
                artifact_uri=reference.artifact_uri,
                sha256=reference.raw_jpeg_sha256,
            )

        self._replay_artifact_reader = read_artifact
        present_references: list[str] = []
        for artifact_uri in by_uri:
            try:
                read_artifact(artifact_uri)
            except FrameArtifactMissingError:
                self._replay_missing_evidence.add(
                    "frame_artifact_file"
                )
            except FrameArtifactError:
                self._verification_failures.add(
                    "frame_artifact_corrupted"
                )
                present_references.append(artifact_uri)
            else:
                present_references.append(artifact_uri)
        try:
            orphans = artifact_store.list_orphans(present_references)
        except FrameArtifactMissingError:
            self._replay_missing_evidence.add(
                "frame_artifact_file"
            )
        except FrameArtifactError:
            self._verification_failures.add(
                "frame_artifact_store_corrupted"
            )
        else:
            if orphans:
                self._verification_failures.add(
                    "orphan_frame_artifact"
                )

    def _apply_replayed_event(
        self,
        event: RuntimeEvidenceJournalEvent,
    ) -> None:
        self._ensure_mutable()
        if type(event) is not RuntimeEvidenceJournalEvent:
            raise RuntimeEvidencePlaneError(
                "journal contains a non-canonical event"
            )
        if (
            event.mission_id != self.mission_id
            or event.run_id != self.run_id
            or event.decision_id is None
            or event.action_id != event.decision_id
        ):
            raise RuntimeEvidencePlaneError(
                "journal event identity is outside evidence-plane scope"
            )
        decision_id = event.decision_id
        payload = event.to_dict()["payload"]
        with self._lock:
            if event.event_kind == "actor_decision":
                record = ActorDecisionRecord.from_dict(payload)
                if (
                    record.decision_id != decision_id
                    or record.mission_id != self.mission_id
                    or record.run_id != self.run_id
                    or decision_id in self._decisions
                ):
                    raise RuntimeEvidencePlaneError(
                        "journal Actor decision identity/order is invalid"
                    )
                self._decisions[decision_id] = _DecisionState(
                    record=record,
                    actor_envelope_sha256=record.actor_envelope_sha256,
                )
                return
            decision = self._decisions.get(decision_id)
            if decision is None:
                raise RuntimeEvidencePlaneError(
                    "journal evidence precedes Actor decision"
                )
            if event.event_kind == "frame_artifact":
                reference = FrameArtifactReference.from_dict(payload)
                key = (decision_id, reference.role)
                if (
                    reference.decision_id != decision_id
                    or reference.action_id != decision_id
                    or reference.mission_id != self.mission_id
                    or reference.run_id != self.run_id
                    or key in self._frame_artifacts
                ):
                    raise RuntimeEvidencePlaneError(
                        "journal frame artifact identity/order is invalid"
                    )
                if reference.role is FrameArtifactRole.BEFORE:
                    envelope = decision.record.actor_envelope
                    expected = {
                        "frame_id": envelope["frame_id"],
                        "epoch": envelope["frame_epoch"],
                        "hwnd": envelope["frame_hwnd"],
                        "region": envelope["frame_region"],
                        "scope": envelope["frame_scope"],
                        "submitted_size": envelope[
                            "submitted_image_size"
                        ],
                        "image_sha256": envelope[
                            "frame_image_sha256"
                        ],
                        "media_type": "image/jpeg",
                    }
                    if (
                        reference.frame_identity != expected
                        or decision_id in self._admissions
                        or decision_id in self._attempts
                        or decision_id in self._receipts
                        or decision_id in self._transitions
                    ):
                        raise RuntimeEvidencePlaneError(
                            "journal before artifact identity/order is invalid"
                        )
                else:
                    admission = self._admissions.get(decision_id)
                    attempt = self._attempts.get(decision_id)
                    before = self._frame_artifacts.get(
                        (decision_id, FrameArtifactRole.BEFORE)
                    )
                    if (
                        admission is None
                        or attempt is None
                        or before is None
                        or decision_id in self._receipts
                        or decision_id in self._transitions
                        or reference.frame_id != before.frame_id + 1
                        or any(
                            reference.frame_identity[field]
                            != before.frame_identity[field]
                            for field in (
                                "epoch",
                                "hwnd",
                                "region",
                                "scope",
                                "submitted_size",
                                "media_type",
                            )
                        )
                    ):
                        raise RuntimeEvidencePlaneError(
                            "journal after artifact identity/order is invalid"
                        )
                self._frame_artifacts[key] = reference
                return
            if event.event_kind == "admission":
                record = AdmissionRecord.from_dict(payload)
                before = self._frame_artifacts.get(
                    (decision_id, FrameArtifactRole.BEFORE)
                )
                if (
                    record.decision_id != decision_id
                    or decision_id in self._admissions
                    or decision_id in self._attempts
                    or decision_id in self._receipts
                    or decision_id in self._transitions
                    or before is None
                    or before.frame_identity != record.frame_identity
                    or record.actor_decision_record_sha256
                    != canonical_sha256(decision.record.to_dict())
                    or record.actor_envelope_sha256
                    != decision.actor_envelope_sha256
                ):
                    raise RuntimeEvidencePlaneError(
                        "journal admission identity/order is invalid"
                    )
                self._admissions[decision_id] = record
                return
            if event.event_kind == "physical_attempt":
                record = PhysicalAttemptRecord.from_dict(payload)
                admission = self._admissions.get(decision_id)
                if (
                    admission is None
                    or decision_id in self._attempts
                    or decision_id in self._receipts
                    or decision_id in self._transitions
                    or record.decision_id != decision_id
                    or record.action_id != decision_id
                    or record.mission_id != self.mission_id
                    or record.run_id != self.run_id
                    or record.obligation_id
                    != decision.record.obligation_id
                    or record.action != decision.record.action
                    or record.submitted_arguments
                    != decision.record.canonical_arguments
                    or record.target_identity
                    != admission.target_identity
                    or record.frame_identity
                    != admission.frame_identity
                    or record.mutation_permit_id
                    != admission.mutation_permit_id
                    or record.actor_decision_record_sha256
                    != canonical_sha256(decision.record.to_dict())
                    or record.admission_record_sha256
                    != canonical_sha256(admission.to_dict())
                ):
                    raise RuntimeEvidencePlaneError(
                        "journal physical attempt identity/order is invalid"
                    )
                self._attempts[decision_id] = record
                return
            if event.event_kind == "raw_receipt":
                snapshot = _canonical_receipt(payload)
                expected_action = (
                    "click"
                    if decision.record.action == "double_click"
                    else decision.record.action
                )
                reserved = decision_id in self._attempts
                if (
                    decision_id in self._receipts
                    or decision_id in self._transitions
                    or snapshot["action"] != expected_action
                    or (
                        snapshot["outcome"] != "not_started"
                        and not reserved
                    )
                ):
                    raise RuntimeEvidencePlaneError(
                        "journal raw receipt identity/order is invalid"
                    )
                self._receipts[decision_id] = _RawReceiptState(
                    snapshot=snapshot,
                    sha256=canonical_sha256(dict(snapshot)),
                    physical_attempt_count=1 if reserved else 0,
                )
                return
            if event.event_kind == "controller_transition":
                item = payload
                if set(item) != {"transition", "binding"}:
                    raise RuntimeEvidencePlaneError(
                        "journal Controller event fields are invalid"
                    )
                transition = _canonical_transition(item["transition"])
                binding = ActionExecutionBinding.from_dict(item["binding"])
                admission = self._admissions.get(decision_id)
                receipt = self._receipts.get(decision_id)
                attempt = self._attempts.get(decision_id)
                before = self._frame_artifacts.get(
                    (decision_id, FrameArtifactRole.BEFORE)
                )
                after = self._frame_artifacts.get(
                    (decision_id, FrameArtifactRole.AFTER)
                )
                refs = list(transition["post_frame_evidence"])
                effect_ref = transition["action_effect_evidence"]
                if effect_ref is not None:
                    refs.append(effect_ref)
                expected_attempt_count = 1 if attempt is not None else 0
                expected_attempt_reservation = (
                    None
                    if attempt is None
                    else attempt.reservation_sha256
                )
                expected_attempt_record = (
                    None
                    if attempt is None
                    else canonical_sha256(attempt.to_dict())
                )
                expected_before_artifact = (
                    None
                    if before is None
                    else canonical_sha256(before.to_dict())
                )
                expected_after_artifact = (
                    None
                    if after is None
                    else canonical_sha256(after.to_dict())
                )
                pre_admission_rejection = bool(
                    admission is None
                    and attempt is None
                    and receipt is not None
                    and _is_pre_admission_rejection(
                        receipt.snapshot,
                        transition,
                    )
                )
                expected_before_identity = (
                    _actor_frame_identity(decision)
                    if admission is None
                    else admission.frame_identity
                )
                if (
                    (
                        admission is None
                        and not pre_admission_rejection
                    )
                    or receipt is None
                    or decision_id in self._transitions
                    or binding.decision_id != decision_id
                    or binding.action_id != decision_id
                    or binding.action != decision.record.action
                    or binding.controller_transition != transition
                    or binding.raw_action_receipt != receipt.snapshot
                    or binding.before_frame_identity
                    != expected_before_identity
                    or binding.actor_envelope_sha256
                    != decision.actor_envelope_sha256
                    or binding.physical_attempt_count
                    != expected_attempt_count
                    or binding.attempt_reservation_sha256
                    != expected_attempt_reservation
                    or binding.physical_attempt_record_sha256
                    != expected_attempt_record
                    or binding.before_artifact_reference_sha256
                    != expected_before_artifact
                    or binding.after_artifact_reference_sha256
                    != expected_after_artifact
                    or (
                        after is not None
                        and binding.after_frame_identity
                        != after.frame_identity
                    )
                    or (
                        pre_admission_rejection
                        and (
                            after is not None
                            or binding.after_frame_identity is not None
                        )
                    )
                    or (
                        any(
                            ref["mission_id"] != self.mission_id
                            or ref["run_id"] != self.run_id
                            or ref["obligation_id"]
                            != decision.record.obligation_id
                            or ref["action_id"] != decision_id
                            for ref in refs
                        )
                    )
                ):
                    raise RuntimeEvidencePlaneError(
                        "journal Controller binding identity/order is invalid"
                    )
                digest = canonical_sha256(dict(transition))
                self._transitions[decision_id] = _TransitionState(
                    snapshot=transition,
                    sha256=digest,
                )
                self._bindings[decision_id] = binding
                return
        raise RuntimeEvidencePlaneError("journal event kind is unsupported")

    @staticmethod
    def _check_artifact(
        reference: FrameArtifactReference,
        *,
        expected_image_sha256: str,
        artifact_reader: ArtifactReader | None,
    ) -> tuple[str | None, str | None]:
        token = f"{reference.role.value}_frame_{reference.frame_id}"
        if artifact_reader is None:
            return f"{token}_artifact_unverified", None
        try:
            payload = artifact_reader(reference.artifact_uri)
        except FrameArtifactMissingError:
            return f"{token}_artifact_unreadable", None
        except FrameArtifactError:
            return None, f"{token}_artifact_corrupted"
        except Exception:
            return f"{token}_artifact_unreadable", None
        if type(payload) is not bytes:
            return f"{token}_artifact_not_bytes", None
        if (
            len(payload) < 4
            or not payload.startswith(b"\xff\xd8\xff")
            or not payload.endswith(b"\xff\xd9")
        ):
            return None, f"{token}_artifact_not_jpeg"
        if (
            hashlib.sha256(payload).hexdigest()
            != reference.raw_jpeg_sha256
        ):
            return None, f"{token}_artifact_hash_mismatch"
        encoded = base64.b64encode(payload)
        if hashlib.sha256(encoded).hexdigest() != expected_image_sha256:
            return None, f"{token}_runtime_frame_hash_mismatch"
        if reference.runtime_frame_image_sha256 != expected_image_sha256:
            return None, f"{token}_reference_runtime_hash_mismatch"
        return None, None

    def build_report(
        self,
        *,
        artifact_reader: ArtifactReader | None = None,
    ) -> RuntimeEvidenceReport:
        if artifact_reader is not None and not callable(artifact_reader):
            raise TypeError("artifact_reader must be callable or null")
        with self._lock:
            if artifact_reader is None:
                artifact_reader = self._replay_artifact_reader
            decisions = tuple(
                self._decisions[key].record
                for key in sorted(self._decisions)
            )
            admissions = dict(self._admissions)
            attempts = dict(self._attempts)
            receipts = dict(self._receipts)
            transitions = dict(self._transitions)
            bindings = dict(self._bindings)
            artifacts = dict(self._frame_artifacts)
            failures = set(self._verification_failures)
            replay_missing = set(self._replay_missing_evidence)
            journal = self._journal

        missing: set[str] = set(replay_missing)
        if not decisions:
            missing.add("actor_decision")
        if journal is None:
            missing.add("durable_journal")
        report_bindings: dict[str, ActionExecutionBinding] = {}
        for decision in decisions:
            decision_id = decision.decision_id
            admission = admissions.get(decision_id)
            binding = bindings.get(decision_id)
            if admission is None:
                missing.add(f"{decision_id}_admission")
            if decision_id not in attempts:
                missing.add(f"{decision_id}_physical_attempt")
            if decision_id not in receipts:
                missing.add(f"{decision_id}_raw_receipt")
            if decision_id not in transitions:
                missing.add(f"{decision_id}_controller_transition")
            if binding is None:
                missing.add(f"{decision_id}_action_binding")
                continue
            before_ref = artifacts.get(
                (decision_id, FrameArtifactRole.BEFORE)
            )
            after_ref = (
                None
                if binding.after_frame_id is None
                    else artifacts.get(
                        (decision_id, FrameArtifactRole.AFTER)
                    )
            )
            if before_ref is None:
                missing.add(f"{decision_id}_before_frame_artifact")
            if binding.after_frame_id is None:
                missing.add(f"{decision_id}_after_frame_identity")
            elif after_ref is None:
                missing.add(f"{decision_id}_after_frame_artifact")
            if admission is not None and before_ref is not None and (
                before_ref.capture_timestamp_ns
                > admission.accepted_timestamp_ns
            ):
                failures.add(
                    f"{decision_id}_before_frame_capture_order_mismatch"
                )
            if admission is not None and after_ref is not None and (
                after_ref.capture_timestamp_ns
                < admission.accepted_timestamp_ns
            ):
                failures.add(
                    f"{decision_id}_after_frame_capture_order_mismatch"
                )
            for reference, identity in (
                (before_ref, binding.before_frame_identity),
                (after_ref, binding.after_frame_identity),
            ):
                if reference is None or identity is None:
                    continue
                missing_item, failure_item = self._check_artifact(
                    reference,
                    expected_image_sha256=identity["image_sha256"],
                    artifact_reader=artifact_reader,
                )
                if missing_item is not None:
                    missing.add(missing_item)
                if failure_item is not None:
                    failures.add(failure_item)
            report_bindings[decision_id] = replace(
                binding,
                before_artifact_reference_sha256=(
                    None
                    if before_ref is None
                    else canonical_sha256(before_ref.to_dict())
                ),
                after_artifact_reference_sha256=(
                    None
                    if after_ref is None
                    else canonical_sha256(after_ref.to_dict())
                ),
            )

        missing_items = tuple(sorted(missing))
        failure_items = tuple(sorted(failures))
        status = (
            RuntimeEvidenceReportStatus.VERIFICATION_FAILED
            if failure_items
            else RuntimeEvidenceReportStatus.INCOMPLETE
            if missing_items
            else RuntimeEvidenceReportStatus.COMPLETE
        )
        bound_artifact_keys = {
            (decision_id, role)
            for decision_id, binding in report_bindings.items()
            for role, frame_id in (
                (FrameArtifactRole.BEFORE, binding.before_frame_id),
                (FrameArtifactRole.AFTER, binding.after_frame_id),
            )
            if frame_id is not None
        }
        return RuntimeEvidenceReport(
            mission_id=self.mission_id,
            run_id=self.run_id,
            status=status,
            actor_decisions=decisions,
            admissions=tuple(
                admissions[key] for key in sorted(admissions)
            ),
            physical_attempts=tuple(
                attempts[key] for key in sorted(attempts)
            ),
            action_bindings=tuple(
                report_bindings[key] for key in sorted(report_bindings)
            ),
            frame_artifacts=tuple(
                artifacts[key]
                for key in sorted(
                    set(artifacts) & bound_artifact_keys,
                    key=lambda item: (item[0], item[1].value),
                )
            ),
            journal_head_sha256=(
                None
                if journal is None or journal.event_count == 0
                else journal.head
            ),
            journal_event_count=(
                0 if journal is None else journal.event_count
            ),
            missing_evidence=missing_items,
            verification_failures=failure_items,
        )


__all__ = [
    "ArtifactReader",
    "RuntimeEvidencePlane",
    "RuntimeEvidencePlaneError",
]
