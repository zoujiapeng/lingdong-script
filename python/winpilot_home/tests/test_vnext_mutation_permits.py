from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from winpilot.vnext.actor import ActorDecisionEnvelope, parse_actor_proposal
from winpilot.vnext.capabilities import PointerDisposition, PointerLeaseState
from winpilot.vnext.runtime import (
    MutationPermitAuthority,
    MutationPermitError,
    MutationPermitReason,
)
from winpilot.visual_runtime.commit_admission import (
    FrameCommitIdentity,
    TargetCommitIdentity,
)


def _envelope(**updates) -> ActorDecisionEnvelope:
    values = {
        "decision_id": "decision-1",
        "mission_id": "mission-1",
        "run_id": "run-1",
        "mission_revision": 4,
        "obligation_id": "obligation-1",
        "frame": FrameCommitIdentity(
            9, 3, 101, (0, 0, 1280, 720), "target_window",
            (1120, 630), "a" * 64,
        ),
        "target": TargetCommitIdentity(
            "target-1", 101, 101, 7, "process-" + "1" * 32, 3,
            (0, 0, 1280, 720),
        ),
        "pointer": PointerLeaseState(PointerDisposition.RELEASED),
        "broker_projection_id": "projection-1",
        "capability_grant_ids": (),
        "allowed_evidence_scope": ("frame-proof-9",),
        "proposal": parse_actor_proposal({
            "format": "actor-proposal/1",
            "kind": "desktop_action",
            "action": "click",
            "args": {"image_x": 100, "image_y": 200},
            "local_intent": "activate the visible target",
        }),
    }
    values.update(updates)
    return ActorDecisionEnvelope(**values)


def test_permit_is_consumed_once_and_replay_is_rejected() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    checks = 0

    def current() -> bool:
        nonlocal checks
        checks += 1
        return True

    envelope = _envelope()
    permit_id = authority.issue(envelope, turn_index=5, still_current=current)

    first = authority.consume(envelope)
    second = authority.consume(envelope)

    assert first.allowed is True
    assert first.reason is MutationPermitReason.ALLOWED
    assert first.permit_id == permit_id
    assert first.turn_index == 5
    assert second.allowed is False
    assert second.reason is MutationPermitReason.ALREADY_CONSUMED
    assert checks == 1


def test_mismatched_envelope_consumes_without_calling_runtime_check() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    calls = 0

    def current() -> bool:
        nonlocal calls
        calls += 1
        return True

    authority.issue(_envelope(), turn_index=0, still_current=current)
    mismatched = _envelope(mission_revision=5)

    rejected = authority.consume(mismatched)

    assert rejected.reason is MutationPermitReason.ENVELOPE_MISMATCH
    assert rejected.allowed is False
    assert calls == 0
    assert authority.consume(_envelope()).reason is MutationPermitReason.ALREADY_CONSUMED


@pytest.mark.parametrize(
    "check,reason",
    (
        (lambda: False, MutationPermitReason.RUNTIME_STATE_CHANGED),
        (lambda: 1, MutationPermitReason.RUNTIME_STATE_CHANGED),
        (lambda: (_ for _ in ()).throw(RuntimeError("lost")),
         MutationPermitReason.RUNTIME_STATE_UNAVAILABLE),
    ),
)
def test_runtime_check_is_strict_and_fail_closed(check, reason) -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    envelope = _envelope()
    authority.issue(envelope, turn_index=0, still_current=check)

    result = authority.consume(envelope)

    assert result.allowed is False
    assert result.reason is reason


def test_revoke_and_close_never_restore_a_permit() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    first = _envelope()
    authority.issue(first, turn_index=0, still_current=lambda: True)
    assert authority.revoke(first.decision_id) is True
    assert authority.consume(first).reason is MutationPermitReason.ALREADY_CONSUMED

    second = _envelope(decision_id="decision-2")
    authority.issue(second, turn_index=1, still_current=lambda: True)
    authority.close()
    assert authority.consume(second).reason is MutationPermitReason.AUTHORITY_CLOSED
    with pytest.raises(MutationPermitError, match="closed"):
        authority.issue(
            _envelope(decision_id="decision-3"),
            turn_index=2,
            still_current=lambda: True,
        )


def test_concurrent_consumers_allow_exactly_one_commit_attempt() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    envelope = _envelope()
    authority.issue(envelope, turn_index=0, still_current=lambda: True)

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _index: authority.consume(envelope), range(64)))

    assert sum(result.allowed for result in results) == 1
    assert sum(result.reason is MutationPermitReason.ALLOWED for result in results) == 1
    assert all(
        result.reason in {
            MutationPermitReason.ALLOWED,
            MutationPermitReason.ALREADY_CONSUMED,
        }
        for result in results
    )


def test_active_admission_is_exact_transient_and_not_replayable() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    envelope = _envelope()
    authority.issue(envelope, turn_index=0, still_current=lambda: True)

    permit = authority.begin_admission(envelope)

    assert permit.allowed is True
    assert authority.pending_count == 0
    assert authority.active_count == 1
    assert authority.active_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        target=envelope.target,
        pointer=envelope.pointer,
    ) is True
    assert authority.active_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        target=envelope.target,
        pointer=PointerLeaseState(PointerDisposition.UNKNOWN),
    ) is False
    assert authority.begin_admission(envelope).reason is (
        MutationPermitReason.ALREADY_CONSUMED
    )

    assert authority.finish_admission(envelope.decision_id) is True
    assert authority.finish_admission(envelope.decision_id) is False
    assert authority.active_count == 0
    assert authority.active_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        target=envelope.target,
        pointer=envelope.pointer,
    ) is False
    assert authority.consume(envelope).reason is MutationPermitReason.ALREADY_CONSUMED


def test_close_revokes_an_active_admission() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    envelope = _envelope()
    authority.issue(envelope, turn_index=0, still_current=lambda: True)
    assert authority.begin_admission(envelope).allowed is True

    authority.close()

    assert authority.active_count == 0
    assert authority.active_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        target=envelope.target,
        pointer=envelope.pointer,
    ) is False


def test_consumed_history_is_bounded_and_scope_is_strict() -> None:
    authority = MutationPermitAuthority(
        "mission-1", "run-1", consumed_history_limit=2,
    )
    with pytest.raises(MutationPermitError, match="outside"):
        authority.issue(
            _envelope(mission_id="mission-other"),
            turn_index=0,
            still_current=lambda: True,
        )
    for index in range(5):
        envelope = _envelope(decision_id=f"decision-{index + 10}")
        authority.issue(envelope, turn_index=index, still_current=lambda: True)
        assert authority.consume(envelope).allowed is True
    assert authority.pending_count == 0
    assert authority.consumed_count == 2
    with pytest.raises(MutationPermitError, match="monotonically"):
        authority.issue(
            _envelope(decision_id="decision-replay"),
            turn_index=1,
            still_current=lambda: True,
        )
