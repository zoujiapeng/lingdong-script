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


# --------------------------------------------------------------------------
#  Menu commands: the accessibility tree reaches a menu bar and its
#  first-level items but reports no children for them, so the menu is walked
#  through the Win32 API the application itself populated. Like the two above,
#  this is not synthesized input, and what the application did about it is
#  reported as an observation rather than assumed.
# --------------------------------------------------------------------------


def _menu_entry(text, item_id, *, submenu=0, enabled=True, separator=False):
    return {
        "text": text,
        "id": item_id,
        "submenu": submenu,
        "enabled": enabled,
        "separator": separator,
    }


# Two levels plus a nested one, shaped the way a real application menu reads:
# heading labels, accelerator markers, shortcut text and a disabled item.
_MENU_ROOT = 900
_MENU_TREE = {
    _MENU_ROOT: [
        _menu_entry("&Edit", 0, submenu=901),
        _menu_entry("&Window", 0),
        _menu_entry("&Help", 0, submenu=903),
        _menu_entry("", 0, enabled=False, separator=True),
    ],
    901: [
        _menu_entry("&Save As...\tCtrl+Shift+S", 4),
        _menu_entry("&Copy\tCtrl+C", 769, enabled=False),
        _menu_entry("&Zoom", 0, submenu=902),
    ],
    902: [_menu_entry("Zoom &in", 41)],
    903: [_menu_entry("&Find", 21), _menu_entry("&Find", 22)],
}


class _MenuGui:
    """Stands in for ``win32gui`` on the paths a menu read uses."""

    def __init__(self, menu, *, children=()):
        self._menu = menu
        self._children = list(children)
        self.window_text_calls = 0

    def GetMenu(self, _hwnd):
        return self._menu

    def IsWindow(self, hwnd):
        return hwnd == 4242

    def IsWindowVisible(self, _hwnd):
        return True

    def GetClassName(self, hwnd):
        return str(self._children[int(hwnd)]) if int(hwnd) < len(self._children) else "Edit"

    def EnumChildWindows(self, _hwnd, callback, param):
        for index in range(len(self._children)):
            callback(index, param)


def _scalar(value):
    """Read a Win32 scalar the call handed over, however it was passed.

    A nested ctypes type does not convert through ``int()`` in this runtime and
    must be read via ``.value``; a plain Python integer must not be.
    """
    inner = getattr(value, "value", value)
    return int(inner)


def _patch_menu(monkeypatch, *, menu=_MENU_ROOT, sent=None, titles=None, children=None, tree=None):
    """Patch a live-shaped Win32 menu, capturing every command that is sent."""
    import ctypes
    import win32con
    import winpilot.fast.controller as controller_mod

    tree = tree if tree is not None else _MENU_TREE
    gui = _MenuGui(menu, children=children or ["Edit"])
    title_calls = iter(titles) if titles else None
    text_length_value = [0]

    def read_item(handle, position):
        items = tree.get(int(handle))
        if items is None or not 0 <= int(position) < len(items):
            return None
        entry = dict(items[int(position)])
        entry["index"] = int(position)
        entry["label"] = controller_mod._menu_label(entry["text"])
        entry["is_container"] = bool(entry["submenu"])
        return entry

    def item_count(handle):
        return len(tree.get(controller_mod._menu_handle(handle), []))

    def get_menu_item_info(_handle, position, _by_position, pointer):
        # The real call receives an HMENU; read it back through the same
        # coercion the production code uses, so the fake cannot disagree about
        # the handle it was handed.
        entry = read_item(controller_mod._menu_handle(_handle), int(position))
        if entry is None:
            return 0
        info = ctypes.cast(pointer, ctypes.POINTER(controller_mod._MenuItemInfo)).contents
        info.fMask = win32con.MIIM_SUBMENU if entry["is_container"] else 0
        info.fType = win32con.MF_SEPARATOR if entry["separator"] else 0
        info.fState = 0 if entry["enabled"] else win32con.MFS_GRAYED
        info.wID = int(entry["id"])
        info.hSubMenu = int(entry["submenu"])
        label = str(entry["text"])
        ctypes.memmove(
            ctypes.c_void_p(info.dwTypeData),
            label,
            (len(label) + 1) * ctypes.sizeof(ctypes.c_wchar),
        )
        return 1

    def send(hwnd, message, wparam, lparam, _flags, _timeout, out):
        # Mirrors the two uses the production code makes of this call: a text
        # length read, and the command itself.
        if _scalar(message) == win32con.WM_GETTEXTLENGTH:
            if out:
                ctypes.cast(out, ctypes.POINTER(ctypes.c_size_t)).contents.value = text_length_value[0]
            return 1
        if sent is not None:
            sent.append({
                "hwnd": controller_mod._menu_handle(hwnd),
                "message": _scalar(message),
                "command": _scalar(wparam),
                "lparam": _scalar(lparam),
            })
        return 1

    monkeypatch.setattr(controller_mod, "win32gui", gui)
    monkeypatch.setattr(controller_mod._user32, "GetMenuItemInfoW", get_menu_item_info)
    monkeypatch.setattr(controller_mod._user32, "GetMenuItemCount", item_count)
    monkeypatch.setattr(controller_mod._user32, "SendMessageTimeoutW", send)
    if title_calls is not None:
        monkeypatch.setattr(
            controller_mod.win32,
            "_window_text",
            lambda _hwnd: next(title_calls, "Target"),
        )
    return gui


@pytest.mark.parametrize(
    "action,expected",
    [
        ({"action": "invoke_menu"}, "requires a non-empty path array"),
        ({"action": "invoke_menu", "path": "File"}, "requires a non-empty path array"),
        ({"action": "invoke_menu", "path": []}, "requires a non-empty path array"),
        ({"action": "invoke_menu", "path": ["a", "b", "c", "d", "e", "f", "g", "h", "i"]}, "limited to 8 menu levels"),
        ({"action": "invoke_menu", "path": [True]}, "path[0] must be a menu label or an index"),
        ({"action": "invoke_menu", "path": [-1]}, "path[0] must not be negative"),
        ({"action": "invoke_menu", "path": [""]}, "path[0] must be a menu label or an index"),
        ({"action": "invoke_menu", "path": ["File", None]}, "path[1] must be a menu label or an index"),
    ],
)
def test_invoke_menu_contract_refuses_a_path_it_could_not_walk(action, expected):
    errors = validate_batch_actions([action])

    assert len(errors) == 1
    assert expected in errors[0]


@pytest.mark.parametrize(
    "action,expected",
    [
        ({"action": "kill_app"}, "requires pid, hwnd, or title"),
        ({"action": "kill_app", "pid": 0}, "pid must be a positive process id"),
        ({"action": "kill_app", "pid": -5}, "pid must be a positive process id"),
        ({"action": "kill_app", "pid": "soon"}, "pid must be a number"),
        ({"action": "kill_app", "pid": 10, "timeout_s": 0}, "timeout_s must be in 0..60"),
        ({"action": "kill_app", "pid": 10, "timeout_s": 61}, "timeout_s must be in 0..60"),
        ({"action": "kill_app", "title": ""}, "requires pid, hwnd, or title"),
    ],
)
def test_kill_app_contract_refuses_an_unnameable_or_impossible_target(action, expected):
    errors = validate_batch_actions([action])

    assert len(errors) == 1
    assert expected in errors[0]


def test_kill_app_is_a_mutating_action_that_requires_a_post_frame():
    assert "kill_app" in ALLOWED_BATCH_ACTIONS
    assert "kill_app" in BATCH_MUTATING_ACTIONS
    envelope = build_action_envelope("kill_app", {"pid": 42}, action_id="a")
    assert envelope.policy.requires_post_frame is True


def _patch_kill(monkeypatch, *, alive_after: int = 0, current_pid: int = 999_999, windows=None):
    """Patch process liveness and the termination call; count the polls."""
    import winpilot.fast.controller as controller_mod

    state = {"polls": 0, "taskkill": []}
    windows = windows if windows is not None else []

    def is_alive(_pid):
        state["polls"] += 1
        # The first poll runs before any taskkill, so `alive_after` counts the
        # polls that still answer "running" after the request.
        return state["polls"] <= 1 + alive_after

    def run(argv, **_kwargs):
        state["taskkill"].append(list(argv))

        class Completed:
            returncode = 0
            stdout = "SUCCESS: The process has been terminated."
            stderr = ""

        return Completed()

    monkeypatch.setattr(controller_mod, "_process_is_alive", is_alive)
    monkeypatch.setattr(controller_mod.subprocess, "run", run)
    monkeypatch.setattr(controller_mod.win32process, "GetCurrentProcessId", lambda: current_pid)
    monkeypatch.setattr(controller_mod.win32process, "GetWindowThreadProcessId", lambda _hwnd: (1, 4242))
    monkeypatch.setattr(controller_mod.win32, "list_windows", lambda: list(windows))
    monkeypatch.setattr(controller_mod.win32, "_window_text", lambda _hwnd: "Target")
    return state


def test_kill_app_terminates_by_pid_and_confirms_it_ended(monkeypatch):
    state = _patch_kill(monkeypatch, alive_after=0)
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "kill_app", "pid": 4242}])

    assert result["ok"] is True
    step = result["results"][0]["data"]
    assert step["terminated"] is True and step["verified"] is True
    assert step["resolved_by"] == "pid"
    assert state["taskkill"] == [["taskkill", "/PID", "4242", "/F"]]


def test_kill_app_fails_when_the_process_is_still_running_after_the_deadline(monkeypatch):
    """The termination request's own success is not the claim; being gone is."""
    _patch_kill(monkeypatch, alive_after=10_000)
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "kill_app", "pid": 4242, "timeout_s": 0.4}])

    assert result["ok"] is False
    step = result["results"][0]["data"]
    assert step["verified"] is False
    assert "still running" in step["error"]
    assert step["exit_code"] == 0, "the request itself succeeded, and that is not enough"


def test_kill_app_refuses_a_title_that_names_more_than_one_application(monkeypatch):
    """Terminating the wrong application cannot be undone, so ambiguity refuses."""
    windows = [
        {"hwnd": 11, "pid": 41, "title": "Notes - one"},
        {"hwnd": 12, "pid": 42, "title": "Notes - two"},
    ]
    state = _patch_kill(monkeypatch, windows=windows)
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "kill_app", "title": "Notes"}])

    assert result["ok"] is False
    step = result["results"][0]["data"]
    assert "names 2 running applications" in step["error"]
    assert step["candidate_pids"] == [41, 42]
    assert state["taskkill"] == [], "nothing may be terminated on an ambiguous target"


def test_kill_app_acts_when_a_title_names_exactly_one_application(monkeypatch):
    windows = [
        {"hwnd": 11, "pid": 41, "title": "Notes - one"},
        {"hwnd": 12, "pid": 42, "title": "Something else"},
    ]
    state = _patch_kill(monkeypatch, windows=windows)
    _patch_window(monkeypatch, rects=[(0, 0, 400, 300)])
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "kill_app", "hwnd": 4242}])

    assert result["ok"] is True
    step = result["results"][0]["data"]
    assert step["pid"] == 4242
    assert step["resolved_by"] == "hwnd"
    assert state["taskkill"] == [["taskkill", "/PID", "4242", "/F"]]


def test_kill_app_refuses_to_terminate_the_runtime_itself(monkeypatch):
    state = _patch_kill(monkeypatch, current_pid=4242)
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "kill_app", "pid": 4242}])

    assert result["ok"] is False
    assert "own process" in result["results"][0]["data"]["error"]
    assert state["taskkill"] == []


def test_kill_app_is_not_offered_as_a_replay_step(monkeypatch):
    """An executable step that ends an application must not be replayable."""
    import winpilot.fast.controller as controller_mod

    events = []
    monkeypatch.setattr(controller_mod.BUS, "publish", lambda event_type, **data: events.append({"type": event_type, **data}))
    _patch_kill(monkeypatch)
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "kill_app", "pid": 4242}])

    assert result["ok"] is True
    done = next(event for event in events if event["type"] == "agent_batch_action")
    assert done["replay"] is None
    # The trace keeps the fact of the action without either identifier.
    assert done["args"]["target_redacted"] is True
    assert "pid" not in done["args"]


def test_invoke_menu_is_a_mutating_action_that_requires_a_post_frame():
    assert "invoke_menu" in ALLOWED_BATCH_ACTIONS
    assert "invoke_menu" in BATCH_MUTATING_ACTIONS
    envelope = build_action_envelope("invoke_menu", {"path": ["File"]}, action_id="a")
    assert envelope.policy.requires_post_frame is True


def test_invoke_menu_walks_the_menu_by_label_and_sends_the_command_id(monkeypatch):
    import win32con

    sent = []
    _patch_menu(monkeypatch, sent=sent, titles=["Untitled"])
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "invoke_menu", "hwnd": 4242, "path": ["Edit", "Save As"]}])

    assert result["ok"] is True
    step = result["results"][0]["data"]
    assert step["command"] == 4
    assert step["delivered"] is True
    assert step["hwnd"] == 4242
    assert step["resolved_by"] == "hwnd"
    # The accelerator marker, the shortcut and the ellipsis are not part of what
    # a caller meant by "Save As", and the window keeps its own label text.
    assert [entry["text"] for entry in step["resolved_path"]] == ["&Edit", "&Save As...\tCtrl+Shift+S"]
    assert [entry["index"] for entry in step["resolved_path"]] == [0, 0]
    assert sent == [{"hwnd": 4242, "message": win32con.WM_COMMAND, "command": 4, "lparam": 0}]


def test_invoke_menu_descends_nested_submenus(monkeypatch):
    sent = []
    _patch_menu(monkeypatch, sent=sent)
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "invoke_menu", "hwnd": 4242, "path": ["Edit", "Zoom", "Zoom in"]}])

    assert result["ok"] is True
    assert result["results"][0]["data"]["command"] == 41
    assert len(result["results"][0]["data"]["resolved_path"]) == 3


def test_invoke_menu_addresses_items_by_index_when_the_label_is_localized(monkeypatch):
    sent = []
    _patch_menu(monkeypatch, sent=sent)
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "invoke_menu", "hwnd": 4242, "path": [0, 0]}])

    assert result["ok"] is True
    assert result["results"][0]["data"]["command"] == 4


def test_invoke_menu_refuses_a_path_that_ends_on_a_menu_heading_and_shows_what_is_in_it(monkeypatch):
    sent = []
    _patch_menu(monkeypatch, sent=sent)
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "invoke_menu", "hwnd": 4242, "path": ["Edit", "Zoom"]}])

    assert result["ok"] is False
    data = result["results"][0]["data"]
    assert "container rather than a command" in data["error"]
    assert data["available"] == [
        {"index": 0, "text": "Zoom &in", "id": 41, "container": False, "enabled": True},
    ]
    assert sent == [], "a heading must not be executed as a command"


def test_invoke_menu_names_what_was_available_instead_of_guessing(monkeypatch):
    sent = []
    _patch_menu(monkeypatch, sent=sent)
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "invoke_menu", "hwnd": 4242, "path": ["Edit", "Paste"]}])

    assert result["ok"] is False
    data = result["results"][0]["data"]
    assert "no menu item is named 'Paste'" in data["error"]
    assert "menu path level 2" in data["error"]
    assert [entry["text"] for entry in data["available"]] == ["&Save As...\tCtrl+Shift+S", "&Copy\tCtrl+C", "&Zoom"]
    assert sent == []


def test_invoke_menu_refuses_an_ambiguous_label_instead_of_picking_one(monkeypatch):
    sent = []
    _patch_menu(monkeypatch, sent=sent)
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "invoke_menu", "hwnd": 4242, "path": ["Help", "Find"]}])

    assert result["ok"] is False
    assert "names 2 items in this menu" in result["results"][0]["data"]["error"]
    assert sent == []


def test_invoke_menu_refuses_a_window_without_a_menu(monkeypatch):
    sent = []
    _patch_menu(monkeypatch, sent=sent, menu=0)
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "invoke_menu", "hwnd": 4242, "path": ["Edit"]}])

    assert result["ok"] is False
    assert "no Win32 menu" in result["results"][0]["data"]["error"]
    assert sent == []


def test_invoke_menu_reports_a_command_whose_effect_is_not_observable_as_unconfirmed(monkeypatch):
    """A delivered command with no visible change is not claimed as success."""
    sent = []
    _patch_menu(monkeypatch, sent=sent, titles=["Target"] * 4)
    controller = FastGuiController(raw_device=RecordingRawDevice())
    # The settle window is a real wait; this case is about what is reported when
    # nothing ever changes, so it is not spent here.
    controller.menu_settle_timeout_s = 0.0

    result = controller.execute([{"action": "invoke_menu", "hwnd": 4242, "path": ["Edit", "Save As"]}])

    assert result["ok"] is True
    step = result["results"][0]["data"]
    assert step["delivered"] is True
    assert step["action_state"] == "unconfirmed"
    assert step["verified"] is False
    assert step["changed"] == []


def test_invoke_menu_waits_for_an_effect_that_lands_after_the_command(monkeypatch):
    """An application may act a moment later; that must not read as unconfirmed."""
    sent = []
    # The first read on each side agrees, then the window's text length moves.
    lengths = iter([7, 7, 7, 9])
    _patch_menu(monkeypatch, sent=sent)
    import winpilot.fast.controller as controller_mod

    monkeypatch.setattr(controller_mod, "_menu_read_text_length", lambda _hwnd: next(lengths, 9))
    controller = FastGuiController(raw_device=RecordingRawDevice())
    controller.menu_settle_timeout_s = 1.0

    result = controller.execute([{"action": "invoke_menu", "hwnd": 4242, "path": ["Edit", "Save As"]}])

    assert result["ok"] is True
    step = result["results"][0]["data"]
    assert step["action_state"] == "changed"
    assert step["changed"] == ["text_length"]
    assert step["after"]["text_length"] == 9


def test_invoke_menu_reports_what_the_application_changed(monkeypatch):
    """The window reacting to the command is observable and reported as such."""
    sent = []
    _patch_menu(monkeypatch, sent=sent, titles=["Untitled", "Find"])
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "invoke_menu", "hwnd": 4242, "path": ["Edit", "Save As"]}])

    assert result["ok"] is True
    step = result["results"][0]["data"]
    assert step["action_state"] == "changed"
    assert step["verified"] is True
    assert step["changed"] == ["title"]
    assert step["before"]["title"] == "Untitled"
    assert step["after"]["title"] == "Find"


def test_invoke_menu_flags_a_disabled_item_instead_of_calling_it_done(monkeypatch):
    sent = []
    _patch_menu(monkeypatch, sent=sent)
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "invoke_menu", "hwnd": 4242, "path": ["Edit", "Copy"]}])

    # The command is still sent: only the application knows whether it will act.
    assert len(sent) == 1
    step = result["results"][0]["data"]
    assert step["enabled"] is False
    assert "is disabled" in step["note"]


def test_invoke_menu_reports_a_command_that_could_not_be_delivered(monkeypatch):
    _patch_menu(monkeypatch)
    import winpilot.fast.controller as controller_mod

    monkeypatch.setattr(controller_mod._user32, "SendMessageTimeoutW", lambda *a, **k: 0)
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "invoke_menu", "hwnd": 4242, "path": ["Edit", "Save As"]}])

    assert result["ok"] is False
    step = result["results"][0]["data"]
    assert step["delivered"] is False
    assert "could not be delivered" in step["error"]


def test_invoke_menu_is_not_offered_as_a_replay_step(monkeypatch):
    """A redacted-or-unknown step must never become an executable replay."""
    import winpilot.fast.controller as controller_mod

    events = []
    monkeypatch.setattr(controller_mod.BUS, "publish", lambda event_type, **data: events.append({"type": event_type, **data}))
    _patch_menu(monkeypatch)
    controller = FastGuiController(raw_device=RecordingRawDevice())

    result = controller.execute([{"action": "invoke_menu", "hwnd": 4242, "path": ["Edit", "Save As"]}])

    assert result["ok"] is True
    done = next(event for event in events if event["type"] == "agent_batch_action")
    assert done["args"]["path"] == ["Edit", "Save As"]
    assert done["replay"] is None


def test_a_bounded_event_preview_stays_parseable_and_keeps_the_alternatives(monkeypatch):
    """A refusal a caller cannot parse is a refusal it cannot act on.

    The BUS record is bounded, and a wholesale character cut would land inside
    the list of alternatives. What must survive is a record that parses and
    still names the choices.
    """
    import winpilot.fast.controller as controller_mod

    events = []
    monkeypatch.setattr(controller_mod.BUS, "publish", lambda event_type, **data: events.append({"type": event_type, **data}))
    # A menu with many long entries, so the projection has something to drop.
    crowded = {
        _MENU_ROOT: list(_MENU_TREE[_MENU_ROOT]),
        901: list(_MENU_TREE[901]) + [
            _menu_entry(f"&A very long menu entry number {index} that goes on", 500 + index)
            for index in range(30)
        ],
        902: list(_MENU_TREE[902]),
        903: list(_MENU_TREE[903]),
    }
    _patch_menu(monkeypatch, tree=crowded)
    controller = FastGuiController(raw_device=RecordingRawDevice())
    # This case is about the record's shape, not about waiting for an effect.
    controller.menu_settle_timeout_s = 0.0

    result = controller.execute([{"action": "invoke_menu", "hwnd": 4242, "path": ["Edit", "Not There"]}])

    assert result["ok"] is False
    done = next(event for event in events if event["type"] == "agent_batch_action")
    preview = done["result_preview"]
    # 800 is what the run timeline allows before it compacts an event summary;
    # the record must survive that cut intact, not merely be small.
    assert len(preview) <= 600
    decoded = json.loads(preview)
    assert decoded["ok"] is False
    assert "Not There" in decoded["error"]
    assert decoded["available"], "the alternatives must survive into the record"
    assert all("text" in entry for entry in decoded["available"])
    # The list is what gives way when the record does not fit, and the caller is
    # told that it was shortened rather than being left to assume it saw all.
    assert decoded["available_truncated"] > 0
