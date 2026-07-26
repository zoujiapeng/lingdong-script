"""Deterministic pre/post frame comparison for one accepted GUI action.

This module is read-only with respect to the desktop and Mission state.  It
classifies comparable pixels and constructs immutable evidence; Runtime owns
registry and event writes.  A visual change is deliberately not interpreted
as causality, task progress, or semantic completion.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, replace
import hashlib
import json
import re
from typing import Any, Protocol, runtime_checkable

import cv2
import numpy as np

from ..mission import (
    ACTION_EFFECT_CHANGE_RATIO_THRESHOLD,
    ACTION_EFFECT_EVIDENCE_KIND,
    ActionEffectReason,
    ActionEffectSummary,
    EvidenceRef,
    LocalVisualChange,
)

@runtime_checkable
class _RuntimeFramePort(Protocol):
    frame_id: int
    epoch: int
    hwnd: int
    region: tuple[int, int, int, int]
    scope: str
    submitted_size: tuple[int, int]
    image_b64: str
    image_sha256: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _FrozenFrame:
    frame_id: int
    epoch: int
    hwnd: int
    region: tuple[int, int, int, int]
    scope: str
    submitted_size: tuple[int, int]
    image_b64: str
    image_sha256: str
    evidence_refs: tuple[str, ...]


PIXEL_CHANNEL_DELTA_THRESHOLD = 0
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ActionEffectVerificationError(ValueError):
    """The deterministic action-effect request or result is incoherent."""


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ActionEffectVerificationError(f"{name} must be a non-empty string")
    return value


def _validate_frame(frame: Any, name: str) -> _RuntimeFramePort:
    try:
        conforms = isinstance(frame, _RuntimeFramePort)
    except Exception as exc:
        raise ActionEffectVerificationError(
            f"{name} must be a valid frame contract"
        ) from exc
    if not conforms:
        raise ActionEffectVerificationError(
            f"{name} must be a valid frame contract"
        )
    try:
        frame_id = frame.frame_id
        epoch = frame.epoch
        hwnd = frame.hwnd
        region = frame.region
        scope = frame.scope
        submitted_size = frame.submitted_size
        image_b64 = frame.image_b64
        image_sha256 = frame.image_sha256
        evidence_refs = frame.evidence_refs
    except Exception as exc:
        raise ActionEffectVerificationError(
            f"{name} must expose readable frame fields"
        ) from exc
    if type(frame_id) is not int or frame_id <= 0:
        raise ActionEffectVerificationError(
            f"{name}.frame_id must be a positive integer"
        )
    if type(epoch) is not int or epoch < 0:
        raise ActionEffectVerificationError(
            f"{name}.epoch must be a non-negative integer"
        )
    if type(hwnd) is not int or hwnd <= 0:
        raise ActionEffectVerificationError(
            f"{name}.hwnd must be a positive integer"
        )
    if (
        type(region) is not tuple
        or len(region) != 4
        or any(type(value) is not int for value in region)
        or region[2] <= 0
        or region[3] <= 0
    ):
        raise ActionEffectVerificationError(
            f"{name}.region must be (left, top, positive width, positive height)"
        )
    if (
        type(submitted_size) is not tuple
        or len(submitted_size) != 2
        or any(type(value) is not int for value in submitted_size)
        or min(submitted_size) <= 0
    ):
        raise ActionEffectVerificationError(
            f"{name}.submitted_size must contain two positive integers"
        )
    if type(scope) is not str or not _IDENTITY.fullmatch(scope):
        raise ActionEffectVerificationError(
            f"{name}.scope must be a canonical identity"
        )
    if type(image_b64) is not str or not image_b64:
        raise ActionEffectVerificationError(
            f"{name}.image_b64 must be a non-empty string"
        )
    try:
        payload_hash = hashlib.sha256(image_b64.encode("ascii")).hexdigest()
    except UnicodeEncodeError as exc:
        raise ActionEffectVerificationError(
            f"{name}.image_b64 must be ASCII"
        ) from exc
    if (
        type(image_sha256) is not str
        or not _SHA256.fullmatch(image_sha256)
        or image_sha256 != payload_hash
    ):
        raise ActionEffectVerificationError(
            f"{name}.image_sha256 must identify image_b64"
        )
    if (
        type(evidence_refs) is not tuple
        or any(
            type(value) is not str or not _IDENTITY.fullmatch(value)
            for value in evidence_refs
        )
        or tuple(sorted(set(evidence_refs))) != evidence_refs
    ):
        raise ActionEffectVerificationError(
            f"{name}.evidence_refs must be sorted unique identities"
        )
    return _FrozenFrame(
        frame_id=frame_id,
        epoch=epoch,
        hwnd=hwnd,
        region=region,
        scope=scope,
        submitted_size=submitted_size,
        image_b64=image_b64,
        image_sha256=image_sha256,
        evidence_refs=evidence_refs,
    )


@dataclass(frozen=True, slots=True)
class ActionEffectVerificationRequest:
    mission_id: str
    run_id: str
    obligation_id: str
    action_id: str
    input_completed: bool
    before_frame: _RuntimeFramePort
    after_frame: _RuntimeFramePort
    before_frame_evidence_ref: str
    after_frame_evidence_ref: str
    before_frame_payload_hash: str
    after_frame_payload_hash: str
    target_binding_valid: bool

    def __post_init__(self) -> None:
        for name in (
            "mission_id",
            "run_id",
            "obligation_id",
            "action_id",
            "before_frame_evidence_ref",
            "after_frame_evidence_ref",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("before_frame_payload_hash", "after_frame_payload_hash"):
            value = getattr(self, name)
            if type(value) is not str or not _SHA256.fullmatch(value):
                raise ActionEffectVerificationError(
                    f"{name} must be a SHA-256 digest"
                )
        if type(self.input_completed) is not bool:
            raise ActionEffectVerificationError("input_completed must be boolean")
        if type(self.target_binding_valid) is not bool:
            raise ActionEffectVerificationError("target_binding_valid must be boolean")
        before_frame = _validate_frame(self.before_frame, "before_frame")
        after_frame = _validate_frame(self.after_frame, "after_frame")
        object.__setattr__(self, "before_frame", before_frame)
        object.__setattr__(self, "after_frame", after_frame)
        if after_frame.frame_id <= before_frame.frame_id:
            raise ActionEffectVerificationError(
                "after_frame must follow before_frame in authority commit order"
            )
        if self.before_frame_evidence_ref not in before_frame.evidence_refs:
            raise ActionEffectVerificationError(
                "before_frame evidence is outside the supplied frame"
            )
        if self.after_frame_evidence_ref not in after_frame.evidence_refs:
            raise ActionEffectVerificationError(
                "after_frame evidence is outside the supplied frame"
            )


@dataclass(frozen=True, slots=True)
class ActionEffectVerificationResult:
    summary: ActionEffectSummary
    evidence: EvidenceRef

    def __post_init__(self) -> None:
        if not isinstance(self.summary, ActionEffectSummary):
            raise ActionEffectVerificationError("summary must be ActionEffectSummary")
        if not isinstance(self.evidence, EvidenceRef):
            raise ActionEffectVerificationError("evidence must be EvidenceRef")
        summary = ActionEffectSummary.from_dict(self.summary.to_dict())
        evidence = self.evidence
        if (
            evidence.id != summary.evidence_ref
            or evidence.kind != ACTION_EFFECT_EVIDENCE_KIND
            or evidence.trust != "deterministic"
            or evidence.action_id != summary.action_id
            or evidence.frame_id != summary.after_frame_id
            or evidence.payload_hash != summary.payload_hash
        ):
            raise ActionEffectVerificationError(
                "action effect evidence does not bind the canonical summary"
            )


def _decode(frame: _RuntimeFramePort) -> tuple[np.ndarray | None, ActionEffectReason | None]:
    try:
        raw = base64.b64decode(frame.image_b64, validate=True)
        encoded = np.frombuffer(raw, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    except Exception:
        return None, ActionEffectReason.DECODE_FAILED
    if image is None or image.size == 0:
        return None, ActionEffectReason.DECODE_FAILED
    expected_width, expected_height = frame.submitted_size
    if image.shape[:2] != (expected_height, expected_width):
        return None, ActionEffectReason.DECODED_SIZE_MISMATCH
    return image, None


def _comparable(before: _RuntimeFramePort, after: _RuntimeFramePort) -> bool:
    return bool(
        before.hwnd == after.hwnd
        and before.epoch == after.epoch
        and before.region == after.region
        and before.scope == after.scope
        and before.submitted_size == after.submitted_size
    )


def _evidence_id(request: ActionEffectVerificationRequest, payload_hash: str) -> str:
    material = {
        "format": "action-effect-evidence-identity/0",
        "mission_id": request.mission_id,
        "run_id": request.run_id,
        "obligation_id": request.obligation_id,
        "action_id": request.action_id,
        "payload_hash": payload_hash,
    }
    digest = hashlib.sha256(
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return f"evidence-action-effect-{digest[:32]}"


class DeterministicActionEffectVerifier:
    """Classify aligned authoritative pixels without choosing recovery."""

    verifier_kind = "deterministic_action_effect"

    @staticmethod
    def _classification(
        request: ActionEffectVerificationRequest,
    ) -> tuple[bool, LocalVisualChange, float | None, ActionEffectReason]:
        if not request.input_completed:
            return (
                False,
                LocalVisualChange.UNKNOWN,
                None,
                ActionEffectReason.INPUT_NOT_EXECUTED,
            )
        if not request.target_binding_valid:
            return (
                False,
                LocalVisualChange.UNKNOWN,
                None,
                ActionEffectReason.TARGET_BINDING_CHANGED,
            )
        if not _comparable(request.before_frame, request.after_frame):
            return (
                False,
                LocalVisualChange.UNKNOWN,
                None,
                ActionEffectReason.FRAME_NOT_COMPARABLE,
            )
        before, before_error = _decode(request.before_frame)
        after, after_error = _decode(request.after_frame)
        decode_error = before_error or after_error
        if decode_error is not None:
            return False, LocalVisualChange.UNKNOWN, None, decode_error
        assert before is not None and after is not None
        try:
            if before.shape != after.shape:
                return (
                    False,
                    LocalVisualChange.UNKNOWN,
                    None,
                    ActionEffectReason.DECODED_SIZE_MISMATCH,
                )
            if request.before_frame.image_sha256 == request.after_frame.image_sha256:
                return (
                    True,
                    LocalVisualChange.UNCHANGED,
                    0.0,
                    ActionEffectReason.EXACT_PAYLOAD,
                )
            delta = cv2.absdiff(before, after)
            changed = int(
                (delta.max(axis=2) > PIXEL_CHANNEL_DELTA_THRESHOLD).sum()
            )
            total = int(before.shape[0] * before.shape[1])
            if total <= 0:
                raise ValueError("decoded frame has no pixels")
            ratio = changed / total
            return (
                True,
                (
                    LocalVisualChange.CHANGED
                    if ratio > ACTION_EFFECT_CHANGE_RATIO_THRESHOLD
                    else LocalVisualChange.UNCHANGED
                ),
                ratio,
                ActionEffectReason.ALIGNED_PIXEL_DELTA,
            )
        except Exception:
            return (
                False,
                LocalVisualChange.UNKNOWN,
                None,
                ActionEffectReason.COMPARISON_FAILED,
            )

    def verify(
        self,
        request: ActionEffectVerificationRequest,
    ) -> ActionEffectVerificationResult:
        if not isinstance(request, ActionEffectVerificationRequest):
            raise ActionEffectVerificationError(
                "request must be ActionEffectVerificationRequest"
            )
        comparable, change, ratio, reason = self._classification(request)
        pending = ActionEffectSummary(
            action_id=request.action_id,
            input_completed=request.input_completed,
            before_frame_id=request.before_frame.frame_id,
            after_frame_id=request.after_frame.frame_id,
            before_frame_evidence_ref=request.before_frame_evidence_ref,
            after_frame_evidence_ref=request.after_frame_evidence_ref,
            before_frame_payload_hash=request.before_frame_payload_hash,
            after_frame_payload_hash=request.after_frame_payload_hash,
            target_binding_valid=request.target_binding_valid,
            frame_comparable=comparable,
            local_visual_change=change,
            change_ratio=ratio,
            reason=reason,
            evidence_ref="pending-action-effect-evidence",
        )
        evidence_id = _evidence_id(request, pending.payload_hash)
        summary = replace(pending, evidence_ref=evidence_id)
        evidence = EvidenceRef(
            id=evidence_id,
            mission_id=request.mission_id,
            run_id=request.run_id,
            obligation_id=request.obligation_id,
            kind=ACTION_EFFECT_EVIDENCE_KIND,
            trust="deterministic",
            payload_hash=summary.payload_hash,
            frame_id=summary.after_frame_id,
            action_id=summary.action_id,
        )
        return ActionEffectVerificationResult(summary=summary, evidence=evidence)


__all__ = [
    "PIXEL_CHANNEL_DELTA_THRESHOLD",
    "ActionEffectVerificationError",
    "ActionEffectVerificationRequest",
    "ActionEffectVerificationResult",
    "DeterministicActionEffectVerifier",
]
