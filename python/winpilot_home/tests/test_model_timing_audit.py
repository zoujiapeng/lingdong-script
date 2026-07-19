from __future__ import annotations

import threading
import time
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


def test_chat_accumulates_successful_model_wait_once():
    runner = _runner()
    try:
        response = object()

        def create(**_kwargs):
            time.sleep(0.025)
            return response

        assert runner._chat(_CompletionsClient(create), []) is response
        assert runner.model_request_count == 1
        assert 15 <= runner.model_elapsed_ms < 500
    finally:
        runner.session.close()


def test_chat_accumulates_failed_model_wait(monkeypatch):
    import winpilot.agent.loop as loop

    monkeypatch.setattr(loop, "_API_RETRIES", 1)
    runner = _runner()
    try:
        def create(**_kwargs):
            time.sleep(0.025)
            raise OSError("provider unavailable")

        with pytest.raises(RuntimeError, match="provider unavailable"):
            runner._chat(_CompletionsClient(create), [])
        assert runner.model_request_count == 1
        assert 15 <= runner.model_elapsed_ms < 500
    finally:
        runner.session.close()


def test_chat_accumulates_only_until_canceled_call_is_abandoned():
    stop_event = threading.Event()
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner("Inspect the current window", stop_event=stop_event)
    runner.messages = [{"role": "user", "content": "Inspect the current window"}]
    try:
        def create(**_kwargs):
            time.sleep(0.6)
            return object()

        timer = threading.Timer(0.03, stop_event.set)
        timer.start()
        started = time.monotonic()
        assert runner._chat(_CompletionsClient(create), []) is None
        wall_ms = int((time.monotonic() - started) * 1000)
        timer.join(timeout=1)

        assert runner.model_request_count == 1
        assert runner.model_elapsed_ms >= 20
        assert runner.model_elapsed_ms <= wall_ms + 20
        assert runner.model_elapsed_ms < 500
    finally:
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
        time.sleep(0.03)
        return {"status": self.status}


def test_create_stage_success_outputs_split_model_and_controller_time(monkeypatch):
    import winpilot.tasking.stage_runtime as stage_runtime

    contract, stage, context = _semantic_stage_context(
        "Organize the currently visible content",
    )
    fake = _FakeSemanticRunner(verified=True, model_elapsed_ms=12)
    monkeypatch.setattr(stage_runtime, "build_runner", lambda *_args, **_kwargs: fake)

    started = time.monotonic()
    result = stage_runtime.StageRuntime()._execute(
        stage,
        contract,
        context,
        threading.Event(),
        started,
    )

    assert result.ok is True
    assert result.output["model_elapsed_ms"] == 12
    assert result.output["controller_elapsed_ms"] >= 10
    assert result.output["model_calls"] == 2


def test_create_stage_failure_output_keeps_model_and_controller_time(monkeypatch):
    import winpilot.tasking.stage_runtime as stage_runtime
    from winpilot.orchestrator.contracts import ArpNode, ArpNodeKind

    contract, stage, context = _semantic_stage_context(
        "Organize the currently visible content",
    )
    fake = _FakeSemanticRunner(verified=False, model_elapsed_ms=12)
    monkeypatch.setattr(stage_runtime, "build_runner", lambda *_args, **_kwargs: fake)
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
    assert result.output["controller_elapsed_ms"] >= 10
    assert result.output["model_calls"] == 2


def test_semantic_stage_failure_before_model_still_outputs_zero_model_time(monkeypatch):
    import winpilot.tasking.stage_runtime as stage_runtime
    from winpilot.orchestrator.contracts import ArpNode, ArpNodeKind

    contract, stage, context = _semantic_stage_context(
        "Organize the currently visible content",
    )

    def fail_to_build(*_args, **_kwargs):
        time.sleep(0.015)
        raise RuntimeError("runner construction failed")

    monkeypatch.setattr(stage_runtime, "build_runner", fail_to_build)
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
    assert result.output["controller_elapsed_ms"] >= 10
