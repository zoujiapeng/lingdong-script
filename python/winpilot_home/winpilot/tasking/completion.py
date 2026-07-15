"""Fail-closed completion verification against task-contract obligations."""
from __future__ import annotations

from typing import Any

from .artifacts import ArtifactStore
from .contracts import CompletionObligation, TaskContract
from .evidence import EvidenceRegistry


class CompletionContractVerifier:
    def __init__(self, contract: TaskContract, evidence: EvidenceRegistry, artifacts: ArtifactStore) -> None:
        self.contract = contract
        self.evidence = evidence
        self.artifacts = artifacts

    def _record_satisfies(
        self,
        record: dict[str, Any],
        obligation: CompletionObligation,
        expected_artifact_id: str = "",
    ) -> bool:
        if record.get("run_id") != self.evidence.run_id or record.get("verified") is not True:
            return False
        if obligation.recipient and record.get("recipient") != obligation.recipient:
            return False
        artifact_id = str(record.get("artifact_id") or "")
        if obligation.artifact_ref and not artifact_id:
            return False
        if obligation.artifact_ref and expected_artifact_id and artifact_id != expected_artifact_id:
            return False
        if artifact_id:
            try:
                self.artifacts.get(artifact_id)
            except Exception:
                return False
        return True

    def verify(self) -> dict[str, Any]:
        records = self.evidence.records()
        # Resolve the logical primary artifact once.  All artifact-bound
        # obligations must then refer to that exact run-scoped identity; a
        # later unrelated capture cannot satisfy delivery for an earlier file.
        primary_record = next((
            record for record in reversed(records)
            if record.get("provider") == "artifact_store_capture"
            and record.get("verified") is True
            and record.get("artifact_id")
        ), None)
        primary_artifact_id = str((primary_record or {}).get("artifact_id") or "")
        results: list[dict[str, Any]] = []
        for obligation in self.contract.obligations:
            matched: dict[str, Any] | None = None
            provider_id = ""
            for provider in obligation.evidence_providers:
                candidate = next((
                    record for record in reversed(records)
                    if record.get("provider") == provider.id
                    and self._record_satisfies(record, obligation, primary_artifact_id)
                ), None)
                if candidate is not None:
                    matched = candidate
                    provider_id = provider.id
                    break
            results.append({
                "obligation_id": obligation.id,
                "description": obligation.description,
                "satisfied": matched is not None,
                "provider": provider_id,
                "evidence_id": str((matched or {}).get("id") or ""),
                "stage_id": obligation.stage_id,
            })
        missing = [item for item in results if not item["satisfied"]]
        return {
            "ok": not missing,
            "run_id": self.evidence.run_id,
            "contract_id": self.contract.task_id,
            "obligations": results,
            "missing": missing,
            "primary_artifact_id": primary_artifact_id,
            "reason": "all task-contract obligations satisfied" if not missing else (
                "missing task-contract evidence: "
                + ", ".join(str(item["obligation_id"]) for item in missing)
            ),
        }
