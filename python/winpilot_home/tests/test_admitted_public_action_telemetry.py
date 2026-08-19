from __future__ import annotations

import hashlib
import inspect
import json
import queue
import threading
from collections.abc import Mapping
from typing import Any

import pytest

from winpilot.action.raw_device import (
    ClickCount,
    ClickMechanism,
    FinalKeyboardGuardDecision,
    FinalPointerGuardDecision,
    HotkeyChord,
    MouseButton,
    RawActionContext,
    RawDeviceBackends,
    RawDeviceExecutor,
)
from winpilot.action.replay_policy import MutationOutcome
from winpilot.utils.log import BUS


PRIVATE_X = 1379
PRIVATE_Y = 743
PRIVATE_AMOUNT = -17
PRIVATE_TEXT = "PRIVATE-TEXT-admitted-8c71"
PRIVATE_TEXT_SHA256 = hashlib.sha256(PRIVATE_TEXT.encode("utf-8")).hexdigest()
PRIVATE_KEYS = ("ctrl", "shift", "f11")
PRIVATE_CHORD = "+".join(PRIVATE_KEYS)

_BASE_PUBLIC_FIELDS = {
    "type",
    "ts",
    "action",
    "input_completed",
    "via",
    "parameter_visibility",
}
_PRIVATE_FIELDS = {
    "x",
    "y",
    "region",
    "point",
    "amount",
    "delta",
    "wheel_amount",
    "wheel_delta",
    "direction",
    "text",
    "text_hash",
    "keys",
    "modifiers",
    "chord",
    "canonical_arguments",
    "physical_arguments",
    "actor_envelope",
    "target_title",
    "application_path",
}


def _nested_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return {str(key) for key in value}.union(
            *(_nested_keys(item) for item in value.values()),
            set(),
        )
    if isinstance(value, (list, tuple)):
        return set().union(
            *(_nested_keys(item) for item in value),
            set(),
        )
    return set()


def _nested_values(value: Any) -> list[Any]:
    if isinstance(value, Mapping):
        values: list[Any] = []
        for item in value.values():
            values.extend(_nested_values(item))
        return values
    if isinstance(value, (list, tuple)):
        values = []
        for item in value:
            values.extend(_nested_values(item))
        return values
    return [value]


def _assert_private_parameters_absent(value: Any) -> None:
    assert not (_nested_keys(value) & _PRIVATE_FIELDS)
    nested = _nested_values(value)
    for sentinel in (
        PRIVATE_X,
        PRIVATE_Y,
        PRIVATE_AMOUNT,
        PRIVATE_TEXT,
        PRIVATE_TEXT_SHA256,
        PRIVATE_CHORD,
        *PRIVATE_KEYS,
    ):
        assert sentinel not in nested
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    assert PRIVATE_TEXT not in serialized
    assert PRIVATE_TEXT_SHA256 not in serialized
    assert PRIVATE_CHORD not in serialized


def _assert_public_action_event(
    event: Mapping[str, Any],
    *,
    action: str,
    extra: Mapping[str, Any] | None = None,
) -> None:
    expected_extra = dict(extra or {})
    assert set(event) == _BASE_PUBLIC_FIELDS | set(expected_extra)
    assert event["type"] == "action"
    assert event["action"] == action
    assert event["input_completed"] is True
    assert event["via"] == "physical_sendinput"
    assert event["parameter_visibility"] == "private_runtime_evidence"
    for key, expected in expected_extra.items():
        assert event[key] == expected
    _assert_private_parameters_absent(event)


def _action_events(event_queue: queue.Queue) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    while True:
        try:
            event = event_queue.get_nowait()
        except queue.Empty:
            return events
        if event.get("type") == "action":
            events.append(event)


def _next_action_event(
    event_queue: queue.Queue,
    *,
    action: str,
) -> dict[str, Any]:
    while True:
        event = event_queue.get(timeout=2)
        if event.get("type") == "action" and event.get("action") == action:
            return event


@pytest.fixture
def strict_backend_environment(monkeypatch):
    from winpilot.action import executor

    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(
        executor,
        "_target_window_still_foreground",
        lambda _hwnd: True,
    )
    monkeypatch.setattr(executor, "_raw_move_mouse", lambda _x, _y: None)
    monkeypatch.setattr(executor, "_cursor_pos", lambda: (PRIVATE_X, PRIVATE_Y))
    monkeypatch.setattr(
        executor,
        "_restore_cursor_after_agent_input",
        lambda _saved, _expected, _hwnd: True,
    )
    monkeypatch.setattr(
        executor,
        "_mouse_input",
        lambda flags, **kwargs: (
            "mouse",
            flags,
            tuple(sorted(kwargs.items())),
        ),
    )
    monkeypatch.setattr(
        executor,
        "_key_input",
        lambda **kwargs: ("key", tuple(sorted(kwargs.items()))),
    )
    monkeypatch.setattr(
        executor,
        "_send_inputs",
        lambda inputs: sent.append(tuple(inputs)),
    )
    monkeypatch.setattr(
        executor,
        "_should_type_via_vk",
        lambda _text: False,
    )
    monkeypatch.setattr(
        executor.user32,
        "GetForegroundWindow",
        lambda: 0,
    )
    monkeypatch.setattr(
        executor,
        "_vk_of",
        {"ctrl": 0x11, "shift": 0x10, "f11": 0x7A}.__getitem__,
    )
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)
    return executor, sent


def _run_scroll(executor):
    return executor.ADMITTED_PHYSICAL_SCROLL_BACKEND(
        PRIVATE_X,
        PRIVATE_Y,
        PRIVATE_AMOUNT,
        hwnd=41,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )


def _run_drag(executor):
    return executor.ADMITTED_PHYSICAL_DRAG_BACKEND(
        PRIVATE_X,
        PRIVATE_Y,
        PRIVATE_X + 16,
        PRIVATE_Y + 16,
        hwnd=41,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )


def _run_type_text(executor):
    return executor.ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND(
        PRIVATE_TEXT,
        hwnd=41,
        final_guard=lambda: FinalKeyboardGuardDecision.ADMITTED,
    )


def _run_hotkey(executor):
    return executor.ADMITTED_PHYSICAL_HOTKEY_BACKEND(
        HotkeyChord(PRIVATE_KEYS),
        hwnd=41,
        final_guard=lambda: FinalKeyboardGuardDecision.ADMITTED,
    )


def _run_click(executor, count: ClickCount):
    return executor.ADMITTED_PHYSICAL_CLICK_BACKEND(
        PRIVATE_X,
        PRIVATE_Y,
        button=MouseButton.LEFT,
        count=count,
        mechanism=ClickMechanism.PHYSICAL,
        hwnd=41,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        (
            {
                "action": "unknown",
                "input_completed": True,
                "via": "physical_sendinput",
            },
            "unknown admitted action",
        ),
        (
            {
                "action": "scroll",
                "input_completed": False,
                "via": "physical_sendinput",
            },
            "requires completed input",
        ),
        (
            {
                "action": "scroll",
                "input_completed": True,
                "via": "sendinput_restored",
            },
            "via is not public",
        ),
        (
            {
                "action": "scroll",
                "input_completed": True,
                "via": "physical_sendinput",
                "completed_codepoints": 0,
            },
            "cannot contain parameter statistics",
        ),
        (
            {
                "action": "click",
                "input_completed": True,
                "via": "physical_sendinput",
                "key_count": 1,
            },
            "cannot contain parameter statistics",
        ),
        (
            {
                "action": "type_text",
                "input_completed": True,
                "via": "physical_sendinput",
            },
            "requires only completed_codepoints",
        ),
        (
            {
                "action": "type_text",
                "input_completed": True,
                "via": "physical_sendinput",
                "completed_codepoints": -1,
            },
            "non-negative integer",
        ),
        (
            {
                "action": "hotkey",
                "input_completed": True,
                "via": "physical_sendinput",
                "key_count": True,
            },
            "non-negative integer",
        ),
        (
            {
                "action": "hotkey",
                "input_completed": True,
                "via": "physical_sendinput",
                "key_count": 2,
                "completed_codepoints": 0,
            },
            "requires only key_count",
        ),
    ],
)
def test_admitted_action_summary_rejects_invalid_field_combinations(
    strict_backend_environment,
    kwargs,
    match,
) -> None:
    executor, _sent = strict_backend_environment
    with pytest.raises(ValueError, match=match):
        executor._publish_admitted_action_summary(**kwargs)


def test_admitted_action_summary_rejects_unknown_keyword(
    strict_backend_environment,
) -> None:
    executor, _sent = strict_backend_environment
    assert all(
        parameter.kind is not inspect.Parameter.VAR_KEYWORD
        for parameter in inspect.signature(
            executor._publish_admitted_action_summary
        ).parameters.values()
    )
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        executor._publish_admitted_action_summary(
            action="scroll",
            input_completed=True,
            via="physical_sendinput",
            x=PRIVATE_X,
        )


def test_real_admitted_scroll_publishes_only_fixed_public_summary(
    strict_backend_environment,
) -> None:
    executor, sent = strict_backend_environment
    events = BUS.subscribe()
    try:
        result = _run_scroll(executor)
        published = _action_events(events)
    finally:
        BUS.unsubscribe(events)

    assert result.input_completed is True
    assert len(sent) == 1
    assert len(published) == 1
    _assert_public_action_event(published[0], action="scroll")


def test_real_admitted_drag_publishes_only_fixed_public_summary(
    strict_backend_environment,
) -> None:
    executor, sent = strict_backend_environment
    events = BUS.subscribe()
    try:
        result = _run_drag(executor)
        published = _action_events(events)
    finally:
        BUS.unsubscribe(events)

    assert result.input_completed is True
    assert sent
    assert len(published) == 1
    _assert_public_action_event(published[0], action="drag")


def test_real_admitted_type_text_publishes_only_codepoint_count(
    strict_backend_environment,
) -> None:
    executor, sent = strict_backend_environment
    events = BUS.subscribe()
    try:
        result = _run_type_text(executor)
        published = _action_events(events)
    finally:
        BUS.unsubscribe(events)

    assert result.input_completed is True
    assert len(sent) == len(PRIVATE_TEXT)
    assert len(published) == 1
    _assert_public_action_event(
        published[0],
        action="type_text",
        extra={"completed_codepoints": len(PRIVATE_TEXT)},
    )


def test_real_admitted_hotkey_publishes_only_key_count(
    strict_backend_environment,
) -> None:
    executor, sent = strict_backend_environment
    events = BUS.subscribe()
    try:
        result = _run_hotkey(executor)
        published = _action_events(events)
    finally:
        BUS.unsubscribe(events)

    assert result.input_completed is True
    assert len(sent) == 1 + len(PRIVATE_KEYS)
    assert len(published) == 1
    _assert_public_action_event(
        published[0],
        action="hotkey",
        extra={"key_count": len(PRIVATE_KEYS)},
    )


@pytest.mark.parametrize(
    ("count", "expected_input_batches"),
    [
        (ClickCount.SINGLE, 2),
        (ClickCount.DOUBLE, 4),
    ],
)
def test_real_admitted_click_does_not_publish_coordinates_or_replay(
    strict_backend_environment,
    count,
    expected_input_batches,
) -> None:
    executor, sent = strict_backend_environment
    events = BUS.subscribe()
    try:
        result = _run_click(executor, count)
        published = _action_events(events)
    finally:
        BUS.unsubscribe(events)

    assert result.input_completed is True
    assert len(sent) == expected_input_batches
    assert published == []


def test_admitted_telemetry_failure_preserves_single_raw_attempt_and_receipt(
    strict_backend_environment,
    monkeypatch,
) -> None:
    executor, sent = strict_backend_environment
    raw = RawDeviceExecutor(
        RawDeviceBackends(
            admitted_physical_scroll=executor.ADMITTED_PHYSICAL_SCROLL_BACKEND,
        )
    )
    monkeypatch.setattr(
        executor.BUS,
        "publish",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected public telemetry failure")
        ),
    )

    receipt = raw.scroll(
        RawActionContext(target_hwnd=41, current_hwnd=41),
        PRIVATE_X,
        PRIVATE_Y,
        PRIVATE_AMOUNT,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )

    assert receipt.outcome is MutationOutcome.APPLIED
    assert receipt.input_completed is True
    assert receipt.backend_result is not None
    assert receipt.backend_result.input_completed is True
    assert len(sent) == 1


def test_real_trace_recorder_persists_only_admitted_public_summaries(
    strict_backend_environment,
    tmp_path,
    monkeypatch,
) -> None:
    from winpilot.recorder import trace

    executor, _sent = strict_backend_environment
    monkeypatch.setattr(trace, "TRACES_DIR", tmp_path)
    recorder = trace.TraceRecorder()
    try:
        recorder.start("admitted-public-summary")
        assert _run_scroll(executor).input_completed is True
        assert _run_type_text(executor).input_completed is True
        assert _run_hotkey(executor).input_completed is True
        assert _run_click(executor, ClickCount.DOUBLE).input_completed is True
        recorder.stop()
    finally:
        recorder.stop()
        BUS.remove_sink(recorder._on_event)

    path = tmp_path / "admitted-public-summary.jsonl"
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    actions = [record for record in records if record.get("type") == "action"]
    assert [record["action"] for record in actions] == [
        "scroll",
        "type_text",
        "hotkey",
    ]
    _assert_public_action_event(actions[0], action="scroll")
    _assert_public_action_event(
        actions[1],
        action="type_text",
        extra={"completed_codepoints": len(PRIVATE_TEXT)},
    )
    _assert_public_action_event(
        actions[2],
        action="hotkey",
        extra={"key_count": len(PRIVATE_KEYS)},
    )
    _assert_private_parameters_absent(records)


def test_websocket_forwards_the_real_admitted_public_summary_unchanged(
    strict_backend_environment,
    monkeypatch,
) -> None:
    from fastapi.testclient import TestClient
    from winpilot import server

    executor, sent = strict_backend_environment
    monkeypatch.setattr(server, "CONTROL_TOKEN", "")
    monkeypatch.setattr(
        server.RUN_PROJECTIONS,
        "hydrate_directory",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(server, "_warm_perception_dimensions", lambda: None)
    original_subscribe = BUS.subscribe
    events = original_subscribe()
    websocket_subscribed = threading.Event()

    def subscribe_for_websocket():
        subscribed = original_subscribe()
        websocket_subscribed.set()
        return subscribed

    monkeypatch.setattr(BUS, "subscribe", subscribe_for_websocket)
    try:
        with TestClient(server.app) as client:
            with client.websocket_connect("/ws/events") as websocket:
                assert websocket_subscribed.wait(timeout=2)
                result = _run_scroll(executor)
                bus_event = _next_action_event(events, action="scroll")
                while True:
                    websocket_event = websocket.receive_json()
                    if (
                        websocket_event.get("type") == "action"
                        and websocket_event.get("action") == "scroll"
                    ):
                        break
    finally:
        BUS.unsubscribe(events)

    assert result.input_completed is True
    assert len(sent) == 1
    assert websocket_event == bus_event
    _assert_public_action_event(websocket_event, action="scroll")
