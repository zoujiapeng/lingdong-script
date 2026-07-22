from __future__ import annotations

import base64
from dataclasses import replace
import hashlib

import cv2
import numpy as np
import pytest

import winpilot.vnext.verification.action_effect as action_effect_module
from winpilot.vnext.mission import (
    ACTION_EFFECT_CHANGE_RATIO_THRESHOLD,
    ACTION_EFFECT_EVIDENCE_KIND,
    ActionEffectReason,
    ActionEffectSummary,
    InvalidMissionEvent,
    LocalVisualChange,
)
from winpilot.vnext.runtime import RuntimeFrame
from winpilot.vnext.verification import (
    ActionEffectVerificationRequest,
    DeterministicActionEffectVerifier,
)


def _image(
    *,
    width: int = 100,
    height: int = 100,
    changed_pixels: int = 0,
    changed_value: int = 255,
) -> str:
    pixels = np.zeros((height, width, 3), dtype=np.uint8)
    if changed_pixels:
        pixels.reshape(-1, 3)[:changed_pixels] = changed_value
    ok, encoded = cv2.imencode(".png", pixels)
    assert ok
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def _frame(
    frame_id: int,
    *,
    image_b64: str | None = None,
    evidence_ref: str | None = None,
    hwnd: int = 101,
    epoch: int = 3,
    region: tuple[int, int, int, int] = (0, 0, 100, 100),
    scope: str = "target_window",
    submitted_size: tuple[int, int] = (100, 100),
) -> RuntimeFrame:
    image = image_b64 or _image()
    return RuntimeFrame(
        frame_id=frame_id,
        epoch=epoch,
        hwnd=hwnd,
        region=region,
        scope=scope,
        submitted_size=submitted_size,
        image_b64=image,
        image_sha256=hashlib.sha256(image.encode("ascii")).hexdigest(),
        evidence_refs=(evidence_ref or f"frame-evidence-{frame_id}",),
    )


def _request(
    before: RuntimeFrame,
    after: RuntimeFrame,
    *,
    input_completed: bool = True,
    target_binding_valid: bool = True,
) -> ActionEffectVerificationRequest:
    return ActionEffectVerificationRequest(
        mission_id="mission-1",
        run_id="run-1",
        obligation_id="obligation-1",
        action_id="decision-1",
        input_completed=input_completed,
        before_frame=before,
        after_frame=after,
        before_frame_evidence_ref=before.evidence_refs[0],
        after_frame_evidence_ref=after.evidence_refs[0],
        before_frame_payload_hash="a" * 64,
        after_frame_payload_hash="b" * 64,
        target_binding_valid=target_binding_valid,
    )


def test_exact_authoritative_pixels_are_unchanged_and_evidence_bound() -> None:
    image = _image()
    before = _frame(1, image_b64=image)
    after = _frame(2, image_b64=image)

    result = DeterministicActionEffectVerifier().verify(
        _request(before, after)
    )

    summary = result.summary
    assert summary.local_visual_change is LocalVisualChange.UNCHANGED
    assert summary.frame_comparable is True
    assert summary.change_ratio == 0.0
    assert summary.reason is ActionEffectReason.EXACT_PAYLOAD
    assert summary.semantic_completion == "unknown"
    assert result.evidence.id == summary.evidence_ref
    assert result.evidence.kind == ACTION_EFFECT_EVIDENCE_KIND
    assert result.evidence.payload_hash == summary.payload_hash
    assert ActionEffectSummary.from_dict(summary.to_dict()) == summary


@pytest.mark.parametrize(
    ("changed_pixels", "changed_value"),
    ((1, 1), (1, 255), (10, 255)),
)
def test_any_aligned_pixel_delta_is_reported_as_changed(
    changed_pixels: int,
    changed_value: int,
) -> None:
    before = _frame(1, image_b64=_image())
    after = _frame(2, image_b64=_image(
        changed_pixels=changed_pixels,
        changed_value=changed_value,
    ))

    summary = DeterministicActionEffectVerifier().verify(
        _request(before, after)
    ).summary

    assert summary.local_visual_change is LocalVisualChange.CHANGED
    assert summary.frame_comparable is True
    assert summary.reason is ActionEffectReason.ALIGNED_PIXEL_DELTA
    assert summary.change_ratio == pytest.approx(changed_pixels / 10_000)
    assert summary.change_ratio > ACTION_EFFECT_CHANGE_RATIO_THRESHOLD


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("hwnd", 202),
        ("epoch", 4),
        ("region", (1, 0, 100, 100)),
        ("scope", "full_desktop"),
        ("submitted_size", (90, 100)),
    ),
)
def test_different_observation_domains_are_unknown(field: str, value: object) -> None:
    before = _frame(1)
    after = replace(_frame(2), **{field: value})

    summary = DeterministicActionEffectVerifier().verify(
        _request(before, after)
    ).summary

    assert summary.local_visual_change is LocalVisualChange.UNKNOWN
    assert summary.frame_comparable is False
    assert summary.change_ratio is None
    assert summary.reason is ActionEffectReason.FRAME_NOT_COMPARABLE


def test_decode_and_decoded_size_failures_remain_unknown() -> None:
    invalid = base64.b64encode(b"not-an-image").decode("ascii")
    decoded_failure = DeterministicActionEffectVerifier().verify(_request(
        _frame(1, image_b64=invalid),
        _frame(2, image_b64=invalid),
    )).summary
    size_failure = DeterministicActionEffectVerifier().verify(_request(
        _frame(1, submitted_size=(99, 100)),
        _frame(2, submitted_size=(99, 100)),
    )).summary

    assert decoded_failure.reason is ActionEffectReason.DECODE_FAILED
    assert decoded_failure.local_visual_change is LocalVisualChange.UNKNOWN
    assert size_failure.reason is ActionEffectReason.DECODED_SIZE_MISMATCH
    assert size_failure.local_visual_change is LocalVisualChange.UNKNOWN


def test_input_and_target_facts_short_circuit_pixel_interpretation() -> None:
    before, after = _frame(1), _frame(2)

    not_executed = DeterministicActionEffectVerifier().verify(
        _request(before, after, input_completed=False)
    ).summary
    target_changed = DeterministicActionEffectVerifier().verify(
        _request(before, after, target_binding_valid=False)
    ).summary

    assert not_executed.reason is ActionEffectReason.INPUT_NOT_EXECUTED
    assert not_executed.frame_comparable is False
    assert target_changed.reason is ActionEffectReason.TARGET_BINDING_CHANGED
    assert target_changed.frame_comparable is False


def test_comparison_exception_is_unknown_not_changed(monkeypatch) -> None:
    before = _frame(1, image_b64=_image())
    after = _frame(2, image_b64=_image(changed_pixels=10))

    monkeypatch.setattr(
        action_effect_module.cv2,
        "absdiff",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("compare failed")),
    )
    summary = DeterministicActionEffectVerifier().verify(
        _request(before, after)
    ).summary

    assert summary.local_visual_change is LocalVisualChange.UNKNOWN
    assert summary.frame_comparable is False
    assert summary.reason is ActionEffectReason.COMPARISON_FAILED


@pytest.mark.parametrize(
    "mutation",
    (
        {"format": "action-effect/9"},
        {"local_visual_change": "changed"},
        {"semantic_completion": "done"},
        {"action_id": 1},
        {"change_ratio": float("nan")},
        {"payload_hash": "0" * 64},
        {"extra": True},
    ),
)
def test_action_effect_wire_contract_fails_closed(mutation: dict) -> None:
    result = DeterministicActionEffectVerifier().verify(
        _request(_frame(1), _frame(2))
    )
    raw = result.summary.to_dict()
    raw.update(mutation)

    with pytest.raises(InvalidMissionEvent):
        ActionEffectSummary.from_dict(raw)


def test_action_effect_result_is_deterministic_for_the_same_frozen_request() -> None:
    request = _request(
        _frame(1, image_b64=_image()),
        _frame(2, image_b64=_image(changed_pixels=10)),
    )
    verifier = DeterministicActionEffectVerifier()

    first = verifier.verify(request)
    second = verifier.verify(request)

    assert first == second
    assert first.summary.to_dict() == second.summary.to_dict()
