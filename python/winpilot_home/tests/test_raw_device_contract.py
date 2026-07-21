from __future__ import annotations

import ast
import json
import math
from pathlib import Path
import random

import pytest

from winpilot.action.raw_device import (
    RawActionContext,
    RawActionReceipt,
    RawBackendResult,
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

    return RawDeviceExecutor(scroll_backend=backend)


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
    with pytest.raises(TypeError, match="scroll_backend"):
        RawDeviceExecutor()
    with pytest.raises(TypeError, match="must be callable"):
        RawDeviceExecutor(scroll_backend=None)


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
    executor = RawDeviceExecutor(
        scroll_backend=lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True},
    )

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

    receipt = RawDeviceExecutor(scroll_backend=backend).scroll(
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

    receipt = RawDeviceExecutor(scroll_backend=backend).scroll(
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
        RawActionReceipt(MutationOutcome.APPLIED)
    with pytest.raises(ValueError, match="backend_result contradicts"):
        RawActionReceipt(
            MutationOutcome.OUTCOME_UNKNOWN,
            RawBackendResult(True, {"ok": True}),
            "uncertain",
        )
    with pytest.raises(ValueError, match="backend_result contradicts"):
        RawActionReceipt(
            MutationOutcome.APPLIED,
            RawBackendResult(False, {"ok": False}),
        )
    with pytest.raises(ValueError, match="requires error_kind"):
        RawActionReceipt(MutationOutcome.NOT_STARTED)
    with pytest.raises(ValueError, match="requires error_kind"):
        RawActionReceipt(MutationOutcome.OUTCOME_UNKNOWN)


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
