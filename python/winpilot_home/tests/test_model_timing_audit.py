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


def test_inline_semantic_vlm_wait_is_accumulated_on_success_and_error(monkeypatch):
    import json

    from winpilot.agent.runtime_profile import RuntimeProfile
    from winpilot.agent.tool_result import ToolResult
    from winpilot.agent.loop import AgentRunner

    runner = AgentRunner(
        "Create the requested visible shape",
        runtime_profile=RuntimeProfile(
            inline_semantic_verification=True,
            stage_type="DrawArtifact",
        ),
    )
    payload = {
        "ok": True,
        "evidence_packet": {
            "scene_structure": {"executed_draw_count": 1},
        },
    }
    try:
        def verified(_name, _args):
            time.sleep(0.02)
            return ToolResult(json.dumps({
                "ok": True,
                "verified": True,
                "semantic_visual_verified": True,
            }))

        monkeypatch.setattr(runner.session, "dispatch", verified)
        verdict = runner._inline_stage_semantic_verdict(
            "execute_subgoal",
            payload,
        )
        assert verdict is not None
        first_elapsed = runner.model_elapsed_ms
        assert verdict["elapsed_ms"] == first_elapsed
        assert first_elapsed >= 10

        def failed(_name, _args):
            time.sleep(0.02)
            raise RuntimeError("semantic provider failed")

        monkeypatch.setattr(runner.session, "dispatch", failed)
        with pytest.raises(RuntimeError, match="semantic provider failed"):
            runner._inline_stage_semantic_verdict("execute_subgoal", payload)

        assert runner.model_request_count == 2
        assert runner.model_elapsed_ms >= first_elapsed + 10
    finally:
        runner.session.close()


def test_semantic_stage_metrics_subtract_model_and_boundary_time(monkeypatch):
    import winpilot.tasking.stage_runtime as stage_runtime

    runner = SimpleNamespace(model_elapsed_ms=120)
    monkeypatch.setattr(stage_runtime.time, "monotonic", lambda: 10.5)

    assert stage_runtime.StageRuntime._semantic_stage_metrics(
        10.0,
        runner,
        boundary_elapsed_ms=30,
    ) == {
        "model_elapsed_ms": 150,
        "controller_elapsed_ms": 350,
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
        self._inline_stage_semantic_passed = False
        self._inline_stage_semantic_attempted = False
        self._inline_stage_semantic_last_verdict = {}
        self._tool_history = []
        self.session = SimpleNamespace(_last_canvas_region=None)

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


def test_draw_stage_adds_independent_boundary_verifier_to_model_time(monkeypatch):
    import json

    import winpilot.tasking.stage_runtime as stage_runtime
    from winpilot.agent.tool_result import ToolResult

    contract, stage, context = _semantic_stage_context(
        "Draw a generic shape in Paint",
    )
    fake = _FakeSemanticRunner(verified=False, model_elapsed_ms=12)
    fake._tool_history = [
        ("draw_strokes", {"strokes": []}, json.dumps({"ok": True})),
    ]

    class OpenSession:
        def __init__(self, **_kwargs):
            self.run_id = ""

        def dispatch(self, name, _args):
            assert name == "open_app"
            return ToolResult(json.dumps({"ok": True}))

        def close(self):
            return None

    runtime = stage_runtime.StageRuntime()
    monkeypatch.setattr(stage_runtime, "ToolSession", OpenSession)
    monkeypatch.setattr(stage_runtime, "build_runner", lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(runtime, "_resolve_hwnd", lambda _app: 42)
    monkeypatch.setattr(
        runtime,
        "_verify_draw_boundary",
        lambda **_kwargs: {
            "ok": True,
            "verified": True,
            "semantic_visual_verified": True,
            "elapsed_ms": 31,
        },
    )

    result = runtime._execute(
        stage,
        contract,
        context,
        threading.Event(),
        time.monotonic(),
    )

    assert result.ok is True
    assert result.output["model_elapsed_ms"] == 43
    assert result.output["controller_elapsed_ms"] >= 0
    assert result.output["model_calls"] == 3
    assert result.output["boundary_verification"]["elapsed_ms"] == 31
