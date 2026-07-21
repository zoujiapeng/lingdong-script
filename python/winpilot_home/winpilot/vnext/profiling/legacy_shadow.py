"""Same-entry legacy shadow bridge for one read-only task profile per run."""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol

from ..mission import project_legacy_contract
from .policy import DEFAULT_CAPABILITY_CATALOG, ExecutionPolicy
from .profiler import (
    ProfilerObligationView,
    TaskProfiler,
    TaskProfilerInput,
    TaskProfilerModel,
)
from .runtime import TaskProfilerShadowRecord, TaskProfilerShadowRuntime


class ShadowTraceWriter(Protocol):
    def record_sidecar(self, event: dict[str, Any], *, run_id: str) -> bool: ...


def profiler_input_from_legacy_contract(
    contract: Any,
    *,
    mission_id: str,
    run_id: str,
    available_capabilities: Iterable[str] = DEFAULT_CAPABILITY_CATALOG,
) -> tuple[Any, TaskProfilerInput]:
    """Build a lossless, identity-free Profiler input from task-contract/1."""
    definition = project_legacy_contract(
        contract,
        mission_id=mission_id,
        run_id=run_id,
    )
    available = tuple(sorted(set(available_capabilities)))
    canonical = set(DEFAULT_CAPABILITY_CATALOG)
    obligations = tuple(
        ProfilerObligationView(
            desired_state=item.desired_state,
            depends_on_count=len(item.depends_on),
            required_capabilities=tuple(
                capability
                for capability in item.required_capabilities
                if capability in canonical
            ),
            acceptance_criteria_count=len(item.acceptance_criteria),
            forbidden_effect_count=len(item.forbidden_effects),
        )
        for item in definition.obligations
    )
    safety_constraints = tuple(dict.fromkeys(
        effect
        for item in definition.obligations
        for effect in item.forbidden_effects
    ))
    return definition, TaskProfilerInput(
        original_goal=definition.original_goal,
        initial_obligations=obligations,
        verified_session_facts=(),
        available_runtime_capabilities=available,
        safety_constraints=safety_constraints,
        current_surface_summary=None,
    )


class LegacyTaskProfilerShadowHook:
    """Run-scoped hook; it records a profile but cannot alter execution."""

    def __init__(
        self,
        model: TaskProfilerModel,
        *,
        execution_policy: ExecutionPolicy | None = None,
        available_capabilities: Iterable[str] = DEFAULT_CAPABILITY_CATALOG,
        provider_availability: Iterable[str] | Mapping[str, bool] = (),
    ) -> None:
        self._model = model
        self._available_capabilities = tuple(sorted(set(available_capabilities)))
        self._provider_availability = provider_availability
        self._runtime = TaskProfilerShadowRuntime(
            TaskProfiler(model),
            execution_policy or ExecutionPolicy(),
            max_records=1,
        )

    def profile_run(
        self,
        *,
        run_id: str,
        contract: Any,
        trace: ShadowTraceWriter,
    ) -> TaskProfilerShadowRecord:
        mission_id = f"mission-{run_id}"
        definition, profiler_input = profiler_input_from_legacy_contract(
            contract,
            mission_id=mission_id,
            run_id=run_id,
            available_capabilities=self._available_capabilities,
        )
        record = self._runtime.profile_once(
            mission_key=f"{run_id}:{mission_id}",
            mission_id=mission_id,
            goal_revision=definition.goal_revision,
            profiler_input=profiler_input,
            available_capabilities=self._available_capabilities,
            provider_availability=self._provider_availability,
        )
        last_outcome = getattr(self._model, "last_outcome", None)
        trace.record_sidecar(
            {
                "type": "vnext_profiler_shadow",
                "runtime_version": "legacy",
                "status": record.status,
                "profile_call_count": record.profile_call_count,
                "request_retry_count": (
                    getattr(last_outcome, "request_retry_count", None)
                    if last_outcome is not None else None
                ),
                "elapsed_ms": record.elapsed_ms,
                "proposal": (
                    record.proposal.to_dict() if record.proposal is not None else None
                ),
                "profile": record.profile.to_dict() if record.profile is not None else None,
                "error_kind": record.error_kind,
            },
            run_id=run_id,
        )
        return record

    def close(self, run_id: str) -> None:
        self._runtime.close(f"{run_id}:mission-{run_id}")


__all__ = [
    "ShadowTraceWriter",
    "profiler_input_from_legacy_contract",
    "LegacyTaskProfilerShadowHook",
]
