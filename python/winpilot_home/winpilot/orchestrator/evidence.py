"""Typed evidence normalization and deterministic gate evaluation."""
from __future__ import annotations

import time
from typing import Any
import uuid

from .contracts import EvidenceRecord, EvidenceRequirement


def normalize_evidence(
    value: Any,
    *,
    run_id: str = "",
    node_id: str = "",
) -> EvidenceRecord:
    if isinstance(value, EvidenceRecord):
        update: dict[str, str] = {}
        if run_id and not value.run_id:
            update["run_id"] = run_id
        if node_id and not value.node_id:
            update["node_id"] = node_id
        return value.model_copy(update=update) if update else value
    if not isinstance(value, dict):
        return EvidenceRecord(
            predicate="legacy.unverified",
            value=value,
            source="legacy",
            verified=False,
            run_id=run_id,
            node_id=node_id,
            text=str(value or ""),
        )

    predicate = str(value.get("predicate") or value.get("type") or "legacy.unverified").strip()
    source = str(value.get("source") or value.get("type") or "legacy").strip()
    if "verified" in value:
        verified = value.get("verified") is True
    elif "passed" in value:
        verified = value.get("passed") is True
    elif "approved" in value:
        verified = value.get("approved") is True
    elif "ok" in value:
        verified = value.get("ok") is True
    else:
        verified = False
    explicit_value = value.get("value")
    if explicit_value is None:
        explicit_value = value.get("target", value.get("subgoal", value.get("tool")))
    text = str(value.get("text") or value.get("snippet") or "")
    known = {
        "id", "predicate", "value", "source", "verified", "captured_at",
        "action_id", "node_id", "run_id", "text", "metadata",
    }
    metadata = dict(value.get("metadata") or {})
    metadata.update({key: item for key, item in value.items() if key not in known})
    return EvidenceRecord(
        id=str(value.get("id") or "") or uuid.uuid4().hex,
        predicate=predicate,
        value=explicit_value,
        source=source,
        verified=verified,
        captured_at=float(value.get("captured_at") or time.time()),
        action_id=str(value.get("action_id") or ""),
        node_id=str(value.get("node_id") or node_id),
        run_id=str(value.get("run_id") or run_id),
        text=text,
        metadata=metadata,
    )


def parse_requirement(value: Any) -> EvidenceRequirement:
    if isinstance(value, EvidenceRequirement):
        return value
    if isinstance(value, dict):
        return EvidenceRequirement.model_validate(value)
    return EvidenceRequirement(predicate=str(value or "").strip())


def evidence_matches(record: EvidenceRecord, requirement: EvidenceRequirement, *, now: float | None = None) -> bool:
    if not record.verified:
        return False
    if requirement.source and record.source.casefold() != requirement.source.casefold():
        return False
    if requirement.max_age_ms is not None:
        age_ms = max(0.0, ((now or time.time()) - record.captured_at) * 1000.0)
        if age_ms > requirement.max_age_ms:
            return False
    expected_predicate = requirement.predicate.casefold().strip()
    predicate_matches = record.predicate.casefold().strip() == expected_predicate
    claims = record.metadata.get("claims")
    if isinstance(claims, list):
        predicate_matches = predicate_matches or expected_predicate in {
            str(item).casefold().strip() for item in claims
        }
    if not predicate_matches:
        return False
    if requirement.equals is not None and record.value != requirement.equals:
        return False
    return True


def has_verified_evidence(values: list[Any]) -> bool:
    return any(normalize_evidence(value).verified for value in values)
