import threading
from types import SimpleNamespace

from winpilot.agent import done_verifier, planner
from winpilot.agent.loop import AgentRunner
from winpilot.modeling import ModelCallError


def _response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def test_planner_uses_single_attempt_central_model_boundary(monkeypatch):
    captured = {}
    stopped = threading.Event()

    def invoke(client, **kwargs):
        captured["client"] = client
        captured.update(kwargs)
        return SimpleNamespace(response=_response('[{"goal":"inspect current state"}]'))

    monkeypatch.setattr(planner, "invoke_chat_completion", invoke)
    client = object()

    result = planner.make_plan(
        client,
        "planner-model",
        "inspect the current state",
        stop_event=stopped,
        decision_id="planner-decision-1",
    )

    assert [step["goal"] for step in result] == ["inspect current state"]
    assert captured["client"] is client
    assert captured["decision_id"] == "planner-decision-1"
    assert captured["model_role"] == "legacy_planner"
    assert captured["purpose"] == "upfront_plan_generation"
    assert captured["policy"].max_attempts == 1
    assert captured["stop_event"] is stopped
    assert captured["provider_request"]["model"] == "planner-model"


def test_planner_model_call_error_keeps_legacy_empty_plan_fallback(monkeypatch):
    monkeypatch.setattr(
        planner,
        "invoke_chat_completion",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ModelCallError("provider unavailable", ())
        ),
    )

    assert planner.make_plan(object(), "planner-model", "inspect") == []


def test_done_verifier_uses_single_attempt_boundary_and_fails_closed(monkeypatch):
    captured = {}
    stopped = threading.Event()
    runner = AgentRunner("inspect the current window", stop_event=stopped)

    def invoke(client, **kwargs):
        captured["client"] = client
        captured.update(kwargs)
        raise ModelCallError("provider unavailable", ())

    monkeypatch.setattr(done_verifier, "invoke_chat_completion", invoke)
    client = object()

    assert runner._call_done_judge(client, "window", "observe", "evidence") is False
    assert captured["client"] is client
    assert captured["model_role"] == "legacy_done_verifier"
    assert captured["purpose"] == "completion_verification"
    assert captured["policy"].max_attempts == 1
    assert captured["stop_event"] is stopped
    assert captured["decision_id"].startswith(f"{runner.run_id}:done_judge:")
    assert "remains unverified" in runner._last_done_reject_reason
