"""P2b read-only, at-most-once Task Profiler shadow tests."""
from __future__ import annotations

import json
import threading

import pytest

from winpilot.vnext.profiling import (
    CognitiveServices,
    ExecutionPolicy,
    InvalidProfileSchema,
    ProfilerObligationView,
    TaskDimensions,
    TaskProfileProposal,
    TaskProfiler,
    TaskProfilerInput,
    TaskProfilerRequest,
    TaskProfilerShadowRecord,
    TaskProfilerShadowRuntime,
)


def _proposal(**overrides) -> TaskProfileProposal:
    values = {
        "dimensions": TaskDimensions(),
        "recommended_depth": "standard",
        "minimum_depth": "direct",
        "requested_services": CognitiveServices(),
        "broad_capability_hypotheses": ("visual.interact",),
        "upgrade_triggers": ("verified cross-application dependency appears",),
        "downgrade_conditions": ("completion becomes deterministic",),
        "confidence": 0.9,
        "rationale": ("ordinary local sequence",),
    }
    values.update(overrides)
    return TaskProfileProposal(**values)


def _input(goal: str = "完成当前任务") -> TaskProfilerInput:
    return TaskProfilerInput(
        original_goal=goal,
        initial_obligations=(
            ProfilerObligationView(
                desired_state=goal,
                required_capabilities=("visual.interact",),
                acceptance_criteria_count=1,
            ),
        ),
        available_runtime_capabilities=("visual.interact", "artifact.capture"),
        safety_constraints=("preserve forbidden effects",),
    )


class StubProfilerModel:
    def __init__(self, output=None, *, error: Exception | None = None) -> None:
        self.output = output if output is not None else _proposal().to_dict()
        self.error = error
        self.requests: list[TaskProfilerRequest] = []

    def propose(self, request: TaskProfilerRequest):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.output


def test_profiler_input_and_request_are_strict_and_identity_free() -> None:
    value = _input()
    assert TaskProfilerInput.from_dict(value.to_dict()) == value
    request = TaskProfilerRequest(input=value)
    payload = request.model_payload()
    serialized = json.dumps(
        {"input": payload["input"], "response_schema": payload["response_schema"]},
        ensure_ascii=False,
        sort_keys=True,
    )
    assert payload["model_role"] == "task_profiler"
    assert payload["tool_names"] == []
    for forbidden in (
        "mission_id",
        "run_id",
        "frame_id",
        "runtime_version",
        "obligation_patch",
        "application_workflow",
        "coordinate",
        "tool_sequence",
    ):
        assert forbidden not in serialized

    with pytest.raises(InvalidProfileSchema):
        TaskProfilerInput.from_dict({**value.to_dict(), "mission_id": "m1"})
    with pytest.raises(InvalidProfileSchema):
        TaskProfilerRequest(input=value, tool_names=("click",))


def test_profiler_uses_one_isolated_tool_free_request() -> None:
    model = StubProfilerModel()
    proposal = TaskProfiler(model).profile(_input())
    assert proposal == _proposal()
    assert len(model.requests) == 1
    assert model.requests[0].tool_names == ()
    assert model.requests[0].input.original_goal == "完成当前任务"


@pytest.mark.parametrize(
    "forbidden,value",
    (
        ("runtime_version", "vnext"),
        ("application_workflow", ["open", "click"]),
        ("obligation_patch", {"add": "step"}),
        ("button", "submit"),
        ("tool", "click"),
        ("coordinate", [1, 2]),
        ("mission_id", "m1"),
        ("frame_id", 4),
    ),
)
def test_profiler_output_cannot_claim_gui_or_runtime_authority(forbidden, value) -> None:
    output = {**_proposal().to_dict(), forbidden: value}
    with pytest.raises(InvalidProfileSchema):
        TaskProfiler(StubProfilerModel(output)).profile(_input())


def test_shadow_calls_profiler_at_most_once_per_mission() -> None:
    model = StubProfilerModel()
    runtime = TaskProfilerShadowRuntime(
        TaskProfiler(model),
        ExecutionPolicy(provider_availability=("obligation_verifier",)),
    )
    first = runtime.profile_once(
        mission_key="run-1:mission-1",
        mission_id="mission-1",
        goal_revision=0,
        profiler_input=_input(),
        available_capabilities=("visual.interact", "artifact.capture"),
        provider_availability=("obligation_verifier",),
    )
    second = runtime.profile_once(
        mission_key="run-1:mission-1",
        mission_id="mission-1",
        goal_revision=0,
        profiler_input=_input("different text must not trigger a second call"),
    )
    assert first == second
    assert first.status == "completed"
    assert first.profile_call_count == 1
    assert len(model.requests) == 1
    assert TaskProfilerShadowRecord.from_dict(first.to_dict()) == first


def test_profiler_failure_is_safe_default_and_not_retried() -> None:
    model = StubProfilerModel(error=TimeoutError("provider timed out"))
    runtime = TaskProfilerShadowRuntime(TaskProfiler(model), ExecutionPolicy())
    first = runtime.profile_once(
        mission_key="mission-1",
        mission_id="mission-1",
        goal_revision=0,
        profiler_input=_input(),
    )
    second = runtime.profile_once(
        mission_key="mission-1",
        mission_id="mission-1",
        goal_revision=0,
        profiler_input=_input(),
    )
    assert first == second
    assert first.status == "safe_default"
    assert first.profile is not None and first.profile.source == "safe_default"
    assert "profiler_shadow_unavailable" in first.profile.policy_reasons
    assert first.error_kind == "TimeoutError"
    assert len(model.requests) == 1


def test_concurrent_shadow_requests_still_schedule_one_model_call() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingModel(StubProfilerModel):
        def propose(self, request: TaskProfilerRequest):
            self.requests.append(request)
            entered.set()
            assert release.wait(2)
            return self.output

    model = BlockingModel()
    runtime = TaskProfilerShadowRuntime(TaskProfiler(model), ExecutionPolicy())
    completed: list[TaskProfilerShadowRecord] = []

    def invoke() -> None:
        completed.append(runtime.profile_once(
            mission_key="mission-1",
            mission_id="mission-1",
            goal_revision=0,
            profiler_input=_input(),
        ))

    thread = threading.Thread(target=invoke)
    thread.start()
    assert entered.wait(1)
    concurrent = runtime.profile_once(
        mission_key="mission-1",
        mission_id="mission-1",
        goal_revision=0,
        profiler_input=_input(),
    )
    assert concurrent.status == "in_progress"
    release.set()
    thread.join(2)
    assert len(model.requests) == 1
    assert completed[0].status == "completed"


def test_shadow_records_are_bounded_and_explicitly_cleanable() -> None:
    runtime = TaskProfilerShadowRuntime(
        TaskProfiler(StubProfilerModel()), ExecutionPolicy(), max_records=2
    )
    for index in range(3):
        runtime.profile_once(
            mission_key=f"mission-{index}",
            mission_id=f"mission-{index}",
            goal_revision=0,
            profiler_input=_input(),
        )
    assert len(runtime) == 2
    assert runtime.get("mission-0") is None
    runtime.close("mission-1")
    assert len(runtime) == 1
    runtime.clear()
    assert len(runtime) == 0
