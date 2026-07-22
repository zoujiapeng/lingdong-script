"""P5c contracts for independently attested visual verification evidence.

The model receives an identity-free request and may return only the existing
``VerifierAssessment`` contract.  A local read-only adapter binds that response
to the exact request and authoritative frame it observed.  Only the Runtime
helper at the bottom of this module may turn the attestation into run-scoped
evidence and append it to the canonical registry.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from threading import Event
from typing import Any, Mapping, Protocol

from ..evidence import (
    CURRENT_FRAME_EVIDENCE_KIND,
    POST_ACTION_FRAME_EVIDENCE_KIND,
    EvidenceRegistry,
    EvidenceRegistryError,
    current_frame_payload_hash,
    frame_identity_payload_hash,
)
from ..mission import EvidenceRef
from ..runtime.contracts import RuntimeFrame
from .contracts import (
    InvalidVerificationSchema,
    ObligationVerificationRequest,
    VerifierAssessment,
)


VISUAL_MODEL_REQUEST_FORMAT = "visual-verifier-model-request/0"
VISUAL_VERIFIER_ATTESTATION_FORMAT = "visual-verifier-attestation/0"
VISUAL_VERIFIER_EVIDENCE_FORMAT = "visual-verifier-evidence/0"
VISUAL_VERIFIER_EVIDENCE_KIND = "visual_verifier_assessment"
VISUAL_VERIFIER_TRUST = "independent_visual_model"


class VisualVerifierContractError(ValueError):
    """A visual-verifier value cannot be trusted at the Runtime boundary."""


class VisualVerifierCanceled(VisualVerifierContractError):
    """The read-only verification request was canceled before evidence commit."""


class VisualVerifierEvidenceRejected(VisualVerifierContractError):
    """Runtime could not bind an attestation to current canonical evidence."""


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256(value: Any, name: str) -> str:
    if type(value) is not str:
        raise VisualVerifierContractError(f"{name} must be a lowercase SHA-256 digest")
    result = value.strip()
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise VisualVerifierContractError(f"{name} must be a lowercase SHA-256 digest")
    return result


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise VisualVerifierContractError(f"{name} must be a non-empty string")
    return value.strip()


def _strings(value: Any, name: str, *, required: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise VisualVerifierContractError(f"{name} must be an array of strings")
    result = tuple(_text(item, f"{name}[]") for item in value)
    if required and not result:
        raise VisualVerifierContractError(f"{name} cannot be empty")
    if len(result) != len(set(result)):
        raise VisualVerifierContractError(f"{name} must not contain duplicates")
    return result


def _object(raw: Any, fields: tuple[str, ...], name: str) -> Mapping[str, Any]:
    if type(raw) is not dict:
        raise VisualVerifierContractError(f"{name} must be a JSON object")
    expected = set(fields)
    if set(raw) != expected:
        raise VisualVerifierContractError(
            f"{name} must contain exactly {sorted(expected)!r}"
        )
    return raw


def _canceled(stop_event: Event | None) -> bool:
    if stop_event is not None and not isinstance(stop_event, Event):
        raise VisualVerifierContractError("stop_event must be threading.Event or null")
    return bool(stop_event is not None and stop_event.is_set())


def _verification_request_fingerprint(request: ObligationVerificationRequest) -> str:
    return _digest({
        "format": "visual-verifier-runtime-request/0",
        "request": request.to_dict(),
    })


def _frame_fingerprint(frame: RuntimeFrame) -> str:
    return _digest({
        "format": "visual-verifier-runtime-frame/0",
        "frame": frame.commit_identity.to_dict(),
        "evidence_refs": list(frame.evidence_refs),
    })


@dataclass(frozen=True, slots=True)
class VisualVerifierModelRequest:
    """Identity-free input sent to the independent visual model role."""

    desired_state: str
    acceptance_criteria: tuple[str, ...]
    forbidden_effects: tuple[str, ...]
    image_b64: str
    image_sha256: str
    submitted_size: tuple[int, int]
    format: str = VISUAL_MODEL_REQUEST_FORMAT

    _FIELDS = (
        "format", "desired_state", "acceptance_criteria", "forbidden_effects",
        "image_b64", "image_sha256", "submitted_size",
    )

    def __post_init__(self) -> None:
        if self.format != VISUAL_MODEL_REQUEST_FORMAT:
            raise VisualVerifierContractError("unsupported visual model request format")
        object.__setattr__(self, "desired_state", _text(self.desired_state, "desired_state"))
        object.__setattr__(
            self,
            "acceptance_criteria",
            _strings(self.acceptance_criteria, "acceptance_criteria", required=True),
        )
        object.__setattr__(
            self, "forbidden_effects", _strings(self.forbidden_effects, "forbidden_effects")
        )
        if type(self.image_b64) is not str or not self.image_b64:
            raise VisualVerifierContractError("image_b64 cannot be empty")
        try:
            actual = hashlib.sha256(self.image_b64.encode("ascii")).hexdigest()
        except UnicodeEncodeError as exc:
            raise VisualVerifierContractError("image_b64 must be ASCII") from exc
        claimed = _sha256(self.image_sha256, "image_sha256")
        if actual != claimed:
            raise VisualVerifierContractError("image_sha256 does not identify image_b64")
        size = tuple(self.submitted_size)
        if len(size) != 2 or any(type(item) is not int or item <= 0 for item in size):
            raise VisualVerifierContractError("submitted_size requires two positive integers")
        object.__setattr__(self, "image_sha256", claimed)
        object.__setattr__(self, "submitted_size", size)

    @classmethod
    def from_runtime(
        cls,
        request: ObligationVerificationRequest,
        frame: RuntimeFrame,
    ) -> "VisualVerifierModelRequest":
        if not isinstance(request, ObligationVerificationRequest):
            raise VisualVerifierContractError(
                "request must be ObligationVerificationRequest"
            )
        if not isinstance(frame, RuntimeFrame):
            raise VisualVerifierContractError("frame must be RuntimeFrame")
        return cls(
            desired_state=request.desired_state,
            acceptance_criteria=request.acceptance_criteria,
            forbidden_effects=request.forbidden_effects,
            image_b64=frame.image_b64,
            image_sha256=frame.image_sha256,
            submitted_size=frame.submitted_size,
        )

    @property
    def fingerprint(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "desired_state": self.desired_state,
            "acceptance_criteria": list(self.acceptance_criteria),
            "forbidden_effects": list(self.forbidden_effects),
            "image_b64": self.image_b64,
            "image_sha256": self.image_sha256,
            "submitted_size": list(self.submitted_size),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "VisualVerifierModelRequest":
        value = _object(raw, cls._FIELDS, "VisualVerifierModelRequest")
        return cls(
            format=value["format"],
            desired_state=value["desired_state"],
            acceptance_criteria=value["acceptance_criteria"],
            forbidden_effects=value["forbidden_effects"],
            image_b64=value["image_b64"],
            image_sha256=value["image_sha256"],
            submitted_size=value["submitted_size"],
        )


@dataclass(frozen=True, slots=True)
class VisualVerifierAttestation:
    """Local binding around a model-owned, identity-free assessment."""

    input_frame_sha256: str
    input_frame_fingerprint: str
    verification_request_fingerprint: str
    model_request_fingerprint: str
    assessment_sha256: str
    assessment: VerifierAssessment
    format: str = VISUAL_VERIFIER_ATTESTATION_FORMAT

    _FIELDS = (
        "format", "input_frame_sha256", "input_frame_fingerprint",
        "verification_request_fingerprint", "model_request_fingerprint",
        "assessment_sha256", "assessment",
    )

    def __post_init__(self) -> None:
        if self.format != VISUAL_VERIFIER_ATTESTATION_FORMAT:
            raise VisualVerifierContractError("unsupported visual verifier attestation format")
        for name in (
            "input_frame_sha256", "input_frame_fingerprint",
            "verification_request_fingerprint", "model_request_fingerprint",
            "assessment_sha256",
        ):
            object.__setattr__(self, name, _sha256(getattr(self, name), name))
        if not isinstance(self.assessment, VerifierAssessment):
            raise VisualVerifierContractError("assessment must be VerifierAssessment")
        actual = hashlib.sha256(self.assessment.to_json().encode("utf-8")).hexdigest()
        if actual != self.assessment_sha256:
            raise VisualVerifierContractError("assessment_sha256 does not identify assessment")

    @classmethod
    def bind(
        cls,
        request: ObligationVerificationRequest,
        frame: RuntimeFrame,
        model_request: VisualVerifierModelRequest,
        assessment: VerifierAssessment,
    ) -> "VisualVerifierAttestation":
        expected = VisualVerifierModelRequest.from_runtime(request, frame)
        if model_request != expected:
            raise VisualVerifierContractError("model request is not the current frozen input")
        return cls(
            input_frame_sha256=frame.image_sha256,
            input_frame_fingerprint=_frame_fingerprint(frame),
            verification_request_fingerprint=_verification_request_fingerprint(request),
            model_request_fingerprint=model_request.fingerprint,
            assessment_sha256=hashlib.sha256(
                assessment.to_json().encode("utf-8")
            ).hexdigest(),
            assessment=assessment,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "input_frame_sha256": self.input_frame_sha256,
            "input_frame_fingerprint": self.input_frame_fingerprint,
            "verification_request_fingerprint": self.verification_request_fingerprint,
            "model_request_fingerprint": self.model_request_fingerprint,
            "assessment_sha256": self.assessment_sha256,
            "assessment": self.assessment.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "VisualVerifierAttestation":
        value = _object(raw, cls._FIELDS, "VisualVerifierAttestation")
        assessment = value["assessment"]
        if not isinstance(assessment, VerifierAssessment):
            try:
                assessment = VerifierAssessment.from_dict(assessment)
            except (InvalidVerificationSchema, TypeError) as exc:
                raise VisualVerifierContractError("assessment is malformed") from exc
        return cls(
            format=value["format"],
            input_frame_sha256=value["input_frame_sha256"],
            input_frame_fingerprint=value["input_frame_fingerprint"],
            verification_request_fingerprint=value["verification_request_fingerprint"],
            model_request_fingerprint=value["model_request_fingerprint"],
            assessment_sha256=value["assessment_sha256"],
            assessment=assessment,
        )


class VisualVerifierModelPort(Protocol):
    """Read-only model boundary; no registry or Mission mutation is exposed."""

    def assess(
        self,
        request: VisualVerifierModelRequest,
        *,
        stop_event: Event | None = None,
    ) -> VerifierAssessment | Mapping[str, Any]: ...


class ReadOnlyVisualVerifierAdapter:
    """Project Runtime input, call one model port, then attest locally."""

    verifier_kind = "independent_visual_model"

    def __init__(self, model: VisualVerifierModelPort) -> None:
        if not callable(getattr(model, "assess", None)):
            raise VisualVerifierContractError("model must provide assess()")
        self._model = model

    def attest(
        self,
        request: ObligationVerificationRequest,
        frame: RuntimeFrame,
        *,
        stop_event: Event | None = None,
    ) -> VisualVerifierAttestation:
        if _canceled(stop_event):
            raise VisualVerifierCanceled("visual verification was canceled")
        model_request = VisualVerifierModelRequest.from_runtime(request, frame)
        raw = self._model.assess(model_request, stop_event=stop_event)
        if _canceled(stop_event):
            raise VisualVerifierCanceled("visual verification was canceled")
        try:
            assessment = (
                raw if isinstance(raw, VerifierAssessment)
                else VerifierAssessment.from_dict(raw)
            )
        except (InvalidVerificationSchema, TypeError) as exc:
            raise VisualVerifierContractError(
                "visual model returned a malformed VerifierAssessment"
            ) from exc
        return VisualVerifierAttestation.bind(
            request, frame, model_request, assessment
        )


def _validate_current_frame_evidence(
    request: ObligationVerificationRequest,
    frame: RuntimeFrame,
    registry: EvidenceRegistry,
) -> tuple[str, ...]:
    snapshot = registry.snapshot()
    registered = {ref.id: ref for ref in snapshot}
    requested = {ref.id: ref for ref in request.evidence_refs}
    if any(registered.get(ref.id) != ref for ref in request.evidence_refs):
        raise VisualVerifierEvidenceRejected(
            "verification request is not backed by the canonical evidence registry"
        )
    if not frame.evidence_refs:
        raise VisualVerifierEvidenceRejected("current frame has no deterministic evidence")
    current_hash = current_frame_payload_hash(frame.commit_identity)
    post_action_hash = frame_identity_payload_hash(frame.commit_identity)
    exact = tuple(sorted(
        ref.id
        for ref in snapshot
        if ref.trust == "deterministic"
        and ref.mission_id == request.mission_id
        and ref.run_id == request.run_id
        and ref.obligation_id == request.obligation_id
        and ref.frame_id == frame.frame_id
        and (
            (
                ref.kind == CURRENT_FRAME_EVIDENCE_KIND
                and ref.payload_hash == current_hash
                and not ref.action_id
            )
            or (
                ref.kind == POST_ACTION_FRAME_EVIDENCE_KIND
                and ref.payload_hash == post_action_hash
                and bool(ref.action_id)
            )
        )
    ))
    if exact != frame.evidence_refs:
        raise VisualVerifierEvidenceRejected(
            "RuntimeFrame evidence does not identify the current authoritative frame"
        )
    if any(requested.get(evidence_id) != registered.get(evidence_id) for evidence_id in exact):
        raise VisualVerifierEvidenceRejected(
            "current frame evidence was not frozen into the verification request"
        )
    return exact


def append_visual_verifier_evidence(
    request: ObligationVerificationRequest,
    current_frame: RuntimeFrame,
    attestation: VisualVerifierAttestation,
    registry: EvidenceRegistry,
    *,
    stop_event: Event | None = None,
) -> EvidenceRef:
    """Validate current Runtime truth and idempotently append visual evidence."""

    if _canceled(stop_event):
        raise VisualVerifierCanceled("visual evidence commit was canceled")
    if not isinstance(request, ObligationVerificationRequest):
        raise VisualVerifierEvidenceRejected(
            "request must be ObligationVerificationRequest"
        )
    if not isinstance(current_frame, RuntimeFrame):
        raise VisualVerifierEvidenceRejected("current_frame must be RuntimeFrame")
    if not isinstance(attestation, VisualVerifierAttestation):
        raise VisualVerifierEvidenceRejected(
            "attestation must be VisualVerifierAttestation"
        )
    if not isinstance(registry, EvidenceRegistry):
        raise VisualVerifierEvidenceRejected("registry must be canonical EvidenceRegistry")
    if not request.evidence_policy.allow_independent_visual_verifier:
        raise VisualVerifierEvidenceRejected(
            "independent visual verifier is disabled by EvidencePolicy"
        )
    if (
        registry.mission_id != request.mission_id
        or registry.run_id != request.run_id
    ):
        raise VisualVerifierEvidenceRejected(
            "evidence registry is outside the verification mission/run"
        )

    expected_model_request = VisualVerifierModelRequest.from_runtime(
        request, current_frame
    )
    expected = {
        "input_frame_sha256": current_frame.image_sha256,
        "input_frame_fingerprint": _frame_fingerprint(current_frame),
        "verification_request_fingerprint": _verification_request_fingerprint(request),
        "model_request_fingerprint": expected_model_request.fingerprint,
    }
    for name, value in expected.items():
        if getattr(attestation, name) != value:
            raise VisualVerifierEvidenceRejected(
                f"visual verifier attestation has stale or mismatched {name}"
            )
    frame_refs = _validate_current_frame_evidence(
        request, current_frame, registry
    )
    payload = {
        "format": VISUAL_VERIFIER_EVIDENCE_FORMAT,
        "mission_id": request.mission_id,
        "run_id": request.run_id,
        "mission_revision": request.mission_revision,
        "obligation_id": request.obligation_id,
        "claim_fingerprint": request.claim_fingerprint,
        "frame_fingerprint": expected["input_frame_fingerprint"],
        "frame_evidence_refs": list(frame_refs),
        "attestation": attestation.to_dict(),
    }
    payload_hash = _digest(payload)
    evidence = EvidenceRef(
        id=f"visual-evidence-{payload_hash[:32]}",
        mission_id=request.mission_id,
        run_id=request.run_id,
        obligation_id=request.obligation_id,
        kind=VISUAL_VERIFIER_EVIDENCE_KIND,
        trust=VISUAL_VERIFIER_TRUST,
        payload_hash=payload_hash,
        frame_id=current_frame.frame_id,
    )
    if _canceled(stop_event):
        raise VisualVerifierCanceled("visual evidence commit was canceled")
    try:
        return registry.append(evidence)
    except EvidenceRegistryError as exc:
        raise VisualVerifierEvidenceRejected(
            "canonical evidence registry rejected visual evidence"
        ) from exc


__all__ = [
    "VISUAL_MODEL_REQUEST_FORMAT",
    "VISUAL_VERIFIER_ATTESTATION_FORMAT",
    "VISUAL_VERIFIER_EVIDENCE_FORMAT",
    "VISUAL_VERIFIER_EVIDENCE_KIND",
    "VISUAL_VERIFIER_TRUST",
    "VisualVerifierContractError",
    "VisualVerifierCanceled",
    "VisualVerifierEvidenceRejected",
    "VisualVerifierModelRequest",
    "VisualVerifierAttestation",
    "VisualVerifierModelPort",
    "ReadOnlyVisualVerifierAdapter",
    "append_visual_verifier_evidence",
]
