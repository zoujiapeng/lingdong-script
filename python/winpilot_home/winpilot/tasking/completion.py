"""Fail-closed completion verification against task-contract obligations."""
from __future__ import annotations

from collections import Counter
from typing import Any

from .artifacts import ArtifactStore
from .contracts import CompletionObligation, TaskContract
from .evidence import EvidenceRegistry


class CompletionContractVerifier:
    """Verify exact run evidence for every declared final condition.

    Evidence providers describe *how* an obligation may be proved.  Provider
    identity alone is never the proof: the record must also carry the exact
    predicate and stage declared by the obligation.  Artifact-bound
    obligations are additionally tied to the producing stage's run-scoped
    artifact identity.
    """

    def __init__(
        self,
        contract: TaskContract,
        evidence: EvidenceRegistry,
        artifacts: ArtifactStore | None,
    ) -> None:
        self.contract = contract
        self.evidence = evidence
        self.artifacts = artifacts

    def _artifact_is_valid(self, artifact_id: str) -> bool:
        if not artifact_id or self.artifacts is None:
            return False
        if self.artifacts.run_id != self.evidence.run_id:
            return False
        try:
            artifact = self.artifacts.get(artifact_id)
        except Exception:
            return False
        return artifact.run_id == self.evidence.run_id

    def _record_satisfies(
        self,
        record: dict[str, Any],
        obligation: CompletionObligation,
        expected_artifact_id: str = "",
    ) -> bool:
        if record.get("run_id") != self.evidence.run_id or record.get("verified") is not True:
            return False
        if str(record.get("predicate") or "") != obligation.evidence_predicate:
            return False
        if str(record.get("stage_id") or "") != obligation.stage_id:
            return False
        if obligation.recipient and record.get("recipient") != obligation.recipient:
            return False
        artifact_id = str(record.get("artifact_id") or "")
        if obligation.artifact_ref and not artifact_id:
            return False
        if obligation.artifact_ref and not expected_artifact_id:
            # An artifact reference is a typed data dependency.  Do not let a
            # send/delivery record invent its identity when no producing-stage
            # evidence established that reference.
            return False
        if obligation.artifact_ref and artifact_id != expected_artifact_id:
            return False
        if artifact_id and not self._artifact_is_valid(artifact_id):
            return False
        return True

    @staticmethod
    def _provider_ids(obligation: CompletionObligation) -> set[str]:
        return {str(provider.id) for provider in obligation.evidence_providers if str(provider.id)}

    def _contract_errors(self) -> list[str]:
        errors: list[str] = []
        stage_ids = [stage.id for stage in self.contract.stages]
        duplicate_stages = sorted(key for key, count in Counter(stage_ids).items() if count > 1)
        if duplicate_stages:
            errors.append("duplicate stage ids: " + ", ".join(duplicate_stages))

        obligation_ids = [item.id for item in self.contract.obligations]
        if self.contract.stages and not obligation_ids:
            errors.append("task contract has executable stages but no completion obligations")
        duplicate_obligations = sorted(
            key for key, count in Counter(obligation_ids).items() if count > 1
        )
        if duplicate_obligations:
            errors.append("duplicate obligation ids: " + ", ".join(duplicate_obligations))

        known_stages = set(stage_ids)
        for obligation in self.contract.obligations:
            if obligation.stage_id not in known_stages:
                errors.append(
                    f"obligation {obligation.id!r} references unknown stage {obligation.stage_id!r}"
                )
            if not obligation.evidence_predicate:
                errors.append(f"obligation {obligation.id!r} has no evidence predicate")
            if not self._provider_ids(obligation):
                errors.append(f"obligation {obligation.id!r} has no evidence provider")

        top_level_declarations = sorted(
            (obligation.model_dump(mode="json") for obligation in self.contract.obligations),
            key=lambda item: str(item.get("id") or ""),
        )
        stage_declarations = sorted(
            (
                obligation.model_dump(mode="json")
                for stage in self.contract.stages
                for obligation in stage.obligations
            ),
            key=lambda item: str(item.get("id") or ""),
        )
        misplaced_stage_obligations = sorted({
            obligation.id
            for stage in self.contract.stages
            for obligation in stage.obligations
            if obligation.stage_id != stage.id
        })
        if misplaced_stage_obligations:
            errors.append(
                "stage obligations are bound to the wrong containing stage: "
                + ", ".join(misplaced_stage_obligations)
            )
        if top_level_declarations != stage_declarations:
            errors.append(
                "top-level obligations do not match their stage-bound declarations"
            )

        declared = list(self.contract.final_conditions)
        duplicate_conditions = sorted(
            key for key, count in Counter(declared).items() if count > 1
        )
        if duplicate_conditions:
            errors.append("duplicate final conditions: " + ", ".join(duplicate_conditions))
        unknown = sorted(set(declared) - set(obligation_ids))
        omitted = sorted(set(obligation_ids) - set(declared))
        if unknown:
            errors.append("unknown final conditions: " + ", ".join(unknown))
        if omitted:
            errors.append("obligations omitted from final conditions: " + ", ".join(omitted))

        if self.contract.compiler_version == "typed-stage-5" and not self.contract.requirements:
            errors.append("task contract has no retained user requirements")
        requirement_ids = [item.id for item in self.contract.requirements]
        duplicate_requirements = sorted(
            key for key, count in Counter(requirement_ids).items() if count > 1
        )
        if duplicate_requirements:
            errors.append("duplicate requirement ids: " + ", ".join(duplicate_requirements))
        known_predicates = {
            obligation.evidence_predicate for obligation in self.contract.obligations
        }
        for requirement in self.contract.requirements:
            unknown_stages = sorted(set(requirement.stage_ids) - known_stages)
            if unknown_stages:
                errors.append(
                    f"requirement {requirement.id!r} references unknown stages: "
                    + ", ".join(unknown_stages)
                )
            unknown_predicates = sorted(
                set(requirement.completion_predicates) - known_predicates
            )
            if unknown_predicates:
                errors.append(
                    f"requirement {requirement.id!r} references unknown completion predicates: "
                    + ", ".join(unknown_predicates)
                )
            if requirement.required and (
                not requirement.stage_ids or not requirement.completion_predicates
            ):
                errors.append(
                    f"required requirement {requirement.id!r} has no executable completion binding"
                )
        coverage = self.contract.requirement_coverage
        expected_covered = {
            item.id for item in self.contract.requirements if item.required
        }
        declared_covered = set(coverage.covered_requirement_ids)
        declared_uncovered = set(coverage.uncovered_requirement_ids)
        if self.contract.compiler_version == "typed-stage-5" and (
            coverage.complete is not True
            or declared_uncovered
            or expected_covered - declared_covered
        ):
            errors.append("task requirement coverage is incomplete")

        producer_refs = {
            output.name
            for stage in self.contract.stages
            for output in stage.outputs
            if output.name
        }
        unbound_refs = sorted({
            obligation.artifact_ref
            for obligation in self.contract.obligations
            if obligation.artifact_ref and obligation.artifact_ref not in producer_refs
        })
        if unbound_refs:
            errors.append("artifact references have no producing stage: " + ", ".join(unbound_refs))
        return errors

    def _artifact_refs(
        self,
        records: list[dict[str, Any]],
    ) -> dict[str, str]:
        """Resolve each logical artifact ref from its producing-stage proof."""
        producer_stages: dict[str, set[str]] = {}
        for stage in self.contract.stages:
            for output in stage.outputs:
                if output.name:
                    producer_stages.setdefault(output.name, set()).add(stage.id)

        resolved: dict[str, str] = {}
        for artifact_ref, stage_ids in producer_stages.items():
            producer_obligations = [
                obligation
                for obligation in self.contract.obligations
                if obligation.artifact_ref == artifact_ref
                and obligation.stage_id in stage_ids
            ]
            candidates: list[dict[str, Any]] = []
            for obligation in producer_obligations:
                providers = self._provider_ids(obligation)
                candidates.extend(
                    record
                    for record in records
                    if record.get("provider") in providers
                    and record.get("run_id") == self.evidence.run_id
                    and record.get("verified") is True
                    and str(record.get("predicate") or "") == obligation.evidence_predicate
                    and str(record.get("stage_id") or "") == obligation.stage_id
                    and self._artifact_is_valid(str(record.get("artifact_id") or ""))
                )
            if candidates:
                latest = max(candidates, key=lambda item: float(item.get("captured_at") or 0.0))
                resolved[artifact_ref] = str(latest.get("artifact_id") or "")
        return resolved

    def verify(self) -> dict[str, Any]:
        records = self.evidence.records()
        contract_errors = self._contract_errors()
        artifact_refs = self._artifact_refs(records)
        results: list[dict[str, Any]] = []
        for obligation in self.contract.obligations:
            matched: dict[str, Any] | None = None
            provider_id = ""
            expected_artifact_id = (
                artifact_refs.get(obligation.artifact_ref, "")
                if obligation.artifact_ref
                else ""
            )
            for provider in obligation.evidence_providers:
                candidate = next((
                    record for record in reversed(records)
                    if record.get("provider") == provider.id
                    and self._record_satisfies(
                        record,
                        obligation,
                        expected_artifact_id,
                    )
                ), None)
                if candidate is not None:
                    matched = candidate
                    provider_id = provider.id
                    break
            results.append({
                "obligation_id": obligation.id,
                "predicate": obligation.evidence_predicate,
                "description": obligation.description,
                "satisfied": matched is not None,
                "provider": provider_id,
                "evidence_id": str((matched or {}).get("id") or ""),
                "stage_id": obligation.stage_id,
                "artifact_ref": obligation.artifact_ref,
                "artifact_id": expected_artifact_id,
            })

        by_id = {item["obligation_id"]: item for item in results}
        conditions = [
            {
                "condition": condition,
                "satisfied": bool(by_id.get(condition, {}).get("satisfied")),
                "evidence_id": str(by_id.get(condition, {}).get("evidence_id") or ""),
            }
            for condition in self.contract.final_conditions
        ]
        missing = [item for item in results if not item["satisfied"]]
        ok = not contract_errors and not missing and all(
            item["satisfied"] for item in conditions
        )
        if contract_errors:
            reason = "invalid task completion contract: " + "; ".join(contract_errors)
        elif missing:
            reason = "missing task-contract evidence: " + ", ".join(
                str(item["obligation_id"]) for item in missing
            )
        else:
            reason = "all task-contract final conditions satisfied"
        primary_artifact_id = artifact_refs.get("primary_artifact", "")
        if not primary_artifact_id and artifact_refs:
            primary_artifact_id = next(iter(artifact_refs.values()))
        return {
            "ok": ok,
            "run_id": self.evidence.run_id,
            "contract_id": self.contract.task_id,
            "obligations": results,
            "final_conditions": conditions,
            "missing": missing,
            "contract_errors": contract_errors,
            "artifact_refs": artifact_refs,
            "primary_artifact_id": primary_artifact_id,
            "reason": reason,
        }
