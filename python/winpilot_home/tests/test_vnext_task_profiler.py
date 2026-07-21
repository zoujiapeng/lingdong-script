"""P2b read-only, at-most-once Task Profiler shadow tests."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

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
    OpenAITaskProfilerModel,
    LegacyTaskProfilerShadowHook,
    profiler_input_from_legacy_contract,
)
from winpilot.modeling import ModelCallRuntime, ModelRetryPolicy


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
        self.stop_events: list[threading.Event | None] = []

    def propose(
        self,
        request: TaskProfilerRequest,
        *,
        stop_event: threading.Event | None = None,
    ):
        self.requests.append(request)
        self.stop_events.append(stop_event)
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
        def propose(
            self,
            request: TaskProfilerRequest,
            *,
            stop_event: threading.Event | None = None,
        ):
            self.requests.append(request)
            self.stop_events.append(stop_event)
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


class _ChatClient:
    def __init__(self, create) -> None:
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=create),
        )


def _provider_response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(payload, ensure_ascii=False),
        ))],
    )


def test_real_profiler_provider_is_tool_free_and_identity_free() -> None:
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return _provider_response(_proposal().to_dict())

    provider = OpenAITaskProfilerModel(
        _ChatClient(create),
        model="read-only-profiler",
        decision_id_factory=lambda: "runtime-bound-decision",
    )
    proposal = TaskProfiler(provider).profile(_input())

    assert proposal == _proposal()
    assert len(calls) == 1
    assert "tools" not in calls[0]
    assert "runtime-bound-decision" not in json.dumps(
        calls[0], ensure_ascii=False, sort_keys=True
    )
    model_input = json.loads(calls[0]["messages"][1]["content"])

    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value)) if value else set()
        return set()

    input_keys = keys(model_input)
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
        assert forbidden not in input_keys


def test_profiler_transport_retry_preserves_exact_request() -> None:
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TimeoutError("temporary provider timeout")
        return _provider_response(_proposal().to_dict())

    provider = OpenAITaskProfilerModel(
        _ChatClient(create),
        model="read-only-profiler",
        retry_policy=ModelRetryPolicy(max_attempts=2, initial_backoff_s=0),
        call_runtime=ModelCallRuntime(sleeper=lambda _delay: None),
        decision_id_factory=lambda: "one-semantic-profile",
    )
    runtime = TaskProfilerShadowRuntime(
        TaskProfiler(provider), ExecutionPolicy()
    )
    first = runtime.profile_once(
        mission_key="run-1:mission-1",
        mission_id="mission-1",
        goal_revision=0,
        profiler_input=_input(),
    )
    second = runtime.profile_once(
        mission_key="run-1:mission-1",
        mission_id="mission-1",
        goal_revision=0,
        profiler_input=_input(),
    )

    assert first.status == "completed"
    assert second == first
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert provider.last_outcome is not None
    assert provider.last_outcome.request_retry_count == 1


def test_profiler_abandons_response_when_run_is_stopped_during_transport() -> None:
    stopped = threading.Event()
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        stopped.set()
        return _provider_response(_proposal().to_dict())

    runtime = TaskProfilerShadowRuntime(
        TaskProfiler(OpenAITaskProfilerModel(
            _ChatClient(create),
            model="read-only-profiler",
        )),
        ExecutionPolicy(),
    )
    record = runtime.profile_once(
        mission_key="run-1:mission-1",
        mission_id="mission-1",
        goal_revision=0,
        profiler_input=_input(),
        stop_event=stopped,
    )

    assert len(calls) == 1
    assert record.status == "safe_default"
    assert record.error_kind == "ModelCallError"


@pytest.mark.parametrize("raw", ("not-json", "[]", "{} trailing"))
def test_profiler_provider_rejects_malformed_output(raw) -> None:
    client = _ChatClient(lambda **_kwargs: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=raw))],
    ))
    with pytest.raises(InvalidProfileSchema):
        TaskProfiler(OpenAITaskProfilerModel(
            client,
            model="read-only-profiler",
        )).profile(_input())


class _ShadowTrace:
    def __init__(self) -> None:
        self.events = []

    def record_sidecar(self, event, *, run_id):
        self.events.append({**event, "run_id": run_id})
        return True


def _legacy_contract(task: str = "Complete the currently visible task") -> dict:
    return {
        "format": "task-contract/1",
        "task_id": "legacy-task",
        "task": task,
        "experience_policy": "deny",
        "stages": [],
        "obligations": [],
    }


def test_legacy_shadow_hook_calls_profiler_at_most_once_per_run() -> None:
    model = StubProfilerModel()
    trace = _ShadowTrace()
    hook = LegacyTaskProfilerShadowHook(model)
    stopped = threading.Event()

    first = hook.profile_run(
        run_id="outer-run",
        contract=_legacy_contract(),
        trace=trace,
        stop_event=stopped,
    )
    second = hook.profile_run(
        run_id="outer-run",
        contract=_legacy_contract("changed text must not trigger another call"),
        trace=trace,
    )

    assert first == second
    assert len(model.requests) == 1
    assert model.stop_events == [stopped]
    assert len(trace.events) == 2
    assert all(event["profile_call_count"] == 1 for event in trace.events)
    assert model.requests[0].input.current_surface_summary is None
    serialized = json.dumps(model.requests[0].model_payload(), ensure_ascii=False)
    assert "outer-run" not in serialized
    assert "mission-outer-run" not in serialized


def test_legacy_bridge_filters_unknown_capability_without_teaching_profiler() -> None:
    contract = _legacy_contract()
    contract["obligations"] = [{
        "id": "o1",
        "description": "A result exists",
        "acceptance_criteria": ["result is visible"],
        "required_capabilities": ["visual.interact", "legacy.unknown"],
        "required": True,
    }]
    definition, profiler_input = profiler_input_from_legacy_contract(
        contract,
        mission_id="m1",
        run_id="r1",
    )
    assert definition.metadata["legacy_unknown_capabilities"] == ("legacy.unknown",)
    assert profiler_input.initial_obligations[0].required_capabilities == (
        "visual.interact",
    )
