"""Behavior-preserving P0 observation of the existing legacy event stream.

The observer is a trace-side sidecar.  It may annotate a private event copy,
but it is never registered as a BUS sink and never mutates the object shared
with WebSocket subscribers or product consumers.  It never dispatches tools,
changes model inputs, changes run status, or participates in completion.
"""
from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Mapping

BASELINE_FORMAT = "vnext-baseline/0"
_ACTION_TYPES = {
    "click", "double_click", "type_text", "hotkey", "scroll", "drag",
    "draw_strokes", "mouse_down", "mouse_up", "move_mouse", "launch",
    "open_app", "focus_window", "tray_click", "clipboard_paste",
}


def _json_chars(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))
    except Exception:
        return len(str(value))


@dataclass(slots=True)
class BaselineTurn:
    """Normalized per-turn fields from legacy events."""

    step: int | None = None
    model_role: str | None = None
    model_tool_count: int | None = None
    model_tool_schema_chars: int | None = None
    prompt_context_chars: int | None = None
    mission_card_chars: int | None = None
    invalid_tool_argument_count: int | None = None
    duplicate_action_count: int | None = None
    no_progress_streak: int | None = None
    frame_refresh_count: int | None = None
    verifier_reject_count: int | None = None
    target_adjustment_px: float | None = None
    model_elapsed_ms: int | None = None
    controller_elapsed_ms: int | None = None
    time_to_first_action_ms: int | None = None
    planner_call_count: int | None = None
    inspector_call_count: int | None = None
    profile_call_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "model_role": self.model_role,
            "model_tool_count": self.model_tool_count,
            "model_tool_schema_chars": self.model_tool_schema_chars,
            "prompt_context_chars": self.prompt_context_chars,
            "mission_card_chars": self.mission_card_chars,
            "invalid_tool_argument_count": self.invalid_tool_argument_count,
            "duplicate_action_count": self.duplicate_action_count,
            "no_progress_streak": self.no_progress_streak,
            "frame_refresh_count": self.frame_refresh_count,
            "verifier_reject_count": self.verifier_reject_count,
            "target_adjustment_px": self.target_adjustment_px,
            "model_elapsed_ms": self.model_elapsed_ms,
            "controller_elapsed_ms": self.controller_elapsed_ms,
            "time_to_first_action_ms": self.time_to_first_action_ms,
            "planner_call_count": self.planner_call_count,
            "inspector_call_count": self.inspector_call_count,
            "profile_call_count": self.profile_call_count,
        }


@dataclass(slots=True)
class _RunObservation:
    run_id: str
    runtime_version: str = "legacy"
    mission_or_stage_id: str = ""
    started_ts: float = 0.0
    current_step: int = 0
    first_action_ts: float | None = None
    last_action_signature: str = ""
    turns: dict[int, BaselineTurn] = field(default_factory=dict)
    recommended_execution_depth: str | None = None
    effective_execution_depth: str | None = None
    execution_policy_override_reason: str | None = None
    required_obligation_count: int | None = None
    verified_obligation_count: int | None = None
    false_done_count: int | None = None
    artifact_identity_errors: int | None = None
    external_oracle_pass: bool | None = None


@dataclass(frozen=True, slots=True)
class LegacyBaselineSnapshot:
    """Serializable P0 snapshot; absent legacy fields remain ``None``."""

    run_id: str
    runtime_version: str
    mission_or_stage_id: str
    turns: tuple[dict[str, Any], ...]
    model_tool_count: int | None = None
    model_tool_schema_chars: int | None = None
    prompt_context_chars: int | None = None
    mission_card_chars: int | None = None
    invalid_tool_argument_count: int | None = None
    duplicate_action_count: int | None = None
    no_progress_streak: int | None = None
    frame_refresh_count: int | None = None
    verifier_reject_count: int | None = None
    target_adjustment_px: float | None = None
    model_elapsed_ms: int | None = None
    controller_elapsed_ms: int | None = None
    time_to_first_action_ms: int | None = None
    planner_call_count: int | None = None
    inspector_call_count: int | None = None
    profile_call_count: int | None = None
    recommended_execution_depth: str | None = None
    effective_execution_depth: str | None = None
    execution_policy_override_reason: str | None = None
    required_obligation_count: int | None = None
    verified_obligation_count: int | None = None
    false_done_count: int | None = None
    artifact_identity_errors: int | None = None
    external_oracle_pass: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": BASELINE_FORMAT,
            "run_id": self.run_id,
            "runtime_version": self.runtime_version,
            "mission_or_stage_id": self.mission_or_stage_id,
            "turns": [dict(turn) for turn in self.turns],
            "model_tool_count": self.model_tool_count,
            "model_tool_schema_chars": self.model_tool_schema_chars,
            "prompt_context_chars": self.prompt_context_chars,
            "mission_card_chars": self.mission_card_chars,
            "invalid_tool_argument_count": self.invalid_tool_argument_count,
            "duplicate_action_count": self.duplicate_action_count,
            "no_progress_streak": self.no_progress_streak,
            "frame_refresh_count": self.frame_refresh_count,
            "verifier_reject_count": self.verifier_reject_count,
            "target_adjustment_px": self.target_adjustment_px,
            "model_elapsed_ms": self.model_elapsed_ms,
            "controller_elapsed_ms": self.controller_elapsed_ms,
            "time_to_first_action_ms": self.time_to_first_action_ms,
            "planner_call_count": self.planner_call_count,
            "inspector_call_count": self.inspector_call_count,
            "profile_call_count": self.profile_call_count,
            "recommended_execution_depth": self.recommended_execution_depth,
            "effective_execution_depth": self.effective_execution_depth,
            "execution_policy_override_reason": self.execution_policy_override_reason,
            "required_obligation_count": self.required_obligation_count,
            "verified_obligation_count": self.verified_obligation_count,
            "false_done_count": self.false_done_count,
            "artifact_identity_errors": self.artifact_identity_errors,
            "external_oracle_pass": self.external_oracle_pass,
        }


class LegacyBaselineObserver:
    """State-free with respect to product behavior, stateful only for metrics."""

    def __init__(self, *, runtime_version: str = "legacy", max_runs: int = 128) -> None:
        self.runtime_version = str(runtime_version or "legacy")
        self._max_runs = max(1, int(max_runs))
        self._runs: OrderedDict[str, _RunObservation] = OrderedDict()
        self._lock = threading.RLock()

    def reset(self, run_id: str | None = None) -> None:
        with self._lock:
            if run_id is None:
                self._runs.clear()
            else:
                self._runs.pop(str(run_id), None)

    close_run = reset

    def _run(self, run_id: str, event: Mapping[str, Any]) -> _RunObservation:
        current = self._runs.get(run_id)
        if current is None:
            while len(self._runs) >= self._max_runs:
                self._runs.popitem(last=False)
            current = _RunObservation(
                run_id=run_id,
                runtime_version=str(event.get("runtime_version") or self.runtime_version),
                started_ts=float(event.get("ts") or time.time()),
            )
            self._runs[run_id] = current
        else:
            self._runs.move_to_end(run_id)
        return current

    @staticmethod
    def _increment(current: int | None, amount: int = 1) -> int:
        return int(current or 0) + int(amount)

    @staticmethod
    def _sum_optional(turns: tuple[dict[str, Any], ...], name: str) -> int | None:
        values = [int(turn[name]) for turn in turns if turn.get(name) is not None]
        return sum(values) if values else None

    @staticmethod
    def _max_optional(turns: tuple[dict[str, Any], ...], name: str) -> int | None:
        values = [int(turn[name]) for turn in turns if turn.get(name) is not None]
        return max(values) if values else None

    @staticmethod
    def _sum_float_optional(turns: tuple[dict[str, Any], ...], name: str) -> float | None:
        values = [float(turn[name]) for turn in turns if turn.get(name) is not None]
        return sum(values) if values else None

    @staticmethod
    def _turn(run: _RunObservation, step: Any = None) -> BaselineTurn:
        try:
            number = max(1, int(step or run.current_step or 1))
        except (TypeError, ValueError):
            number = max(1, run.current_step or 1)
        run.current_step = number
        return run.turns.setdefault(number, BaselineTurn(step=number))

    @staticmethod
    def _result_meta(event: Mapping[str, Any]) -> Mapping[str, Any]:
        value = event.get("result_meta")
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _set_optional_int(run: _RunObservation, name: str, value: Any) -> None:
        parsed = _safe_int(value)
        if parsed is not None:
            setattr(run, name, parsed)

    def _observe_run_metadata(self, run: _RunObservation, event: Mapping[str, Any]) -> None:
        """Copy only explicitly emitted facts into the observation sidecar."""
        for name in (
            "recommended_execution_depth",
            "effective_execution_depth",
            "execution_policy_override_reason",
        ):
            if event.get(name) is not None:
                setattr(run, name, str(event.get(name)))
        for name in ("required_obligation_count", "verified_obligation_count"):
            if name in event:
                self._set_optional_int(run, name, event.get(name))
        for key in ("external_oracle_pass", "oracle_pass"):
            if key in event and event.get(key) is not None:
                run.external_oracle_pass = bool(event.get(key))
        for key in ("artifact_identity_errors", "artifact_identity_error_count"):
            if key in event:
                value = _safe_int(event.get(key))
                if value is not None:
                    run.artifact_identity_errors = max(
                        int(run.artifact_identity_errors or 0), value,
                    )
        if event.get("false_done") is True:
            run.false_done_count = self._increment(run.false_done_count)
        if event.get("artifact_identity_error") is True:
            run.artifact_identity_errors = self._increment(run.artifact_identity_errors)

    def observe(self, event: dict[str, Any]) -> None:
        """Annotate a private event copy with a normalized baseline view."""
        if not isinstance(event, dict):
            return
        with self._lock:
            self._observe_unlocked(event)

    def _observe_unlocked(self, event: dict[str, Any]) -> None:
        """Internal observer; caller holds ``_lock``."""
        if not isinstance(event, dict):
            return
        event_type = str(event.get("type") or "")
        run_id = str(event.get("run_id") or "")
        if not run_id:
            return
        run = self._runs.get(run_id) or self._run(run_id, event)
        self._observe_run_metadata(run, event)
        if event_type == "agent_start":
            run.started_ts = float(event.get("ts") or time.time())
            runtime = event.get("runtime")
            run.runtime_version = str(
                event.get("runtime_version")
                or (runtime.get("runtime_version") if isinstance(runtime, Mapping) else "")
                or self.runtime_version
            )
            run.mission_or_stage_id = str(
                event.get("mission_or_stage_id")
                or event.get("stage_type")
                or event.get("task_contract_id")
                or ""
            )
            turn = self._turn(run, event.get("step") or 1)
            role = event.get("model_role")
            if role is None:
                role = event.get("agent")
            turn.model_role = str(role) if role is not None else None
            turn.model_tool_count = _safe_int(event.get("model_tool_count"))
            schema_value = event.get("model_tool_schema_chars")
            if schema_value is None:
                schema_value = event.get("tool_schema_chars")
            if schema_value is None and event.get("tools") is not None:
                schema_value = _json_chars(event.get("tools"))
            turn.model_tool_schema_chars = _safe_int(schema_value)
            event["vnext_baseline"] = self.snapshot(run_id).to_dict()
            return
        if event_type in {"agent_step", "agent_turn"}:
            turn = self._turn(run, event.get("step"))
            if event.get("model_role") is not None:
                turn.model_role = str(event.get("model_role"))
            if event.get("mission_card_chars") is not None:
                parsed = _safe_int(event.get("mission_card_chars"))
                if parsed is not None:
                    turn.mission_card_chars = parsed
            if event.get("prompt_context_chars") is not None:
                turn.prompt_context_chars = _safe_int(event.get("prompt_context_chars"))
            if event.get("model_tool_count") is not None:
                turn.model_tool_count = _safe_int(event.get("model_tool_count"))
            if event.get("model_tool_schema_chars") is not None:
                turn.model_tool_schema_chars = _safe_int(event.get("model_tool_schema_chars"))
        elif event_type == "agent_decision":
            turn = self._turn(run, event.get("step"))
            if event.get("model_role") is not None:
                turn.model_role = str(event.get("model_role"))
            elapsed = _safe_int(event.get("elapsed_ms"))
            if elapsed is not None:
                turn.model_elapsed_ms = int(turn.model_elapsed_ms or 0) + elapsed
            if event.get("prompt_context_chars") is not None:
                turn.prompt_context_chars = _safe_int(event.get("prompt_context_chars"))
        elif event_type == "agent_tool_start":
            turn = self._turn(run, event.get("step"))
            tool = str(event.get("tool") or "")
            args = event.get("args") if isinstance(event.get("args"), Mapping) else {}
            signature = json.dumps({"tool": tool, "args": args}, ensure_ascii=False, sort_keys=True, default=str)
            if tool in _ACTION_TYPES and signature == run.last_action_signature:
                turn.duplicate_action_count = self._increment(turn.duplicate_action_count)
            elif tool in _ACTION_TYPES:
                turn.duplicate_action_count = int(turn.duplicate_action_count or 0)
            if tool in _ACTION_TYPES and run.first_action_ts is None:
                now = float(event.get("ts") or time.time())
                run.first_action_ts = now
                turn.time_to_first_action_ms = max(0, int((now - run.started_ts) * 1000))
            if tool in _ACTION_TYPES:
                run.last_action_signature = signature
        elif event_type == "agent_tool":
            turn = self._turn(run, event.get("step"))
            elapsed = _safe_int(event.get("elapsed_ms"))
            if elapsed is not None:
                turn.controller_elapsed_ms = int(turn.controller_elapsed_ms or 0) + elapsed
            meta = self._result_meta(event)
            streak = _safe_int(meta.get("no_progress_streak"))
            if streak is not None:
                turn.no_progress_streak = max(int(turn.no_progress_streak or 0), streak)
            adjustment = meta.get("target_adjustment_px")
            if adjustment is not None:
                try:
                    turn.target_adjustment_px = float(turn.target_adjustment_px or 0.0) + float(adjustment)
                except (TypeError, ValueError):
                    pass
            if event.get("error") and "json" in str(event.get("error")).lower():
                turn.invalid_tool_argument_count = self._increment(turn.invalid_tool_argument_count)
            elif event.get("error") is not None:
                turn.invalid_tool_argument_count = int(turn.invalid_tool_argument_count or 0)
            if str(event.get("tool") or "") in {"observe", "screenshot"}:
                turn.frame_refresh_count = self._increment(turn.frame_refresh_count)
        elif event_type in {"visual_frame", "frame_refresh", "trace_screenshot"}:
            turn = self._turn(run, event.get("step"))
            turn.frame_refresh_count = self._increment(turn.frame_refresh_count)
        elif event_type == "done_check":
            if event.get("passed") is False:
                turn = self._turn(run, event.get("step"))
                turn.verifier_reject_count = self._increment(turn.verifier_reject_count)
                if event.get("after_agent_done") is True:
                    run.false_done_count = self._increment(run.false_done_count)
        elif event_type in {"agent_plan", "planner_call"}:
            turn = self._turn(run, event.get("step"))
            turn.planner_call_count = self._increment(turn.planner_call_count)
        elif event_type in {"visual_inspector", "inspector_call"}:
            turn = self._turn(run, event.get("step"))
            turn.inspector_call_count = self._increment(turn.inspector_call_count)
        elif event_type in {"task_profile", "profile_call", "profiler_call"}:
            turn = self._turn(run, event.get("step"))
            turn.profile_call_count = self._increment(turn.profile_call_count)
        elif event_type == "agent_done":
            event["vnext_baseline"] = self.snapshot(run_id).to_dict()

        if event_type in {"agent_step", "agent_turn", "agent_tool", "agent_decision"}:
            event["vnext_baseline_turn"] = self._turn(run).to_dict()

    def snapshot(self, run_id: str | None = None) -> LegacyBaselineSnapshot:
        with self._lock:
            return self._snapshot_unlocked(run_id)

    def _snapshot_unlocked(self, run_id: str | None = None) -> LegacyBaselineSnapshot:
        """Return a snapshot; caller holds ``_lock``."""
        if run_id is None:
            run_id = next(reversed(self._runs), "") if self._runs else ""
        run = self._runs.get(str(run_id))
        if run is None:
            return LegacyBaselineSnapshot(
                run_id=str(run_id or ""),
                runtime_version=self.runtime_version,
                mission_or_stage_id="",
                turns=(),
                recommended_execution_depth=None,
                effective_execution_depth=None,
                execution_policy_override_reason=None,
            )
        turns = tuple(run.turns[key].to_dict() for key in sorted(run.turns))
        return LegacyBaselineSnapshot(
            run_id=run.run_id,
            runtime_version=run.runtime_version,
            mission_or_stage_id=run.mission_or_stage_id,
            turns=turns,
            model_tool_count=self._max_optional(turns, "model_tool_count"),
            model_tool_schema_chars=self._max_optional(turns, "model_tool_schema_chars"),
            prompt_context_chars=self._max_optional(turns, "prompt_context_chars"),
            mission_card_chars=self._max_optional(turns, "mission_card_chars"),
            invalid_tool_argument_count=self._sum_optional(turns, "invalid_tool_argument_count"),
            duplicate_action_count=self._sum_optional(turns, "duplicate_action_count"),
            no_progress_streak=self._max_optional(turns, "no_progress_streak"),
            frame_refresh_count=self._sum_optional(turns, "frame_refresh_count"),
            verifier_reject_count=self._sum_optional(turns, "verifier_reject_count"),
            target_adjustment_px=self._sum_float_optional(turns, "target_adjustment_px"),
            model_elapsed_ms=self._sum_optional(turns, "model_elapsed_ms"),
            controller_elapsed_ms=self._sum_optional(turns, "controller_elapsed_ms"),
            time_to_first_action_ms=next(
                (turn.get("time_to_first_action_ms") for turn in turns if turn.get("time_to_first_action_ms") is not None),
                None,
            ),
            planner_call_count=self._sum_optional(turns, "planner_call_count"),
            inspector_call_count=self._sum_optional(turns, "inspector_call_count"),
            profile_call_count=self._sum_optional(turns, "profile_call_count"),
            recommended_execution_depth=run.recommended_execution_depth,
            effective_execution_depth=run.effective_execution_depth,
            execution_policy_override_reason=run.execution_policy_override_reason,
            required_obligation_count=run.required_obligation_count,
            verified_obligation_count=run.verified_obligation_count,
            false_done_count=run.false_done_count,
            artifact_identity_errors=run.artifact_identity_errors,
            external_oracle_pass=run.external_oracle_pass,
        )


def _safe_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def install_legacy_baseline_observer() -> LegacyBaselineObserver:
    """Create an observer for a private TraceRecorder event copy.

    The historical function name is retained for import compatibility, but
    it no longer attaches anything to BUS.  Callers own the returned instance
    and must pass only a private event copy to :meth:`observe`.
    """
    return LegacyBaselineObserver()


def default_legacy_baseline_observer() -> LegacyBaselineObserver | None:
    return None


__all__ = [
    "BASELINE_FORMAT",
    "BaselineTurn",
    "LegacyBaselineSnapshot",
    "LegacyBaselineObserver",
    "install_legacy_baseline_observer",
    "default_legacy_baseline_observer",
]
