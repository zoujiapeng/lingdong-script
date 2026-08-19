from __future__ import annotations

import ast
import json
import math
from pathlib import Path
import random

import pytest

from winpilot.action.raw_device import (
    AdmittedPhysicalClickBackend,
    AdmittedPhysicalDragBackend,
    AdmittedPhysicalHotkeyBackend,
    AdmittedPhysicalMoveBackend,
    AdmittedPhysicalSequenceBackend,
    AdmittedPhysicalScrollBackend,
    AdmittedPhysicalTypeTextBackend,
    CANONICAL_HOTKEY_KEYS,
    ClickCount,
    ClickMechanism,
    FinalKeyboardGuardDecision,
    FinalPointerGuardDecision,
    HotkeyChord,
    InputEventKind,
    MAX_TYPE_TEXT_CODEPOINTS,
    MouseButton,
    PhysicalInputEvent,
    RawActionContext,
    RawActionReceipt,
    RawBackendResult,
    RawDeviceBackends,
    RawDeviceExecutor,
)
from winpilot.action.replay_policy import MutationOutcome


def _context(*, target: int = 41, current: int = 41) -> RawActionContext:
    return RawActionContext(target_hwnd=target, current_hwnd=current)


def _executor(calls: list, *, completed: bool | None = True) -> RawDeviceExecutor:
    def backend(x, y, amount, *, hwnd=None):
        calls.append((x, y, amount, hwnd))
        return RawBackendResult(completed, {"via": "test"})

    return RawDeviceExecutor(RawDeviceBackends(scroll=backend))


def test_fast_controller_owns_a_typed_raw_device_without_session_reflection() -> None:
    from winpilot.fast.controller import FastGuiController

    controller = FastGuiController()
    try:
        assert isinstance(controller.raw_device, RawDeviceExecutor)
        assert set(controller.raw_device.admitted_action_names) == {
            "click", "drag", "hotkey", "input_sequence",
            "move_mouse", "scroll", "type_text",
        }
    finally:
        controller.close()


def test_raw_device_requires_an_explicit_typed_backend() -> None:
    with pytest.raises(TypeError, match="backends"):
        RawDeviceExecutor()
    with pytest.raises(TypeError, match="RawDeviceBackends"):
        RawDeviceExecutor(None)
    with pytest.raises(TypeError, match="callable"):
        RawDeviceBackends(scroll=object())
    with pytest.raises(TypeError, match="AdmittedPhysicalScrollBackend"):
        RawDeviceBackends(admitted_physical_scroll=object())
    with pytest.raises(TypeError, match="AdmittedPhysicalDragBackend"):
        RawDeviceBackends(admitted_physical_drag=object())
    with pytest.raises(TypeError, match="AdmittedPhysicalTypeTextBackend"):
        RawDeviceBackends(admitted_physical_type_text=object())
    with pytest.raises(TypeError, match="AdmittedPhysicalHotkeyBackend"):
        RawDeviceBackends(admitted_physical_hotkey=object())


@pytest.mark.parametrize(
    ("context", "args", "error"),
    [
        (object(), (1, 2, -1), "invalid_context"),
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


def test_window_context_change_is_advisory_for_physical_input() -> None:
    calls: list = []

    receipt = _executor(calls).scroll(
        _context(target=41, current=42),
        1,
        2,
        -1,
    )

    assert receipt.outcome is MutationOutcome.APPLIED
    assert calls == [(1, 2, -1, 41)]


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


def test_scroll_uses_only_explicit_admitted_backend_and_ignores_legacy_guard() -> None:
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
    assert invalid_guard.outcome is MutationOutcome.APPLIED
    assert admitted_calls == [(10, 20, -3, 41), (10, 20, -3, 41)]


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
    (), ("ctrl",) * 7, ("ctrl", "ctrl"), ("control", "s"),
    ("CTRL", "s"), (" ctrl", "s"), ("unknown",), (1,),
    ("s", "ctrl"), ("shift", "ctrl", "s"), ("ctrl", "s", "a"),
    ("ctrl", "alt"), ("a", "b"), "ctrl+s", ["ctrl", "s"], b"ctrl+s",
])
def test_hotkey_chord_rejects_noncanonical_or_ambiguous_keys(keys) -> None:
    with pytest.raises((TypeError, ValueError)):
        HotkeyChord(keys)


def test_hotkey_chord_accepts_canonical_modifier_prefix_and_one_primary() -> None:
    assert HotkeyChord(("enter",)).keys == ("enter",)
    assert HotkeyChord(("win",)).keys == ("win",)
    chord = HotkeyChord(("ctrl", "alt", "shift", "win", "f12"))
    assert chord.keys == ("ctrl", "alt", "shift", "win", "f12")
    assert all(key in CANONICAL_HOTKEY_KEYS for key in chord.keys)


@pytest.mark.parametrize("action,argument", _KEYBOARD_INPUTS)
@pytest.mark.parametrize("context,error", [
    (object(), "invalid_context"),
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
    ("hotkey", ("ctrl", "s"), None),
    ("hotkey", "ctrl+s", None),
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
def test_keyboard_legacy_guard_is_advisory(action, argument) -> None:
    calls: list = []

    def backend(value, *, hwnd, final_guard):
        calls.append((value, hwnd, final_guard()))
        return RawBackendResult(True, {})

    receipt = getattr(_keyboard_raw(action, backend), action)(
        _context(), argument, final_guard=object(),
    )
    assert receipt.outcome is MutationOutcome.APPLIED
    assert calls == [(argument, 41, FinalKeyboardGuardDecision.ADMITTED)]


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

    raw.click(
        _context(), 10, 20, MouseButton.LEFT, ClickCount.SINGLE,
        ClickMechanism.PHYSICAL,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
        enforce_foreground=False,
    )
    assert admitted_calls[-1][2]["hwnd"] is None

    unavailable = RawDeviceExecutor(RawDeviceBackends()).click(
        _context(), 10, 20, MouseButton.LEFT, ClickCount.SINGLE,
        ClickMechanism.PHYSICAL,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )
    assert unavailable.outcome is MutationOutcome.NOT_STARTED
    assert unavailable.error_kind == "backend_unavailable"


def test_drag_uses_one_typed_admitted_backend_and_preserves_unknown() -> None:
    calls = []

    def admitted(x1, y1, x2, y2, *, hwnd, final_guard):
        calls.append((x1, y1, x2, y2, hwnd))
        assert final_guard(x1, y1) is FinalPointerGuardDecision.ADMITTED
        return RawBackendResult(None, {"via": "physical_sendinput"})

    raw = RawDeviceExecutor(
        RawDeviceBackends(
            admitted_physical_drag=AdmittedPhysicalDragBackend(admitted)
        )
    )
    receipt = raw.drag(
        _context(),
        10,
        20,
        30,
        40,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )

    assert calls == [(10, 20, 30, 40, 41)]
    assert receipt.outcome is MutationOutcome.OUTCOME_UNKNOWN
    assert receipt.input_completed is None


def test_stationary_drag_reaches_one_typed_backend_call() -> None:
    calls = []

    def admitted(x1, y1, x2, y2, *, hwnd, final_guard):
        calls.append((x1, y1, x2, y2, hwnd))
        assert final_guard(x1, y1) is FinalPointerGuardDecision.ADMITTED
        return RawBackendResult(True, {"via": "physical_sendinput"})

    raw = RawDeviceExecutor(RawDeviceBackends(
        admitted_physical_drag=AdmittedPhysicalDragBackend(admitted)
    ))

    receipt = raw.drag(_context(), 10, 20, 10, 20)

    assert receipt.outcome is MutationOutcome.APPLIED
    assert calls == [(10, 20, 10, 20, 41)]


def test_drag_preserves_ordered_intermediate_points_in_one_backend_call() -> None:
    calls = []

    def admitted(x1, y1, x2, y2, *, hwnd, points, final_guard):
        calls.append((x1, y1, x2, y2, hwnd, points))
        assert final_guard(x1, y1) is FinalPointerGuardDecision.ADMITTED
        return RawBackendResult(True, {"via": "physical_sendinput"})

    raw = RawDeviceExecutor(
        RawDeviceBackends(
            admitted_physical_drag=AdmittedPhysicalDragBackend(admitted)
        )
    )
    receipt = raw.drag(
        _context(),
        10,
        20,
        50,
        60,
        points=[[20, 20], (20, 50), [40, 50]],
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )

    assert receipt.outcome is MutationOutcome.APPLIED
    assert calls == [
        (
            10,
            20,
            50,
            60,
            41,
            ((20, 20), (20, 50), (40, 50)),
        )
    ]


def test_pressed_pointer_interpolation_reaches_each_bend_without_corner_cutting(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    emitted: list[tuple[int, int]] = []
    monkeypatch.setattr(
        executor,
        "_target_window_still_foreground",
        lambda _hwnd: True,
    )
    monkeypatch.setattr(
        executor,
        "_emit_move",
        lambda x, y: emitted.append((x, y)),
    )
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    completed = executor._move_path_while_pressed(
        [(10, 20), (34, 20), (34, 44)],
        0.4,
    )

    assert completed is True
    bend = emitted.index((34, 20))
    assert all(y == 20 for _x, y in emitted[: bend + 1])
    assert all(x == 34 for x, _y in emitted[bend:])
    assert emitted[-1] == (34, 44)


def test_drag_accepts_closed_path_that_returns_to_start() -> None:
    """A closed loop (start == end with a path) is a legal drawing gesture."""

    calls = []

    def admitted(x1, y1, x2, y2, *, hwnd, points, final_guard):
        calls.append((x1, y1, x2, y2, hwnd, points))
        assert final_guard(x1, y1) is FinalPointerGuardDecision.ADMITTED
        return RawBackendResult(True, {"via": "physical_sendinput"})

    raw = RawDeviceExecutor(
        RawDeviceBackends(
            admitted_physical_drag=AdmittedPhysicalDragBackend(admitted)
        )
    )
    receipt = raw.drag(
        _context(),
        10,
        20,
        10,
        20,
        points=[[30, 20], [30, 50], [10, 20]],
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )

    assert receipt.outcome is MutationOutcome.APPLIED
    assert calls == [
        (
            10,
            20,
            10,
            20,
            41,
            ((30, 20), (30, 50), (10, 20)),
        )
    ]



@pytest.mark.parametrize(
    "points",
    (
        None,
        "10,20",
        [(1,)],
        [(1, 2, 3)],
        [(True, 2)],
        [(1.5, 2)],
    ),
)
def test_drag_rejects_invalid_intermediate_points_before_backend(points) -> None:
    calls = []

    def admitted(*args, **kwargs):
        calls.append((args, kwargs))
        return RawBackendResult(True, {"via": "physical_sendinput"})

    raw = RawDeviceExecutor(
        RawDeviceBackends(
            admitted_physical_drag=AdmittedPhysicalDragBackend(admitted)
        )
    )
    receipt = raw.drag(
        _context(),
        10,
        20,
        30,
        40,
        points=points,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )

    assert receipt.outcome is MutationOutcome.NOT_STARTED
    assert receipt.error_kind == "invalid_args"
    assert calls == []


def _held_drag_raw(
    calls,
    *,
    fail_segment: bool = False,
    sequence_result: bool | None = True,
    sequence_completed_events: int | None = None,
    sequence_reports_progress: bool = True,
    drag_result: bool | None = True,
):
    side_calls = []

    def admitted(
        x1,
        y1,
        x2,
        y2,
        *,
        hwnd,
        final_guard,
        points=(),
        pointer_already_held=False,
        keep_holding=False,
        hold_token=None,
        cleanup_only=False,
    ):
        phase = (
            "cleanup"
            if cleanup_only
            else "continue"
            if pointer_already_held and keep_holding
            else "finish"
            if pointer_already_held
            else "begin"
            if keep_holding
            else "atomic"
        )
        calls.append((
            phase,
            (x1, y1),
            (x2, y2),
            hwnd,
            tuple(points),
            hold_token,
        ))
        if fail_segment and not cleanup_only:
            raise OSError("injected held segment uncertainty")
        return RawBackendResult(drag_result, {
            "segment_phase": phase,
            "held_after": keep_holding and not cleanup_only,
        })

    def side_backend(*args, **kwargs):
        side_calls.append((args, kwargs))
        return RawBackendResult(True, {"via": "side"})

    def sequence_backend(events, *args, **kwargs):
        side_calls.append(((events, *args), kwargs))
        completed = (
            len(events)
            if sequence_completed_events is None
            else sequence_completed_events
        )
        facts = {"via": "side"}
        if sequence_reports_progress:
            facts["completed_events"] = completed
        if sequence_result is None:
            facts["error_kind"] = "stop_requested"
        return RawBackendResult(sequence_result, facts)

    raw = RawDeviceExecutor(RawDeviceBackends(
        scroll=side_backend,
        click=side_backend,
        admitted_physical_drag=AdmittedPhysicalDragBackend(admitted),
        admitted_physical_move=AdmittedPhysicalMoveBackend(side_backend),
        admitted_physical_sequence=AdmittedPhysicalSequenceBackend(
            sequence_backend
        ),
        admitted_physical_type_text=AdmittedPhysicalTypeTextBackend(side_backend),
        admitted_physical_hotkey=AdmittedPhysicalHotkeyBackend(side_backend),
    ))
    return raw, side_calls


def test_held_drag_state_keeps_compatible_mouse_keyboard_input_available() -> None:
    calls = []
    raw, side_calls = _held_drag_raw(calls)
    token = "held-drag-contract-1"
    guard = lambda _x, _y: FinalPointerGuardDecision.ADMITTED

    try:
        begin = raw.drag(
            _context(), 10, 20, 20, 30,
            final_guard=guard,
            keep_holding=True,
            hold_token=token,
        )
        assert begin.outcome is MutationOutcome.APPLIED

        # The owner is module-level, not scoped to one RawDeviceExecutor.
        other_calls = []
        other = RawDeviceExecutor(RawDeviceBackends(
            scroll=lambda *args, **kwargs: (
                other_calls.append((args, kwargs))
                or RawBackendResult(True, {})
            ),
        ))
        mixed = [
            other.scroll(_context(), 1, 2, -1),
            raw.click(
                _context(), 1, 2, MouseButton.RIGHT, ClickCount.SINGLE,
                ClickMechanism.PHYSICAL,
            ),
            raw.move_mouse(_context(), 22, 32, duration_ms=15),
            raw.input_sequence(
                _context(),
                (
                    PhysicalInputEvent(InputEventKind.KEY_DOWN, key="shift"),
                    PhysicalInputEvent(InputEventKind.KEY_UP, key="shift"),
                ),
            ),
        ]
        assert all(item.outcome is MutationOutcome.APPLIED for item in mixed)
        assert len(side_calls) == 3
        assert len(other_calls) == 1

        typed = raw.type_text(
            _context(), "x",
            final_guard=lambda: FinalKeyboardGuardDecision.ADMITTED,
        )
        chord = raw.hotkey(
            _context(), HotkeyChord(("ctrl", "s")),
            final_guard=lambda: FinalKeyboardGuardDecision.ADMITTED,
        )
        assert typed.outcome is MutationOutcome.APPLIED
        assert chord.outcome is MutationOutcome.APPLIED
        assert len(side_calls) == 5

        # Token, remembered start point and hwnd are cleanup hints; serialized
        # physical button ownership is the continuation authority.
        continued = raw.drag(
            _context(target=99, current=100), 21, 31, 30, 40,
            final_guard=guard,
            pointer_already_held=True,
            keep_holding=True,
            hold_token="not-the-owner",
        )
        finished = raw.drag(
            _context(), 30, 40, 30, 40,
            final_guard=guard,
            pointer_already_held=True,
            hold_token="another-advisory-token",
        )
        assert continued.outcome is MutationOutcome.APPLIED
        assert finished.outcome is MutationOutcome.APPLIED
        assert [item[0] for item in calls] == ["begin", "continue", "finish"]
        assert calls[1][1:4] == ((21, 31), (30, 40), 99)

        assert other.scroll(_context(), 1, 2, -1).outcome is (
            MutationOutcome.APPLIED
        )
        assert len(other_calls) == 2
    finally:
        raw.release_held_drag(token)


def test_failed_held_drag_reports_uncertainty_without_a_global_input_gate() -> None:
    calls = []
    raw, side_calls = _held_drag_raw(calls, fail_segment=True)
    token = "held-drag-contract-uncertain"

    begin = raw.drag(
        _context(), 10, 20, 20, 30,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
        keep_holding=True,
        hold_token=token,
    )
    assert (begin.outcome, begin.error_kind) == (
        MutationOutcome.OUTCOME_UNKNOWN,
        "backend_exception",
    )
    assert raw.scroll(_context(), 1, 2, -1).outcome is MutationOutcome.APPLIED
    assert len(side_calls) == 1

    cleanup = raw.release_held_drag("different-advisory-token")
    second_cleanup = raw.release_held_drag(token)
    assert cleanup.outcome is MutationOutcome.APPLIED
    assert cleanup.backend_result.facts["held_after"] is False
    assert second_cleanup.outcome is MutationOutcome.APPLIED
    assert second_cleanup.backend_result.facts["cleanup_noop"] is True
    assert [item[0] for item in calls] == ["begin", "cleanup"]
    assert raw.scroll(_context(), 1, 2, -1).outcome is MutationOutcome.APPLIED


def test_input_sequence_can_explicitly_release_a_cross_turn_drag() -> None:
    calls = []
    raw, _side_calls = _held_drag_raw(calls)
    token = "held-drag-sequence-release"

    try:
        begin = raw.drag(
            _context(), 10, 20, 20, 30,
            keep_holding=True,
            hold_token=token,
        )
        released = raw.input_sequence(
            _context(),
            (PhysicalInputEvent(
                InputEventKind.MOUSE_UP,
                button=MouseButton.LEFT,
            ),),
        )
        cleanup = raw.release_held_drag(token)

        assert begin.outcome is MutationOutcome.APPLIED
        assert released.outcome is MutationOutcome.APPLIED
        assert cleanup.outcome is MutationOutcome.APPLIED
        assert cleanup.backend_result.facts["cleanup_noop"] is True
        assert [item[0] for item in calls] == ["begin"]
    finally:
        raw.release_held_drag(token)


def test_successful_sequence_without_progress_facts_releases_held_drag() -> None:
    calls = []
    raw, _side_calls = _held_drag_raw(
        calls,
        sequence_reports_progress=False,
    )
    token = "held-drag-sequence-release-without-progress"

    try:
        begin = raw.drag(
            _context(), 10, 20, 20, 30,
            keep_holding=True,
            hold_token=token,
        )
        released = raw.input_sequence(
            _context(),
            (PhysicalInputEvent(
                InputEventKind.MOUSE_UP,
                button=MouseButton.LEFT,
            ),),
        )
        cleanup = raw.release_held_drag(token)

        assert begin.outcome is MutationOutcome.APPLIED
        assert released.input_completed is True
        assert "completed_events" not in released.backend_result.facts
        assert cleanup.backend_result.facts["cleanup_noop"] is True
        assert [item[0] for item in calls] == ["begin"]
    finally:
        raw.release_held_drag(token)


def test_confirmed_not_applied_held_drag_begin_does_not_create_owner() -> None:
    calls = []
    raw, _side_calls = _held_drag_raw(calls, drag_result=False)
    token = "held-drag-not-applied-begin"

    begin = raw.drag(
        _context(), 10, 20, 20, 30,
        keep_holding=True,
        hold_token=token,
    )
    cleanup = raw.release_held_drag(token)

    assert begin.outcome is MutationOutcome.NOT_APPLIED
    assert begin.input_completed is False
    assert cleanup.backend_result.facts["cleanup_noop"] is True
    assert [item[0] for item in calls] == ["begin"]


def test_confirmed_mouse_up_prefix_clears_held_drag_after_later_sequence_failure(
) -> None:
    calls = []
    raw, _side_calls = _held_drag_raw(
        calls,
        sequence_result=None,
        sequence_completed_events=1,
    )
    token = "held-drag-partial-sequence-release"

    try:
        begin = raw.drag(
            _context(), 10, 20, 20, 30,
            keep_holding=True,
            hold_token=token,
        )
        released = raw.input_sequence(
            _context(),
            (
                PhysicalInputEvent(
                    InputEventKind.MOUSE_UP,
                    button=MouseButton.LEFT,
                ),
                PhysicalInputEvent(InputEventKind.WAIT, duration_ms=10),
            ),
        )
        cleanup = raw.release_held_drag(token)

        assert begin.outcome is MutationOutcome.APPLIED
        assert released.outcome is MutationOutcome.OUTCOME_UNKNOWN
        assert released.backend_result.facts["completed_events"] == 1
        assert cleanup.outcome is MutationOutcome.APPLIED
        assert cleanup.backend_result.facts["cleanup_noop"] is True
        assert [item[0] for item in calls] == ["begin"]
    finally:
        raw.release_held_drag(token)


def _physical_executor_mocks(monkeypatch, events):
    from winpilot.action import executor

    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_target_window_still_foreground", lambda _hwnd: True)
    monkeypatch.setattr(executor, "_raw_move_mouse", lambda x, y: events.append(("move", x, y)))
    monkeypatch.setattr(executor, "_cursor_pos", lambda: (10, 20))
    monkeypatch.setattr(executor, "_mouse_input", lambda flags, **_kwargs: flags)
    monkeypatch.setattr(
        executor,
        "_absolute_mouse_input",
        lambda x, y: ("absolute", x, y),
    )
    monkeypatch.setattr(
        executor,
        "_send_inputs",
        lambda inputs: events.append(("input", tuple(inputs))),
    )
    monkeypatch.setattr(executor, "_sample_pressed_state", lambda: False)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)
    return executor


def test_absolute_mouse_input_maps_real_virtual_desktop_pixels(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    metrics = {
        76: -1920,
        77: -1080,
        78: 3840,
        79: 2160,
    }
    monkeypatch.setattr(
        executor.user32,
        "GetSystemMetrics",
        lambda index: metrics[index],
    )

    top_left = executor._absolute_mouse_input(-1920, -1080).union.mi
    bottom_right = executor._absolute_mouse_input(1919, 1079).union.mi
    center = executor._absolute_mouse_input(0, 0).union.mi
    far_outside = executor._absolute_mouse_input(-10**9, 10**9).union.mi

    assert (top_left.dx, top_left.dy) == (0, 0)
    assert (bottom_right.dx, bottom_right.dy) == (65535, 65535)
    assert center.dx == round(1920 * 65535 / 3839)
    assert center.dy == round(1080 * 65535 / 2159)
    assert (far_outside.dx, far_outside.dy) == (0, 65535)
    required_flags = (
        executor.win32con.MOUSEEVENTF_MOVE
        | executor.win32con.MOUSEEVENTF_ABSOLUTE
        | 0x2000
        | 0x4000
    )
    assert top_left.dwFlags == required_flags
    assert bottom_right.dwFlags == required_flags


def test_pointer_paths_saturate_extreme_coordinates_before_interpolation(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    metrics = {76: -1920, 77: -1080, 78: 3840, 79: 2160}
    emitted: list[tuple[int, int]] = []
    monkeypatch.setattr(
        executor.user32,
        "GetSystemMetrics",
        lambda index: metrics[index],
    )
    monkeypatch.setattr(executor, "_emit_move", lambda x, y: emitted.append((x, y)))
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    assert executor._move_pointer_path(
        (-10**9, -10**9),
        (10**9, 10**9),
        0,
    ) is True
    diagonal_steps = math.ceil(math.hypot(3839, 2159) / 8)
    assert len(emitted) == diagonal_steps
    assert emitted[-1] == (1919, 1079)
    assert all(-1920 <= x <= 1919 and -1080 <= y <= 1079 for x, y in emitted)

    emitted.clear()
    assert executor._move_path_while_pressed(
        [(-10**9, -10**9), (10**9, 10**9), (-10**9, -10**9)],
        0,
    ) is True
    assert len(emitted) == diagonal_steps * 2
    assert all(-1920 <= x <= 1919 and -1080 <= y <= 1079 for x, y in emitted)


def test_relative_pointer_move_bounds_extreme_delta_before_interpolation(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    metrics = {76: -1920, 77: -1080, 78: 3840, 79: 2160}
    sent: list[tuple[int, int, int]] = []
    monkeypatch.setattr(
        executor.user32,
        "GetSystemMetrics",
        lambda index: metrics[index],
    )
    monkeypatch.setattr(
        executor,
        "_mouse_input",
        lambda flags, dx=0, dy=0, **_kwargs: (flags, dx, dy),
    )
    monkeypatch.setattr(executor, "_send_inputs", lambda items: sent.extend(items))

    assert executor._move_pointer_relative(10**9, -10**9, 0) is True
    assert len(sent) == math.ceil(math.hypot(3840, 2160) / 8)
    assert sum(item[1] for item in sent) == 3840
    assert sum(item[2] for item in sent) == -2160


def test_admitted_drag_executes_one_held_polyline_in_order(monkeypatch) -> None:
    events = []
    executor = _physical_executor_mocks(monkeypatch, events)
    captured = []

    def stroke(path, duration_s, *, hwnd, before_press, progress):
        assert before_press() is True
        captured.append(("stroke", path, duration_s, hwnd))
        progress.update(
            button_pressed=True,
            moves_completed=4,
            release_cleanup_attempted=True,
            release_cleanup_completed=True,
        )
        return True

    monkeypatch.setattr(executor, "_stroke_path", stroke)

    result = executor.execute_admitted_physical_drag(
        10,
        20,
        50,
        60,
        hwnd=41,
        points=((20, 20), (20, 50), (40, 50)),
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )

    assert result.input_completed is True
    assert captured == [
        (
            "stroke",
            [(10, 20), (20, 20), (20, 50), (40, 50), (50, 60)],
            0.4,
            41,
        ),
    ]
    assert result.facts["cursor_restored"] is False


def test_admitted_stationary_drag_presses_and_releases_once(monkeypatch) -> None:
    events = []
    executor = _physical_executor_mocks(monkeypatch, events)
    with executor._held_mouse_lock:
        executor._held_mouse_buttons.discard("left")
        executor._held_mouse_key_vks.pop("left", None)

    result = executor.execute_admitted_physical_drag(10, 20, 10, 20)

    assert result.input_completed is True
    assert events == [
        (
            "input",
            (("absolute", 10, 20), executor.win32con.MOUSEEVENTF_LEFTDOWN),
        ),
        ("input", (executor.win32con.MOUSEEVENTF_LEFTUP,)),
    ]
    assert result.facts["release_cleanup_completed"] is True
    assert executor.agent_holds_mouse_button("left") is False


def test_modifier_drag_holds_keys_before_mouse_and_releases_mouse_first(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    sent: list[tuple] = []
    with executor._held_mouse_lock:
        executor._held_mouse_buttons.discard("left")
        executor._held_mouse_key_vks.pop("left", None)
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(
        executor,
        "_hardware_key_input",
        lambda vk, key_up=False: ("key_up" if key_up else "key_down", vk),
    )
    monkeypatch.setattr(executor, "_vk_of", {"shift": 0x10, "w": 0x57}.__getitem__)
    monkeypatch.setattr(executor, "_mouse_input", lambda flags, **_kwargs: flags)
    monkeypatch.setattr(
        executor,
        "_absolute_mouse_input",
        lambda x, y: ("absolute", x, y),
    )
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.append(tuple(inputs)))
    monkeypatch.setattr(
        executor,
        "_move_path_while_pressed",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    result = executor.execute_admitted_physical_drag(
        10,
        20,
        30,
        40,
        held_keys=("shift", "w"),
    )

    assert result.input_completed is True
    assert sent == [
        (
            ("key_down", 0x10),
            ("key_down", 0x57),
            ("absolute", 10, 20),
            executor.win32con.MOUSEEVENTF_LEFTDOWN,
        ),
        (executor.win32con.MOUSEEVENTF_LEFTUP,),
        (("key_up", 0x57),),
        (("key_up", 0x10),),
    ]
    assert executor.agent_holds_mouse_button("left") is False
    with executor._held_mouse_lock:
        assert "left" not in executor._held_mouse_key_vks


def test_held_modifier_drag_keeps_keys_across_turns_and_cleanup_releases_them(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    sent: list[tuple] = []
    with executor._held_mouse_lock:
        executor._held_mouse_buttons.discard("left")
        executor._held_mouse_key_vks.pop("left", None)
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_cursor_pos", lambda: (20, 30))
    monkeypatch.setattr(
        executor,
        "_hardware_key_input",
        lambda vk, key_up=False: ("key_up" if key_up else "key_down", vk),
    )
    monkeypatch.setattr(executor, "_vk_of", {"shift": 0x10}.__getitem__)
    monkeypatch.setattr(executor, "_mouse_input", lambda flags, **_kwargs: flags)
    monkeypatch.setattr(
        executor,
        "_absolute_mouse_input",
        lambda x, y: ("absolute", x, y),
    )
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.append(tuple(inputs)))
    monkeypatch.setattr(
        executor,
        "_move_path_while_pressed",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    begin = executor.execute_admitted_physical_drag(
        10, 20, 20, 30,
        held_keys=("shift",),
        keep_holding=True,
        hold_token="held-modifier",
    )
    finish = executor.execute_admitted_physical_drag(
        20, 30, 30, 40,
        held_keys=("shift",),
        pointer_already_held=True,
        hold_token="held-modifier",
    )

    assert begin.input_completed is True
    assert finish.input_completed is True
    assert sent[0] == (
        ("key_down", 0x10),
        ("absolute", 10, 20),
        executor.win32con.MOUSEEVENTF_LEFTDOWN,
    )
    assert sent[-3:] == [
        (("key_down", 0x10),),
        (executor.win32con.MOUSEEVENTF_LEFTUP,),
        (("key_up", 0x10),),
    ]
    assert executor.agent_holds_mouse_button("left") is False


def test_modifier_drag_key_release_failure_is_retried_by_session_cleanup(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    sent: list[tuple] = []
    key_up_attempts = 0
    with executor._held_mouse_lock:
        executor._held_mouse_buttons.discard("left")
        executor._held_mouse_key_vks.pop("left", None)
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(
        executor,
        "_hardware_key_input",
        lambda vk, key_up=False: ("key_up" if key_up else "key_down", vk),
    )
    monkeypatch.setattr(executor, "_vk_of", {"shift": 0x10}.__getitem__)
    monkeypatch.setattr(executor, "_mouse_input", lambda flags, **_kwargs: flags)
    monkeypatch.setattr(
        executor,
        "_absolute_mouse_input",
        lambda x, y: ("absolute", x, y),
    )
    monkeypatch.setattr(
        executor,
        "_move_path_while_pressed",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    def send(inputs):
        nonlocal key_up_attempts
        batch = tuple(inputs)
        sent.append(batch)
        if batch == (("key_up", 0x10),):
            key_up_attempts += 1
            if key_up_attempts <= 2:
                raise OSError("injected modifier release failure")

    monkeypatch.setattr(executor, "_send_inputs", send)
    try:
        result = executor.execute_admitted_physical_drag(
            10, 20, 30, 40,
            held_keys=("shift",),
        )

        assert result.input_completed is None
        assert result.facts["release_cleanup_completed"] is False
        assert executor.agent_holds_mouse_button("left") is False
        with executor._held_mouse_lock:
            assert executor._held_mouse_key_vks["left"] == (0x10,)

        assert executor.release_held_input_state() is True
        with executor._held_mouse_lock:
            assert "left" not in executor._held_mouse_key_vks
    finally:
        with executor._held_mouse_lock:
            executor._held_mouse_buttons.discard("left")
            executor._held_mouse_key_vks.pop("left", None)


def test_admitted_drag_begin_continue_finish_preserve_one_physical_hold(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    events = []
    cursor = {"point": (10, 20)}
    guards = []
    with executor._held_mouse_lock:
        executor._held_mouse_buttons.discard("left")

    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_target_window_still_foreground", lambda _hwnd: True)
    monkeypatch.setattr(executor, "_cursor_pos", lambda: cursor["point"])
    monkeypatch.setattr(executor, "_mouse_input", lambda flags, **_kwargs: flags)
    monkeypatch.setattr(
        executor,
        "_absolute_mouse_input",
        lambda x, y: ("absolute", x, y),
    )
    monkeypatch.setattr(
        executor,
        "_send_inputs",
        lambda inputs: events.append(("input", tuple(inputs))),
    )
    monkeypatch.setattr(
        executor,
        "_abs_mouse_move",
        lambda x, y: (
            events.append(("preposition", x, y)),
            cursor.__setitem__("point", (x, y)),
        ),
    )
    monkeypatch.setattr(
        executor,
        "_restore_cursor_after_agent_input",
        lambda *_args: pytest.fail("held segment restored the shared cursor"),
    )
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    def move(path, duration_s, *, hwnd, progress):
        events.append(("stroke", tuple(path), duration_s, hwnd))
        progress["moves_completed"] = int(progress["moves_completed"]) + 1
        cursor["point"] = path[-1]
        return True

    def guard(x, y):
        guards.append((x, y))
        return FinalPointerGuardDecision.ADMITTED

    monkeypatch.setattr(executor, "_move_path_while_pressed", move)
    try:
        begin = executor.execute_admitted_physical_drag(
            10, 20, 20, 30,
            hwnd=41,
            final_guard=guard,
            keep_holding=True,
            hold_token="physical-held-contract",
        )
        cursor["point"] = (24, 34)
        continued = executor.execute_admitted_physical_drag(
            20, 30, 30, 40,
            hwnd=41,
            final_guard=guard,
            pointer_already_held=True,
            keep_holding=True,
            hold_token="physical-held-contract",
        )
        finished = executor.execute_admitted_physical_drag(
            30, 40, 30, 40,
            hwnd=41,
            final_guard=guard,
            pointer_already_held=True,
            hold_token="physical-held-contract",
        )
    finally:
        with executor._held_mouse_lock:
            executor._held_mouse_buttons.discard("left")

    assert [result.input_completed for result in (begin, continued, finished)] == [
        True, True, True,
    ]
    assert [
        result.facts["segment_phase"]
        for result in (begin, continued, finished)
    ] == ["begin", "continue", "finish"]
    assert guards == []
    assert [
        event for event in events if event[0] == "input"
    ] == [
        (
            "input",
            (("absolute", 10, 20), executor.win32con.MOUSEEVENTF_LEFTDOWN),
        ),
        ("input", (executor.win32con.MOUSEEVENTF_LEFTUP,)),
    ]
    assert [event for event in events if event[0] == "preposition"] == []
    assert [event[0] for event in events].count("stroke") == 3
    assert [event[1] for event in events if event[0] == "stroke"][1] == (
        (24, 34),
        (30, 40),
    )
    assert [event[1] for event in events if event[0] == "stroke"][-1] == (
        (30, 40),
    )


def test_failed_held_segment_releases_without_cursor_restore(monkeypatch) -> None:
    from winpilot.action import executor

    sent = []
    with executor._held_mouse_lock:
        executor._held_mouse_buttons.discard("left")
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_target_window_still_foreground", lambda _hwnd: True)
    monkeypatch.setattr(executor, "_cursor_pos", lambda: (10, 20))
    monkeypatch.setattr(executor, "_abs_mouse_move", lambda _x, _y: None)
    monkeypatch.setattr(executor, "_mouse_input", lambda flags, **_kwargs: flags)
    monkeypatch.setattr(
        executor,
        "_absolute_mouse_input",
        lambda x, y: ("absolute", x, y),
    )
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.append(tuple(inputs)))
    monkeypatch.setattr(
        executor,
        "_move_path_while_pressed",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        executor,
        "_restore_cursor_after_agent_input",
        lambda *_args: pytest.fail("failed held segment restored cursor"),
    )
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    try:
        result = executor.execute_admitted_physical_drag(
            10, 20, 20, 30,
            hwnd=41,
            final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
            keep_holding=True,
            hold_token="physical-held-failure",
        )
        assert result.input_completed is None
        assert sent == [
            (("absolute", 10, 20), executor.win32con.MOUSEEVENTF_LEFTDOWN),
            (executor.win32con.MOUSEEVENTF_LEFTUP,),
        ]
        assert result.facts["release_cleanup_completed"] is True
        assert executor.agent_holds_mouse_button("left") is False
    finally:
        with executor._held_mouse_lock:
            executor._held_mouse_buttons.discard("left")


def test_admitted_backend_places_and_presses_in_one_batch_without_window_guard(
    monkeypatch,
):
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
    assert events[-2:] == [
        (
            "input",
            (("absolute", 10, 20), executor.win32con.MOUSEEVENTF_LEFTDOWN),
        ),
        ("input", (executor.win32con.MOUSEEVENTF_LEFTUP,)),
    ]
    assert executor.ADMITTED_PHYSICAL_CLICK_BACKEND.execute is (
        executor.execute_admitted_physical_click
    )
    assert not hasattr(executor.execute_admitted_physical_click, "__wrapped__")
    assert hasattr(executor.execute_click_input, "__wrapped__")
    with pytest.raises(TypeError, match="decorated"):
        AdmittedPhysicalClickBackend(executor.execute_click_input)


@pytest.mark.parametrize("amount", (-12, -1, 1, 12))
def test_admitted_scroll_is_single_mechanism_unqueued_and_unguarded(
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
        "_absolute_mouse_input",
        lambda x, y: ("absolute", x, y),
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
    assert [event[0] for event in events] == ["input"]
    assert result.facts["cursor_restored"] is False
    assert events[0] == (
        "input",
        (
            ("absolute", 10, 20),
            (
                executor.win32con.MOUSEEVENTF_WHEEL,
                amount & 0xFFFFFFFF,
            ),
        ),
    )
    assert outer.status()["submitted"] == 1
    assert executor.ADMITTED_PHYSICAL_SCROLL_BACKEND.execute is (
        executor.execute_admitted_physical_scroll
    )
    assert not hasattr(executor.execute_admitted_physical_scroll, "__wrapped__")
    assert hasattr(executor.execute_scroll_input, "__wrapped__")
    with pytest.raises(TypeError, match="decorated"):
        AdmittedPhysicalScrollBackend(executor.execute_scroll_input)


def test_admitted_scroll_ignores_window_guard_verdict(monkeypatch):
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

    assert result.input_completed is True
    assert result.facts["via"] == "physical_sendinput"
    assert len([event for event in events if event[0] == "input"]) == 1


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
        ("input_attempt", 2),
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


def test_window_guard_cannot_interrupt_clicks_before_or_during_input(
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

    assert result.input_completed is True
    assert result.facts["completed_clicks"] == 2
    assert [event for event in events if event[0] == "input"] == [
        (
            "input",
            (("absolute", 10, 20), executor.win32con.MOUSEEVENTF_LEFTDOWN),
        ),
        ("input", (executor.win32con.MOUSEEVENTF_LEFTUP,)),
        (
            "input",
            (("absolute", 10, 20), executor.win32con.MOUSEEVENTF_LEFTDOWN),
        ),
        ("input", (executor.win32con.MOUSEEVENTF_LEFTUP,)),
    ]

    events.clear()
    admitted = executor.ADMITTED_PHYSICAL_CLICK_BACKEND(
        10, 20, button=MouseButton.LEFT, count=ClickCount.SINGLE,
        mechanism=ClickMechanism.PHYSICAL, hwnd=41,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.TARGET_ROOT_CHANGED,
    )
    assert admitted.input_completed is True
    assert len([event for event in events if event[0] == "input"]) == 2


def test_admitted_backend_does_not_resample_pointer_after_guard(monkeypatch):
    events = []
    executor = _physical_executor_mocks(monkeypatch, events)
    positions = iter(((10, 20), (11, 20)))
    monkeypatch.setattr(executor, "_cursor_pos", lambda: next(positions))

    result = executor.ADMITTED_PHYSICAL_CLICK_BACKEND(
        10, 20, button=MouseButton.LEFT, count=ClickCount.SINGLE,
        mechanism=ClickMechanism.PHYSICAL, hwnd=41,
        final_guard=lambda _x, _y: FinalPointerGuardDecision.ADMITTED,
    )

    assert result.input_completed is True
    assert not hasattr(result.facts, "error")
    assert [event for event in events if event[0] == "input"]


def _keyboard_executor_mocks(monkeypatch):
    from winpilot.action import executor

    sent: list[tuple] = []
    with executor._held_mouse_lock:
        executor._held_keyboard_vks.clear()
        executor._held_unicode_units.clear()
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        executor.user32,
        "GetForegroundWindow",
        lambda: 41,
    )
    monkeypatch.setattr(
        executor.user32,
        "GetKeyboardLayout",
        lambda _tid: 0x04090409,
    )
    monkeypatch.setattr(
        executor,
        "_key_input",
        lambda **kwargs: ("key", tuple(sorted(kwargs.items()))),
    )
    scan_codes = {
        0x11: 0x1D,
        0x10: 0x2A,
        0x5B: 0x5B,
        0x53: 0x1F,
    }
    monkeypatch.setattr(
        executor.user32,
        "MapVirtualKeyW",
        lambda vk, _mode: scan_codes.get(vk, 0),
    )
    monkeypatch.setattr(
        executor,
        "_send_inputs",
        lambda inputs: sent.append(tuple(inputs)),
    )
    monkeypatch.setattr(
        executor,
        "_vk_of",
        lambda key: {
            "ctrl": 0x11,
            "shift": 0x10,
            "win": 0x5B,
            "s": 0x53,
        }[key],
    )
    return executor, sent


def test_admitted_type_text_never_queues_or_calls_window_guard(monkeypatch):
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
    assert guards == []
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
            (ord(char),),
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


def test_ascii_oem_punctuation_uses_active_layout_mapping_and_scan_code(
    monkeypatch,
) -> None:
    import winpilot.action.executor as ex

    english = 0x04090409
    mapped: list[tuple[str, int]] = []
    hardware: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        ex.user32,
        "LoadKeyboardLayoutW",
        lambda name, flags: english if (name, flags) == ("00000409", 0) else 0,
    )

    def fake_scan(char, layout):
        mapped.append((char.value, layout))
        return 0xBD  # VK_OEM_MINUS, no modifier

    monkeypatch.setattr(ex.user32, "VkKeyScanExW", fake_scan)
    monkeypatch.setattr(
        ex,
        "_hardware_key_input",
        lambda vk, key_up=False: (
            hardware.append((vk, key_up)) or ("scan", vk, key_up)
        ),
    )

    assert ex._vk_character_inputs("-") == (
        [("scan", 0xBD, False)],
        [("scan", 0xBD, True)],
        (0xBD,),
    )
    assert mapped == [("-", english)]
    assert hardware == [(0xBD, False), (0xBD, True)]


def test_ascii_text_switches_english_layout_away_from_ime(
    monkeypatch,
) -> None:
    """VK transitions under a Chinese/Japanese/Korean IME swallow digit keys;
    the strict text path must switch the target thread to a Latin layout and
    restore it afterwards."""
    import winpilot.action.executor as ex

    english = 0x04090409
    chinese = 0x08040804
    calls: list[tuple[int, int, int]] = []

    def fake_thread_layout(hwnd: int, _pid: int | None) -> int:
        return chinese

    def fake_layout(tid: int) -> int:
        return chinese

    def fake_load(layout: str, flags: int) -> int:
        assert layout == "00000409"
        return english

    def fake_post(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        calls.append((hwnd, msg, lparam))
        return 1

    monkeypatch.setattr(ex.user32, "GetForegroundWindow", lambda: 41)
    monkeypatch.setattr(ex.user32, "GetWindowThreadProcessId", fake_thread_layout)
    monkeypatch.setattr(ex.user32, "GetKeyboardLayout", fake_layout)
    monkeypatch.setattr(ex.user32, "LoadKeyboardLayoutW", fake_load)
    monkeypatch.setattr(ex.user32, "PostMessageW", fake_post)

    switch, safe = ex._english_layout_switch(41)
    assert safe is True
    assert switch == (chinese, False)
    assert calls == [(41, ex._WM_INPUTLANGCHANGEREQUEST, english)]

    ex._restore_layout_switch(41, (chinese, False))
    assert calls[-1] == (41, ex._WM_INPUTLANGCHANGEREQUEST, chinese)


def test_ascii_text_skips_layout_switch_on_english_layout(
    monkeypatch,
) -> None:
    import winpilot.action.executor as ex

    english = 0x04090409

    def fake_thread_layout(hwnd: int, _pid: int | None) -> int:
        return english

    def fake_layout(tid: int) -> int:
        return english

    monkeypatch.setattr(ex.user32, "GetForegroundWindow", lambda: 41)
    monkeypatch.setattr(ex.user32, "GetWindowThreadProcessId", fake_thread_layout)
    monkeypatch.setattr(ex.user32, "GetKeyboardLayout", fake_layout)

    # English layout with no open IME: nothing to do, VK is safe.
    assert ex._english_layout_switch(41) == (None, True)
    # A bound-window hint is optional: text follows the actual focused window.
    assert ex._english_layout_switch(0) == (None, True)


def test_english_layout_with_open_ime_still_closes_ime_for_vk(
    monkeypatch,
) -> None:
    """GetKeyboardLayout cannot see IME activation: an open Chinese IME
    corrupts plain ASCII VK input even on a 0x0409 layout."""
    import winpilot.action.executor as ex

    english = 0x04090409
    context = object()
    calls: list[tuple[int, int, int]] = []
    ime_calls: list[str] = []

    def fake_thread_layout(hwnd: int, _pid: int | None) -> int:
        return english

    def fake_layout(tid: int) -> int:
        return english

    def fake_get_context(hwnd: int):
        ime_calls.append(("get", hwnd))
        return context

    def fake_open_status(ctx) -> int:
        ime_calls.append(("status",))
        return 1

    def fake_set_open_status(ctx, opened: int) -> int:
        ime_calls.append(("set", opened))
        return 1

    def fake_release(hwnd: int, ctx) -> int:
        ime_calls.append(("release", hwnd))
        return 1

    monkeypatch.setattr(ex.user32, "GetForegroundWindow", lambda: 41)
    monkeypatch.setattr(ex.user32, "GetWindowThreadProcessId", fake_thread_layout)
    monkeypatch.setattr(ex.user32, "GetKeyboardLayout", fake_layout)
    monkeypatch.setattr(ex.imm32, "ImmGetContext", fake_get_context)
    monkeypatch.setattr(ex.imm32, "ImmGetOpenStatus", fake_open_status)
    monkeypatch.setattr(ex.imm32, "ImmSetOpenStatus", fake_set_open_status)
    monkeypatch.setattr(ex.imm32, "ImmReleaseContext", fake_release)

    switch, safe = ex._english_layout_switch(41)
    assert safe is True
    assert switch == (english, True)
    assert ("set", 0) in ime_calls  # IME closed for the VK input duration

    ex._restore_layout_switch(41, (english, True))
    assert ("set", 1) in ime_calls  # IME reopened afterwards


def test_physical_type_text_restores_layout_switch_tuple_after_input(
    monkeypatch,
) -> None:
    """The finally path must hand the whole (original, ime) tuple to the
    restore helper, not its first element (regression: passing the int
    raised TypeError -> backend_exception on every typed character)."""
    import winpilot.action.executor as ex

    restore_calls: list[tuple[int | None, tuple[int, bool] | None]] = []
    sent_chars: list[str] = []

    def fake_send(char: str, *, via_vk: bool) -> tuple[bool | None, bool, bool]:
        sent_chars.append(char)
        return True, True, True

    monkeypatch.setattr(ex, "_should_strict_type_via_vk", lambda text: True)
    monkeypatch.setattr(
        ex,
        "_english_layout_switch",
        lambda hwnd: ((0x0804, True), True),
    )
    monkeypatch.setattr(
        ex,
        "_restore_layout_switch",
        lambda hwnd, restore: restore_calls.append((hwnd, restore)),
    )
    monkeypatch.setattr(ex, "_send_text_codepoint", fake_send)
    monkeypatch.setattr(ex.time, "sleep", lambda _seconds: None)

    result = ex._execute_physical_type_text(
        "ab",
        hwnd=41,
        final_guard=lambda: FinalKeyboardGuardDecision.ADMITTED,
        interval_s=0.008,
    )

    assert result.input_completed is True
    assert sent_chars == ["a", "b"]
    assert restore_calls == [(41, (0x0804, True))]


@pytest.mark.parametrize("backend_name,argument", [
    ("ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND", "secret"),
    ("ADMITTED_PHYSICAL_HOTKEY_BACKEND", HotkeyChord(("ctrl", "s"))),
])
def test_admitted_keyboard_ignores_window_guard_verdict(
    monkeypatch, backend_name, argument,
) -> None:
    executor, sent = _keyboard_executor_mocks(monkeypatch)
    result = getattr(executor, backend_name)(
        argument, hwnd=41,
        final_guard=lambda: FinalKeyboardGuardDecision.FOREGROUND_CHANGED,
    )
    assert result.input_completed is True
    assert sent
    assert "secret" not in json.dumps(dict(result.facts), sort_keys=True)


def test_admitted_type_text_window_guard_cannot_interrupt_input(
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

    assert result.input_completed is True
    assert len(sent) == 2
    assert result.facts["completed_codepoints"] == 2


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


def test_type_text_release_failure_remains_owned_until_session_cleanup(
    monkeypatch,
) -> None:
    executor, sent = _keyboard_executor_mocks(monkeypatch)
    fail_releases = True

    def send(inputs):
        nonlocal fail_releases
        batch = tuple(inputs)
        sent.append(batch)
        is_key_up = (
            len(batch) == 1
            and bool(
                dict(batch[0][1]).get("flags", 0)
                & executor._KEYEVENTF_KEYUP
            )
        )
        if fail_releases and is_key_up:
            raise OSError("injected persistent Enter release failure")

    monkeypatch.setattr(executor, "_send_inputs", send)
    try:
        result = executor.ADMITTED_PHYSICAL_TYPE_TEXT_BACKEND("\n", hwnd=41)

        assert result.input_completed is None
        assert result.facts["error_kind"] == "release_cleanup_incomplete"
        with executor._held_mouse_lock:
            assert executor._held_keyboard_vks == {executor._VK["enter"]}

        fail_releases = False
        assert executor.release_held_input_state() is True
        with executor._held_mouse_lock:
            assert executor._held_keyboard_vks == set()
    finally:
        with executor._held_mouse_lock:
            executor._held_keyboard_vks.clear()


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
    assert guard_calls == []
    assert len(sent) == 4
    downs, *up_batches = sent
    ups = tuple(batch[0] for batch in up_batches)
    assert [dict(token[1])["scan"] for token in downs] == [0x1D, 0x2A, 0x1F]
    assert [dict(token[1])["scan"] for token in ups] == [0x1F, 0x2A, 0x1D]
    assert all(
        dict(token[1]).get("flags", 0) & executor._KEYEVENTF_SCANCODE
        for token in downs
    )
    assert all(
        dict(token[1]).get("flags", 0) & executor._KEYEVENTF_KEYUP
        for token in ups
    )
    assert executor.ADMITTED_PHYSICAL_HOTKEY_BACKEND.execute is (
        executor.execute_admitted_physical_hotkey
    )
    assert not hasattr(executor.execute_admitted_physical_hotkey, "__wrapped__")


def test_admitted_hotkey_foreground_change_after_complete_chord_is_applied(
    monkeypatch,
) -> None:
    executor, sent = _keyboard_executor_mocks(monkeypatch)
    decisions = iter((
        FinalKeyboardGuardDecision.ADMITTED,
        FinalKeyboardGuardDecision.FOREGROUND_CHANGED,
    ))

    result = executor.ADMITTED_PHYSICAL_HOTKEY_BACKEND(
        HotkeyChord(("win",)),
        hwnd=41,
        final_guard=lambda: next(decisions),
    )

    assert result.input_completed is True
    assert len(sent) == 2
    assert "guard_decision" not in result.facts
    assert result.facts["release_cleanup_completed"] is True


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

    assert len(sent) >= 3
    release_scans = [
        dict(batch[0][1])["scan"]
        for batch in sent[1:]
    ]
    assert release_scans[0] == 0x1F
    assert release_scans[-1] == 0x1D
    assert set(release_scans) == {0x1F, 0x1D}
    if fail_at == 1:
        assert result.input_completed is None
    else:
        # A failed UP is retried idempotently; once it succeeds, the complete
        # chord is mechanically known to have been pressed and released.
        assert result.input_completed is True


def test_hotkey_release_failure_remains_owned_until_session_cleanup(
    monkeypatch,
) -> None:
    executor, sent = _keyboard_executor_mocks(monkeypatch)
    fail_releases = True

    def send(inputs):
        nonlocal fail_releases
        batch = tuple(inputs)
        sent.append(batch)
        is_key_up = (
            len(batch) == 1
            and bool(
                dict(batch[0][1]).get("flags", 0)
                & executor._KEYEVENTF_KEYUP
            )
        )
        if fail_releases and is_key_up:
            raise OSError("injected persistent key-up failure")

    monkeypatch.setattr(executor, "_send_inputs", send)
    try:
        result = executor.ADMITTED_PHYSICAL_HOTKEY_BACKEND(
            HotkeyChord(("ctrl", "s")),
            hwnd=41,
        )

        assert result.input_completed is None
        assert result.facts["error_kind"] == "release_cleanup_incomplete"
        with executor._held_mouse_lock:
            assert executor._held_keyboard_vks == {0x11, 0x53}

        fail_releases = False
        assert executor.release_held_input_state() is True
        with executor._held_mouse_lock:
            assert executor._held_keyboard_vks == set()
    finally:
        with executor._held_mouse_lock:
            executor._held_keyboard_vks.clear()


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
                (ord(char),),
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

    assert focused == []
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

    monkeypatch.setattr(executor, "_raw_move_mouse", lambda x, y: None)
    monkeypatch.setattr(executor, "_mouse_input", lambda flag: flag)
    monkeypatch.setattr(
        executor,
        "_absolute_mouse_input",
        lambda x, y: ("absolute", x, y),
    )
    monkeypatch.setattr(executor, "_send_inputs", send_inputs)
    monkeypatch.setattr(executor, "_sample_pressed_state", lambda: False)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    result = executor._raw_click(
        10, 20, MouseButton.LEFT, ClickCount.DOUBLE, None,
    )

    # UP is retried in-place.  A transient first-UP failure is recovered and
    # the double click continues; DOWN failures remain outcome-unknown because
    # Windows may have accepted the input before reporting the error.
    if fail_at == 2:
        assert result.input_completed is True
        assert result.facts["completed_clicks"] == 2
        assert len(calls) == 5
    else:
        assert result.input_completed is None
        assert result.facts["completed_clicks"] == completed_clicks
        assert result.facts["release_cleanup_attempted"] is True
        assert result.facts["release_cleanup_completed"] is True
        assert len(calls) == fail_at + 1


def test_click_release_failure_remains_owned_until_session_cleanup(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    sent: list[list] = []
    fail_releases = True
    monkeypatch.setattr(executor, "_raw_move_mouse", lambda x, y: None)
    monkeypatch.setattr(executor, "_mouse_input", lambda flag, **_kwargs: flag)
    monkeypatch.setattr(
        executor,
        "_absolute_mouse_input",
        lambda x, y: ("absolute", x, y),
    )
    monkeypatch.setattr(executor, "_sample_pressed_state", lambda: False)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)
    with executor._held_mouse_lock:
        executor._held_mouse_buttons.discard("left")

    def send(inputs):
        sent.append(list(inputs))
        if (
            fail_releases
            and inputs[0] == executor.win32con.MOUSEEVENTF_LEFTUP
        ):
            raise OSError("injected persistent mouse-up failure")

    monkeypatch.setattr(executor, "_send_inputs", send)
    try:
        result = executor._raw_click(
            10, 20, MouseButton.LEFT, ClickCount.SINGLE, None,
        )

        assert result.input_completed is None
        assert result.facts["error_kind"] == "release_cleanup_incomplete"
        assert executor.agent_holds_mouse_button("left") is True

        fail_releases = False
        assert executor.release_held_input_state() is True
        assert executor.agent_holds_mouse_button("left") is False
    finally:
        with executor._held_mouse_lock:
            executor._held_mouse_buttons.discard("left")


def test_physical_click_uses_final_root_ownership_not_focus_helper_result(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    sent: list[list] = []
    monkeypatch.setattr(executor, "_raw_focus_window", lambda hwnd: {"ok": False})
    monkeypatch.setattr(executor, "_target_window_still_foreground", lambda hwnd: True)
    monkeypatch.setattr(executor, "_raw_move_mouse", lambda x, y: None)
    monkeypatch.setattr(executor, "_mouse_input", lambda flag: flag)
    monkeypatch.setattr(
        executor,
        "_absolute_mouse_input",
        lambda x, y: ("absolute", x, y),
    )
    monkeypatch.setattr(executor, "_send_inputs", sent.append)
    monkeypatch.setattr(executor, "_sample_pressed_state", lambda: False)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    result = executor._raw_click(
        10, 20, MouseButton.LEFT, ClickCount.SINGLE, 41,
        enforce_foreground=True,
    )

    assert result.input_completed is True
    assert len(sent) == 2


def test_physical_click_ignores_window_metadata_change_after_cursor_move(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    ownership = iter((True, False))
    sent: list[list] = []
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

    assert result.input_completed is True
    assert result.facts["completed_clicks"] == 1
    assert len(sent) == 2


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



def test_legacy_click_dry_run_keeps_preexisting_argument_behavior(monkeypatch) -> None:
    from winpilot.action import executor

    monkeypatch.setattr(executor, "_dry_run", lambda: True)

    assert executor.click(1, 2, button="legacy-invalid") == {
        "ok": True,
        "dry_run": True,
    }



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


def test_admitted_move_uses_real_absolute_path_and_leaves_cursor_at_endpoint(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    cursor = {"point": (10, 20)}
    emitted: list[tuple[int, int]] = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_cursor_pos", lambda: cursor["point"])
    monkeypatch.setattr(executor, "_target_window_still_foreground", lambda _hwnd: True)
    monkeypatch.setattr(
        executor,
        "_emit_move",
        lambda x, y: (
            emitted.append((x, y)),
            cursor.__setitem__("point", (x, y)),
        ),
    )
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    result = executor.execute_admitted_physical_move(
        42,
        52,
        duration_ms=80,
        final_guard=lambda x, y: (
            FinalPointerGuardDecision.ADMITTED
            if (x, y) == (42, 52)
            else FinalPointerGuardDecision.POINTER_POSITION_CHANGED
        ),
    )

    assert result.input_completed is True
    assert emitted[-1] == (42, 52)
    assert cursor["point"] == (42, 52)
    assert result.facts["cursor_restored"] is False


def test_atomic_sequence_interleaves_shift_and_right_drag_then_releases(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    sent: list[tuple[str, int, int]] = []
    cursor = {"point": (5, 5)}
    with executor._held_mouse_lock:
        executor._held_mouse_buttons.clear()
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_cursor_pos", lambda: cursor["point"])
    monkeypatch.setattr(executor, "_target_window_still_foreground", lambda _hwnd: True)
    monkeypatch.setattr(
        executor,
        "_move_pointer_path",
        lambda _start, end, _duration, **_kwargs: (
            cursor.__setitem__("point", end) or True
        ),
    )
    monkeypatch.setattr(executor, "_vk_of", lambda key: {"shift": 0x10}[key])
    monkeypatch.setattr(
        executor.user32,
        "MapVirtualKeyW",
        lambda vk, _mode: 0x2A if vk == 0x10 else 0,
    )
    monkeypatch.setattr(executor, "_key_input", lambda vk=0, scan=0, flags=0: ("key", vk, flags))
    monkeypatch.setattr(executor, "_mouse_input", lambda flags, data=0, **_kwargs: ("mouse", flags, data))
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.extend(inputs))

    events = (
        PhysicalInputEvent(InputEventKind.KEY_DOWN, key="shift"),
        PhysicalInputEvent(InputEventKind.MOVE, x=20, y=30, duration_ms=20),
        PhysicalInputEvent(InputEventKind.MOUSE_DOWN, button=MouseButton.RIGHT),
        PhysicalInputEvent(InputEventKind.MOVE, x=80, y=90, duration_ms=50),
        PhysicalInputEvent(InputEventKind.MOUSE_UP, button=MouseButton.RIGHT),
        PhysicalInputEvent(InputEventKind.KEY_UP, key="shift"),
    )
    result = executor.execute_admitted_physical_sequence(
        events,
        final_keyboard_guard=lambda: FinalKeyboardGuardDecision.ADMITTED,
    )

    assert result.input_completed is True
    assert result.facts["completed_events"] == len(events)
    assert cursor["point"] == (80, 90)
    assert sent == [
        ("key", 0, executor._KEYEVENTF_SCANCODE),
        ("mouse", 57345, 0),
        ("mouse", executor.win32con.MOUSEEVENTF_RIGHTDOWN, 0),
        ("mouse", executor.win32con.MOUSEEVENTF_RIGHTUP, 0),
        (
            "key",
            0,
            executor._KEYEVENTF_SCANCODE | executor._KEYEVENTF_KEYUP,
        ),
    ]
    assert executor.agent_holds_mouse_button("right") is False


def test_sequence_failure_releases_only_inputs_owned_by_that_sequence(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    sent: list[tuple[str, int, int]] = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_cursor_pos", lambda: (1, 1))
    monkeypatch.setattr(executor, "_target_window_still_foreground", lambda _hwnd: True)
    monkeypatch.setattr(executor, "_vk_of", lambda key: {"shift": 0x10}[key])
    monkeypatch.setattr(
        executor.user32,
        "MapVirtualKeyW",
        lambda vk, _mode: 0x2A if vk == 0x10 else 0,
    )
    monkeypatch.setattr(executor, "_key_input", lambda vk=0, scan=0, flags=0: ("key", vk, flags))
    monkeypatch.setattr(executor, "_mouse_input", lambda flags, data=0, **_kwargs: ("mouse", flags, data))

    def send(inputs):
        sent.extend(inputs)
        if inputs[0] == ("mouse", executor.win32con.MOUSEEVENTF_RIGHTDOWN, 0):
            raise OSError("injected down uncertainty")

    monkeypatch.setattr(executor, "_send_inputs", send)
    with executor._held_mouse_lock:
        executor._held_mouse_buttons.clear()
    result = executor.execute_admitted_physical_sequence(
        (
            PhysicalInputEvent(InputEventKind.KEY_DOWN, key="shift"),
            PhysicalInputEvent(InputEventKind.MOUSE_DOWN, button=MouseButton.RIGHT),
            PhysicalInputEvent(InputEventKind.MOUSE_UP, button=MouseButton.RIGHT),
            PhysicalInputEvent(InputEventKind.KEY_UP, key="shift"),
        ),
        final_keyboard_guard=lambda: FinalKeyboardGuardDecision.ADMITTED,
    )

    assert result.input_completed is None
    assert result.facts["release_cleanup_attempted"] is True
    assert result.facts["release_cleanup_completed"] is True
    assert ("mouse", executor.win32con.MOUSEEVENTF_RIGHTUP, 0) in sent
    assert (
        "key",
        0,
        executor._KEYEVENTF_SCANCODE | executor._KEYEVENTF_KEYUP,
    ) in sent
    assert executor.agent_holds_mouse_button("right") is False


def test_sequence_release_failure_keeps_mouse_owner_discoverable(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    sent: list[tuple[str, int, int]] = []
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_cursor_pos", lambda: (1, 1))
    monkeypatch.setattr(
        executor,
        "_mouse_input",
        lambda flags, data=0, **_kwargs: ("mouse", flags, data),
    )

    def send(inputs):
        sent.extend(inputs)
        if inputs[0] == (
            "mouse",
            executor.win32con.MOUSEEVENTF_LEFTUP,
            0,
        ):
            raise OSError("injected release uncertainty")

    monkeypatch.setattr(executor, "_send_inputs", send)
    with executor._held_mouse_lock:
        executor._held_mouse_buttons.clear()
    try:
        result = executor.execute_admitted_physical_sequence(
            (
                PhysicalInputEvent(
                    InputEventKind.MOUSE_DOWN,
                    button=MouseButton.LEFT,
                ),
            ),
            final_keyboard_guard=lambda: FinalKeyboardGuardDecision.ADMITTED,
        )

        assert result.input_completed is None
        assert result.facts["error_kind"] == "release_cleanup_incomplete"
        assert result.facts["release_cleanup_completed"] is False
        assert executor.agent_holds_mouse_button("left") is True
        assert sent.count(
            ("mouse", executor.win32con.MOUSEEVENTF_LEFTUP, 0)
        ) == 2
    finally:
        with executor._held_mouse_lock:
            executor._held_mouse_buttons.clear()


def test_admitted_hotkey_repeats_inside_one_backend_call(monkeypatch) -> None:
    executor, sent = _keyboard_executor_mocks(monkeypatch)

    result = executor.ADMITTED_PHYSICAL_HOTKEY_BACKEND(
        HotkeyChord(("s",)),
        hwnd=41,
        repeat=3,
    )

    assert result.input_completed is True
    assert len(sent) == 6
    assert result.facts["requested_repetitions"] == 3
    assert result.facts["completed_repetitions"] == 3


def test_sequence_accepts_repeated_down_and_standalone_up_as_device_events(
    monkeypatch,
) -> None:
    from winpilot.action import executor

    sent: list[tuple[str, int, int]] = []
    with executor._held_mouse_lock:
        executor._held_mouse_buttons.clear()
    monkeypatch.setattr(executor, "_dry_run", lambda: False)
    monkeypatch.setattr(executor, "_vk_of", lambda key: {"shift": 0x10}[key])
    monkeypatch.setattr(
        executor,
        "_hardware_key_input",
        lambda vk, key_up=False: ("key", vk, int(key_up)),
    )
    monkeypatch.setattr(
        executor,
        "_mouse_button_input",
        lambda flags, data: ("mouse", flags, data),
    )
    monkeypatch.setattr(executor, "_send_inputs", lambda inputs: sent.extend(inputs))

    events = (
        PhysicalInputEvent(InputEventKind.MOUSE_UP, button=MouseButton.LEFT),
        PhysicalInputEvent(InputEventKind.MOUSE_DOWN, button=MouseButton.LEFT),
        PhysicalInputEvent(InputEventKind.MOUSE_DOWN, button=MouseButton.LEFT),
        PhysicalInputEvent(InputEventKind.MOUSE_UP, button=MouseButton.LEFT),
        PhysicalInputEvent(InputEventKind.KEY_UP, key="shift"),
        PhysicalInputEvent(InputEventKind.KEY_DOWN, key="shift"),
        PhysicalInputEvent(InputEventKind.KEY_DOWN, key="shift"),
        PhysicalInputEvent(InputEventKind.KEY_UP, key="shift"),
    )

    result = executor.execute_admitted_physical_sequence(events)

    assert result.input_completed is True
    assert result.facts["completed_events"] == len(events)
    assert sent == [
        ("mouse", executor.win32con.MOUSEEVENTF_LEFTUP, 0),
        ("mouse", executor.win32con.MOUSEEVENTF_LEFTDOWN, 0),
        ("mouse", executor.win32con.MOUSEEVENTF_LEFTDOWN, 0),
        ("mouse", executor.win32con.MOUSEEVENTF_LEFTUP, 0),
        ("key", 0x10, 1),
        ("key", 0x10, 0),
        ("key", 0x10, 0),
        ("key", 0x10, 1),
    ]
    assert result.facts.get("release_cleanup_attempted", False) is False
    assert executor.agent_holds_mouse_button("left") is False
