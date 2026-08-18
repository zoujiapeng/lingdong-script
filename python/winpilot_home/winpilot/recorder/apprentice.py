"""Apprentice-mode lifecycle built on deterministic human recording."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import SCRIPTS_DIR
from ..memory import experience
from . import human as human_mod
from . import trace as trace_mod


class ApprenticeManager:
    """Turns one human lesson into an editable graph and skill candidate."""

    def __init__(self, recorder: Any = None) -> None:
        self.recorder = recorder or human_mod.RECORDER
        self._lock = threading.RLock()
        self._session: dict[str, Any] | None = None

    def start(
        self,
        task: str,
        *,
        name: str = "",
        app: str = "",
        verification: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        task = str(task or "").strip() or "未命名录制"
        requirements = list(verification or [])
        if requirements:
            evidence_error = trace_mod.validate_evidence_requirements(requirements)
            if evidence_error:
                raise ValueError(evidence_error)
        with self._lock:
            if self.recorder.is_recording():
                raise RuntimeError("a demonstration is already recording")
            session_id = f"lesson-{uuid.uuid4().hex[:12]}"
            macro_name = trace_mod.ensure_script_name_available(
                str(name) if name is not None and name != "" else session_id,
                directory=Path(SCRIPTS_DIR),
                what="macro name",
            )
            session = {
                "session_id": session_id,
                "mode": "apprentice",
                "task": task,
                "app": str(app or "").strip(),
                "name": macro_name,
                "verification": requirements,
                "started_at": time.time(),
            }
            self.recorder.start(session["name"])
            self._session = session
            return {**session, "recording": True}

    def status(self) -> dict[str, Any]:
        with self._lock:
            session = dict(self._session or {})
            return {
                "mode": "apprentice",
                "recording": bool(self.recorder.is_recording()),
                "session_id": session.get("session_id"),
                "task": session.get("task", ""),
                "name": session.get("name", ""),
                "app": session.get("app", ""),
                "verification": session.get("verification", []),
                "started_at": session.get("started_at"),
            }

    def stop(self) -> dict[str, Any]:
        with self._lock:
            session = dict(self._session or {})
            if not session:
                raise RuntimeError("no apprentice lesson is active")
            result = self.recorder.stop()
            if not result.get("ok"):
                if not self.recorder.is_recording():
                    self._session = None
                raise RuntimeError(str(result.get("error") or "failed to stop demonstration"))

            path = (Path(SCRIPTS_DIR) / str(result.get("file") or "")).resolve()
            try:
                path.relative_to(Path(SCRIPTS_DIR).resolve())
            except ValueError as exc:
                raise RuntimeError("recorded lesson escaped the scripts directory") from exc
            script = json.loads(path.read_text(encoding="utf-8"))
            script.update({
                "format": "wps/2",
                "task": session["task"],
                "source": "apprentice",
                "source_session": session["session_id"],
                "evidence": {"required": list(session.get("verification") or [])},
            })
            error = trace_mod.validate_script(script)
            if error:
                raise RuntimeError(f"recorded lesson is invalid: {error}")
            path.write_text(json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")
            try:
                canonical_path = trace_mod.write_automation_ir(
                    script,
                    name=session["name"],
                    basename=path.name.removesuffix(".wps.json"),
                    directory=path.parent,
                )
            except FileExistsError as exc:
                self._session = None
                raise RuntimeError(
                    f"recorded actions were kept in {path.name}, but {exc}"
                ) from exc

            summaries = [json.dumps(step, ensure_ascii=False, sort_keys=True) for step in script.get("steps", [])]
            recipe = experience.record(
                session["task"],
                session.get("app", ""),
                summaries,
                verified=False,
                script=canonical_path.name,
                conditions=[
                    "Reuse only for the same demonstrated workflow goal.",
                    *([f'Current app or window must match "{session["app"]}".'] if session.get("app") else []),
                ],
                evidence=[
                    "Promotion requires a real replay whose structured postflight checks all pass.",
                ],
                verification=list(session.get("verification") or []),
            )
            self._session = None
            return {
                **result,
                "legacy_file": path.name,
                "file": canonical_path.name,
                "mode": "apprentice",
                "session_id": session["session_id"],
                "task": session["task"],
                "script": script,
                "memory": recipe,
            }


APPRENTICE = ApprenticeManager()
