from __future__ import annotations

import pytest

from winpilot.vnext.bridge.initial_target import (
    InitialTargetMode,
    InitialTargetResolutionError,
    resolve_initial_target,
)


PROCESS_IDENTITY = "process-" + "a" * 32


def _handoff(
    *,
    required: bool,
    strategy: str,
    yielded: bool,
) -> dict[str, object]:
    return {
        "required": required,
        "strategy": strategy,
        "yielded": yielded,
        "control_surface_pid": 900 if required else 0,
        "prepared_at": 5_000 if required else 0,
        "reason": "",
    }


def _context(
    *,
    required: bool = True,
    strategy: str = "foreground_after_control_surface_yield",
    yielded: bool = True,
    target: object = ...,
) -> dict[str, object]:
    context: dict[str, object] = {
        "format": "execution-context/1",
        "entrypoint": "native_agent_cockpit",
        "task_id": "task-1",
        "submitted_at": 5_000,
        "desktop_handoff": _handoff(
            required=required,
            strategy=strategy,
            yielded=yielded,
        ),
    }
    if target is not ...:
        context["target_window"] = target
    return context


def _target(*, captured_at: int = 5_000) -> dict[str, object]:
    return {
        "format": "desktop-target/2",
        "hwnd": 101,
        "root_hwnd": 100,
        "title": "Unknown visual surface",
        "process_id": 7,
        "process_identity": PROCESS_IDENTITY,
        "class_name": "Surface",
        "rect": [10, 20, 810, 620],
        "captured_at": captured_at,
        "foreground_verified": True,
        "usable": True,
        "failure_type": "",
        "reason": "",
    }


@pytest.mark.parametrize("target", (..., None))
def test_exact_optional_handoff_enters_targetless_without_a_target(target) -> None:
    resolution = resolve_initial_target(_context(
        required=False,
        strategy="not_required",
        yielded=False,
        target=target,
    ))

    assert resolution.mode is InitialTargetMode.TARGETLESS
    assert resolution.hwnd == 0
    assert resolution.target is None
    assert resolution.targetless_reason == "optional"


def test_bound_handoff_reuses_existing_validation_and_conflict_guards() -> None:
    resolution = resolve_initial_target(
        _context(target=_target()),
        window_exists_resolver=lambda hwnd: hwnd == 101,
        process_id_resolver=lambda hwnd: 7 if hwnd == 101 else 0,
        now_ms=5_500,
    )

    assert resolution.mode is InitialTargetMode.BOUND
    assert resolution.hwnd == 101
    assert resolution.target is not None
    assert resolution.target["process_identity"] == PROCESS_IDENTITY
    thawed = resolution.target_dict()
    assert thawed == _target()
    assert isinstance(thawed["rect"], list)
    thawed["rect"][0] = 999
    assert resolution.target["rect"][0] == 10
    assert resolution.target_dict() == _target()
    assert resolution.targetless_reason is None


@pytest.mark.parametrize(
    "rewrite",
    (
        lambda context: "not-an-object",
        lambda context: {**context, "format": "execution-context/0"},
        lambda context: {**context, "entrypoint": "api"},
        lambda context: {**context, "entrypoint": "web_workbench"},
        lambda context: {
            **context,
            "desktop_handoff": {
                **context["desktop_handoff"],
                "required": 0,
            },
        },
        lambda context: {
            **context,
            "desktop_handoff": {
                **context["desktop_handoff"],
                "strategy": 1,
            },
        },
        lambda context: {
            **context,
            "desktop_handoff": {
                **context["desktop_handoff"],
                "yielded": 0,
            },
        },
    ),
)
def test_malformed_context_never_collapses_to_targetless(rewrite) -> None:
    context = _context(
        required=False,
        strategy="not_required",
        yielded=False,
        target=None,
    )

    with pytest.raises(
        InitialTargetResolutionError,
        match="execution context|handoff",
    ):
        resolve_initial_target(rewrite(context))


@pytest.mark.parametrize(
    "rewrite",
    (
        lambda context: {
            **context,
            "desktop_handoff": {
                **context["desktop_handoff"],
                "strategy": "foreground_after_control_surface_yield",
            },
        },
        lambda context: {
            **context,
            "desktop_handoff": {
                **context["desktop_handoff"],
                "yielded": True,
            },
        },
        lambda context: {**context, "target_window": {}},
        lambda context: {**context, "target_window": []},
        lambda context: {**context, "target_window": _target()},
    ),
)
def test_optional_targetless_rejects_strategy_yield_and_target_conflicts(
    rewrite,
) -> None:
    context = _context(
        required=False,
        strategy="not_required",
        yielded=False,
        target=None,
    )

    with pytest.raises(InitialTargetResolutionError):
        resolve_initial_target(rewrite(context))


def test_required_handoff_with_absent_target_remains_fail_closed() -> None:
    with pytest.raises(
        InitialTargetResolutionError,
        match="desktop target was not captured",
    ) as missing:
        resolve_initial_target(_context(target=None), now_ms=5_500)

    assert missing.value.failure_type == "target_binding_missing"


def test_required_handoff_accepts_only_a_fresh_all_zero_absence_report() -> None:
    unavailable = {
        "format": "desktop-target/2",
        "hwnd": 0,
        "root_hwnd": 0,
        "title": "",
        "process_id": 0,
        "process_identity": "",
        "class_name": "",
        "rect": [],
        "captured_at": 5_000,
        "foreground_verified": False,
        "usable": False,
        "failure_type": "target_binding_missing",
        "reason": "no foreground target",
    }

    resolution = resolve_initial_target(
        _context(target=unavailable),
        now_ms=5_500,
    )

    assert resolution.mode is InitialTargetMode.TARGETLESS
    assert resolution.targetless_reason == "absent"


@pytest.mark.parametrize(
    "rewrite",
    (
        lambda target: {**target, "hwnd": 101},
        lambda target: {**target, "process_id": 7},
        lambda target: {**target, "process_identity": PROCESS_IDENTITY},
        lambda target: {**target, "rect": [0, 0, 10, 10]},
        lambda target: {**target, "foreground_verified": True},
        lambda target: {**target, "captured_at": 0},
        lambda target: {**target, "captured_at": 1},
        lambda target: {**target, "failure_type": "capture_failed"},
        lambda target: {**target, "extra": True},
    ),
)
def test_required_absence_rejects_partial_stale_or_noncanonical_reports(
    rewrite,
) -> None:
    unavailable = {
        "format": "desktop-target/2",
        "hwnd": 0,
        "root_hwnd": 0,
        "title": "",
        "process_id": 0,
        "process_identity": "",
        "class_name": "",
        "rect": [],
        "captured_at": 5_000,
        "foreground_verified": False,
        "usable": False,
        "failure_type": "target_binding_missing",
        "reason": "no foreground target",
    }

    with pytest.raises(InitialTargetResolutionError):
        changed = rewrite(unavailable)
        resolve_initial_target(
            _context(target=changed),
            now_ms=20_001 if changed.get("captured_at") == 1 else 5_500,
        )


@pytest.mark.parametrize(
    "rewrite",
    (
        lambda context: {**context, "extra": True},
        lambda context: {key: value for key, value in context.items() if key != "task_id"},
        lambda context: {**context, "task_id": False},
        lambda context: {**context, "task_id": ""},
        lambda context: {**context, "submitted_at": "5000"},
        lambda context: {
            **context,
            "desktop_handoff": {**context["desktop_handoff"], "extra": True},
        },
        lambda context: {
            **context,
            "desktop_handoff": {
                **context["desktop_handoff"], "control_surface_pid": "0",
            },
        },
        lambda context: {
            **context,
            "desktop_handoff": {
                **context["desktop_handoff"], "prepared_at": False,
            },
        },
        lambda context: {
            **context,
            "desktop_handoff": {**context["desktop_handoff"], "reason": 1},
        },
    ),
)
def test_unknown_or_malformed_raw_fields_never_enter_targetless(rewrite) -> None:
    context = _context(
        required=False,
        strategy="not_required",
        yielded=False,
        target=None,
    )

    with pytest.raises(InitialTargetResolutionError):
        resolve_initial_target(rewrite(context), now_ms=5_500)


def test_required_handoff_rejects_yield_conflict_before_target_resolution() -> None:
    with pytest.raises(InitialTargetResolutionError) as conflict:
        resolve_initial_target(_context(yielded=False, target=None))

    assert conflict.value.failure_type == "control_surface_handoff_failed"


def test_bound_handoff_rejects_stale_target_and_control_surface_conflict() -> None:
    with pytest.raises(InitialTargetResolutionError) as stale:
        resolve_initial_target(
            _context(target=_target(captured_at=5_000)),
            window_exists_resolver=lambda _hwnd: True,
            now_ms=25_001,
        )
    assert stale.value.failure_type == "target_binding_stale"

    with pytest.raises(InitialTargetResolutionError) as conflict:
        resolve_initial_target(
            _context(target=_target()),
            window_exists_resolver=lambda _hwnd: True,
            process_id_resolver=lambda _hwnd: 900,
            now_ms=5_500,
        )
    assert conflict.value.failure_type == "control_surface_handoff_failed"
