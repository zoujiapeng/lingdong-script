"""Run-scoped evidence registry used by tools, adapters, and completion gates."""
from __future__ import annotations

import threading
import time
from typing import Any
import uuid


class EvidenceRegistry:
    def __init__(self, run_id: str) -> None:
        self.run_id = str(run_id)
        self._records: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def record(
        self,
        predicate: str,
        *,
        provider: str,
        verified: bool,
        value: Any = None,
        stage_id: str = "",
        artifact_id: str = "",
        recipient: str = "",
        metadata: dict[str, Any] | None = None,
        text: str = "",
    ) -> dict[str, Any]:
        record = {
            "id": uuid.uuid4().hex,
            "predicate": str(predicate),
            "source": str(provider),
            "provider": str(provider),
            "verified": bool(verified),
            "value": value,
            "stage_id": str(stage_id),
            "artifact_id": str(artifact_id),
            "recipient": str(recipient),
            "run_id": self.run_id,
            "captured_at": time.time(),
            "metadata": dict(metadata or {}),
            "text": str(text or "")[:2000],
        }
        with self._lock:
            self._records.append(record)
        return dict(record)

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._records]

    def find(
        self,
        *,
        predicate: str = "",
        provider: str = "",
        artifact_id: str = "",
        recipient: str = "",
        verified: bool | None = True,
    ) -> list[dict[str, Any]]:
        def matches(item: dict[str, Any]) -> bool:
            return (
                (not predicate or item.get("predicate") == predicate)
                and (not provider or item.get("provider") == provider)
                and (not artifact_id or item.get("artifact_id") == artifact_id)
                and (not recipient or item.get("recipient") == recipient)
                and (verified is None or item.get("verified") is verified)
                and item.get("run_id") == self.run_id
            )
        with self._lock:
            return [dict(item) for item in self._records if matches(item)]

    def snapshot(self) -> dict[str, Any]:
        records = self.records()
        return {"run_id": self.run_id, "count": len(records), "records": records[-100:]}
