from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest


class _CompletionsClient:
    def __init__(self, create):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=create),
        )


def _runner(task: str = "Inspect the current window"):
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(task)
    runner.messages = [{"role": "user", "content": task}]
    return runner


def _install_elapsed_recorder(runner, monkeypatch, *, elapsed_ms: int = 25):
    calls: list[float] = []

    def accumulate(started: float) -> int:
        calls.append(started)
        runner.model_elapsed_ms += elapsed_ms
        return elapsed_ms

    monkeypatch.setattr(runner, "_accumulate_model_elapsed", accumulate)
    return calls


def test_model_elapsed_accumulator_uses_monotonic_delta(monkeypatch):
    import winpilot.agent.loop as loop

    runner = _runner()
    try:
        monkeypatch.setattr(loop.time, "monotonic", lambda: 10.125)

        assert runner._accumulate_model_elapsed(10.0) == 125
        assert runner.model_elapsed_ms == 125
    finally:
        runner.session.close()


def test_chat_accumulates_successful_model_wait_once(monkeypatch):
    runner = _runner()
    try:
        response = object()
        elapsed_calls = _install_elapsed_recorder(runner, monkeypatch)

        def create(**_kwargs):
            return response

        assert runner._chat(_CompletionsClient(create), []) is response
        assert runner.model_request_count == 1
        assert runner.model_elapsed_ms == 25
        assert len(elapsed_calls) == 1
    finally:
        runner.session.close()


def test_chat_accumulates_failed_model_wait(monkeypatch):
    import winpilot.agent.loop as loop

    monkeypatch.setattr(loop, "_API_RETRIES", 1)
    runner = _runner()
    try:
        elapsed_calls = _install_elapsed_recorder(runner, monkeypatch)

        def create(**_kwargs):
            raise OSError("provider unavailable")

        with pytest.raises(RuntimeError, match="provider unavailable"):
            runner._chat(_CompletionsClient(create), [])
        assert runner.model_request_count == 1
        assert runner.model_elapsed_ms == 25
        assert len(elapsed_calls) == 1
    finally:
        runner.session.close()


def test_chat_accumulates_only_until_canceled_call_is_abandoned(monkeypatch):
    stop_event = threading.Event()
    release_provider = threading.Event()
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("Inspect the current window", stop_event=stop_event)
    runner.messages = [{"role": "user", "content": "Inspect the current window"}]
    try:
        elapsed_calls = _install_elapsed_recorder(runner, monkeypatch)

        def create(**_kwargs):
            stop_event.set()
            release_provider.wait(timeout=1)
            return object()

        assert runner._chat(_CompletionsClient(create), []) is None

        assert runner.model_request_count == 1
        assert runner.model_elapsed_ms == 25
        assert len(elapsed_calls) == 1
    finally:
        release_provider.set()
        runner.session.close()


def test_semantic_stage_metrics_subtract_model_time(monkeypatch):
    import winpilot.tasking.stage_runtime as stage_runtime

    runner = SimpleNamespace(model_elapsed_ms=120)
    monkeypatch.setattr(stage_runtime.time, "monotonic", lambda: 10.5)

    assert stage_runtime.StageRuntime._semantic_stage_metrics(
        10.0,
        runner,
    ) == {
        "model_elapsed_ms": 120,
        "controller_elapsed_ms": 380,
    }


def _semantic_stage_context(task: str):
    from winpilot.tasking.compiler import TaskContractCompiler

    contract = TaskContractCompiler().compile(task)
    stage = contract.stages[0]
    context = {
        "run_id": "run-model-timing",
        "runtime_state": {},
        "graph": {
            "metadata": {
                "task_contract": contract.model_dump(mode="json"),
            },
        },
    }
    return contract, stage, context


class _FakeSemanticRunner:
    def __init__(self, *, verified: bool, model_elapsed_ms: int):
        self.run_id = "child-model-timing"
        self.status = "done" if verified else "fail"
        self.result_text = "" if verified else "semantic stage rejected"
        self.usage_total = {}
        self.model_request_count = 2
        self.model_elapsed_ms = model_elapsed_ms
        self._done_verified = verified
        self._tool_history = []

    def bind_target_window(self, _hwnd, _app):
        return None

    def run(self):
        return {"status": self.status}


def test_create_stage_success_outputs_split_model_and_controller_time(monkeypatch):
    import winpilot.tasking.stage_runtime as stage_runtime

    contract, stage, context = _semantic_stage_context(
        "Organize the currently visible content",
    )
    fake = _FakeSemanticRunner(verified=True, model_elapsed_ms=12)
    monkeypatch.setattr(stage_runtime, "build_runner", lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(stage_runtime.time, "monotonic", lambda: 10.125)

    result = stage_runtime.StageRuntime()._execute(
        stage,
        contract,
        context,
        threading.Event(),
        10.0,
    )

    assert result.ok is True
    assert result.output["model_elapsed_ms"] == 12
    assert result.output["controller_elapsed_ms"] == 113
    assert result.output["model_calls"] == 2


def test_create_stage_failure_output_keeps_model_and_controller_time(monkeypatch):
    import winpilot.tasking.stage_runtime as stage_runtime
    from winpilot.orchestrator.contracts import ArpNode, ArpNodeKind

    contract, stage, context = _semantic_stage_context(
        "Organize the currently visible content",
    )
    fake = _FakeSemanticRunner(verified=False, model_elapsed_ms=12)
    monkeypatch.setattr(stage_runtime, "build_runner", lambda *_args, **_kwargs: fake)
    first_clock_read = True

    def monotonic():
        nonlocal first_clock_read
        if first_clock_read:
            first_clock_read = False
            return 10.0
        return 10.125

    monkeypatch.setattr(stage_runtime.time, "monotonic", monotonic)
    node = ArpNode(
        id=stage.id,
        kind=ArpNodeKind.TASK_STAGE,
        config={"stage": stage.model_dump(mode="json")},
    )

    result = stage_runtime.StageRuntime().execute(
        node,
        context,
        threading.Event(),
    )

    assert result.ok is False
    assert result.output["status"] == "failed"
    assert result.output["model_elapsed_ms"] == 12
    assert result.output["controller_elapsed_ms"] == 113
    assert result.output["model_calls"] == 2


def test_semantic_stage_failure_before_model_still_outputs_zero_model_time(monkeypatch):
    import winpilot.tasking.stage_runtime as stage_runtime
    from winpilot.orchestrator.contracts import ArpNode, ArpNodeKind

    contract, stage, context = _semantic_stage_context(
        "Organize the currently visible content",
    )

    def fail_to_build(*_args, **_kwargs):
        raise RuntimeError("runner construction failed")

    monkeypatch.setattr(stage_runtime, "build_runner", fail_to_build)
    first_clock_read = True

    def monotonic():
        nonlocal first_clock_read
        if first_clock_read:
            first_clock_read = False
            return 10.0
        return 10.125

    monkeypatch.setattr(stage_runtime.time, "monotonic", monotonic)
    node = ArpNode(
        id=stage.id,
        kind=ArpNodeKind.TASK_STAGE,
        config={"stage": stage.model_dump(mode="json")},
    )

    result = stage_runtime.StageRuntime().execute(
        node,
        context,
        threading.Event(),
    )

    assert result.ok is False
    assert result.output["model_elapsed_ms"] == 0
    assert result.output["controller_elapsed_ms"] == 125
