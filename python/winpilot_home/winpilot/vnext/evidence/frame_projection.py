"""Read-only projection of exact post-action frame evidence.

The projector never captures pixels and never copies registry state.  It reads
the run's canonical :class:`EvidenceRegistry` on every call and returns only
evidence whose payload hash identifies the complete supplied frame identity.
"""
from __future__ import annotations

import hashlib
import json
from typing import Protocol, runtime_checkable

from ...visual_runtime.commit_admission import FrameCommitIdentity
from .registry import EvidenceRegistry


POST_ACTION_FRAME_EVIDENCE_FORMAT = "post-action-frame-evidence/0"
POST_ACTION_FRAME_EVIDENCE_KIND = "post_action_frame"
CURRENT_FRAME_EVIDENCE_FORMAT = "current-frame-evidence/0"
CURRENT_FRAME_EVIDENCE_KIND = "current_frame"


def frame_identity_payload_hash(frame: FrameCommitIdentity) -> str:
    """Hash the complete canonical frame identity, including pixel digest."""

    if not isinstance(frame, FrameCommitIdentity):
        raise TypeError("frame must be FrameCommitIdentity")
    payload = {
        "format": POST_ACTION_FRAME_EVIDENCE_FORMAT,
        "frame": frame.to_dict(),
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def current_frame_payload_hash(frame: FrameCommitIdentity) -> str:
    """Hash the exact pixels/geometry of a non-mutation observation frame."""

    if not isinstance(frame, FrameCommitIdentity):
        raise TypeError("frame must be FrameCommitIdentity")
    payload = {
        "format": CURRENT_FRAME_EVIDENCE_FORMAT,
        "frame": frame.to_dict(),
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


@runtime_checkable
class FrameEvidenceProjectionPort(Protocol):
    """Read evidence identities for one exact authoritative frame."""

    def evidence_refs_for(
        self,
        frame: FrameCommitIdentity,
    ) -> tuple[str, ...]: ...


class RegistryFrameEvidenceProjector:
    """Project matching references directly from one canonical registry."""

    def __init__(self, registry: EvidenceRegistry) -> None:
        if not isinstance(registry, EvidenceRegistry):
            raise TypeError("registry must be EvidenceRegistry")
        self._registry = registry

    def evidence_refs_for(
        self,
        frame: FrameCommitIdentity,
    ) -> tuple[str, ...]:
        payload_hash = frame_identity_payload_hash(frame)
        return tuple(
            ref.id
            for ref in self._registry.snapshot()
            if ref.kind == POST_ACTION_FRAME_EVIDENCE_KIND
            and ref.trust == "deterministic"
            and ref.frame_id == frame.frame_id
            and ref.payload_hash == payload_hash
            and bool(ref.obligation_id)
            and bool(ref.action_id)
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
]
