from __future__ import annotations

import copy

from eval_targets import visual_interaction_server as server


def _event(case_id: str, *, succeeded: bool = False) -> dict:
    return {
        "case_id": case_id,
        "scenario": "ordered_clicks",
        "seed": 17,
        "type": "scenario_success" if succeeded else "page_ready",
        "trusted": succeeded,
        "succeeded": succeeded,
        "received_at": 123.0,
        "details": {"succeeded": succeeded},
    }


def _dynamic_event(event_type: str, *, inside_goal: bool = True) -> dict:
    succeeded = event_type in {"pointerup", "scenario_success"} and inside_goal
    return {
        "case_id": "dynamic-case",
        "scenario": "dynamic_hold",
        "seed": 17,
        "type": event_type,
        "trusted": True,
        "succeeded": succeeded,
        "received_at": 123.0,
        "details": {
            "succeeded": succeeded,
            "down_count": 1,
            "checkpoint_reached": True,
            "goal_revealed_after_down": True,
            "inside_goal": inside_goal,
            "completion_event": "pointerup",
        },
    }


def _keyboard_event(
    scenario: str,
    event_type: str,
    details: dict,
    *,
    trusted: bool = False,
    succeeded: bool = False,
) -> dict:
    return {
        "case_id": "keyboard-case",
        "scenario": scenario,
        "seed": 23,
        "type": event_type,
        "trusted": trusted,
        "succeeded": succeeded,
        "received_at": 123.0,
        "details": {"succeeded": succeeded, **details},
    }


def _keyboard_entry_events() -> list[dict]:
    identity = {"target_id": "entry-field-1"}
    return [
        _keyboard_event("keyboard_entry", "page_ready", {
            **identity, "expected_text": "A7", "candidate_count": 3,
            "initial_value": "",
        }),
        _keyboard_event("keyboard_entry", "trusted_beforeinput", {
            **identity, "input_type": "insertText", "data": "A",
            "value_before": "", "is_composing": False,
        }, trusted=True),
        _keyboard_event("keyboard_entry", "trusted_input", {
            **identity, "input_type": "insertText", "data": "A", "value": "A",
            "is_composing": False,
        }, trusted=True),
        _keyboard_event("keyboard_entry", "trusted_beforeinput", {
            **identity, "input_type": "insertText", "data": "7",
            "value_before": "A", "is_composing": False,
        }, trusted=True),
        _keyboard_event("keyboard_entry", "trusted_input", {
            **identity, "input_type": "insertText", "data": "7", "value": "A7",
            "is_composing": False,
        }, trusted=True),
        _keyboard_event("keyboard_entry", "scenario_success", {
            **identity, "expected_text": "A7", "final_value": "A7",
            "beforeinput_count": 2, "input_count": 2,
            "wrong_target_activity": False, "sequence_valid": True,
        }, trusted=True, succeeded=True),
    ]


def _key_details(
    key: str,
    *,
    ctrl: bool,
    selection_end: int = 0,
    target_id: str = "selection-editor-2",
) -> dict:
    return {
        "target_id": target_id,
        "key": key,
        "code": "KeyA" if key.casefold() == "a" else "ControlLeft",
        "ctrl_key": ctrl,
        "alt_key": False,
        "shift_key": False,
        "meta_key": False,
        "repeat": False,
        "selection_start": 0,
        "selection_end": selection_end,
        "content": "amber cedar",
    }


def _keyboard_select_events() -> list[dict]:
    identity = {"target_id": "selection-editor-2"}
    length = len("amber cedar")
    return [
        _keyboard_event("keyboard_select_all", "page_ready", {
            **identity, "original_text": "amber cedar", "candidate_count": 3,
            "selection_start": 0, "selection_end": 0,
        }),
        _keyboard_event(
            "keyboard_select_all", "trusted_keydown",
            _key_details("Control", ctrl=True), trusted=True,
        ),
        _keyboard_event(
            "keyboard_select_all", "trusted_keydown",
            _key_details("a", ctrl=True), trusted=True,
        ),
        _keyboard_event(
            "keyboard_select_all", "trusted_keyup",
            _key_details("a", ctrl=True, selection_end=length), trusted=True,
        ),
        _keyboard_event(
            "keyboard_select_all", "trusted_keyup",
            _key_details("Control", ctrl=False, selection_end=length), trusted=True,
        ),
        _keyboard_event("keyboard_select_all", "scenario_success", {
            **identity, "original_text": "amber cedar", "final_text": "amber cedar",
            "selection_start": 0, "selection_end": length,
            "modifier_released": True, "mutation_count": 0,
            "wrong_target_activity": False, "sequence_valid": True,
        }, trusted=True, succeeded=True),
    ]


def test_case_scoped_state_cannot_inherit_another_cases_success():
    original = copy.deepcopy(server.STATE)
    try:
        server.STATE.clear()
        server.STATE.update({
            "ready": True,
            "success": True,
            "events": [_event("old-case", succeeded=True), _event("new-case")],
            "started_at": 100.0,
            "case_id": "old-case",
            "success_event": _event("old-case", succeeded=True),
        })

        current = server._state_snapshot("new-case")

        assert current["ready"] is True
        assert current["success"] is False
        assert current["case_id"] == "new-case"
        assert [item["case_id"] for item in current["events"]] == ["new-case"]
        assert "success_event" not in current
    finally:
        server.STATE.clear()
        server.STATE.update(original)


def test_case_scoped_state_reports_only_its_own_trusted_success():
    original = copy.deepcopy(server.STATE)
    try:
        own_success = _event("new-case", succeeded=True)
        server.STATE.clear()
        server.STATE.update({
            "ready": True,
            "success": True,
            "events": [_event("old-case", succeeded=True), own_success],
            "started_at": 100.0,
        })

        current = server._state_snapshot("new-case")

        assert current["success"] is True
        assert current["success_event"] == own_success
        assert current["scenario"] == "ordered_clicks"
        assert current["seed"] == 17
    finally:
        server.STATE.clear()
        server.STATE.update(original)


def test_dynamic_success_requires_preceding_trusted_in_goal_pointerup():
    original = copy.deepcopy(server.STATE)
    try:
        forged_success = _dynamic_event("scenario_success")
        server.STATE.clear()
        server.STATE.update({
            "ready": True,
            "success": True,
            "events": [forged_success],
            "started_at": 100.0,
        })

        without_release = server._state_snapshot("dynamic-case")

        assert without_release["success"] is False
        assert "success_event" not in without_release

        server.STATE["events"] = [
            _dynamic_event("pointerup", inside_goal=False),
            forged_success,
        ]
        outside_release = server._state_snapshot("dynamic-case")
        assert outside_release["success"] is False

        server.STATE["events"] = [_dynamic_event("pointerup"), forged_success]
        valid = server._state_snapshot("dynamic-case")
        assert valid["success"] is True
        assert valid["success_event"] == forged_success
    finally:
        server.STATE.clear()
        server.STATE.update(original)


def test_keyboard_entry_success_requires_complete_trusted_prefix_chain():
    events = _keyboard_entry_events()

    assert server._accepted_success_event(events, len(events) - 1) is True

    focus_diagnostic = copy.deepcopy(events)
    focus_diagnostic.insert(1, _keyboard_event("keyboard_entry", "trusted_focus", {
        "target_id": "entry-field-1", "active_target_id": "entry-field-1",
        "value_length": 0, "selection_start": 0, "selection_end": 0,
    }, trusted=True))
    assert server._accepted_success_event(
        focus_diagnostic, len(focus_diagnostic) - 1,
    ) is True

    missing_beforeinput = copy.deepcopy(events)
    del missing_beforeinput[1]
    assert server._accepted_success_event(
        missing_beforeinput, len(missing_beforeinput) - 1,
    ) is False

    appended = copy.deepcopy(events)
    appended[1]["details"]["value_before"] = "OLD"
    assert server._accepted_success_event(appended, len(appended) - 1) is False


def test_keyboard_entry_rejects_paste_wrong_target_and_untrusted_activity():
    events = _keyboard_entry_events()
    paste = _keyboard_event("keyboard_entry", "trusted_paste", {
        "target_id": "entry-field-1", "value_before": "",
    }, trusted=True)
    events.insert(-1, paste)
    assert server._accepted_success_event(events, len(events) - 1) is False

    wrong_target = _keyboard_entry_events()
    wrong_target[2]["details"]["target_id"] = "entry-field-0"
    assert server._accepted_success_event(
        wrong_target, len(wrong_target) - 1,
    ) is False

    untrusted = _keyboard_entry_events()
    untrusted[1]["type"] = "untrusted_beforeinput"
    untrusted[1]["trusted"] = False
    assert server._accepted_success_event(untrusted, len(untrusted) - 1) is False

    composing = _keyboard_entry_events()
    composing[1]["details"]["is_composing"] = True
    assert server._accepted_success_event(composing, len(composing) - 1) is False


def test_keyboard_select_all_requires_ordered_trusted_chord_and_release():
    events = _keyboard_select_events()

    assert server._accepted_success_event(events, len(events) - 1) is True

    no_release = copy.deepcopy(events)
    del no_release[-2]
    assert server._accepted_success_event(no_release, len(no_release) - 1) is False

    wrong_order = copy.deepcopy(events)
    wrong_order[1], wrong_order[2] = wrong_order[2], wrong_order[1]
    assert server._accepted_success_event(wrong_order, len(wrong_order) - 1) is False


def test_keyboard_select_all_rejects_partial_selection_mutation_and_wrong_target():
    partial = _keyboard_select_events()
    partial[-3]["details"]["selection_end"] = 4
    assert server._accepted_success_event(partial, len(partial) - 1) is False

    mutated = _keyboard_select_events()
    mutated.insert(-1, _keyboard_event("keyboard_select_all", "trusted_input", {
        "target_id": "selection-editor-2", "input_type": "insertText",
        "data": "x", "value": "amber cedarx",
    }, trusted=True))
    assert server._accepted_success_event(mutated, len(mutated) - 1) is False

    wrong_target = _keyboard_select_events()
    wrong_target[2]["details"]["target_id"] = "selection-editor-0"
    assert server._accepted_success_event(
        wrong_target, len(wrong_target) - 1,
    ) is False

    repeated = _keyboard_select_events()
    repeated[2]["details"]["repeat"] = True
    assert server._accepted_success_event(repeated, len(repeated) - 1) is False

    cut = _keyboard_select_events()
    cut.insert(-1, _keyboard_event("keyboard_select_all", "trusted_cut", {
        "target_id": "selection-editor-2", "content": "amber cedar",
    }, trusted=True))
    assert server._accepted_success_event(cut, len(cut) - 1) is False


def test_keyboard_success_cannot_reuse_pre_ready_or_ambiguous_evidence():
    for build in (_keyboard_entry_events, _keyboard_select_events):
        before_ready = build()
        before_ready[0], before_ready[1] = before_ready[1], before_ready[0]
        assert server._accepted_success_event(
            before_ready, len(before_ready) - 1,
        ) is False

        duplicate_ready = build()
        duplicate_ready.insert(1, copy.deepcopy(duplicate_ready[0]))
        assert server._accepted_success_event(
            duplicate_ready, len(duplicate_ready) - 1,
        ) is False

    missing_selection = _keyboard_select_events()
    missing_selection[0]["details"].pop("selection_start")
    missing_selection[0]["details"].pop("selection_end")
    assert server._accepted_success_event(
        missing_selection, len(missing_selection) - 1,
    ) is False

    non_integer_selection = _keyboard_select_events()
    non_integer_selection[-3]["details"]["selection_start"] = False
    non_integer_selection[-3]["details"]["selection_end"] = float(
        non_integer_selection[-3]["details"]["selection_end"],
    )
    assert server._accepted_success_event(
        non_integer_selection, len(non_integer_selection) - 1,
    ) is False
