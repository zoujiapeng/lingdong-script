from __future__ import annotations

import ast
import json
import math
from pathlib import Path
import random

import pytest

from winpilot.action.raw_device import (
    AdmittedPhysicalClickBackend,
    AdmittedPhysicalHotkeyBackend,
    AdmittedPhysicalScrollBackend,
    AdmittedPhysicalTypeTextBackend,
    CANONICAL_HOTKEY_KEYS,
    ClickCount,
    ClickMechanism,
    FinalKeyboardGuardDecision,
    FinalPointerGuardDecision,
    HotkeyChord,
    MAX_TYPE_TEXT_CODEPOINTS,
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
    with pytest.raises(TypeError, match="AdmittedPhysicalScrollBackend"):
        RawDeviceBackends(admitted_physical_scroll=object())
    with pytest.raises(TypeError, match="AdmittedPhysicalTypeTextBackend"):
        RawDeviceBackends(admitted_physical_type_text=object())
    with pytest.raises(TypeError, match="AdmittedPhysicalHotkeyBackend"):
        RawDeviceBackends(admitted_physical_hotkey=object())


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


def test_guarded_scroll_uses_only_explicit_admitted_backend() -> None:
    normal_calls, admitted_calls = [], []

    def admitted(x, y, amount, *, hwnd=None, final_guard):
        admitted_calls.append((x, y, amount, hwnd))
        assert final_guard(x, y) is FinalPointerGuardDecision.ADMITTED
        return RawBackendResult(True, {"guarded": True})

    raw = RawDeviceExecutor(RawDeviceBackends(
        scroll=lambda *args, **kwargs: normal_calls.append((args, kwargs)),
        admitted_physical_scroll=AdmittedPhysicalScrollBackend(admitted),
    ))
    receipt = raw.scroll(
        _context(), 10, 20, -3,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )

    assert receipt.outcome is MutationOutcome.APPLIED
    assert admitted_calls == [(10, 20, -3, 41)]
    assert normal_calls == []

    unavailable = RawDeviceExecutor(RawDeviceBackends()).scroll(
        _context(), 10, 20, -3,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )
    assert unavailable.outcome is MutationOutcome.NOT_STARTED
    assert unavailable.error_kind == "backend_unavailable"

    invalid_guard = raw.scroll(_context(), 10, 20, -3, final_guard=object())
    assert invalid_guard.outcome is MutationOutcome.NOT_STARTED
    assert invalid_guard.error_kind == "invalid_final_guard"
    assert admitted_calls == [(10, 20, -3, 41)]


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


_KEYBOARD_INPUTS = (
    ("type_text", "keyboard text"),
    ("hotkey", HotkeyChord(("ctrl", "s"))),
)
_KEYBOARD_BACKENDS = {
    "type_text": ("admitted_physical_type_text", AdmittedPhysicalTypeTextBackend),
    "hotkey": ("admitted_physical_hotkey", AdmittedPhysicalHotkeyBackend),
}


def _keyboard_raw(action: str, backend=None) -> RawDeviceExecutor:
    field, wrapper = _KEYBOARD_BACKENDS[action]
    value = None if backend is None else wrapper(backend)
    return RawDeviceExecutor(RawDeviceBackends(**{field: value}))


def _call_keyboard(raw, action, argument, guard=None):
    return getattr(raw, action)(
        _context(), argument,
        final_guard=guard or (lambda: FinalKeyboardGuardDecision.ADMITTED),
    )


@pytest.mark.parametrize("keys", [
    (), ("ctrl",), ("ctrl",) * 7, ("ctrl", "ctrl"), ("control", "s"),
    ("CTRL", "s"), (" ctrl", "s"), ("unknown",), (1,),
    ("s", "ctrl"), ("shift", "ctrl", "s"), ("ctrl", "s", "a"),
    ("ctrl", "alt"), ("a", "b"), "ctrl+s", ["ctrl", "s"], b"ctrl+s",
])
def test_hotkey_chord_rejects_noncanonical_or_ambiguous_keys(keys) -> None:
    with pytest.raises((TypeError, ValueError)):
        HotkeyChord(keys)


def test_hotkey_chord_accepts_canonical_modifier_prefix_and_one_primary() -> None:
    assert HotkeyChord(("enter",)).keys == ("enter",)
    chord = HotkeyChord(("ctrl", "alt", "shift", "win", "f12"))
    assert chord.keys == ("ctrl", "alt", "shift", "win", "f12")
    assert all(key in CANONICAL_HOTKEY_KEYS for key in chord.keys)


@pytest.mark.parametrize("action,argument", _KEYBOARD_INPUTS)
@pytest.mark.parametrize("context,error", [
    (object(), "invalid_context"),
    (_context(target=41, current=42), "target_mismatch"),
])
def test_keyboard_context_rejection_has_zero_backend_calls(
    action, argument, context, error,
) -> None:
    calls: list = []
    raw = _keyboard_raw(action, lambda *args, **kwargs: calls.append((args, kwargs)))
    receipt = getattr(raw, action)(
        context, argument,
        final_guard=lambda: FinalKeyboardGuardDecision.ADMITTED,
    )
    assert (receipt.outcome, receipt.error_kind, calls) == (
        MutationOutcome.NOT_STARTED, error, [])


@pytest.mark.parametrize("action,argument,guard", [
    ("type_text", "", None),
    ("type_text", "x" * (MAX_TYPE_TEXT_CODEPOINTS + 1), None),
    ("type_text", True, None),
    ("type_text", "a", object()),
    ("hotkey", ("ctrl", "s"), None),
    ("hotkey", "ctrl+s", None),
    ("hotkey", HotkeyChord(("ctrl", "s")), object()),
])
def test_keyboard_argument_rejection_has_zero_backend_calls(
    action, argument, guard,
) -> None:
    calls: list = []
    raw = _keyboard_raw(action, lambda *args, **kwargs: calls.append((args, kwargs)))
    receipt = _call_keyboard(raw, action, argument, guard)
    assert (receipt.outcome, receipt.error_kind, calls) == (
        MutationOutcome.NOT_STARTED, "invalid_args", [],
    )


@pytest.mark.parametrize("action,argument", _KEYBOARD_INPUTS)
def test_keyboard_actions_require_their_exact_admitted_backend(
    action, argument,
) -> None:
    receipt = _call_keyboard(_keyboard_raw(action), action, argument)
    assert (receipt.outcome, receipt.input_completed, receipt.error_kind) == (
        MutationOutcome.NOT_STARTED, False, "backend_unavailable",
    )


@pytest.mark.parametrize("action,argument", _KEYBOARD_INPUTS)
@pytest.mark.parametrize("completed,outcome", [
    (True, MutationOutcome.APPLIED),
    (False, MutationOutcome.NOT_APPLIED),
    (None, MutationOutcome.OUTCOME_UNKNOWN),
])
def test_keyboard_actions_map_backend_completion(
    action, argument, completed, outcome,
) -> None:
    calls: list = []

    def backend(value, *, hwnd, final_guard):
        calls.append((value, hwnd, final_guard()))
        return RawBackendResult(completed, {})

    receipt = _call_keyboard(_keyboard_raw(action, backend), action, argument)
    assert calls == [(argument, 41, FinalKeyboardGuardDecision.ADMITTED)]
    assert (receipt.outcome, receipt.input_completed) == (outcome, completed)
    assert receipt.error_kind == (
        "backend_result_uncertain" if completed is None else None
    )


@pytest.mark.parametrize("action,argument", _KEYBOARD_INPUTS)
def test_raw_device_filters_malicious_keyboard_backend_content_facts(
    action, argument,
) -> None:
    sentinel = "RAW-KEYBOARD-SENTINEL-7c54"
    safe_fact = ("requested_codepoints", 26) if action == "type_text" else (
        "key_count", 2,
    )

    def backend(*_args, **_kwargs):
        return RawBackendResult(True, {
            "ok": True, "via": "physical_sendinput", safe_fact[0]: safe_fact[1],
            "text": sentinel, "echo": sentinel, "error": sentinel,
        })

    receipt = _call_keyboard(_keyboard_raw(action, backend), action, argument)
    assert receipt.outcome is MutationOutcome.APPLIED
    assert sentinel not in json.dumps(receipt.to_dict(), sort_keys=True)
    assert receipt.backend_result.facts[safe_fact[0]] == safe_fact[1]


@pytest.mark.parametrize("action,argument", _KEYBOARD_INPUTS)
def test_keyboard_backend_exception_is_unknown_and_never_replayed(
    action, argument,
) -> None:
    calls: list = []

    def backend(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("injected uncertain keyboard input")

    receipt = _call_keyboard(_keyboard_raw(action, backend), action, argument)
    assert len(calls) == 1
    assert (receipt.outcome, receipt.input_completed, receipt.error_kind) == (
        MutationOutcome.OUTCOME_UNKNOWN, None, "backend_exception",
    )


def test_keyboard_raw_device_property_preserves_values_and_single_calls() -> None:
    rng = random.Random(20260723)
    modifiers = ("ctrl", "alt", "shift", "win")
    primaries = sorted(CANONICAL_HOTKEY_KEYS - set(modifiers))
    for _ in range(50):
        text = "".join(rng.choice("aZ09 汉") for _ in range(rng.randint(1, 40)))
        keys = (*(key for key in modifiers if rng.choice((False, True))),
                rng.choice(primaries))
        for action, value in (("type_text", text), ("hotkey", HotkeyChord(keys))):
            calls: list = []
            backend = lambda item, **kwargs: (
                calls.append((item, kwargs["hwnd"])) or RawBackendResult(True, {})
            )
            assert _call_keyboard(
                _keyboard_raw(action, backend), action, value,
            ).outcome is MutationOutcome.APPLIED
            assert calls == [(value, 41)]


def test_final_guard_uses_only_explicit_admitted_backend() -> None:
    normal_calls, admitted_calls = [], []

    def admitted(x, y, *, final_guard, **kwargs):
        admitted_calls.append((x, y, kwargs))
        assert final_guard(x, y) is FinalPointerGuardDecision.ADMITTED
        return RawBackendResult(True, {"guarded": True})

    raw = RawDeviceExecutor(RawDeviceBackends(
        click=lambda *args, **kwargs: normal_calls.append((args, kwargs)),
        admitted_physical_click=AdmittedPhysicalClickBackend(admitted),
    ))
    receipt = raw.click(
        _context(), 10, 20, MouseButton.LEFT, ClickCount.SINGLE,
        ClickMechanism.PHYSICAL,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )

    assert receipt.outcome is MutationOutcome.APPLIED
    assert len(admitted_calls) == 1
    assert normal_calls == []

    unavailable = RawDeviceExecutor(RawDeviceBackends()).click(
        _context(), 10, 20, MouseButton.LEFT, ClickCount.SINGLE,
        ClickMechanism.PHYSICAL,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )
    assert unavailable.outcome is MutationOutcome.NOT_STARTED
    assert unavailable.error_kind == "backend_unavailable"


def _physical_executor_mocks(monkeypatch, events):
    from winpilot.action import executor

    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: False)
    monkeypatch.setattr(executor, "_target_window_still_foreground", lambda _hwnd: True)
    monkeypatch.setattr(executor, "_raw_move_mouse", lambda x, y: events.append(("move", x, y)))
    monkeypatch.setattr(executor, "_cursor_pos", lambda: (10, 20))
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
        return FinalPointerGuardDecision.ADMITTED

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


@pytest.mark.parametrize("amount", (-12, -1, 1, 12))
def test_admitted_scroll_is_single_mechanism_unqueued_and_guarded(
    monkeypatch, amount,
):
    from winpilot.action import desktop_queue
    from winpilot.action.desktop_queue import DesktopActionQueue

    events = []
    executor = _physical_executor_mocks(monkeypatch, events)
    monkeypatch.setattr(
        executor,
        "_mouse_input",
        lambda flags, **kwargs: (flags, kwargs.get("data")),
    )
    monkeypatch.setattr(
        executor,
        "_restore_cursor_after_agent_input",
        lambda saved, expected, hwnd: (
            events.append(("restore", saved, expected, hwnd)) or True
        ),
    )
    monkeypatch.setattr(
        desktop_queue.DESKTOP_ACTION_QUEUE,
        "run",
        lambda *args, **kwargs: pytest.fail("admitted scroll queued a second time"),
    )

    def guard(x, y):
        events.append(("guard", x, y))
        return FinalPointerGuardDecision.ADMITTED

    outer = DesktopActionQueue()
    result = outer.run(
        "scroll",
        lambda: executor.ADMITTED_PHYSICAL_SCROLL_BACKEND(
            10, 20, amount, hwnd=41, final_guard=guard,
        ),
        target_hwnd=41,
        scope="physical",
    )

    assert result.input_completed is True
    assert [event[0] for event in events] == ["move", "guard", "input", "restore"]
    assert events[2] == (
        "input",
        (executor.win32con.MOUSEEVENTF_WHEEL, (amount * 120) & 0xFFFFFFFF),
    )
    assert outer.status()["submitted"] == 1
    assert executor.ADMITTED_PHYSICAL_SCROLL_BACKEND.execute is (
        executor.execute_admitted_physical_scroll
    )
    assert not hasattr(executor.execute_admitted_physical_scroll, "__wrapped__")
    assert hasattr(executor.execute_scroll_input, "__wrapped__")
    with pytest.raises(TypeError, match="decorated"):
        AdmittedPhysicalScrollBackend(executor.execute_scroll_input)


def test_admitted_scroll_guard_rejects_before_wheel(monkeypatch):
    events = []
    executor = _physical_executor_mocks(monkeypatch, events)
    monkeypatch.setattr(
        executor,
        "_restore_cursor_after_agent_input",
        lambda *_args: True,
    )

    result = executor.ADMITTED_PHYSICAL_SCROLL_BACKEND(
        10, 20, 2, hwnd=41,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.HIT_TEST_CHANGED,
    )

    assert result.input_completed is False
    assert result.facts["via"] == "final_target_guard"
    assert "hit_test_changed" in result.facts["error"]
    assert not [event for event in events if event[0] == "input"]


@pytest.mark.parametrize("amount", (0, True, 1.5, "-3"))
def test_admitted_scroll_rejects_untyped_or_zero_amount_before_input(
    monkeypatch, amount,
) -> None:
    events = []
    executor = _physical_executor_mocks(monkeypatch, events)

    result = executor.execute_admitted_physical_scroll(
        10, 20, amount, hwnd=41,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )

    assert result.input_completed is False
    assert result.facts["error"] == "invalid admitted physical scroll"
    assert events == []


def test_admitted_scroll_send_exception_is_unknown_without_fallback(monkeypatch):
    events = []
    executor = _physical_executor_mocks(monkeypatch, events)
    monkeypatch.setattr(
        executor,
        "_restore_cursor_after_agent_input",
        lambda *_args: True,
    )

    def uncertain_send(inputs):
        events.append(("input_attempt", len(inputs)))
        raise OSError("injected uncertain SendInput")

    monkeypatch.setattr(executor, "_send_inputs", uncertain_send)
    receipt = RawDeviceExecutor(RawDeviceBackends(
        admitted_physical_scroll=executor.ADMITTED_PHYSICAL_SCROLL_BACKEND,
    )).scroll(
        _context(), 10, 20, -4,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )

    assert receipt.outcome is MutationOutcome.OUTCOME_UNKNOWN
    assert receipt.input_completed is None
    assert receipt.backend_result.facts["error"] == "scroll wheel outcome unknown"
    assert [event for event in events if event[0] == "input_attempt"] == [
        ("input_attempt", 1),
    ]


def test_admitted_scroll_telemetry_failure_cannot_change_completion(monkeypatch):
    events = []
    executor = _physical_executor_mocks(monkeypatch, events)
    monkeypatch.setattr(
        executor,
        "_restore_cursor_after_agent_input",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        executor.BUS,
        "publish",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected telemetry failure")
        ),
    )

    result = executor.ADMITTED_PHYSICAL_SCROLL_BACKEND(
        10, 20, -2, hwnd=41,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )

    assert result.input_completed is True
    assert len([event for event in events if event[0] == "input"]) == 1


def test_final_guard_failure_is_certain_before_first_down_and_unknown_after_partial(
    monkeypatch,
):
    events = []
    executor = _physical_executor_mocks(monkeypatch, events)
    decisions = iter((
        FinalPointerGuardDecision.ADMITTED,
        FinalPointerGuardDecision.HIT_TEST_CHANGED,
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
        final_guard=lambda _x, _y: FinalPointerGuardDecision.TARGET_ROOT_CHANGED,
    )
    assert rejected.input_completed is False
    assert not [event for event in events if event[0] == "input"]


def test_admitted_backend_rejects_pointer_motion_during_final_guard(monkeypatch):
    events = []
    executor = _physical_executor_mocks(monkeypatch, events)
    positions = iter(((10, 20), (11, 20)))
    monkeypatch.setattr(executor, "_cursor_pos", lambda: next(positions))

    result = executor.ADMITTED_PHYSICAL_CLICK_BACKEND(
        10, 20, button=MouseButton.LEFT, count=ClickCount.SINGLE,
        mechanism=ClickMechanism.PHYSICAL, hwnd=41,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )

    assert result.input_completed is False
    assert result.facts["via"] == "final_target_guard"
    assert "pointer_position_changed" in result.facts["error"]
    assert not [event for event in events if event[0] == "input"]


def _keyboard_executor_mocks(monkeypatch):
    from winpilot.action import executor

    sent: list[tuple] = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)
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
        "_vk_of",
        lambda key: {"ctrl": 0x11, "shift": 0x10, "s": 0x53}[key],
    )
    return executor, sent


def test_admitted_type_text_guards_each_character_and_never_queues(monkeypatch):
    from winpilot.action import desktop_queue

    executor, sent = _keyboard_executor_mocks(monkeypatch)
    guards: list[int] = []
    monkeypatch.setattr(
        desktop_queue.DESKTOP_ACTION_QUEUE,
        "run",
        lambda *args, **kwargs: pytest.fail("admitted type_text queued twice"),
    )

    result = executor.ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND(
        "ab", hwnd=41,
        final_guard=lambda: (
            guards.append(len(sent)) or FinalKeyboardGuardDecision.ADMITTED
        ),
    )

    assert result.input_completed is True
    assert guards[0] == 0
    assert guards.count(1) >= 1
    assert guards[-1] == 2
    assert len(sent) == 2
    assert "ab" not in json.dumps(dict(result.facts), sort_keys=True)
    assert executor.ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND.execute is (
        executor.execute_admitted_physical_type_text
    )
    assert not hasattr(executor.execute_admitted_physical_type_text, "__wrapped__")


def test_admitted_ascii_text_uses_distinct_vk_transitions(
    monkeypatch,
) -> None:
    executor, sent = _keyboard_executor_mocks(monkeypatch)
    monkeypatch.setattr(
        executor,
        "_should_type_via_vk",
        lambda _text: pytest.fail(
            "strict admitted input used the legacy text selector"
        ),
    )
    monkeypatch.setattr(
        executor,
        "_vk_character_inputs",
        lambda char: (
            [("down", char)],
            [("up", char)],
        ),
    )

    result = executor.ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND(
        "CANARY-DELTA-43",
        hwnd=41,
        final_guard=lambda: FinalKeyboardGuardDecision.ADMITTED,
    )

    assert result.input_completed is True
    assert len(sent) == len("CANARY-DELTA-43")
    assert executor._should_strict_type_via_vk("CANARY-DELTA-43") is True
    assert executor._should_strict_type_via_vk("中文") is False


@pytest.mark.parametrize("backend_name,argument", [
    ("ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND", "secret"),
    ("ADMITTED_PHYSICAL_HOTKEY_BACKEND", HotkeyChord(("ctrl", "s"))),
])
def test_admitted_keyboard_guard_rejects_before_any_input(
    monkeypatch, backend_name, argument,
) -> None:
    executor, sent = _keyboard_executor_mocks(monkeypatch)
    result = getattr(executor, backend_name)(
        argument, hwnd=41,
        final_guard=lambda: FinalKeyboardGuardDecision.FOREGROUND_CHANGED,
    )
    assert result.input_completed is False
    assert sent == []
    assert "secret" not in json.dumps(dict(result.facts), sort_keys=True)


def test_admitted_type_text_guard_rejection_after_partial_input_is_unknown(
    monkeypatch,
) -> None:
    executor, sent = _keyboard_executor_mocks(monkeypatch)
    decisions = iter((
        FinalKeyboardGuardDecision.ADMITTED,
        FinalKeyboardGuardDecision.FOREGROUND_CHANGED,
    ))

    result = executor.ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND(
        "ab", hwnd=41, final_guard=lambda: next(decisions),
    )

    assert result.input_completed is None
    assert len(sent) == 1
    assert result.facts["completed_codepoints"] == 1


@pytest.mark.parametrize("fail_at", [1, 2])
def test_admitted_type_text_send_failure_is_unknown_without_replay(
    monkeypatch, fail_at,
) -> None:
    executor, sent = _keyboard_executor_mocks(monkeypatch)

    def fail_once(inputs):
        sent.append(tuple(inputs))
        if len(sent) == fail_at:
            raise OSError("injected uncertain SendInput")

    monkeypatch.setattr(executor, "_send_inputs", fail_once)
    result = executor.ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND(
        "ab", hwnd=41,
        final_guard=lambda: FinalKeyboardGuardDecision.ADMITTED,
    )

    assert result.input_completed is None
    assert len(sent) == fail_at + 1  # one best-effort key-up cleanup, no retry
    assert "ab" not in json.dumps(dict(result.facts), sort_keys=True)


def test_admitted_hotkey_sends_one_chord_and_always_releases_in_reverse(
    monkeypatch,
) -> None:
    from winpilot.action import desktop_queue

    executor, sent = _keyboard_executor_mocks(monkeypatch)
    guard_calls: list[None] = []
    monkeypatch.setattr(
        desktop_queue.DESKTOP_ACTION_QUEUE,
        "run",
        lambda *args, **kwargs: pytest.fail("admitted hotkey queued twice"),
    )

    result = executor.ADMITTED_PHYSICAL_HOTKEY_BACKEND(
        HotkeyChord(("ctrl", "shift", "s")), hwnd=41,
        final_guard=lambda: (
            guard_calls.append(None) or FinalKeyboardGuardDecision.ADMITTED
        ),
    )

    assert result.input_completed is True
    assert len(guard_calls) == 3  # before down, after down, after cleanup
    assert len(sent) == 4
    downs, *up_batches = sent
    ups = tuple(batch[0] for batch in up_batches)
    assert [dict(token[1])["vk"] for token in downs] == [0x11, 0x10, 0x53]
    assert [dict(token[1])["vk"] for token in ups] == [0x53, 0x10, 0x11]
    assert all(dict(token[1]).get("flags", 0) == 0 for token in downs)
    assert all(
        dict(token[1]).get("flags", 0) & executor._KEYEVENTF_KEYUP
        for token in ups
    )
    assert executor.ADMITTED_PHYSICAL_HOTKEY_BACKEND.execute is (
        executor.execute_admitted_physical_hotkey
    )
    assert not hasattr(executor.execute_admitted_physical_hotkey, "__wrapped__")


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_admitted_hotkey_send_failure_is_unknown_and_continues_reverse_cleanup(
    monkeypatch, fail_at,
) -> None:
    executor, sent = _keyboard_executor_mocks(monkeypatch)

    def fail_once(inputs):
        sent.append(tuple(inputs))
        if len(sent) == fail_at:
            raise OSError("injected uncertain chord SendInput")

    monkeypatch.setattr(executor, "_send_inputs", fail_once)
    result = executor.ADMITTED_PHYSICAL_HOTKEY_BACKEND(
        HotkeyChord(("ctrl", "s")), hwnd=41,
        final_guard=lambda: FinalKeyboardGuardDecision.ADMITTED,
    )

    assert result.input_completed is None
    assert len(sent) == 3
    assert [dict(batch[0][1])["vk"] for batch in sent[1:]] == [0x53, 0x11]


def test_legacy_keyboard_public_entries_delegate_to_shared_unqueued_owners(
    monkeypatch,
) -> None:
    from winpilot.action import desktop_queue, executor

    keys = ["*", "=", "ctrl", "ctrl", "a", "b", "c"]
    mapped = {"*": 0x6A, "=": 0xBB, "ctrl": 0x11,
              "a": 0x41, "b": 0x42, "c": 0x43}
    queued: list[tuple] = []
    typed: list[tuple] = []
    hotkey_vks: list[tuple] = []

    def run(action, fn, **_kwargs):
        queued.append(action)
        return fn()

    def type_backend(text, *, hwnd, final_guard, interval_s, **_kwargs):
        typed.append((text, hwnd, interval_s, final_guard()))
        return RawBackendResult(
            True, {"ok": True, "completed_codepoints": len(text)},
        )

    def hotkey_backend(vks, *, final_guard, release_individually=True):
        hotkey_vks.append((vks, final_guard(), release_individually))
        return RawBackendResult(True, {"ok": True, "key_count": len(vks)})

    monkeypatch.setattr(desktop_queue.DESKTOP_ACTION_QUEUE, "run", run)
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "cooperative_input_guard", lambda *_args: None)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: False)
    monkeypatch.setattr(executor, "_execute_physical_type_text", type_backend)
    monkeypatch.setattr(executor, "_vk_of", mapped.__getitem__)
    monkeypatch.setattr(executor, "_execute_physical_hotkey_vks", hotkey_backend)

    typed_result = executor.type_text("legacy", interval_s=0.25, hwnd=41)
    hotkey_result = executor.press_keys(keys, hwnd=41)

    assert typed_result == {"ok": True, "typed": 6}
    assert hotkey_result == {"ok": True, "keys": keys}
    assert queued == ["type_text", "press_keys"]
    assert typed == [(
        "legacy", 41, 0.25, FinalKeyboardGuardDecision.ADMITTED,
    )]
    assert hotkey_vks == [(
        (0x6A, 0xBB, 0x11, 0x11, 0x41, 0x42, 0x43),
        FinalKeyboardGuardDecision.ADMITTED, False,
    )]


def test_legacy_keyboard_bus_action_payloads_remain_byte_for_byte_compatible(
    monkeypatch,
) -> None:
    executor, _sent = _keyboard_executor_mocks(monkeypatch)
    events: list[dict] = []
    focused: list[int] = []
    monkeypatch.setattr(executor, "cooperative_input_guard", lambda *_args: None)
    monkeypatch.setattr(executor, "multi_input_cooperation_enabled", lambda: False)
    monkeypatch.setattr(executor.win32gui, "GetForegroundWindow", lambda: 999)
    monkeypatch.setattr(
        executor, "_raw_focus_window",
        lambda hwnd: focused.append(hwnd) or {"ok": True},
    )

    executor.BUS.add_sink(events.append)
    try:
        assert executor.type_text("abcdefghi", interval_s=0, hwnd=41) == {
            "ok": True, "typed": 9,
        }
        monkeypatch.setattr(executor, "_should_type_via_vk", lambda _text: True)
        monkeypatch.setattr(
            executor,
            "_vk_character_inputs",
            lambda char: (
                [executor._key_input(vk=ord(char))],
                [executor._key_input(
                    vk=ord(char), flags=executor._KEYEVENTF_KEYUP,
                )],
            ),
        )
        assert executor.type_text("12", interval_s=0, hwnd=41) == {
            "ok": True, "typed": 2, "requested": 2, "via": "vk",
        }
        assert executor.press_keys(["ctrl", "s"], hwnd=41) == {
            "ok": True, "keys": ["ctrl", "s"],
        }
    finally:
        executor.BUS.remove_sink(events.append)

    assert focused == [41]
    action_payloads = [
        {key: value for key, value in event.items() if key not in {"type", "ts"}}
        for event in events
        if event.get("type") == "action"
    ]
    assert action_payloads == [
        {"action": "type_text", "length": 9},
        {"action": "type_text", "length": 2, "via": "vk"},
        {"action": "hotkey", "keys": ["ctrl", "s"]},
    ]


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
        enforce_foreground=True,
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
        enforce_foreground=True,
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
