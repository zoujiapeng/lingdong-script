from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from winpilot.vnext.evidence import (
    EvidenceRegistry,
    EvidenceRegistryClosed,
    EvidenceRegistryError,
)
from winpilot.vnext.mission import (
    EvidenceRef,
    EvidenceScope,
    EvidenceScopeError,
    MissionDefinition,
    MissionReducer,
    ObligationSpec,
    TargetBinding,
)


def _ref(
    evidence_id: str,
    *,
    mission_id: str = "mission-1",
    run_id: str = "run-1",
    obligation_id: str | None = "obligation-1",
    payload_hash: str | None = None,
) -> EvidenceRef:
    return EvidenceRef(
        id=evidence_id,
        mission_id=mission_id,
        run_id=run_id,
        kind="frame_capture",
        trust="deterministic",
        obligation_id=obligation_id,
        payload_hash=payload_hash or f"hash-{evidence_id}",
    )


def test_identical_id_is_idempotent_but_conflicting_content_fails() -> None:
    registry = EvidenceRegistry("mission-1", "run-1")
    original = _ref("evidence-1")

    assert registry.append(original) == original
    assert registry.append(original) == original
    with pytest.raises(EvidenceRegistryError, match="conflicting"):
        registry.append(replace(original, payload_hash="different"))

    assert registry.snapshot() == (original,)


def test_scope_and_value_type_fail_closed_without_writes() -> None:
    registry = EvidenceRegistry("mission-1", "run-1")

    for ref in (
        _ref("other-mission", mission_id="mission-2"),
        _ref("other-run", run_id="run-2"),
    ):
        with pytest.raises(EvidenceRegistryError, match="outside"):
            registry.append(ref)
    with pytest.raises(EvidenceRegistryError, match="only EvidenceRef"):
        registry.append({"id": "not-typed"})  # type: ignore[arg-type]

    assert registry.snapshot() == ()


def test_append_many_is_atomic_for_registry_and_batch_conflicts() -> None:
    registry = EvidenceRegistry("mission-1", "run-1")
    existing = _ref("evidence-2")
    registry.append(existing)

    with pytest.raises(EvidenceRegistryError, match="conflicting"):
        registry.append_many((
            _ref("evidence-1"),
            replace(existing, payload_hash="changed"),
        ))
    assert registry.snapshot() == (existing,)

    with pytest.raises(EvidenceRegistryError, match="in batch"):
        registry.append_many((
            _ref("evidence-3"),
            _ref("evidence-3", payload_hash="changed"),
        ))
    assert registry.snapshot() == (existing,)


def test_snapshot_is_sorted_immutable_and_evidence_scope_compatible() -> None:
    registry = EvidenceRegistry("mission-1", "run-1")
    registry.append_many((_ref("evidence-z"), _ref("evidence-a")))

    snapshot = registry.snapshot()
    assert isinstance(snapshot, tuple)
    assert [ref.id for ref in snapshot] == ["evidence-a", "evidence-z"]
    scope = EvidenceScope.from_source("mission-1", "run-1", registry)
    assert scope.require(("evidence-z",))[0] == _ref("evidence-z")


def test_concurrent_appends_preserve_all_unique_refs_and_one_identity() -> None:
    registry = EvidenceRegistry("mission-1", "run-1")
    refs = [_ref(f"evidence-{index:03d}") for index in range(64)]
    work = [*refs, *([refs[0]] * 64)]

    with ThreadPoolExecutor(max_workers=16) as pool:
        returned = list(pool.map(registry.append, work))

    assert len(returned) == len(work)
    assert len(registry) == len(refs)
    assert registry.snapshot() == tuple(refs)


def test_reducer_reads_registry_dynamically_on_each_apply() -> None:
    definition = MissionDefinition(
        mission_id="mission-1",
        run_id="run-1",
        original_goal="bind the visible target",
        obligations=(ObligationSpec(
            id="obligation-1",
            desired_state="the visible target is bound",
        ),),
    )
    registry = EvidenceRegistry("mission-1", "run-1")
    reducer = MissionReducer(definition, evidence=registry)
    event = reducer.event(
        "target_bound",
        payload={
            "target_binding": TargetBinding(
                binding_id="target-1",
                hwnd=101,
                process_id=202,
                executable_identity="notepad.exe",
                title_snapshot="Notepad",
                established_by_evidence_ref="evidence-late",
                epoch=0,
            ).to_dict(),
        },
        evidence_refs=("evidence-late",),
    )

    with pytest.raises(EvidenceScopeError, match="not in run scope"):
        reducer.apply(event)
    registry.append(_ref("evidence-late"))
    projection = reducer.apply(event)

    assert projection.revision == 1
    assert projection.target_binding is not None
    assert projection.target_binding.binding_id == "target-1"


def test_close_rejects_all_writes_but_keeps_reads_available() -> None:
    registry = EvidenceRegistry("mission-1", "run-1")
    ref = _ref("evidence-1")
    registry.append(ref)
    registry.close()
    registry.close()

    assert registry.closed is True
    assert registry.snapshot() == (ref,)
    assert EvidenceScope.from_source(
        "mission-1", "run-1", registry,
    ).require((ref.id,)) == (ref,)
    with pytest.raises(EvidenceRegistryClosed):
        registry.append(_ref("evidence-2"))
    with pytest.raises(EvidenceRegistryClosed):
        registry.append_many(())
