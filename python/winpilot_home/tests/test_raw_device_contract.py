from __future__ import annotations

import ast
import json
import math
from pathlib import Path
import random

import pytest

from winpilot.action.raw_device import (
    AdmittedPhysicalClickBackend,
    ClickCount,
    ClickMechanism,
    FinalClickGuardDecision,
    MouseButton,
    RawActionContext,
    RawActionReceipt,
    RawBackendResult,
    RawDeviceBackends,
    RawDeviceExecutor,
)
from winpilot.action.replay_policy import MutationOutcome
from winpilot.fast.legacy_session_actions import LegacyToolSessionActionAdapter


def _context(*, target: int = 41, current: int = 41) -> RawActionContext:
    return RawActionContext(target_hwnd=target, current_hwnd=current)


def _executor(calls: list, *, completed: bool | None = True) -> RawDeviceExecutor:
    def backend(x, y, amount, *, hwnd=None):
        calls.append((x, y, amount, hwnd))
        return RawBackendResult(completed, {"via": "test"})

    return RawDeviceExecutor(RawDeviceBackends(scroll=backend))


def test_fast_controller_names_reflective_session_adapter_as_legacy() -> None:
    from winpilot.fast.controller import FastGuiController

    class Session:
        snapshot = None
        hwnd = 41

    assert isinstance(
        FastGuiController(Session()).raw_device,
        LegacyToolSessionActionAdapter,
    )


def test_raw_device_requires_an_explicit_typed_backend() -> None:
    with pytest.raises(TypeError, match="backends"):
        RawDeviceExecutor()
    with pytest.raises(TypeError, match="RawDeviceBackends"):
        RawDeviceExecutor(None)
    with pytest.raises(TypeError, match="callable"):
        RawDeviceBackends(scroll=object())


@pytest.mark.parametrize(
    ("context", "args", "error"),
    [
        (object(), (1, 2, -1), "invalid_context"),
        (RawActionContext(0, 0), (1, 2, -1), "invalid_context"),
        (_context(target=41, current=42), (1, 2, -1), "target_mismatch"),
        (_context(), (True, 2, -1), "invalid_args"),
        (_context(), (1.5, 2, -1), "invalid_args"),
        (_context(), (1, "2", -1), "invalid_args"),
        (_context(), (1, 2, 0), "invalid_args"),
    ],
)
def test_scroll_rejects_invalid_typed_input_before_backend(
    context, args, error,
) -> None:
    calls: list = []

    receipt = _executor(calls).scroll(context, *args)

    assert receipt.outcome is MutationOutcome.NOT_STARTED
    assert receipt.input_completed is False
    assert receipt.error_kind == error
    assert calls == []


@pytest.mark.parametrize(
    ("completed", "outcome"),
    [
        (True, MutationOutcome.APPLIED),
        (False, MutationOutcome.NOT_APPLIED),
        (None, MutationOutcome.OUTCOME_UNKNOWN),
    ],
)
def test_scroll_uses_explicit_backend_completion_fact(
    completed, outcome,
) -> None:
    calls: list = []

    receipt = _executor(calls, completed=completed).scroll(
        _context(), -200, 300, 4,
    )

    assert calls == [(-200, 300, 4, 41)]
    assert receipt.outcome is outcome
    assert receipt.input_completed is completed
    assert receipt.backend_result.facts == {"via": "test"}
    assert receipt.error_kind == (
        "backend_result_uncertain" if completed is None else None
    )
    assert json.loads(json.dumps(receipt.to_dict()))["input_completed"] is completed


def test_generic_ok_mapping_cannot_claim_input_completion() -> None:
    calls: list = []
    executor = RawDeviceExecutor(RawDeviceBackends(
        scroll=lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True},
    ))

    receipt = executor.scroll(_context(), 1, 2, -1)

    assert len(calls) == 1
    assert receipt.outcome is MutationOutcome.OUTCOME_UNKNOWN
    assert receipt.input_completed is None
    assert receipt.backend_result is None


def test_backend_exception_after_possible_input_is_unknown_and_not_replayed() -> None:
    calls: list = []

    def backend(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("injected post-admission failure")

    receipt = RawDeviceExecutor(RawDeviceBackends(scroll=backend)).scroll(
        _context(), 1, 2, -3,
    )

    assert len(calls) == 1
    assert receipt.outcome is MutationOutcome.OUTCOME_UNKNOWN
    assert receipt.input_completed is None
    assert receipt.error_kind == "backend_exception"


def test_invalid_post_call_fact_is_unknown_instead_of_escaping() -> None:
    calls: list = []

    def backend(*args, **kwargs):
        calls.append((args, kwargs))
        return RawBackendResult(True, {"invalid": object()})

    receipt = RawDeviceExecutor(RawDeviceBackends(scroll=backend)).scroll(
        _context(), 1, 2, -3,
    )

    assert len(calls) == 1
    assert receipt.outcome is MutationOutcome.OUTCOME_UNKNOWN
    assert receipt.error_kind == "backend_exception"


def test_backend_facts_are_scalar_finite_and_immutable() -> None:
    source = {"ok": True, "ratio": 1.5}
    result = RawBackendResult(True, source)
    source["ok"] = False

    assert result.facts == {"ok": True, "ratio": 1.5}
    with pytest.raises(TypeError):
        result.facts["ok"] = False
    with pytest.raises(TypeError, match="scalar"):
        RawBackendResult(True, {"nested": {"ok": True}})
    with pytest.raises(TypeError, match="finite"):
        RawBackendResult(True, {"ratio": math.nan})


def test_receipt_rejects_contradictory_completion_identity() -> None:
    with pytest.raises(ValueError, match="requires backend_result"):
        RawActionReceipt("scroll", MutationOutcome.APPLIED)
    with pytest.raises(ValueError, match="backend_result contradicts"):
        RawActionReceipt(
            "scroll",
            MutationOutcome.OUTCOME_UNKNOWN,
            RawBackendResult(True, {"ok": True}),
            "uncertain",
        )
    with pytest.raises(ValueError, match="backend_result contradicts"):
        RawActionReceipt(
            "scroll",
            MutationOutcome.APPLIED,
            RawBackendResult(False, {"ok": False}),
        )
    with pytest.raises(ValueError, match="requires error_kind"):
        RawActionReceipt("scroll", MutationOutcome.NOT_STARTED)
    with pytest.raises(ValueError, match="requires error_kind"):
        RawActionReceipt("scroll", MutationOutcome.OUTCOME_UNKNOWN)


def test_raw_device_has_no_session_or_second_action_parser() -> None:
    import winpilot.action.raw_device as raw_device

    path = Path(raw_device.__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")

    assert all("agent" not in module for module in imported)
    assert "ToolSession" not in source
    assert ".dispatch(" not in source
    assert not hasattr(RawDeviceExecutor, "execute")


def test_scroll_property_preserves_signed_coordinates_and_single_call() -> None:
    rng = random.Random(20260722)
    calls: list = []
    executor = _executor(calls)
    expected = []
    for _ in range(100):
        args = (
            rng.randint(-5000, 5000),
            rng.randint(-3000, 3000),
            rng.choice(tuple(range(-12, 0)) + tuple(range(1, 13))),
        )
        expected.append((*args, 41))
        assert executor.scroll(_context(), *args).outcome is MutationOutcome.APPLIED

    assert calls == expected


def test_click_uses_typed_single_mechanism_backend() -> None:
    calls: list = []

    def backend(x, y, *, button, count, mechanism, hwnd=None):
        calls.append((x, y, button, count, mechanism, hwnd))
        return RawBackendResult(True, {"via": mechanism.value})

    receipt = RawDeviceExecutor(RawDeviceBackends(click=backend)).click(
        _context(),
        -12,
        34,
        MouseButton.RIGHT,
        ClickCount.DOUBLE,
        ClickMechanism.PHYSICAL,
    )

    assert calls == [(
        -12, 34, MouseButton.RIGHT, ClickCount.DOUBLE,
        ClickMechanism.PHYSICAL, 41,
    )]
    assert receipt.action == "click"
    assert receipt.outcome is MutationOutcome.APPLIED
    assert receipt.input_completed is True


@pytest.mark.parametrize(
    ("context", "x", "y", "button", "count", "mechanism", "error"),
    [
        (object(), 1, 2, MouseButton.LEFT, ClickCount.SINGLE,
         ClickMechanism.PHYSICAL, "invalid_context"),
        (_context(target=41, current=42), 1, 2, MouseButton.LEFT,
         ClickCount.SINGLE, ClickMechanism.PHYSICAL, "target_mismatch"),
        (_context(), True, 2, MouseButton.LEFT, ClickCount.SINGLE,
         ClickMechanism.PHYSICAL, "invalid_args"),
        (_context(), 1, 2, "left", ClickCount.SINGLE,
         ClickMechanism.PHYSICAL, "invalid_args"),
        (_context(), 1, 2, MouseButton.LEFT, 1,
         ClickMechanism.PHYSICAL, "invalid_args"),
        (_context(), 1, 2, MouseButton.LEFT, ClickCount.SINGLE,
         "physical", "invalid_args"),
    ],
)
def test_click_rejects_untyped_or_stale_input_before_backend(
    context, x, y, button, count, mechanism, error,
) -> None:
    calls: list = []
    executor = RawDeviceExecutor(RawDeviceBackends(
        click=lambda *args, **kwargs: calls.append((args, kwargs)),
    ))

    receipt = executor.click(context, x, y, button, count, mechanism)

    assert receipt.outcome is MutationOutcome.NOT_STARTED
    assert receipt.error_kind == error
    assert calls == []


def test_missing_click_backend_fails_before_input() -> None:
    receipt = RawDeviceExecutor(RawDeviceBackends()).click(
        _context(), 1, 2, MouseButton.LEFT, ClickCount.SINGLE,
        ClickMechanism.PHYSICAL,
    )

    assert receipt.outcome is MutationOutcome.NOT_STARTED
    assert receipt.error_kind == "backend_unavailable"


def test_final_guard_uses_only_explicit_admitted_backend() -> None:
    normal_calls, admitted_calls = [], []

    def admitted(x, y, *, final_guard, **kwargs):
        admitted_calls.append((x, y, kwargs))
        assert final_guard(x, y) is FinalClickGuardDecision.ADMITTED
        return RawBackendResult(True, {"guarded": True})

    raw = RawDeviceExecutor(RawDeviceBackends(
        click=lambda *args, **kwargs: normal_calls.append((args, kwargs)),
        admitted_physical_click=AdmittedPhysicalClickBackend(admitted),
    ))
    receipt = raw.click(
        _context(), 10, 20, MouseButton.LEFT, ClickCount.SINGLE,
        ClickMechanism.PHYSICAL,
        final_guard=lambda _x, _y: FinalClickGuardDecision.ADMITTED,
    )

    assert receipt.outcome is MutationOutcome.APPLIED
    assert len(admitted_calls) == 1
    assert normal_calls == []

    unavailable = RawDeviceExecutor(RawDeviceBackends()).click(
        _context(), 10, 20, MouseButton.LEFT, ClickCount.SINGLE,
        ClickMechanism.PHYSICAL,
        final_guard=lambda _x, _y: FinalClickGuardDecision.ADMITTED,
    )
    assert unavailable.outcome is MutationOutcome.NOT_STARTED
    assert unavailable.error_kind == "backend_unavailable"


def _physical_executor_mocks(monkeypatch, events):
    from winpilot.action import executor

    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: False)
    monkeypatch.setattr(executor, "_target_window_still_foreground", lambda _hwnd: True)
    monkeypatch.setattr(executor, "_raw_move_mouse", lambda x, y: events.append(("move", x, y)))
    monkeypatch.setattr(executor, "_mouse_input", lambda flags, **_kwargs: flags)
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: events.append(("input", inputs[0])))
    monkeypatch.setattr(executor, "_sample_pressed_state", lambda: False)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)
    return executor


def test_admitted_backend_guards_immediately_before_down_without_queue(monkeypatch):
    events = []
    executor = _physical_executor_mocks(monkeypatch, events)
    from winpilot.action import desktop_queue

    monkeypatch.setattr(
        desktop_queue.DESKTOP_ACTION_QUEUE,
        "run",
        lambda *args, **kwargs: pytest.fail("admitted backend queued a second time"),
    )

    def guard(x, y):
        assert (x, y) == (10, 20)
        events.append(("guard",))
        return FinalClickGuardDecision.ADMITTED

    result = executor.ADMITTED_PHYSICAL_CLICK_BACKEND(
        10, 20, button=MouseButton.LEFT, count=ClickCount.SINGLE,
        mechanism=ClickMechanism.PHYSICAL, hwnd=41, final_guard=guard,
    )

    assert result.input_completed is True
    assert events[-3:] == [
        ("guard",),
        ("input", executor.win32con.MOUSEEVENTF_LEFTDOWN),
        ("input", executor.win32con.MOUSEEVENTF_LEFTUP),
    ]
    assert executor.ADMITTED_PHYSICAL_CLICK_BACKEND.execute is (
        executor.execute_admitted_physical_click
    )
    assert not hasattr(executor.execute_admitted_physical_click, "__wrapped__")
    assert hasattr(executor.execute_click_input, "__wrapped__")
    with pytest.raises(TypeError, match="decorated"):
        AdmittedPhysicalClickBackend(executor.execute_click_input)


def test_final_guard_failure_is_certain_before_first_down_and_unknown_after_partial(
    monkeypatch,
):
    events = []
    executor = _physical_executor_mocks(monkeypatch, events)
    decisions = iter((
        FinalClickGuardDecision.ADMITTED,
        FinalClickGuardDecision.HIT_TEST_CHANGED,
    ))

    result = executor.ADMITTED_PHYSICAL_CLICK_BACKEND(
        10, 20, button=MouseButton.LEFT, count=ClickCount.DOUBLE,
        mechanism=ClickMechanism.PHYSICAL, hwnd=41,
        final_guard=lambda _x, _y: next(decisions),
    )

    assert result.input_completed is None
    assert result.facts["completed_clicks"] == 1
    assert result.facts["via"] == "final_target_guard"
    assert [event for event in events if event[0] == "input"] == [
        ("input", executor.win32con.MOUSEEVENTF_LEFTDOWN),
        ("input", executor.win32con.MOUSEEVENTF_LEFTUP),
    ]

    events.clear()
    rejected = executor.ADMITTED_PHYSICAL_CLICK_BACKEND(
        10, 20, button=MouseButton.LEFT, count=ClickCount.SINGLE,
        mechanism=ClickMechanism.PHYSICAL, hwnd=41,
        final_guard=lambda _x, _y: FinalClickGuardDecision.TARGET_ROOT_CHANGED,
    )
    assert rejected.input_completed is False
    assert not [event for event in events if event[0] == "input"]


@pytest.mark.parametrize(
    ("fail_at", "completed_clicks"),
    [(1, 0), (2, 0), (3, 1)],
)
def test_post_click_partial_failure_is_unknown_and_releases(
    monkeypatch, fail_at: int, completed_clicks: int,
) -> None:
    from winpilot.action import executor

    calls: list[tuple] = []

    def post_message(*args):
        calls.append(args)
        if len(calls) == fail_at:
            raise OSError("injected partial message failure")

    monkeypatch.setattr(executor.win32gui, "WindowFromPoint", lambda point: 41)
    monkeypatch.setattr(executor, "_same_window_root", lambda target, hwnd: True)
    monkeypatch.setattr(
        executor.win32gui, "ScreenToClient", lambda target, point: point,
    )
    monkeypatch.setattr(executor.win32gui, "PostMessage", post_message)
    monkeypatch.setattr(executor, "_sample_pressed_state", lambda: False)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    result = executor._post_click(
        10, 20, MouseButton.LEFT, ClickCount.DOUBLE, expected_hwnd=41,
    )

    assert result.input_completed is None
    assert result.facts["completed_clicks"] == completed_clicks
    assert result.facts["release_cleanup_attempted"] is True
    assert result.facts["release_cleanup_completed"] is True
    assert len(calls) == fail_at + 1


@pytest.mark.parametrize(
    ("fail_at", "completed_clicks"),
    [(1, 0), (2, 0), (3, 1)],
)
def test_physical_click_partial_failure_is_unknown_and_releases(
    monkeypatch, fail_at: int, completed_clicks: int,
) -> None:
    from winpilot.action import executor

    calls: list[list] = []

    def send_inputs(inputs):
        calls.append(inputs)
        if len(calls) == fail_at:
            raise OSError("injected partial SendInput failure")

    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: False)
    monkeypatch.setattr(executor, "_raw_move_mouse", lambda x, y: None)
    monkeypatch.setattr(executor, "_mouse_input", lambda flag: flag)
    monkeypatch.setattr(executor, "_send_inputs", send_inputs)
    monkeypatch.setattr(executor, "_sample_pressed_state", lambda: False)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    result = executor._raw_click(
        10, 20, MouseButton.LEFT, ClickCount.DOUBLE, None,
    )

    assert result.input_completed is None
    assert result.facts["completed_clicks"] == completed_clicks
    assert result.facts["release_cleanup_attempted"] is True
    assert result.facts["release_cleanup_completed"] is True
    assert len(calls) == fail_at + 1


def test_physical_click_uses_final_root_ownership_not_focus_helper_result(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    sent: list[list] = []
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: False)
    monkeypatch.setattr(executor, "_raw_focus_window", lambda hwnd: {"ok": False})
    monkeypatch.setattr(executor, "_target_window_still_foreground", lambda hwnd: True)
    monkeypatch.setattr(executor, "_raw_move_mouse", lambda x, y: None)
    monkeypatch.setattr(executor, "_mouse_input", lambda flag: flag)
    monkeypatch.setattr(executor, "_send_inputs", sent.append)
    monkeypatch.setattr(executor, "_sample_pressed_state", lambda: False)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    result = executor._raw_click(
        10, 20, MouseButton.LEFT, ClickCount.SINGLE, 41,
    )

    assert result.input_completed is True
    assert len(sent) == 2


def test_physical_click_rechecks_target_after_cursor_move(monkeypatch) -> None:
    from winpilot.action import executor

    ownership = iter((True, False))
    sent: list[list] = []
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: False)
    monkeypatch.setattr(executor, "_raw_focus_window", lambda hwnd: {"ok": True})
    monkeypatch.setattr(
        executor, "_target_window_still_foreground", lambda hwnd: next(ownership),
    )
    monkeypatch.setattr(executor, "_raw_move_mouse", lambda x, y: None)
    monkeypatch.setattr(executor, "_send_inputs", sent.append)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    result = executor._raw_click(
        10, 20, MouseButton.LEFT, ClickCount.SINGLE, 41,
    )

    assert result.input_completed is False
    assert result.facts["completed_clicks"] == 0
    assert sent == []


def test_strict_background_click_never_switches_mechanism(monkeypatch) -> None:
    from winpilot.action import executor

    posted: list[tuple] = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(
        executor,
        "_post_click",
        lambda *args, **kwargs: (
            posted.append((args, kwargs)) or RawBackendResult(False, {})
        ),
    )
    monkeypatch.setattr(
        executor,
        "_raw_click",
        lambda *args, **kwargs: pytest.fail("strict click changed mechanism"),
    )

    result = executor.execute_click_input(
        10, 20, MouseButton.LEFT, ClickCount.SINGLE,
        ClickMechanism.BACKGROUND_MESSAGE, hwnd=41,
    )

    assert result.input_completed is False
    assert len(posted) == 1


def test_legacy_click_unknown_never_falls_back_or_claims_success(monkeypatch) -> None:
    from winpilot.action import executor

    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: True)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: False)
    monkeypatch.setattr(
        executor,
        "_post_click",
        lambda *args, **kwargs: RawBackendResult(
            None, {"completed_clicks": 1, "via": "post_message"},
        ),
    )
    monkeypatch.setattr(
        executor,
        "_raw_click",
        lambda *args, **kwargs: pytest.fail("unknown click was replayed"),
    )

    result = executor.click(10, 20, double=True, hwnd=41)

    assert result["ok"] is False
    assert result["verified"] is False
    assert result["input_completed"] is None
    assert result["outcome_unknown"] is True
    assert result["completed_clicks"] == 1


def test_legacy_click_dry_run_keeps_preexisting_argument_behavior(monkeypatch) -> None:
    from winpilot.action import executor

    monkeypatch.setattr(executor, "_dry_run", lambda: True)

    assert executor.click(1, 2, button="legacy-invalid") == {
        "ok": True,
        "dry_run": True,
    }


def test_completed_click_is_not_replayed_when_telemetry_fails(monkeypatch) -> None:
    from winpilot.action import executor

    calls: list[str] = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: True)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: False)
    monkeypatch.setattr(
        executor,
        "_post_click",
        lambda *args, **kwargs: calls.append("post") or RawBackendResult(True, {}),
    )
    monkeypatch.setattr(
        executor,
        "_raw_click",
        lambda *args, **kwargs: pytest.fail("completed click was replayed"),
    )
    original_publish = executor.BUS.publish

    def publish(event_type, **payload):
        if event_type == "action":
            raise RuntimeError("injected telemetry failure")
        return original_publish(event_type, **payload)

    monkeypatch.setattr(executor.BUS, "publish", publish)

    assert executor.click(10, 20, hwnd=41) == {
        "ok": True,
        "x": 10,
        "y": 20,
        "via": "post_message",
    }
    assert calls == ["post"]


def test_legacy_scroll_output_and_exception_semantics_are_unchanged(monkeypatch) -> None:
    from winpilot.agent import interaction_tools
    from winpilot.agent.interaction_tools import InteractionToolMixin

    calls: list = []

    def backend(x, y, amount):
        calls.append((x, y, amount))
        return {"ok": True, "via": "legacy-test"}

    monkeypatch.setattr(interaction_tools.executor, "scroll", backend)

    class Session(InteractionToolMixin):
        _fmt = staticmethod(lambda data: json.dumps(data, ensure_ascii=False))

        def _action_hwnd(self):
            return 41

    result = Session()._t_scroll({"x": "-2", "y": "30", "amount": "-4"})

    assert calls == [(-2, 30, -4)]
    assert json.loads(result.text) == {"ok": True, "via": "legacy-test"}
    assert result.replay == {"tool": "scroll", "coords": [-2, 30], "amount": -4}

    class InjectedError(RuntimeError):
        pass

    monkeypatch.setattr(
        interaction_tools.executor,
        "scroll",
        lambda *args, **kwargs: (_ for _ in ()).throw(InjectedError("legacy error")),
    )
    with pytest.raises(InjectedError, match="legacy error"):
        Session()._t_scroll({"x": 1, "y": 2, "amount": -3})


def test_legacy_public_scroll_delegates_typed_single_queue_owner(monkeypatch) -> None:
    from winpilot.action import executor

    calls: list = []
    monkeypatch.setattr(
        executor,
        "execute_scroll_input",
        lambda x, y, amount, hwnd=None: (
            calls.append((x, y, amount, hwnd))
            or RawBackendResult(True, {"ok": True, "via": "typed"})
        ),
    )

    assert executor.scroll(1, 2, -3, hwnd=41) == {"ok": True, "via": "typed"}
    assert calls == [(1, 2, -3, 41)]


def test_postmessage_success_never_falls_back_when_action_telemetry_fails(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    posted: list = []
    sent: list = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: True)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: False)
    monkeypatch.setattr(executor.win32gui, "WindowFromPoint", lambda point: 41)
    monkeypatch.setattr(executor, "_same_window_root", lambda target, hwnd: True)
    monkeypatch.setattr(
        executor.win32gui,
        "PostMessage",
        lambda *args: posted.append(args),
    )
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.append(inputs))
    original_publish = executor.BUS.publish

    def publish(event_type, **payload):
        if event_type == "action":
            raise RuntimeError("injected telemetry failure")
        return original_publish(event_type, **payload)

    monkeypatch.setattr(executor.BUS, "publish", publish)

    result = executor.execute_scroll_input(1, 2, -3, hwnd=41)

    assert result.input_completed is True
    assert result.facts == {"ok": True, "via": "post_message"}
    assert len(posted) == 1
    assert sent == []


def test_postmessage_exception_is_unknown_and_never_falls_back(monkeypatch) -> None:
    from winpilot.action import executor

    sent: list = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_no_mouse_steal", lambda: True)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: False)
    monkeypatch.setattr(executor.win32gui, "WindowFromPoint", lambda point: 41)
    monkeypatch.setattr(executor, "_same_window_root", lambda target, hwnd: True)
    monkeypatch.setattr(
        executor.win32gui,
        "PostMessage",
        lambda *args: (_ for _ in ()).throw(OSError("uncertain post")),
    )
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.append(inputs))

    result = executor.execute_scroll_input(1, 2, -3, hwnd=41)

    assert result.input_completed is None
    assert result.facts["error"] == "post_message_outcome_unknown"
    assert sent == []
