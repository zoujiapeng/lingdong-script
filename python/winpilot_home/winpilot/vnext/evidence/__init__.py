"""Run-scoped evidence ownership for vNext."""

from .frame_projection import (
    POST_ACTION_FRAME_EVIDENCE_FORMAT,
    POST_ACTION_FRAME_EVIDENCE_KIND,
    FrameEvidenceProjectionPort,
    RegistryFrameEvidenceProjector,
    frame_identity_payload_hash,
)
from .registry import (
    EvidenceRegistry,
    EvidenceRegistryClosed,
    EvidenceRegistryError,
)

__all__ = [
    "POST_ACTION_FRAME_EVIDENCE_FORMAT",
    "POST_ACTION_FRAME_EVIDENCE_KIND",
    "FrameEvidenceProjectionPort",
    "RegistryFrameEvidenceProjector",
    "frame_identity_payload_hash",
    "EvidenceRegistry",
    "EvidenceRegistryClosed",
    "EvidenceRegistryError",
]
