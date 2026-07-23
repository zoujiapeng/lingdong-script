from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from winpilot.vnext.targeting import (
    TargetCandidate,
    TargetDiscoverySnapshot,
    TargetSelectionLease,
    TargetSelectionLeaseError,
    TargetSelectionScope,
    candidate_ref_for,
    launch_identity_for,
)
from winpilot.visual_runtime.commit_admission import FrameCommitIdentity


def _candidate(index: int) -> TargetCandidate:
    target = fr"C:\Apps\Candidate-{index}.exe"
    identity = launch_identity_for(kind="exe", target=target)
    return TargetCandidate(
        candidate_ref=candidate_ref_for(activation="open_app", identity=identity),
        activation="open_app",
        state="installed",
        label=f"Candidate {index}",
        provenance=("installed_catalog",),
        launch_kind="exe",
        launch_target=target,
    )


def _frame() -> FrameCommitIdentity:
    return FrameCommitIdentity(
        7, 0, 1, (0, 0, 1920, 1080), "full_desktop",
        (1920, 1080), "a" * 64,
    )


def _snapshot(*candidates: TargetCandidate) -> TargetDiscoverySnapshot:
    return TargetDiscoverySnapshot(
        query="Candidate",
        candidates=candidates or (_candidate(1), _candidate(2)),
        captured_at=1,
    )


def _scope(snapshot: TargetDiscoverySnapshot | None = None) -> TargetSelectionScope:
    snapshot = snapshot or _snapshot()
    return TargetSelectionScope(
        mission_id="mission-1",
        run_id="run-1",
        mission_revision=3,
        obligation_id="obligation-1",
        turn_index=4,
        frame=_frame(),
        broker_projection_id="projection-1",
        candidate_fingerprint=snapshot.candidate_fingerprint,
    )


def _lease(*candidates: TargetCandidate) -> TargetSelectionLease:
    snapshot = _snapshot(*candidates)
    return TargetSelectionLease(
        scope=_scope(snapshot),
        discovery=snapshot,
    )


def test_lease_resolves_original_frozen_ordinal_exactly_once() -> None:
    first, second = _candidate(1), _candidate(2)
    snapshot = _snapshot(first, second)
    lease = TargetSelectionLease(scope=_scope(snapshot), discovery=snapshot)

    assert lease.consume(scope=_scope(snapshot), candidate_ordinal=2) is second
    with pytest.raises(TargetSelectionLeaseError, match="exhausted"):
        lease.consume(scope=_scope(snapshot), candidate_ordinal=1)


@pytest.mark.parametrize("ordinal", (True, 1.0, "1", 0, -1, 3))
def test_invalid_ordinal_is_fail_closed_and_consumes_lease(ordinal) -> None:
    lease = _lease()
    with pytest.raises(TargetSelectionLeaseError, match="outside"):
        lease.consume(scope=_scope(), candidate_ordinal=ordinal)
    assert lease.consumed is True
    with pytest.raises(TargetSelectionLeaseError, match="exhausted"):
        lease.consume(scope=_scope(), candidate_ordinal=1)


@pytest.mark.parametrize(
    "changed",
    (
        {"mission_id": "mission-2"},
        {"run_id": "run-2"},
        {"mission_revision": 4},
        {"obligation_id": "obligation-2"},
        {"turn_index": 5},
        {"frame": replace(_frame(), frame_id=8)},
        {"broker_projection_id": "projection-2"},
        {"candidate_fingerprint": "target-candidates-" + "f" * 64},
    ),
)
def test_any_runtime_authority_change_stales_and_exhausts_lease(changed) -> None:
    lease = _lease()
    with pytest.raises(TargetSelectionLeaseError, match="stale"):
        lease.consume(scope=replace(_scope(), **changed), candidate_ordinal=1)
    assert lease.consumed is True


def test_old_ordinal_never_remaps_to_new_discovery_order() -> None:
    first, second = _candidate(1), _candidate(2)
    old_snapshot = _snapshot(first, second)
    new_snapshot = _snapshot(second, first)
    old = TargetSelectionLease(
        scope=_scope(old_snapshot), discovery=old_snapshot,
    )
    new = TargetSelectionLease(
        scope=replace(
            _scope(new_snapshot), turn_index=5,
            broker_projection_id="projection-2",
        ),
        discovery=new_snapshot,
    )

    assert old.consume(scope=_scope(old_snapshot), candidate_ordinal=1) is first
    assert new.consume(
        scope=new.scope, candidate_ordinal=1,
    ) is second


def test_concurrent_consumers_allow_at_most_one_resolution() -> None:
    lease = _lease()

    def consume():
        try:
            return lease.consume(scope=_scope(), candidate_ordinal=1)
        except TargetSelectionLeaseError:
            return None

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = tuple(executor.map(lambda _index: consume(), range(64)))

    assert sum(item is not None for item in results) == 1


def test_discard_is_idempotent_and_prevents_future_resolution() -> None:
    lease = _lease()
    assert lease.discard() is True
    assert lease.discard() is False
    with pytest.raises(TargetSelectionLeaseError, match="exhausted"):
        lease.consume(scope=_scope(), candidate_ordinal=1)


def test_private_mapping_has_no_public_bypass_and_safe_facts_expire() -> None:
    lease = _lease()
    assert not hasattr(lease, "ordered_candidates")
    assert tuple(item.ordinal for item in lease.choice_facts()) == (1, 2)
    lease.discard()
    with pytest.raises(TargetSelectionLeaseError, match="exhausted"):
        lease.choice_facts()


def test_scope_and_discovery_fingerprint_must_match() -> None:
    first = _snapshot(_candidate(1))
    second = _snapshot(_candidate(2))
    with pytest.raises(TargetSelectionLeaseError, match="does not identify"):
        TargetSelectionLease(scope=_scope(first), discovery=second)


def test_lease_rejects_untyped_candidate_iterable_before_iteration() -> None:
    with pytest.raises(TargetSelectionLeaseError, match="TargetDiscoverySnapshot"):
        TargetSelectionLease(scope=_scope(), discovery=iter(_snapshot().candidates))


@pytest.mark.parametrize(
    "scope",
    (
        lambda: replace(_scope(), mission_revision=True),
        lambda: replace(_scope(), turn_index=-1),
        lambda: replace(_scope(), frame=("not", "a", "frame")),
        lambda: replace(_scope(), broker_projection_id=""),
        lambda: replace(_scope(), candidate_fingerprint="not-a-fingerprint"),
    ),
)
def test_scope_rejects_coerced_or_mutable_authority(scope) -> None:
    with pytest.raises(TargetSelectionLeaseError):
        scope()
