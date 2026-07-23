from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import random

import pytest

from winpilot.agent.frame_authority import FrameAuthority
from winpilot.visual_runtime.commit_admission import (
    COMMIT_ADMISSION_REQUEST_FORMAT,
    CommitAdmissionError,
    CommitAdmissionGate,
    CommitInputKind,
    CommitAdmissionReason,
    CommitAdmissionRequest,
    CommitAdmissionResult,
    CommitAdmissionStatus,
    CommitEnvironment,
    CommitRoute,
    FrameCommitIdentity,
    TargetCommitIdentity,
)


SHA = "a" * 64
PROCESS_IDENTITY = "process-" + "1" * 32


def _frame(**changes) -> FrameCommitIdentity:
    value = FrameCommitIdentity(
        frame_id=7,
        epoch=3,
        hwnd=101,
        region=(10, 20, 800, 600),
        scope="target_window",
        submitted_size=(800, 600),
        image_sha256=SHA,
    )
    return replace(value, **changes)


def _target(**changes) -> TargetCommitIdentity:
    value = TargetCommitIdentity(
        binding_id="target-1",
        hwnd=101,
        root_hwnd=100,
        process_id=444,
        process_identity=PROCESS_IDENTITY,
        epoch=3,
        window_region=(10, 20, 800, 600),
    )
    return replace(value, **changes)


def _request(**changes) -> CommitAdmissionRequest:
    value = CommitAdmissionRequest(
        request_id="commit-1",
        frame=_frame(),
        target=_target(),
        route="physical",
        input_kind="point",
        screen_point=(150, 140),
    )
    return replace(value, **changes)


def _keyboard_request(**changes) -> CommitAdmissionRequest:
    values = {
        "input_kind": CommitInputKind.KEYBOARD,
        "screen_point": None,
    }
    values.update(changes)
    return _request(**values)


def _environment(request: CommitAdmissionRequest, **changes) -> CommitEnvironment:
    point_input = request.input_kind is CommitInputKind.POINT
    values = dict(
        target=request.target,
        pointer_released=True,
        mutation_allowed=True,
        stop_requested=False,
        canceled=False,
        foreground_hwnd=101,
        foreground_root_hwnd=request.target.root_hwnd,
        foreground_process_id=request.target.process_id,
        foreground_process_identity=request.target.process_identity,
        hit_test_point=request.screen_point if point_input else None,
        hit_hwnd=111 if point_input else None,
        hit_root_hwnd=request.target.root_hwnd if point_input else None,
        hit_process_id=request.target.process_id if point_input else None,
        hit_process_identity=request.target.process_identity if point_input else None,
    )
    values.update(changes)
    return CommitEnvironment(**values)


def _reject_reason(
    request: CommitAdmissionRequest,
    observed: CommitEnvironment,
) -> CommitAdmissionReason:
    calls = []
    result = CommitAdmissionGate(lambda _request: observed).admit_and_commit(
        request, lambda: calls.append("called"),
    )
    assert calls == []
    assert result.value is None
    assert result.admission.status is CommitAdmissionStatus.NOT_STARTED
    assert result.admission.callback_invoked is False
    return result.admission.reason


def test_admitted_callback_receives_frozen_request_once_and_value_is_forwarded() -> None:
    request = _request()
    observed_requests = []
    callback_requests = []

    result = CommitAdmissionGate(
        lambda bound: observed_requests.append(bound) or _environment(bound)
    ).admit_and_commit(
        request,
        lambda: callback_requests.append(request) or {"backend": "returned"},
    )

    assert observed_requests == [request]
    assert callback_requests == [request]
    assert result.value == {"backend": "returned"}
    assert result.admission == CommitAdmissionResult(
        request_id="commit-1",
        status="admitted",
        reason="admitted",
        callback_invoked=True,
    )


def test_callback_exception_escapes_after_one_invocation() -> None:
    request = _request()
    calls = []

    def fail() -> None:
        calls.append(request)
        raise OSError("backend failed after entry")

    with pytest.raises(OSError, match="after entry"):
        CommitAdmissionGate(lambda bound: _environment(bound)).admit_and_commit(
            request, fail,
        )
    assert calls == [request]


@pytest.mark.parametrize(
    "input_kind",
    [CommitInputKind.POINT, CommitInputKind.KEYBOARD],
)
def test_frame_authority_pin_accepts_contract_identity_and_rejects_stale_before_gate(
    input_kind,
) -> None:
    authority = FrameAuthority()
    state = authority.commit(
        epoch=3, hwnd=101, region=(10, 20, 800, 600),
        scope="target_window", reason="test", submitted_size=(800, 600),
        image_b64="cGl4ZWxz", captured_at=1.0,
    )
    frame = FrameCommitIdentity(
        state.frame_id, state.epoch, state.hwnd, state.region, state.scope,
        state.submitted_size, state.image_sha256,
    )
    request = (
        _request(frame=frame)
        if input_kind is CommitInputKind.POINT
        else _keyboard_request(frame=frame)
    )
    callbacks = []
    gate = CommitAdmissionGate(lambda bound: _environment(bound))

    admitted, result = authority.run_if_current(
        request.frame,
        lambda: gate.admit_and_commit(request, lambda: callbacks.append("input")),
    )
    assert admitted is True
    assert result is not None and result.admission.status is CommitAdmissionStatus.ADMITTED
    assert callbacks == ["input"]

    stale = replace(request.frame, image_sha256="b" * 64)
    assert authority.run_if_current(
        stale,
        lambda: gate.admit_and_commit(request, lambda: callbacks.append("replayed")),
    ) == (False, None)
    assert callbacks == ["input"]


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"stop_requested": True}, CommitAdmissionReason.STOP_REQUESTED),
        ({"canceled": True}, CommitAdmissionReason.CANCELED),
        ({"mutation_allowed": False}, CommitAdmissionReason.MUTATION_NOT_ALLOWED),
        ({"pointer_released": False}, CommitAdmissionReason.POINTER_NOT_RELEASED),
        ({"target": None}, CommitAdmissionReason.TARGET_UNAVAILABLE),
        ({"target": _target(root_hwnd=99)}, CommitAdmissionReason.TARGET_ROOT_CHANGED),
        ({"target": _target(window_region=(11, 20, 800, 600))}, CommitAdmissionReason.TARGET_GEOMETRY_CHANGED),
        ({"target": _target(process_id=445)}, CommitAdmissionReason.TARGET_CHANGED),
        ({
            "foreground_hwnd": None,
            "foreground_root_hwnd": None,
            "foreground_process_id": None,
            "foreground_process_identity": None,
        }, CommitAdmissionReason.FOREGROUND_UNAVAILABLE),
        ({"foreground_root_hwnd": 99}, CommitAdmissionReason.FOREGROUND_CHANGED),
        ({"foreground_process_id": 445}, CommitAdmissionReason.FOREGROUND_CHANGED),
        ({
            "hit_test_point": None,
            "hit_hwnd": None,
            "hit_root_hwnd": None,
            "hit_process_id": None,
            "hit_process_identity": None,
        }, CommitAdmissionReason.HIT_TEST_UNAVAILABLE),
        ({"hit_test_point": (151, 140)}, CommitAdmissionReason.HIT_TEST_CHANGED),
        ({"hit_root_hwnd": 99}, CommitAdmissionReason.HIT_TEST_CHANGED),
        ({"hit_process_id": 445}, CommitAdmissionReason.HIT_TEST_CHANGED),
    ],
)
def test_every_dynamic_identity_mismatch_fails_before_callback(changes, reason) -> None:
    request = _request()
    assert _reject_reason(request, _environment(request, **changes)) is reason


@pytest.mark.parametrize(
    ("case_request", "reason"),
    [
        (_request(screen_point=(0, 0)), CommitAdmissionReason.POINT_OUTSIDE_FRAME),
        (_request(screen_point=(900, 700)), CommitAdmissionReason.POINT_OUTSIDE_FRAME),
        (
            _request(
                frame=_frame(region=(0, 0, 1000, 800)),
                target=_target(window_region=(100, 100, 400, 300)),
                screen_point=(90, 110),
            ),
            CommitAdmissionReason.POINT_OUTSIDE_TARGET,
        ),
    ],
)
def test_point_is_mechanically_bounded(case_request, reason) -> None:
    assert _reject_reason(case_request, _environment(case_request)) is reason


def test_background_route_does_not_require_foreground_but_still_requires_hit() -> None:
    request = _request(route=CommitRoute.BACKGROUND_WINDOW)
    environment = _environment(
        request,
        foreground_hwnd=None,
        foreground_root_hwnd=None,
        foreground_process_id=None,
        foreground_process_identity=None,
    )
    calls = []
    result = CommitAdmissionGate(lambda _bound: environment).admit_and_commit(
        request, lambda: calls.append(request) or "ok",
    )
    assert result.admission.status is CommitAdmissionStatus.ADMITTED
    assert result.value == "ok"
    assert calls == [request]

    missing_hit = _environment(
        request,
        foreground_hwnd=None,
        foreground_root_hwnd=None,
        foreground_process_id=None,
        foreground_process_identity=None,
        hit_test_point=None,
        hit_hwnd=None,
        hit_root_hwnd=None,
        hit_process_id=None,
        hit_process_identity=None,
    )
    assert _reject_reason(request, missing_hit) is CommitAdmissionReason.HIT_TEST_UNAVAILABLE


def test_keyboard_admission_requires_foreground_but_never_requires_hit_test() -> None:
    request = _keyboard_request()
    environment = _environment(
        request,
        hit_test_point=None,
        hit_hwnd=None,
        hit_root_hwnd=None,
        hit_process_id=None,
        hit_process_identity=None,
    )
    calls = []

    result = CommitAdmissionGate(lambda _bound: environment).admit_and_commit(
        request, lambda: calls.append(request) or "typed",
    )

    assert result.admission.status is CommitAdmissionStatus.ADMITTED
    assert result.value == "typed"
    assert calls == [request]

    no_foreground = replace(
        environment,
        foreground_hwnd=None,
        foreground_root_hwnd=None,
        foreground_process_id=None,
        foreground_process_identity=None,
    )
    assert _reject_reason(
        request, no_foreground,
    ) is CommitAdmissionReason.FOREGROUND_UNAVAILABLE


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"pointer_released": False}, CommitAdmissionReason.POINTER_NOT_RELEASED),
        ({"target": None}, CommitAdmissionReason.TARGET_UNAVAILABLE),
        ({"target": _target(epoch=4)}, CommitAdmissionReason.TARGET_CHANGED),
        ({"target": _target(root_hwnd=99)}, CommitAdmissionReason.TARGET_ROOT_CHANGED),
        (
            {"target": _target(window_region=(11, 20, 800, 600))},
            CommitAdmissionReason.TARGET_GEOMETRY_CHANGED,
        ),
        ({"target": _target(process_id=445)}, CommitAdmissionReason.TARGET_CHANGED),
        (
            {
                "foreground_hwnd": None,
                "foreground_root_hwnd": None,
                "foreground_process_id": None,
                "foreground_process_identity": None,
            },
            CommitAdmissionReason.FOREGROUND_UNAVAILABLE,
        ),
        ({"foreground_root_hwnd": 99}, CommitAdmissionReason.FOREGROUND_CHANGED),
        ({"foreground_process_id": 445}, CommitAdmissionReason.FOREGROUND_CHANGED),
    ],
)
def test_keyboard_dynamic_identity_mismatch_fails_before_callback(
    changes, reason,
) -> None:
    request = _keyboard_request()
    environment = _environment(
        request,
        hit_test_point=None,
        hit_hwnd=None,
        hit_root_hwnd=None,
        hit_process_id=None,
        hit_process_identity=None,
        **changes,
    )
    assert _reject_reason(request, environment) is reason


def test_observation_failure_or_wrong_type_is_definite_not_started() -> None:
    request = _request()
    calls = []

    def broken(_bound):
        raise RuntimeError("observation unavailable")

    failed = CommitAdmissionGate(broken).admit_and_commit(
        request, lambda: calls.append(request),
    )
    invalid = CommitAdmissionGate(lambda _bound: {}).admit_and_commit(
        request, lambda: calls.append(request),
    )

    assert calls == []
    assert failed.admission.reason is CommitAdmissionReason.OBSERVATION_FAILED
    assert invalid.admission.reason is CommitAdmissionReason.OBSERVATION_INVALID
    assert failed.admission.status is invalid.admission.status is CommitAdmissionStatus.NOT_STARTED


def test_contract_round_trip_and_unknown_fields_fail_closed() -> None:
    for request in (_request(), _keyboard_request()):
        assert CommitAdmissionRequest.from_dict(request.to_dict()) == request
        assert request.to_dict()["input_kind"] == request.input_kind.value
    result = CommitAdmissionResult(
        request_id=request.request_id,
        status="not_started",
        reason="target_changed",
        callback_invoked=False,
    )
    assert CommitAdmissionResult.from_dict(result.to_dict()) == result

    raw = request.to_dict()
    raw["tool"] = "click"
    with pytest.raises(CommitAdmissionError, match="extra=.*tool"):
        CommitAdmissionRequest.from_dict(raw)
    raw = request.to_dict()
    raw.pop("format")
    with pytest.raises(CommitAdmissionError, match="missing=.*format"):
        CommitAdmissionRequest.from_dict(raw)
    raw = request.to_dict()
    raw.pop("input_kind")
    with pytest.raises(CommitAdmissionError, match="missing=.*input_kind"):
        CommitAdmissionRequest.from_dict(raw)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: _frame(frame_id=True),
        lambda: _frame(hwnd=0),
        lambda: _frame(region=(0, 0, 0, 20)),
        lambda: _frame(submitted_size=(0, 20)),
        lambda: _frame(image_sha256="A" * 64),
        lambda: _target(process_id=0),
        lambda: _target(root_hwnd=0),
        lambda: _target(window_region=(0, 0, 1, 0)),
        lambda: _request(route=""),
        lambda: _request(input_kind="mouse"),
        lambda: _request(input_kind=True),
        lambda: _request(screen_point=(True, 1)),
        lambda: _request(route="auto"),
        lambda: _environment(_request(), mutation_allowed=1),
        lambda: _environment(_request(), foreground_root_hwnd=None),
        lambda: replace(_request(), format="commit-admission-request/1"),
        lambda: replace(_environment(_request()), format="commit-environment/0"),
    ],
)
def test_malformed_contract_values_are_rejected(factory) -> None:
    with pytest.raises(CommitAdmissionError):
        factory()


@pytest.mark.parametrize(
    "factory",
    [
        lambda: _request(screen_point=None),
        lambda: _request(input_kind=CommitInputKind.KEYBOARD),
        lambda: _keyboard_request(screen_point=(150, 140)),
        lambda: _keyboard_request(input_kind=CommitInputKind.POINT),
        lambda: _keyboard_request(route=CommitRoute.BACKGROUND_WINDOW),
    ],
)
def test_input_kind_and_screen_point_combinations_fail_closed(factory) -> None:
    with pytest.raises(CommitAdmissionError):
        factory()


def test_frame_and_target_must_bind_same_hwnd_and_epoch() -> None:
    with pytest.raises(CommitAdmissionError, match="frame and target identity disagree"):
        _request(target=_target(hwnd=102))
    with pytest.raises(CommitAdmissionError, match="frame and target identity disagree"):
        _request(target=_target(epoch=4))


def test_result_cannot_launder_a_rejection_or_callback_state() -> None:
    with pytest.raises(CommitAdmissionError, match="NOT_STARTED"):
        CommitAdmissionResult("commit-1", "not_started", "target_changed", True)
    with pytest.raises(CommitAdmissionError, match="ADMITTED"):
        CommitAdmissionResult("commit-1", "admitted", "admitted", False)
    with pytest.raises(CommitAdmissionError, match="rejection reason"):
        CommitAdmissionResult("commit-1", "not_started", "admitted", False)


@pytest.mark.parametrize(
    "input_kind",
    [CommitInputKind.POINT, CommitInputKind.KEYBOARD],
)
def test_randomized_exact_replay_admits_and_stale_identity_rejects(
    input_kind,
) -> None:
    rng = random.Random(20260722)
    for index in range(50):
        left = rng.randint(-500, 500)
        top = rng.randint(-300, 300)
        width = rng.randint(50, 1000)
        height = rng.randint(50, 800)
        frame = FrameCommitIdentity(
            frame_id=index + 1,
            epoch=index,
            hwnd=1000 + index,
            region=(left, top, width, height),
            scope="target_window",
            submitted_size=(width, height),
            image_sha256=f"{index + 1:064x}",
        )
        target = TargetCommitIdentity(
            binding_id=f"binding-{index}", hwnd=frame.hwnd,
            root_hwnd=frame.hwnd, process_id=2000 + index,
            process_identity=f"process-{index + 1:032x}",
            epoch=frame.epoch, window_region=frame.region,
        )
        point = (left + width // 2, top + height // 2)
        request = CommitAdmissionRequest(
            request_id=f"request-{index}", frame=frame, target=target,
            route="physical",
            input_kind=input_kind,
            screen_point=(
                point if input_kind is CommitInputKind.POINT else None
            ),
        )
        observed = _environment(request)
        assert CommitAdmissionRequest.from_dict(request.to_dict()) == request
        assert CommitAdmissionGate(lambda _bound: observed).admit_and_commit(
            request, lambda: index,
        ).value == index
        stale = replace(observed, target=replace(target, epoch=target.epoch + 1))
        assert _reject_reason(request, stale) is CommitAdmissionReason.TARGET_CHANGED
        stale_foreground = replace(
            observed, foreground_process_id=target.process_id + 1,
        )
        assert _reject_reason(
            request, stale_foreground,
        ) is CommitAdmissionReason.FOREGROUND_CHANGED


def test_commit_admission_has_no_semantic_or_legacy_imports() -> None:
    from winpilot.visual_runtime import commit_admission

    tree = ast.parse(Path(commit_admission.__file__).read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    forbidden = ("agent", "task", "uia", "ocr", "mcp", "app", "controller", "raw_device")
    assert not any(any(token in module.lower() for token in forbidden) for module in imported)
    assert COMMIT_ADMISSION_REQUEST_FORMAT == "commit-admission-request/2"
