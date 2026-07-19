"""Deterministic recovery routing for classified desktop-agent failures."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .failure_classifier import (
    FailureClassification,
    FailureClassifier,
    parse_tool_json,
)


class FailureRecoveryStatus(str, Enum):
    RECORDED = "recorded"


@dataclass
class FailureRecoveryResult:
    status: FailureRecoveryStatus
    classification: FailureClassification
    result_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "classification": self.classification.to_dict(),
            "executed": False,
            "result_meta": self.result_meta,
        }


def _root_result_is_proven_success(data: dict[str, Any]) -> bool:
    """A verified root tool result should not carry stale inner failure advice."""
    if not isinstance(data, dict):
        return False
    if data.get("ok") is True and data.get("verified") is True:
        return True
    return False


def _root_result_is_nonmutating_negative_observation(data: dict[str, Any]) -> bool:
    """A read-only verify miss is evidence, not a recovery failure."""
    if not isinstance(data, dict):
        return False
    actions = data.get("actions_executed")
    if not isinstance(actions, list) or not actions:
        return False
    mutating = {
        "click",
        "click_element",
        "type_text",
        "type_into_element",
        "hotkey",
        "drag",
        "draw_strokes",
        "run_action_batch",
    }
    if any(isinstance(item, dict) and item.get("action") in mutating for item in actions):
        return False
    packet = data.get("evidence_packet")
    return bool(
        isinstance(packet, dict)
        and packet.get("missing_texts")
        and not packet.get("matched_texts")
    )


class FailureRecoveryLayer:
    """Attach factual failure classification without choosing a recovery."""

    def __init__(
        self,
        classifier: FailureClassifier | None = None,
    ) -> None:
        self.classifier = classifier or FailureClassifier()

    def handle_tool_result(
        self,
        *,
        tool_name: str,
        args: dict[str, Any] | None,
        result_text: str,
        task: str = "",
        session: Any = None,
        recent_results: list[tuple[str, str]] | None = None,
        visible_evidence_ledger: list[dict[str, Any]] | None = None,
        tool_history: list[tuple[str, dict[str, Any], str]] | None = None,
    ) -> FailureRecoveryResult | None:
        data = parse_tool_json(result_text)
        if _root_result_is_proven_success(data):
            return None
        if _root_result_is_nonmutating_negative_observation(data):
            return None
        classification = self.classifier.classify_tool_result(
            tool_name,
            args or {},
            result_text,
        )
        if classification is None:
            return None

        return FailureRecoveryResult(
            status=FailureRecoveryStatus.RECORDED,
            classification=classification,
            result_meta={
                "failure_type": classification.failure_type.value,
            },
        )

def attach_recovery_metadata(
    result_text: str,
    recovery: FailureRecoveryResult | None,
) -> str:
    """Merge recovery metadata into JSON tool results without dropping fields."""
    if recovery is None:
        return result_text
    try:
        data = json.loads((result_text or "").strip())
    except (json.JSONDecodeError, TypeError):
        return result_text
    if not isinstance(data, dict) or not data:
        return result_text
    data.setdefault("failure_classification", recovery.classification.to_dict())
    data.setdefault("failure_recovery", {
        "status": recovery.status.value,
        "executed": False,
        "result_meta": recovery.result_meta,
    })
    return json.dumps(data, ensure_ascii=False, default=str)
