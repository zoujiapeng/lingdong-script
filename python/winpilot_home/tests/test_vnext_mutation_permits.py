from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from typing import get_args

import pytest
from winpilot.process_identity import process_identity_for
from winpilot.vnext.actor import ActorDecisionEnvelope, parse_actor_proposal
from winpilot.vnext.capabilities import PointerDisposition, PointerLeaseState
from winpilot.vnext.runtime import (
    MutationPermitAuthority,
    MutationPermitError,
    MutationPermitReason,
)
from winpilot.vnext.runtime.contracts import ControllerTransition
from winpilot.vnext.runtime.mutation_permits import MutationEnvelope
from winpilot.vnext.runtime.ports import ControllerEnvelope, ControllerResult
from winpilot.vnext.runtime.target_activation import (
    TargetActivationEnvelope,
    TargetActivationTransition,
)
from winpilot.vnext.runtime.target_launch import (
    TargetLaunchEnvelope,
    TargetLaunchTransition,
)
from winpilot.vnext.targeting import (
    TargetCandidate,
    candidate_fingerprint_for,
    candidate_ref_for,
    launch_identity_for,
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


def _target_candidate(
    *,
    hwnd: int = 901,
    process_id: int = 41,
    creation_time_ns: int = 100,
) -> TargetCandidate:
    process_identity = process_identity_for(
        process_id=process_id,
        executable_path=fr"C:\Apps\host-{process_id}.exe",
        creation_time_ns=creation_time_ns,
    )
    return TargetCandidate(
        candidate_ref=candidate_ref_for(
            activation="switch_window",
            identity=f"{hwnd}|{process_id}|{process_identity}",
        ),
        activation="switch_window",
        state="visible",
        label="Visible workspace",
        provenance=("visible_windows",),
        window_title="Workspace",
        hwnd=hwnd,
        process_id=process_id,
        process_identity=process_identity,
    )


def _activation_envelope(
    *,
    candidate: TargetCandidate | None = None,
    **updates,
) -> TargetActivationEnvelope:
    selected = candidate or _target_candidate()
    values = {
        "decision_id": "decision-activation-1",
        "mission_id": "mission-1",
        "run_id": "run-1",
        "mission_revision": 4,
        "obligation_id": "obligation-1",
        "frame": FrameCommitIdentity(
            82, 0, 1, (0, 0, 1920, 1080), "full_desktop",
            (1280, 720), "b" * 64,
        ),
        "pointer": PointerLeaseState(PointerDisposition.RELEASED),
        "broker_projection_id": "projection-activation-1",
        "capability_grant_ids": (),
        "allowed_evidence_scope": ("frame-proof-82",),
        "candidate_fingerprint": candidate_fingerprint_for((selected,)),
        "proposal": parse_actor_proposal({
            "format": "actor-proposal/1",
            "kind": "desktop_action",
            "action": "activate_target",
            "args": {"candidate_ordinal": 1},
            "local_intent": "activate the selected visible target",
        }),
        "candidate": selected,
    }
    values.update(updates)
    return TargetActivationEnvelope(**values)


def _launch_candidate(
    *,
    target: str = r"C:\Apps\Workspace.exe",
) -> TargetCandidate:
    launch_identity = launch_identity_for(kind="exe", target=target)
    return TargetCandidate(
        candidate_ref=candidate_ref_for(
            activation="open_app",
            identity=launch_identity,
        ),
        activation="open_app",
        state="installed",
        label="Installed workspace",
        provenance=("installed_catalog",),
        launch_kind="exe",
        launch_target=target,
    )


def _launch_envelope(
    *,
    candidate: TargetCandidate | None = None,
    **updates,
) -> TargetLaunchEnvelope:
    selected = candidate or _launch_candidate()
    values = {
        "decision_id": "decision-launch-1",
        "mission_id": "mission-1",
        "run_id": "run-1",
        "mission_revision": 4,
        "obligation_id": "obligation-1",
        "frame": FrameCommitIdentity(
            92, 0, 1, (0, 0, 1920, 1080), "full_desktop",
            (1280, 720), "c" * 64,
        ),
        "pointer": PointerLeaseState(PointerDisposition.RELEASED),
        "broker_projection_id": "projection-launch-1",
        "capability_grant_ids": (),
        "allowed_evidence_scope": ("frame-proof-92",),
        "candidate_fingerprint": candidate_fingerprint_for((selected,)),
        "proposal": parse_actor_proposal({
            "format": "actor-proposal/1",
            "kind": "desktop_action",
            "action": "activate_target",
            "args": {"candidate_ordinal": 1},
            "local_intent": "activate the selected installed target",
        }),
        "candidate": selected,
    }
    values.update(updates)
    return TargetLaunchEnvelope(**values)


def test_controller_and_permit_unions_cover_only_current_mutation_contracts() -> None:
    assert set(get_args(MutationEnvelope)) == {
        ActorDecisionEnvelope,
        TargetActivationEnvelope,
        TargetLaunchEnvelope,
    }
    assert set(get_args(ControllerEnvelope)) == {
        ActorDecisionEnvelope,
        TargetActivationEnvelope,
        TargetLaunchEnvelope,
    }
    assert set(get_args(ControllerResult)) == {
        ControllerTransition,
        TargetActivationTransition,
        TargetLaunchTransition,
    }


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


def test_activation_permit_is_consumed_once_and_replay_is_rejected() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    checks = 0

    def current() -> bool:
        nonlocal checks
        checks += 1
        return True

    envelope = _activation_envelope()
    permit_id = authority.issue(envelope, turn_index=5, still_current=current)

    first = authority.consume(envelope)
    second = authority.consume(envelope)

    assert first.allowed is True
    assert first.reason is MutationPermitReason.ALLOWED
    assert first.permit_id == permit_id
    assert first.envelope_sha256 == hashlib.sha256(
        envelope.to_json().encode("utf-8")
    ).hexdigest()
    assert first.turn_index == 5
    assert second.allowed is False
    assert second.reason is MutationPermitReason.ALREADY_CONSUMED
    assert checks == 1


def test_launch_permit_is_consumed_once_and_replay_is_rejected() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    checks = 0

    def current() -> bool:
        nonlocal checks
        checks += 1
        return True

    envelope = _launch_envelope()
    permit_id = authority.issue(envelope, turn_index=5, still_current=current)

    first = authority.consume(envelope)
    second = authority.consume(envelope)

    assert first.allowed is True
    assert first.reason is MutationPermitReason.ALLOWED
    assert first.permit_id == permit_id
    assert first.envelope_sha256 == hashlib.sha256(
        envelope.to_json().encode("utf-8")
    ).hexdigest()
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


def test_private_activation_candidate_mismatch_consumes_without_runtime_check() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    calls = 0

    def current() -> bool:
        nonlocal calls
        calls += 1
        return True

    original = _activation_envelope()
    replacement = _target_candidate(creation_time_ns=101)
    mismatched = _activation_envelope(
        candidate=replacement,
        candidate_fingerprint=candidate_fingerprint_for((replacement,)),
    )
    authority.issue(original, turn_index=0, still_current=current)

    rejected = authority.consume(mismatched)

    assert rejected.reason is MutationPermitReason.ENVELOPE_MISMATCH
    assert rejected.allowed is False
    assert rejected.envelope_sha256 == hashlib.sha256(
        mismatched.to_json().encode("utf-8")
    ).hexdigest()
    assert rejected.envelope_sha256 != hashlib.sha256(
        original.to_json().encode("utf-8")
    ).hexdigest()
    assert calls == 0
    assert authority.consume(original).reason is (
        MutationPermitReason.ALREADY_CONSUMED
    )


def test_private_launch_candidate_mismatch_consumes_without_runtime_check() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    calls = 0

    def current() -> bool:
        nonlocal calls
        calls += 1
        return True

    original = _launch_envelope()
    replacement = _launch_candidate(target=r"C:\Apps\Workspace-2.exe")
    mismatched = _launch_envelope(
        candidate=replacement,
        candidate_fingerprint=candidate_fingerprint_for((replacement,)),
    )
    authority.issue(original, turn_index=0, still_current=current)

    rejected = authority.consume(mismatched)

    assert rejected.reason is MutationPermitReason.ENVELOPE_MISMATCH
    assert rejected.allowed is False
    assert rejected.envelope_sha256 == hashlib.sha256(
        mismatched.to_json().encode("utf-8")
    ).hexdigest()
    assert rejected.envelope_sha256 != hashlib.sha256(
        original.to_json().encode("utf-8")
    ).hexdigest()
    assert calls == 0
    assert authority.consume(original).reason is (
        MutationPermitReason.ALREADY_CONSUMED
    )


@pytest.mark.parametrize(
    "issued_kind,submitted_kind",
    (
        ("actor", "activation"),
        ("actor", "launch"),
        ("activation", "actor"),
        ("activation", "launch"),
        ("launch", "actor"),
        ("launch", "activation"),
    ),
)
def test_mutation_envelope_kinds_cannot_cross_consume(
    issued_kind: str,
    submitted_kind: str,
) -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    decision_id = "decision-cross-kind"
    envelopes = {
        "actor": _envelope(decision_id=decision_id),
        "activation": _activation_envelope(decision_id=decision_id),
        "launch": _launch_envelope(decision_id=decision_id),
    }
    issued = envelopes[issued_kind]
    submitted = envelopes[submitted_kind]
    checks = 0

    def current() -> bool:
        nonlocal checks
        checks += 1
        return True

    authority.issue(issued, turn_index=0, still_current=current)

    assert authority.consume(submitted).reason is (
        MutationPermitReason.ENVELOPE_MISMATCH
    )
    assert checks == 0
    assert authority.consume(issued).reason is (
        MutationPermitReason.ALREADY_CONSUMED
    )


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


def test_concurrent_activation_consumers_allow_exactly_one_commit_attempt() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    envelope = _activation_envelope()
    authority.issue(envelope, turn_index=0, still_current=lambda: True)

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(
            lambda _index: authority.consume(envelope), range(64),
        ))

    assert sum(result.allowed for result in results) == 1
    assert sum(
        result.reason is MutationPermitReason.ALLOWED for result in results
    ) == 1
    assert all(
        result.reason in {
            MutationPermitReason.ALLOWED,
            MutationPermitReason.ALREADY_CONSUMED,
        }
        for result in results
    )


def test_concurrent_launch_consumers_allow_exactly_one_commit_attempt() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    envelope = _launch_envelope()
    authority.issue(envelope, turn_index=0, still_current=lambda: True)

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(
            lambda _index: authority.consume(envelope), range(64),
        ))

    assert sum(result.allowed for result in results) == 1
    assert sum(
        result.reason is MutationPermitReason.ALLOWED for result in results
    ) == 1
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


def test_active_activation_admission_requires_exact_private_authority() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    envelope = _activation_envelope()
    authority.issue(envelope, turn_index=0, still_current=lambda: True)

    permit = authority.begin_admission(envelope)

    assert permit.allowed is True
    assert authority.active_count == 1
    assert authority.active_activation_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        pointer=envelope.pointer,
        candidate_fingerprint=envelope.candidate_fingerprint,
        candidate=envelope.candidate,
    ) is True
    assert authority.active_launch_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        pointer=envelope.pointer,
        candidate_fingerprint=envelope.candidate_fingerprint,
        candidate=envelope.candidate,
    ) is False
    assert authority.active_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        target=None,
        pointer=envelope.pointer,
    ) is False

    replacement = _target_candidate(creation_time_ns=101)
    assert authority.active_activation_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        pointer=envelope.pointer,
        candidate_fingerprint=envelope.candidate_fingerprint,
        candidate=replacement,
    ) is False
    assert authority.active_activation_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        pointer=envelope.pointer,
        candidate_fingerprint=candidate_fingerprint_for((replacement,)),
        candidate=envelope.candidate,
    ) is False
    assert authority.active_activation_admission_matches(
        envelope.decision_id,
        frame=FrameCommitIdentity(
            83, 0, 1, (0, 0, 1920, 1080), "full_desktop",
            (1280, 720), "c" * 64,
        ),
        pointer=envelope.pointer,
        candidate_fingerprint=envelope.candidate_fingerprint,
        candidate=envelope.candidate,
    ) is False
    assert authority.active_activation_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        pointer=PointerLeaseState(PointerDisposition.UNKNOWN),
        candidate_fingerprint=envelope.candidate_fingerprint,
        candidate=envelope.candidate,
    ) is False

    assert authority.finish_admission(envelope.decision_id) is True
    assert authority.active_activation_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        pointer=envelope.pointer,
        candidate_fingerprint=envelope.candidate_fingerprint,
        candidate=envelope.candidate,
    ) is False
    assert authority.consume(envelope).reason is (
        MutationPermitReason.ALREADY_CONSUMED
    )


def test_active_launch_admission_requires_exact_private_authority() -> None:
    authority = MutationPermitAuthority("mission-1", "run-1")
    envelope = _launch_envelope()
    authority.issue(envelope, turn_index=0, still_current=lambda: True)

    permit = authority.begin_admission(envelope)

    assert permit.allowed is True
    assert authority.active_count == 1
    assert authority.active_launch_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        pointer=envelope.pointer,
        candidate_fingerprint=envelope.candidate_fingerprint,
        candidate=envelope.candidate,
    ) is True
    assert authority.active_activation_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        pointer=envelope.pointer,
        candidate_fingerprint=envelope.candidate_fingerprint,
        candidate=envelope.candidate,
    ) is False

    replacement = _launch_candidate(target=r"C:\Apps\Workspace-2.exe")
    assert authority.active_launch_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        pointer=envelope.pointer,
        candidate_fingerprint=envelope.candidate_fingerprint,
        candidate=replacement,
    ) is False
    assert authority.active_launch_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        pointer=envelope.pointer,
        candidate_fingerprint=candidate_fingerprint_for((replacement,)),
        candidate=envelope.candidate,
    ) is False
    assert authority.active_launch_admission_matches(
        envelope.decision_id,
        frame=FrameCommitIdentity(
            93, 0, 1, (0, 0, 1920, 1080), "full_desktop",
            (1280, 720), "d" * 64,
        ),
        pointer=envelope.pointer,
        candidate_fingerprint=envelope.candidate_fingerprint,
        candidate=envelope.candidate,
    ) is False
    assert authority.active_launch_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        pointer=PointerLeaseState(PointerDisposition.UNKNOWN),
        candidate_fingerprint=envelope.candidate_fingerprint,
        candidate=envelope.candidate,
    ) is False

    assert authority.finish_admission(envelope.decision_id) is True
    assert authority.active_launch_admission_matches(
        envelope.decision_id,
        frame=envelope.frame,
        pointer=envelope.pointer,
        candidate_fingerprint=envelope.candidate_fingerprint,
        candidate=envelope.candidate,
    ) is False
    assert authority.consume(envelope).reason is (
        MutationPermitReason.ALREADY_CONSUMED
    )


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
