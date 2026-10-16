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
from winpilot.fast.action_contract import (
    ALLOWED_BATCH_ACTIONS,
    BATCH_MUTATING_ACTIONS,
    build_action_envelope,
    validate_batch_actions,
)
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


# --------------------------------------------------------------------------
#  Value writes and window frames: the two primitives the shipped driver has
#  and this controller did not. Neither is synthesized input, so neither goes
#  through RawDevice; each is verified by reading the result back instead.
# --------------------------------------------------------------------------


class FakeValuePattern:
    def __init__(self, value: str = "", fail_set: bool = False):
        self.Value = value
        self.fail_set = fail_set

    def SetValue(self, text: str) -> None:
        if self.fail_set:
            raise RuntimeError("control rejected the value")
        self.Value = text


class FakeControl:
    def __init__(self, value: str = "", pattern: bool = True, fail_set: bool = False):
        self._pattern = FakeValuePattern(value, fail_set=fail_set) if pattern else None

    def GetValuePattern(self):
        if self._pattern is None:
            raise AttributeError("no value pattern")
        return self._pattern


def _uia_element(automation_id: str, control, text: str = "", box=(0, 0, 200, 40)):
    left, top, width, height = box
    return UIElement(
        source="uia",
        role="edit",
        text=text,
        cx=left + width // 2,
        cy=top + height // 2,
        left=left,
        top=top,
        width=width,
        height=height,
        extra={"automation_id": automation_id, "uia_ctrl": control},
    )


def _patch_uia(monkeypatch, elements):
    import winpilot.perception.uia as uia_mod

    monkeypatch.setattr(uia_mod, "collect", lambda _hwnd: list(elements))


def _patch_window(monkeypatch, *, rects, state=None):
    """Patch the window API with a small state machine over successive rects."""
    import winpilot.fast.controller as controller_mod

    remaining = list(rects)
    live = {"rect": remaining[0] if remaining else (0, 0, 100, 100)}
    flags = {"iconic": False, "zoomed": False}
    if state:
        flags.update(state)
    calls = {"set_window_pos": [], "show_window": []}

    def current_rect(_hwnd):
        return live["rect"]

    def next_rect():
        if len(remaining) > 1:
            remaining.pop(0)
        live["rect"] = remaining[0]
        return live["rect"]

    def set_window_pos(hwnd, insert_after, x, y, width, height, flags_used):
        calls["set_window_pos"].append((hwnd, insert_after, x, y, width, height, flags_used))
        if flags["zoomed"]:
            # A maximized window accepts the call and keeps its maximized frame,
            # which is exactly why the controller verifies by readback instead of
            # trusting the request.
            return None
        if width == 0 and height == 0:
            left, top, right, bottom = live["rect"]
            live["rect"] = (x, y, x + (right - left), y + (bottom - top))
        else:
            live["rect"] = (x, y, x + width, y + height)
        return None  # pywin32 returns None, not the Win32 BOOL

    def show_window(_hwnd, _command):
        calls["show_window"].append(_command)
        flags["iconic"] = False
        flags["zoomed"] = False
        next_rect()

    monkeypatch.setattr(controller_mod.win32gui, "IsWindow", lambda hwnd: hwnd == 4242)
    monkeypatch.setattr(controller_mod.win32gui, "GetWindowRect", current_rect)
    monkeypatch.setattr(controller_mod.win32gui, "SetWindowPos", set_window_pos)
    monkeypatch.setattr(controller_mod.win32gui, "ShowWindow", show_window)
    monkeypatch.setattr(controller_mod.win32gui, "IsIconic", lambda _hwnd: flags["iconic"])
    # pywin32 has no IsZoomed; the controller reads the placement instead.
    monkeypatch.setattr(
        controller_mod.win32gui,
        "GetWindowPlacement",
        lambda _hwnd: (0, 3 if flags["zoomed"] else 1, (0, 0), (0, 0), live["rect"]),
    )
    monkeypatch.setattr(controller_mod.win32gui, "GetForegroundWindow", lambda: 99)
    monkeypatch.setattr(controller_mod.win32, "_window_text", lambda _hwnd: "Target")
    return calls


@pytest.mark.parametrize(
    "action,expected",
    [
        ({"action": "set_value", "text": "x"}, "requires x and y, element_id, automation_id, or a label"),
        ({"action": "set_value", "automation_id": "15"}, "requires text"),
        ({"action": "set_window_frame"}, "requires at least one of x, y, width, height"),
        ({"action": "set_window_frame", "x": "wide"}, "x must be a number"),
        ({"action": "set_window_frame", "width": 0}, "width must be positive"),
        ({"action": "set_window_frame", "x": 1, "height": -3}, "height must be positive"),
    ],
)
def test_new_action_contract_refuses_incomplete_or_impossible_requests(action, expected):
    errors = validate_batch_actions([action])

    assert len(errors) == 1
    assert expected in errors[0]


def test_new_actions_are_mutating_so_the_batch_requires_a_post_frame():
    for name in ("set_value", "set_window_frame"):
        assert name in ALLOWED_BATCH_ACTIONS
        assert name in BATCH_MUTATING_ACTIONS
        envelope = build_action_envelope(name, {"x": 1, "y": 2}, action_id="a")
        assert envelope.policy.requires_post_frame is True


def test_set_value_writes_through_the_value_pattern_and_reports_the_read_back(monkeypatch):
    control = FakeControl("")
    _patch_uia(monkeypatch, [_uia_element("15", control)])
    _patch_window(monkeypatch, rects=[(0, 0, 800, 600)])
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "set_value", "hwnd": 4242, "automation_id": "15", "text": "written"}])

    assert result["ok"] is True
    step = result["results"][0]["data"]
    assert step["value_matches"] is True
    assert step["resolved_by"] == "automation_id"
    assert step["before_length"] == 0
    assert step["read_back_length"] == len("written")
    assert control.GetValuePattern().Value == "written"


def test_set_value_reports_a_read_back_mismatch_as_failure(monkeypatch):
    class NormalizingControl(FakeControl):
        def GetValuePattern(self):
            return FakeValuePattern("normalized")

    _patch_uia(monkeypatch, [_uia_element("15", NormalizingControl())])
    _patch_window(monkeypatch, rects=[(0, 0, 800, 600)])
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "set_value", "hwnd": 4242, "automation_id": "15", "text": "typed"}])

    assert result["ok"] is False
    assert result["results"][0]["data"]["value_matches"] is False


def test_set_value_refuses_an_unknown_target_by_name_instead_of_guessing(monkeypatch):
    _patch_uia(monkeypatch, [_uia_element("15", FakeControl())])
    _patch_window(monkeypatch, rects=[(0, 0, 800, 600)])
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "set_value", "hwnd": 4242, "automation_id": "nope", "text": "x"}])

    assert result["ok"] is False
    assert "nope" in result["results"][0]["error"]


def test_set_value_refuses_a_control_without_a_value_pattern(monkeypatch):
    _patch_uia(monkeypatch, [_uia_element("15", FakeControl(pattern=False))])
    _patch_window(monkeypatch, rects=[(0, 0, 800, 600)])
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "set_value", "hwnd": 4242, "automation_id": "15", "text": "x"}])

    assert result["ok"] is False
    assert "no value pattern" in result["results"][0]["error"]


def test_set_value_text_is_redacted_from_child_bus_and_preview(monkeypatch):
    import winpilot.fast.controller as controller_mod

    secret = "set-value-credential-secret"
    events = []
    monkeypatch.setattr(controller_mod.BUS, "publish", lambda event_type, **data: events.append({"type": event_type, **data}))
    _patch_uia(monkeypatch, [_uia_element("15", FakeControl())])
    _patch_window(monkeypatch, rects=[(0, 0, 800, 600)])
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "set_value", "hwnd": 4242, "automation_id": "15", "text": secret}])

    assert result["ok"] is True
    assert secret not in json.dumps(events, ensure_ascii=False, default=str)
    assert secret not in result["results"][0]["preview"]
    done = next(event for event in events if event["type"] == "agent_batch_action")
    assert done["args"]["text_redacted"] is True
    assert done["args"]["text_length"] == len(secret)
    # A redacted value must never become an executable replay step.
    assert done["replay"] is None


def test_set_window_frame_moves_and_resizes_and_reports_no_activation(monkeypatch):
    calls = _patch_window(monkeypatch, rects=[(-9, -9, 1929, 1029)])
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([
        {"action": "set_window_frame", "hwnd": 4242, "x": 140, "y": 110, "width": 720, "height": 470},
    ])

    assert result["ok"] is True
    step = result["results"][0]["data"]
    assert step["after"] == {"left": 140, "top": 110, "width": 720, "height": 470}
    assert step["before"]["width"] == 1938
    assert step["activated"] is False
    assert step["restored_from"] is None
    assert calls["set_window_pos"][0][2:6] == (140, 110, 720, 470)


def test_set_window_frame_keeps_the_size_it_was_not_asked_to_change(monkeypatch):
    _patch_window(monkeypatch, rects=[(100, 200, 900, 800)])
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "set_window_frame", "hwnd": 4242, "x": 300}])

    assert result["ok"] is True
    assert result["results"][0]["data"]["after"] == {"left": 300, "top": 200, "width": 800, "height": 600}


def test_set_window_frame_restores_a_maximized_window_before_applying(monkeypatch):
    calls = _patch_window(
        monkeypatch,
        rects=[(-9, -9, 1929, 1029), (100, 100, 900, 700)],
        state={"zoomed": True},
    )
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([
        {"action": "set_window_frame", "hwnd": 4242, "x": 50, "y": 60, "width": 640, "height": 480},
    ])

    assert result["ok"] is True
    assert result["results"][0]["data"]["restored_from"] == "maximized"
    assert calls["show_window"], "a maximized window must be restored before a frame can apply"
    assert result["results"][0]["data"]["after"] == {"left": 50, "top": 60, "width": 640, "height": 480}


def test_set_window_frame_reports_the_geometry_it_actually_got(monkeypatch):
    """A window that ignores the request must not be reported as success."""
    import winpilot.fast.controller as controller_mod

    _patch_window(monkeypatch, rects=[(0, 0, 400, 300)])
    monkeypatch.setattr(controller_mod.win32gui, "SetWindowPos", lambda *a, **k: None)
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "set_window_frame", "hwnd": 4242, "x": 700, "y": 500}])

    assert result["ok"] is False
    assert result["results"][0]["data"]["verified"] is False
    assert result["results"][0]["data"]["after"] == {"left": 0, "top": 0, "width": 400, "height": 300}
