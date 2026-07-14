from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


def _tool_call(call_id: str, name: str, args: dict):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _runner(monkeypatch, task: str = "edit the current document", responder=None):
    from winpilot.agent.loop import AgentRunner
    from winpilot.agent.tool_result import ToolResult

    runner = AgentRunner(task)
    runner.messages = [{"role": "system", "content": "system"}]
    runner.session.hwnd = 41
    runner._visual_closed_loop_active = False
    runner._trace_debug = lambda *_args, **_kwargs: None
    calls: list[tuple[str, dict]] = []

    def dispatch(name: str, args: dict):
        calls.append((name, args))
        runner.session._screen_epoch += 1
        payload = responder(name, args) if responder else {"ok": True, "verified": True}
        return ToolResult(payload if isinstance(payload, str) else json.dumps(payload))

    monkeypatch.setattr(runner, "_dispatch_with_fast_visual_wrap", dispatch)
    monkeypatch.setattr(
        runner._failure_recovery_layer,
        "handle_tool_result",
        lambda **_kwargs: None,
    )
    return runner, calls


def _run(runner, call_id: str, name: str, args: dict) -> dict:
    runner._handle_tool_calls([_tool_call(call_id, name, args)])
    tool_messages = [item for item in runner.messages if item.get("role") == "tool"]
    return json.loads(tool_messages[-1]["content"])


def _seed_output(runner) -> None:
    result = _run(runner, "seed", "type_text", {"text": "retained content"})
    assert result["ok"] is True
    assert runner._mutation_ledger


def _confirm_visual_change(runner, action: str, frame_id: int, ratio: float = 0.02) -> None:
    epoch = runner.session._screen_epoch
    runner._partial_output_guard.record_post_action(
        action,
        hwnd=runner.session.hwnd,
        screen_epoch=epoch,
        frame_id=frame_id,
        comparable=True,
        change_ratio=ratio,
        threshold=0.0005,
    )
    runner._visual_frame_id = frame_id
    runner._visual_frame_epoch = epoch
    runner._visual_frame_hwnd = runner.session.hwnd


def test_successful_mutation_is_recorded_but_ok_only_click_is_not(monkeypatch):
    runner, _calls = _runner(monkeypatch)
    _seed_output(runner)
    assert runner._mutation_ledger[-1]["tool"] == "type_text"

    click_runner, _calls = _runner(
        monkeypatch,
        responder=lambda _name, _args: {"ok": True, "verified": False},
    )
    _run(click_runner, "click", "click", {"x": 20, "y": 30})
    assert click_runner._mutation_ledger == []

    changed_click_runner, _calls = _runner(
        monkeypatch,
        responder=lambda _name, _args: {
            "ok": True,
            "verified": True,
            "visual_change_detected": True,
        },
    )
    _run(changed_click_runner, "changed-click", "click", {"x": 20, "y": 30})
    assert changed_click_runner._mutation_ledger == []


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("hotkey", {"keys": ["ctrl", "n"]}),
        ("click", {"label": "New", "context": "new blank document"}),
        ("click", {"label": "Clear", "context": "clear the whole canvas"}),
        ("click", {"label": "Clear all"}),
    ],
)
def test_reset_and_clear_actions_are_blocked_before_dispatch(
    monkeypatch, tool_name, args,
):
    runner, calls = _runner(monkeypatch)
    _seed_output(runner)
    before = len(calls)

    result = _run(runner, "destructive", tool_name, args)

    assert len(calls) == before
    assert result["partial_output_destructive_guard"] is True
    assert result["mutation_applied"] is False


@pytest.mark.parametrize(
    "args",
    [
        {"label": "Clear"},
        {"label": "Clear", "context": "search field"},
        {"label": "New", "context": "new record"},
        {"label": "清空", "context": "筛选输入框"},
        {"label": "新建", "context": "新建记录"},
    ],
)
def test_ambiguous_local_clear_and_new_controls_are_allowed(monkeypatch, args):
    runner, calls = _runner(monkeypatch)
    _seed_output(runner)
    before = len(calls)

    result = _run(runner, "local-control", "click", args)

    assert result["ok"] is True
    assert len(calls) == before + 1
    assert calls[-1] == ("click", args)


@pytest.mark.parametrize("task", ["Reset the filter", "清空搜索框"])
def test_local_reset_wording_does_not_authorize_whole_surface_clear(monkeypatch, task):
    runner, calls = _runner(monkeypatch, task=task)
    _seed_output(runner)
    before = len(calls)

    result = _run(runner, "whole-clear", "click", {"label": "Clear all"})

    assert len(calls) == before
    assert result["partial_output_destructive_guard"] is True
    assert result["user_reset_requested"] is False


def test_batch_and_cross_call_select_all_delete_are_blocked(monkeypatch):
    batch_runner, batch_calls = _runner(monkeypatch)
    _seed_output(batch_runner)
    before = len(batch_calls)
    batch = _run(batch_runner, "batch", "run_action_batch", {
        "actions": [
            {"action": "hotkey", "keys": ["ctrl", "a"]},
            {"action": "hotkey", "keys": ["delete"]},
        ],
    })
    assert len(batch_calls) == before
    assert batch["destructive_action"] == "select_all_delete"

    split_runner, split_calls = _runner(monkeypatch)
    _seed_output(split_runner)
    _run(split_runner, "select", "hotkey", {"keys": ["ctrl", "a"]})
    before = len(split_calls)
    delete = _run(split_runner, "delete", "hotkey", {"keys": ["delete"]})
    assert len(split_calls) == before
    assert delete["destructive_action"] == "select_all_delete"


@pytest.mark.parametrize(
    "replacement",
    [
        {"action": "type_text", "text": "replacement"},
        {
            "action": "type_into_element",
            "label": "Document body",
            "text": "replacement",
            "clear": False,
        },
        {"action": "clipboard_paste"},
        {"action": "hotkey", "keys": ["ctrl", "v"]},
    ],
)
def test_batch_select_all_replacement_inputs_are_blocked(monkeypatch, replacement):
    runner, calls = _runner(monkeypatch)
    _seed_output(runner)
    before = len(calls)

    result = _run(runner, "replace-batch", "run_action_batch", {
        "actions": [
            {"action": "hotkey", "keys": ["ctrl", "a"]},
            replacement,
        ],
    })

    assert len(calls) == before
    assert result["destructive_action"] == "select_all_replace"
    assert result["mutation_applied"] is False


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("type_text", {"text": "replacement"}),
        (
            "type_into_element",
            {"label": "Document body", "text": "replacement", "clear": False},
        ),
        ("clipboard_paste", {}),
        ("hotkey", {"keys": ["ctrl", "v"]}),
    ],
)
def test_cross_call_select_all_replacement_inputs_are_blocked(
    monkeypatch, tool_name, args,
):
    runner, calls = _runner(monkeypatch)
    _seed_output(runner)
    _run(runner, "select-for-replace", "hotkey", {"keys": ["ctrl", "a"]})
    before = len(calls)

    result = _run(runner, "replace-after-select", tool_name, args)

    assert len(calls) == before
    assert result["destructive_action"] == "select_all_replace"


def test_batch_shorthand_select_all_delete_is_blocked(monkeypatch):
    runner, calls = _runner(monkeypatch)
    _seed_output(runner)
    before = len(calls)

    result = _run(runner, "shorthand", "run_action_batch", {
        "actions": [
            {"hotkey": ["ctrl", "a"]},
            {"hotkey": ["delete"]},
        ],
    })

    assert len(calls) == before
    assert result["destructive_action"] == "select_all_delete"


def test_document_surface_clear_is_blocked_before_controller_expands_it(monkeypatch):
    runner, calls = _runner(monkeypatch)
    _seed_output(runner)
    before = len(calls)

    result = _run(runner, "document-clear", "execute_subgoal", {
        "subgoal": "replace the focused document body",
        "input": {
            "text": "replacement body",
            "document_surface": True,
            "clear": True,
        },
    })

    assert len(calls) == before
    assert result["destructive_action"] == "document_surface_clear"
    assert result["partial_output_destructive_guard"] is True


def test_document_input_auto_upgrade_is_rechecked_before_dispatch(monkeypatch):
    runner, calls = _runner(
        monkeypatch,
        task=(
            "Type exactly these lines into the document body:\n"
            "Release heading\n"
            "Keep this complete paragraph"
        ),
    )
    _seed_output(runner)
    before = len(calls)

    result = _run(runner, "upgraded-document-clear", "execute_subgoal", {
        "subgoal": "continue entering the requested body",
        "input": {
            "label": "document body",
            "text": "Keep this complete paragraph",
        },
    })

    assert len(calls) == before
    assert result["destructive_action"] == "document_surface_clear"
    assert result["protected_mutation"]["tool"] == "type_text"


def test_repeated_same_destructive_block_safe_stops_on_second_attempt(monkeypatch):
    runner, calls = _runner(monkeypatch)
    _seed_output(runner)
    before = len(calls)
    destructive = {"label": "Clear all"}

    first = _run(runner, "clear-1", "click", destructive)
    terminal = runner._handle_tool_calls([
        _tool_call("clear-2", "click", destructive),
    ])
    second = json.loads([
        item for item in runner.messages if item.get("role") == "tool"
    ][-1]["content"])

    assert len(calls) == before
    assert first["guard_blocks"] == 1
    assert first["safe_stop"] is False
    assert second["guard_blocks"] == 2
    assert second["safe_stop"] is True
    assert terminal == "reply"


def test_successful_intervening_strategy_resets_destructive_block_count(monkeypatch):
    runner, calls = _runner(monkeypatch)
    _seed_output(runner)
    destructive = {"label": "Clear all"}

    first = _run(runner, "clear-before-observe", "click", destructive)
    observed = _run(runner, "observe-strategy", "observe", {})
    terminal = runner._handle_tool_calls([
        _tool_call("clear-after-observe", "click", destructive),
    ])
    second = json.loads([
        item for item in runner.messages if item.get("role") == "tool"
    ][-1]["content"])

    assert first["guard_blocks"] == 1
    assert observed["ok"] is True
    assert calls[-1][0] == "observe"
    assert second["guard_blocks"] == 1
    assert second["safe_stop"] is False
    assert terminal is None


@pytest.mark.parametrize("strategy_tool", ["observe", "read_text", "list_windows"])
def test_plain_text_strategy_result_resets_destructive_block_count(
    monkeypatch, strategy_tool,
):
    runner, calls = _runner(
        monkeypatch,
        responder=lambda name, _args: (
            "current visible state" if name == strategy_tool
            else {"ok": True, "verified": True}
        ),
    )
    _seed_output(runner)
    destructive = {"label": "Clear all"}

    first = _run(runner, "clear-before-plain-strategy", "click", destructive)
    runner._handle_tool_calls([
        _tool_call("plain-strategy", strategy_tool, {}),
    ])
    second = _run(runner, "clear-after-plain-strategy", "click", destructive)

    assert first["guard_blocks"] == 1
    assert calls[-1][0] == strategy_tool
    assert second["guard_blocks"] == 1
    assert second["safe_stop"] is False


def test_turn_barrier_does_not_increment_hidden_destructive_block(monkeypatch):
    runner, calls = _runner(monkeypatch)
    _seed_output(runner)
    runner._visual_closed_loop_active = True
    monkeypatch.setattr("winpilot.agent.loop._main_model_has_vision", lambda: False)
    destructive = {"label": "Clear all"}

    first = _run(runner, "clear-before-turn", "click", destructive)
    runner._handle_tool_calls([
        _tool_call("strategy-click", "click", {"x": 20, "y": 30}),
        _tool_call("turn-deferred-clear", "click", destructive),
    ])
    deferred = json.loads([
        item for item in runner.messages if item.get("role") == "tool"
    ][-1]["content"])
    second = _run(runner, "clear-after-turn", "click", destructive)

    assert first["guard_blocks"] == 1
    assert deferred["visual_turn_barrier"] is True
    assert calls[-1] == ("click", {"x": 20, "y": 30})
    assert second["guard_blocks"] == 1
    assert second["safe_stop"] is False


def test_fallback_text_and_current_element_label_are_blocked(monkeypatch):
    fallback_runner, fallback_calls = _runner(monkeypatch)
    _seed_output(fallback_runner)
    before = len(fallback_calls)
    fallback = _run(fallback_runner, "fallback", "execute_subgoal", {
        "click": {"fallback_text": ["Clear all"]},
    })
    assert len(fallback_calls) == before
    assert fallback["destructive_action"] == "reset_or_clear"

    element_runner, element_calls = _runner(monkeypatch)
    _seed_output(element_runner)
    element_runner._partial_output_guard.record_result(
        "observe",
        {},
        '[9] Button "Clear all" center=(40,40)\n'
        '[10] Button "Bold" center=(80,40)',
        dispatched=True,
        terminal=None,
        hwnd=element_runner.session.hwnd,
        screen_epoch=element_runner.session._screen_epoch,
        sequence=element_runner._tool_sequence,
        frame_id=element_runner._visual_frame_id,
    )
    before = len(element_calls)
    blocked = _run(element_runner, "element-clear", "click", {"element_id": 9})
    assert len(element_calls) == before
    assert blocked["destructive_action"] == "reset_or_clear"

    allowed = _run(element_runner, "element-bold", "click", {"element_id": 10})
    assert allowed["ok"] is True
    assert element_calls[-1][1] == {"element_id": 10}


def test_standalone_delete_and_select_all_copy_are_allowed(monkeypatch):
    runner, calls = _runner(monkeypatch)
    _seed_output(runner)

    delete = _run(runner, "delete", "hotkey", {"keys": ["delete"]})
    copy = _run(runner, "copy", "run_action_batch", {
        "actions": [
            {"action": "hotkey", "keys": ["ctrl", "a"]},
            {"action": "hotkey", "keys": ["ctrl", "c"]},
        ],
    })

    assert delete["ok"] is True
    assert copy["ok"] is True
    assert [name for name, _args in calls][-2:] == ["hotkey", "run_action_batch"]


def test_one_fresh_same_window_local_undo_is_allowed_then_next_is_blocked(monkeypatch):
    runner, calls = _runner(monkeypatch)
    _seed_output(runner)
    epoch = runner.session._screen_epoch
    runner._partial_output_guard.record_post_action(
        "type_text",
        hwnd=runner.session.hwnd,
        screen_epoch=epoch,
        frame_id=7,
        comparable=True,
        change_ratio=0.02,
        threshold=0.0005,
    )
    runner._visual_frame_id = 7
    runner._visual_frame_epoch = epoch
    runner._visual_frame_hwnd = runner.session.hwnd

    first = _run(runner, "undo-1", "hotkey", {"keys": ["ctrl", "z"]})
    before = len(calls)
    second = _run(runner, "undo-2", "hotkey", {"keys": ["ctrl", "z"]})

    assert first["ok"] is True
    assert len(calls) == before
    assert second["destructive_action"] == "single_undo"


def test_visible_undo_then_immediate_second_reversal_is_blocked_as_no_net_progress(
    monkeypatch,
):
    runner, calls = _runner(monkeypatch)
    _seed_output(runner)
    _confirm_visual_change(runner, "type_text", 7)

    first_undo = _run(runner, "undo-initial", "hotkey", {"keys": ["ctrl", "z"]})
    assert first_undo["ok"] is True
    _confirm_visual_change(runner, "hotkey", 8)
    assert runner._mutation_ledger[-1]["invalidated_by"] == "single_undo"

    corrected = _run(runner, "corrected-output", "type_text", {"text": "replacement"})
    assert corrected["ok"] is True
    _confirm_visual_change(runner, "type_text", 9)
    before = len(calls)

    blocked = _run(runner, "undo-corrected", "hotkey", {"keys": ["ctrl", "z"]})
    assert len(calls) == before
    assert blocked["net_progress_reversal_guard"] is True
    assert blocked["destructive_action"] == "single_undo"
    assert blocked["reversal_debt"]["forward_mutations"] == 1
    assert blocked["mutation_applied"] is False

    repeated = _run(runner, "undo-corrected-again", "hotkey", {"keys": ["ctrl", "z"]})
    assert repeated["safe_stop"] is True
    assert repeated["guard_blocks"] == 2
    assert len(calls) == before


def test_two_visible_forward_mutations_repay_reversal_debt(monkeypatch):
    runner, calls = _runner(monkeypatch)
    _seed_output(runner)
    _confirm_visual_change(runner, "type_text", 20)
    assert _run(
        runner, "undo-once", "hotkey", {"keys": ["ctrl", "z"]},
    )["ok"] is True
    _confirm_visual_change(runner, "hotkey", 21)

    assert _run(
        runner, "forward-one", "type_text", {"text": "first correction"},
    )["ok"] is True
    _confirm_visual_change(runner, "type_text", 22)
    assert runner._partial_output_guard._reversal_debts[runner.session.hwnd][
        "forward_mutations"
    ] == 1

    assert _run(
        runner, "forward-two", "type_text", {"text": " durable addition"},
    )["ok"] is True
    _confirm_visual_change(runner, "type_text", 23)
    assert runner.session.hwnd not in runner._partial_output_guard._reversal_debts

    before = len(calls)
    allowed = _run(runner, "undo-after-net-progress", "hotkey", {"keys": ["ctrl", "z"]})
    assert allowed["ok"] is True
    assert len(calls) == before + 1


def test_fresh_explicit_user_undo_can_override_reversal_debt_once(monkeypatch):
    runner, calls = _runner(monkeypatch)
    _seed_output(runner)
    _confirm_visual_change(runner, "type_text", 30)
    assert _run(
        runner, "first-undo", "hotkey", {"keys": ["ctrl", "z"]},
    )["ok"] is True
    _confirm_visual_change(runner, "hotkey", 31)
    assert _run(
        runner, "first-rebuild", "type_text", {"text": "rebuilt"},
    )["ok"] is True
    _confirm_visual_change(runner, "type_text", 32)

    runner._native_visual_user_directives.append("Undo that last change now")
    before = len(calls)
    explicitly_allowed = _run(
        runner, "explicit-second-undo", "hotkey", {"keys": ["ctrl", "z"]},
    )
    assert explicitly_allowed["ok"] is True
    assert len(calls) == before + 1
    _confirm_visual_change(runner, "hotkey", 33)

    assert _run(
        runner, "second-rebuild", "type_text", {"text": "rebuilt again"},
    )["ok"] is True
    _confirm_visual_change(runner, "type_text", 34)
    before = len(calls)
    reused = _run(runner, "stale-directive", "hotkey", {"keys": ["ctrl", "z"]})
    assert len(calls) == before
    assert reused["net_progress_reversal_guard"] is True


def test_unverified_undo_without_pixel_reversal_does_not_create_reversal_debt(
    monkeypatch,
):
    runner, _calls = _runner(monkeypatch)
    _seed_output(runner)
    _confirm_visual_change(runner, "type_text", 40)
    original = runner._mutation_ledger[-1]

    assert _run(
        runner, "no-effect-undo", "hotkey", {"keys": ["ctrl", "z"]},
    )["ok"] is True
    _confirm_visual_change(runner, "hotkey", 41, ratio=0.0)

    assert "invalidated_by" not in original
    assert runner.session.hwnd not in runner._partial_output_guard._reversal_debts


def test_task_reset_wording_expires_after_first_successful_write(monkeypatch):
    runner, calls = _runner(
        monkeypatch,
        task="Reset the current work and clear everything before rebuilding it",
    )
    _seed_output(runner)
    before = len(calls)

    result = _run(runner, "reset", "hotkey", {"keys": ["ctrl", "n"]})

    assert len(calls) == before
    assert result["partial_output_destructive_guard"] is True
    assert result["user_reset_requested"] is False


def test_runtime_reset_directive_is_consumed_once(monkeypatch):
    runner, calls = _runner(monkeypatch)
    _seed_output(runner)
    runner._native_visual_user_directives.append("Clear the entire document now")

    first = _run(runner, "authorized-reset", "hotkey", {"keys": ["ctrl", "n"]})
    assert first["ok"] is True
    rewritten = _run(
        runner,
        "rewrite-after-reset",
        "type_text",
        {"text": "new retained content"},
    )
    assert rewritten["ok"] is True
    assert runner._partial_output_guard._active(runner.session.hwnd) is not None
    before = len(calls)

    second = _run(runner, "reused-reset", "hotkey", {"keys": ["ctrl", "n"]})

    assert len(calls) == before
    assert second["partial_output_destructive_guard"] is True
    assert second["user_reset_requested"] is False


def test_other_window_and_failed_mutation_do_not_protect(monkeypatch):
    runner, calls = _runner(monkeypatch)
    _seed_output(runner)
    runner.session.hwnd = 52
    other = _run(runner, "other-window", "hotkey", {"keys": ["ctrl", "n"]})
    assert other["ok"] is True
    assert calls[-1][0] == "hotkey"

    def responder(name: str, _args: dict):
        if name == "type_text":
            return {"ok": False, "verified": False, "error": "input failed"}
        return {"ok": True, "verified": True}

    failed_runner, failed_calls = _runner(monkeypatch, responder=responder)
    failed = _run(failed_runner, "failed", "type_text", {"text": "not applied"})
    assert failed["ok"] is False
    assert failed_runner._mutation_ledger == []
    reset = _run(failed_runner, "reset", "hotkey", {"keys": ["ctrl", "n"]})
    assert reset["ok"] is True
    assert failed_calls[-1][0] == "hotkey"
