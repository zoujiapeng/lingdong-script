import io
import json
import os
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from engine import engine as engine_module
from engine.executor import DSLExecutor
from engine.executor_types import result_indicates_failure
from engine.healing import HealingPipeline


def _run(source, handler):
    executor = DSLExecutor(action_handler=handler)
    executor.set_dialog_auto(False)
    return executor.run_dsl_sync(source)


@pytest.mark.parametrize(
    ("source", "failure_result"),
    [
        ("CLICK submit", {"success": False, "error": "target not found"}),
        ("TYPE hello", {"ok": False, "error": "input rejected"}),
        (
            "CLICK submit",
            {
                "success": True,
                "result": {"success": True, "result": False},
            },
        ),
    ],
)
def test_explicit_failure_results_fail_non_idempotent_action_once(source, failure_result):
    calls = []

    def handler(action, **kwargs):
        calls.append((action, kwargs))
        return failure_result

    result = _run(source, handler)

    assert result["success"] is False
    assert len(calls) == 1
    assert "未自动重试" in result["error"]


def test_nested_registry_failure_result_is_rejected():
    assert result_indicates_failure({
        "success": True,
        "result": {"success": False, "error": "feature failed"},
    }) is True


@pytest.mark.parametrize("nested", [False, None])
def test_nested_explicit_negative_result_is_rejected(nested):
    assert result_indicates_failure({
        "success": True,
        "result": nested,
    }) is True


def test_double_wrapped_registry_negative_result_is_rejected():
    assert result_indicates_failure({
        "success": True,
        "result": {
            "success": True,
            "result": False,
        },
    }) is True


@pytest.mark.parametrize(
    "failure_result",
    [
        None,
        {"error": "target unavailable"},
        {"found": False, "error": "not found"},
        {"valid": False, "error": "invalid target"},
        {"verified": False},
    ],
)
def test_ambiguous_or_error_results_fail_closed(failure_result):
    assert result_indicates_failure(failure_result) is True


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("success", None),
        ("success", "true"),
        ("ok", 1),
        ("found", "yes"),
        ("valid", None),
        ("verified", 1),
        ("passed", "false"),
    ],
)
def test_explicit_status_envelopes_require_literal_true(key, value):
    assert result_indicates_failure({key: value}) is True


@pytest.mark.parametrize(
    "key",
    ["success", "ok", "found", "valid", "verified", "passed"],
)
def test_explicit_status_envelopes_accept_literal_true(key):
    assert result_indicates_failure({key: True}) is False


def test_condition_negative_allows_only_well_formed_success_wrappers():
    assert DSLExecutor._is_clean_negative_result({
        "success": True,
        "result": {"ok": True, "result": False},
    }) is True
    assert DSLExecutor._is_clean_negative_result({
        "success": "true",
        "result": False,
    }) is False
    assert DSLExecutor._is_clean_negative_result({
        "ok": 1,
        "result": False,
    }) is False


def test_none_result_fails_action_instead_of_continuing():
    result = _run("CLICK submit", lambda *_args, **_kwargs: None)

    assert result["success"] is False


@pytest.mark.parametrize("condition", ["text:missing", "screen_change:0.01"])
def test_false_condition_takes_else_branch_without_retry(condition):
    calls = []

    def handler(action, **kwargs):
        calls.append((action, kwargs))
        if action in {"check_condition", "wait_screen_change"}:
            return False
        return True

    result = _run(
        f"IF {condition}\n  CLICK wrong\nELSE\n  CLICK expected\nEND",
        handler,
    )

    predicate = (
        "check_condition" if condition.startswith("text:")
        else "wait_screen_change"
    )
    assert result["success"] is True
    assert [action for action, _kwargs in calls].count(predicate) == 1
    assert calls[-1][0] == "click"
    assert calls[-1][1]["target"] == "expected"


def test_allow_negative_result_does_not_hide_explicit_error(monkeypatch):
    executor = DSLExecutor(
        action_handler=lambda *_args, **_kwargs: {
            "ok": False,
            "error": "predicate backend failed",
        }
    )
    executor.set_dialog_auto(False)
    monkeypatch.setattr(
        executor,
        "_heal_action",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("healing should not convert the error into False")
        ),
    )

    with pytest.raises(RuntimeError, match="healing should not convert"):
        executor._call_action(
            "check_condition",
            condition="text:anything",
            retry_limit=1,
            allow_negative_result=True,
        )


def test_unknown_registry_keyword_fails_closed():
    result = _run("NO_SUCH_ACTION value", lambda *args, **kwargs: True)

    assert result["success"] is False
    assert "未知指令" in result["error"]


def test_parallel_branch_failure_propagates_to_flow_result():
    calls = []

    def handler(action, **kwargs):
        calls.append(kwargs["target"])
        if kwargs["target"] == "bad":
            return {"success": False, "error": "bad branch"}
        return {"success": True}

    source = """\
PARALLEL
WITH
  CLICK good
END
WITH
  CLICK bad
END
END
"""
    result = _run(source, handler)

    assert result["success"] is False
    assert "PARALLEL 分支失败" in result["error"]
    assert sorted(calls) == ["bad", "good"]


def test_healing_replays_real_safe_action_and_rejects_failure_dict(monkeypatch):
    pipeline = HealingPipeline()
    monkeypatch.setattr(pipeline, "_strategy_escalate", lambda context, retry: None)
    calls = []

    def retry_action(kwargs):
        calls.append(kwargs)
        return {"ok": False, "error": "still unavailable"}

    result = pipeline.heal(
        "ocr_find",
        "initial failure",
        {
            "retry_safe": True,
            "action_kwargs": {"query": "Save"},
            "action": "ocr_find",
        },
        retry_action,
    )

    assert result.success is False
    assert calls == [{"query": "Save"}]
    assert result.details[0]["strategy"] == "retry"
    assert result.details[0]["success"] is False
    assert result.details[0]["error"] == "still unavailable"


def test_offset_spray_is_unavailable_for_non_idempotent_action():
    action_calls = []
    pipeline = HealingPipeline(
        action_handler=lambda *args, **kwargs: action_calls.append((args, kwargs))
    )
    context = {
        "action": "click",
        "target": "Submit",
        "retry_safe": False,
        "allow_offset_spray": True,
    }

    names = [name for name, _ in pipeline._build_strategy_chain("click", context)]
    result = pipeline._strategy_offset(context)

    assert names == ["escalate"]
    assert result is None
    assert action_calls == []


@pytest.mark.parametrize(
    "stdin_payload",
    [
        "CLICK bad\n",
        json.dumps({"dsl": "CLICK bad"}) + "\n",
    ],
)
def test_engine_main_reports_sync_failure_and_does_not_crystallize(
    monkeypatch, stdin_payload
):
    events = []
    crystallized = []

    class FailingExecutor:
        def __init__(self, action_handler=None):
            pass

        def on(self, event, callback):
            pass

        def run_dsl_sync(self, source):
            return {"success": False, "error": "branch failed"}

    monkeypatch.setattr(engine_module, "DSLExecutor", FailingExecutor)
    monkeypatch.setattr(engine_module, "emit", events.append)
    monkeypatch.setattr(engine_module, "crystallize", lambda *args: crystallized.append(args))
    monkeypatch.setattr(engine_module.sys, "stdin", io.StringIO(stdin_payload))

    engine_module.main()

    done = [event for event in events if event.get("type") == "execution_done"]
    assert done == [{"type": "execution_done", "status": "error", "error": "branch failed"}]
    assert crystallized == []
