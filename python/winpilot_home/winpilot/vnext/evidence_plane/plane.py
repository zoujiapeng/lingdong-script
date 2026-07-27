"""Run-scoped canonical binding of vNext Runtime execution evidence."""
from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, replace
import hashlib
import re
import threading
import time
from typing import Any, Mapping

from .contracts import (
    ActionExecutionBinding,
    ActorDecisionRecord,
    AdmissionRecord,
    FrameArtifactReference,
    FrameArtifactRole,
    RuntimeEvidenceContractError,
    RuntimeEvidenceReport,
    RuntimeEvidenceReportStatus,
    canonical_controller_transition,
    canonical_raw_action_receipt,
    canonical_sha256,
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
    ) -> None:
        self.mission_id = _required_text(mission_id, "mission_id")
        self.run_id = _required_text(run_id, "run_id")
        if not callable(clock_ns):
            raise TypeError("clock_ns must be callable")
        self._clock_ns = clock_ns
        self._decisions: dict[str, _DecisionState] = {}
        self._admissions: dict[str, AdmissionRecord] = {}
        self._attempt_reservations: set[str] = set()
        self._receipts: dict[str, _RawReceiptState] = {}
        self._transitions: dict[str, _TransitionState] = {}
        self._bindings: dict[str, ActionExecutionBinding] = {}
        self._frame_artifacts: dict[
            tuple[FrameArtifactRole, int], FrameArtifactReference
        ] = {}
        self._verification_failures: set[str] = set()
        self._lock = threading.RLock()

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
        decision_id: str,
        mission_id: str,
        run_id: str,
        obligation_id: str,
        proposal_kind: str,
        action: str,
        canonical_arguments: dict[str, Any],
        tool_schema: dict[str, Any],
        broker_projection: dict[str, Any],
        actor_envelope_sha256: str,
    ) -> ActorDecisionRecord:
        self._check_scope(mission_id=mission_id, run_id=run_id)
        record = ActorDecisionRecord(
            decision_id=decision_id,
            mission_id=mission_id,
            obligation_id=obligation_id,
            proposal_kind=proposal_kind,
            action=action,
            canonical_arguments_sha256=canonical_sha256(
                _plain_mapping(canonical_arguments, "canonical_arguments")
            ),
            tool_schema_sha256=canonical_sha256(
                _plain_mapping(tool_schema, "tool_schema")
            ),
            broker_projection_sha256=canonical_sha256(
                _plain_mapping(broker_projection, "broker_projection")
            ),
        )
        envelope_sha = _sha256(
            actor_envelope_sha256, "actor_envelope_sha256"
        )
        with self._lock:
            if decision_id in self._decisions:
                self._verification_failures.add(
                    f"{decision_id}_decision_id_reused"
                )
                raise RuntimeEvidencePlaneError(
                    "decision_id cannot be reused"
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
        self._check_scope(mission_id=mission_id, run_id=run_id)
        record = AdmissionRecord(
            decision_id=decision_id,
            frame_identity=frame_identity,
            target_identity=target_identity,
            mutation_permit_id=mutation_permit_id,
            controller_route=controller_route,
            accepted_timestamp_ns=self._clock_ns(),
        )
        with self._lock:
            self._decision_for_envelope(
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
            self._admissions[decision_id] = record
        return record

    def reserve_physical_attempt(
        self,
        *,
        decision_id: str,
        mission_id: str,
        run_id: str,
        actor_envelope_sha256: str,
    ) -> str:
        """Atomically reserve the sole RawDevice crossing for one decision."""

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
            if decision_id in self._attempt_reservations:
                self._verification_failures.add(
                    f"{decision_id}_duplicate_physical_attempt"
                )
                raise RuntimeEvidencePlaneError(
                    "decision already reserved its physical attempt"
                )
            self._attempt_reservations.add(decision_id)
            return decision_id

    def record_raw_receipt(
        self,
        *,
        decision_id: str,
        mission_id: str,
        run_id: str,
        actor_envelope_sha256: str,
        receipt: dict[str, Any],
    ) -> None:
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
            reserved = decision_id in self._attempt_reservations
            if snapshot["outcome"] != "not_started" and not reserved:
                self._verification_failures.add(
                    f"{decision_id}_unreserved_raw_receipt"
                )
                raise RuntimeEvidencePlaneError(
                    "physical receipt lacks an attempt reservation"
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
            if admission is None or receipt is None:
                raise RuntimeEvidencePlaneError(
                    "Controller transition requires admission and raw receipt"
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
                    canonical_sha256(
                        {
                            "action_id": decision_id,
                            "actor_envelope_sha256": (
                                actor_envelope_sha256
                            ),
                            "admission_sha256": canonical_sha256(
                                admission.to_dict()
                            ),
                        }
                    )
                    if receipt.physical_attempt_count == 1
                    else None
                ),
                before_frame_identity=admission.frame_identity,
                after_frame_identity=post_frame_identity,
            )
            self._transitions[decision_id] = _TransitionState(
                snapshot=snapshot, sha256=digest
            )
            self._bindings[decision_id] = binding
            return binding

    def record_frame_artifact(
        self, reference: FrameArtifactReference
    ) -> FrameArtifactReference:
        if type(reference) is not FrameArtifactReference:
            raise TypeError("reference must be FrameArtifactReference")
        key = (reference.role, reference.frame_id)
        with self._lock:
            previous = self._frame_artifacts.get(key)
            if previous is not None and previous != reference:
                self._verification_failures.add(
                    f"{reference.role.value}_frame_{reference.frame_id}"
                    "_artifact_reference_changed"
                )
                raise RuntimeEvidencePlaneError(
                    "frame artifact reference is immutable"
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
                    key=lambda item: (item[1], item[0].value),
                )
            )

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
        except Exception:
            return f"{token}_artifact_unreadable", None
        if type(payload) is not bytes:
            return f"{token}_artifact_not_bytes", None
        if hashlib.sha256(payload).hexdigest() != reference.sha256:
            return None, f"{token}_artifact_hash_mismatch"
        encoded = base64.b64encode(payload)
        if hashlib.sha256(encoded).hexdigest() != expected_image_sha256:
            return None, f"{token}_runtime_frame_hash_mismatch"
        return None, None

    def build_report(
        self,
        *,
        artifact_reader: ArtifactReader | None = None,
    ) -> RuntimeEvidenceReport:
        if artifact_reader is not None and not callable(artifact_reader):
            raise TypeError("artifact_reader must be callable or null")
        with self._lock:
            decisions = tuple(
                self._decisions[key].record
                for key in sorted(self._decisions)
            )
            admissions = dict(self._admissions)
            receipts = dict(self._receipts)
            transitions = dict(self._transitions)
            bindings = dict(self._bindings)
            artifacts = dict(self._frame_artifacts)
            failures = set(self._verification_failures)

        missing: set[str] = set()
        if not decisions:
            missing.add("actor_decision")
        report_bindings: dict[str, ActionExecutionBinding] = {}
        for decision in decisions:
            decision_id = decision.decision_id
            admission = admissions.get(decision_id)
            binding = bindings.get(decision_id)
            if admission is None:
                missing.add(f"{decision_id}_admission")
            if decision_id not in receipts:
                missing.add(f"{decision_id}_raw_receipt")
            if decision_id not in transitions:
                missing.add(f"{decision_id}_controller_transition")
            if binding is None:
                missing.add(f"{decision_id}_action_binding")
                continue
            before_ref = artifacts.get(
                (FrameArtifactRole.BEFORE, binding.before_frame_id)
            )
            after_ref = (
                None
                if binding.after_frame_id is None
                else artifacts.get(
                    (FrameArtifactRole.AFTER, binding.after_frame_id)
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
            RuntimeEvidenceReportStatus.INCOMPLETE
            if missing_items
            else RuntimeEvidenceReportStatus.VERIFICATION_FAILED
            if failure_items
            else RuntimeEvidenceReportStatus.COMPLETE
        )
        bound_artifact_keys = {
            (role, frame_id)
            for binding in report_bindings.values()
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
            action_bindings=tuple(
                report_bindings[key] for key in sorted(report_bindings)
            ),
            frame_artifacts=tuple(
                artifacts[key]
                for key in sorted(
                    set(artifacts) & bound_artifact_keys,
                    key=lambda item: (item[1], item[0].value),
                )
            ),
            missing_evidence=missing_items,
            verification_failures=failure_items,
        )


__all__ = [
    "ArtifactReader",
    "RuntimeEvidencePlane",
    "RuntimeEvidencePlaneError",
]
