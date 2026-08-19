from __future__ import annotations

import json
import threading

import pytest

from winpilot.action.raw_device import (
    AdmittedPhysicalClickBackend,
    RawActionReceipt,
    RawBackendResult,
    RawDeviceBackends,
    RawDeviceExecutor,
)
from winpilot.action.replay_policy import MutationOutcome
from winpilot.fast.action_contract import ALLOWED_BATCH_ACTIONS
from winpilot.fast.controller import FastGuiController
from winpilot.perception.fusion import Snapshot
from winpilot.perception.model import UIElement
from winpilot.utils.screenshot import Region


def _receipt(action: str, completed: bool | None = True) -> RawActionReceipt:
    if completed is True:
        return RawActionReceipt(
            action,
            MutationOutcome.APPLIED,
            RawBackendResult(True, {"ok": True, "via": "test"}),
        )
    if completed is False:
        return RawActionReceipt(
            action,
            MutationOutcome.NOT_APPLIED,
            RawBackendResult(False, {"ok": False, "via": "test"}),
        )
    return RawActionReceipt(
        action,
        MutationOutcome.OUTCOME_UNKNOWN,
        RawBackendResult(None, {"ok": False, "via": "test"}),
        "injected_unknown",
    )


class RecordingRawDevice:
    def __init__(self, *, completed: bool | None = True, stop_event=None):
        self.completed = completed
        self.stop_event = stop_event
        self.calls = []

    def _record(self, action, context, args, kwargs):
        self.calls.append((action, context, args, kwargs))
        if self.stop_event is not None:
            self.stop_event.set()
        return _receipt(action, self.completed)

    def click(self, context, *args, **kwargs):
        return self._record("click", context, args, kwargs)

    def type_text(self, context, *args, **kwargs):
        return self._record("type_text", context, args, kwargs)

    def hotkey(self, context, *args, **kwargs):
        return self._record("hotkey", context, args, kwargs)

    def scroll(self, context, *args, **kwargs):
        return self._record("scroll", context, args, kwargs)

    def move_mouse(self, context, *args, **kwargs):
        return self._record("move_mouse", context, args, kwargs)

    def drag(self, context, *args, **kwargs):
        return self._record("drag", context, args, kwargs)

    def input_sequence(self, context, *args, **kwargs):
        return self._record("input_sequence", context, args, kwargs)


def test_every_contract_action_has_an_explicit_route_and_renderer_actions_are_present():
    controller = FastGuiController(raw_device=RecordingRawDevice())
    routes = set(controller._routes())

    assert routes == ALLOWED_BATCH_ACTIONS
    assert {
        "click",
        "click_element",
        "type_text",
        "hotkey",
        "drag",
        "focus_window",
        "launch",
        "screenshot",
        "clipboard_read",
        "clipboard_write",
        "clipboard_paste",
        "wait",
        "scroll",
        "move_mouse",
    } <= routes


@pytest.mark.parametrize("completed", [False, None])
def test_physical_receipt_failure_is_not_retried(completed):
    raw = RecordingRawDevice(completed=completed)
    controller = FastGuiController(raw_device=raw)

    result = controller.execute([{"action": "click", "x": 11, "y": 22}])

    assert result["ok"] is False
    assert result["failed_at"] == 1
    assert len(raw.calls) == 1
    assert raw.calls[0][0] == "click"


def test_controller_calls_the_typed_raw_click_backend_exactly_once():
    calls = []

    def click_backend(
        x,
        y,
        button,
        count,
        mechanism,
        hwnd=None,
        *,
        final_guard=None,
        enforce_foreground=True,
        press_duration_ms=35,
        stop_event=None,
    ):
        calls.append({
            "x": x,
            "y": y,
            "button": button.value,
            "count": count.value,
            "mechanism": mechanism.value,
            "hwnd": hwnd,
            "enforce_foreground": enforce_foreground,
        })
        return RawBackendResult(True, {"ok": True, "via": "test"})

    raw = RawDeviceExecutor(RawDeviceBackends(
        admitted_physical_click=AdmittedPhysicalClickBackend(click_backend),
    ))
    controller = FastGuiController(raw_device=raw)

    result = controller.execute([{"action": "click", "x": 11, "y": 22}])

    assert result["ok"] is True
    assert calls == [{
        "x": 11,
        "y": 22,
        "button": "left",
        "count": 1,
        "mechanism": "physical",
        "hwnd": None,
        "enforce_foreground": False,
    }]


def test_stop_event_prevents_the_next_physical_child():
    stopped = threading.Event()
    raw = RecordingRawDevice(stop_event=stopped)
    controller = FastGuiController(stop_event=stopped, raw_device=raw)

    result = controller.execute([
        {"action": "click", "x": 11, "y": 22},
        {"action": "click", "x": 33, "y": 44},
    ])

    assert result["ok"] is False
    assert result["failed_at"] == 2
    assert len(raw.calls) == 1
    assert result["results"][1]["canceled"] is True


def test_run_scoped_target_reaches_later_raw_action(monkeypatch):
    import winpilot.fast.controller as controller_mod

    raw = RecordingRawDevice()
    monkeypatch.setattr(controller_mod.win32gui, "IsWindow", lambda hwnd: hwnd == 4242)
    monkeypatch.setattr(controller_mod.win32, "_window_text", lambda hwnd: "Target")
    monkeypatch.setattr(
        controller_mod.action_executor,
        "focus_window",
        lambda hwnd: {"ok": True, "hwnd": hwnd},
    )
    controller = FastGuiController(raw_device=raw)

    focused = controller.execute([{"action": "focus_window", "hwnd": 4242}])
    clicked = controller.execute([{"action": "click", "x": 50, "y": 60}])

    assert focused["ok"] is True
    assert clicked["ok"] is True
    assert controller.hwnd == 4242
    assert raw.calls[0][1].target_hwnd == 4242
    assert raw.calls[0][3]["enforce_foreground"] is False


def test_semantic_click_uses_fresh_target_observation_and_one_raw_click(monkeypatch):
    import winpilot.fast.controller as controller_mod

    raw = RecordingRawDevice()
    snapshots = []

    def observe(hwnd, **_kwargs):
        snapshots.append(hwnd)
        return Snapshot(
            hwnd=hwnd,
            title="Target",
            region=Region(0, 0, 800, 600),
            elements=[UIElement(
                elem_id=7,
                source="uia",
                role="Button",
                text="Submit",
                cx=320,
                cy=240,
                left=280,
                top=220,
                width=80,
                height=40,
            )],
        )

    monkeypatch.setattr(controller_mod.win32gui, "IsWindow", lambda hwnd: hwnd == 4242)
    controller = FastGuiController(raw_device=raw, observe_fn=observe)
    controller.hwnd = 4242

    result = controller.execute([{"action": "click_element", "label": "Submit"}])

    assert result["ok"] is True
    assert snapshots == [4242]
    assert len(raw.calls) == 1
    assert raw.calls[0][0] == "click"
    assert raw.calls[0][2][:2] == (320, 240)


@pytest.mark.parametrize(
    "action",
    ["mouse_down", "mouse_up", "ensure_input_mode", "select_color"],
)
def test_unimplemented_actions_are_not_advertised_or_executed(action):
    raw = RecordingRawDevice()
    controller = FastGuiController(raw_device=raw)

    result = controller.execute([{"action": action}])

    assert action not in ALLOWED_BATCH_ACTIONS
    assert result["ok"] is False
    assert result["failed_at"] == 1
    assert result["failure_type"] == "INVALID_ACTION_TRANSACTION"
    assert "unsupported controller action" in result["error"]
    assert raw.calls == []


@pytest.mark.parametrize(
    ("action", "secret"),
    [
        ({"action": "type_text", "text": "p@ssword-typed-secret"}, "p@ssword-typed-secret"),
        ({"action": "clipboard_write", "text": "clipboard-secret-token"}, "clipboard-secret-token"),
    ],
)
def test_sensitive_text_is_redacted_from_child_bus_and_preview(monkeypatch, action, secret):
    import winpilot.fast.controller as controller_mod

    events = []
    raw = RecordingRawDevice()
    monkeypatch.setattr(controller_mod.BUS, "publish", lambda event_type, **data: events.append({"type": event_type, **data}))
    monkeypatch.setattr(
        controller_mod.action_executor,
        "clipboard_write",
        lambda text: {"ok": True, "length": len(text)},
    )
    controller = FastGuiController(raw_device=raw)

    result = controller.execute([action])

    assert result["ok"] is True
    serialized_events = json.dumps(events, ensure_ascii=False, default=str)
    assert secret not in serialized_events
    assert secret not in result["results"][0]["preview"]
    done = next(event for event in events if event["type"] == "agent_batch_action")
    assert done["args"]["text_redacted"] is True
    assert done["args"]["text_length"] == len(secret)
    assert done["replay"] is None


def test_type_into_element_text_is_redacted_from_child_bus(monkeypatch):
    import winpilot.fast.controller as controller_mod

    secret = "typed-credential-secret"
    events = []
    raw = RecordingRawDevice()
    monkeypatch.setattr(controller_mod.BUS, "publish", lambda event_type, **data: events.append({"type": event_type, **data}))
    monkeypatch.setattr(controller_mod.win32gui, "IsWindow", lambda hwnd: hwnd == 4242)

    def observe(hwnd, **_kwargs):
        return Snapshot(
            hwnd=hwnd,
            title="Target",
            region=Region(0, 0, 800, 600),
            elements=[UIElement(
                elem_id=8,
                source="uia",
                role="Edit",
                text="Password",
                cx=300,
                cy=200,
                left=180,
                top=180,
                width=240,
                height=40,
            )],
        )

    controller = FastGuiController(raw_device=raw, observe_fn=observe)
    controller.hwnd = 4242

    result = controller.execute([{
        "action": "type_into_element",
        "label": "Password",
        "text": secret,
    }])

    assert result["ok"] is True
    assert [call[0] for call in raw.calls] == ["click", "type_text"]
    assert secret not in json.dumps(events, ensure_ascii=False, default=str)
    assert secret not in result["results"][0]["preview"]
    done = next(event for event in events if event["type"] == "agent_batch_action")
    assert done["args"]["text_redacted"] is True
    assert done["replay"] is None


def test_clipboard_read_content_is_not_copied_into_bus_preview(monkeypatch):
    import winpilot.fast.controller as controller_mod

    secret = "clipboard-read-secret"
    events = []
    monkeypatch.setattr(controller_mod.BUS, "publish", lambda event_type, **data: events.append({"type": event_type, **data}))
    monkeypatch.setattr(controller_mod.action_executor, "clipboard_read", lambda: secret)
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "clipboard_read"}])

    assert result["ok"] is True
    assert result["results"][0]["data"]["text"] == secret
    assert secret not in json.dumps(events, ensure_ascii=False, default=str)
    assert secret not in result["results"][0]["preview"]


def test_launch_command_token_is_redacted_from_bus_preview_and_replay(monkeypatch):
    import winpilot.fast.controller as controller_mod

    command = "browser.exe https://example.invalid/?access_token=launch-secret"
    events = []
    monkeypatch.setattr(controller_mod.BUS, "publish", lambda event_type, **data: events.append({"type": event_type, **data}))
    monkeypatch.setattr(
        controller_mod.apps,
        "launch_application",
        lambda app, arguments: {"ok": True, "target": app, "arguments": [arguments]},
    )
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "launch", "command": command}])

    assert result["ok"] is True
    serialized = json.dumps(events, ensure_ascii=False, default=str)
    assert "launch-secret" not in serialized
    assert "launch-secret" not in json.dumps(result, ensure_ascii=False, default=str)
    assert "launch-secret" not in result["results"][0]["preview"]
    done = next(event for event in events if event["type"] == "agent_batch_action")
    assert done["args"] == {
        "command_length": len(command),
        "arguments_length": 0,
        "command_redacted": True,
    }
    assert done["replay"] is None


def test_replayable_click_child_keeps_executable_replay(monkeypatch):
    import winpilot.fast.controller as controller_mod

    events = []
    monkeypatch.setattr(controller_mod.BUS, "publish", lambda event_type, **data: events.append({"type": event_type, **data}))
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "click", "x": 15, "y": 25}])

    assert result["ok"] is True
    done = next(event for event in events if event["type"] == "agent_batch_action")
    assert done["replay"] == {
        "tool": "click",
        "coords": [15, 25],
        "button": "left",
        "double": False,
    }


def test_close_is_idempotent_and_releases_owned_input(monkeypatch):
    import winpilot.fast.controller as controller_mod

    releases = []
    monkeypatch.setattr(
        controller_mod.action_executor,
        "release_held_input_state",
        lambda: releases.append(True) or True,
    )
    controller = FastGuiController(raw_device=RecordingRawDevice())

    controller.close()
    controller.close()

    assert releases == [True]


def test_batch_queue_scope_matches_physical_and_clipboard_resources(monkeypatch):
    import winpilot.fast.controller as controller_mod

    scopes = []
    monkeypatch.setattr(
        controller_mod.DESKTOP_ACTION_QUEUE,
        "run",
        lambda _action, fn, **kwargs: scopes.append(kwargs["scope"]) or fn(),
    )
    monkeypatch.setattr(
        controller_mod.action_executor,
        "release_held_input_state",
        lambda: True,
    )
    monkeypatch.setattr(
        controller_mod.action_executor,
        "clipboard_write",
        lambda text: {"ok": True, "length": len(text)},
    )
    controller = FastGuiController(raw_device=RecordingRawDevice())

    assert controller.execute([{"action": "click", "x": 1, "y": 2}])["ok"] is True
    assert controller.execute([{"action": "clipboard_write", "text": "x"}])["ok"] is True
    assert controller.execute([{"action": "clipboard_paste"}])["ok"] is True

    assert scopes == ["physical", "clipboard", "physical_clipboard"]
