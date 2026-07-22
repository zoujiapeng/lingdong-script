"""Run-scoped evidence ownership for vNext."""

from .frame_projection import (
    CURRENT_FRAME_EVIDENCE_FORMAT,
    CURRENT_FRAME_EVIDENCE_KIND,
    POST_ACTION_FRAME_EVIDENCE_FORMAT,
    POST_ACTION_FRAME_EVIDENCE_KIND,
    FrameEvidenceProjectionPort,
    RegistryFrameEvidenceProjector,
    current_frame_payload_hash,
    frame_identity_payload_hash,
)
from .registry import (
    EvidenceRegistry,
    EvidenceRegistryClosed,
    EvidenceRegistryError,
)

__all__ = [
    "CURRENT_FRAME_EVIDENCE_FORMAT",
    "CURRENT_FRAME_EVIDENCE_KIND",
    "POST_ACTION_FRAME_EVIDENCE_FORMAT",
    "POST_ACTION_FRAME_EVIDENCE_KIND",
    "FrameEvidenceProjectionPort",
    "RegistryFrameEvidenceProjector",
    "current_frame_payload_hash",
    "frame_identity_payload_hash",
    "EvidenceRegistry",
    "EvidenceRegistryClosed",
    "EvidenceRegistryError",
]
