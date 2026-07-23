from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import base64
import hashlib

import pytest

from winpilot.vnext.capabilities import BrokerRuntimeState
from winpilot.vnext.evidence import (
    POST_ACTION_FRAME_EVIDENCE_KIND,
    EvidenceRegistry,
    frame_identity_payload_hash,
)
from winpilot.vnext.mission import EvidenceRef, EvidenceScope
from winpilot.vnext.runtime.contracts import (
    ControllerOutcome,
    ControllerTransition,
    RuntimeFrame,
    RuntimeSnapshot,
)
from winpilot.vnext.runtime.post_action_reconcile import (
    PostActionReconciliationError,
    reconcile_post_action,
)


MISSION_ID = "mission-1"
RUN_ID = "run-1"
OBLIGATION_ID = "obligation-1"
ACTION_ID = "action-1"
ACTION = "click"
EVIDENCE_ID = "post-action-proof-1"


def _bare_frame(*, frame_id: int = 7) -> RuntimeFrame:
    image_b64 = base64.b64encode(
        f"post-action-frame:{frame_id}".encode()
    ).decode("ascii")
    return RuntimeFrame(
        frame_id=frame_id,
        epoch=0,
        hwnd=101,
        region=(0, 0, 640, 480),
        scope="full_desktop",
        submitted_size=(640, 480),
        image_b64=image_b64,
        image_sha256=hashlib.sha256(
            image_b64.encode("ascii")
        ).hexdigest(),
    )


def _evidence(
    frame: RuntimeFrame,
    **changes: object,
) -> EvidenceRef:
    values: dict[str, object] = {
        "id": EVIDENCE_ID,
        "mission_id": MISSION_ID,
        "run_id": RUN_ID,
        "obligation_id": OBLIGATION_ID,
        "kind": POST_ACTION_FRAME_EVIDENCE_KIND,
        "trust": "deterministic",
        "payload_hash": frame_identity_payload_hash(frame.commit_identity),
        "frame_id": frame.frame_id,
        "action_id": ACTION_ID,
    }
    values.update(changes)
    return EvidenceRef(**values)


def _facts(
    *,
    authoritative: bool = True,
    fresh: bool = True,
    evidence_changes: dict[str, object] | None = None,
) -> tuple[
    RuntimeFrame,
    RuntimeSnapshot,
    ControllerTransition,
    EvidenceRegistry,
    EvidenceRef,
]:
    frame_without_evidence = _bare_frame()
    evidence = _evidence(
        frame_without_evidence,
        **(evidence_changes or {}),
    )
    frame = replace(
        frame_without_evidence,
        evidence_refs=(evidence.id,),
    )
    snapshot = RuntimeSnapshot(
        broker_state=BrokerRuntimeState(
            turn_index=3,
            available_capabilities=("visual.interact",),
            frame=frame.broker_frame(
                authoritative=authoritative,
                fresh=fresh,
            ),
        ),
        frame=frame,
        target=None,
    )
    transition = ControllerTransition(
        outcome=ControllerOutcome.COMPLETED,
        action_id=ACTION_ID,
        action=ACTION,
        input_completed=True,
        evidence_refs=(evidence.id,),
        post_frame_id=frame.frame_id,
    )
    registry = EvidenceRegistry(MISSION_ID, RUN_ID)
    registry.append(evidence)
    return frame, snapshot, transition, registry, evidence


def _reconcile(
    transition: ControllerTransition,
    snapshot: RuntimeSnapshot,
    evidence: EvidenceRegistry,
):
    return reconcile_post_action(
        transition,
        snapshot,
        evidence,
        mission_id=MISSION_ID,
        run_id=RUN_ID,
        obligation_id=OBLIGATION_ID,
        action_id=ACTION_ID,
        action=ACTION,
    )


def test_reconcile_reads_registry_without_mutating_it() -> None:
    frame, snapshot, transition, registry, evidence = _facts()
    before = registry.snapshot()

    result = _reconcile(transition, snapshot, registry)

    assert result.action_id == ACTION_ID
    assert result.action == ACTION
    assert result.frame == frame.commit_identity
    assert result.payload_hash == frame_identity_payload_hash(
        frame.commit_identity
    )
    assert result.evidence_refs == (evidence,)
    assert registry.snapshot() == before
    with pytest.raises(FrozenInstanceError):
        result.action = "type_text"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("authoritative", "fresh"),
    ((False, True), (True, False), (False, False)),
)
def test_reconcile_requires_current_authoritative_fresh_frame(
    authoritative: bool,
    fresh: bool,
) -> None:
    _, snapshot, transition, registry, _ = _facts(
        authoritative=authoritative,
        fresh=fresh,
    )

    with pytest.raises(
        PostActionReconciliationError,
        match="current authoritative and fresh",
    ):
        _reconcile(transition, snapshot, registry)


def test_reconcile_rejects_missing_current_frame() -> None:
    _, _, transition, registry, _ = _facts()
    snapshot = RuntimeSnapshot(
        broker_state=BrokerRuntimeState(
            turn_index=3,
            available_capabilities=("visual.interact",),
        ),
        frame=None,
        target=None,
    )

    with pytest.raises(
        PostActionReconciliationError,
        match="current authoritative and fresh",
    ):
        _reconcile(transition, snapshot, registry)


def test_reconcile_requires_exact_post_frame_id() -> None:
    _, snapshot, transition, registry, _ = _facts()
    transition = replace(transition, post_frame_id=8)

    with pytest.raises(
        PostActionReconciliationError,
        match="current authoritative and fresh",
    ):
        _reconcile(transition, snapshot, registry)


def test_reconcile_requires_exact_controller_and_frame_evidence_ids() -> None:
    frame, _, transition, registry, _ = _facts()
    frame = replace(
        frame,
        evidence_refs=(EVIDENCE_ID, "unrelated-frame-proof"),
    )
    snapshot = RuntimeSnapshot(
        broker_state=BrokerRuntimeState(
            turn_index=3,
            available_capabilities=("visual.interact",),
            frame=frame.broker_frame(),
        ),
        frame=frame,
        target=None,
    )

    with pytest.raises(
        PostActionReconciliationError,
        match="evidence identities differ",
    ):
        _reconcile(transition, snapshot, registry)


@pytest.mark.parametrize(
    "changes",
    (
        {"obligation_id": "other-obligation"},
        {"action_id": "other-action"},
        {"frame_id": 8},
        {"payload_hash": "a" * 64},
        {"trust": "visual"},
        {"kind": "current_frame"},
        {
            "artifact_id": "artifact-1",
            "content_sha256": "b" * 64,
        },
    ),
)
def test_reconcile_rejects_evidence_identity_or_trust_tampering(
    changes: dict[str, object],
) -> None:
    _, snapshot, transition, registry, _ = _facts(
        evidence_changes=changes,
    )

    with pytest.raises(PostActionReconciliationError):
        _reconcile(transition, snapshot, registry)


@pytest.mark.parametrize(
    "transition_changes",
    (
        {"action_id": "other-action"},
        {"action": "type_text"},
    ),
)
def test_reconcile_requires_exact_controller_action_identity(
    transition_changes: dict[str, object],
) -> None:
    _, snapshot, transition, registry, _ = _facts()
    transition = replace(transition, **transition_changes)

    with pytest.raises(
        PostActionReconciliationError,
        match="action identity changed",
    ):
        _reconcile(transition, snapshot, registry)


def test_reconcile_rejects_unknown_mutation_outcome() -> None:
    frame, snapshot, transition, registry, _ = _facts()
    transition = ControllerTransition(
        outcome=ControllerOutcome.UNKNOWN,
        action_id=transition.action_id,
        action=transition.action,
        input_completed=None,
        evidence_refs=transition.evidence_refs,
        post_frame_id=frame.frame_id,
    )

    with pytest.raises(
        PostActionReconciliationError,
        match="unknown mutation outcome",
    ):
        _reconcile(transition, snapshot, registry)


@pytest.mark.parametrize(
    ("mission_id", "run_id"),
    (("other-mission", RUN_ID), (MISSION_ID, "other-run")),
)
def test_reconcile_rejects_evidence_outside_expected_run_scope(
    mission_id: str,
    run_id: str,
) -> None:
    _, snapshot, transition, _, evidence = _facts()
    registry = EvidenceRegistry(mission_id, run_id)
    registry.append(replace(
        evidence,
        mission_id=mission_id,
        run_id=run_id,
    ))

    with pytest.raises(
        PostActionReconciliationError,
        match="outside the expected mission/run",
    ):
        _reconcile(transition, snapshot, registry)


@pytest.mark.parametrize(
    "source",
    ({}, EvidenceScope(MISSION_ID, RUN_ID)),
)
def test_reconcile_rejects_noncanonical_evidence_source(
    source: object,
) -> None:
    _, snapshot, transition, _, _ = _facts()

    with pytest.raises(TypeError, match="canonical EvidenceRegistry"):
        _reconcile(
            transition,
            snapshot,
            source,  # type: ignore[arg-type]
        )
